"""Hierarchical hard budgets (AIG-FR-020..025, BR-2/3/4/7/9/12/14).

Scopes stack platform → tenant → workspace → principal → virtual_key; each
scope may define daily + monthly windows and a request must fit every
governing window. Counters live in the LedgerStore (Redis in prod, Postgres
fallback, fail-closed when both are down)."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.domain.entities import (
    SYSTEM_SCOPE_REF,
    Attribution,
    Budget,
    GoverningWindow,
    Reservation,
)
from app.domain.errors import BudgetExhausted, DependencyUnavailable
from app.domain.ports import LedgerStore, LedgerUnavailable, UowFactory
from app.domain.windows import window_reset_at, window_start
from app.utils import Clock, uuid7

# Scope specificity order (BR-9: error names the most specific exhausted scope).
_SPECIFICITY = ["platform", "tenant", "workspace", "principal", "virtual_key"]


@dataclass
class Preflight:
    reservations: list[Reservation]
    degrading: bool
    governing_state: str  # ok | degrading
    estimate_cents: int


class BudgetEngine:
    def __init__(self, uow_factory: UowFactory, ledger: LedgerStore, clock: Clock,
                 settings: Settings, emit_event):
        """`emit_event(tenant_id, event_type, payload)` is an async callback
        wired to the outbox/bus by the container."""
        self.uow_factory = uow_factory
        self.ledger = ledger
        self.clock = clock
        self.settings = settings
        self.emit_event = emit_event

    # ------------------------------------------------------------- governing scopes

    async def governing_windows(self, tenant_id: str, attribution: Attribution,
                                principal_id: str, key_id: str,
                                request_class: str, tz_name: str) -> list[GoverningWindow]:
        now = self.clock.now()
        platform_tid = self.settings.platform_tenant_id

        if request_class == "judge":
            # BR-7: judge/guardrail system class draws only from the reserved
            # platform system budget (still metered with tenant attribution).
            budgets = await self._scope_budgets(platform_tid, "platform", SYSTEM_SCOPE_REF)
            if not budgets:
                budgets = self._default_budgets(
                    platform_tid, "platform", SYSTEM_SCOPE_REF,
                    self.settings.system_budget_daily_usd,
                    self.settings.system_budget_monthly_usd,
                )
            return [self._window(b, now, "UTC") for b in budgets]

        out: list[GoverningWindow] = []
        # platform-wide budget (optional)
        for b in await self._scope_budgets(platform_tid, "platform", "platform"):
            out.append(self._window(b, now, "UTC"))
        # tenant — BR-12: a tenant can never be unbudgeted
        tenant_budgets = await self._scope_budgets(tenant_id, "tenant", tenant_id)
        if not tenant_budgets:
            tenant_budgets = self._default_budgets(
                tenant_id, "tenant", tenant_id,
                self.settings.default_tenant_budget_daily_usd,
                self.settings.default_tenant_budget_monthly_usd,
            )
        out.extend(self._window(b, now, tz_name) for b in tenant_budgets)
        # workspace / principal / virtual_key (only when configured)
        if attribution.workspace_id:
            for b in await self._scope_budgets(tenant_id, "workspace", attribution.workspace_id):
                out.append(self._window(b, now, tz_name))
        for b in await self._scope_budgets(tenant_id, "principal", principal_id):
            out.append(self._window(b, now, tz_name))
        for b in await self._scope_budgets(tenant_id, "virtual_key", key_id):
            out.append(self._window(b, now, tz_name))
        out.sort(key=lambda gw: _SPECIFICITY.index(gw.budget.scope_type))
        return out

    async def _scope_budgets(self, tenant_id: str, scope_type: str,
                             scope_ref: str) -> list[Budget]:
        async with self.uow_factory(tenant_id) as uow:
            budgets = await uow.budgets.for_scope(scope_type, scope_ref)
        return [b for b in budgets if b.status == "active"]

    def _default_budgets(self, tenant_id: str, scope_type: str, scope_ref: str,
                         daily: float, monthly: float) -> list[Budget]:
        return [
            Budget(
                id=f"default-{scope_ref}-{window}",
                tenant_id=tenant_id,
                scope_type=scope_type,
                scope_ref=scope_ref,
                window=window,
                limit_usd=limit,
                degrade_pct=self.settings.default_degrade_pct,
            )
            for window, limit in (("daily", daily), ("monthly", monthly))
            if limit > 0
        ]

    def _window(self, budget: Budget, now, tz_name: str) -> GoverningWindow:
        start = window_start(budget.window, now, tz_name)
        return GoverningWindow(
            budget=budget,
            window_start=start,
            ledger_key=f"bud:{budget.id}:{start}",
            reset_at=window_reset_at(budget.window, now, tz_name),
        )

    # ------------------------------------------------------------- preflight / settle

    async def preflight(self, windows: list[GoverningWindow],
                        estimate_cents: int) -> Preflight:
        """Reserve `estimate_cents` against every governing window, top-down.
        Raises BudgetExhausted naming the most specific exhausted scope."""
        exhausted: GoverningWindow | None = None
        degrading = False
        try:
            for gw in windows:
                spent, reserved = await self.ledger.usage(gw.ledger_key)
                limit = gw.budget.limit_cents
                if spent >= limit:
                    exhausted = gw  # keep scanning: most specific wins (BR-9)
                elif spent >= limit * gw.budget.degrade_pct / 100:
                    degrading = True
            if exhausted is not None:
                raise self._exhausted_error(exhausted)

            reservations: list[Reservation] = []
            for gw in windows:
                rid = str(uuid7())
                ok = await self.ledger.reserve(
                    gw.ledger_key, gw.budget.limit_cents, estimate_cents, rid
                )
                if not ok:
                    # BR-3: atomic reservation lost the race / would exceed —
                    # hard budgets fail closed.
                    for r in reservations:
                        await self.ledger.release(r.governing.ledger_key, r.reservation_id)
                    raise self._exhausted_error(gw)
                reservations.append(Reservation(gw, rid, estimate_cents))
        except LedgerUnavailable as exc:
            raise DependencyUnavailable(
                "budget ledger unavailable; failing closed (BR-14)"
            ) from exc
        return Preflight(
            reservations=reservations,
            degrading=degrading,
            governing_state="degrading" if degrading else "ok",
            estimate_cents=estimate_cents,
        )

    def _exhausted_error(self, gw: GoverningWindow) -> BudgetExhausted:
        b = gw.budget
        scope_name = b.scope_ref if b.scope_type != "platform" else "platform"
        return BudgetExhausted(
            f"{b.window.capitalize()} budget for {b.scope_type} {scope_name} exhausted "
            f"(${b.limit_usd:.2f}/${b.limit_usd:.2f}). "
            f"Resets {gw.reset_at.strftime('%Y-%m-%dT%H:%M:%SZ')}.",
            details={
                "scope_type": b.scope_type,
                "scope_ref": b.scope_ref,
                "window": b.window,
                "reset_at": gw.reset_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )

    async def reserve_more(self, preflight: Preflight, extra_cents: int) -> None:
        """Supplemental reservation when auto-escalation raises the estimate."""
        if extra_cents <= 0:
            return
        for res in list(preflight.reservations):
            gw = res.governing
            rid = str(uuid7())
            ok = await self.ledger.reserve(gw.ledger_key, gw.budget.limit_cents,
                                           extra_cents, rid)
            if not ok:
                raise self._exhausted_error(gw)
            preflight.reservations.append(Reservation(gw, rid, extra_cents))

    async def settle(self, preflight: Preflight, actual_cents: int) -> str:
        """Settle actual spend, refund the rest, emit threshold events exactly
        once per window crossing. Returns the resulting budget state."""
        state = "ok"
        # Group reservations per window so multi-reservation requests settle once.
        per_window: dict[str, list[Reservation]] = {}
        for res in preflight.reservations:
            per_window.setdefault(res.governing.ledger_key, []).append(res)
        for key, reservations in per_window.items():
            gw = reservations[0].governing
            remaining_actual = actual_cents
            prev_spent = new_spent = 0
            for i, res in enumerate(reservations):
                portion = remaining_actual if i == len(reservations) - 1 else min(
                    res.amount_cents, remaining_actual
                )
                remaining_actual -= min(portion, remaining_actual)
                p, n = await self.ledger.settle(key, res.reservation_id, portion)
                if i == 0:
                    prev_spent = p
                new_spent = n
            limit = gw.budget.limit_cents
            for pct in (80, 95, 100):
                threshold = limit * pct / 100
                if prev_spent < threshold <= new_spent:
                    if await self.ledger.flag_once(
                        f"budthr:{gw.budget.id}:{gw.window_start}:{pct}"
                    ):
                        event_type = "budget.exhausted" if pct == 100 else "budget.threshold"
                        await self.emit_event(gw.budget.tenant_id, event_type, {
                            "scope_type": gw.budget.scope_type,
                            "scope_ref": gw.budget.scope_ref,
                            "window": gw.budget.window,
                            "pct": pct,
                            "limit_usd": gw.budget.limit_usd,
                            "spend_usd": new_spent / 100,
                        })
            if new_spent >= limit:
                state = "exhausted"
            elif state != "exhausted" and new_spent >= limit * gw.budget.degrade_pct / 100:
                state = "degrading"
        return state

    async def release(self, preflight: Preflight) -> None:
        for res in preflight.reservations:
            await self.ledger.release(res.governing.ledger_key, res.reservation_id)

    # ------------------------------------------------------------- spend queries

    async def live_spend(self, budget: Budget, tz_name: str) -> dict:
        gw = self._window(budget, self.clock.now(), tz_name)
        try:
            spent, reserved = await self.ledger.usage(gw.ledger_key)
        except LedgerUnavailable as exc:
            raise DependencyUnavailable("budget ledger unavailable") from exc
        return {
            "budget_id": budget.id,
            "scope_type": budget.scope_type,
            "scope_ref": budget.scope_ref,
            "window": budget.window,
            "window_start": gw.window_start,
            "limit_usd": budget.limit_usd,
            "spend_usd": spent / 100,
            "reserved_usd": reserved / 100,
            "reset_at": gw.reset_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }


def child_exceeds_parent_warning(new: Budget, parents: list[Budget]) -> str | None:
    """AIG-FR-024: soft warning when a child limit exceeds a parent limit."""
    order = _SPECIFICITY
    for parent in parents:
        if (
            parent.window == new.window
            and order.index(parent.scope_type) < order.index(new.scope_type)
            and new.limit_usd > parent.limit_usd
        ):
            return (
                f"limit ${new.limit_usd:.2f} exceeds {parent.scope_type} "
                f"{parent.window} limit ${parent.limit_usd:.2f}"
            )
    return None
