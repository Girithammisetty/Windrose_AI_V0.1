"""Temporal worker + client helpers (ART-FR-010)."""

from __future__ import annotations

from temporalio.client import Client
from temporalio.worker import Worker

from app.runtime.temporalx.activities import AgentActivities
from app.runtime.temporalx.workflows import AgentRunWorkflow


async def connect(target: str, namespace: str) -> Client:
    return await Client.connect(target, namespace=namespace)


def build_worker(client: Client, container, *, task_queue: str) -> Worker:
    acts = AgentActivities(container)
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[AgentRunWorkflow],
        activities=[acts.run_graph, acts.create_proposal, acts.execute_proposal,
                    acts.mark_awaiting, acts.finalize_run, acts.capture_transcript],
    )


async def run_worker(container) -> None:
    s = container.settings
    client = await connect(s.temporal_target, s.temporal_namespace)
    worker = build_worker(client, container, task_queue=s.temporal_task_queue)
    await worker.run()
