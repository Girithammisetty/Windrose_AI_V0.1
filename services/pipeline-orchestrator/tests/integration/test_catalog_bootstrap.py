"""BUG-2: bootstrap_catalog must actually populate the global Postgres catalog tables
(the pg_insert previously crashed on the shadowed `metadata`/`order_no` columns)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.domain.catalog import seed_algorithm_templates, seed_components
from app.store.sql import bootstrap_catalog

pytestmark = pytest.mark.integration


async def test_bootstrap_populates_catalog_tables(app_sf):
    components = seed_components()
    algorithms = seed_algorithm_templates()

    # Must not raise (previously: AttributeError _bulk_update_tuples).
    await bootstrap_catalog(app_sf, components, algorithms)

    async with app_sf() as s:
        n_algos = (await s.execute(
            text("SELECT count(*) FROM algorithm_templates"))).scalar_one()
        n_comps = (await s.execute(
            text("SELECT count(*) FROM components"))).scalar_one()
        xgb_runnable = (await s.execute(text(
            "SELECT runnable FROM algorithm_templates WHERE name='xgboost'"))).scalar_one()
        z_runnable = (await s.execute(text(
            "SELECT runnable FROM algorithm_templates "
            "WHERE name='z_score_based_anomaly_detection'"))).scalar_one()
        xgb_meta = (await s.execute(text(
            "SELECT metadata FROM algorithm_templates WHERE name='xgboost'"))).scalar_one()

    assert n_algos == 21
    assert n_comps == len(components)
    assert xgb_runnable is True
    assert z_runnable is False  # BR-14 preserved in the DB row
    assert xgb_meta.get("supervised") is True  # metadata column round-tripped

    # Idempotent re-bootstrap (on_conflict_do_update) still works.
    await bootstrap_catalog(app_sf, components, algorithms)
    async with app_sf() as s:
        again = (await s.execute(
            text("SELECT count(*) FROM algorithm_templates"))).scalar_one()
    assert again == 21
