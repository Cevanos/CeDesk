"""The storage drivers and signed links, without a database. S3 runs against moto's fake S3."""

import asyncio
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import boto3
import pytest
import requests
from moto import mock_aws

from cedesk.settings import Settings, StorageDriver
from cedesk.storage import (
    LocalStore,
    ObjectMissing,
    S3Store,
    content_disposition,
    links,
    make_store,
)
from cedesk.storage.base import check_key

KEY = "ws-1/ticket-1/file-1"
LINK_KEY = links.link_key("test-secret")


# ── keys and headers ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", ["../x", "a/../b", "/abs", "a//b", "a/.hidden", "a\\b", ""])
def test_keys_that_could_escape_are_refused(key: str) -> None:
    with pytest.raises(ValueError, match="storage key"):
        check_key(key)


def test_a_download_is_always_an_attachment_with_its_name() -> None:
    value = content_disposition('Résumé "final".pdf')
    assert value.startswith('attachment; filename="R_sum_ _final_.pdf"')
    assert "filename*=UTF-8''R%C3%A9sum%C3%A9%20%22final%22.pdf" in value


# ── signed links ─────────────────────────────────────────────────────────────


def _link(expires_at: int) -> links.FileLink:
    return links.FileLink(KEY, "photo.jpg", "image/jpeg", expires_at)


def test_a_signed_link_round_trips() -> None:
    link = _link(2_000_000_000)
    assert links.verify(links.sign(link, LINK_KEY), LINK_KEY, now=1_000) == link


def test_a_changed_link_is_refused() -> None:
    token = links.sign(_link(2_000_000_000), LINK_KEY)
    body, mac = token.split(".")
    other = links.sign(links.FileLink("ws-2/t/f", "x", "image/jpeg", 2_000_000_000), LINK_KEY)
    for bad in (
        f"{other.split('.')[0]}.{mac}",  # someone else's file with this signature
        f"{body}.{mac[:-2]}xx",
        body,
        "",
        "not.a.link",
        f"{body}.{mac}é",
    ):
        with pytest.raises(links.BadLink):
            links.verify(bad, LINK_KEY, now=1_000)


def test_a_link_signed_with_another_secret_is_refused() -> None:
    token = links.sign(_link(2_000_000_000), links.link_key("other-secret"))
    with pytest.raises(links.BadLink):
        links.verify(token, LINK_KEY, now=1_000)


def test_an_expired_link_says_so() -> None:
    token = links.sign(_link(1_000), LINK_KEY)
    with pytest.raises(links.ExpiredLink):
        links.verify(token, LINK_KEY, now=1_000)


# ── local disk ───────────────────────────────────────────────────────────────


def test_local_store_writes_reads_and_deletes(tmp_path: Path) -> None:
    store = LocalStore(tmp_path, LINK_KEY)

    async def run() -> None:
        await store.put(KEY, b"hello", content_type="text/plain")
        assert (tmp_path / "ws-1" / "ticket-1" / "file-1").read_bytes() == b"hello"
        assert await store.get(KEY) == b"hello"
        await store.put(KEY, b"replaced", content_type="text/plain")
        assert await store.get(KEY) == b"replaced"
        await store.delete(KEY)
        await store.delete(KEY)  # already gone: not an error
        with pytest.raises(ObjectMissing):
            await store.get(KEY)

    asyncio.run(run())
    leftovers = [p.name for p in tmp_path.rglob("*") if p.is_file()]
    assert leftovers == []


def test_local_store_never_leaves_its_folder(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "files", LINK_KEY)
    with pytest.raises(ValueError):
        store.path("../outside")
    assert store.path(KEY).is_relative_to((tmp_path / "files").resolve())


def test_local_download_url_is_a_signed_path(tmp_path: Path) -> None:
    store = LocalStore(tmp_path, LINK_KEY)
    url = asyncio.run(
        store.download_url(KEY, name="a.pdf", content_type="application/pdf", expires_in=60)
    )
    assert url.startswith("/api/v1/files/")
    link = links.verify(url.rsplit("/", 1)[1], LINK_KEY, now=int(time.time()))
    assert (link.key, link.name, link.content_type) == (KEY, "a.pdf", "application/pdf")
    assert 55 <= link.expires_at - int(time.time()) <= 60


# ── S3 (moto) ────────────────────────────────────────────────────────────────


@pytest.fixture
def s3() -> Iterator[S3Store]:
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="cedesk-files")
        yield S3Store(
            bucket="cedesk-files",
            region="us-east-1",
            access_key_id="testing",
            secret_access_key="testing",
        )


def test_s3_store_writes_reads_and_deletes(s3: S3Store) -> None:
    async def run() -> None:
        await s3.put(KEY, b"%PDF-1.7", content_type="application/pdf")
        assert await s3.get(KEY) == b"%PDF-1.7"
        await s3.delete(KEY)
        with pytest.raises(ObjectMissing):
            await s3.get(KEY)

    asyncio.run(run())


def test_s3_download_url_is_presigned_and_downloads_as_an_attachment(s3: S3Store) -> None:
    async def run() -> str:
        await s3.put(KEY, b"%PDF-1.7", content_type="application/pdf")
        return await s3.download_url(
            KEY, name="Report Q3.pdf", content_type="application/pdf", expires_in=300
        )

    url = asyncio.run(run())
    query = parse_qs(urlsplit(url).query)
    assert query["X-Amz-Expires"] == ["300"]
    assert "X-Amz-Signature" in query
    assert query["response-content-disposition"] == [content_disposition("Report Q3.pdf")]
    response = requests.get(url, timeout=5)
    assert response.status_code == 200
    assert response.content == b"%PDF-1.7"


def test_s3_refuses_keys_it_would_not_make(s3: S3Store) -> None:
    with pytest.raises(ValueError):
        asyncio.run(s3.put("../x", b"x", content_type="text/plain"))


# ── choosing the driver ──────────────────────────────────────────────────────


def test_the_default_driver_is_local_disk(tmp_path: Path) -> None:
    store = make_store(Settings(storage_local_path=str(tmp_path)))
    assert isinstance(store, LocalStore)
    assert not any(tmp_path.iterdir())  # nothing written until the first upload


def test_s3_is_one_setting_away() -> None:
    store = make_store(
        Settings(
            storage_driver=StorageDriver.s3,
            storage_s3_bucket="cedesk-files",
            storage_s3_region="ap-south-1",
            storage_s3_endpoint_url="http://localhost:9000",
            storage_s3_path_style=True,
        )
    )
    assert isinstance(store, S3Store)
    assert store.bucket == "cedesk-files"


def test_s3_without_a_bucket_stops_at_start_up() -> None:
    with pytest.raises(ValueError, match="CEDESK_STORAGE_S3_BUCKET"):
        make_store(Settings(storage_driver=StorageDriver.s3))
