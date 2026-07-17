"""Real connection drivers, wired into the runtime registries.

Two tiers, both **real** (no runtime stubs — CONVENTIONS.md END STATE):

* **Local-protocol, locally verified** — dockerized equivalents exist, so these
  are exercised end-to-end in the integration tier:

      postgres  -> asyncpg   (probe SELECT 1, prepare-for-columns, streaming cursor)
      mysql     -> aiomysql  (probe SELECT 1, LIMIT 0 columns, SSCursor stream)
      mariadb   -> aiomysql  (same wire protocol as mysql)
      sqlserver -> pymssql   (TDS; probe SELECT 1, TOP 0 columns, fetchmany stream)
      synapse   -> pymssql   (Synapse T-SQL / TDS endpoint)
      oracle    -> oracledb  (THIN async; SELECT 1 FROM dual, FETCH-first columns)
      sftp      -> asyncssh  (probe LIST, streaming file fetch to the object store)
      ftp       -> aioftp    (probe LIST, streaming file fetch to the object store)
      http_api  -> httpx     (probe HEAD/GET, streaming fetch to the object store)
      s3        -> boto3      (list bucket probe, incremental list + stream-decode;
                              verified live against MinIO — the local S3 API)

* **Object-store SOURCE connectors** (data-lake buckets: list under a prefix →
  filter by glob + incremental object-mtime watermark → stream-decode each file
  into bronze, memory-bounded). S3 is locally verified against MinIO; GCS and
  Azure Blob are credential-gated (real SDK, contract-tested with an injected
  client, live test skips with a "needs <X> credentials" reason):

      s3         -> boto3                (MinIO / real AWS S3 / S3-compatible)
      gcs        -> google-cloud-storage (credential-gated)
      azure_blob -> azure-storage-blob   (credential-gated)

* **Credential-gated, real SDK, contract-tested** — the adapter drives the real
  vendor SDK, but live end-to-end verification needs real customer credentials
  (CONVENTIONS.md "one honest ceiling"). Registered here so the runtime path
  resolves to the real driver (never a fake); a mocked-transport contract test
  exercises request/response shaping offline:

      snowflake  -> snowflake-connector-python
      redshift   -> redshift-connector
      databricks -> databricks-sql-connector
      bigquery   -> google-cloud-bigquery
      spanner    -> google-cloud-spanner (also runs against the Spanner emulator)
      salesforce -> httpx over the REST Query API + OAuth2

Watermark values are ALWAYS bound as driver-level parameters — the SQL text
carries only a placeholder, translated to each driver's native style with the
typed value passed out-of-band (ING-FR-061, BR-5). The sole protocol ceiling is
Salesforce SOQL (no bind facility): its typed ``datetime`` watermark is rendered
through an injection-safe canonical-literal formatter (see ``salesforce.py``).
No string interpolation of untyped values ever happens.
"""

from __future__ import annotations

from app.config import Settings
from app.domain.drivers.azure_blob import azure_blob_client_factory
from app.domain.drivers.bigquery import (
    BigQueryPreviewer,
    BigQueryProber,
    BigQueryQuerySource,
)
from app.domain.drivers.databricks import databricks_dialect
from app.domain.drivers.dbapi import DbapiPreviewer, DbapiProber, DbapiQuerySource
from app.domain.drivers.fetch import FetcherRegistry, SourceFetcher
from app.domain.drivers.ftp import FtpProber, FtpSourceFetcher, FtpSourcePreviewer
from app.domain.drivers.gcs import gcs_client_factory
from app.domain.drivers.http import HttpProber, HttpSourceFetcher, HttpSourcePreviewer
from app.domain.drivers.mssql import SqlServerPreviewer, SqlServerProber, SqlServerQuerySource
from app.domain.drivers.mysql import MysqlPreviewer, MysqlProber, MysqlQuerySource
from app.domain.drivers.objectsource import (
    ObjectSourceIngestor,
    ObjectStoreProber,
    ObjectStoreSourceFetcher,
    ObjectStoreSourcePreviewer,
)
from app.domain.drivers.oracle import OraclePreviewer, OracleProber, OracleQuerySource
from app.domain.drivers.postgres import PostgresPreviewer, PostgresProber, PostgresQuerySource
from app.domain.drivers.preview import DispatchingSourcePreviewer
from app.domain.drivers.redshift import redshift_dialect
from app.domain.drivers.s3 import s3_client_factory
from app.domain.drivers.salesforce import (
    SalesforcePreviewer,
    SalesforceProber,
    SalesforceQuerySource,
)
from app.domain.drivers.sftp import SftpProber, SftpSourceFetcher, SftpSourcePreviewer
from app.domain.drivers.snowflake import snowflake_dialect
from app.domain.drivers.spanner import SpannerPreviewer, SpannerProber, SpannerQuerySource
from app.domain.probers import ProberRegistry, SourcePreviewer
from app.domain.querysource import QuerySourceRegistry

__all__ = [
    "DispatchingSourcePreviewer",
    "FetcherRegistry",
    "ObjectSourceIngestor",
    "SourceFetcher",
    "wire_local_drivers",
]

# Connector types with a real local-protocol driver, verified against dockerized
# infra (or the local S3 API, MinIO) in the integration tier.
LOCAL_DRIVER_TYPES: tuple[str, ...] = (
    "postgres",
    "mysql",
    "mariadb",
    "sqlserver",
    "synapse",
    "oracle",
    "sftp",
    "ftp",
    "http_api",
    "s3",
)

# Connector types whose real SDK adapter is credential-gated (contract-tested;
# live test skips with a "needs <X> credentials" reason). Includes the two
# credential-gated object stores (gcs, azure_blob).
CREDENTIAL_GATED_TYPES: tuple[str, ...] = (
    "snowflake",
    "redshift",
    "databricks",
    "bigquery",
    "spanner",
    "salesforce",
    "gcs",
    "azure_blob",
)

# Object-store SOURCE connector types (list/preview/fetch/incremental engine).
OBJECT_STORE_TYPES: tuple[str, ...] = ("s3", "gcs", "azure_blob")


def wire_local_drivers(
    settings: Settings,
    probers: ProberRegistry,
    query_sources: QuerySourceRegistry,
    fetchers: FetcherRegistry,
    previewer: DispatchingSourcePreviewer | SourcePreviewer,
) -> None:
    """Register every real driver on the runtime registries.

    Local-protocol drivers (Postgres/MySQL/SQL Server/Oracle/SFTP/HTTP) and the
    credential-gated cloud SDK drivers all resolve here — no connector in the
    target matrix falls back to a fake on the runtime path.
    """
    connect_timeout = settings.connection_test_timeout_s
    query_timeout = float(settings.query_timeout_s)
    preview_timeout = settings.preview_timeout_s
    is_dispatch = isinstance(previewer, DispatchingSourcePreviewer)

    def wire(ctype: str, prober, source, prev) -> None:
        probers.set(ctype, prober)
        query_sources.set(ctype, source)
        if is_dispatch and prev is not None:
            previewer.set(ctype, prev)

    # --- local-protocol: SQL databases ---------------------------------------
    wire(
        "postgres",
        PostgresProber(connect_timeout_s=connect_timeout),
        PostgresQuerySource(connect_timeout_s=connect_timeout, query_timeout_s=query_timeout),
        PostgresPreviewer(connect_timeout_s=connect_timeout),
    )
    for mysql_type in ("mysql", "mariadb"):  # MariaDB speaks the MySQL wire protocol
        wire(
            mysql_type,
            MysqlProber(connect_timeout_s=connect_timeout),
            MysqlQuerySource(connect_timeout_s=connect_timeout, query_timeout_s=query_timeout),
            MysqlPreviewer(connect_timeout_s=connect_timeout),
        )
    for tds_type in ("sqlserver", "synapse"):  # Synapse exposes a T-SQL / TDS endpoint
        wire(
            tds_type,
            SqlServerProber(connect_timeout_s=connect_timeout),
            SqlServerQuerySource(connect_timeout_s=connect_timeout, query_timeout_s=query_timeout),
            SqlServerPreviewer(connect_timeout_s=connect_timeout),
        )
    wire(
        "oracle",
        OracleProber(connect_timeout_s=connect_timeout),
        OracleQuerySource(connect_timeout_s=connect_timeout, query_timeout_s=query_timeout),
        OraclePreviewer(connect_timeout_s=connect_timeout),
    )

    # --- credential-gated warehouses (generic DB-API harness) ----------------
    for ctype, dialect in (
        ("snowflake", snowflake_dialect()),
        ("redshift", redshift_dialect()),
        ("databricks", databricks_dialect()),
    ):
        wire(
            ctype,
            DbapiProber(dialect, connect_timeout_s=connect_timeout),
            DbapiQuerySource(
                dialect, connect_timeout_s=connect_timeout, query_timeout_s=query_timeout
            ),
            DbapiPreviewer(dialect, connect_timeout_s=connect_timeout),
        )

    # --- credential-gated cloud APIs (native typed query params) -------------
    wire(
        "bigquery",
        BigQueryProber(connect_timeout_s=connect_timeout),
        BigQueryQuerySource(connect_timeout_s=connect_timeout, query_timeout_s=query_timeout),
        BigQueryPreviewer(connect_timeout_s=connect_timeout),
    )
    wire(
        "spanner",
        SpannerProber(connect_timeout_s=connect_timeout),
        SpannerQuerySource(connect_timeout_s=connect_timeout, query_timeout_s=query_timeout),
        SpannerPreviewer(connect_timeout_s=connect_timeout),
    )
    wire(
        "salesforce",
        SalesforceProber(connect_timeout_s=connect_timeout),
        SalesforceQuerySource(connect_timeout_s=connect_timeout, query_timeout_s=query_timeout),
        SalesforcePreviewer(connect_timeout_s=preview_timeout),
    )

    # --- remote-file fetchers, streamed to the object store (ING-FR-041) -----
    fetchers.set("sftp", SftpSourceFetcher(connect_timeout_s=connect_timeout))
    fetchers.set("ftp", FtpSourceFetcher(connect_timeout_s=connect_timeout))
    fetchers.set("http_api", HttpSourceFetcher(timeout_s=preview_timeout))

    # --- object-store / data-lake SOURCE connectors (ING-FR-064) -------------
    # Each backend supplies a client factory; the shared engine does the
    # list → glob/incremental filter → stream-decode pipeline. S3 is verified
    # live against MinIO; gcs/azure_blob are credential-gated (real SDK).
    object_factories = {
        "s3": s3_client_factory,
        "gcs": gcs_client_factory,
        "azure_blob": azure_blob_client_factory,
    }
    for ctype, factory in object_factories.items():
        fetchers.set(ctype, ObjectStoreSourceFetcher(factory, connect_timeout_s=connect_timeout))
        probers.set(ctype, ObjectStoreProber(factory, connect_timeout_s=connect_timeout))
        if is_dispatch:
            previewer.set(
                ctype, ObjectStoreSourcePreviewer(factory, connect_timeout_s=preview_timeout)
            )

    # --- remaining local-protocol probers/previewers -------------------------
    probers.set("sftp", SftpProber(connect_timeout_s=connect_timeout))
    probers.set("ftp", FtpProber(connect_timeout_s=connect_timeout))
    probers.set("http_api", HttpProber(connect_timeout_s=connect_timeout))
    if is_dispatch:
        previewer.set("sftp", SftpSourcePreviewer(connect_timeout_s=connect_timeout))
        previewer.set("ftp", FtpSourcePreviewer(connect_timeout_s=connect_timeout))
        previewer.set("http_api", HttpSourcePreviewer(timeout_s=preview_timeout))
