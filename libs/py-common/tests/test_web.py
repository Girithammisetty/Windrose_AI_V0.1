"""Unit coverage for the framework-agnostic HTTP helpers (no infra)."""

from __future__ import annotations

import uuid

import pytest

from windrose_common.web import (
    CursorError,
    clamp_limit,
    decode_cursor,
    encode_cursor,
    error_body,
    page_envelope,
)


def test_error_envelope_shape():
    body = error_body("VALIDATION_FAILED", "bad", "trace-abc", [{"field": "x"}])
    assert body == {
        "error": {
            "code": "VALIDATION_FAILED",
            "message": "bad",
            "trace_id": "trace-abc",
            "details": [{"field": "x"}],
        }
    }
    # details omitted when None
    assert "details" not in error_body("INTERNAL", "e", "t")["error"]


def test_cursor_roundtrip_and_validation():
    ident = str(uuid.uuid4())
    assert decode_cursor(encode_cursor(ident)) == ident
    with pytest.raises(CursorError):
        decode_cursor("not-base64-uuid!!")


def test_clamp_limit_bounds():
    assert clamp_limit(None) == 50
    assert clamp_limit(10) == 10
    with pytest.raises(CursorError):
        clamp_limit(0)
    with pytest.raises(CursorError):
        clamp_limit(999)


def test_page_envelope():
    assert page_envelope("cur", True) == {"next_cursor": "cur", "has_more": True}
