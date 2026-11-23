from dataclasses import dataclass
import sqlite3

from archive.store import ArchiveSnapshot


@dataclass(frozen=True)
class FirstReceipt:
    receipt_id: int
    reel_id: str


@dataclass(frozen=True)
class PublishedReport:
    snapshot: ArchiveSnapshot
    receipt: FirstReceipt


def initialize_receipts(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS archive_receipts (
            receipt_id INTEGER PRIMARY KEY,
            reel_id TEXT NOT NULL UNIQUE REFERENCES archive_reports (reel_id)
        )
    ''')


def issue_first_receipt(db: sqlite3.Connection, reel_id: str) -> FirstReceipt:
    if not db.in_transaction:
        raise RuntimeError('receipt writes require a publication transaction')
    cursor = db.execute('INSERT INTO archive_receipts (reel_id) VALUES (?)', (reel_id,))
    return FirstReceipt(cursor.lastrowid, reel_id)


def read_first_receipt(db: sqlite3.Connection, reel_id: str) -> FirstReceipt | None:
    row = db.execute('''
        SELECT receipt_id, reel_id FROM archive_receipts WHERE reel_id = ?
    ''', (reel_id,)).fetchone()
    return None if row is None else FirstReceipt(*row)


def restore_publication(db: sqlite3.Connection, snapshot: ArchiveSnapshot) -> PublishedReport:
    receipt = read_first_receipt(db, snapshot.reel_id)
    if receipt is None:
        raise RuntimeError(f'committed report {snapshot.reel_id!r} is missing its first receipt')
    return PublishedReport(snapshot, receipt)
