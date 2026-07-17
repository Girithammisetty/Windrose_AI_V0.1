"""Widen provider_deployments' provider CHECK to match the domain PROVIDERS
tuple (app/domain/entities.py), which has included ``ollama`` since the
provider-agnostic adapter work — the DB constraint was never updated to match,
so no ``ollama`` deployment could ever be inserted. Every deployment row was
consequently forced onto ``bedrock`` (real AWS credentials required, never
configured for local/dev), so every LLM-backed call failed with
UPSTREAM_UNAVAILABLE regardless of the virtual key or Ollama's own health.

Forward-only (MASTER-FR-060).

Revision ID: 0003
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE provider_deployments
            DROP CONSTRAINT provider_deployments_provider_check;
        ALTER TABLE provider_deployments
            ADD CONSTRAINT provider_deployments_provider_check
            CHECK (provider = ANY (ARRAY['azure_openai','bedrock','vertex','anthropic','ollama']::text[]));
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migrations (MASTER-FR-060)")
