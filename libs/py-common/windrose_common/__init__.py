"""windrose-common: real shared adapters for the Windrose platform.

Every adapter here speaks a real wire protocol against local, protocol-compatible
infrastructure (MinIO/S3, Iceberg REST catalog, Vault, Redpanda/Kafka, OPA,
Redis, JWKS, OTLP). No stubs — see CONVENTIONS.md "END STATE".
"""

from __future__ import annotations

__version__ = "0.1.0"
