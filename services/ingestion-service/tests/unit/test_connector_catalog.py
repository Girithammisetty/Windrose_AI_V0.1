"""Connector-type catalog endpoint (ING-FR-002).

The UI renders per-type connection forms from this catalog, so every one of the
supported connector types must expose a display name, a category, a per-field
schema (name/type/required/default/enum/help) and the correct secret flags.
"""

from __future__ import annotations

from app.domain.connectors import CONNECTOR_TYPES, SECRET_FIELDS

VALID_CATEGORIES = {"database", "warehouse", "object-store", "file", "saas"}


def _by_name(fields: list[dict]) -> dict[str, dict]:
    return {f["name"]: f for f in fields}


async def test_catalog_lists_every_connector_type_with_metadata(client, auth_a) -> None:
    resp = await client.get("/api/v1/connector-types", headers=auth_a)
    assert resp.status_code == 200
    data = resp.json()["data"]
    # Every supported type is present exactly once.
    assert [e["connector_type"] for e in data] == list(CONNECTOR_TYPES)
    assert len(data) == len(CONNECTOR_TYPES) == 19

    for entry in data:
        assert entry["display_name"], entry["connector_type"]
        assert entry["category"] in VALID_CATEGORIES
        assert entry["config_schema"]["additionalProperties"] is False
        # secret_fields exactly mirrors the SECRET_FIELDS registry.
        assert set(entry["secret_fields"]) == set(SECRET_FIELDS[entry["connector_type"]])
        # Every declared secret is present in `fields` and flagged secret.
        fields = _by_name(entry["fields"])
        for secret in entry["secret_fields"]:
            assert fields[secret]["secret"] is True
            assert fields[secret]["type"] == "string"
        # Non-secret fields never carry the secret flag.
        for name, f in fields.items():
            if name not in entry["secret_fields"]:
                assert f["secret"] is False


async def test_catalog_postgres_field_schema(client, auth_a) -> None:
    resp = await client.get("/api/v1/connector-types/postgres", headers=auth_a)
    assert resp.status_code == 200
    entry = resp.json()["data"]
    assert entry["display_name"] == "PostgreSQL"
    assert entry["category"] == "database"
    fields = _by_name(entry["fields"])

    assert fields["host"] == {
        "name": "host",
        "type": "string",
        "required": True,
        "secret": False,
        "default": None,
        "enum": None,
        "help": fields["host"]["help"],
    }
    assert fields["host"]["help"]  # help present (curated or model-derived)
    assert fields["port"]["type"] == "integer"
    assert fields["port"]["required"] is False
    assert fields["port"]["default"] == 5432
    assert fields["database"]["required"] is True
    assert fields["username"]["required"] is True
    # ssl_mode is an enum with a default and no secret flag.
    assert fields["ssl_mode"]["type"] == "enum"
    assert fields["ssl_mode"]["default"] == "require"
    assert set(fields["ssl_mode"]["enum"]) == {
        "disable",
        "allow",
        "prefer",
        "require",
        "verify-ca",
        "verify-full",
    }
    # password is the only secret, write-only.
    assert entry["secret_fields"] == ["password"]
    assert fields["password"]["secret"] is True
    assert fields["password"]["required"] is False


async def test_catalog_s3_field_schema(client, auth_a) -> None:
    entry = (await client.get("/api/v1/connector-types/s3", headers=auth_a)).json()["data"]
    assert entry["category"] == "object-store"
    fields = _by_name(entry["fields"])
    assert fields["bucket"]["required"] is True
    assert fields["region"]["default"] == "us-east-1"
    assert fields["root_prefix"]["default"] == "/"
    assert fields["file_format"]["type"] == "enum"
    assert set(fields["file_format"]["enum"]) == {
        "csv", "tsv", "json", "jsonl", "parquet", "avro", "xml"
    }
    # optional object-store fields are not required
    assert fields["endpoint"]["required"] is False
    assert fields["role_arn"]["required"] is False
    # both AWS-key secrets are present and flagged
    assert entry["secret_fields"] == ["access_key_id", "secret_access_key"]
    assert fields["access_key_id"]["secret"] is True
    assert fields["secret_access_key"]["secret"] is True


async def test_catalog_salesforce_field_schema(client, auth_a) -> None:
    entry = (await client.get("/api/v1/connector-types/salesforce", headers=auth_a)).json()["data"]
    assert entry["category"] == "saas"
    fields = _by_name(entry["fields"])
    assert fields["username"]["required"] is True
    assert fields["domain"]["type"] == "enum"
    assert set(fields["domain"]["enum"]) == {"login", "test"}
    assert fields["domain"]["default"] == "login"
    assert fields["api_version"]["default"] == "59.0"
    # salesforce has four write-only secrets
    assert entry["secret_fields"] == ["client_id", "client_secret", "password", "security_token"]
    for s in entry["secret_fields"]:
        assert fields[s]["secret"] is True


async def test_catalog_unknown_type_is_404(client, auth_a) -> None:
    resp = await client.get("/api/v1/connector-types/mongodb", headers=auth_a)
    assert resp.status_code == 404


async def test_catalog_requires_auth(client) -> None:
    resp = await client.get("/api/v1/connector-types")
    assert resp.status_code == 401
