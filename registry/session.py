from dataclasses import dataclass
from pathlib import Path
import sqlite3

from archive.receipts import initialize_receipts
from archive.store import initialize_archive
from custody.device import DevicePort
from custody.evidence import initialize_custody
from registry.identity import InvalidMaterial, require_identifier
from registry.intake import initialize_registry


@dataclass
class ExchangeSession:
    ledger: sqlite3.Connection
    batch_id: str
    device: DevicePort | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def close(self) -> None:
        try:
            if self.device is not None:
                self.device.close()
        finally:
            self.ledger.close()


def initialize(session: ExchangeSession) -> None:
    db = session.ledger
    with db:
        db.execute('BEGIN IMMEDIATE')
        initialize_registry(db)
        initialize_custody(db)
        initialize_archive(db)
        initialize_receipts(db)
    if session.device is not None:
        session.device.initialize()


def open_session(ledger_path: str | Path, batch_id: str, *,
                 device_path: str | Path | None = None) -> ExchangeSession:
    require_identifier(batch_id, 'batch_id')
    if device_path is not None:
        primary = Path(ledger_path)
        physical = Path(device_path)
        if primary.resolve() == physical.resolve() or (
            primary.exists() and physical.exists() and primary.samefile(physical)
        ):
            raise InvalidMaterial('device and exchange ledgers require separate files')

    db = sqlite3.connect(ledger_path)
    session = ExchangeSession(db, batch_id)
    try:
        db.execute('PRAGMA foreign_keys = ON')
        initialize(session)
        if device_path is not None:
            session.device = DevicePort(device_path)
    except BaseException:
        session.close()
        raise
    return session
