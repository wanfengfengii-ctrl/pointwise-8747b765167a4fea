from dataclasses import dataclass
import sqlite3

from reading.framing import Dialect
from reading.origin import SourceSlice
from reading.records import ReadingRecord


@dataclass(frozen=True)
class ArchiveSnapshot:
    reel_id: str
    dialect: Dialect
    byte_count: int
    records: tuple[ReadingRecord, ...]
    total: int
    authorization_key: str


def initialize_archive(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS archive_reports (
            reel_id TEXT PRIMARY KEY REFERENCES registered_reels (reel_id),
            dialect TEXT NOT NULL CHECK (dialect IN ('single', 'double')),
            byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
            total INTEGER NOT NULL CHECK (total >= 0),
            authorization_key TEXT NOT NULL
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS archive_rows (
            reel_id TEXT NOT NULL REFERENCES archive_reports (reel_id),
            row_number INTEGER NOT NULL,
            byte_start INTEGER NOT NULL,
            byte_end INTEGER NOT NULL CHECK (byte_end > byte_start),
            reading INTEGER NOT NULL CHECK (reading >= 0),
            PRIMARY KEY (reel_id, row_number)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS archive_origins (
            reel_id TEXT NOT NULL,
            row_number INTEGER NOT NULL,
            piece_number INTEGER NOT NULL,
            page_id TEXT NOT NULL,
            page_start INTEGER NOT NULL,
            page_end INTEGER NOT NULL CHECK (page_end > page_start),
            PRIMARY KEY (reel_id, row_number, piece_number),
            FOREIGN KEY (reel_id, row_number) REFERENCES archive_rows (reel_id, row_number)
        )
    ''')


def save_snapshot(db: sqlite3.Connection, snapshot: ArchiveSnapshot) -> None:
    if not db.in_transaction:
        raise RuntimeError('snapshot writes require a publication transaction')
    reel_id = snapshot.reel_id
    db.execute('INSERT INTO archive_reports VALUES (?, ?, ?, ?, ?)', (
        reel_id, snapshot.dialect.value, snapshot.byte_count, snapshot.total,
        snapshot.authorization_key,
    ))
    db.executemany('INSERT INTO archive_rows VALUES (?, ?, ?, ?, ?)', (
        (reel_id, number, row.start, row.end, row.value)
        for number, row in enumerate(snapshot.records)
    ))
    db.executemany('INSERT INTO archive_origins VALUES (?, ?, ?, ?, ?, ?)', (
        (reel_id, number, piece_number, piece.page_id, piece.start, piece.end)
        for number, row in enumerate(snapshot.records)
        for piece_number, piece in enumerate(row.sources)
    ))


def load_snapshot(db: sqlite3.Connection, reel_id: str) -> ArchiveSnapshot | None:
    head = db.execute('''
        SELECT dialect, byte_count, total, authorization_key
        FROM archive_reports WHERE reel_id = ?
    ''', (reel_id,)).fetchone()
    if head is None:
        return None
    sources_by_row: dict[int, list[SourceSlice]] = {}
    for row_number, page_id, start, end in db.execute('''
        SELECT row_number, page_id, page_start, page_end FROM archive_origins
        WHERE reel_id = ? ORDER BY row_number, piece_number
    ''', (reel_id,)):
        sources_by_row.setdefault(row_number, []).append(SourceSlice(page_id, start, end))
    rows = db.execute('''
        SELECT row_number, byte_start, byte_end, reading FROM archive_rows
        WHERE reel_id = ? ORDER BY row_number
    ''', (reel_id,))
    records = tuple(ReadingRecord(start, end, value, tuple(sources_by_row.get(number, ())))
                    for number, start, end, value in rows)
    dialect, byte_count, total, key = head
    return ArchiveSnapshot(reel_id, Dialect(dialect), byte_count, records, total, key)


def list_published(db: sqlite3.Connection, batch_id: str) -> frozenset[str]:
    rows = db.execute('''
        SELECT reports.reel_id FROM registered_reels AS reels
        JOIN archive_reports AS reports ON reports.reel_id = reels.reel_id
        WHERE reels.batch_id = ?
    ''', (batch_id,))
    return frozenset(reel_id for reel_id, in rows)
