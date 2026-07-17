"""Adapter ports (Protocols). Real adapters (app/adapters/*) and unit-tier
doubles (tests) both satisfy these; nothing here imports httpx/kafka."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class LlmResult:
    content: str
    input_tokens: int
    output_tokens: int
    model: str
    deployment: str | None = None


class LlmClient(Protocol):
    async def chat(
        self,
        *,
        messages: list[dict],
        tenant_id: str,
        response_format: dict | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LlmResult: ...


@dataclass(slots=True)
class ToolResult:
    ok: bool
    status: str            # "ok" | "proposal_required" | "error"
    output: dict = field(default_factory=dict)
    tier: str | None = None
    side_effects: str | None = None
    code: str | None = None
    message: str | None = None


class ToolClient(Protocol):
    async def call(
        self,
        *,
        tool_id: str,
        arguments: dict,
        tenant_id: str,
        auth_token: str,
        version: str | None = None,
        proposal_grant: str | None = None,
    ) -> ToolResult: ...


class MemoryClient(Protocol):
    async def retrieve(
        self, *, tenant_id: str, query: str, auth_token: str, top_k: int = 5
    ) -> list[dict]: ...


class CaseReader(Protocol):
    async def get_case(
        self, *, tenant_id: str, case_id: str, auth_token: str
    ) -> dict: ...


class RealtimePublisher(Protocol):
    async def publish(self, *, topic: str, event: str, data: dict) -> None: ...


class EventBus(Protocol):
    async def publish(self, topic: str, envelope: dict) -> None: ...


class KillRegistry(Protocol):
    async def is_killed(
        self, *, agent_key: str, version: int, tenant_id: str
    ) -> bool: ...

    async def set_kill(self, ks: Any) -> None: ...

    async def clear_kill(self, kill_id: str) -> None: ...


class Authz(Protocol):
    async def allow(
        self,
        *,
        subject: dict,
        action: str,
        tenant: str,
        resource_urn: str | None = None,
        workspace_id: str | None = None,
    ) -> bool: ...
