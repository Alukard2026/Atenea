"""Correspondencia entre días calendario y configuración persistida por empresa."""


# El orden coincide con date.weekday(): lunes=0, domingo=6.
WORKDAY_FIELDS = (
    "workday_monday", "workday_tuesday", "workday_wednesday", "workday_thursday",
    "workday_friday", "workday_saturday", "workday_sunday",
)
DAY_NAMES = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")


def workday_flags(organization) -> tuple[bool, ...]:
    """Leer los siete valores de la organización; no suponer un calendario fijo."""
    return tuple(getattr(organization, field) for field in WORKDAY_FIELDS)
