"""Read-only classification of verified-query SQL (BR-11, AC-11).

Mirrors query-service's AST read-only gate (BRD 05 QRY-FR): a verified query
must be a single SELECT (optionally WITH-prefixed); DDL/DML anywhere -> 422.
`{{dataset('Name')}}` refs and `:variable` binds are placeholders, not SQL.
"""

from __future__ import annotations

import re

from app.domain.errors import ValidationFailed

_FORBIDDEN = frozenset(
    ["insert", "update", "delete", "drop", "alter", "create", "truncate", "merge",
     "grant", "revoke", "call", "exec", "execute", "copy", "vacuum", "attach"]
)

_STRING_RE = re.compile(r"'(?:[^']|'')*'")
_DATASET_REF_RE = re.compile(r"\{\{\s*dataset\(\s*'[^']*'\s*\)\s*\}\}")
_VARIABLE_RE = re.compile(r":[a-z][a-z0-9_]*")
_WORD_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")


def validate_read_only_sql(sql_text: str) -> None:
    if not isinstance(sql_text, str) or not sql_text.strip():
        raise ValidationFailed("sql_text must be a non-empty string")
    stripped = _DATASET_REF_RE.sub(" placeholder_table ", sql_text)
    stripped = _VARIABLE_RE.sub(" ? ", stripped)
    if "--" in stripped or "/*" in stripped:
        raise ValidationFailed("comments are not allowed in verified query SQL")
    no_strings = _STRING_RE.sub("''", stripped)
    statements = [s for s in no_strings.split(";") if s.strip()]
    if len(statements) != 1 or ";" in no_strings.rstrip().rstrip(";"):
        if len(statements) != 1:
            raise ValidationFailed("verified query SQL must be a single statement")
    body = statements[0].strip()
    first_word = (_WORD_RE.match(body) or _WORD_RE.search(body))
    if first_word is None or first_word.group().lower() not in ("select", "with"):
        raise ValidationFailed("verified query SQL must be a single SELECT")
    words = {w.lower() for w in _WORD_RE.findall(no_strings)}
    hit = sorted(words & _FORBIDDEN)
    if hit:
        raise ValidationFailed(
            f"read-only violation: {', '.join(hit)} not allowed in verified query SQL")


def referenced_words(sql_text: str) -> set[str]:
    """Lowercased identifiers in the SQL (for schema-break re-validation)."""
    no_strings = _STRING_RE.sub("''", sql_text)
    return {w.lower() for w in _WORD_RE.findall(no_strings)}
