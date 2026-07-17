"""Real Google Cloud Storage object-store source client (google-cloud-storage).

Credential-gated: the adapter drives the real GCS SDK, but a live pull needs a
GCP service-account (``credentials_json`` secret). The SDK is imported lazily and
only lives in the optional ``cloud`` extra, so the base install stays light and
``make test-unit`` runs without it. A contract test injects a fake underlying
client (``_client=``) to exercise list/read shaping offline — no SDK, no network.

Same list/preview/fetch/incremental shape as the S3 client: listing is paginated
by the SDK's iterator, bodies stream via ``blob.open("rb")`` (``.read(n)``) so
the engine never buffers a whole object (ING-FR-041).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from app.domain.drivers.objectsource import ObjectRef, ObjectStoreClient


class GcsObjectStoreClient:
    def __init__(
        self,
        config: BaseModel,
        secrets: dict[str, str],
        timeout: float,
        *,
        _client: Any | None = None,
    ) -> None:
        self.bucket_name = config.bucket
        self.project_id = getattr(config, "project_id", None)
        self._timeout = timeout
        if _client is not None:
            self._client = _client
        else:
            from google.cloud import storage
            from google.oauth2 import service_account

            credentials = None
            if secrets.get("credentials_json"):
                info = json.loads(secrets["credentials_json"])
                credentials = service_account.Credentials.from_service_account_info(info)
            self._client = storage.Client(project=self.project_id, credentials=credentials)
        self._bucket = self._client.bucket(self.bucket_name)

    def probe(self) -> None:
        # List at most one blob — trivial round-trip proving auth + bucket access.
        next(iter(self._client.list_blobs(self.bucket_name, max_results=1)), None)

    def list_objects(self, prefix: str) -> list[ObjectRef]:
        refs: list[ObjectRef] = []
        for blob in self._client.list_blobs(self.bucket_name, prefix=prefix):
            refs.append(
                ObjectRef(
                    key=blob.name,
                    size=int(blob.size or 0),
                    last_modified=blob.updated,  # timezone-aware datetime
                )
            )
        return refs

    def open_read(self, key: str):
        blob = self._bucket.blob(key)
        return blob.open("rb")  # BlobReader file-like: .read(n), .close()

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()


def gcs_client_factory(
    config: BaseModel, secrets: dict[str, str], timeout: float
) -> ObjectStoreClient:
    return GcsObjectStoreClient(config, secrets, timeout)
