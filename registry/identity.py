from dataclasses import dataclass
from enum import Enum

from reading.framing import Dialect


class ExchangeError(Exception):
    """A business rejection in the exchange workflow."""


class NotFound(ExchangeError):
    pass


class PendingReel(ExchangeError):
    pass


class Conflict(ExchangeError):
    pass


class InvalidMaterial(ExchangeError):
    pass


class RegistrationMode(Enum):
    FIXED_SINGLE = 'fixed-single'
    FIXED_DOUBLE = 'fixed-double'
    TAIL = 'tail'

    @property
    def fixed_dialect(self) -> Dialect | None:
        if self is RegistrationMode.FIXED_SINGLE:
            return Dialect.SINGLE
        if self is RegistrationMode.FIXED_DOUBLE:
            return Dialect.DOUBLE
        return None


@dataclass(frozen=True)
class RegisteredReel:
    reel_id: str
    device_id: str
    batch_id: str
    mode: RegistrationMode


def require_identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidMaterial(f'{label} must be a nonempty string')


def matches_registration(reel: RegisteredReel, device_id: str, mode: RegistrationMode) -> bool:
    return reel.device_id == device_id and reel.mode is mode
