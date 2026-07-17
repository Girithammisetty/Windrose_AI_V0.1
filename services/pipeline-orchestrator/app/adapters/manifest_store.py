"""Compiled-manifest object storage (PIPE-FR-020/062). Manifests are written to
object storage with a pointer (``compiled_manifest_ref``) + SHA-256 digest on the
version row. Local FS for the unit tier; real MinIO (S3) for runtime."""

from __future__ import annotations

import json
import os


class LocalFSManifestStore:
    def __init__(self, base_dir: str):
        self.base = base_dir
        os.makedirs(base_dir, exist_ok=True)

    async def put(self, key: str, manifest: dict) -> str:
        path = os.path.join(self.base, key.replace("/", "__"))
        with open(path, "w") as fh:
            json.dump(manifest, fh, sort_keys=True)
        return key

    async def get(self, ref: str) -> dict | None:
        path = os.path.join(self.base, ref.replace("/", "__"))
        if not os.path.exists(path):
            return None
        with open(path) as fh:
            return json.load(fh)


class S3ManifestStore:
    def __init__(self, object_store):
        self._store = object_store

    async def put(self, key: str, manifest: dict) -> str:
        await self._store.put(key, json.dumps(manifest, sort_keys=True).encode(),
                              "application/json")
        return key

    async def get(self, ref: str) -> dict | None:
        try:
            raw = await self._store.get(ref)
        except Exception:  # noqa: BLE001
            return None
        return json.loads(raw)
