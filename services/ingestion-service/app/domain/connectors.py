"""Connector-type catalog (ING-FR-002, BRD 03 §4.2).

Typed config schemas as a pydantic discriminated union. Unknown fields are
rejected (`extra="forbid"`) and surface as VALIDATION_FAILED with per-field
details. Secret fields never appear in configs — they are supplied in a
separate `secrets` object and stored via the SecretsStore (ING-FR-003).
"""

from __future__ import annotations

import warnings
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from app.domain.errors import ValidationFailedError

# BRD §4.2 names a config field `schema` (presto/snowflake); the pydantic
# parent-attribute shadow warning is harmless here.
warnings.filterwarnings(
    "ignore",
    message=r'Field name "schema" .*',
    category=UserWarning,
)


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="forbid")


# File formats an object-store / file source decodes (matches decode.FILE_FORMATS).
FileFormat = Literal["csv", "tsv", "json", "jsonl", "parquet", "avro", "xml"]


class PostgresConfig(_Cfg):
    connector_type: Literal["postgres"] = "postgres"
    host: str
    port: int = 5432
    database: str
    username: str
    ssl_mode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = (
        "require"
    )


class MysqlConfig(_Cfg):
    connector_type: Literal["mysql"] = "mysql"
    host: str
    port: int = 3306
    database: str
    username: str


class MariadbConfig(_Cfg):
    connector_type: Literal["mariadb"] = "mariadb"
    host: str
    port: int = 3306
    database: str
    username: str


class OracleConfig(_Cfg):
    connector_type: Literal["oracle"] = "oracle"
    host: str
    port: int = 1521
    service_name: str
    username: str


class SqlserverConfig(_Cfg):
    connector_type: Literal["sqlserver"] = "sqlserver"
    host: str
    port: int = 1433
    database: str
    username: str
    azure_ad: bool = False


class SynapseConfig(_Cfg):
    connector_type: Literal["synapse"] = "synapse"
    host: str
    port: int = 1433
    database: str
    username: str


class PrestoConfig(_Cfg):
    """Execution via Trino driver (V1 'presto' parity)."""

    connector_type: Literal["presto"] = "presto"
    host: str
    port: int = 8080
    catalog: str
    schema: str | None = None
    username: str
    tls: bool = True


class BigqueryConfig(_Cfg):
    connector_type: Literal["bigquery"] = "bigquery"
    project_id: str
    dataset: str


class SnowflakeConfig(_Cfg):
    connector_type: Literal["snowflake"] = "snowflake"
    account: str
    username: str
    warehouse: str
    database: str
    schema: str = "PUBLIC"
    role: str | None = None


class RedshiftConfig(_Cfg):
    """Amazon Redshift (Postgres-wire compatible endpoint)."""

    connector_type: Literal["redshift"] = "redshift"
    host: str
    port: int = 5439
    database: str
    username: str


class DatabricksConfig(_Cfg):
    """Databricks SQL warehouse / cluster (databricks-sql-connector)."""

    connector_type: Literal["databricks"] = "databricks"
    server_hostname: str
    http_path: str
    catalog: str | None = None
    schema: str | None = None


class SpannerConfig(_Cfg):
    """Google Cloud Spanner (google-cloud-spanner)."""

    connector_type: Literal["spanner"] = "spanner"
    project_id: str
    instance_id: str
    database: str


class SalesforceConfig(_Cfg):
    """Salesforce SOQL over the REST/Bulk API (OAuth2 password flow).

    ``domain`` selects the login host: ``login`` (production) or ``test``
    (sandbox); an explicit ``instance_url`` overrides host discovery.
    """

    connector_type: Literal["salesforce"] = "salesforce"
    username: str
    domain: Literal["login", "test"] = "login"
    instance_url: str | None = None
    api_version: str = "59.0"


class S3Config(_Cfg):
    """S3 / S3-compatible object store as a data-lake source (ING-FR-064).

    ``root_prefix`` bounds the listing; ``glob`` further filters object keys
    (fnmatch); ``file_format`` selects the streaming decoder. ``endpoint`` targets
    an S3-compatible store (MinIO/Ceph); omit it for real AWS S3.
    """

    connector_type: Literal["s3"] = "s3"
    region: str = "us-east-1"
    bucket: str
    root_prefix: str = "/"
    endpoint: str | None = None
    role_arn: str | None = None  # role-based auth: no secret material needed
    file_format: FileFormat = "csv"
    glob: str | None = None


class AzureBlobConfig(_Cfg):
    connector_type: Literal["azure_blob"] = "azure_blob"
    account_name: str
    container_name: str
    root_prefix: str = "/"
    file_format: FileFormat = "csv"
    glob: str | None = None


class GcsConfig(_Cfg):
    connector_type: Literal["gcs"] = "gcs"
    project_id: str
    bucket: str
    root_prefix: str = "/"
    file_format: FileFormat = "csv"
    glob: str | None = None


class SftpConfig(_Cfg):
    """ING-FR-008: protocol explicit (V1 inferred SFTP from port==22)."""

    connector_type: Literal["sftp"] = "sftp"
    host: str
    port: int = 22
    username: str
    root_directory: str = "/"


class FtpConfig(_Cfg):
    connector_type: Literal["ftp"] = "ftp"
    host: str
    port: int = 21
    username: str
    root_directory: str = "/"
    ftps: bool = False
    file_format: FileFormat = "csv"
    glob: str | None = None


class HttpApiConfig(_Cfg):
    """Structured request spec — V1 raw `curl_command` is retired (BR-6)."""

    connector_type: Literal["http_api"] = "http_api"
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"] = "GET"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None
    pagination: dict[str, Any] | None = None

    @field_validator("headers")
    @classmethod
    def _no_auth_headers(cls, v: dict[str, str]) -> dict[str, str]:
        for key in v:
            if key.lower() == "authorization":
                raise ValueError(
                    "Authorization header must be supplied via secrets.auth_header_value"
                )
        return v


ConnectorConfig = Annotated[
    PostgresConfig
    | MysqlConfig
    | MariadbConfig
    | OracleConfig
    | SqlserverConfig
    | SynapseConfig
    | PrestoConfig
    | BigqueryConfig
    | SnowflakeConfig
    | RedshiftConfig
    | DatabricksConfig
    | SpannerConfig
    | SalesforceConfig
    | S3Config
    | AzureBlobConfig
    | GcsConfig
    | SftpConfig
    | FtpConfig
    | HttpApiConfig,
    Field(discriminator="connector_type"),
]

_ADAPTER: TypeAdapter[Any] = TypeAdapter(ConnectorConfig)

CONNECTOR_TYPES: tuple[str, ...] = (
    "postgres",
    "mysql",
    "mariadb",
    "oracle",
    "sqlserver",
    "synapse",
    "presto",
    "bigquery",
    "snowflake",
    "redshift",
    "databricks",
    "spanner",
    "salesforce",
    "s3",
    "azure_blob",
    "gcs",
    "sftp",
    "ftp",
    "http_api",
)

# ING-FR-003: allowed write-only secret fields per connector type.
SECRET_FIELDS: dict[str, frozenset[str]] = {
    "postgres": frozenset({"password"}),
    "mysql": frozenset({"password"}),
    "mariadb": frozenset({"password"}),
    "oracle": frozenset({"password"}),
    "sqlserver": frozenset({"password"}),
    "synapse": frozenset({"password"}),
    "presto": frozenset({"password"}),
    "bigquery": frozenset({"credentials_json"}),
    "snowflake": frozenset({"password", "private_key"}),
    "redshift": frozenset({"password"}),
    "databricks": frozenset({"access_token"}),
    "spanner": frozenset({"credentials_json"}),
    "salesforce": frozenset({"password", "security_token", "client_id", "client_secret"}),
    "s3": frozenset({"access_key_id", "secret_access_key"}),
    "azure_blob": frozenset({"account_key", "sas_token"}),
    "gcs": frozenset({"credentials_json"}),
    "sftp": frozenset({"password", "private_key"}),
    "ftp": frozenset({"password"}),
    "http_api": frozenset({"auth_header_value", "basic_username", "basic_password"}),
}

MASKED = "•••"


def _validation_details(exc: ValidationError) -> list[dict[str, str]]:
    details = []
    for err in exc.errors():
        loc = [str(p) for p in err["loc"] if str(p) not in CONNECTOR_TYPES]
        details.append({"field": ".".join(loc) or "config", "message": err["msg"]})
    return details


def validate_config(connector_type: str, config: dict[str, Any]) -> BaseModel:
    """Validate a non-secret config dict against its typed schema."""
    if connector_type not in CONNECTOR_TYPES:
        raise ValidationFailedError(
            "unsupported connector_type",
            details=[
                {"field": "connector_type", "message": f"must be one of {sorted(CONNECTOR_TYPES)}"}
            ],
        )
    if not isinstance(config, dict):
        raise ValidationFailedError(
            "config must be an object",
            details=[{"field": "config", "message": "must be an object"}],
        )
    payload = dict(config)
    payload["connector_type"] = connector_type
    try:
        return _ADAPTER.validate_python(payload)
    except ValidationError as exc:
        raise ValidationFailedError(
            "invalid connector config", details=_validation_details(exc)
        ) from exc


def dump_config(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(exclude={"connector_type"}, exclude_none=True)


def validate_secrets(connector_type: str, secrets: dict[str, Any]) -> dict[str, str]:
    """Validate write-only secret fields; returns the cleaned secret dict."""
    allowed = SECRET_FIELDS.get(connector_type, frozenset())
    details = []
    for key, value in secrets.items():
        if key not in allowed:
            details.append(
                {"field": f"secrets.{key}", "message": f"unknown secret field for {connector_type}"}
            )
        elif not isinstance(value, str) or not value:
            details.append({"field": f"secrets.{key}", "message": "must be a non-empty string"})
    if details:
        raise ValidationFailedError("invalid secrets", details=details)
    return dict(secrets)


def mask_secrets(secret_field_names: list[str]) -> dict[str, str]:
    """FR-003: reads return masked markers only."""
    return dict.fromkeys(secret_field_names, MASKED)


CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "postgres": PostgresConfig,
    "mysql": MysqlConfig,
    "mariadb": MariadbConfig,
    "oracle": OracleConfig,
    "sqlserver": SqlserverConfig,
    "synapse": SynapseConfig,
    "presto": PrestoConfig,
    "bigquery": BigqueryConfig,
    "snowflake": SnowflakeConfig,
    "redshift": RedshiftConfig,
    "databricks": DatabricksConfig,
    "spanner": SpannerConfig,
    "salesforce": SalesforceConfig,
    "s3": S3Config,
    "azure_blob": AzureBlobConfig,
    "gcs": GcsConfig,
    "sftp": SftpConfig,
    "ftp": FtpConfig,
    "http_api": HttpApiConfig,
}


def config_json_schema(connector_type: str) -> dict[str, Any]:
    """JSON Schema per connector type (ING-FR-002; used by the MCP facade)."""
    return CONFIG_MODELS[connector_type].model_json_schema()


# --- UI catalog (ING-FR-002): human display name + category per connector type.
# `category` groups the connector picker; it drives no behaviour (display only).
CONNECTOR_DISPLAY_NAMES: dict[str, str] = {
    "postgres": "PostgreSQL",
    "mysql": "MySQL",
    "mariadb": "MariaDB",
    "oracle": "Oracle Database",
    "sqlserver": "SQL Server",
    "synapse": "Azure Synapse",
    "presto": "Presto / Trino",
    "bigquery": "Google BigQuery",
    "snowflake": "Snowflake",
    "redshift": "Amazon Redshift",
    "databricks": "Databricks SQL",
    "spanner": "Cloud Spanner",
    "salesforce": "Salesforce",
    "s3": "Amazon S3",
    "azure_blob": "Azure Blob Storage",
    "gcs": "Google Cloud Storage",
    "sftp": "SFTP",
    "ftp": "FTP",
    "http_api": "HTTP API",
}

CONNECTOR_CATEGORIES: dict[str, str] = {
    "postgres": "database",
    "mysql": "database",
    "mariadb": "database",
    "oracle": "database",
    "sqlserver": "database",
    "spanner": "database",
    "synapse": "warehouse",
    "presto": "warehouse",
    "bigquery": "warehouse",
    "snowflake": "warehouse",
    "redshift": "warehouse",
    "databricks": "warehouse",
    "s3": "object-store",
    "azure_blob": "object-store",
    "gcs": "object-store",
    "sftp": "file",
    "ftp": "file",
    "salesforce": "saas",
    "http_api": "saas",
}

# Curated fallback help text keyed by field name; the per-field description on the
# pydantic model (JSON Schema `description`) always wins when present. This keeps
# the catalog useful for the dynamic UI form without duplicating the schema.
_FIELD_HELP: dict[str, str] = {
    "host": "Hostname or IP address of the server.",
    "port": "TCP port the server listens on.",
    "database": "Name of the database / schema to read from.",
    "username": "Login user for authentication.",
    "password": "Login password (stored write-only in Vault).",
    "ssl_mode": "TLS negotiation mode for the connection.",
    "service_name": "Oracle service name (SID alternative).",
    "account": "Snowflake account identifier (e.g. xy12345.us-east-1).",
    "warehouse": "Snowflake virtual warehouse to run queries on.",
    "schema": "Default schema to resolve unqualified object names.",
    "role": "Role to assume for the session (optional).",
    "project_id": "Google Cloud project id.",
    "dataset": "BigQuery dataset id.",
    "instance_id": "Spanner instance id.",
    "server_hostname": "Databricks workspace host (adb-*.azuredatabricks.net).",
    "http_path": "Databricks SQL warehouse / cluster HTTP path.",
    "catalog": "Catalog (Unity/Trino) to resolve objects against.",
    "region": "Cloud region of the bucket / endpoint.",
    "bucket": "Object-store bucket name.",
    "root_prefix": "Key prefix that bounds listing (acts as the source root).",
    "endpoint": "Custom S3-compatible endpoint (MinIO/Ceph); omit for AWS.",
    "role_arn": "IAM role ARN for role-based access (no static keys needed).",
    "file_format": "Decoder applied to each object streamed from the source.",
    "glob": "fnmatch pattern further filtering object keys.",
    "account_name": "Azure Storage account name.",
    "container_name": "Azure Blob container name.",
    "root_directory": "Remote directory that bounds file listing.",
    "ftps": "Use implicit FTPS (TLS) instead of plain FTP.",
    "azure_ad": "Authenticate via Azure AD instead of SQL login.",
    "method": "HTTP method for the request.",
    "url": "Absolute request URL.",
    "headers": "Static request headers (Authorization is rejected — use secrets).",
    "body": "Optional request body sent verbatim.",
    "pagination": "Pagination spec ({cursor|page|offset}, applied by the driver).",
    "domain": "Salesforce login host: login (prod) or test (sandbox).",
    "instance_url": "Explicit Salesforce instance URL (overrides host discovery).",
    "api_version": "Salesforce REST API version.",
    "access_key_id": "Access key id (stored write-only in Vault).",
    "secret_access_key": "Secret access key (stored write-only in Vault).",
    "account_key": "Azure storage account key (stored write-only in Vault).",
    "sas_token": "Azure SAS token (stored write-only in Vault).",
    "credentials_json": "Service-account credentials JSON (stored write-only in Vault).",
    "access_token": "Databricks personal access token (stored write-only in Vault).",
    "private_key": "PEM private key for key-pair auth (stored write-only in Vault).",
    "security_token": "Salesforce security token (stored write-only in Vault).",
    "client_id": "OAuth client id (stored write-only in Vault).",
    "client_secret": "OAuth client secret (stored write-only in Vault).",
    "auth_header_value": "Value for the Authorization header (stored write-only).",
    "basic_username": "HTTP basic-auth username (stored write-only in Vault).",
    "basic_password": "HTTP basic-auth password (stored write-only in Vault).",
    "tls": "Negotiate TLS for the connection.",
}

_JSON_TYPE = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "object": "object",
    "array": "array",
}


def _field_type(prop: dict[str, Any]) -> tuple[str, list[Any] | None]:
    """Reduce a JSON-Schema property to a UI widget type + optional enum values."""
    if "enum" in prop:
        return "enum", list(prop["enum"])
    t = prop.get("type")
    if t in _JSON_TYPE:
        return _JSON_TYPE[t], None
    for sub in prop.get("anyOf", []):  # optional/union: first non-null member wins
        if sub.get("type") == "null":
            continue
        if "enum" in sub:
            return "enum", list(sub["enum"])
        st = sub.get("type")
        if st in _JSON_TYPE:
            return _JSON_TYPE[st], None
    return "string", None


def _catalog_fields(connector_type: str) -> list[dict[str, Any]]:
    """The dynamic-form field list for a connector: config fields (non-secret)
    derived from the pydantic model, followed by the write-only secret fields."""
    js = config_json_schema(connector_type)
    props: dict[str, Any] = js.get("properties", {})
    required = set(js.get("required", []))
    fields: list[dict[str, Any]] = []
    for name, prop in props.items():
        if name == "connector_type":  # the discriminator is implicit, never shown
            continue
        ftype, enum = _field_type(prop)
        fields.append(
            {
                "name": name,
                "type": ftype,
                "required": name in required,
                "secret": False,
                "default": prop.get("default"),
                "enum": enum,
                "help": prop.get("description") or _FIELD_HELP.get(name),
            }
        )
    for name in sorted(SECRET_FIELDS.get(connector_type, frozenset())):
        # Secrets are supplied out-of-band (never in the config model). They are
        # optional at the schema level — role/keypair auth may need none; the
        # test-connection probe is the real gate (ING-FR-003/004).
        fields.append(
            {
                "name": name,
                "type": "string",
                "required": False,
                "secret": True,
                "default": None,
                "enum": None,
                "help": _FIELD_HELP.get(name),
            }
        )
    return fields


def connector_catalog_entry(connector_type: str) -> dict[str, Any]:
    """One catalog entry: display metadata + the dynamic-form field schema +
    the raw JSON Schema (kept for MCP/`get_connection_schema` parity)."""
    return {
        "connector_type": connector_type,
        "display_name": CONNECTOR_DISPLAY_NAMES[connector_type],
        "category": CONNECTOR_CATEGORIES[connector_type],
        "fields": _catalog_fields(connector_type),
        "secret_fields": sorted(SECRET_FIELDS.get(connector_type, frozenset())),
        "config_schema": config_json_schema(connector_type),
    }


def connector_catalog() -> list[dict[str, Any]]:
    """The full connector-type catalog for every supported type (ING-FR-002)."""
    return [connector_catalog_entry(t) for t in CONNECTOR_TYPES]
