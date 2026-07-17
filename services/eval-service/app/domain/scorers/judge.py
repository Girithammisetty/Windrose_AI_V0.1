"""LLM-judge scorers v1 (EVL-FR-012): groundedness + helpfulness.

Judges call the **real ai-gateway `judge` request class** (temperature 0, pinned
judge ladder) via the injected judge client; every judge result stores
``{judge_model, judge_prompt_ver, rationale}``. Judge scorers are
``gate_eligible=False`` standalone — they may only gate in combination (BR-1),
enforced at suite save AND re-asserted at gate evaluation."""

from __future__ import annotations

import json
import re

from .base import ScoreResult

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict:
    """Parse the first JSON object out of a judge completion (qwen sometimes wraps
    it in prose). Falls back to a rating regex if the JSON is malformed."""
    m = _JSON_RE.search(text or "")
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            pass
    rating = re.search(r"(?:rating|score)\D{0,6}([1-5](?:\.\d+)?)", text or "", re.IGNORECASE)
    if rating:
        return {"rating": float(rating.group(1)), "rationale": (text or "")[:400]}
    return {}


GROUNDEDNESS_PROMPT_VER = "groundedness@3"
HELPFULNESS_PROMPT_VER = "helpfulness@1"

_GROUNDEDNESS_SYSTEM = (
    "You are a strict evaluation judge. Rate how well the ANSWER's claims are "
    "supported by the provided EVIDENCE (tool results / retrieved chunks). "
    "Unsupported or fabricated claims lower the score. Respond ONLY with compact "
    'JSON: {"rating": <1-5 integer>, "unsupported_claims": [<strings>], '
    '"rationale": <one sentence>}. 5 = every claim supported; 1 = mostly fabricated.'
)

_HELPFULNESS_SYSTEM = (
    "You are a strict evaluation judge. Rate how helpful the ANSWER is for the "
    "USER REQUEST on a 1-5 rubric (5 = fully answers, actionable; 1 = unhelpful). "
    'Respond ONLY with compact JSON: {"rating": <1-5 integer>, '
    '"rationale": <one sentence>}.'
)


class _JudgeScorerBase:
    kind = "llm_judge"
    gate_eligible = False  # BR-1: judge never gates alone
    system_prompt = ""
    prompt_ver = ""

    def __init__(self, judge_client):
        self._judge = judge_client

    def _build_user(self, case: dict, candidate_output: dict) -> str:
        # Overridden by each concrete judge; the safe default poses the raw
        # request + answer so the base is always usable (no runtime stub).
        answer = candidate_output.get("answer") or candidate_output.get("content") or ""
        return f"USER REQUEST:\n{_user_text(case)}\n\nANSWER:\n{answer[:2000]}"

    async def score(self, case: dict, candidate_output: dict, config: dict) -> ScoreResult:
        user = self._build_user(case, candidate_output)
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user},
        ]
        result = await self._judge.judge(
            messages=messages,
            tenant_id=case.get("_tenant_id", ""),
            max_tokens=256,
        )
        parsed = _extract_json(result.content)
        rating = parsed.get("rating", parsed.get("score"))
        try:
            rating = float(rating)
        except (TypeError, ValueError):
            rating = 0.0
        rating = max(0.0, min(5.0, rating))
        threshold = float(config.get("pass_threshold", 3.0))
        details = {
            "rating": rating,
            "rationale": parsed.get("rationale", (result.content or "")[:400]),
            "judge_model": result.model,
            "judge_prompt_ver": self.prompt_ver,
            "raw": (result.content or "")[:600],
        }
        if "unsupported_claims" in parsed:
            details["unsupported_claims"] = parsed["unsupported_claims"]
        return ScoreResult(
            score=rating,
            passed=rating >= threshold,
            details=details,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
            trace_ref=result.trace_ref,
        )


class GroundednessJudgeScorer(_JudgeScorerBase):
    scorer_key = "groundedness"
    version = 3
    applicable_expected_kinds = ("rubric", "structured", "sql_result")
    system_prompt = _GROUNDEDNESS_SYSTEM
    prompt_ver = GROUNDEDNESS_PROMPT_VER

    def _build_user(self, case: dict, candidate_output: dict) -> str:
        answer = candidate_output.get("answer") or candidate_output.get("content") or ""
        evidence = (
            candidate_output.get("evidence")
            or candidate_output.get("tool_results")
            or candidate_output.get("citations")
            or []
        )
        return (
            f"USER REQUEST:\n{_user_text(case)}\n\n"
            f"EVIDENCE:\n{json.dumps(evidence, default=str)[:2000]}\n\n"
            f"ANSWER:\n{answer[:2000]}"
        )


class HelpfulnessJudgeScorer(_JudgeScorerBase):
    scorer_key = "helpfulness"
    version = 1
    applicable_expected_kinds = ("rubric", "structured", "sql_result")
    system_prompt = _HELPFULNESS_SYSTEM
    prompt_ver = HELPFULNESS_PROMPT_VER

    def _build_user(self, case: dict, candidate_output: dict) -> str:
        answer = candidate_output.get("answer") or candidate_output.get("content") or ""
        return f"USER REQUEST:\n{_user_text(case)}\n\nANSWER:\n{answer[:2000]}"


def _user_text(case: dict) -> str:
    inp = case.get("input", {})
    if "task" in inp:
        return str(inp["task"])
    msgs = inp.get("messages", [])
    for m in reversed(msgs):
        if m.get("role") == "user":
            return str(m.get("content", ""))
    return json.dumps(inp, default=str)[:1000]
