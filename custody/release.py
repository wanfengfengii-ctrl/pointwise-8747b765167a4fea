import sqlite3

from custody.device import DevicePort, ReleaseOutcome, ReleaseReplyLost
from custody.evidence import PageEvidence, load_page_evidence, record_release_status
from registry.identity import NotFound


def confirm_release(db: sqlite3.Connection, page: PageEvidence,
                    outcome: ReleaseOutcome) -> ReleaseOutcome:
    with db:
        db.execute('BEGIN IMMEDIATE')
        return record_release_status(db, page, outcome)


def release_received_page(db: sqlite3.Connection, device: DevicePort,
                          page: PageEvidence) -> ReleaseOutcome:
    if db.in_transaction:
        raise RuntimeError('intake must commit before physical release')
    stored = load_page_evidence(db, page.reel_id, page.page_id)
    if stored is None or (stored.device_id, stored.ticket) != (page.device_id, page.ticket):
        raise NotFound('physical release requires a committed original intake')
    if stored.outcome is ReleaseOutcome.RELEASED:
        return stored.outcome
    try:
        outcome = device.release(stored.device_id, stored.reel_id, stored.page_id, stored.ticket)
    except ReleaseReplyLost:
        outcome = ReleaseOutcome.UNKNOWN
    return confirm_release(db, stored, outcome)
