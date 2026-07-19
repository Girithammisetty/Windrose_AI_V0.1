"""Mid-execution kill switch (P1): a running agent is cancelled in-flight, not
just refused at start."""

from __future__ import annotations

import asyncio

import pytest

from app.domain.errors import AgentKilled
from app.runtime.killrace import run_with_killswitch


async def test_returns_result_when_not_killed():
    async def work():
        await asyncio.sleep(0.01)
        return "done"

    out = await run_with_killswitch(work(), is_killed=lambda: _false(), poll_interval=0.01)
    assert out == "done"


async def test_cancels_mid_execution_when_killed():
    cancelled = asyncio.Event()

    async def long_work():
        try:
            await asyncio.sleep(10)  # simulate an in-flight LLM/tool call
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return "should-not-reach"

    killed = {"v": False}

    async def is_killed():
        return killed["v"]

    async def flip():
        await asyncio.sleep(0.05)
        killed["v"] = True

    asyncio.ensure_future(flip())
    with pytest.raises(AgentKilled):
        await run_with_killswitch(long_work(), is_killed=is_killed, poll_interval=0.01)
    assert cancelled.is_set()  # the in-flight work really was cancelled, not left running


async def test_outer_cancellation_is_forwarded_to_the_task():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def work():
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.ensure_future(
        run_with_killswitch(work(), is_killed=lambda: _false(), poll_interval=0.01))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


async def _false() -> bool:
    return False
