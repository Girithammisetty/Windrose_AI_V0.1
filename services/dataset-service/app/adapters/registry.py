"""Swappable dependency provider registries (Phase 3).

The catalog and object-store backends are pluggable by NAME instead of a single
binary `use_real_adapters` flag. Each provider is a factory keyed by a stable
name; a deployment selects one via config (`catalog_provider` /
`object_store_provider`). This:

- **decouples** the two dependencies — you can run a `local` catalog with an
  `s3` object store, or `iceberg_rest` with a `local` store — which the old
  flag could not express;
- makes adding a backend a one-line `register(...)` with NO wiring change
  (e.g. an `azure_blob`/`gcs` object store, once its adapter exists);
- keeps 100% backward compatibility: when a provider is not named explicitly,
  it is derived from `use_real_adapters` exactly as before.

Providers are built lazily (the factory imports its adapter on demand) so a
deployment never imports a backend it doesn't use (e.g. pyiceberg).
"""

from __future__ import annotations

from collections.abc import Callable

from app.config import Settings

# A provider factory builds a backend from settings. Kept lazy so heavy adapter
# imports (pyiceberg, botocore) happen only for the selected provider.
type ProviderFactory[T] = Callable[[Settings], T]


class ProviderRegistry[T]:
    """A name -> factory registry for a swappable dependency."""

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._factories: dict[str, ProviderFactory[T]] = {}

    def register(self, name: str, factory: ProviderFactory[T]) -> None:
        self._factories[name] = factory

    def names(self) -> list[str]:
        return sorted(self._factories)

    def create(self, name: str, settings: Settings) -> T:
        factory = self._factories.get(name)
        if factory is None:
            raise ValueError(
                f"unknown {self._kind} provider {name!r}; "
                f"registered: {', '.join(self.names()) or '(none)'}"
            )
        return factory(settings)


# ---- catalog providers -----------------------------------------------------

def _local_catalog(settings: Settings):
    from app.adapters.catalog import LocalCatalog

    return LocalCatalog(settings.catalog_dir)


def _iceberg_rest_catalog(settings: Settings):
    from app.adapters.catalog import IcebergRestCatalog

    return IcebergRestCatalog(
        settings.iceberg_catalog_uri,
        warehouse=settings.iceberg_warehouse,
        s3_endpoint=settings.s3_endpoint_url,
        s3_access_key=settings.s3_access_key,
        s3_secret_key=settings.s3_secret_key,
        s3_region=settings.s3_region,
    )


CATALOG_PROVIDERS: ProviderRegistry = ProviderRegistry("catalog")
CATALOG_PROVIDERS.register("local", _local_catalog)
CATALOG_PROVIDERS.register("iceberg_rest", _iceberg_rest_catalog)


# ---- object-store providers ------------------------------------------------

def _local_object_store(settings: Settings):
    from app.adapters.object_store import LocalFSObjectStore

    return LocalFSObjectStore(settings.object_store_dir)


def _s3_object_store(settings: Settings):
    from app.adapters.object_store import S3ObjectStore

    return S3ObjectStore(
        settings.profiles_bucket,
        endpoint_url=settings.s3_endpoint_url,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
    )


OBJECT_STORE_PROVIDERS: ProviderRegistry = ProviderRegistry("object_store")
OBJECT_STORE_PROVIDERS.register("local", _local_object_store)
OBJECT_STORE_PROVIDERS.register("s3", _s3_object_store)


# ---- selection -------------------------------------------------------------

def catalog_provider_name(settings: Settings) -> str:
    """Explicit `catalog_provider` wins; else derive from use_real_adapters
    (the pre-registry default) so existing deployments are unchanged."""
    return settings.catalog_provider or ("iceberg_rest" if settings.use_real_adapters else "local")


def object_store_provider_name(settings: Settings) -> str:
    return settings.object_store_provider or ("s3" if settings.use_real_adapters else "local")


def resolve_catalog(settings: Settings):
    return CATALOG_PROVIDERS.create(catalog_provider_name(settings), settings)


def resolve_object_store(settings: Settings):
    return OBJECT_STORE_PROVIDERS.create(object_store_provider_name(settings), settings)
