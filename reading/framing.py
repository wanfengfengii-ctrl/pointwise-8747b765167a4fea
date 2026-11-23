from dataclasses import dataclass
from enum import Enum
from typing import Iterator


class Dialect(Enum):
    SINGLE = 'single'
    DOUBLE = 'double'


class InvalidReading(ValueError):
    def __init__(self, offset: int, reason: str):
        self.offset = offset
        self.reason = reason
        super().__init__(f'{reason} at reel byte {offset}')


@dataclass(frozen=True)
class RecordFrame:
    start: int
    end: int
    sample_count: int
    sample_bytes: bytes


def sample_width(dialect: Dialect) -> int:
    if dialect is Dialect.SINGLE:
        return 1
    if dialect is Dialect.DOUBLE:
        return 2
    raise ValueError('unsupported reading dialect')


def iter_frames(payload: bytes, dialect: Dialect) -> Iterator[RecordFrame]:
    width = sample_width(dialect)
    start = 0
    while start < len(payload):
        marker = payload[start]
        if marker not in (1, 2):
            raise InvalidReading(start, f'invalid record marker 0x{marker:02x}')
        samples_start = start + 1
        end = samples_start + marker * width
        if end > len(payload):
            raise InvalidReading(start, 'incomplete final record')
        yield RecordFrame(start, end, marker, payload[samples_start:end])
        start = end
