from __future__ import annotations

from typing import TYPE_CHECKING

from archive.admission import authorization_key, evaluate_admission
from archive.receipts import PublishedReport, issue_first_receipt, restore_publication
from archive.seal import TailSeal
from archive.store import ArchiveSnapshot, load_snapshot, save_snapshot
from custody.evidence import load_manifest, save_manifest
from reading.framing import Dialect, InvalidReading, iter_frames
from reading.samples import sum_record_samples
from registry.identity import (
    Conflict, InvalidMaterial, NotFound, PendingReel, require_identifier,
)
from registry.intake import find_registered_reel, require_registered_reel

if TYPE_CHECKING:
    from registry.session import ExchangeSession


def _signed_report(db, reel_id: str) -> PublishedReport:
    """Load an already-signed publication without touching the device side."""
    require_identifier(reel_id, 'reel_id')
    snapshot = load_snapshot(db, reel_id)
    if snapshot is None:
        if find_registered_reel(db, reel_id) is None:
            raise NotFound(f'unknown reel {reel_id!r}')
        raise PendingReel(f'reel {reel_id!r} has no committed report')
    return restore_publication(db, snapshot)


def _decoded_values(payload: bytes, dialect: Dialect) -> tuple[int, ...]:
    return tuple(sum_record_samples(frame, dialect) for frame in iter_frames(payload, dialect))


def _encode_shared(single_values: tuple[int, ...],
                   double_values: tuple[int, ...]) -> bytes | None:
    """Find one byte stream that both old machines read into the given values.

    A single-dialect frame is one marker byte (1 or 2) plus that many sample
    bytes; a double-dialect frame is one marker byte plus twice that many
    sample bytes.  Both parsers consume the same stream from the first byte to
    the last, so the search walks the stream once while tracking the progress
    of both parsers.  Values are unsigned byte sums, so a marker always costs
    a positive frame value while sample bytes are chosen freely.
    """
    n = len(single_values)
    m = len(double_values)
    if n == 0 and m == 0:
        return b''
    if n == 0 or m == 0:
        # An empty stream yields zero rows for both dialects; a non-empty one
        # yields at least one row for both.  A single empty side has no common
        # encoding.
        return None
    if any(v < 0 or v > 2 * 255 for v in single_values):
        return None
    if any(v < 0 or v > 4 * 255 for v in double_values):
        return None
    if max(2 * n, 3 * m) > min(3 * n, 5 * m):
        return None

    def bounds(state):
        i, sr, ss, j, dr, ds = state
        if i == n and j == m and sr == 0 and dr == 0:
            return None  # all rows consumed by both parsers
        if i >= n or j >= m:
            # One parser has finished but the other still expects a byte, and
            # no stream can satisfy both.  A single empty side is rejected
            # above, so this only means the attempted prefix is a dead end.
            return False
        if sr == 0:
            s_low, s_high = 1, 2
        elif sr == 1:
            if ss > 255:
                return False
            s_low = s_high = ss
        else:
            s_low = max(0, ss - (sr - 1) * 255)
            s_high = min(255, ss)
        if dr == 0:
            d_low, d_high = 1, 2
        elif dr == 1:
            if ds > 255:
                return False
            d_low = d_high = ds
        else:
            d_low = max(0, ds - (dr - 1) * 255)
            d_high = min(255, ds)
        low, high = max(s_low, d_low), min(s_high, d_high)
        if low > high:
            return False
        return low, high

    def advance(state, byte):
        i, sr, ss, j, dr, ds = state
        if sr == 0:
            if byte not in (1, 2):
                return None  # a frame must start with a valid marker
            target = single_values[i]
            if target > byte * 255:
                return None
            next_s = (i, byte, target)
        else:
            remainder = ss - byte
            if sr == 1:
                if remainder != 0:
                    return None
                next_s = (i + 1, 0, 0)
            else:
                next_s = (i, sr - 1, remainder)
        if dr == 0:
            if byte not in (1, 2):
                return None
            target = double_values[j]
            if target > 2 * byte * 255:
                return None
            next_d = (j, 2 * byte, target)
        else:
            remainder = ds - byte
            if dr == 1:
                if remainder != 0:
                    return None
                next_d = (j + 1, 0, 0)
            else:
                next_d = (j, dr - 1, remainder)
        return (next_s[0], next_s[1], next_s[2], next_d[0], next_d[1], next_d[2])

    start = (0, 0, 0, 0, 0, 0)
    span = bounds(start)
    if span is None:
        return b''
    if span is False:
        return None
    # Depth-first search with an explicit stack so long reels cannot exhaust
    # the interpreter recursion limit.  `failed` memoizes dead partial states.
    dead: set = set()
    stack = [[start, span[0], span[1], span[0]]]
    while stack:
        frame = stack[-1]
        state, low, high, byte = frame
        if byte > high:
            dead.add(state)
            stack.pop()
            continue
        frame[3] = byte + 1
        child = advance(state, byte)
        if child is None or child in dead:
            continue
        span = bounds(child)
        if span is None:
            return bytes(entry[3] - 1 for entry in stack)
        if span is False:
            dead.add(child)
            continue
        stack.append([child, span[0], span[1], span[0]])
    return None


def issue_shared(session: ExchangeSession, single_reel: str,
                 double_reel: str) -> dict:
    """Issue one payload consumed directly by both old reader dialects.

    Both reels must already be signed in this ledger; the device side is not
    consulted, so a retired machine does not block issuance.  Selection is
    read-only.  The returned receipts are the original first receipts, single
    side first.
    """
    db = session.ledger
    with db:
        db.execute('BEGIN')
        single_report = _signed_report(db, single_reel)
        double_report = _signed_report(db, double_reel)

    if single_report.snapshot.dialect is not Dialect.SINGLE:
        raise InvalidMaterial(f'reel {single_reel!r} is not a single-dialect publication')
    if double_report.snapshot.dialect is not Dialect.DOUBLE:
        raise InvalidMaterial(f'reel {double_reel!r} is not a double-dialect publication')

    single_values = tuple(row.value for row in single_report.snapshot.records)
    double_values = tuple(row.value for row in double_report.snapshot.records)
    payload = _encode_shared(single_values, double_values)
    if payload is None:
        raise InvalidMaterial('the two published readings have no common encoding')

    try:
        reproduced_single = _decoded_values(payload, Dialect.SINGLE)
        reproduced_double = _decoded_values(payload, Dialect.DOUBLE)
    except InvalidReading as error:  # pragma: no cover - solver output is valid
        raise InvalidMaterial(f'constructed shared payload is invalid: {error}') from error
    if reproduced_single != single_values or reproduced_double != double_values:
        raise InvalidMaterial('constructed shared payload does not reproduce both readings')

    return {'receipts': (single_report.receipt, double_report.receipt),
            'payload': payload}


def finalize(session: ExchangeSession, reel_id: str,
             authorization: Dialect | TailSeal | None = None) -> PublishedReport:
    db = session.ledger
    with db:
        db.execute('BEGIN IMMEDIATE')
        reel = require_registered_reel(db, reel_id)
        existing = load_snapshot(db, reel_id)
        if existing is not None:
            try:
                key = authorization_key(reel, authorization)
            except (InvalidMaterial, PendingReel) as error:
                raise Conflict(f'reel {reel_id!r} already has a different publication') from error
            if key != existing.authorization_key:
                raise Conflict(f'reel {reel_id!r} already has a different publication')
            return restore_publication(db, existing)

        # An empty reel can have a device manifest without an intake call.
        if load_manifest(db, reel_id) is None and session.device is not None:
            manifest = session.device.reel_manifest(reel.device_id, reel_id)
            if manifest is not None:
                save_manifest(db, manifest)
        admitted = evaluate_admission(db, reel, authorization)
        snapshot = ArchiveSnapshot(admitted.reel_id, admitted.dialect, admitted.byte_count,
                                   admitted.records, admitted.total, admitted.authorization_key)
        save_snapshot(db, snapshot)
        receipt = issue_first_receipt(db, reel_id)
        return PublishedReport(snapshot, receipt)
