"""Canary comparison statistics (EVL-FR-040..042).

Paired/split samples per scorer are compared candidate-vs-baseline with a
bootstrap 95% confidence interval on the delta; a regression is flagged when the
delta breaches the scorer's per-suite threshold. Early stop fires when a Must
scorer regresses beyond 2× its threshold with ≥50 samples (EVL-FR-042)."""

from __future__ import annotations

import random
import statistics

EARLY_STOP_MIN_SAMPLES = 50
EARLY_STOP_MULTIPLIER = 2.0


def _bootstrap_ci(deltas: list[float], *, iters: int = 1000, seed: int = 42) -> tuple[float, float]:
    if len(deltas) < 2:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(deltas)
    means = []
    for _ in range(iters):
        sample = [deltas[rng.randrange(n)] for _ in range(n)]
        means.append(statistics.fmean(sample))
    means.sort()
    lo = means[int(0.025 * iters)]
    hi = means[int(0.975 * iters)]
    return (round(lo, 6), round(hi, 6))


def compare(
    paired_scores: dict[str, list[tuple[float, float]]],
    thresholds: dict[str, float],
    must_scorers: set[str] | None = None,
) -> dict:
    """paired_scores: {scorer: [(candidate, baseline), ...]}. thresholds: per-scorer
    regression threshold (negative = allowed drop). Returns a report dict."""
    must_scorers = must_scorers or set()
    metrics = []
    any_regressed = False
    early_stop = None
    total_samples = 0
    for scorer, pairs in paired_scores.items():
        if not pairs:
            continue
        total_samples = max(total_samples, len(pairs))
        cand_vals = [c for c, _ in pairs]
        base_vals = [b for _, b in pairs]
        deltas = [c - b for c, b in pairs]
        cand_mean = statistics.fmean(cand_vals)
        base_mean = statistics.fmean(base_vals)
        delta_mean = statistics.fmean(deltas)
        ci = _bootstrap_ci(deltas)
        threshold = thresholds.get(scorer, 0.0)
        regressed = delta_mean < threshold
        if regressed:
            any_regressed = True
        metrics.append(
            {
                "scorer": scorer,
                "candidate": round(cand_mean, 6),
                "baseline": round(base_mean, 6),
                "delta": round(delta_mean, 6),
                "ci95": list(ci),
                "threshold": threshold,
                "regressed": regressed,
                "samples": len(pairs),
            }
        )
        # EVL-FR-042: Must scorer regresses 2x threshold at >=50 samples -> early stop.
        if (
            scorer in must_scorers
            and len(pairs) >= EARLY_STOP_MIN_SAMPLES
            and threshold < 0
            and delta_mean < EARLY_STOP_MULTIPLIER * threshold
        ):
            early_stop = {
                "scorer": scorer,
                "delta": round(delta_mean, 6),
                "threshold": threshold,
                "samples": len(pairs),
            }

    recommendation = "halt" if any_regressed else "promote"
    return {
        "metrics": metrics,
        "samples": total_samples,
        "any_regressed": any_regressed,
        "early_stop": early_stop,
        "recommendation": recommendation,
    }
