"""SFT chat-format template (distillation milestone 2).

Turns one governed transcript into an OpenAI-style chat training example
(system / user / assistant), where the assistant turn is the GOLD target:

- ``decision == "edit"``   → the human's ``corrected_output`` (the highest-value
  input->corrected-output pair);
- ``decision == "approve"`` → the agent's own accepted action (its proposed
  args, else its final answer).

Only CONSENTED, human-decided, positive (approve/edit) transcripts become gold
pairs. Rejected/cancelled/undecided runs are not positive training data and are
skipped here (they remain in the corpus for later negative-signal use). PII was
already redacted at capture (milestone 1); the target is re-checked for
emptiness so a degenerate example is dropped.
"""

from __future__ import annotations

import hashlib
import json

from app.domain.entities import Transcript

# minimum characters for a usable user prompt / target (drop degenerate rows)
_MIN_USER = 8
_MIN_TARGET = 1


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def _target(t: Transcript) -> str | None:
    if t.decision == "edit":
        if t.corrected_output:
            return _canonical(t.corrected_output)
        return None
    if t.decision == "approve":
        if t.proposed_action and t.proposed_action.get("args"):
            return _canonical(t.proposed_action["args"])
        if t.final_text:
            return t.final_text
    return None


def _render_user(t: Transcript) -> str:
    parts: list[str] = []
    if t.inputs:
        parts.append("INPUT:\n" + _canonical(t.inputs))
    grounding = (t.grounding or {}).get("evidence") if isinstance(t.grounding, dict) else None
    if grounding:
        parts.append("GROUNDING:\n" + _canonical(grounding))
    return "\n\n".join(parts)


def to_sft_example(t: Transcript) -> dict | None:
    """Return {messages, target_kind, source_transcript_id, example_hash} or None
    if the transcript is not a usable gold pair."""
    if not t.consent or t.decision not in ("approve", "edit"):
        return None
    target = _target(t)
    user = _render_user(t)
    if not target or len(target) < _MIN_TARGET or len(user) < _MIN_USER:
        return None
    system = (
        f"You are the Windrose '{t.agent_key}' agent. Given the case input and "
        "the retrieved grounding evidence, produce the governed action. Respond "
        "with only the action."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
        {"role": "assistant", "content": target},
    ]
    example_hash = hashlib.sha256((user + "\x00" + target).encode()).hexdigest()
    return {
        "messages": messages,
        "target_kind": t.decision,
        "source_transcript_id": t.transcript_id,
        "example_hash": example_hash,
    }
