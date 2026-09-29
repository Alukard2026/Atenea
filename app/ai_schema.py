"""Contrato independiente del proveedor; salida acotada y validada de nuevo."""

from datetime import date, time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Priority = Literal["low", "normal", "high", "urgent"]
Category = Literal["urgente", "legal", "cobro_facturacion", "reunion_cita", "seguimiento", "cliente",
                   "institucion_publica", "interno", "informativo", "sin_clasificar"]
ShortText = Annotated[str, Field(min_length=1, max_length=500)]
DateText = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
TimeText = Annotated[str, Field(pattern=r"^\d{2}:\d{2}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")

    @field_validator("*", mode="after")
    @classmethod
    def safe_strings(cls, value):
        if isinstance(value, str) and ((value and not value.strip()) or any(ord(c) < 32 and c not in "\n\r\t" for c in value)):
            raise ValueError("Texto no válido.")
        return value


def valid_date(value):
    if value is not None:
        parsed = date.fromisoformat(value)
        if not date(1900, 1, 1) <= parsed <= date(2100, 12, 31):
            raise ValueError("Fecha fuera de rango.")
    return value


class ActionItem(StrictModel):
    text: ShortText
    kind: Literal["explicit", "inferred"]
    due_date: DateText | None
    due_time: TimeText | None

    _date = field_validator("due_date")(valid_date)

    @model_validator(mode="after")
    def valid_time(self):
        if self.due_time is not None:
            time.fromisoformat(self.due_time)
            if self.due_date is None:
                raise ValueError("La hora requiere fecha.")
        return self


class FoundDate(StrictModel):
    text: Annotated[str, Field(min_length=1, max_length=200)]
    date: DateText | None
    meaning: Annotated[str, Field(min_length=1, max_length=200)]

    _date = field_validator("date")(valid_date)


class Entity(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    type: Literal["person", "company", "institution", "other"]


class EmailAnalysis(StrictModel):
    summary: Annotated[str, Field(min_length=1, max_length=1200)]
    suggested_priority: Priority
    priority_reason: ShortText
    categories: Annotated[list[Category], Field(max_length=10)]
    action_items: Annotated[list[ActionItem], Field(max_length=8)]
    dates_found: Annotated[list[FoundDate], Field(max_length=8)]
    entities: Annotated[list[Entity], Field(max_length=10)]
    suggested_client: Annotated[str, Field(min_length=1, max_length=255)] | None
    suggested_task_title: Annotated[str, Field(min_length=1, max_length=255)] | None
    suggested_task_description: Annotated[str, Field(min_length=1, max_length=3000)] | None
    warnings: Annotated[list[ShortText], Field(max_length=8)]
    confidence: Literal["low", "medium", "high"]


class TaskDraft(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=255)]
    description: Annotated[str, Field(max_length=3000)]
    priority: Priority
    due_date: DateText | None
    due_time: TimeText | None

    _date = field_validator("due_date")(valid_date)

    @model_validator(mode="after")
    def valid_time(self):
        if self.due_time is not None:
            time.fromisoformat(self.due_time)
            if self.due_date is None:
                raise ValueError("La hora requiere fecha.")
        return self
