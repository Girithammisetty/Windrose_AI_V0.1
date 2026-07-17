"""Versioned price table (BR-5) + cost estimation (AIG-FR-021).

Price table changes apply to new requests only; in-flight requests settle at
reservation-time prices — the pipeline captures a `PriceQuote` at pre-flight
and reuses it at settlement. Every metering event records the price version.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import DEFAULT_PRICE_TABLE, DEFAULT_PROVIDER_PRICE_TABLE


@dataclass(frozen=True)
class PriceQuote:
    model_alias: str
    input_per_1k: float
    output_per_1k: float
    version: str
    # Cost-detail provenance: which (provider, model_id) the price was resolved
    # for, and whether the exact per-(provider,model) tier was used vs the alias
    # fallback. Stamped into the metering event for accurate breakdown.
    provider: str | None = None
    model_id: str | None = None
    source: str = "alias"  # "provider_model" | "alias" | "provider_zero" | "default"

    def cost_cents(self, input_tokens: int, output_tokens: int) -> int:
        usd = (input_tokens / 1000) * self.input_per_1k + (
            output_tokens / 1000
        ) * self.output_per_1k
        # Budgets are hard: round cost up to the next cent, floor 1 cent for
        # any non-zero token usage so spend is never under-counted.
        cents = usd * 100
        rounded = int(cents) + (0 if cents == int(cents) else 1)
        if rounded == 0 and (input_tokens or output_tokens) and (
            self.input_per_1k or self.output_per_1k
        ):
            rounded = 1
        return rounded

    def cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        return self.cost_cents(input_tokens, output_tokens) / 100


class PriceTable:
    def __init__(self, table: dict[str, dict[str, float]] | None = None,
                 version: str = "dev",
                 provider_table: dict[str, dict[str, dict[str, float]]] | None = None):
        self.table = table or DEFAULT_PRICE_TABLE
        self.provider_table = (provider_table if provider_table is not None
                               else DEFAULT_PROVIDER_PRICE_TABLE)
        self.version = version

    def quote(self, model_alias: str) -> PriceQuote:
        """Alias-tier quote (pre-flight reservation estimate). Deployment-agnostic
        upper-bound price; the accurate per-(provider,model) price is applied at
        settlement via `quote_for`."""
        row = self.table.get(model_alias) or {"input_per_1k": 0.01, "output_per_1k": 0.03}
        return PriceQuote(
            model_alias=model_alias,
            input_per_1k=row["input_per_1k"],
            output_per_1k=row["output_per_1k"],
            version=self.version,
            source="alias" if model_alias in self.table else "default",
        )

    def quote_for(self, provider: str | None, model_id: str | None,
                  model_alias: str) -> PriceQuote:
        """Accurate quote for the concrete (provider, model_id) the request ran
        on, falling back to the alias tier. Used at settlement so spend is priced
        by what actually served the request, not just the ladder rung alias:

          1. exact (provider, model_id) published price, else
          2. provider == 'ollama' (or any local model) -> $0/$0, else
          3. the alias-tier price (`quote`)."""
        if provider and model_id:
            row = (self.provider_table.get(provider) or {}).get(model_id)
            if row is not None:
                return PriceQuote(
                    model_alias=model_alias,
                    input_per_1k=row["input_per_1k"],
                    output_per_1k=row["output_per_1k"],
                    version=self.version, provider=provider, model_id=model_id,
                    source="provider_model",
                )
        if provider == "ollama":  # local inference is free regardless of model id
            return PriceQuote(
                model_alias=model_alias, input_per_1k=0.0, output_per_1k=0.0,
                version=self.version, provider=provider, model_id=model_id,
                source="provider_zero",
            )
        base = self.quote(model_alias)
        return PriceQuote(
            model_alias=base.model_alias, input_per_1k=base.input_per_1k,
            output_per_1k=base.output_per_1k, version=self.version,
            provider=provider, model_id=model_id, source=base.source,
        )

    def estimate_cents(self, model_alias: str, prompt_tokens: int, max_tokens: int) -> int:
        """Pre-flight reservation = prompt tokens + max_tokens upper bound."""
        return self.quote(model_alias).cost_cents(prompt_tokens, max_tokens)
