from dataclasses import dataclass

from custody.coverage import PageCoverage
from reading.framing import Dialect
from registry.identity import InvalidMaterial, require_identifier


@dataclass(frozen=True)
class TailSeal:
    reel_id: str
    page_ids: tuple[str, ...]
    dialect: Dialect

    def __post_init__(self):
        require_identifier(self.reel_id, 'tail seal reel_id')
        if not isinstance(self.page_ids, tuple):
            raise InvalidMaterial('tail seal page order must be a tuple')
        for page_id in self.page_ids:
            require_identifier(page_id, 'tail seal page_id')
        if len(set(self.page_ids)) != len(self.page_ids):
            raise InvalidMaterial('tail seal contains duplicate pages')
        if not isinstance(self.dialect, Dialect):
            raise InvalidMaterial('invalid tail seal dialect')


def validate_tail_seal(seal: TailSeal, coverage: PageCoverage) -> Dialect:
    if seal.reel_id != coverage.manifest.reel_id:
        raise InvalidMaterial('tail seal belongs to a different reel')
    if seal.page_ids != coverage.manifest.page_ids:
        raise InvalidMaterial('tail seal does not match the complete physical page order')
    return seal.dialect
