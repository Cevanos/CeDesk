"""Virus scan of stored files (md T18), run by the worker.

Every file starts `not_scanned`. With a scanner set up, the worker reads each new file
from storage and asks the scanner:
- clean: it can be downloaded and emailed;
- infected: its bytes are deleted from storage and a system line on the ticket names it;
- the scanner can't check this file (too big for it): `scan_failed`, blocked the same way;
- the scanner is down: the pass stops and the files are tried on the next one.
"""

import logging
from dataclasses import dataclass
from uuid import UUID

from cedesk.db import workspace_session
from cedesk.helpdesk.domain.attachments import ScanStatus
from cedesk.helpdesk.repo import attachments as repo
from cedesk.helpdesk.repo import auth as auth_repo
from cedesk.helpdesk.repo import tickets as ticket_repo
from cedesk.helpdesk.service.auth import Sessionmaker
from cedesk.scanning import ScanError, Scanner
from cedesk.storage import ObjectMissing, ObjectStore

logger = logging.getLogger("cedesk.scanning")

BATCH = 20


@dataclass
class ScanResult:
    clean: int = 0
    infected: int = 0
    failed: int = 0
    waiting: bool = False  # the scanner was unreachable; files left for the next pass


class _ScannerDown(Exception):
    pass


async def _scan_workspace(
    sm: Sessionmaker,
    store: ObjectStore,
    scanner: Scanner,
    workspace_id: UUID,
    result: ScanResult,
    batch: int,
) -> None:
    async with workspace_session(sm, workspace_id) as s:
        files = await repo.to_scan(s, batch)
    for f in files:
        # Read and checked outside any transaction: a slow scan holds no lock.
        status, note = ScanStatus.clean, None
        try:
            verdict = await scanner.scan(await store.get(str(f["object_key"])))
            if not verdict.clean:
                status, note = ScanStatus.infected, verdict.virus
        except ObjectMissing:
            status, note = ScanStatus.scan_failed, "missing from storage"
        except ScanError as exc:
            if not exc.permanent:
                raise _ScannerDown(str(exc)) from exc
            status, note = ScanStatus.scan_failed, str(exc)[:200]

        async with workspace_session(sm, workspace_id) as s:
            if not await repo.set_scan(s, f["id"], status.value, note):
                continue  # another worker recorded it
            if status is ScanStatus.clean:
                result.clean += 1
                continue
            if status is ScanStatus.infected:
                result.infected += 1
                line = f"File {f['name']} was blocked: a virus was found ({note})."
            else:
                result.failed += 1
                line = f"File {f['name']} was blocked: it could not be checked for viruses."
            await ticket_repo.insert_message(
                s, workspace_id=workspace_id, ticket_id=f["ticket_id"], kind="system",
                body=line[:500], author_member_id=None, author_name=None,
            )  # fmt: skip
        logger.warning(
            "file blocked by the virus scan",
            extra={"workspace_id": workspace_id, "ticket_id": f["ticket_id"], "error": note},
        )
        if status is ScanStatus.infected:
            await store.delete(str(f["object_key"]))  # the row stays, for the record


async def scan_due(
    sm: Sessionmaker, store: ObjectStore, scanner: Scanner, *, batch: int = BATCH
) -> ScanResult:
    """One pass over every workspace, each under its own RLS."""
    async with sm() as s, s.begin():
        workspace_ids = await auth_repo.all_workspace_ids(s)
    result = ScanResult()
    for workspace_id in workspace_ids:
        try:
            await _scan_workspace(sm, store, scanner, workspace_id, result, batch)
        except _ScannerDown as exc:
            logger.warning("virus scanner unavailable", extra={"error": str(exc)})
            result.waiting = True
            break  # no use trying the other workspaces now
        except Exception:
            logger.exception("scan pass failed", extra={"workspace_id": workspace_id})
    return result
