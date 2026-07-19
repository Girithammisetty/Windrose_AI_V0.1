"""ModalGpuTrainer — the `modal` GpuTrainer backend (SLM distillation M3).

Runs the real QLoRA distillation on a serverless GPU (Modal) and returns a real
artifact reference. The GPU recipe itself lives in ``slm_modal_app.py``, which is
deployed to Modal separately; this module is only the thin client that invokes it,
so agent-runtime needs the small ``modal`` SDK and none of the ML stack.

Enable with::

    pip install 'modal>=0.64'         # or: uv sync --extra slm-modal
    modal token new                    # one-time, your Modal account
    modal deploy services/agent-runtime/app/adapters/slm_modal_app.py
    AR_SLM_TRAINER_BACKEND=modal       # restart agent-runtime

Rule 2 boundary — this class NEVER fabricates an adapter:
  * missing SDK / credentials / undeployed function -> ``GpuTrainerNotConfigured``
    (typed, non-retryable; the job lands in `failed` with an honest reason,
    exactly like the Unconfigured trainer it replaces);
  * a genuine training failure on the GPU -> the underlying error propagates so
    the control plane records a failed job. Neither path invents an artifact.
"""

from __future__ import annotations

from typing import Any

from app.domain.ports import GpuTrainerNotConfigured, TrainingResult, TrainingSpec

DEFAULT_APP = "windrose-slm-trainer"
DEFAULT_FUNCTION = "train_lora"
#: Volume the deployed app writes adapters into (slm_modal_app.ADAPTER_VOLUME).
ADAPTER_VOLUME = "windrose-slm-adapters"


class ModalGpuTrainer:
    """Invokes the deployed Modal ``train_lora`` function for one distillation."""

    def __init__(
        self,
        *,
        app_name: str = DEFAULT_APP,
        function_name: str = DEFAULT_FUNCTION,
        lookup: Any | None = None,
    ) -> None:
        self._app = app_name
        self._fn = function_name
        # Injectable ONLY so unit tests can exercise result mapping without the
        # Modal SDK or a GPU; production always resolves through the real SDK.
        self._lookup = lookup

    def _resolve(self) -> Any:
        """Return the deployed remote function, or fail honestly.

        Both the injected and the real SDK lookup go through the SAME error
        mapping, so the test seam cannot behave differently from production:
        every resolution failure is config, not a training failure, and must
        surface as GpuTrainerNotConfigured.
        """
        try:
            if self._lookup is not None:
                return self._lookup(self._app, self._fn)
            import modal

            # from_name is the current API; lookup is kept for older SDKs.
            getter = getattr(modal.Function, "from_name", None) or modal.Function.lookup
            return getter(self._app, self._fn)
        except ImportError as e:  # SDK not installed on this build
            raise GpuTrainerNotConfigured(
                "SLM_TRAINER_BACKEND=modal but the 'modal' SDK is not installed "
                "(install the 'slm-modal' extra to enable GPU distillation)"
            ) from e
        except GpuTrainerNotConfigured:
            raise
        except Exception as e:
            # Not deployed, bad/absent token, wrong workspace — all config.
            raise GpuTrainerNotConfigured(
                f"modal function {self._app}/{self._fn} is unavailable "
                f"({type(e).__name__}: {e}). Run `modal token new` and "
                f"`modal deploy app/adapters/slm_modal_app.py`."
            ) from e

    async def train(self, spec: TrainingSpec) -> TrainingResult:
        fn = self._resolve()
        remote = getattr(fn, "remote", None)
        call = getattr(remote, "aio", None) if remote is not None else None
        if call is None:
            raise GpuTrainerNotConfigured(
                "the resolved modal function exposes no async .remote.aio() "
                "invoker — check the installed 'modal' SDK version"
            )

        # Corpus is passed INLINE: Modal needs no path into MinIO/MLflow/Postgres.
        out = await call(
            base_model=spec.base_model,
            sft_jsonl=spec.sft_examples_jsonl,
            params=dict(spec.params or {}),
        )
        if not isinstance(out, dict) or not out.get("adapter_path"):
            # Defensive: a malformed remote result must not become a fake success.
            raise GpuTrainerNotConfigured(
                f"modal {self._app}/{self._fn} returned no adapter_path "
                f"(got {type(out).__name__}); refusing to record an artifact"
            )

        return TrainingResult(
            adapter_uri=f"modal://{ADAPTER_VOLUME}/{out['adapter_path']}",
            # Modal's own call id is the run reference until the adapter is
            # mirrored into MLflow (next increment, with serving).
            mlflow_run_ref=str(out.get("call_id") or out.get("run_id") or f"modal:{self._app}"),
            checksum=str(out.get("checksum") or ""),
        )
