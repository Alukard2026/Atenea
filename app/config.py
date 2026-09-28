"""Configuración web leída del .env de la raíz, sin secretos predeterminados."""

from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import dotenv_values


ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


@dataclass(frozen=True)
class Settings:
    session_secret: str = field(repr=False)
    session_cookie_secure: bool = False
    session_max_age: int = 8 * 60 * 60
    app_timezone: str = "America/Guatemala"
    microsoft_client_id: str = field(default="", repr=False)
    microsoft_client_secret: str = field(default="", repr=False)
    microsoft_tenant: str = field(default="", repr=False)
    microsoft_redirect_uri: str = field(default="", repr=False)
    token_encryption_key: str = field(default="", repr=False)

    def __post_init__(self):
        if len(self.session_secret.strip()) < 32:
            raise RuntimeError("Define SESSION_SECRET aleatorio (mínimo 32 caracteres) en .env. Consulta README.md.")
        try:
            ZoneInfo(self.app_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise RuntimeError("APP_TIMEZONE debe ser una zona IANA válida y tzdata debe estar instalado.") from None


def load_settings() -> Settings:
    values = dotenv_values(ENV_PATH, interpolate=False)
    secure = (values.get("SESSION_COOKIE_SECURE") or "false").strip().lower()
    if secure not in {"true", "false"}:
        raise RuntimeError("SESSION_COOKIE_SECURE debe ser true o false en .env.")
    return Settings(
        session_secret=values.get("SESSION_SECRET") or "",
        session_cookie_secure=secure == "true",
        app_timezone=(values.get("APP_TIMEZONE") or "America/Guatemala").strip(),
        **{name: (values.get(name.upper()) or "").strip() for name in (
            "microsoft_client_id", "microsoft_client_secret", "microsoft_tenant",
            "microsoft_redirect_uri", "token_encryption_key",
        )},
    )
