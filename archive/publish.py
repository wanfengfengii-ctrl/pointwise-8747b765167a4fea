from __future__ import annotations

from typing import TYPE_CHECKING

from archive.admission import authorization_key, evaluate_admission
from archive.receipts import PublishedReport, issue_first_receipt, restore_publication
from archive.seal import TailSeal
from archive.store import ArchiveSnapshot, load_snapshot, save_snapshot
from custody.evidence import load_manifest, save_manifest
from reading.framing import Dialect
from registry.identity import Conflict, InvalidMaterial, PendingReel
from registry.intake import require_registered_reel

if TYPE_CHECKING:
    from registry.session import ExchangeSession


def finalize(session: ExchangeSession, reel_id: str,
             authorization: Dialect | TailSeal | None = None) -> PublishedReport:
    db = session.ledger
    with db:
        db.execute('BEGIN IMMEDIATE')
        reel = require_registered_reel(db, reel_id)
        existing = load_snapshot(db, reel_id)
        if existing is not None:
            try:
                key = authorization_key(reel, authorization)
            except (InvalidMaterial, PendingReel) as error:
                raise Conflict(f'reel {reel_id!r} already has a different publication') from error
            if key != existing.authorization_key:
                raise Conflict(f'reel {reel_id!r} already has a different publication')
            return restore_publication(db, existing)

        # An empty reel can have a device manifest without an intake call.
        if load_manifest(db, reel_id) is None and session.device is not None:
            manifest = session.device.reel_manifest(reel.device_id, reel_id)
            if manifest is not None:
                save_manifest(db, manifest)
        admitted = evaluate_admission(db, reel, authorization)
        snapshot = ArchiveSnapshot(admitted.reel_id, admitted.dialect, admitted.byte_count,
                                   admitted.records, admitted.total, admitted.authorization_key)
        save_snapshot(db, snapshot)
        receipt = issue_first_receipt(db, reel_id)
        return PublishedReport(snapshot, receipt)
