from __future__ import annotations

from typing import TYPE_CHECKING

from reading.records import ReadingRecord
from review.ranges import normalize_window, overlaps_record
from review.report import get_report

if TYPE_CHECKING:
    from registry.session import ExchangeSession


def audit_range(session: ExchangeSession, reel_id: str, start: int,
                end: int) -> tuple[ReadingRecord, ...]:
    snapshot = get_report(session, reel_id).snapshot
    window = normalize_window(start, end, snapshot.byte_count)
    return tuple(row for row in snapshot.records if overlaps_record(window, row.start, row.end))
