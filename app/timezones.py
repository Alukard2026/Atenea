"""Zonas IANA validadas. Nunca sustituir silenciosamente una zona inválida."""

from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones


DEFAULT_TIMEZONE = "America/El_Salvador"


@lru_cache(maxsize=1)
def supported_timezones():
    return frozenset(available_timezones())


def valid_timezone(value):
    if not isinstance(value, str) or value not in supported_timezones():
        raise ValueError("Selecciona una zona horaria IANA válida.")
    ZoneInfo(value)
    return value


def organization_zone(organization, fallback=None):
    value = organization.timezone
    if not value:
        value = fallback.key if fallback is not None else DEFAULT_TIMEZONE
    return ZoneInfo(valid_timezone(value))
