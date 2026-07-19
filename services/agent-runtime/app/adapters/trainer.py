"""GpuTrainer adapters (SLM distillation milestone 3).

The control plane (submit -> track -> promote) is real and runs on any stack;
the actual LoRA/QLoRA compute needs a GPU node pool + an executor backend. When
none is wired the runtime uses ``UnconfiguredGpuTrainer``, which — exactly like
ai-gateway's UNRUNNABLE_PROVIDERS path — ACCEPTS the job at the control-plane
layer but raises a typed, non-retryable ``GpuTrainerNotConfigured`` at
execution, so the job lands in ``failed`` with a clear reason rather than a fake
success (Rule 2). ``FakeGpuTrainer`` produces a deterministic artifact for
unit tests of the succeed path.
"""

from __future__ import annotations

import hashlib

from app.domain.ports import (
    GpuTrainer,
    GpuTrainerNotConfigured,
    TrainingResult,
    TrainingSpec,
)


class UnconfiguredGpuTrainer:
    """The default on a CPU-only / no-executor deployment. Real object, honest
    failure — never fabricates an adapter."""

    def __init__(self, reason: str | None = None) -> None:
        self._reason = reason or (
            "no GPU trainer executor is configured "
            "(set SLM_TRAINER_BACKEND + a GPU node pool to enable LoRA distillation)"
        )

    async def train(self, spec: TrainingSpec) -> TrainingResult:  # noqa: ARG002
        raise GpuTrainerNotConfigured(self._reason)


class FakeGpuTrainer:
    """Deterministic in-memory trainer for unit tests: derives a stable artifact
    URI + checksum from the SFT corpus so the succeed path is exercisable
    without a GPU. NOT wired into the real runtime."""

    async def train(self, spec: TrainingSpec) -> TrainingResult:
        digest = hashlib.sha256(spec.sft_examples_jsonl.encode("utf-8")).hexdigest()
        return TrainingResult(
            adapter_uri=f"memory://slm-adapters/{spec.archetype}/{digest[:16]}",
            mlflow_run_ref=f"fake-run-{digest[:12]}",
            checksum=digest,
        )


def build_trainer(backend: str | None) -> GpuTrainer:
    """Select the trainer by backend name.

    "modal" runs the REAL QLoRA distillation on a serverless GPU (see
    adapters/modal_trainer.py + slm_modal_app.py). It is returned unconditionally
    rather than probed here, because the Rule-2 contract is to ACCEPT the job at
    the control plane and fail honestly at EXECUTION: a missing SDK, absent
    credentials or an undeployed function surfaces as GpuTrainerNotConfigured
    from train(), so the job lands in `failed` with a reason naming the exact
    missing wiring. "fake" is the deterministic in-process trainer for tests;
    other real backends ("sagemaker", "k8s-job") remain follow-ups.
    """
    if backend == "fake":
        return FakeGpuTrainer()
    if backend == "modal":
        from app.adapters.modal_trainer import ModalGpuTrainer

        return ModalGpuTrainer()
    if backend:
        return UnconfiguredGpuTrainer(
            f"SLM_TRAINER_BACKEND={backend!r} is not implemented on this build "
            "(GPU LoRA training is a GPU-gated follow-up)"
        )
    return UnconfiguredGpuTrainer()
