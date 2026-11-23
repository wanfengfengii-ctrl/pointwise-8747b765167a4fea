from dataclasses import dataclass
import json
import sqlite3

from custody.device import DeviceManifest, ReleaseOutcome
from registry.identity import Conflict, NotFound


@dataclass(frozen=True)
class PageEvidence:
    reel_id: str
    device_id: str
    page_id: str
    position: int
    start: int
    length: int
    checksum: str
    ticket: int
    payload: bytes | None
    outcome: ReleaseOutcome


def initialize_custody(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS custody_manifests (
            reel_id TEXT PRIMARY KEY REFERENCES registered_reels (reel_id),
            device_id TEXT NOT NULL,
            page_ids TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS custody_pages (
            reel_id TEXT NOT NULL REFERENCES custody_manifests (reel_id),
            device_id TEXT NOT NULL,
            page_id TEXT NOT NULL,
            position INTEGER NOT NULL CHECK (position >= 0),
            byte_start INTEGER NOT NULL CHECK (byte_start >= 0),
            byte_length INTEGER NOT NULL CHECK (byte_length >= 0),
            checksum TEXT NOT NULL,
            ticket INTEGER NOT NULL,
            payload BLOB,
            outcome TEXT NOT NULL CHECK (outcome IN ('current', 'unknown', 'released')),
            PRIMARY KEY (reel_id, page_id),
            UNIQUE (reel_id, position)
        )
    ''')


def load_manifest(db: sqlite3.Connection, reel_id: str) -> DeviceManifest | None:
    row = db.execute('''
        SELECT device_id, page_ids FROM custody_manifests WHERE reel_id = ?
    ''', (reel_id,)).fetchone()
    if row is None:
        return None
    return DeviceManifest(row[0], reel_id, tuple(json.loads(row[1])))


def save_manifest(db: sqlite3.Connection, manifest: DeviceManifest) -> None:
    saved = load_manifest(db, manifest.reel_id)
    if saved is None:
        db.execute('INSERT INTO custody_manifests VALUES (?, ?, ?)', (
            manifest.reel_id, manifest.device_id, json.dumps(manifest.page_ids),
        ))
    elif saved != manifest:
        raise Conflict(f'device manifest changed for reel {manifest.reel_id!r}')


def save_page_evidence(db: sqlite3.Connection, page: PageEvidence) -> None:
    db.execute('''
        INSERT INTO custody_pages (
            reel_id, device_id, page_id, position, byte_start, byte_length,
            checksum, ticket, payload, outcome
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (page.reel_id, page.device_id, page.page_id, page.position, page.start,
          page.length, page.checksum, page.ticket, page.payload, page.outcome.value))


def _from_row(row: tuple) -> PageEvidence:
    return PageEvidence(*row[:-1], ReleaseOutcome(row[-1]))


def load_page_evidence(db: sqlite3.Connection, reel_id: str, page_id: str) -> PageEvidence | None:
    row = db.execute('''
        SELECT reel_id, device_id, page_id, position, byte_start, byte_length,
               checksum, ticket, payload, outcome
        FROM custody_pages WHERE reel_id = ? AND page_id = ?
    ''', (reel_id, page_id)).fetchone()
    return None if row is None else _from_row(row)


def list_page_evidence(db: sqlite3.Connection, reel_id: str) -> tuple[PageEvidence, ...]:
    rows = db.execute('''
        SELECT reel_id, device_id, page_id, position, byte_start, byte_length,
               checksum, ticket, payload, outcome
        FROM custody_pages WHERE reel_id = ? ORDER BY position
    ''', (reel_id,))
    return tuple(_from_row(row) for row in rows)


def record_release_status(db: sqlite3.Connection, page: PageEvidence,
                          outcome: ReleaseOutcome) -> ReleaseOutcome:
    if outcome not in (ReleaseOutcome.UNKNOWN, ReleaseOutcome.RELEASED):
        raise ValueError('release observation must be unknown or confirmed')
    key = (page.reel_id, page.page_id, page.device_id, page.ticket)
    db.execute('''
        UPDATE custody_pages SET outcome = ?
        WHERE reel_id = ? AND page_id = ? AND device_id = ? AND ticket = ?
          AND outcome <> 'released'
    ''', (outcome.value, *key))
    row = db.execute('''
        SELECT outcome FROM custody_pages
        WHERE reel_id = ? AND page_id = ? AND device_id = ? AND ticket = ?
    ''', key).fetchone()
    if row is None:
        raise NotFound('release observation has no matching original intake')
    return ReleaseOutcome(row[0])
