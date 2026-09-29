"""Clasificación efímera y explicable. Sin IA, red, escrituras ni logs de correo.

Editar RULES para palabras/categorías/prioridad; no acumular pesos por repetición.
INTERNAL_DOMAINS_BY_ORGANIZATION solo admite dominios comprobados por el operador.
No deducir un dominio interno de la dirección de un usuario o su cuenta Microsoft.
"""

from dataclasses import dataclass
import re
import unicodedata

from app.client_domains import (
    DomainSuggestion, is_public_domain, normalize_domain, sender_domain,
    suggest_clients, suggestion_for_domain,
)


CATEGORIES = {
    "urgente": "Urgente", "legal": "Legal", "cobro_facturacion": "Cobro / facturación",
    "reunion_cita": "Reunión / cita", "seguimiento": "Seguimiento", "cliente": "Cliente",
    "institucion_publica": "Institución pública", "interno": "Interno",
    "informativo": "Informativo", "sin_clasificar": "Sin clasificar",
}
PRIORITIES = {"low": "Baja", "normal": "Normal", "high": "Alta", "urgent": "Urgente"}
PRIORITY_RANK = {value: index for index, value in enumerate(PRIORITIES)}
SUBJECT_LIMIT = 1000
PREVIEW_LIMIT = 240
INTERNAL_DOMAINS_BY_ORGANIZATION: dict[int, frozenset[str]] = {}


@dataclass(frozen=True)
class Rule:
    key: str
    categories: tuple[str, ...]
    priority: str
    terms: tuple[str, ...]


RULES = (
    Rule("urgencia_explicita", ("urgente",), "urgent", (
        "urgente", "urgentes", "inmediato", "inmediata", "inmediatamente", "vence hoy",
        "último día", "requerimiento urgente", "plazo vence hoy",
    )),
    Rule("plazo_atencion", (), "high", (
        "cuanto antes", "vencimiento", "plazo", "plazos", "suspensión", "incumplimiento",
    )),
    Rule("factura_vencida", ("cobro_facturacion",), "high", ("factura vencida", "facturas vencidas")),
    Rule("legal", ("legal",), "normal", (
        "audiencia", "audiencias", "citación", "citaciones", "expediente", "expedientes",
        "tribunal", "juzgado", "demanda", "escrito", "resolución", "notificación",
        "requerimiento", "recurso", "apelación",
    )),
    Rule("facturacion", ("cobro_facturacion",), "normal", (
        "factura", "facturas", "cobro", "pago", "pagos", "saldo", "mora", "vencida", "estado de cuenta",
    )),
    Rule("reunion", ("reunion_cita",), "normal", (
        "reunión", "reuniones", "reunión virtual", "teams", "zoom", "cita", "convocatoria", "agenda", "calendar",
    )),
    Rule("seguimiento", ("seguimiento",), "normal", (
        "seguimiento", "pendiente", "pendientes", "recordar", "confirmación", "respuesta pendiente",
    )),
    Rule("informativo", ("informativo",), "low", (
        "boletín", "newsletter", "informativo", "para su información", "para tu información",
    )),
)


def limited_text(value, limit):
    return value[:limit] if isinstance(value, str) else ""


def words(value):
    normalized = unicodedata.normalize("NFKD", value.casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return tuple(re.findall(r"[^\W_]+", normalized))


def contains(tokens, phrase):
    """Palabras completas y frases contiguas, con negación local conservadora."""
    for index in range(len(tokens) - len(phrase) + 1):
        if tokens[index:index + len(phrase)] == phrase:
            if not {"no", "sin"}.intersection(tokens[max(0, index - 3):index]):
                return True
    return False


@dataclass(frozen=True)
class MailInput:
    sender_email: str = ""
    subject: str = ""
    preview: str = ""

    @property
    def domain(self):
        return sender_domain(self.sender_email)


def message_input(raw):
    """No lee body ni el nombre visible para inferir el dominio."""
    sender = raw.get("from")
    address = sender.get("emailAddress") if isinstance(sender, dict) else None
    return MailInput(
        sender_email=limited_text(address.get("address"), 320) if isinstance(address, dict) else "",
        subject=limited_text(raw.get("subject"), SUBJECT_LIMIT),
        preview=limited_text(raw.get("bodyPreview"), PREVIEW_LIMIT),
    )


@dataclass(frozen=True)
class Signal:
    rule: str
    source: str
    term: str


@dataclass(frozen=True)
class Analysis:
    categories: tuple[str, ...]
    priority: str
    reasons: tuple[str, ...]
    signals: tuple[Signal, ...]
    entity: DomainSuggestion

    @property
    def priority_label(self):
        return PRIORITIES[self.priority]

    @property
    def category_labels(self):
        return tuple(CATEGORIES[value] for value in self.categories)

    @property
    def compact_labels(self):
        return tuple(CATEGORIES[value] for value in self.categories if value not in {"sin_clasificar", "urgente", "cliente"})


def classify(message, suggestion=None, *, internal_domains=(), rules=None):
    """Función pura. Precedencia máxima: urgent > high > normal > low.

    La ausencia de señales da normal; repetir términos no escala prioridad.
    Los dominios no elevan prioridad; importancia de Outlook no interviene.
    """
    domain = message.domain
    entity = suggestion if suggestion is not None and suggestion.domain == domain else suggestion_for_domain(domain)
    categories, priorities, reasons, signals = set(), [], [], []
    texts = (("asunto", words(limited_text(message.subject, SUBJECT_LIMIT))),
             ("vista previa", words(limited_text(message.preview, PREVIEW_LIMIT))))
    for rule in RULES if rules is None else rules:
        hits = []
        for term in rule.terms:
            phrase = words(term)
            if not phrase:
                continue
            for source, tokens in texts:
                if contains(tokens, phrase):
                    hits.append((term, source))
                    signals.append(Signal(rule.key, source, term))
                    break
        if hits:
            categories.update(rule.categories)
            priorities.append(rule.priority)
            terms = ", ".join(f"«{term}» ({source})" for term, source in hits)
            reasons.append(f"Palabras clave detectadas: {terms}. Sugieren prioridad {PRIORITIES[rule.priority].lower()}.")
    if domain and not is_public_domain(domain):
        if entity.client is not None:
            categories.add("cliente")
            reasons.append(f"Dominio {domain} asociado a un cliente de tu organización.")
            signals.append(Signal("cliente_conocido", "dominio", domain))
        if domain.endswith(".gob.sv") or entity.client_type == "institucion_publica":
            categories.add("institucion_publica")
            reasons.append(f"Dominio institucional {domain}" + (" (.gob.sv)." if domain.endswith(".gob.sv") else ": tipo registrado en el cliente."))
            signals.append(Signal("institucion_publica", "dominio", domain))
        for configured in internal_domains:
            try:
                internal = normalize_domain(configured)
            except ValueError:
                continue
            if domain == internal:
                categories.add("interno")
                reasons.append(f"Dominio {domain} coincide exactamente con un dominio interno configurado para tu organización.")
                signals.append(Signal("dominio_interno", "dominio", domain))
                break
    else:
        # Ni una asociación heredada a Gmail ni un nombre visible prueban cliente.
        entity = suggestion_for_domain(domain)
    priority = max(priorities, key=PRIORITY_RANK.get) if priorities else "normal"
    if not reasons:
        reasons.append("No se detectaron señales suficientes en el asunto, la vista previa o el dominio. Prioridad normal.")
    return Analysis(tuple(value for value in CATEGORIES if value in categories) or ("sin_clasificar",),
                    priority, tuple(reasons), tuple(signals), entity)


def analyze_inputs(db, user, messages):
    suggestions = suggest_clients(db, user, [message.sender_email for message in messages])
    internal = INTERNAL_DOMAINS_BY_ORGANIZATION.get(user.organization_id, ())
    return [classify(message, suggestions[message.domain], internal_domains=internal) for message in messages]


def analyze_messages(db, user, messages):
    for message, analysis in zip(messages, analyze_inputs(db, user, [message["rule_input"] for message in messages])):
        message["analysis"] = analysis
