"""Integration fixtures: real Postgres (Testcontainers) + alembic migrations +
non-privileged RLS role, plus helpers to register a REAL model in the running
MLflow server and seed a REAL input parquet in MinIO. Auto-skips when Docker or a
dev-infra endpoint is unreachable."""

from __future__ import annotations

import io
import os
import socket
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.container import build_container

ROOT = Path(__file__).resolve().parents[2]

APP_USER = "inference_rt"
APP_PASSWORD = "rt-secret"

MLFLOW_URI = "http://localhost:5500"
S3_ENDPOINT = "http://localhost:9000"
DATASETS_BUCKET = "windrose-datasets"

TABLES = [
    "inference_jobs", "scoring_schedules", "job_queue", "input_datasets",
    "output_datasets", "output_dataset_versions", "lineage_edges",
    "serving_endpoints", "outbox", "idempotency_keys", "processed_events",
]


def reachable(port: int) -> bool:
    try:
        with socket.create_connection(("localhost", port), timeout=1.0):
            return True
    except OSError:
        return False


def require_infra(*ports_names) -> None:
    for port, name in ports_names:
        if not reachable(port):
            pytest.skip(f"{name} not reachable on localhost:{port} — dev infra down")


@pytest.fixture(scope="session")
def pg():
    try:
        from testcontainers.postgres import PostgresContainer

        container = PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Docker unavailable — skipping integration tier: {exc}")
        return

    host = container.get_container_host_ip()
    port = container.get_exposed_port(5432)
    creds = (container.username, container.password, container.dbname)
    super_url = f"postgresql://{creds[0]}:{creds[1]}@{host}:{port}/{creds[2]}"

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    os.environ["INF_MIGRATE_URL"] = super_url.replace("postgresql://", "postgresql+psycopg://")
    command.upgrade(cfg, "head")

    import psycopg

    with psycopg.connect(super_url, autocommit=True) as conn:
        conn.execute(
            f"CREATE USER {APP_USER} LOGIN PASSWORD '{APP_PASSWORD}' IN ROLE inference_app")

    yield {
        "super_url": super_url,
        "app_url": f"postgresql+asyncpg://{APP_USER}:{APP_PASSWORD}@{host}:{port}/{creds[2]}",
        # DSN using the DEFAULT runtime role created by the migration (inference_app)
        # — proves the SHIPPED default (not a test-only role) enforces RLS.
        "default_url": (
            f"postgresql+asyncpg://inference_app:inference_app@{host}:{port}/{creds[2]}"),
        "host": host, "port": port, "db": creds[2],
    }
    container.stop()


@pytest.fixture
def _clean_db(pg):
    import psycopg

    with psycopg.connect(pg["super_url"], autocommit=True) as conn:
        conn.execute(f"TRUNCATE {', '.join(TABLES)} CASCADE")
    yield


@pytest.fixture
async def engine(pg, _clean_db):
    engine = create_async_engine(pg["app_url"], pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest.fixture
def real_settings() -> Settings:
    return Settings(
        use_real_adapters=True, mlflow_tracking_uri=MLFLOW_URI, s3_endpoint_url=S3_ENDPOINT,
        datasets_bucket=DATASETS_BUCKET)


@pytest.fixture
async def real_container(engine, real_settings):
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    # launch_run=None: tests drive execute_job explicitly for determinism.
    return build_container(real_settings, mode="sql", session_factory=session_factory)


def _s3():
    from windrose_common.objectstore import S3Config, build_s3_client

    return build_s3_client(S3Config.for_minio(DATASETS_BUCKET, endpoint_url=S3_ENDPOINT))


def register_real_model(name: str) -> int:
    """Train a tiny real sklearn model, log it to the running MLflow server with a
    signature, register it and promote it to Production. Returns the version."""
    os.environ["MLFLOW_S3_ENDPOINT_URL"] = S3_ENDPOINT
    os.environ["AWS_ACCESS_KEY_ID"] = "windrose"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "windrose_dev"

    import mlflow
    import mlflow.sklearn
    import numpy as np
    import pandas as pd
    from mlflow.models import infer_signature
    from mlflow.tracking import MlflowClient
    from sklearn.linear_model import LogisticRegression

    mlflow.set_tracking_uri(MLFLOW_URI)
    mlflow.set_experiment("inference-it")

    rng = np.random.default_rng(7)
    X = pd.DataFrame({
        "amount": rng.normal(100, 25, size=60).astype("float64"),
        "age": rng.integers(18, 80, size=60).astype("int64"),
    })
    y = (X["amount"] + X["age"] > 150).astype("int64")
    model = LogisticRegression(max_iter=200).fit(X, y)
    signature = infer_signature(X, model.predict(X))

    with mlflow.start_run():
        info = mlflow.sklearn.log_model(model, artifact_path="model", signature=signature,
                                        registered_model_name=name)
    client = MlflowClient(tracking_uri=MLFLOW_URI)
    version = int(info.registered_model_version) if getattr(
        info, "registered_model_version", None) else int(
        client.get_latest_versions(name, stages=["None"])[0].version)
    client.transition_model_version_stage(name, str(version), "Production",
                                          archive_existing_versions=False)
    return version


def seed_input_parquet(key: str, *, rows: int = 5, missing_age: bool = False) -> str:
    """Write a real input parquet to MinIO; return its s3:// uri."""
    import numpy as np
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    rng = np.random.default_rng(11)
    data = {"amount": rng.normal(120, 30, size=rows).astype("float64")}
    if not missing_age:
        data["age"] = rng.integers(20, 70, size=rows).astype("int64")
    df = pd.DataFrame(data)
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), buf)
    buf.seek(0)
    _s3().put_object(Bucket=DATASETS_BUCKET, Key=key, Body=buf.getvalue())
    return f"s3://{DATASETS_BUCKET}/{key}"


def read_output_parquet(storage_uri: str):
    import pyarrow.parquet as pq

    _, _, rest = storage_uri.partition("s3://")
    bucket, _, obj_key = rest.partition("/")
    body = _s3().get_object(Bucket=bucket, Key=obj_key)["Body"].read()
    return pq.read_table(io.BytesIO(body)).to_pandas()


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"
