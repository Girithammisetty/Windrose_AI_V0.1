"""Unit: URNs, state machines (DST-FR-002/§4.3), schema diff (DST-FR-005)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domain.entities import Dataset, DatasetStatus, Profile, ProfileStatus
from app.domain.errors import Conflict, ValidationFailed
from app.domain.schema_diff import compute_schema_diff
from app.domain.state import transition_dataset, transition_profile
from app.domain.urn import (
    dataset_urn,
    is_valid_urn,
    parse_urn,
    parse_version_urn,
    version_urn,
)

NOW = datetime(2026, 7, 1, tzinfo=UTC)


def _dataset(status=DatasetStatus.DRAFT) -> Dataset:
    return Dataset(
        id="d1", tenant_id="t1", workspace_id="w1", name="ds", iceberg_table="b.t.x",
        created_by="u1", created_at=NOW, updated_at=NOW, status=status,
    )


def _profile(status=ProfileStatus.PENDING) -> Profile:
    return Profile(id="p1", tenant_id="t1", dataset_id="d1", version_id="v1",
                   created_at=NOW, status=status)


class TestUrn:
    def test_parse_roundtrip(self):
        urn = parse_urn("wr:t-42:dataset:dataset/ds-9f2")
        assert (urn.tenant, urn.service, urn.rtype, urn.rid) == (
            "t-42", "dataset", "dataset", "ds-9f2"
        )
        assert str(urn) == "wr:t-42:dataset:dataset/ds-9f2"

    def test_version_urn_parse(self):
        urn = parse_urn(version_urn("t1", "abc", 7))
        assert parse_version_urn(urn) == ("abc", 7)

    def test_invalid_urns_rejected(self):
        for bad in ["", "wr:t1:dataset", "foo:t1:dataset:dataset/x", "wr::dataset:dataset/x"]:
            assert not is_valid_urn(bad)
            with pytest.raises(ValidationFailed):
                parse_urn(bad)

    def test_builders(self):
        assert dataset_urn("t1", "d1") == "wr:t1:dataset:dataset/d1"
        assert version_urn("t1", "d1", 3) == "wr:t1:dataset:version/d1@v3"


class TestDatasetStateMachine:
    def test_happy_path(self):
        ds = _dataset()
        transition_dataset(ds, DatasetStatus.PROCESSING)
        transition_dataset(ds, DatasetStatus.READY, has_version=True)
        transition_dataset(ds, DatasetStatus.PROCESSING)  # new version being produced
        transition_dataset(ds, DatasetStatus.FAILED, error_log={"err": "x"})
        transition_dataset(ds, DatasetStatus.PROCESSING)  # retry clears error_log
        assert ds.error_log is None

    def test_failed_requires_error_log(self):
        ds = _dataset(DatasetStatus.PROCESSING)
        with pytest.raises(Conflict):
            transition_dataset(ds, DatasetStatus.FAILED)

    def test_ready_requires_version(self):
        ds = _dataset(DatasetStatus.PROCESSING)
        with pytest.raises(Conflict):
            transition_dataset(ds, DatasetStatus.READY, has_version=False)

    def test_illegal_transitions_conflict(self):
        for frm, to in [
            (DatasetStatus.DRAFT, DatasetStatus.READY),
            (DatasetStatus.DRAFT, DatasetStatus.FAILED),
            (DatasetStatus.READY, DatasetStatus.FAILED),
            (DatasetStatus.FAILED, DatasetStatus.READY),
        ]:
            with pytest.raises(Conflict):
                transition_dataset(_dataset(frm), to, error_log={"e": 1}, has_version=True)


class TestProfileStateMachine:
    def test_lifecycle(self):
        p = _profile()
        transition_profile(p, ProfileStatus.RUNNING)
        transition_profile(p, ProfileStatus.FAILED)
        transition_profile(p, ProfileStatus.PENDING)  # manual re-trigger
        transition_profile(p, ProfileStatus.RUNNING)
        transition_profile(p, ProfileStatus.COMPLETED)

    def test_terminal_is_terminal(self):
        for terminal in (ProfileStatus.COMPLETED,):
            for to in (ProfileStatus.RUNNING, ProfileStatus.PENDING, ProfileStatus.FAILED):
                with pytest.raises(Conflict):
                    transition_profile(_profile(terminal), to)

    def test_illegal(self):
        with pytest.raises(Conflict):
            transition_profile(_profile(ProfileStatus.PENDING), ProfileStatus.COMPLETED)


class TestSchemaDiff:
    def test_ac5_added_and_removed_is_breaking(self):
        """AC-5: adding `discount` and dropping `legacy_code` -> both listed, breaking."""
        old = {"order_id": {"type": "long"}, "legacy_code": {"type": "string"}}
        new = {"order_id": {"type": "long"}, "discount": {"type": "double"}}
        diff, breaking = compute_schema_diff(old, new)
        assert diff["added"] == ["discount"]
        assert diff["removed"] == ["legacy_code"]
        assert diff["type_changed"] == []
        assert breaking is True

    def test_type_change_is_breaking(self):
        diff, breaking = compute_schema_diff(
            {"a": {"type": "int"}}, {"a": {"type": "string"}}
        )
        assert diff["type_changed"] == [{"column": "a", "from": "int", "to": "string"}]
        assert breaking is True

    def test_pure_addition_not_breaking(self):
        diff, breaking = compute_schema_diff(
            {"a": {"type": "int"}}, {"a": {"type": "int"}, "b": {"type": "string"}}
        )
        assert breaking is False
        assert diff["added"] == ["b"]

    def test_case_insensitive_match(self):
        diff, breaking = compute_schema_diff(
            {"Amount": {"type": "double"}}, {"amount": {"type": "double"}}
        )
        assert breaking is False
        assert diff["added"] == [] and diff["removed"] == []
