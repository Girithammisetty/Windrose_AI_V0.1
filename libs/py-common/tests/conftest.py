"""Integration fixtures for windrose-common.

Every test hits a REAL local dependency from deploy/docker-compose.dev.yml. Each
fixture pings its endpoint first and skips with a clear message when unreachable
(so the suite degrades gracefully on a machine without the infra up).
"""

from __future__ import annotations

import socket
import uuid

import pytest

MINIO = ("localhost", 9000)
ICEBERG = ("localhost", 8181)
VAULT = ("localhost", 8200)
KAFKA = ("localhost", 9092)
OPA = ("localhost", 8281)
REDIS = ("localhost", 6379)
POSTGRES = ("localhost", 5432)


def _reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def require(endpoint: tuple[str, int], name: str) -> None:
    if not _reachable(*endpoint):
        pytest.skip(f"{name} not reachable at {endpoint[0]}:{endpoint[1]} — is the dev infra up?")


@pytest.fixture
def unique() -> str:
    return uuid.uuid4().hex[:12]


@pytest.fixture
def minio():
    require(MINIO, "MinIO")


@pytest.fixture
def iceberg():
    require(ICEBERG, "Iceberg REST catalog")
    require(MINIO, "MinIO (Iceberg S3 FileIO)")


@pytest.fixture
def vault():
    require(VAULT, "Vault")


@pytest.fixture
def kafka():
    require(KAFKA, "Redpanda/Kafka")


@pytest.fixture
def opa():
    require(OPA, "OPA")
    require(REDIS, "Redis")


@pytest.fixture
def redis_up():
    require(REDIS, "Redis")


@pytest.fixture
def postgres():
    require(POSTGRES, "Postgres")
