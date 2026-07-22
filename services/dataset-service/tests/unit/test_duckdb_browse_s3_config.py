"""_configure_s3 must quote every tenant-configured value it interpolates into
a SET '...' statement -- an unescaped single quote in a stored s3 config field
(region/endpoint/access_key/secret_key) otherwise breaks out of the string
literal into raw SQL (BRD 58 SEC-5)."""

from __future__ import annotations

import duckdb

from app.adapters.duckdb_browse import _configure_s3


def test_quote_in_region_does_not_execute_injected_sql():
    con = duckdb.connect()
    # A region value containing a single quote followed by a second statement
    # that would create a marker table if the interpolation were unescaped.
    payload = "us-east-1'; CREATE TABLE injected(x INTEGER); --"
    _configure_s3(con, {"region": payload, "access_key": "ak", "secret_key": "sk"})

    tables = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_name = 'injected'"
    ).fetchall()
    assert tables == [], "injected SQL must not have executed"

    # The value was still applied, quote and all -- proving this is a real
    # escape, not a silent no-op/dropped setting.
    got = con.execute("SELECT current_setting('s3_region')").fetchone()[0]
    assert got == payload


def test_quote_in_credentials_round_trips_safely():
    con = duckdb.connect()
    _configure_s3(con, {
        "region": "us-east-1",
        "access_key": "ak'; DROP TABLE foo; --",
        "secret_key": "sk' OR '1'='1",
    })
    assert con.execute("SELECT current_setting('s3_access_key_id')").fetchone()[0] == "ak'; DROP TABLE foo; --"
    assert con.execute("SELECT current_setting('s3_secret_access_key')").fetchone()[0] == "sk' OR '1'='1"


def test_endpoint_with_quote_is_escaped():
    con = duckdb.connect()
    _configure_s3(con, {"endpoint": "https://minio.example.com'; SELECT 1; --"})
    got = con.execute("SELECT current_setting('s3_endpoint')").fetchone()[0]
    assert got == "minio.example.com'; SELECT 1; --"
