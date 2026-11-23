from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from registry.identity import (
    Conflict, InvalidMaterial, NotFound, RegisteredReel, RegistrationMode,
    matches_registration, require_identifier,
)

if TYPE_CHECKING:
    from registry.session import ExchangeSession


def initialize_registry(db: sqlite3.Connection) -> None:
    db.execute('''
        CREATE TABLE IF NOT EXISTS registered_reels (
            registration_order INTEGER PRIMARY KEY,
            reel_id TEXT NOT NULL UNIQUE,
            device_id TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            mode TEXT NOT NULL CHECK (mode IN ('fixed-single', 'fixed-double', 'tail'))
        )
    ''')
    db.execute('''
        CREATE INDEX IF NOT EXISTS registered_reels_batch
        ON registered_reels (batch_id, registration_order)
    ''')


def find_registered_reel(db: sqlite3.Connection, reel_id: str) -> RegisteredReel | None:
    row = db.execute('''
        SELECT reel_id, device_id, batch_id, mode FROM registered_reels WHERE reel_id = ?
    ''', (reel_id,)).fetchone()
    if row is None:
        return None
    return RegisteredReel(row[0], row[1], row[2], RegistrationMode(row[3]))


def require_registered_reel(db: sqlite3.Connection, reel_id: str) -> RegisteredReel:
    require_identifier(reel_id, 'reel_id')
    reel = find_registered_reel(db, reel_id)
    if reel is None:
        raise NotFound(f'unknown reel {reel_id!r}')
    return reel


def register(session: ExchangeSession, reel_id: str, device_id: str,
             mode: RegistrationMode) -> RegisteredReel:
    require_identifier(reel_id, 'reel_id')
    require_identifier(device_id, 'device_id')
    if not isinstance(mode, RegistrationMode):
        raise InvalidMaterial('invalid registration mode')

    db = session.ledger
    with db:
        db.execute('BEGIN IMMEDIATE')
        existing = find_registered_reel(db, reel_id)
        if existing is not None:
            if not matches_registration(existing, device_id, mode):
                raise Conflict(f'reel {reel_id!r} already has a different registration')
            return existing
        db.execute('''
            INSERT INTO registered_reels (reel_id, device_id, batch_id, mode)
            VALUES (?, ?, ?, ?)
        ''', (reel_id, device_id, session.batch_id, mode.value))
        return RegisteredReel(reel_id, device_id, session.batch_id, mode)
