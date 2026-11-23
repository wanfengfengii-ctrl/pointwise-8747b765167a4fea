from typing import Iterable

from reading.records import ReadingRecord


def sum_readings(records: Iterable[ReadingRecord]) -> int:
    return sum(record.value for record in records)
