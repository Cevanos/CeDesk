"""Where uploaded files live, chosen by one setting: `CEDESK_STORAGE_DRIVER`.

- `local`: a folder on this server (`CEDESK_STORAGE_LOCAL_PATH`).
- `s3`: AWS S3 or any S3-compatible service (`CEDESK_STORAGE_S3_*`).
- `gcs`: Google Cloud Storage through its S3-compatible API (`CEDESK_STORAGE_GCS_*`).
- `azure`: Azure Blob Storage (`CEDESK_STORAGE_AZURE_*`).

The rest of CeDesk only sees `ObjectStore`, so a driver changes nothing outside this
package.
"""

from pathlib import Path

from cedesk.settings import Settings, StorageDriver
from cedesk.storage.azure import AzureStore
from cedesk.storage.base import ObjectMissing, ObjectStore, content_disposition
from cedesk.storage.links import link_key
from cedesk.storage.local import LocalStore
from cedesk.storage.s3 import S3Store

GCS_ENDPOINT = "https://storage.googleapis.com"

__all__ = [
    "AzureStore",
    "LocalStore",
    "ObjectMissing",
    "ObjectStore",
    "S3Store",
    "content_disposition",
    "make_store",
]


def make_store(settings: Settings) -> ObjectStore:
    """The configured driver. A missing setting fails here, when the app starts, rather
    than at the first upload."""
    if settings.storage_driver is StorageDriver.s3:
        if not settings.storage_s3_bucket:
            raise ValueError("Set CEDESK_STORAGE_S3_BUCKET to use CEDESK_STORAGE_DRIVER=s3.")
        secret = settings.storage_s3_secret_access_key
        return S3Store(
            bucket=settings.storage_s3_bucket,
            region=settings.storage_s3_region,
            endpoint_url=settings.storage_s3_endpoint_url,
            access_key_id=settings.storage_s3_access_key_id,
            secret_access_key=secret.get_secret_value() if secret else None,
            path_style=settings.storage_s3_path_style,
        )
    if settings.storage_driver is StorageDriver.gcs:
        hmac = settings.storage_gcs_hmac_secret
        if not (settings.storage_gcs_bucket and settings.storage_gcs_hmac_access_id and hmac):
            raise ValueError(
                "Set CEDESK_STORAGE_GCS_BUCKET, CEDESK_STORAGE_GCS_HMAC_ACCESS_ID and "
                "CEDESK_STORAGE_GCS_HMAC_SECRET to use CEDESK_STORAGE_DRIVER=gcs."
            )
        return S3Store(
            bucket=settings.storage_gcs_bucket,
            region="auto",
            endpoint_url=GCS_ENDPOINT,
            access_key_id=settings.storage_gcs_hmac_access_id,
            secret_access_key=hmac.get_secret_value(),
            path_style=True,
        )
    if settings.storage_driver is StorageDriver.azure:
        key = settings.storage_azure_account_key
        if not (settings.storage_azure_account_name and key and settings.storage_azure_container):
            raise ValueError(
                "Set CEDESK_STORAGE_AZURE_ACCOUNT_NAME, CEDESK_STORAGE_AZURE_ACCOUNT_KEY and "
                "CEDESK_STORAGE_AZURE_CONTAINER to use CEDESK_STORAGE_DRIVER=azure."
            )
        return AzureStore(
            account_name=settings.storage_azure_account_name,
            account_key=key.get_secret_value(),
            container=settings.storage_azure_container,
            endpoint=settings.storage_azure_endpoint,
        )
    return LocalStore(
        Path(settings.storage_local_path), link_key(settings.secret_key.get_secret_value())
    )
