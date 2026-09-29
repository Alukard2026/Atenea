"""Administración local: python -m app.cli {create-admin,list-users}."""

import argparse
import getpass
import sys
import warnings
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.security import InvalidPasswordError, hash_password

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models import User


# Serializa las altas de este CLI sin cambiar el esquema de organizations.
_ADMIN_CREATION_LOCK = 0x4154454E4541


class CLIError(ValueError):
    """Error previsto con un mensaje apto para mostrarse en la terminal."""


def _required_text(value: str, label: str, maximum: int) -> str:
    value = value.strip()
    if not value or len(value) > maximum or not value.isprintable():
        raise CLIError(f"{label}: introduce entre 1 y {maximum} caracteres imprimibles.")
    return value


def _read_password() -> str:
    # getpass puede recurrir a una entrada visible si no dispone de terminal.
    # Convertir su advertencia en error impide ese fallback antes de leer.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            password = getpass.getpass("Contraseña (mínimo 12 caracteres): ")
            confirmation = getpass.getpass("Repite la contraseña: ")
        except getpass.GetPassWarning:
            raise CLIError("No hay entrada oculta disponible. Usa una terminal interactiva.") from None
    if password != confirmation:
        raise CLIError("Las contraseñas no coinciden.")
    return password


def create_admin(
    db: "Session", organization_name: str, full_name: str, email: str, password: str,
) -> "User":
    """Prepara el alta; quien llama debe confirmar o revertir la transacción."""
    from app.models import Organization, User
    from app.users import normalize_email, email_key
    from app.worklog import FormError

    organization_name = _required_text(organization_name, "Organización", 255)
    full_name = _required_text(full_name, "Nombre completo", 255)
    try:
        email = normalize_email(email)
    except FormError as error:
        raise CLIError(error.message) from None
    hashed_password = hash_password(password)

    db.execute(select(func.pg_advisory_xact_lock(_ADMIN_CREATION_LOCK)))
    if db.scalar(select(User.id).where(email_key() == email).limit(1)) is not None:
        raise CLIError("Ya existe un usuario con ese email en Atenea. El email debe ser único globalmente.")
    organizations = db.scalars(
        select(Organization).where(
            func.lower(func.trim(Organization.name)) == func.lower(organization_name)
        ).limit(2)
    ).all()
    if len(organizations) > 1:
        raise CLIError("Hay varias organizaciones con ese nombre. Resuelve la ambigüedad antes del alta.")
    if organizations:
        organization = organizations[0]
        if not organization.is_active:
            raise CLIError("La organización está inactiva; no se creará el administrador.")
    else:
        organization = Organization(name=organization_name, is_active=True)
        db.add(organization)
        db.flush()

    existing = db.scalar(select(User.id).where(
        email_key() == email,
    ))
    if existing is not None:
        raise CLIError("Ya existe un usuario con ese email en Atenea.")

    user = User(
        organization=organization,
        email=email,
        full_name=full_name,
        hashed_password=hashed_password,
        role="admin",
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def list_users(db: "Session") -> None:
    """Consulta únicamente las columnas que se muestran, sin cargar hashes."""
    from app.models import Organization, User

    rows = db.execute(select(
        User.id, Organization.id, Organization.name, User.full_name, User.email, User.role, User.is_active,
    ).join(User.organization).order_by(Organization.name, User.id)).all()
    print("ID\tOrganización ID\tOrganización\tNombre\tEmail\tRol\tEstado")
    for user_id, organization_id, organization, name, email, role, active in rows:
        values = (user_id, organization_id, organization, name, email, role, "activo" if active else "inactivo")
        # Evita que datos existentes introduzcan controles de terminal.
        print("\t".join("".join(c if c.isprintable() else " " for c in str(value)) for value in values))
    if not rows:
        print("No hay usuarios registrados.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Administración local de Atenea.")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-admin", help="Crear una organización y/o un administrador.")
    create.add_argument("--organization", help="Nombre de la organización.")
    create.add_argument("--full-name", help="Nombre completo del administrador.")
    create.add_argument("--email", help="Email del administrador.")
    commands.add_parser("list-users", help="Listar usuarios de todas las organizaciones.")
    args = parser.parse_args(argv)

    try:
        if args.command == "create-admin":
            organization = args.organization if args.organization is not None else input("Organización: ")
            full_name = args.full_name if args.full_name is not None else input("Nombre completo: ")
            email = args.email if args.email is not None else input("Email: ")
            password = _read_password()

            # Importar dentro del bloque evita revelar detalles de configuración
            # mediante un traceback si la inicialización de la BD falla.
            from app.database import SessionLocal
            try:
                with SessionLocal() as db, db.begin():
                    user = create_admin(db, organization, full_name, email, password)
                    user_id, organization_id = user.id, user.organization_id
            finally:
                del password
            print(f"Administrador creado. ID: {user_id}; organización ID: {organization_id}.")
        else:
            from app.database import SessionLocal
            with SessionLocal() as db:
                list_users(db)
        return 0
    except (CLIError, InvalidPasswordError) as error:
        print(f"Error: {error}", file=sys.stderr)
    except IntegrityError:
        print("Error de integridad: el alta se revirtió. Comprueba si el usuario ya existe.", file=sys.stderr)
    except SQLAlchemyError:
        print("Error de base de datos. Comprueba la conexión y las tablas; no se confirmó ningún alta.", file=sys.stderr)
    except (EOFError, KeyboardInterrupt):
        print("\nOperación cancelada.", file=sys.stderr)
        return 130
    except Exception:
        # Los errores SQL pueden incluir parámetros (también hashes). No imprimir
        # excepciones ni tracebacks en este CLI de administración.
        print("No se pudo completar la operación. Revisa la configuración y las dependencias.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
