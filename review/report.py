from __future__ import annotations

from typing import TYPE_CHECKING

from archive.receipts import PublishedReport, restore_publication
from archive.store import load_snapshot
from registry.identity import NotFound, PendingReel, require_identifier
from registry.intake import find_registered_reel

if TYPE_CHECKING:
    from registry.session import ExchangeSession


def get_report(session: ExchangeSession, reel_id: str) -> PublishedReport:
    require_identifier(reel_id, 'reel_id')
    db = session.ledger
    with db:
        db.execute('BEGIN')
        snapshot = load_snapshot(db, reel_id)
        if snapshot is not None:
            return restore_publication(db, snapshot)
        if find_registered_reel(db, reel_id) is not None:
            raise PendingReel(f'reel {reel_id!r} has no committed report')
        raise NotFound(f'unknown reel {reel_id!r}')
