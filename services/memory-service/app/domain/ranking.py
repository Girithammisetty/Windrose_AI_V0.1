"""Retrieval ranking blend (MEM-FR-022, worked example in AC §10).

score = w_sim*cosine + w_rec*recency_decay(half_life) + w_conf*confidence
"""

from __future__ import annotations

from datetime import datetime

from app.domain.policy import half_life_seconds
from app.utils import recency_decay


def blend(
    *,
    similarity: float,
    confidence: float | None,
    reference_time: datetime | None,
    now: datetime,
    scope: str,
    settings,
    default_conf: float,
) -> tuple[float, dict]:
    conf = confidence if confidence is not None else default_conf
    if reference_time is not None:
        age = (now - reference_time).total_seconds()
        rec = recency_decay(age, half_life_seconds(scope, settings))
    else:
        rec = 1.0
    total = (
        settings.w_sim * similarity
        + settings.w_rec * rec
        + settings.w_conf * conf
    )
    debug = {
        "sim": round(similarity, 6),
        "recency": round(rec, 6),
        "confidence": round(conf, 6),
        "w_sim": settings.w_sim,
        "w_rec": settings.w_rec,
        "w_conf": settings.w_conf,
        "score": round(total, 6),
    }
    return total, debug
