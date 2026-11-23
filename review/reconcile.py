from __future__ import annotations

from typing import TYPE_CHECKING

from archive.receipts import FirstReceipt, PublishedReport
from reading.framing import Dialect, InvalidReading, iter_frames
from reading.records import ReadingRecord
from reading.samples import sum_record_samples
from registry.identity import InvalidMaterial
from review.ranges import normalize_window, overlaps_record
from review.report import get_report

if TYPE_CHECKING:
    from registry.session import ExchangeSession


def audit_range(session: ExchangeSession, reel_id: str, start: int,
                end: int) -> tuple[ReadingRecord, ...]:
    snapshot = get_report(session, reel_id).snapshot
    window = normalize_window(start, end, snapshot.byte_count)
    return tuple(row for row in snapshot.records if overlaps_record(window, row.start, row.end))


def _require_report(report) -> PublishedReport:
    if not isinstance(report, PublishedReport):
        raise InvalidMaterial('a published report is required')
    snapshot = report.snapshot
    if not isinstance(snapshot.reel_id, str) or not isinstance(snapshot.dialect, Dialect):
        raise InvalidMaterial('published report has an invalid shape')
    if type(snapshot.byte_count) is not int or type(snapshot.total) is not int:
        raise InvalidMaterial('published report has an invalid shape')
    if not isinstance(snapshot.records, tuple) or not isinstance(report.receipt, FirstReceipt):
        raise InvalidMaterial('published report has an invalid shape')
    if not all(isinstance(record, ReadingRecord)
               and type(record.start) is int and type(record.end) is int
               and type(record.value) is int and isinstance(record.sources, tuple)
               for record in snapshot.records):
        raise InvalidMaterial('published report carries an invalid row')
    if not isinstance(report.receipt.receipt_id, int) or not isinstance(report.receipt.reel_id, str):
        raise InvalidMaterial('published report has an invalid first receipt')
    if report.receipt.reel_id != snapshot.reel_id:
        raise InvalidMaterial('first receipt is bound to a different reel than the report')
    return report


def _frame_rows(payload: bytes, dialect: Dialect,
                report: PublishedReport) -> tuple[tuple[tuple[int, int], ReadingRecord], ...]:
    try:
        frames = tuple(iter_frames(payload, dialect))
    except InvalidReading as error:
        raise InvalidMaterial(f'received payload does not parse as {dialect.value}: {error}') from error
    records = report.snapshot.records
    if len(frames) != len(records):
        raise InvalidMaterial('received row count disagrees with the publication')
    rows = []
    for frame, record in zip(frames, records):
        if sum_record_samples(frame, dialect) != record.value:
            raise InvalidMaterial('received row value disagrees with the publication')
        rows.append(((frame.start, frame.end), record))
    return tuple(rows)


def reconcile_shared(package, single_report, double_report):
    """Night shift acceptance for a payload shared by both old dialects.

    Only the received package and the two independently obtained publications
    are consulted.  Both full ordered readings must match the publications, and
    the package's first receipts must be the original ones.
    """
    if not isinstance(package, dict) or set(package) != {'receipts', 'payload'}:
        raise InvalidMaterial('package must be a dict with receipts and payload')
    payload = package['payload']
    receipts = package['receipts']
    if type(payload) is not bytes:
        raise InvalidMaterial('package payload must be bytes')
    if not isinstance(receipts, tuple) or len(receipts) != 2:
        raise InvalidMaterial('package must carry two first receipts')
    single_receipt, double_receipt = receipts
    if not isinstance(single_receipt, FirstReceipt) or not isinstance(double_receipt, FirstReceipt):
        raise InvalidMaterial('package receipts must be first receipts')
    if single_receipt == double_receipt:
        raise InvalidMaterial('the two received receipts are not independent')

    single_report = _require_report(single_report)
    double_report = _require_report(double_report)
    if single_report.snapshot.dialect is not Dialect.SINGLE:
        raise InvalidMaterial('single report is not signed in the single dialect')
    if double_report.snapshot.dialect is not Dialect.DOUBLE:
        raise InvalidMaterial('double report is not signed in the double dialect')
    if single_report.snapshot.reel_id == double_report.snapshot.reel_id:
        raise InvalidMaterial('the two reports do not come from two independent reels')
    if single_report.receipt != single_receipt:
        raise InvalidMaterial('single receipt does not belong to the single publication')
    if double_report.receipt != double_receipt:
        raise InvalidMaterial('double receipt does not belong to the double publication')

    single_rows = _frame_rows(payload, Dialect.SINGLE, single_report)
    double_rows = _frame_rows(payload, Dialect.DOUBLE, double_report)
    return single_rows, double_rows
