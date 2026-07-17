"""Budget ledger adapters (AIG-FR-021, BR-14).

- InMemoryLedger: unit tier / dev, clock-aware reservation expiry.
- RedisLedger: hot path — atomic remaining-cents counters (`bud:{id}:{window}`),
  SETNX threshold guards, reservation records with 180s expiry.
- PgLedger: source of truth + fallback when Redis is down (degraded latency).
- FallbackLedger: Redis → Postgres → fail closed (raises LedgerUnavailable)."""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import text

from app.domain.ports import LedgerUnavailable
from app.utils import Clock

logger = logging.getLogger(__name__)

RESERVATION_TTL_SECONDS = 180


class InMemoryLedger:
    def __init__(self, clock: Clock, reservation_ttl: int = RESERVATION_TTL_SECONDS):
        self.clock = clock
        self.reservation_ttl = reservation_ttl
        self._spent: dict[str, int] = {}
        self._reservations: dict[str, dict[str, tuple[int, object]]] = {}
        self._flags: set[str] = set()

    def _expire(self, key: str) -> None:
        now = self.clock.now()
        held = self._reservations.get(key, {})
        for rid, (_, deadline) in list(held.items()):
            if now >= deadline:
                del held[rid]

    async def reserve(self, key: str, limit_cents: int, amount_cents: int,
                      reservation_id: str) -> bool:
        self._expire(key)
        spent = self._spent.get(key, 0)
        reserved = sum(a for a, _ in self._reservations.get(key, {}).values())
        if spent + reserved + amount_cents > limit_cents:
            return False
        deadline = self.clock.now() + timedelta(seconds=self.reservation_ttl)
        self._reservations.setdefault(key, {})[reservation_id] = (amount_cents, deadline)
        return True

    async def settle(self, key: str, reservation_id: str,
                     actual_cents: int) -> tuple[int, int]:
        self._reservations.get(key, {}).pop(reservation_id, None)
        prev = self._spent.get(key, 0)
        new = prev + actual_cents
        self._spent[key] = new
        return prev, new

    async def release(self, key: str, reservation_id: str) -> None:
        self._reservations.get(key, {}).pop(reservation_id, None)

    async def usage(self, key: str) -> tuple[int, int]:
        self._expire(key)
        reserved = sum(a for a, _ in self._reservations.get(key, {}).values())
        return self._spent.get(key, 0), reserved

    async def flag_once(self, flag_key: str) -> bool:
        if flag_key in self._flags:
            return False
        self._flags.add(flag_key)
        return True

    async def sweep_expired(self) -> int:
        count = 0
        for key in list(self._reservations):
            before = len(self._reservations[key])
            self._expire(key)
            count += before - len(self._reservations[key])
        return count


class RedisLedger:
    """Counters: `{key}:spent` (cents), reservations in hash `{key}:resv` with
    a deadline zset `{key}:resvz` for expiry sweeps. Reserve is an atomic Lua
    check-and-add so concurrent reservations can't over-commit (BR-3)."""

    _RESERVE_LUA = """
    local spent = tonumber(redis.call('GET', KEYS[1]) or '0')
    local held = 0
    local vals = redis.call('HVALS', KEYS[2])
    for i = 1, #vals do held = held + tonumber(vals[i]) end
    local amount = tonumber(ARGV[1])
    local limit = tonumber(ARGV[2])
    if spent + held + amount > limit then return 0 end
    redis.call('HSET', KEYS[2], ARGV[3], ARGV[1])
    redis.call('ZADD', KEYS[3], ARGV[4], ARGV[3])
    return 1
    """

    _SETTLE_LUA = """
    redis.call('HDEL', KEYS[2], ARGV[1])
    redis.call('ZREM', KEYS[3], ARGV[1])
    local prev = tonumber(redis.call('GET', KEYS[1]) or '0')
    local new = redis.call('INCRBY', KEYS[1], ARGV[2])
    return {prev, new}
    """

    def __init__(self, redis, clock: Clock,
                 reservation_ttl: int = RESERVATION_TTL_SECONDS):
        self.r = redis
        self.clock = clock
        self.reservation_ttl = reservation_ttl
        self._reserve = self.r.register_script(self._RESERVE_LUA)
        self._settle = self.r.register_script(self._SETTLE_LUA)

    def _keys(self, key: str) -> list[str]:
        return [f"{key}:spent", f"{key}:resv", f"{key}:resvz"]

    async def _sweep_key(self, key: str) -> None:
        spent_k, resv_k, resvz_k = self._keys(key)
        now_ts = self.clock.now().timestamp()
        expired = await self.r.zrangebyscore(resvz_k, "-inf", now_ts)
        for rid in expired:
            rid = rid.decode() if isinstance(rid, bytes) else rid
            await self.r.hdel(resv_k, rid)
            await self.r.zrem(resvz_k, rid)

    async def reserve(self, key: str, limit_cents: int, amount_cents: int,
                      reservation_id: str) -> bool:
        try:
            await self._sweep_key(key)
            deadline = self.clock.now().timestamp() + self.reservation_ttl
            ok = await self._reserve(
                keys=self._keys(key),
                args=[amount_cents, limit_cents, reservation_id, deadline],
            )
            return bool(ok)
        except LedgerUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - any redis failure -> unavailable
            raise LedgerUnavailable(str(exc)) from exc

    async def settle(self, key: str, reservation_id: str,
                     actual_cents: int) -> tuple[int, int]:
        try:
            prev, new = await self._settle(
                keys=self._keys(key), args=[reservation_id, actual_cents]
            )
            return int(prev), int(new)
        except Exception as exc:  # noqa: BLE001
            raise LedgerUnavailable(str(exc)) from exc

    async def release(self, key: str, reservation_id: str) -> None:
        try:
            _, resv_k, resvz_k = self._keys(key)
            await self.r.hdel(resv_k, reservation_id)
            await self.r.zrem(resvz_k, reservation_id)
        except Exception as exc:  # noqa: BLE001
            raise LedgerUnavailable(str(exc)) from exc

    async def usage(self, key: str) -> tuple[int, int]:
        try:
            await self._sweep_key(key)
            spent_k, resv_k, _ = self._keys(key)
            spent = int(await self.r.get(spent_k) or 0)
            vals = await self.r.hvals(resv_k)
            reserved = sum(int(v) for v in vals)
            return spent, reserved
        except Exception as exc:  # noqa: BLE001
            raise LedgerUnavailable(str(exc)) from exc

    async def flag_once(self, flag_key: str) -> bool:
        try:
            ok = await self.r.set(flag_key, "1", nx=True, ex=40 * 86_400)
            return bool(ok)
        except Exception as exc:  # noqa: BLE001
            raise LedgerUnavailable(str(exc)) from exc

    async def sweep_expired(self) -> int:
        return 0  # per-key sweeps happen inline on reserve/usage


class PgLedger:
    """Postgres source of truth (`budget_spend`); serves as the fallback path
    when Redis is unavailable (BR-14, AC-13). Uses the worker GUC because
    budget rows may belong to the platform tenant while requests are
    tenant-scoped."""

    def __init__(self, session_factory, clock: Clock,
                 reservation_ttl: int = RESERVATION_TTL_SECONDS):
        self.session_factory = session_factory
        self.clock = clock
        self.reservation_ttl = reservation_ttl
        self._flags: set[str] = set()

    @staticmethod
    def _parse(key: str) -> tuple[str, str]:
        # key = "bud:{budget_id}:{window_start}"
        _, budget_id, window_start = key.split(":", 2)
        return budget_id, window_start

    async def _run(self, fn):
        try:
            async with self.session_factory() as session:
                await session.execute(
                    text("SELECT set_config('app.worker', 'true', true)")
                )
                result = await fn(session)
                await session.commit()
                return result
        except LedgerUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - any pg failure -> unavailable
            raise LedgerUnavailable(str(exc)) from exc

    async def reserve(self, key: str, limit_cents: int, amount_cents: int,
                      reservation_id: str) -> bool:
        budget_id, window_start = self._parse(key)
        now = self.clock.now()

        async def fn(session):
            await session.execute(text(
                "INSERT INTO budget_spend (budget_ref, window_start, spend_cents, "
                "reserved_cents, updated_at) VALUES (:b, :w, 0, 0, :now) "
                "ON CONFLICT (budget_ref, window_start) DO NOTHING"
            ), {"b": budget_id, "w": window_start, "now": now})
            row = (await session.execute(text(
                "SELECT spend_cents, reserved_cents FROM budget_spend "
                "WHERE budget_ref = :b AND window_start = :w FOR UPDATE"
            ), {"b": budget_id, "w": window_start})).first()
            await session.execute(text(
                "DELETE FROM budget_reservations WHERE budget_ref = :b "
                "AND window_start = :w AND expires_at <= :now"
            ), {"b": budget_id, "w": window_start, "now": now})
            held = (await session.execute(text(
                "SELECT coalesce(sum(amount_cents), 0) FROM budget_reservations "
                "WHERE budget_ref = :b AND window_start = :w"
            ), {"b": budget_id, "w": window_start})).scalar_one()
            if row.spend_cents + held + amount_cents > limit_cents:
                return False
            await session.execute(text(
                "INSERT INTO budget_reservations (id, budget_ref, window_start, "
                "amount_cents, expires_at) VALUES (:id, :b, :w, :a, :exp)"
            ), {"id": reservation_id, "b": budget_id, "w": window_start,
                "a": amount_cents,
                "exp": now + timedelta(seconds=self.reservation_ttl)})
            return True

        return await self._run(fn)

    async def settle(self, key: str, reservation_id: str,
                     actual_cents: int) -> tuple[int, int]:
        budget_id, window_start = self._parse(key)

        async def fn(session):
            await session.execute(text(
                "DELETE FROM budget_reservations WHERE id = :id"
            ), {"id": reservation_id})
            await session.execute(text(
                "INSERT INTO budget_spend (budget_ref, window_start, spend_cents, "
                "reserved_cents, updated_at) VALUES (:b, :w, 0, 0, :now) "
                "ON CONFLICT (budget_ref, window_start) DO NOTHING"
            ), {"b": budget_id, "w": window_start, "now": self.clock.now()})
            row = (await session.execute(text(
                "UPDATE budget_spend SET spend_cents = spend_cents + :a, "
                "updated_at = :now WHERE budget_ref = :b AND window_start = :w "
                "RETURNING spend_cents"
            ), {"a": actual_cents, "b": budget_id, "w": window_start,
                "now": self.clock.now()})).first()
            new = row.spend_cents if row else actual_cents
            return new - actual_cents, new

        return await self._run(fn)

    async def release(self, key: str, reservation_id: str) -> None:
        async def fn(session):
            await session.execute(text(
                "DELETE FROM budget_reservations WHERE id = :id"
            ), {"id": reservation_id})

        await self._run(fn)

    async def usage(self, key: str) -> tuple[int, int]:
        budget_id, window_start = self._parse(key)

        async def fn(session):
            row = (await session.execute(text(
                "SELECT spend_cents FROM budget_spend "
                "WHERE budget_ref = :b AND window_start = :w"
            ), {"b": budget_id, "w": window_start})).first()
            held = (await session.execute(text(
                "SELECT coalesce(sum(amount_cents), 0) FROM budget_reservations "
                "WHERE budget_ref = :b AND window_start = :w "
                "AND expires_at > :now"
            ), {"b": budget_id, "w": window_start,
                "now": self.clock.now()})).scalar_one()
            return (row.spend_cents if row else 0, int(held))

        return await self._run(fn)

    async def flag_once(self, flag_key: str) -> bool:
        async def fn(session):
            inserted = (await session.execute(text(
                "INSERT INTO budget_threshold_flags (flag_key, created_at) "
                "VALUES (:k, :now) ON CONFLICT (flag_key) DO NOTHING "
                "RETURNING flag_key"
            ), {"k": flag_key, "now": self.clock.now()})).first()
            return inserted is not None

        return await self._run(fn)

    async def sweep_expired(self) -> int:
        async def fn(session):
            result = await session.execute(text(
                "DELETE FROM budget_reservations WHERE expires_at <= :now"
            ), {"now": self.clock.now()})
            return result.rowcount or 0

        return await self._run(fn)


class FallbackLedger:
    """Redis-first with Postgres fallback; both down → LedgerUnavailable and
    the gateway fails closed with 503 (BR-14, AC-13). A fallback activation
    fires an alert callback (metrics + log)."""

    def __init__(self, primary, fallback, on_fallback=None):
        self.primary = primary
        self.fallback = fallback
        self.on_fallback = on_fallback or (lambda: None)

    async def _call(self, method: str, *args):
        try:
            return await getattr(self.primary, method)(*args)
        except LedgerUnavailable as exc:
            logger.warning("ledger primary unavailable (%s); falling back", exc)
            self.on_fallback()
            return await getattr(self.fallback, method)(*args)

    async def reserve(self, key, limit_cents, amount_cents, reservation_id):
        return await self._call("reserve", key, limit_cents, amount_cents,
                                reservation_id)

    async def settle(self, key, reservation_id, actual_cents):
        return await self._call("settle", key, reservation_id, actual_cents)

    async def release(self, key, reservation_id):
        return await self._call("release", key, reservation_id)

    async def usage(self, key):
        return await self._call("usage", key)

    async def flag_once(self, flag_key):
        return await self._call("flag_once", flag_key)

    async def sweep_expired(self):
        return await self._call("sweep_expired")
