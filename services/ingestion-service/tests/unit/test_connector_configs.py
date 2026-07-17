"""Config schema validation per connector type (ING-FR-002/003)."""

from __future__ import annotations

import pytest

from app.domain.connectors import (
    CONNECTOR_TYPES,
    SECRET_FIELDS,
    config_json_schema,
    dump_config,
    mask_secrets,
    validate_config,
    validate_secrets,
)
from app.domain.errors import ValidationFailedError

VALID = {
    "postgres": {"host": "db", "database": "sales", "username": "u"},
    "mysql": {"host": "db", "database": "sales", "username": "u"},
    "mariadb": {"host": "db", "database": "sales", "username": "u"},
    "oracle": {"host": "db", "service_name": "orcl", "username": "u"},
    "sqlserver": {"host": "db", "database": "sales", "username": "u"},
    "synapse": {"host": "db", "database": "sales", "username": "u"},
    "presto": {"host": "db", "catalog": "hive", "username": "u"},
    "bigquery": {"project_id": "p", "dataset": "d"},
    "snowflake": {"account": "acme", "username": "u", "warehouse": "wh", "database": "d"},
    "redshift": {"host": "rs", "database": "d", "username": "u"},
    "databricks": {"server_hostname": "dbc.example.com", "http_path": "/sql/1.0/x"},
    "spanner": {"project_id": "p", "instance_id": "i", "database": "d"},
    "salesforce": {"username": "u@acme.com"},
    "s3": {"bucket": "b"},
    "azure_blob": {"account_name": "acct", "container_name": "c"},
    "gcs": {"project_id": "p", "bucket": "b"},
    "sftp": {"host": "files.acme.io", "username": "u"},
    "ftp": {"host": "files.acme.io", "username": "u"},
    "http_api": {"url": "https://api.acme.io/export"},
}

EXPECTED_DEFAULTS = {
    "postgres": {"port": 5432, "ssl_mode": "require"},
    "mysql": {"port": 3306},
    "mariadb": {"port": 3306},
    "oracle": {"port": 1521},
    "sqlserver": {"port": 1433, "azure_ad": False},
    "synapse": {"port": 1433},
    "presto": {"port": 8080, "tls": True},
    "snowflake": {"schema": "PUBLIC"},
    "redshift": {"port": 5439},
    "salesforce": {"domain": "login", "api_version": "59.0"},
    "s3": {"region": "us-east-1", "root_prefix": "/", "file_format": "csv", "glob": None},
    "gcs": {"root_prefix": "/", "file_format": "csv", "glob": None},
    "azure_blob": {"root_prefix": "/", "file_format": "csv", "glob": None},
    "sftp": {"port": 22, "root_directory": "/"},
    "ftp": {"port": 21, "ftps": False, "file_format": "csv", "glob": None},
    "http_api": {"method": "GET"},
}


def test_catalog_covers_v1_parity_and_wave2_matrix() -> None:
    # 15 V1-parity types + wave-2 matrix (redshift, databricks, spanner, salesforce)
    assert len(CONNECTOR_TYPES) == 19
    assert set(VALID) == set(CONNECTOR_TYPES)
    assert set(SECRET_FIELDS) == set(CONNECTOR_TYPES)


@pytest.mark.parametrize("connector_type", CONNECTOR_TYPES)
def test_valid_config_accepted_with_defaults(connector_type: str) -> None:
    model = validate_config(connector_type, VALID[connector_type])
    dumped = dump_config(model)
    assert "connector_type" not in dumped
    for key, expected in EXPECTED_DEFAULTS.get(connector_type, {}).items():
        assert getattr(model, key) == expected


@pytest.mark.parametrize("connector_type", CONNECTOR_TYPES)
def test_unknown_field_rejected_with_field_detail(connector_type: str) -> None:
    payload = {**VALID[connector_type], "totally_bogus": 1}
    with pytest.raises(ValidationFailedError) as exc:
        validate_config(connector_type, payload)
    assert exc.value.status == 422
    assert any("totally_bogus" in d["field"] for d in exc.value.details)


@pytest.mark.parametrize("connector_type", CONNECTOR_TYPES)
def test_missing_required_field_rejected(connector_type: str) -> None:
    payload = dict(VALID[connector_type])
    removed = next(iter(payload))
    payload.pop(removed)
    with pytest.raises(ValidationFailedError) as exc:
        validate_config(connector_type, payload)
    assert any(removed in d["field"] for d in exc.value.details)


@pytest.mark.parametrize("connector_type", sorted(SECRET_FIELDS))
def test_secret_fields_rejected_inside_config(connector_type: str) -> None:
    """ING-FR-003: secret material must never ride in the plain config."""
    secret_field = sorted(SECRET_FIELDS[connector_type])[0]
    payload = {**VALID[connector_type], secret_field: "super-secret"}
    with pytest.raises(ValidationFailedError):
        validate_config(connector_type, payload)


def test_unknown_connector_type_rejected() -> None:
    with pytest.raises(ValidationFailedError) as exc:
        validate_config("mongodb", {"host": "x"})
    assert exc.value.details[0]["field"] == "connector_type"


def test_validate_secrets_allows_known_and_rejects_unknown() -> None:
    assert validate_secrets("postgres", {"password": "pw"}) == {"password": "pw"}
    with pytest.raises(ValidationFailedError) as exc:
        validate_secrets("postgres", {"passw0rd": "pw"})
    assert exc.value.details[0]["field"] == "secrets.passw0rd"
    with pytest.raises(ValidationFailedError):
        validate_secrets("postgres", {"password": ""})  # empty secret


def test_http_api_authorization_header_must_use_secrets() -> None:
    with pytest.raises(ValidationFailedError) as exc:
        validate_config(
            "http_api", {"url": "https://x", "headers": {"AUTHORIZATION": "Bearer abc"}}
        )
    assert any("headers" in d["field"] for d in exc.value.details)
    # the sanctioned way:
    validate_secrets("http_api", {"auth_header_value": "Bearer abc"})


def test_s3_role_arn_needs_no_secret() -> None:
    model = validate_config("s3", {"bucket": "b", "role_arn": "arn:aws:iam::1:role/x"})
    assert dump_config(model)["role_arn"].startswith("arn:")
    assert validate_secrets("s3", {}) == {}


@pytest.mark.parametrize("connector_type", CONNECTOR_TYPES)
def test_json_schema_declared_per_type(connector_type: str) -> None:
    schema = config_json_schema(connector_type)
    assert schema["additionalProperties"] is False
    assert "properties" in schema


def test_mask_secrets_marker() -> None:
    assert mask_secrets(["password"]) == {"password": "•••"}
