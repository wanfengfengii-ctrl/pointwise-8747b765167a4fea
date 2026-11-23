from dataclasses import dataclass
import sqlite3

from registry.identity import NotFound, require_identifier


@dataclass(frozen=True)
class ReelWorklist:
    batch_id: str
    reel_ids: tuple[str, ...]


def load_worklist(db: sqlite3.Connection, batch_id: str) -> ReelWorklist:
    require_identifier(batch_id, 'batch_id')
    rows = db.execute('''
        SELECT reel_id FROM registered_reels
        WHERE batch_id = ? ORDER BY registration_order
    ''', (batch_id,))
    reel_ids = tuple(reel_id for reel_id, in rows)
    if not reel_ids:
        raise NotFound(f'unknown batch {batch_id!r}')
    return ReelWorklist(batch_id, reel_ids)
