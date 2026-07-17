"""Real S3 object storage against MinIO (the local S3 API).

Two adapter shapes are exported because the two Windrose services define two
different ObjectStore ports:

* ``S3StreamingObjectStore`` — ingestion-service's byte-movement port. ``put``
  consumes an *async byte iterator* and streams it to S3 as a multipart upload
  with at most one part buffered in memory (ING-FR-041 memory bound). ``etag``
  is the sha256 hex of the content (not the S3 ETag) so the checksum semantics
  match the local-fs store the service was built against.
* ``S3BlobObjectStore`` — dataset-service's blob port: ``put(key, bytes,
  content_type)`` / ``get`` / ``signed_url`` (real presigned GET URLs).

All blocking boto3 calls run in a thread so the adapters are usable from async
code without blocking the event loop.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

DEFAULT_CHUNK = 1024 * 1024  # 1 MiB read chunks
DEFAULT_PART_SIZE = 8 * 1024 * 1024  # 8 MiB multipart parts (>= S3 5 MiB minimum)


@dataclass(slots=True)
class PutResult:
    size: int
    etag: str  # sha256 hex of content (matches the local-fs store semantics)


@dataclass(slots=True)
class S3Config:
    endpoint_url: str
    access_key: str
    secret_key: str
    bucket: str
    region: str = "us-east-1"

    @classmethod
    def for_minio(
        cls,
        bucket: str,
        *,
        endpoint_url: str = "http://localhost:9000",
        access_key: str = "windrose",
        secret_key: str = "windrose_dev",
        region: str = "us-east-1",
    ) -> S3Config:
        return cls(
            endpoint_url=endpoint_url,
            access_key=access_key,
            secret_key=secret_key,
            bucket=bucket,
            region=region,
        )


def build_s3_client(cfg: S3Config):
    """A path-style boto3 S3 client (MinIO requires path-style addressing)."""
    return boto3.client(
        "s3",
        endpoint_url=cfg.endpoint_url,
        aws_access_key_id=cfg.access_key,
        aws_secret_access_key=cfg.secret_key,
        region_name=cfg.region,
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


class _S3Base:
    def __init__(self, cfg: S3Config, *, part_size: int = DEFAULT_PART_SIZE) -> None:
        self.cfg = cfg
        self.bucket = cfg.bucket
        self.part_size = part_size
        self._client = build_s3_client(cfg)

    @staticmethod
    def _key(key: str) -> str:
        return key.lstrip("/")


class S3StreamingObjectStore(_S3Base):
    """ingestion-service ObjectStore port, backed by MinIO multipart uploads."""

    async def put(self, key: str, stream: AsyncIterator[bytes]) -> PutResult:
        key = self._key(key)
        digest = hashlib.sha256()
        size = 0
        upload_id = (
            await asyncio.to_thread(
                self._client.create_multipart_upload, Bucket=self.bucket, Key=key
            )
        )["UploadId"]
        parts: list[dict] = []
        part_number = 1
        buffer = bytearray()
        try:
            async for chunk in stream:
                if not chunk:
                    continue
                digest.update(chunk)
                size += len(chunk)
                buffer.extend(chunk)
                while len(buffer) >= self.part_size:
                    body = bytes(buffer[: self.part_size])
                    del buffer[: self.part_size]
                    parts.append(await self._upload_part(key, upload_id, part_number, body))
                    part_number += 1
            if buffer or not parts:
                # flush the tail (or an empty object with a single empty part)
                parts.append(
                    await self._upload_part(key, upload_id, part_number, bytes(buffer))
                )
            await asyncio.to_thread(
                self._client.complete_multipart_upload,
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except BaseException:
            await asyncio.to_thread(
                self._client.abort_multipart_upload,
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
            )
            raise
        return PutResult(size=size, etag=digest.hexdigest())

    async def _upload_part(
        self, key: str, upload_id: str, part_number: int, body: bytes
    ) -> dict:
        resp = await asyncio.to_thread(
            self._client.upload_part,
            Bucket=self.bucket,
            Key=key,
            UploadId=upload_id,
            PartNumber=part_number,
            Body=body,
        )
        return {"PartNumber": part_number, "ETag": resp["ETag"]}

    async def open_stream(
        self, key: str, chunk_size: int = DEFAULT_CHUNK
    ) -> AsyncIterator[bytes]:
        key = self._key(key)
        resp = await asyncio.to_thread(
            self._client.get_object, Bucket=self.bucket, Key=key
        )
        body = resp["Body"]
        try:
            while True:
                chunk = await asyncio.to_thread(body.read, chunk_size)
                if not chunk:
                    return
                yield chunk
        finally:
            await asyncio.to_thread(body.close)

    async def exists(self, key: str) -> bool:
        try:
            await asyncio.to_thread(
                self._client.head_object, Bucket=self.bucket, Key=self._key(key)
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    async def size(self, key: str) -> int:
        resp = await asyncio.to_thread(
            self._client.head_object, Bucket=self.bucket, Key=self._key(key)
        )
        return int(resp["ContentLength"])

    async def move(self, src: str, dst: str) -> None:
        src, dst = self._key(src), self._key(dst)
        await asyncio.to_thread(
            self._client.copy_object,
            Bucket=self.bucket,
            Key=dst,
            CopySource={"Bucket": self.bucket, "Key": src},
        )
        await asyncio.to_thread(self._client.delete_object, Bucket=self.bucket, Key=src)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(
            self._client.delete_object, Bucket=self.bucket, Key=self._key(key)
        )

    async def delete_prefix(self, prefix: str) -> int:
        prefix = self._key(prefix)
        if prefix and not prefix.endswith("/"):
            prefix = prefix + "/"
        paginator = self._client.get_paginator("list_objects_v2")
        count = 0

        def _collect_and_delete() -> int:
            deleted = 0
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                if objects:
                    self._client.delete_objects(
                        Bucket=self.bucket, Delete={"Objects": objects}
                    )
                    deleted += len(objects)
            return deleted

        count = await asyncio.to_thread(_collect_and_delete)
        return count


class S3BlobObjectStore(_S3Base):
    """dataset-service ObjectStore port: whole-blob put/get + presigned URLs."""

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self.bucket,
            Key=self._key(key),
            Body=data,
            ContentType=content_type,
        )

    async def get(self, key: str) -> bytes:
        resp = await asyncio.to_thread(
            self._client.get_object, Bucket=self.bucket, Key=self._key(key)
        )
        try:
            return await asyncio.to_thread(resp["Body"].read)
        finally:
            await asyncio.to_thread(resp["Body"].close)

    async def exists(self, key: str) -> bool:
        try:
            await asyncio.to_thread(
                self._client.head_object, Bucket=self.bucket, Key=self._key(key)
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(
            self._client.delete_object, Bucket=self.bucket, Key=self._key(key)
        )

    async def signed_url(self, key: str, ttl_hours: int) -> str:
        return await asyncio.to_thread(
            self._client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self.bucket, "Key": self._key(key)},
            ExpiresIn=ttl_hours * 3600,
        )
