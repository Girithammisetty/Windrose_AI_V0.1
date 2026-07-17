"""Real Azure Blob Storage object-store source client (azure-storage-blob).

Credential-gated: the adapter drives the real Azure SDK, but a live pull needs an
``account_key`` or ``sas_token`` secret. The SDK is imported lazily and only
lives in the optional ``cloud`` extra, so the base install stays light and
``make test-unit`` runs without it. A contract test injects a fake service client
(``_service=``) to exercise list/read shaping offline — no SDK, no network.

Same list/preview/fetch/incremental shape as the S3 client. Blob downloads are
read in chunks and adapted to a ``.read(n)`` body (``_ChunkReader``) so the
engine never buffers a whole blob (ING-FR-041).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.domain.drivers.objectsource import ObjectRef, ObjectStoreClient


class _ChunkReader:
    """Adapt an Azure download chunk-iterator to a ``.read(n)`` streaming body."""

    def __init__(self, chunks: Any) -> None:
        self._chunks = iter(chunks)
        self._buffer = bytearray()
        self._eof = False

    def read(self, size: int) -> bytes:
        while not self._eof and len(self._buffer) < size:
            try:
                self._buffer.extend(next(self._chunks))
            except StopIteration:
                self._eof = True
        take = bytes(self._buffer[:size])
        del self._buffer[: len(take)]
        return take

    def close(self) -> None:
        self._chunks = iter(())


class AzureBlobObjectStoreClient:
    def __init__(
        self,
        config: BaseModel,
        secrets: dict[str, str],
        timeout: float,
        *,
        _service: Any | None = None,
    ) -> None:
        self.account_name = config.account_name
        self.container = config.container_name
        self._timeout = timeout
        if _service is not None:
            self._service = _service
        else:
            from azure.storage.blob import BlobServiceClient

            account_url = f"https://{self.account_name}.blob.core.windows.net"
            if secrets.get("sas_token"):
                self._service = BlobServiceClient(
                    account_url=account_url, credential=secrets["sas_token"]
                )
            elif secrets.get("account_key"):
                self._service = BlobServiceClient(
                    account_url=account_url,
                    credential={
                        "account_name": self.account_name,
                        "account_key": secrets["account_key"],
                    },
                )
            else:  # public container / ambient identity
                self._service = BlobServiceClient(account_url=account_url)
        self._container_client = self._service.get_container_client(self.container)

    def probe(self) -> None:
        # Trivial round-trip: container properties (bucket HEAD equivalent).
        self._container_client.get_container_properties()

    def list_objects(self, prefix: str) -> list[ObjectRef]:
        refs: list[ObjectRef] = []
        for blob in self._container_client.list_blobs(name_starts_with=prefix or None):
            refs.append(
                ObjectRef(
                    key=blob.name,
                    size=int(getattr(blob, "size", 0) or 0),
                    last_modified=getattr(blob, "last_modified", None),
                )
            )
        return refs

    def open_read(self, key: str):
        downloader = self._container_client.download_blob(key)
        return _ChunkReader(downloader.chunks())

    def close(self) -> None:
        close = getattr(self._service, "close", None)
        if callable(close):
            close()


def azure_blob_client_factory(
    config: BaseModel, secrets: dict[str, str], timeout: float
) -> ObjectStoreClient:
    return AzureBlobObjectStoreClient(config, secrets, timeout)
