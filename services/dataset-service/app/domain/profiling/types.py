"""Type & semantic inference (DST-FR-025) — V1 DataProfiler parity, Iceberg-normalized.

Logical types: boolean, int, long, float, double, decimal(p,s), date, timestamp,
string, categorical. Semantics: id, email, phone, country, currency, url, free_text, null.
Boolean-like string columns (Y/N, T/F, true/false, case-insensitive) report as
boolean with a coercion_hint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import pandas as pd

_BOOL_SETS: list[tuple[frozenset[str], str]] = [
    (frozenset({"y", "n"}), "Y/N"),
    (frozenset({"t", "f"}), "T/F"),
    (frozenset({"true", "false"}), "true/false"),
]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)
_PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()./-]{6,19}$")

_ISO_COUNTRIES = {
    "us", "gb", "de", "fr", "in", "cn", "jp", "br", "ca", "au", "es", "it", "nl", "mx",
    "usa", "gbr", "deu", "fra", "ind", "chn", "jpn", "bra", "can", "aus", "esp", "ita",
    "united states", "united kingdom", "germany", "france", "india", "china", "japan",
    "brazil", "canada", "australia", "spain", "italy", "netherlands", "mexico",
}

_CURRENCY_NAME_HINTS = (
    "amount", "price", "total", "cost", "revenue", "fee", "balance", "salary", "spend",
)

# Threshold to accept a coerced type; MIXED_TYPES flags failure rates above 1%
# (quality-flag table, BRD §4.4) while still below this acceptance bar.
_ACCEPT_PARSE_RATE = 0.95


@dataclass(slots=True)
class InferredType:
    logical_type: str
    coercion_hint: str | None = None
    parse_fail_pct: float = 0.0  # percent (0..100) of sampled values failing the type parse
    # Parsed view of an object column (numeric/datetime) for downstream stats.
    coerced: pd.Series | None = None


def _int_type(min_v: float, max_v: float) -> str:
    if min_v >= -(2**31) and max_v < 2**31:
        return "int"
    return "long"


def _decimal_type(values: pd.Series) -> str:
    precision, scale = 1, 0
    for v in values:
        sign, digits, exponent = Decimal(v).as_tuple()
        s = max(0, -int(exponent))
        p = max(len(digits), s)
        precision, scale = max(precision, p), max(scale, s)
    return f"decimal({precision},{scale})"


def infer_logical_type(series: pd.Series) -> InferredType:
    non_null = series.dropna()

    if pd.api.types.is_bool_dtype(series):
        return InferredType("boolean")
    if pd.api.types.is_datetime64_any_dtype(series):
        if len(non_null) and (non_null.dt.normalize() == non_null).all():
            return InferredType("date")
        return InferredType("timestamp")
    if pd.api.types.is_integer_dtype(series):
        if len(non_null) == 0:
            return InferredType("long")
        return InferredType(_int_type(non_null.min(), non_null.max()))
    if pd.api.types.is_float_dtype(series):
        return InferredType("float" if series.dtype == np.float32 else "double")

    if len(non_null) == 0:
        return InferredType("string")

    if all(isinstance(v, Decimal) for v in non_null):
        return InferredType(_decimal_type(non_null))
    if all(isinstance(v, bool) for v in non_null):
        return InferredType("boolean")

    text = non_null.astype(str).str.strip()
    lowered = text.str.lower()

    uniq = frozenset(lowered.unique())
    for bool_set, hint in _BOOL_SETS:
        if uniq and uniq <= bool_set:
            return InferredType("boolean", coercion_hint=hint)

    numeric = pd.to_numeric(text, errors="coerce")
    numeric_ok = float(numeric.notna().mean())
    if numeric_ok >= _ACCEPT_PARSE_RATE:
        parsed = numeric.dropna()
        fail_pct = round((1 - numeric_ok) * 100, 4)
        if (parsed % 1 == 0).all() and not text.str.contains(r"\.", regex=True).any():
            return InferredType(
                _int_type(parsed.min(), parsed.max()), parse_fail_pct=fail_pct, coerced=numeric
            )
        return InferredType("double", parse_fail_pct=fail_pct, coerced=numeric)

    with pd.option_context("mode.chained_assignment", None):
        try:
            dt = pd.to_datetime(text, errors="coerce", format="mixed", utc=True)
        except (ValueError, TypeError):
            dt = pd.Series(pd.NaT, index=text.index)
    dt_ok = float(dt.notna().mean())
    if dt_ok >= _ACCEPT_PARSE_RATE:
        fail_pct = round((1 - dt_ok) * 100, 4)
        parsed = dt.dropna()
        logical = "date" if len(parsed) and (parsed.dt.normalize() == parsed).all() else "timestamp"
        return InferredType(logical, parse_fail_pct=fail_pct, coerced=dt)

    distinct = lowered.nunique()
    distinct_pct = distinct / len(lowered) * 100
    if distinct <= 100 and distinct_pct <= 5.0:
        return InferredType("categorical")
    return InferredType("string")


def _match_rate(text: pd.Series, pattern: re.Pattern) -> float:
    if len(text) == 0:
        return 0.0
    return float(text.str.match(pattern).mean())


def infer_semantic(
    name: str,
    series: pd.Series,
    logical_type: str,
    *,
    is_unique: bool,
    avg_length: float | None,
    distinct_pct: float,
) -> str | None:
    lname = name.lower()
    non_null = series.dropna()

    if lname == "id" or lname.endswith("_id") or (is_unique and "id" in lname.split("_")):
        return "id"

    numeric_like = logical_type in ("int", "long", "float", "double") or logical_type.startswith(
        "decimal"
    )
    if numeric_like and any(h in lname for h in _CURRENCY_NAME_HINTS):
        return "currency"

    if logical_type in ("string", "categorical") and len(non_null):
        text = non_null.astype(str).str.strip()
        if _match_rate(text, _EMAIL_RE) >= 0.9:
            return "email"
        if _match_rate(text, _URL_RE) >= 0.9:
            return "url"
        if _match_rate(text, _PHONE_RE) >= 0.9 and ("phone" in lname or "tel" in lname):
            return "phone"
        lowered = text.str.lower()
        if float(lowered.isin(_ISO_COUNTRIES).mean()) >= 0.9:
            return "country"
        if avg_length is not None and avg_length > 40 and distinct_pct > 80:
            return "free_text"
    return None
