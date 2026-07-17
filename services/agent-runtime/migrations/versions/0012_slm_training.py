"""SLM distillation — milestone 3/4 control plane: training jobs + adapters.

The control plane above the (GPU-gated) LoRA compute: a ``slm_training_jobs``
row records a submitted distillation run against a versioned SFT dataset
(milestone 2 output) and its lifecycle (queued -> running -> succeeded/failed);
a ``slm_adapters`` row records the resulting distilled adapter and its
promotion lifecycle (candidate -> gated -> promoted / demoted) — a promoted
adapter becomes the tenant's cheapest ai-gateway ladder rung (design doc §M4).

The DB/API/lifecycle here are fully real; the actual GPU LoRA training is
executed behind a typed port (``GpuTrainer``) that fails honestly
(``GpuTrainerNotConfigured``) when no GPU/executor backend is wired — so a
submitted job on a CPU-only stack lands in ``failed`` with a clear reason
rather than a fake success (Rule 2).

Both RLS-isolated + granted to agent_runtime_app. Forward-only (MASTER-FR-060).
Revision ID: 0012
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE slm_training_jobs (
            job_id         uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL,
            archetype      text NOT NULL,               -- agent_key / archetype the SLM specializes
            sft_dataset_id uuid NOT NULL,               -- the versioned SFT dataset consumed (milestone 2)
            base_model     text NOT NULL,               -- open student base to fine-tune
            status         text NOT NULL DEFAULT 'queued',  -- queued|running|succeeded|failed|cancelled
            params         jsonb NOT NULL DEFAULT '{}',      -- lora rank/alpha/epochs/teacher rung...
            mlflow_run_ref text,                         -- MLflow run the trainer registered against
            adapter_id     uuid,                         -- set when a run succeeds
            error          jsonb,                        -- {reason, detail} on failure
            created_by     text,
            created_at     timestamptz NOT NULL DEFAULT now(),
            updated_at     timestamptz NOT NULL DEFAULT now(),
            started_at     timestamptz,
            finished_at    timestamptz
        );
        CREATE INDEX ix_slm_jobs_archetype ON slm_training_jobs (tenant_id, archetype, created_at DESC);

        CREATE TABLE slm_adapters (
            adapter_id       uuid PRIMARY KEY,
            tenant_id        uuid NOT NULL,
            training_job_id  uuid NOT NULL,
            archetype        text NOT NULL,
            base_model       text NOT NULL,
            adapter_uri      text NOT NULL,               -- artifact location (produced by real training)
            checksum         text NOT NULL DEFAULT '',
            model_alias      text NOT NULL,               -- the ladder-rung alias it would serve as
            promotion_status text NOT NULL DEFAULT 'candidate',  -- candidate|gated|promoted|demoted
            eval_result_ref  text,                        -- the eval-gate result that cleared promotion
            target_rung_alias text,                       -- ai-gateway rung it was promoted to
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_slm_adapters_archetype ON slm_adapters (tenant_id, archetype, created_at DESC);

        GRANT SELECT, INSERT, UPDATE, DELETE ON slm_training_jobs TO agent_runtime_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON slm_adapters TO agent_runtime_app;

        ALTER TABLE slm_training_jobs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE slm_training_jobs FORCE ROW LEVEL SECURITY;
        CREATE POLICY slm_training_jobs_isolation ON slm_training_jobs
            USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

        ALTER TABLE slm_adapters ENABLE ROW LEVEL SECURITY;
        ALTER TABLE slm_adapters FORCE ROW LEVEL SECURITY;
        CREATE POLICY slm_adapters_isolation ON slm_adapters
            USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
