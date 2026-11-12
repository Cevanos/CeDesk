"""Azure Blob Storage: one container, signed in with the storage account's name and key.

The Azure SDK's client is synchronous here (its async one needs aiohttp), so each call runs
in a thread, as for S3. Downloads never pass through CeDesk: the browser fetches the blob
straight from Azure with a read-only SAS link that names the file and its type.

`endpoint` is for Azurite (Microsoft's local emulator) or a sovereign cloud; left empty it
is `https://<account>.blob.core.windows.net`.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobSasPermissions, BlobServiceClient, ContentSettings
from azure.storage.blob import generate_blob_sas as _generate_blob_sas

from cedesk.storage.base import ObjectMissing, check_key, content_disposition

CLOCK_SKEW = timedelta(minutes=5)  # a link starts a little in the past, so a slow clock is fine


class AzureStore:
    def __init__(
        self,
        *,
        account_name: str,
        account_key: str,
        container: str,
        endpoint: str | None = None,
    ) -> None:
        self.container = container
        self._account_name = account_name
        self._account_key = account_key
        url = (endpoint or f"https://{account_name}.blob.core.windows.net").rstrip("/")
        self._service: Any = BlobServiceClient(
            account_url=url,
            credential={"account_name": account_name, "account_key": account_key},
            retry_total=3,
        )
        self._container: Any = self._service.get_container_client(container)

    def _blob(self, key: str) -> Any:
        return self._container.get_blob_client(check_key(key))

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        blob = self._blob(key)
        await asyncio.to_thread(
            blob.upload_blob,
            data,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )

    async def get(self, key: str) -> bytes:
        blob = self._blob(key)

        def read() -> bytes:
            try:
                body: bytes = blob.download_blob().readall()
            except ResourceNotFoundError:  # no such blob, or no such container
                raise ObjectMissing(key) from None
            return body

        return await asyncio.to_thread(read)

    async def delete(self, key: str) -> None:
        blob = self._blob(key)

        def remove() -> None:
            try:
                blob.delete_blob()
            except ResourceNotFoundError:
                return

        await asyncio.to_thread(remove)

    async def download_url(self, key: str, *, name: str, content_type: str, expires_in: int) -> str:
        blob = self._blob(key)
        now = datetime.now(UTC)
        sas = _generate_blob_sas(
            account_name=self._account_name,
            container_name=self.container,
            blob_name=check_key(key),
            account_key=self._account_key,
            permission=BlobSasPermissions(read=True),
            start=now - CLOCK_SKEW,
            expiry=now + timedelta(seconds=expires_in),
            content_disposition=content_disposition(name),
            content_type=content_type,
            protocol=None if blob.url.startswith("http://") else "https",
        )
        return f"{blob.url}?{sas}"
