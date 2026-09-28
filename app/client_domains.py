"""Sugerencias editables por dominio; sin IA, DNS ni consultas externas."""

import re
from dataclasses import dataclass

from sqlalchemy import select


CLIENT_TYPES = {"empresa": "Empresa", "institucion_publica": "Institución pública", "otro": "Otro"}
DOMAIN_NAMES = {
    "corporativa.cr": "Corporativa",
    "dma.com.sv": "DMA",
    "defensoria.gob.sv": "Defensoría del Consumidor",
}
PUBLIC_EMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "outlook.es", "hotmail.com", "hotmail.es",
    "live.com", "live.com.mx", "msn.com", "yahoo.com", "yahoo.es", "yahoo.com.mx",
    "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com",
    "gmx.com", "gmx.net", "mail.com", "yandex.com", "zoho.com",
})


def normalize_domain(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("Dominio inválido.")
    try:
        if not value.strip():
            return None
        domain = value.strip().lower().removesuffix(".").encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError("Dominio inválido.") from None
    if len(domain) > 253 or "." not in domain or not all(
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in domain.split(".")
    ) or domain.split(".")[-1].isdigit():
        raise ValueError("Introduce un dominio válido, sin @, rutas ni protocolo.")
    return domain


def sender_domain(address):
    if not isinstance(address, str) or address.count("@") != 1 or any(c.isspace() for c in address):
        return None
    local, domain = address.rsplit("@", 1)
    if not local or any(c in local for c in '<>"'):
        return None
    try:
        return normalize_domain(domain)
    except ValueError:
        return None


def is_public_domain(domain):
    return bool(domain) and any(domain == provider or domain.endswith("." + provider) for provider in PUBLIC_EMAIL_DOMAINS)


@dataclass
class DomainSuggestion:
    domain: str | None = None
    name: str = ""
    client_type: str = "otro"
    client: object = None
    public_provider: bool = False


def suggest_client(db, user, address):
    from app.models import Client

    domain = sender_domain(address)
    result = DomainSuggestion(domain=domain)
    if not domain:
        return result
    if is_public_domain(domain):
        result.public_provider = True
        return result
    client = db.scalar(select(Client).where(Client.organization_id == user.organization_id, Client.email_domain == domain))
    if client is not None:
        result.client, result.name, result.client_type = client, client.name, client.client_type
        return result
    result.client_type = "institucion_publica" if domain.endswith(".gob.sv") else "empresa"
    result.name = DOMAIN_NAMES.get(domain) or domain.split(".")[0].replace("-", " ").capitalize()
    return result
