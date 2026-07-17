"""Real S3 / S3-compatible object-store source client (boto3).

Local-protocol driver: verified end-to-end against MinIO (the local S3 API,
CONVENTIONS.md END STATE). The same code targets real AWS S3 (omit ``endpoint``)
and any S3-compatible store. Listing is paginated; object bodies are read as a
streaming ``StreamingBody`` (``.read(n)``) so the engine never buffers a whole
object (ING-FR-041). boto3 blocking calls are wrapped in threads by the engine.

Credentials: ``access_key_id`` / ``secret_access_key`` secrets, or the ambient
provider chain (instance/role) when a ``role_arn`` config is used and no keys are
supplied.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.domain.drivers.objectsource import ObjectRef, ObjectStoreClient


class S3ObjectStoreClient:
    def __init__(self, config: BaseModel, secrets: dict[str, str], timeout: float) -> None:
        import boto3
        from botocore.client import Config as BotoConfig

        self.bucket = config.bucket
        kwargs: dict = {
            "region_name": getattr(config, "region", "us-east-1") or "us-east-1",
            "config": BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=timeout,
                read_timeout=max(timeout, 60.0),
                retries={"max_attempts": 2},
            ),
        }
        endpoint = getattr(config, "endpoint", None)
        if endpoint:
            kwargs["endpoint_url"] = endpoint
        if secrets.get("access_key_id"):
            kwargs["aws_access_key_id"] = secrets["access_key_id"]
        if secrets.get("secret_access_key"):
            kwargs["aws_secret_access_key"] = secrets["secret_access_key"]
        self._client = boto3.client("s3", **kwargs)

    def probe(self) -> None:
        # Trivial round-trip: list at most one key (ING-FR-004 "bucket HEAD/LIST").
        self._client.list_objects_v2(Bucket=self.bucket, MaxKeys=1)

    def list_objects(self, prefix: str) -> list[ObjectRef]:
        paginator = self._client.get_paginator("list_objects_v2")
        refs: list[ObjectRef] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                refs.append(
                    ObjectRef(
                        key=obj["Key"],
                        size=int(obj.get("Size", 0)),
                        last_modified=obj.get("LastModified"),
                    )
                )
        return refs

    def open_read(self, key: str):
        resp = self._client.get_object(Bucket=self.bucket, Key=key)
        return resp["Body"]  # botocore StreamingBody: .read(n), .close()

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()


def s3_client_factory(
    config: BaseModel, secrets: dict[str, str], timeout: float
) -> ObjectStoreClient:
    return S3ObjectStoreClient(config, secrets, timeout)
