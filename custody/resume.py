from __future__ import annotations

from typing import TYPE_CHECKING

from custody.device import ReleaseOutcome
from custody.evidence import load_page_evidence
from custody.release import confirm_release, release_received_page
from registry.identity import NotFound, require_identifier

if TYPE_CHECKING:
    from registry.session import ExchangeSession


def resume_page(session: ExchangeSession, reel_id: str, page_id: str) -> ReleaseOutcome:
    require_identifier(reel_id, 'reel_id')
    require_identifier(page_id, 'page_id')
    db = session.ledger
    page = load_page_evidence(db, reel_id, page_id)
    if page is None:
        raise NotFound(f'no intake recorded for {reel_id!r}/{page_id!r}')
    if page.outcome is ReleaseOutcome.RELEASED:
        return page.outcome
    device = session.device
    if device is None:
        return confirm_release(db, page, ReleaseOutcome.UNKNOWN)
    outcome = device.release_status(page.device_id, page.reel_id, page.page_id)
    if outcome is ReleaseOutcome.CURRENT:
        return release_received_page(db, device, page)
    return confirm_release(db, page, outcome)
