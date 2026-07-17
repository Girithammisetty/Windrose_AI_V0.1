"""Real Vault (KV v2) integration: put/get/delete, per-tenant paths, and the
scheduled-destroy grace sweeper (ING-FR-006)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from windrose_common.secrets import VaultSecretsStore

pytestmark = pytest.mark.integration


def _path(tenant: str, conn: str) -> str:
    return f"secret/data/tenants/{tenant}/connections/{conn}"


async def test_put_get_delete_roundtrip(vault, unique):
    store = VaultSecretsStore()
    path = _path(f"t{unique}", f"c{unique}")

    await store.put(path, {"password": "s3cr3t", "username": "svc"})
    got = await store.get(path)
    assert got == {"password": "s3cr3t", "username": "svc"}

    # merge semantics (matches the in-memory fake)
    await store.put(path, {"token": "abc"})
    assert (await store.get(path))["token"] == "abc"
    assert (await store.get(path))["password"] == "s3cr3t"

    await store.delete(path)
    assert await store.get(path) is None


async def test_schedule_destroy_sweeper(vault, unique):
    store = VaultSecretsStore()
    path = _path(f"t{unique}", f"c{unique}")
    await store.put(path, {"password": "will-be-destroyed"})

    # schedule a destroy in the past -> the sweeper should destroy it now
    await store.schedule_destroy(path, datetime.now(UTC) - timedelta(seconds=1))
    assert await store.get(path) is not None  # still present until swept

    destroyed = await store.run_due_destroys(datetime.now(UTC))
    assert destroyed >= 1
    assert await store.get(path) is None

    # a future-dated destroy is NOT swept
    path2 = _path(f"t{unique}", f"future{unique}")
    await store.put(path2, {"password": "safe"})
    await store.schedule_destroy(path2, datetime.now(UTC) + timedelta(days=7))
    assert await store.run_due_destroys(datetime.now(UTC)) == 0
    assert await store.get(path2) is not None
    await store.delete(path2)
