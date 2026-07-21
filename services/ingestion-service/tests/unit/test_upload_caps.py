"""B2 (BRD 58): total upload size / part-count caps reject before the
memory-bound Iceberg commit."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.domain.errors import ValidationFailedError
from app.domain.services.uploads import enforce_upload_caps


@dataclass
class _S:
    max_upload_bytes: int
    max_upload_parts: int


def test_within_caps_ok():
    enforce_upload_caps(1_000, 3, _S(max_upload_bytes=5_000, max_upload_parts=10))


def test_over_bytes_rejected():
    with pytest.raises(ValidationFailedError) as ei:
        enforce_upload_caps(6_000, 3, _S(max_upload_bytes=5_000, max_upload_parts=10))
    assert "exceeds" in str(ei.value)


def test_over_parts_rejected():
    with pytest.raises(ValidationFailedError) as ei:
        enforce_upload_caps(100, 11, _S(max_upload_bytes=5_000, max_upload_parts=10))
    assert "parts" in str(ei.value)


def test_zero_cap_means_unlimited():
    # 0 disables the check — no raise even for a huge upload.
    enforce_upload_caps(10**12, 10**6, _S(max_upload_bytes=0, max_upload_parts=0))


def test_boundary_equal_is_allowed():
    enforce_upload_caps(5_000, 10, _S(max_upload_bytes=5_000, max_upload_parts=10))
