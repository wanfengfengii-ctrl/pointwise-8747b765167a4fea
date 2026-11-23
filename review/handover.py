from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from archive.store import list_published
from registry.worklist import load_worklist

if TYPE_CHECKING:
    from registry.session import ExchangeSession


@dataclass(frozen=True)
class HandoverList:
    batch_id: str
    ready: tuple[str, ...]
    pending: tuple[str, ...]


def handover_list(session: ExchangeSession, batch_id: str) -> HandoverList:
    db = session.ledger
    with db:
        db.execute('BEGIN')
        worklist = load_worklist(db, batch_id)
        published = list_published(db, batch_id)
        ready = tuple(reel_id for reel_id in worklist.reel_ids if reel_id in published)
        pending = tuple(reel_id for reel_id in worklist.reel_ids if reel_id not in published)
        return HandoverList(batch_id, ready, pending)
