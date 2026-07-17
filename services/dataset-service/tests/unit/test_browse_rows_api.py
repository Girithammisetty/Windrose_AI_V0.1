"""Unit: user-facing paginated row browse (DST-FR-050).

GET /api/v1/datasets/{id}/rows — offset/limit paging, single-column sort
(asc/desc), per-column filters (eq|neq|contains|gt|gte|lt|lte), and the
total (unfiltered) vs filtered counts the grid header shows.
"""

from __future__ import annotations

import pandas as pd

from tests.conftest import SPIFFE_INGESTION, TENANT_A, auth, create_dataset

# 12 rows, mixed types: numeric `amount` (for numeric sort/compare), string
# `status`/`vendor` (for contains/eq filters).
DF = pd.DataFrame(
    {
        "claim_id": [f"C{i:02d}" for i in range(12)],
        "status": (["open"] * 5) + (["denied"] * 4) + (["paid"] * 3),
        "vendor": ["acme", "globex"] * 6,
        "amount": [100, 250, 90, 400, 175, 60, 320, 210, 500, 45, 280, 130],
    }
)


async def _seed(client, container, snapshot_id=7001, df=DF):
    ds = await create_dataset(client, name="claims-browse")
    await container.catalog.commit_snapshot(ds["iceberg_table"], snapshot_id, df)
    resp = await client.post(
        f"/internal/v1/datasets/{ds['id']}/versions",
        json={
            "tenant_id": TENANT_A, "iceberg_snapshot_id": snapshot_id,
            "schema": {}, "row_count": len(df), "bytes": 2048, "skip_profiling": True,
        },
        headers={"x-client-spiffe-id": SPIFFE_INGESTION},
    )
    assert resp.status_code == 201, resp.text
    return ds


async def _rows(client, ds_id, **params):
    resp = await client.get(
        f"/api/v1/datasets/{ds_id}/rows", params=params, headers=auth()
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


class TestBrowseRows:
    async def test_columns_total_and_default_page(self, client, container):
        ds = await _seed(client, container)
        data = await _rows(client, ds["id"], limit=5)
        assert data["columns"] == ["claim_id", "status", "vendor", "amount"]
        assert data["total"] == 12
        assert data["filtered"] == 12
        assert data["limit"] == 5
        assert len(data["rows"]) == 5
        # cells are stringified for display
        assert data["rows"][0][0] == "C00"

    async def test_offset_paging(self, client, container):
        ds = await _seed(client, container)
        page2 = await _rows(client, ds["id"], offset=10, limit=5)
        assert page2["offset"] == 10
        assert len(page2["rows"]) == 2  # only 2 rows left after offset 10

    async def test_numeric_sort_desc(self, client, container):
        ds = await _seed(client, container)
        data = await _rows(client, ds["id"], sort="amount", dir="desc", limit=3)
        amounts = [int(r[3]) for r in data["rows"]]
        assert amounts == [500, 400, 320]  # numeric, not lexicographic

    async def test_numeric_sort_asc(self, client, container):
        ds = await _seed(client, container)
        data = await _rows(client, ds["id"], sort="amount", dir="asc", limit=3)
        assert [int(r[3]) for r in data["rows"]] == [45, 60, 90]

    async def test_string_contains_filter(self, client, container):
        ds = await _seed(client, container)
        data = await _rows(client, ds["id"], filter="status:contains:den")
        assert data["total"] == 12
        assert data["filtered"] == 4  # 4 'denied'
        assert {r[1] for r in data["rows"]} == {"denied"}

    async def test_string_eq_filter(self, client, container):
        ds = await _seed(client, container)
        data = await _rows(client, ds["id"], filter="vendor:eq:acme")
        assert data["filtered"] == 6

    async def test_numeric_gte_filter(self, client, container):
        ds = await _seed(client, container)
        data = await _rows(client, ds["id"], filter="amount:gte:300")
        # 400, 320, 500 -> 3
        assert data["filtered"] == 3

    async def test_combined_filters_are_anded(self, client, container):
        ds = await _seed(client, container)
        data = await _rows(
            client, ds["id"],
            filter=["status:eq:open", "amount:gte:150"],
        )
        # open rows: amounts 100,250,90,400,175 -> >=150: 250,400,175 -> 3
        assert data["filtered"] == 3

    async def test_filter_then_sort_then_page(self, client, container):
        ds = await _seed(client, container)
        data = await _rows(
            client, ds["id"], filter="vendor:eq:globex",
            sort="amount", dir="asc", limit=2, offset=0,
        )
        assert data["filtered"] == 6
        # globex rows (odd indices) amounts = 250,400,60,210,45,130 → asc first 2
        assert [int(r[3]) for r in data["rows"]] == [45, 60]

    async def test_limit_capped(self, client, container):
        ds = await _seed(client, container)
        resp = await client.get(
            f"/api/v1/datasets/{ds['id']}/rows", params={"limit": 9999},
            headers=auth(),
        )
        # FastAPI Query le=500 rejects an over-cap limit with 422.
        assert resp.status_code == 422

    async def test_pushdown_counts_and_sort_are_global_and_exact(self, client, container):
        # The browse is pushed into DuckDB, so filter/sort/counts are GLOBAL and
        # exact over the whole snapshot (never a truncated working set) — proven
        # on a dataset far larger than any single page.
        import pandas as pd

        n = 5000
        df = pd.DataFrame({
            "id": [f"R{i:05d}" for i in range(n)],
            "grp": ["a" if i % 2 == 0 else "b" for i in range(n)],
            "val": [i for i in range(n)],  # max is the LAST row, id R04999
        })
        ds = await _seed(client, container, snapshot_id=8202, df=df)

        base = await _rows(client, ds["id"], limit=10)
        assert base["truncated"] is False
        assert base["total"] == n  # exact, not a lower bound

        # global filter count is exact across all 5000 rows
        filt = await _rows(client, ds["id"], filter="grp:eq:a")
        assert filt["filtered"] == n // 2

        # global sort surfaces the true max (row far beyond the first page)
        top = await _rows(client, ds["id"], sort="val", dir="desc", limit=1)
        assert int(top["rows"][0][2]) == n - 1
        assert top["rows"][0][0] == "R04999"

    async def test_small_dataset_not_truncated(self, client, container):
        ds = await _seed(client, container)  # 12 rows
        data = await _rows(client, ds["id"], limit=50)
        assert data["truncated"] is False
        assert data["total"] == 12
