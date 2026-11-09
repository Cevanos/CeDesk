"""Where uploaded files live, chosen by one setting: `CEDESK_STORAGE_DRIVER`.

- `local`: a folder on this server (`CEDESK_STORAGE_LOCAL_PATH`).
- `s3`: AWS S3 or any S3-compatible service (`CEDESK_STORAGE_S3_*`).

The rest of CeDesk only sees `ObjectStore`, so adding a driver (GCS, Azure) changes
nothing outside this package.
"""

from pathlib import Path

from cedesk.settings import Settings, StorageDriver
from cedesk.storage.base import ObjectMissing, ObjectStore, content_disposition
from cedesk.storage.links import link_key
from cedesk.storage.local import LocalStore
from cedesk.storage.s3 import S3Store

__all__ = [
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
    return LocalStore(
        Path(settings.storage_local_path), link_key(settings.secret_key.get_secret_value())
    )
