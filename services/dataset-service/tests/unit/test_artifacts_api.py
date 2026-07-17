"""Unit: GET /api/v1/artifacts — the dataset "metric artifact" chart-service's
metric/parameter family fetches (CHART-FR-025). Resolves a dataset (version) URN
to its profile summary rendered as headline {kind, metrics} key/values."""

from __future__ import annotations

import pandas as pd

from tests.conftest import TENANT_A, auth, create_dataset
from tests.unit.test_profiles_api import register_version

DF = pd.DataFrame(
    {
        "order_id": range(100),
        "order_total": [10.0 + i for i in range(100)],
        "discount_code": [None] * 45 + ["SAVE10"] * 55,
    }
)


def _metrics_by_label(payload: dict) -> dict:
    data = payload["data"]
    assert data["kind"] == "dataset_summary"
    return {m["label"]: m["value"] for m in data["metrics"]}


class TestArtifactsApi:
    async def test_version_urn_returns_profile_metrics(self, client, container):
        ds = await create_dataset(client, name="Orders")
        resp = await register_version(client, container, ds, df=DF)
        assert resp.status_code == 201, resp.text

        urn = f"wr:{TENANT_A}:dataset:version/{ds['id']}@v1"
        resp = await client.get(f"/api/v1/artifacts?urn={urn}", headers=auth())
        assert resp.status_code == 200, resp.text
        by_label = _metrics_by_label(resp.json())
        assert by_label["Rows"] == 100
        assert by_label["Columns"] == 3
        # discount_code is 45% null; completeness/null-rate are derived headline stats.
        assert "Avg Null %" in by_label
        assert "Completeness %" in by_label
        assert "Alerts" in by_label

    async def test_plain_dataset_urn_uses_current_version(self, client, container):
        ds = await create_dataset(client, name="Orders")
        resp = await register_version(client, container, ds, df=DF)
        assert resp.status_code == 201, resp.text

        urn = f"wr:{TENANT_A}:dataset:dataset/{ds['id']}"
        resp = await client.get(f"/api/v1/artifacts?urn={urn}", headers=auth())
        assert resp.status_code == 200, resp.text
        by_label = _metrics_by_label(resp.json())
        assert by_label["Rows"] == 100
        assert by_label["Columns"] == 3

    async def test_no_profile_falls_back_to_version_row_count_schema(
        self, client, container
    ):
        """Defensive: an unprofiled version still yields real Rows/Columns from the
        version's row_count/schema instead of erroring."""
        ds = await create_dataset(client, name="Bulk")
        resp = await register_version(
            client, container, ds, df=DF, skip_profiling=True
        )
        assert resp.status_code == 201, resp.text

        urn = f"wr:{TENANT_A}:dataset:version/{ds['id']}@v1"
        resp = await client.get(f"/api/v1/artifacts?urn={urn}", headers=auth())
        assert resp.status_code == 200, resp.text
        by_label = _metrics_by_label(resp.json())
        assert by_label["Rows"] == 100
        assert by_label["Columns"] == 3
        # No profile => no derived null-rate metric.
        assert "Avg Null %" not in by_label

    async def test_non_dataset_urn_rejected(self, client):
        urn = f"wr:{TENANT_A}:experiment:run/r-1"
        resp = await client.get(f"/api/v1/artifacts?urn={urn}", headers=auth())
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_FAILED"

    async def test_requires_profile_read_permission(self, client, container):
        ds = await create_dataset(client, name="Orders")
        await register_version(client, container, ds, df=DF)
        urn = f"wr:{TENANT_A}:dataset:version/{ds['id']}@v1"
        resp = await client.get(
            f"/api/v1/artifacts?urn={urn}",
            headers=auth(scopes=["dataset.dataset.read"]),
        )
        assert resp.status_code == 403
