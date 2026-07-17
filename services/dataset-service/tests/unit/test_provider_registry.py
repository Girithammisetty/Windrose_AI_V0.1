"""Swappable dependency provider registries (Phase 3)."""

from __future__ import annotations

import pytest

from app.adapters.catalog import IcebergRestCatalog, LocalCatalog
from app.adapters.object_store import LocalFSObjectStore, S3ObjectStore
from app.adapters.registry import (
    CATALOG_PROVIDERS,
    OBJECT_STORE_PROVIDERS,
    ProviderRegistry,
    catalog_provider_name,
    object_store_provider_name,
    resolve_catalog,
    resolve_object_store,
)
from app.config import Settings


def _settings(**over) -> Settings:
    # never touch real infra in a unit test
    base = {"use_real_adapters": False}
    base.update(over)
    return Settings(**base)


def test_registered_provider_names():
    assert CATALOG_PROVIDERS.names() == ["iceberg_rest", "local"]
    assert OBJECT_STORE_PROVIDERS.names() == ["local", "s3"]


def test_default_derives_from_use_real_adapters():
    # local when not real...
    s = _settings(use_real_adapters=False)
    assert catalog_provider_name(s) == "local"
    assert object_store_provider_name(s) == "local"
    # ...and the real backends when real (name only; not constructed here).
    s2 = _settings(use_real_adapters=True)
    assert catalog_provider_name(s2) == "iceberg_rest"
    assert object_store_provider_name(s2) == "s3"


def test_explicit_provider_overrides_the_flag():
    # explicit wins even when use_real_adapters disagrees
    s = _settings(use_real_adapters=True, catalog_provider="local")
    assert catalog_provider_name(s) == "local"


def test_local_providers_are_constructed():
    s = _settings(use_real_adapters=False)
    assert isinstance(resolve_catalog(s), LocalCatalog)
    assert isinstance(resolve_object_store(s), LocalFSObjectStore)


def test_mixed_backends_are_now_expressible():
    # the whole point of the registry: a local catalog + an s3 object store,
    # which the old binary flag could not express.
    s = _settings(use_real_adapters=False, object_store_provider="s3")
    assert isinstance(resolve_catalog(s), LocalCatalog)
    assert isinstance(resolve_object_store(s), S3ObjectStore)
    # and the inverse
    s2 = _settings(use_real_adapters=False, catalog_provider="iceberg_rest")
    assert isinstance(resolve_catalog(s2), IcebergRestCatalog)
    assert isinstance(resolve_object_store(s2), LocalFSObjectStore)


def test_unknown_provider_is_a_clear_error():
    s = _settings(catalog_provider="cassandra")
    with pytest.raises(ValueError, match="unknown catalog provider 'cassandra'"):
        resolve_catalog(s)


def test_registry_is_extensible():
    reg: ProviderRegistry = ProviderRegistry("thing")
    reg.register("x", lambda _s: "built-x")
    assert reg.create("x", _settings()) == "built-x"
    assert reg.names() == ["x"]
