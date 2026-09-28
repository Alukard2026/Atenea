"""Configuración de PostgreSQL y sesiones de base de datos para FastAPI."""

from collections.abc import Generator
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, declarative_base, sessionmaker


# Ruta absoluta para no depender del directorio desde el que se inicia FastAPI.
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
# Sin interpolación ni fallback a variables del sistema: solo se usa este archivo.
database_url = dotenv_values(ENV_PATH, interpolate=False).get("DATABASE_URL")
if not database_url or not database_url.strip():
    raise RuntimeError("Falta DATABASE_URL en el archivo .env de la raíz.")

try:
    connection_url = make_url(database_url)
except (ArgumentError, ValueError):
    raise RuntimeError("DATABASE_URL en .env no tiene un formato válido.") from None

if connection_url.get_backend_name() != "postgresql":
    raise RuntimeError("DATABASE_URL en .env debe usar PostgreSQL.")

engine = create_engine(connection_url, pool_pre_ping=True, echo=False, hide_parameters=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """Entrega una sesión por petición y la cierra incluso si ocurre un error."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
