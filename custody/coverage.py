from dataclasses import dataclass
import sqlite3

from custody.device import DeviceManifest, ReleaseOutcome, page_checksum
from custody.evidence import PageEvidence, list_page_evidence, load_manifest
from registry.identity import InvalidMaterial, PendingReel, RegisteredReel


@dataclass(frozen=True)
class PageCoverage:
    manifest: DeviceManifest
    pages: tuple[PageEvidence, ...]
    byte_count: int


def _confirmed_prefix_end(manifest: DeviceManifest, pages: tuple[PageEvidence, ...]) -> int:
    if len(pages) > len(manifest.page_ids):
        raise InvalidMaterial('received pages exceed the device manifest')
    end = 0
    for position, page in enumerate(pages):
        if (page.reel_id != manifest.reel_id or page.device_id != manifest.device_id
                or page.page_id != manifest.page_ids[position] or page.position != position
                or page.start != end or page.length < 0):
            raise InvalidMaterial('received pages disagree with the original ordered coverage')
        if page.outcome is not ReleaseOutcome.RELEASED:
            raise PendingReel(f'exit still unconfirmed for page {page.page_id!r}')
        end += page.length
    return end


def next_page_position(db: sqlite3.Connection, manifest: DeviceManifest,
                       page_id: str) -> tuple[int, int]:
    pages = list_page_evidence(db, manifest.reel_id)
    position = len(pages)
    if position >= len(manifest.page_ids) or manifest.page_ids[position] != page_id:
        raise InvalidMaterial(f'page {page_id!r} is outside the next intake position')
    return position, _confirmed_prefix_end(manifest, pages)


def collect_coverage(db: sqlite3.Connection, reel: RegisteredReel) -> PageCoverage:
    manifest = load_manifest(db, reel.reel_id)
    if manifest is None:
        raise PendingReel(f'complete page manifest unavailable for {reel.reel_id!r}')
    if manifest.device_id != reel.device_id:
        raise InvalidMaterial('device manifest does not match registration')
    pages = list_page_evidence(db, reel.reel_id)
    end = _confirmed_prefix_end(manifest, pages)
    if len(pages) != len(manifest.page_ids):
        raise PendingReel(f'reel {reel.reel_id!r} has missing pages')
    return PageCoverage(manifest, pages, end)


def replay_coverage(coverage: PageCoverage) -> bytes:
    parts = []
    for page in coverage.pages:
        payload = page.payload
        if payload is None:
            raise PendingReel(f'original payload unavailable for page {page.page_id!r}')
        if len(payload) != page.length or page_checksum(payload) != page.checksum:
            raise InvalidMaterial(f'preserved payload does not match page {page.page_id!r}')
        parts.append(payload)
    return b''.join(parts)
