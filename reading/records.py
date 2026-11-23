from dataclasses import dataclass
from typing import Sequence

from reading.framing import Dialect, iter_frames
from reading.origin import PageSpan, SourceSlice, trace_origin
from reading.samples import sum_record_samples


@dataclass(frozen=True)
class ReadingRecord:
    start: int
    end: int
    value: int
    sources: tuple[SourceSlice, ...]


def decode_records(payload: bytes, dialect: Dialect,
                   pages: Sequence[PageSpan]) -> tuple[ReadingRecord, ...]:
    end = 0
    for page in pages:
        if page.start != end or page.end < page.start:
            raise ValueError('page spans must be continuous in payload order')
        end = page.end
    if end != len(payload):
        raise ValueError('page spans do not cover the supplied payload')

    records = []
    for frame in iter_frames(payload, dialect):
        sources = trace_origin(frame.start, frame.end, pages)
        value = sum_record_samples(frame, dialect)
        records.append(ReadingRecord(frame.start, frame.end, value, sources))
    return tuple(records)
