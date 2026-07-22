"""Unit: driver placeholder translation keeps the watermark a BOUND parameter.

These prove structurally that no value is ever spliced into SQL text — the value
rides out-of-band in the args/params returned alongside the translated SQL
(ING-FR-061, BR-5). No database needed.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.drivers.sql import (
    quote_bracket_identifier,
    quote_identifier,
    to_at_named,
    to_format,
    to_positional,
    to_pyformat,
    wrap_limit_zero,
    wrap_top_zero,
    wrap_where_false,
)
from app.domain.watermark import WatermarkSpec, build_incremental_query


def test_to_positional_binds_value_out_of_band() -> None:
    sql = "SELECT * FROM (SELECT * FROM orders) src WHERE updated_at > :watermark"
    wm = datetime(2026, 7, 1, tzinfo=UTC)
    translated, args = to_positional(sql, {"watermark": wm})
    assert translated.endswith("WHERE updated_at > $1")
    assert ":watermark" not in translated
    assert "2026" not in translated  # value never enters the text
    assert args == [wm]


def test_to_positional_leaves_pg_casts_alone() -> None:
    sql = "SELECT id::text FROM t WHERE id > :watermark"
    translated, args = to_positional(sql, {"watermark": 5})
    assert "id::text" in translated  # `::cast` untouched
    assert translated.endswith("id > $1")
    assert args == [5]


def test_to_pyformat_binds_value_and_escapes_percent() -> None:
    sql = "SELECT * FROM t WHERE name LIKE '%x%' AND ts > :watermark"
    translated, params = to_pyformat(sql, {"watermark": 42})
    assert "%(watermark)s" in translated
    assert "'%%x%%'" in translated  # literal % doubled for pyformat safety
    assert ":watermark" not in translated
    assert params == {"watermark": 42}


def test_build_incremental_query_then_translate_never_splices() -> None:
    spec = WatermarkSpec(
        column="updated_at", operator=">", value_type="timestamp", value="2026-07-01T00:00:00Z"
    )
    sql, params = build_incremental_query("SELECT * FROM orders", spec)
    assert sql.endswith("WHERE updated_at > :watermark")
    # both driver styles carry the typed value out-of-band
    pg_sql, pg_args = to_positional(sql, params)
    my_sql, my_params = to_pyformat(sql, params)
    assert "2026" not in pg_sql and "2026" not in my_sql
    assert pg_args == [datetime(2026, 7, 1, tzinfo=UTC)]
    assert my_params == {"watermark": datetime(2026, 7, 1, tzinfo=UTC)}


def test_wrap_limit_zero_strips_trailing_semicolon() -> None:
    assert (
        wrap_limit_zero("SELECT * FROM orders;")
        == "SELECT * FROM (SELECT * FROM orders) _wr_cols LIMIT 0"
    )


def test_to_format_binds_positional_and_escapes_percent() -> None:
    """redshift-connector `format` paramstyle: `%s` + a positional args list."""
    sql = "SELECT * FROM t WHERE name LIKE '%x%' AND ts > :watermark"
    translated, args = to_format(sql, {"watermark": 42})
    assert translated.endswith("ts > %s")
    assert "'%%x%%'" in translated  # literal % doubled
    assert ":watermark" not in translated and "42" not in translated
    assert args == [42]


def test_to_at_named_binds_named_out_of_band() -> None:
    """BigQuery / Spanner `@name` params: value never enters the text."""
    wm = datetime(2026, 7, 1, tzinfo=UTC)
    sql = "SELECT * FROM (SELECT * FROM orders) src WHERE updated_at > :watermark"
    translated, params = to_at_named(sql, {"watermark": wm})
    assert translated.endswith("WHERE updated_at > @watermark")
    assert ":watermark" not in translated and "2026" not in translated
    assert params == {"watermark": wm}


def test_wrap_top_zero_and_where_false_shape_columns_only() -> None:
    assert wrap_top_zero("SELECT * FROM orders;") == (
        "SELECT TOP 0 * FROM (SELECT * FROM orders) _wr_cols"
    )
    assert wrap_where_false("SELECT * FROM orders;") == (
        "SELECT * FROM (SELECT * FROM orders) wr_cols WHERE 1=0"
    )


def test_quote_identifier_quotes_each_dotted_part() -> None:
    # A caller-supplied table name is never a single opaque token to trust --
    # each dot-separated part is quoted on its own (BRD 58 SEC-5).
    assert quote_identifier("orders", quote='"') == '"orders"'
    assert quote_identifier("myproject.mydataset.orders", quote="`") == (
        "`myproject`.`mydataset`.`orders`"
    )


def test_quote_identifier_escapes_embedded_quote_by_doubling() -> None:
    # A malicious table name containing the quote char must not break out of
    # the quoted identifier into raw SQL -- this is the actual vulnerability
    # being closed: `SELECT * FROM {table}` spliced this value unescaped.
    injected = 'orders); DROP TABLE secrets; --'
    assert quote_identifier(injected, quote='"') == '"orders); DROP TABLE secrets; --"'
    assert quote_identifier('a"b', quote='"') == '"a""b"'
    assert quote_identifier("a`b", quote="`") == "`a``b`"


def test_quote_bracket_identifier_escapes_close_bracket() -> None:
    assert quote_bracket_identifier("orders") == "[orders]"
    assert quote_bracket_identifier("dbo.orders") == "[dbo].[orders]"
    assert quote_bracket_identifier("a]b") == "[a]]b]"
