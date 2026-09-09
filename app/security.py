"""Hash y verificación de contraseñas mediante bcrypt."""

import bcrypt


MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_BYTES = 72
BCRYPT_ROUNDS = 12


class InvalidPasswordError(ValueError):
    """Contraseña que no cumple los límites admitidos, sin revelar su valor."""


def hash_password(password: str) -> str:
    """Genera un hash con sal aleatoria; nunca trunca la contraseña."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise InvalidPasswordError("La contraseña debe tener al menos 12 caracteres.")
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise InvalidPasswordError("La contraseña no puede superar 72 bytes en UTF-8.")
    if not password.strip():
        raise InvalidPasswordError("La contraseña no puede contener solo espacios.")
    return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def verify_password(password: str, hashed_password: str) -> bool:
    """Devuelve False para contraseñas incorrectas o hashes inválidos."""
    if not isinstance(password, str) or not isinstance(hashed_password, str):
        return False
    try:
        encoded = password.encode("utf-8")
        if len(encoded) > MAX_PASSWORD_BYTES:
            return False
        return bcrypt.checkpw(encoded, hashed_password.encode("ascii"))
    except (ValueError, UnicodeError):
        return False
