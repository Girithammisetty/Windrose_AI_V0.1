"""Gateway-tier guardrails (AIG-FR-050..054, BR-10).

PII redaction and injection classification run in parallel on inbound user
content; the redaction map lives only in memory on the in-flight request and
is never persisted. Output schema validation is applied by the pipeline."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass, field

import jsonschema

from app.config import DEFAULT_GUARDRAIL_POLICY, Settings
from app.domain.entities import GuardrailPolicy
from app.domain.errors import GuardrailBlocked, ValidationFailed
from app.domain.ports import InjectionClassifier, PIIAnalyzer, UowFactory
from app.utils import uuid7


@dataclass
class GuardrailOutcome:
    messages: list[dict]
    flags: list[str] = field(default_factory=list)  # kinds only, never values
    redaction_map: dict[str, str] = field(default_factory=dict)  # placeholder -> original
    policy_version: int = 0
    events: list[dict] = field(default_factory=list)  # guardrail.triggered payloads
    deredact_response: bool = False


class GuardrailEngine:
    def __init__(self, uow_factory: UowFactory, pii: PIIAnalyzer,
                 injection: InjectionClassifier, settings: Settings):
        self.uow_factory = uow_factory
        self.pii = pii
        self.injection = injection
        self.settings = settings

    async def policy_for(self, tenant_id: str) -> GuardrailPolicy:
        async with self.uow_factory(tenant_id) as uow:
            policy = await uow.policies.current()
        if policy is not None:
            return policy
        return GuardrailPolicy(id=f"default-{tenant_id}", tenant_id=tenant_id,
                               policy=copy.deepcopy(DEFAULT_GUARDRAIL_POLICY), version=0)

    async def inbound(self, tenant_id: str, messages: list[dict],
                      policy: GuardrailPolicy, request_id: str) -> GuardrailOutcome:
        """PII redaction ∥ injection classification (AIG-FR-054)."""
        cfg = policy.policy
        outcome = GuardrailOutcome(messages=messages, policy_version=policy.version)
        pii_task = asyncio.to_thread(self._pii_pass, messages, cfg.get("pii", {}))
        injection_task = asyncio.to_thread(
            self._injection_score, messages
        )
        (redacted, redaction_map, pii_kinds), score = await asyncio.gather(
            pii_task, injection_task
        )

        inj_cfg = cfg.get("injection", {})
        inj_mode = inj_cfg.get("mode", "block")
        if inj_mode != "off":
            block_at = float(inj_cfg.get("block_threshold", 0.85))
            flag_at = float(inj_cfg.get("flag_threshold", 0.65))
            if inj_mode == "block" and score >= block_at:
                outcome.events.append(self._event("injection", inj_mode, "blocked",
                                                  policy.version, request_id))
                raise GuardrailBlocked(
                    "prompt classified as an injection attempt",
                    details={"kind": "injection", "score": round(score, 3)},
                )
            if score >= flag_at:
                outcome.flags.append("injection_flagged")
                outcome.events.append(self._event("injection", inj_mode, "flagged",
                                                  policy.version, request_id))

        pii_cfg = cfg.get("pii", {})
        pii_mode = pii_cfg.get("mode", "redact")
        if pii_mode != "off" and pii_kinds:
            if pii_mode == "block":
                outcome.events.append(self._event("pii", pii_mode, "blocked",
                                                  policy.version, request_id))
                raise GuardrailBlocked(
                    "prompt contains disallowed PII",
                    details={"kind": "pii", "entities": sorted(set(pii_kinds))},
                )
            outcome.messages = redacted
            outcome.redaction_map = redaction_map
            outcome.flags.append("pii_redacted")
            outcome.deredact_response = bool(pii_cfg.get("deredact_response", False))
            outcome.events.append(self._event("pii", pii_mode, "redacted",
                                              policy.version, request_id))
        return outcome

    def _pii_pass(self, messages: list[dict], pii_cfg: dict):
        """Deterministic per-request placeholders `<PII:KIND:n>` (BR-10)."""
        if pii_cfg.get("mode", "redact") == "off":
            return messages, {}, []
        entities = pii_cfg.get("entities") or DEFAULT_GUARDRAIL_POLICY["pii"]["entities"]
        counters: dict[str, int] = {}
        by_value: dict[str, str] = {}  # original -> placeholder (stable per request)
        redaction_map: dict[str, str] = {}
        kinds: list[str] = []
        redacted: list[dict] = []
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, str):
                redacted.append(msg)
                continue
            found = sorted(self.pii.analyze(content, entities), key=lambda e: e.start)
            if not found:
                redacted.append(msg)
                continue
            out, pos = [], 0
            for ent in found:
                if ent.start < pos:
                    continue  # overlapping match already covered
                placeholder = by_value.get(ent.text)
                if placeholder is None:
                    counters[ent.kind] = counters.get(ent.kind, 0) + 1
                    placeholder = f"<PII:{ent.kind}:{counters[ent.kind]}>"
                    by_value[ent.text] = placeholder
                    redaction_map[placeholder] = ent.text
                kinds.append(ent.kind)
                out.append(content[pos:ent.start])
                out.append(placeholder)
                pos = ent.end
            out.append(content[pos:])
            redacted.append({**msg, "content": "".join(out)})
        return redacted, redaction_map, kinds

    def _injection_score(self, messages: list[dict]) -> float:
        scores = [
            self.injection.score(m["content"])
            for m in messages
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ]
        return max(scores, default=0.0)

    @staticmethod
    def _event(kind: str, mode: str, action: str, policy_version: int,
               request_id: str) -> dict:
        # guardrail.triggered — no PII values (MASTER-FR-042)
        return {
            "kind": kind,
            "mode": mode,
            "action": action,
            "policy_version": policy_version,
            "request_id": request_id,
        }

    @staticmethod
    def deredact(text: str, redaction_map: dict[str, str]) -> str:
        for placeholder, original in redaction_map.items():
            text = text.replace(placeholder, original)
        return text

    # ---------------------------------------------------------------- output schema

    @staticmethod
    def validate_output_schema(content: str, response_format: dict | None) -> str | None:
        """Returns None when valid; an error string when invalid (AIG-FR-052)."""
        if not response_format or response_format.get("type") != "json_schema":
            return None
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            return f"output is not valid JSON: {exc}"
        schema = (response_format.get("json_schema") or {}).get("schema")
        if schema:
            try:
                jsonschema.validate(parsed, schema)
            except jsonschema.ValidationError as exc:
                return f"output violates schema: {exc.message}"
        return None


def validate_policy_doc(policy: dict, operator_approved_off: bool = False) -> None:
    """PUT /admin/guardrails validation (AIG-FR-050/053)."""
    pii = policy.get("pii", {})
    if pii.get("mode") not in ("redact", "block", "off"):
        raise ValidationFailed("pii.mode must be redact|block|off")
    if pii.get("mode") == "off" and not operator_approved_off:
        raise ValidationFailed(
            "pii.mode=off requires the platform-operator approval flag",
            details=[{"field": "pii.mode", "problem": "operator approval required"}],
        )
    inj = policy.get("injection", {})
    if inj.get("mode") not in ("block", "flag", "off"):
        raise ValidationFailed("injection.mode must be block|flag|off")
    if policy.get("schema_validation") not in ("on", "off"):
        raise ValidationFailed("schema_validation must be on|off")
    if len(json.dumps(policy)) > 8192:
        raise ValidationFailed("policy document exceeds 8KB")


def new_policy_version(tenant_id: str, existing: GuardrailPolicy | None,
                       policy: dict) -> GuardrailPolicy:
    return GuardrailPolicy(
        id=existing.id if existing else str(uuid7()),
        tenant_id=tenant_id,
        policy=policy,
        version=(existing.version + 1) if existing else 1,
    )
