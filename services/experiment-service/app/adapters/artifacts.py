"""Signed artifact-URL generation (EXP-FR-014).

The artifacts *index* (paths + sizes) is mirrored; the bytes are not. A read for
artifact content resolves the mirrored ``artifact_uri`` + relative path to a
short-lived presigned GET URL against object storage (MinIO S3). The local
signer is the unit-tier double (HMAC pseudo-URL).
"""

from __future__ import annotations

import asyncio
import hmac
import time
from hashlib import sha256
from urllib.parse import urlparse


class S3ArtifactSigner:
    """Real presigned GET URLs via botocore against MinIO."""

    def __init__(self, *, endpoint_url: str, access_key: str, secret_key: str,
                 region: str, default_bucket: str = "mlflow"):
        import boto3
        from botocore.client import Config as BotoConfig

        self.default_bucket = default_bucket
        self._client = boto3.client(
            "s3", endpoint_url=endpoint_url, aws_access_key_id=access_key,
            aws_secret_access_key=secret_key, region_name=region,
            config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def _resolve(self, artifact_uri: str, path: str) -> tuple[str, str]:
        parsed = urlparse(artifact_uri)
        if parsed.scheme == "s3":
            bucket = parsed.netloc
            prefix = parsed.path.lstrip("/")
        else:
            # mlflow-artifacts:/<exp>/<run>/artifacts -> default bucket
            bucket = self.default_bucket
            prefix = parsed.path.lstrip("/")
        key = f"{prefix.rstrip('/')}/{path.lstrip('/')}"
        return bucket, key

    async def signed_url(self, artifact_uri: str, path: str, ttl_seconds: int) -> str:
        bucket, key = self._resolve(artifact_uri, path)
        return await asyncio.to_thread(
            self._client.generate_presigned_url, "get_object",
            Params={"Bucket": bucket, "Key": key}, ExpiresIn=ttl_seconds,
        )


class LocalArtifactSigner:
    """Unit-tier HMAC pseudo-URL signer (never wired from app.main)."""

    def __init__(self, secret: str = "dev-artifact-secret"):
        self._secret = secret.encode()

    async def signed_url(self, artifact_uri: str, path: str, ttl_seconds: int) -> str:
        expires = int(time.time()) + ttl_seconds
        sig = hmac.new(self._secret, f"{artifact_uri}/{path}:{expires}".encode(),
                       sha256).hexdigest()[:32]
        return (f"https://artifacts.windrose.local/{artifact_uri}/{path}"
                f"?expires={expires}&sig={sig}")
