# ruff: noqa: S603, S607  (runs the deploy scripts, gpg and bash on purpose)
"""deploy/backup.sh and deploy/restore.sh, end to end with throwaway data.

`docker` is replaced by a small script: `pg_dump` prints a known dump, `tar` packs a
folder of known files, and `psql` records what it was given. A gpg key made for the test
stands in for the offline backup key. Nothing here touches a real database or server.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / "deploy" / "backup.sh"
RESTORE = ROOT / "deploy" / "restore.sh"
SECRET = "jane.customer@example.com said: my card is in the post"

pytestmark = pytest.mark.skipif(
    shutil.which("gpg") is None or shutil.which("bash") is None, reason="needs gpg and bash"
)

FAKE_DOCKER = r"""#!/usr/bin/env bash
# docker compose [-f x]... exec -T <service> <command...>
while [ "$1" != exec ]; do shift; done
shift; [ "$1" = -T ] && shift
shift  # the service
case "$1" in
  pg_dump) [ -n "${FAKE_DUMP_FAILS:-}" ] && exit 1; cat "$FAKE_DUMP" ;;
  tar) cd "$FAKE_FILES" && tar -cf - . ;;
  psql)
    for a in "$@"; do
      if [ "$a" = -c ]; then echo "$*" >> "$FAKE_PSQL_LOG"; exit 0; fi
    done
    cat > "$FAKE_RESTORED" ;;
  *) exit 9 ;;
esac
"""


@pytest.fixture(scope="module")
def gnupg() -> Iterator[tuple[str, str]]:
    """A keyring with one key pair; returns (GNUPGHOME, fingerprint). Short path: gpg-agent's
    socket path has a length limit."""
    home = tempfile.mkdtemp(prefix="g", dir="/tmp")
    env = {**os.environ, "GNUPGHOME": home}
    subprocess.run(
        ["gpg", "--batch", "--passphrase", "", "--quick-gen-key",
         "CeDesk backup test <backup-test@example.invalid>", "default", "default", "never"],
        env=env, check=True, capture_output=True,
    )  # fmt: skip
    out = subprocess.run(
        ["gpg", "--batch", "--with-colons", "--list-keys"],
        env=env, check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    fingerprint = next(line.split(":")[9] for line in out.splitlines() if line.startswith("fpr"))
    yield home, fingerprint
    subprocess.run(["gpgconf", "--kill", "gpg-agent"], env=env, check=False)
    shutil.rmtree(home, ignore_errors=True)


@pytest.fixture
def world(tmp_path: Path, gnupg: tuple[str, str]) -> dict[str, str]:
    home, fingerprint = gnupg
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    dump = tmp_path / "dump.sql"
    dump.write_text(f"-- fake dump\nINSERT INTO hd_contact VALUES ('{SECRET}');\n")
    files = tmp_path / "files"
    (files / "ws1").mkdir(parents=True)
    (files / "ws1" / "invoice.pdf").write_bytes(b"%PDF-1.4 " + SECRET.encode())
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "GNUPGHOME": home,
        "BACKUP_CONFIG": str(tmp_path / "no-such-config"),
        "BACKUP_DIR": str(tmp_path / "backups"),
        "BACKUP_GPG_RECIPIENT": fingerprint,
        "FAKE_DUMP": str(dump),
        "FAKE_FILES": str(files),
        "FAKE_PSQL_LOG": str(tmp_path / "psql.log"),
        "FAKE_RESTORED": str(tmp_path / "restored.sql"),
    }


def run(script: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), *args], env=env, capture_output=True, text=True, timeout=60
    )


def manifests(env: dict[str, str]) -> list[Path]:
    return sorted(Path(env["BACKUP_DIR"]).glob("cedesk-*.manifest"))


def test_a_backup_is_encrypted_private_and_restores_exactly(world: dict[str, str]) -> None:
    done = run(BACKUP, world)
    assert done.returncode == 0, done.stderr
    backups = Path(world["BACKUP_DIR"])
    assert stat.S_IMODE(backups.stat().st_mode) == 0o700
    stored = sorted(p.name for p in backups.iterdir())
    assert len(stored) == 3, stored  # manifest, database, files; no work folder left
    assert all(n.endswith((".manifest", ".gpg")) for n in stored)
    for p in backups.iterdir():
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
        assert SECRET.encode() not in p.read_bytes()  # neither in clear nor in the manifest

    (manifest,) = manifests(world)
    out = Path(world["HOME"]) / "restored-files"
    done = run(RESTORE, world, str(manifest), "--files-to", str(out))
    assert done.returncode == 0, done.stderr
    assert Path(world["FAKE_RESTORED"]).read_text() == Path(world["FAKE_DUMP"]).read_text()
    assert (out / "ws1" / "invoice.pdf").read_bytes().endswith(SECRET.encode())
    log = Path(world["FAKE_PSQL_LOG"]).read_text()
    assert "CREATE DATABASE cedesk_restore_" in log  # a new database, never the live one


def test_a_changed_backup_is_refused_and_nothing_is_restored(world: dict[str, str]) -> None:
    assert run(BACKUP, world).returncode == 0
    (manifest,) = manifests(world)
    db = next(Path(world["BACKUP_DIR"]).glob("*-db.sql.gz.gpg"))
    data = bytearray(db.read_bytes())
    data[len(data) // 2] ^= 0xFF
    db.write_bytes(bytes(data))
    done = run(RESTORE, world, str(manifest))
    assert done.returncode != 0
    assert not Path(world["FAKE_RESTORED"]).exists()


def test_the_live_database_is_never_overwritten_by_accident(world: dict[str, str]) -> None:
    assert run(BACKUP, world).returncode == 0
    (manifest,) = manifests(world)
    done = run(RESTORE, world, str(manifest), "--db", "cedesk")
    assert done.returncode == 2 and "--force" in done.stderr
    assert not Path(world["FAKE_RESTORED"]).exists()


def test_without_a_key_the_backup_is_kept_private_and_flagged(world: dict[str, str]) -> None:
    env = {**world, "BACKUP_GPG_RECIPIENT": ""}
    done = run(BACKUP, env)
    assert done.returncode == 3 and "NOT encrypted" in done.stderr
    for p in Path(env["BACKUP_DIR"]).iterdir():
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    (manifest,) = manifests(env)
    assert run(RESTORE, env, str(manifest)).returncode == 0  # still restorable


def test_strict_mode_refuses_without_a_key(world: dict[str, str]) -> None:
    env = {**world, "BACKUP_GPG_RECIPIENT": "", "BACKUP_REQUIRE_ENCRYPTION": "1"}
    done = run(BACKUP, env)
    assert done.returncode not in (0, 3) and "FAILED" in done.stderr
    assert manifests(env) == []


def test_a_failed_dump_writes_nothing_and_says_so(world: dict[str, str]) -> None:
    done = run(BACKUP, {**world, "FAKE_DUMP_FAILS": "1"})
    assert done.returncode != 0
    assert "FAILED at step: database dump" in done.stderr
    assert list(Path(world["BACKUP_DIR"]).iterdir()) == []  # no half backup, no work folder


def test_old_sets_go_but_the_newest_and_older_backups_stay(world: dict[str, str]) -> None:
    backups = Path(world["BACKUP_DIR"])
    backups.mkdir(mode=0o700)
    month_ago = time.time() - 30 * 86400
    for stamp in ("20250101-000000", "20250102-000000", "20250103-000000"):
        for name in (f"cedesk-{stamp}.manifest", f"cedesk-{stamp}-db.sql.gz.gpg"):
            (backups / name).write_text("old")
            os.utime(backups / name, (month_ago, month_ago))
    legacy = backups / "cedesk-20250101-0230.sql.gz"  # from the previous backup.sh
    legacy.write_text("old")
    os.utime(legacy, (month_ago, month_ago))

    done = run(BACKUP, {**world, "BACKUP_KEEP_DAYS": "14", "BACKUP_KEEP_MIN": "2"})
    assert done.returncode == 0, done.stderr
    left = sorted(p.name for p in backups.iterdir())
    assert legacy.name in left
    assert "cedesk-20250103-000000.manifest" in left  # the 2nd newest set is kept
    assert "cedesk-20250101-000000.manifest" not in left
    assert "cedesk-20250101-000000-db.sql.gz.gpg" not in left
    assert len(manifests(world)) == 2
