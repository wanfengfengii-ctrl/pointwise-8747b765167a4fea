from __future__ import annotations

from typing import TYPE_CHECKING

from custody.coverage import next_page_position
from custody.device import DevicePage, DevicePageUnavailable, ReleaseOutcome, page_checksum
from custody.evidence import PageEvidence, load_page_evidence, save_manifest, save_page_evidence
from custody.release import release_received_page
from custody.resume import resume_page
from registry.identity import Conflict, InvalidMaterial, PendingReel, RegisteredReel, require_identifier
from registry.intake import require_registered_reel

if TYPE_CHECKING:
    from registry.session import ExchangeSession


def validate_page_material(reel: RegisteredReel, page_id: str, page: DevicePage) -> None:
    require_identifier(page_id, 'page_id')
    if not isinstance(page, DevicePage):
        raise InvalidMaterial('intake requires a physical device page ticket')
    if (page.reel_id, page.device_id, page.page_id) != (reel.reel_id, reel.device_id, page_id):
        raise InvalidMaterial('physical page does not belong to the registered reel and position')
    if type(page.ticket) is not int or page.ticket <= 0:
        raise InvalidMaterial('invalid physical page ticket')
    if not isinstance(page.payload, bytes) or page_checksum(page.payload) != page.checksum:
        raise InvalidMaterial(f'payload checksum failed for page {page_id!r}')


def receive_page(session: ExchangeSession, reel_id: str, page_id: str,
                 page: DevicePage) -> ReleaseOutcome:
    db = session.ledger
    device = session.device
    with db:
        db.execute('BEGIN IMMEDIATE')
        reel = require_registered_reel(db, reel_id)
        validate_page_material(reel, page_id, page)
        previous = load_page_evidence(db, reel_id, page_id)
        if previous is not None:
            if (previous.ticket, previous.checksum, previous.payload) != (
                page.ticket, page.checksum, page.payload
            ):
                raise Conflict(f'page {page_id!r} already has different intake material')
        else:
            if device is None:
                raise PendingReel('current physical delivery cannot be checked offline')
            manifest = device.reel_manifest(reel.device_id, reel_id)
            if manifest is None:
                raise InvalidMaterial('no device manifest for the registered reel')
            try:
                current = device.current_page(reel.device_id, reel_id, page_id)
            except DevicePageUnavailable as error:
                raise InvalidMaterial('ticket does not identify the current physical page') from error
            if current != page:
                raise InvalidMaterial('submitted material differs from the current physical ticket')
            save_manifest(db, manifest)
            position, start = next_page_position(db, manifest, page_id)
            new_page = PageEvidence(reel_id, reel.device_id, page_id, position, start,
                                    len(page.payload), page.checksum, page.ticket, page.payload,
                                    ReleaseOutcome.CURRENT)
            save_page_evidence(db, new_page)

    if previous is not None:
        return resume_page(session, reel_id, page_id)
    return release_received_page(db, device, new_page)
