from bisect import bisect_right
from dataclasses import dataclass
from operator import attrgetter
from typing import Sequence


@dataclass(frozen=True)
class PageSpan:
    page_id: str
    start: int
    end: int


@dataclass(frozen=True)
class SourceSlice:
    page_id: str
    start: int
    end: int


def trace_origin(start: int, end: int, pages: Sequence[PageSpan]) -> tuple[SourceSlice, ...]:
    """Map a reel interval to a contiguous, ordered sequence of physical pages."""
    first = bisect_right(pages, start, key=attrgetter('end'))
    pieces = []
    for index in range(first, len(pages)):
        page = pages[index]
        if page.start >= end:
            break
        left = max(start, page.start)
        right = min(end, page.end)
        if left < right:
            pieces.append(SourceSlice(page.page_id, left - page.start, right - page.start))
    return tuple(pieces)
