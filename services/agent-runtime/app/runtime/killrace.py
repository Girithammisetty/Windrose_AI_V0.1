"""Mid-execution kill switch (P1).

A production kill switch must forcibly stop an agent DURING active execution — not
merely refuse the next run (permission revocation / pre-flight checks only prevent
the NEXT action, per runtimeai.io / the research). This races the running agent
coroutine against a fast poll of the kill registry: when a kill is flagged, the
run task is cancelled, which propagates asyncio cancellation into any in-flight
ai-gateway HTTP call (httpx closes the connection on cancel), and ``AgentKilled``
is raised.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

from app.domain.errors import AgentKilled


async def run_with_killswitch(
    coro: Awaitable[Any], *, is_killed: Callable[[], Awaitable[bool]],
    poll_interval: float = 0.1,
) -> Any:
    """Run ``coro`` to completion, but cancel it mid-flight if ``is_killed()`` turns
    true (polled every ``poll_interval`` seconds). Returns the coroutine's result on
    normal completion; raises ``AgentKilled`` if it was terminated by the switch.
    Outer cancellation is honoured and forwarded to the task."""
    task = asyncio.ensure_future(coro)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=poll_interval)
            if task in done:
                return task.result()
            if await is_killed():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                raise AgentKilled("run terminated mid-execution by kill switch")
    except asyncio.CancelledError:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        raise
