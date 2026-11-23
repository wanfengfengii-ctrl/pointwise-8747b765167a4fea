from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path
import sqlite3
from typing import Iterable


class ReleaseOutcome(Enum):
    CURRENT = 'current'
    UNKNOWN = 'unknown'
    RELEASED = 'released'


class DevicePageUnavailable(LookupError):
    pass


class ReleaseReplyLost(TimeoutError):
    pass


@dataclass(frozen=True)
class DevicePage:
    device_id: str
    reel_id: str
    page_id: str
    ticket: int
    checksum: str
    payload: bytes


@dataclass(frozen=True)
class DeviceManifest:
    device_id: str
    reel_id: str
    page_ids: tuple[str, ...]


def page_checksum(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def initialize_device(db: sqlite3.Connection) -> None:
    with db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''
            CREATE TABLE IF NOT EXISTS device_reels (
                device_id TEXT NOT NULL,
                reel_id TEXT NOT NULL,
                PRIMARY KEY (device_id, reel_id)
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS device_pages (
                ticket INTEGER PRIMARY KEY,
                device_id TEXT NOT NULL,
                reel_id TEXT NOT NULL,
                page_id TEXT NOT NULL,
                position INTEGER NOT NULL CHECK (position >= 0),
                checksum TEXT NOT NULL,
                payload BLOB,
                UNIQUE (device_id, reel_id, page_id),
                UNIQUE (device_id, reel_id, position),
                FOREIGN KEY (device_id, reel_id) REFERENCES device_reels (device_id, reel_id)
            )
        ''')
        db.execute('''
            CREATE INDEX IF NOT EXISTS device_pending_pages
            ON device_pages (device_id, ticket) WHERE payload IS NOT NULL
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS device_release_events (
                ticket INTEGER PRIMARY KEY REFERENCES device_pages (ticket),
                phase TEXT NOT NULL CHECK (phase IN ('before', 'after'))
            )
        ''')


class DevicePort:
    """Local reader with one current physical page per device and an independent ledger."""

    def __init__(self, path: str | Path):
        self._db = sqlite3.connect(path)
        try:
            self._db.execute('PRAGMA foreign_keys = ON')
            self.initialize()
        except BaseException:
            self._db.close()
            raise

    def initialize(self) -> None:
        initialize_device(self._db)

    def close(self) -> None:
        self._db.close()

    def load_reel(self, device_id: str, reel_id: str,
                  pages: Iterable[tuple[str, bytes]]) -> DeviceManifest:
        material = tuple(pages)
        page_ids = tuple(page_id for page_id, _ in material)
        for identifier in (device_id, reel_id, *page_ids):
            if not isinstance(identifier, str) or not identifier.strip():
                raise ValueError('device, reel and page identifiers must be nonempty strings')
        if len(set(page_ids)) != len(page_ids):
            raise ValueError('physical page identifiers must be distinct within a reel')
        if any(not isinstance(payload, bytes) for _, payload in material):
            raise ValueError('device payloads must be bytes')

        with self._db:
            self._db.execute('BEGIN IMMEDIATE')
            if self._db.execute('''
                SELECT 1 FROM device_reels WHERE device_id = ? AND reel_id = ?
            ''', (device_id, reel_id)).fetchone() is not None:
                raise ValueError(f'reel {reel_id!r} is already loaded on {device_id!r}')
            self._db.execute('INSERT INTO device_reels VALUES (?, ?)', (device_id, reel_id))
            self._db.executemany('''
                INSERT INTO device_pages (device_id, reel_id, page_id, position, checksum, payload)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', ((device_id, reel_id, page_id, position, page_checksum(payload), payload)
                  for position, (page_id, payload) in enumerate(material)))
        return DeviceManifest(device_id, reel_id, page_ids)

    def reel_manifest(self, device_id: str, reel_id: str) -> DeviceManifest | None:
        with self._db:
            self._db.execute('BEGIN')
            known = self._db.execute('''
                SELECT 1 FROM device_reels WHERE device_id = ? AND reel_id = ?
            ''', (device_id, reel_id)).fetchone()
            if known is None:
                return None
            rows = self._db.execute('''
                SELECT page_id FROM device_pages
                WHERE device_id = ? AND reel_id = ? ORDER BY position
            ''', (device_id, reel_id))
            return DeviceManifest(device_id, reel_id, tuple(page_id for page_id, in rows))

    def _current(self, device_id: str) -> DevicePage | None:
        row = self._db.execute('''
            SELECT device_id, reel_id, page_id, ticket, checksum, payload
            FROM device_pages WHERE device_id = ? AND payload IS NOT NULL
            ORDER BY ticket LIMIT 1
        ''', (device_id,)).fetchone()
        return None if row is None else DevicePage(*row)

    def current_page(self, device_id: str, reel_id: str | None = None,
                     page_id: str | None = None) -> DevicePage | None:
        page = self._current(device_id)
        if reel_id is None and page_id is None:
            return page
        if (page is None or (reel_id is not None and page.reel_id != reel_id)
                or (page_id is not None and page.page_id != page_id)):
            raise DevicePageUnavailable(f'{device_id!r} cannot deliver {reel_id!r}/{page_id!r}')
        return page

    def _find_ticket(self, device_id: str, reel_id: str, page_id: str):
        return self._db.execute('''
            SELECT ticket, payload IS NULL FROM device_pages
            WHERE device_id = ? AND reel_id = ? AND page_id = ?
        ''', (device_id, reel_id, page_id)).fetchone()

    def schedule_release_loss(self, device_id: str, reel_id: str,
                              page_id: str, *, phase: str = 'after') -> None:
        if phase not in ('before', 'after'):
            raise ValueError('release loss must be before or after exit')
        with self._db:
            self._db.execute('BEGIN IMMEDIATE')
            row = self._find_ticket(device_id, reel_id, page_id)
            if row is None or row[1]:
                raise DevicePageUnavailable('a release event requires an unreturned page')
            self._db.execute('INSERT INTO device_release_events VALUES (?, ?)', (row[0], phase))

    def release(self, device_id: str, reel_id: str, page_id: str, ticket: int) -> ReleaseOutcome:
        with self._db:
            self._db.execute('BEGIN IMMEDIATE')
            row = self._find_ticket(device_id, reel_id, page_id)
            if row is None or row[0] != ticket:
                raise DevicePageUnavailable('release does not identify the original ticket')
            if row[1]:
                return ReleaseOutcome.RELEASED
            current = self._current(device_id)
            if current is None or current.ticket != ticket:
                raise DevicePageUnavailable('only the current page can be released')
            event = self._db.execute('''
                SELECT phase FROM device_release_events WHERE ticket = ?
            ''', (ticket,)).fetchone()
            phase = event[0] if event is not None else None
            if event is not None:
                self._db.execute('DELETE FROM device_release_events WHERE ticket = ?', (ticket,))
            if phase != 'before':
                self._db.execute('UPDATE device_pages SET payload = NULL WHERE ticket = ?', (ticket,))
        if phase is not None:
            raise ReleaseReplyLost(f'release response lost for {reel_id!r}/{page_id!r}')
        return ReleaseOutcome.RELEASED

    def release_status(self, device_id: str, reel_id: str, page_id: str) -> ReleaseOutcome:
        with self._db:
            self._db.execute('BEGIN')
            row = self._find_ticket(device_id, reel_id, page_id)
            if row is None:
                return ReleaseOutcome.UNKNOWN
            if row[1]:
                return ReleaseOutcome.RELEASED
            current = self._current(device_id)
            if current is not None and current.ticket == row[0]:
                return ReleaseOutcome.CURRENT
            return ReleaseOutcome.UNKNOWN
