"""AWS S3, and any S3-compatible service (MinIO, Cloudflare R2, DigitalOcean Spaces, Wasabi,
Backblaze B2) through `endpoint_url`.

boto3 is synchronous, so each call runs in a thread. Downloads never pass through CeDesk:
the browser fetches the file straight from the bucket with a presigned link.
"""

import asyncio
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from cedesk.storage.base import ObjectMissing, check_key, content_disposition

# What S3 answers for a key or bucket that is not there.
_MISSING = frozenset({"404", "NoSuchKey", "NoSuchBucket"})


class S3Store:
    def __init__(
        self,
        *,
        bucket: str,
        region: str | None = None,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        path_style: bool = False,
    ) -> None:
        self.bucket = bucket
        # Signature v4: what AWS requires for presigned links, and what MinIO accepts.
        self._client: Any = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url or None,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 3},
                s3={"addressing_style": "path" if path_style else "auto"},
            ),
        )

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self.bucket,
            Key=check_key(key),
            Body=data,
            ContentType=content_type,
        )

    async def get(self, key: str) -> bytes:
        def read() -> bytes:
            try:
                response = self._client.get_object(Bucket=self.bucket, Key=check_key(key))
            except ClientError as exc:
                if str(exc.response.get("Error", {}).get("Code")) in _MISSING:
                    raise ObjectMissing(key) from None
                raise
            body: bytes = response["Body"].read()
            return body

        return await asyncio.to_thread(read)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self.bucket, Key=check_key(key))

    async def download_url(
        self, key: str, *, name: str, content_type: str, expires_in: int
    ) -> str:
        # A thread too: the first signing may fetch credentials from the instance role.
        url: str = await asyncio.to_thread(
            self._client.generate_presigned_url,
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": check_key(key),
                "ResponseContentDisposition": content_disposition(name),
                "ResponseContentType": content_type,
            },
            ExpiresIn=expires_in,
        )
        return url
