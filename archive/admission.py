from dataclasses import dataclass
import json
import sqlite3

from archive.seal import TailSeal, validate_tail_seal
from custody.coverage import collect_coverage, replay_coverage
from reading.framing import Dialect
from reading.origin import PageSpan
from reading.records import ReadingRecord, decode_records
from reading.totals import sum_readings
from registry.identity import InvalidMaterial, PendingReel, RegisteredReel, RegistrationMode


@dataclass(frozen=True)
class AdmittedReel:
    reel_id: str
    dialect: Dialect
    byte_count: int
    records: tuple[ReadingRecord, ...]
    total: int
    authorization_key: str


def authorization_key(reel: RegisteredReel, authorization: Dialect | TailSeal | None) -> str:
    if reel.mode is RegistrationMode.TAIL:
        if authorization is None:
            raise PendingReel(f'waiting for tail seal for {reel.reel_id!r}')
        if not isinstance(authorization, TailSeal) or authorization.reel_id != reel.reel_id:
            raise InvalidMaterial('a tail seal for the registered reel is required')
        material = ['tail', authorization.reel_id, authorization.page_ids, authorization.dialect.value]
    else:
        if not isinstance(authorization, Dialect) or authorization is not reel.mode.fixed_dialect:
            raise InvalidMaterial('fixed marker must match the registered dialect')
        material = ['fixed', reel.reel_id, authorization.value]
    return json.dumps(material, separators=(',', ':'))


def evaluate_admission(db: sqlite3.Connection, reel: RegisteredReel,
                       authorization: Dialect | TailSeal | None) -> AdmittedReel:
    key = authorization_key(reel, authorization)
    coverage = collect_coverage(db, reel)
    if isinstance(authorization, TailSeal):
        dialect = validate_tail_seal(authorization, coverage)
    else:
        dialect = reel.mode.fixed_dialect
    payload = replay_coverage(coverage)
    spans = tuple(PageSpan(page.page_id, page.start, page.start + page.length)
                  for page in coverage.pages)
    records = decode_records(payload, dialect, spans)
    return AdmittedReel(reel.reel_id, dialect, coverage.byte_count, records,
                        sum_readings(records), key)
