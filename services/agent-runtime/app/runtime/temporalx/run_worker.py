"""`python -m app.runtime.temporalx.run_worker` — standalone Temporal worker."""

from __future__ import annotations

import asyncio

from app.container import build_container
from app.runtime.temporalx.worker import run_worker


def main() -> None:
    container = build_container()
    asyncio.run(run_worker(container))


if __name__ == "__main__":
    main()
