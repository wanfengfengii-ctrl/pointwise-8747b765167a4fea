from dataclasses import dataclass

from registry.identity import InvalidMaterial


@dataclass(frozen=True)
class AuditWindow:
    start: int
    end: int


def normalize_window(start: int, end: int, byte_count: int) -> AuditWindow:
    if type(start) is not int or type(end) is not int or not 0 <= start <= end <= byte_count:
        raise InvalidMaterial(f'audit window must have integer bounds within [0, {byte_count}]')
    return AuditWindow(start, end)


def overlaps_record(window: AuditWindow, start: int, end: int) -> bool:
    return max(window.start, start) < min(window.end, end)
