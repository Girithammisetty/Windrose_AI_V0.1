"""ProfilerRunner implementations.

InProcessProfilerRunner is a *real* profiler (pandas engine from
app.domain.profiling) that runs in-process and reports its result through the
same signed callback contract the containerized profiler uses — so tests
exercise real profiles end to end. K8sProfilerRunner is the production stub.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from hashlib import sha256
from typing import Any

from app.domain.entities import ProfileErrorCategory
from app.domain.ports import Catalog, ObjectStore, ProfileJobSpec
from app.domain.profiling.engine import (
    ProfilerError,
    build_summary,
    profile_dataframe,
    render_html_report,
)
from app.utils import Clock

# reporter(spec, result_body) -> None. The default HTTP reporter PUTs
# /internal/v1/profiles/{id} with the HMAC signature header (DST-FR-023).
Reporter = Callable[[ProfileJobSpec, dict], Awaitable[None]]


def sign_callback(token: str, body: bytes) -> str:
    return hmac.new(token.encode(), body, sha256).hexdigest()


class InProcessProfilerRunner:
    def __init__(
        self,
        catalog: Catalog,
        object_store: ObjectStore,
        reporter: Reporter,
        *,
        profiler_version: str,
        clock: Clock | None = None,
        max_rows: int = 10_000_000,
    ):
        self.catalog = catalog
        self.object_store = object_store
        self.reporter = reporter
        self.profiler_version = profiler_version
        self.clock = clock or Clock()
        self.max_rows = max_rows
        self.launched: list[ProfileJobSpec] = []

    async def launch(self, spec: ProfileJobSpec) -> None:
        self.launched.append(spec)
        result = await self._run(spec)
        await self.reporter(spec, result)

    async def kill(self, profile_id: str) -> None:  # in-process jobs finish synchronously
        return None

    async def _run(self, spec: ProfileJobSpec) -> dict[str, Any]:
        base = {"tenant_id": spec.tenant_id, "profiler_version": self.profiler_version}
        try:
            df = await self.catalog.read_snapshot(spec.iceberg_table, spec.iceberg_snapshot_id)
            doc = profile_dataframe(
                df,
                dataset_urn=spec.dataset_urn,
                version_no=spec.version_no,
                profiler_version=self.profiler_version,
                generated_at=self.clock.now(),
                sample_strategy=spec.sample_strategy,
                max_rows=self.max_rows,
            )
        except ProfilerError as exc:
            return {**base, "status": "failed", "error_category": exc.category,
                    "error_message": exc.message}
        except MemoryError:
            return {**base, "status": "failed", "error_category": ProfileErrorCategory.OOM,
                    "error_message": "profiler out of memory"}
        except Exception as exc:  # noqa: BLE001 — profiler must always report a category
            return {**base, "status": "failed", "error_category": ProfileErrorCategory.INTERNAL,
                    "error_message": str(exc)}

        import json

        key_json = f"{spec.output_prefix}/profile.json"
        key_html = f"{spec.output_prefix}/profile.html"
        await self.object_store.put(
            key_json, json.dumps(doc, default=str).encode(), "application/json"
        )
        await self.object_store.put(
            key_html, render_html_report(doc).encode(), "text/html"
        )
        return {
            **base,
            "status": "completed",
            "object_key_json": key_json,
            "object_key_html": key_html,
            "summary": build_summary(doc),
            "sample": doc["sample"],
        }


class K8sProfilerRunner:
    """TODO(prod): launch a K8s Job on the `data` node pool with image
    `windrose/profiler`, args --tenant-id --dataset-urn --version-no
    --snapshot-id --sample-strategy --output-prefix, resource caps 4CPU/8GiB
    (retry at 16GiB on OOM), supervised via Temporal (DST-FR-020, §8)."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "TODO: K8s Job profiler runner — use InProcessProfilerRunner in dev"
        )
