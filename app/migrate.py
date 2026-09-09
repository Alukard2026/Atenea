"""Migración incremental de la base existente: python -m app.migrate."""

from pathlib import Path

from sqlalchemy import Boolean, inspect, text


MIGRATION_PATH = Path(__file__).resolve().parent.parent / "migrations" / "001_organization_workdays.sql"
# Esta definición corresponde a la versión 001 y no cambia con futuros defaults.
EXPECTED_DEFAULTS = {
    "workday_monday": "true", "workday_tuesday": "true", "workday_wednesday": "true",
    "workday_thursday": "true", "workday_friday": "true",
    "workday_saturday": "false", "workday_sunday": "false",
}


def migrate_workdays(connection) -> list[str]:
    """Ejecutar dentro de una transacción; no confirma ni elimina datos."""
    if connection.dialect.name != "postgresql":
        raise RuntimeError("Esta migración requiere PostgreSQL.")
    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(text("SET LOCAL statement_timeout = '30s'"))
    connection.execute(text("SELECT pg_advisory_xact_lock(41003, 1)"))
    schema = connection.scalar(text("SELECT current_schema()"))
    before = {column["name"] for column in inspect(connection).get_columns("organizations", schema=schema)}
    connection.exec_driver_sql(MIGRATION_PATH.read_text(encoding="utf-8"))
    columns = {column["name"]: column for column in inspect(connection).get_columns("organizations", schema=schema)}
    for name, expected_default in EXPECTED_DEFAULTS.items():
        column = columns[name]
        default = (column["default"] or "").lower().replace("::boolean", "").strip("() ")
        if not isinstance(column["type"], Boolean) or column["nullable"] or default != expected_default:
            raise RuntimeError("El esquema de días laborables no coincide con la migración 001.")
    return sorted(set(EXPECTED_DEFAULTS) - before)


def migrate_optional_project(connection) -> bool:
    """Migración 002, repetible y sin cambios a registros ni claves foráneas."""
    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(text("SET LOCAL statement_timeout = '30s'"))
    connection.execute(text("SELECT pg_advisory_xact_lock(41003, 1)"))
    schema = connection.scalar(text("SELECT current_schema()"))
    before = {c["name"]: c for c in inspect(connection).get_columns("time_entries", schema=schema)}
    foreign_keys = inspect(connection).get_foreign_keys("time_entries", schema=schema)
    path = MIGRATION_PATH.with_name("002_optional_time_entry_project.sql")
    connection.exec_driver_sql(path.read_text(encoding="utf-8"))
    after = {c["name"]: c for c in inspect(connection).get_columns("time_entries", schema=schema)}
    if not after["project_id"]["nullable"] or inspect(connection).get_foreign_keys("time_entries", schema=schema) != foreign_keys:
        raise RuntimeError("No se pudo verificar la migración 002.")
    return not before["project_id"]["nullable"]


def main() -> int:
    try:
        from app.database import engine

        with engine.begin() as connection:
            added = migrate_workdays(connection)
            project_changed = migrate_optional_project(connection)
            tasks_created = migrate_tasks(connection)
            migrate_recurrence_timezone(connection)
        engine.dispose()
        print(f"Migración 001 verificada. Columnas añadidas: {len(added)}. Datos existentes conservados.")
        print(f"Migración 002 verificada. Proyecto opcional; cambio aplicado: {project_changed}.")
        print(f"Migración 003 verificada. Tabla tasks creada: {tasks_created}.")
        print("Migración 004 verificada. Recurrencias y zonas horarias por organización disponibles.")
        return 0
    except Exception:
        # No mostrar excepciones SQL, URL de conexión ni otros valores sensibles.
        print("No se pudo aplicar la migración; la transacción se revirtió. Revisa la conexión, los bloqueos y el esquema.")
        return 1


def migrate_tasks(connection) -> bool:
    """003: crear tareas y verificar la estructura; nunca reemplazar tablas existentes."""
    if connection.dialect.name != "postgresql":
        raise RuntimeError("Esta migración requiere PostgreSQL.")
    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(text("SET LOCAL statement_timeout = '30s'"))
    connection.execute(text("SELECT pg_advisory_xact_lock(41003, 1)"))
    schema = connection.scalar(text("SELECT current_schema()"))
    existed = inspect(connection).has_table("tasks", schema=schema)
    connection.exec_driver_sql(MIGRATION_PATH.with_name("003_tasks.sql").read_text(encoding="utf-8"))
    inspector = inspect(connection)
    columns = {column["name"]: column for column in inspector.get_columns("tasks", schema=schema)}
    expected = {
        "id": ("INTEGER", False), "organization_id": ("INTEGER", False), "user_id": ("INTEGER", False),
        "client_id": ("INTEGER", True), "project_id": ("INTEGER", True), "title": ("VARCHAR(255)", False),
        "description": ("TEXT", True), "due_date": ("DATE", True), "due_time": ("TIME WITHOUT TIME ZONE", True),
        "priority": ("VARCHAR(20)", False), "status": ("VARCHAR(20)", False),
        "reminder_at": ("TIMESTAMP WITH TIME ZONE", True), "completed_at": ("TIMESTAMP WITH TIME ZONE", True),
        "created_at": ("TIMESTAMP WITH TIME ZONE", False), "updated_at": ("TIMESTAMP WITH TIME ZONE", False),
        "source_type": ("VARCHAR(50)", True), "source_id": ("VARCHAR(1024)", True),
    }
    for name, (kind, nullable) in expected.items():
        column = columns.get(name)
        if column is None or str(column["type"].compile(dialect=connection.dialect)) != kind or column["nullable"] != nullable:
            raise RuntimeError("El esquema de tareas no coincide con la migración 003.")
    keys = {(tuple(key["constrained_columns"]), key["referred_table"], tuple(key["referred_columns"])) for key in inspector.get_foreign_keys("tasks", schema=schema)}
    required_keys = {
        (("organization_id",), "organizations", ("id",)),
        (("organization_id", "user_id"), "users", ("organization_id", "id")),
        (("organization_id", "client_id"), "clients", ("organization_id", "id")),
        (("organization_id", "client_id", "project_id"), "projects", ("organization_id", "client_id", "id")),
    }
    checks = {check["name"] for check in inspector.get_check_constraints("tasks", schema=schema)}
    required_checks = {"ck_tasks_" + suffix for suffix in ("project_client", "time_date", "priority", "status", "completion", "title", "source")}
    indexes = {tuple(index["column_names"]) for index in inspector.get_indexes("tasks", schema=schema)}
    required_indexes = {("organization_id", "user_id", "status", "due_date"), ("organization_id", "user_id", "status", "reminder_at"), ("organization_id", "client_id"), ("organization_id", "project_id")}
    defaults_valid = all(expected_default in (columns[name]["default"] or "") for name, expected_default in (("priority", "'normal'"), ("status", "'pending'"), ("created_at", "now()"), ("updated_at", "now()")))
    if not (required_keys <= keys and required_checks <= checks and required_indexes <= indexes and defaults_valid):
        raise RuntimeError("No se pudieron verificar las restricciones de tareas.")
    if inspector.get_pk_constraint("tasks", schema=schema)["constrained_columns"] != ["id"]:
        raise RuntimeError("No se pudo verificar la clave de tareas.")
    return not existed


def migrate_recurrence_timezone(connection):
    """004: repetible, sin reinicializar zonas ni reglas previamente configuradas."""
    from app.timezones import valid_timezone

    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(text("SET LOCAL statement_timeout = '30s'"))
    connection.execute(text("SELECT pg_advisory_xact_lock(41003, 1)"))
    schema = connection.scalar(text("SELECT current_schema()"))
    connection.exec_driver_sql(MIGRATION_PATH.with_name("004_task_recurrence_timezone.sql").read_text(encoding="utf-8"))
    # Nombres y expresiones constantes propios de la versión 004, no del modelo futuro.
    constraints = {
        "uq_tasks_org_user_id": "UNIQUE (organization_id, user_id, id)",
        "uq_tasks_parent": "UNIQUE (parent_task_id)",
        "fk_tasks_parent_owner": "FOREIGN KEY (organization_id, user_id, parent_task_id) REFERENCES tasks(organization_id, user_id, id)",
        "ck_tasks_recurrence_type": "CHECK (recurrence_type IN ('none', 'daily', 'weekly', 'monthly', 'yearly'))",
        "ck_tasks_recurrence_interval": "CHECK (recurrence_interval BETWEEN 1 AND 365)",
        "ck_tasks_recurrence_index": "CHECK (recurrence_index >= 0)",
        "ck_tasks_recurrence_date": "CHECK (recurrence_type = 'none' OR (due_date IS NOT NULL AND recurrence_anchor_date IS NOT NULL))",
        "ck_tasks_recurrence_end": "CHECK (recurrence_end_date IS NULL OR (recurrence_anchor_date IS NOT NULL AND recurrence_end_date >= recurrence_anchor_date))",
    }
    existing = set(connection.scalars(text("SELECT conname FROM pg_constraint WHERE conrelid = to_regclass(:table)"), {"table": f'"{schema}".tasks'}))
    for name, definition in constraints.items():
        if name not in existing:
            connection.exec_driver_sql(f'ALTER TABLE tasks ADD CONSTRAINT "{name}" {definition}')
    expected = {
        "organizations": {"timezone": ("VARCHAR(100)", False, "'America/El_Salvador'")},
        "tasks": {"recurrence_type": ("VARCHAR(20)", False, "'none'"), "recurrence_interval": ("INTEGER", False, "1"),
                  "recurrence_index": ("INTEGER", False, "0"), "recurrence_end_date": ("DATE", True, None),
                  "recurrence_anchor_date": ("DATE", True, None), "parent_task_id": ("INTEGER", True, None)},
    }
    for table, fields in expected.items():
        columns = {c["name"]: c for c in inspect(connection).get_columns(table, schema=schema)}
        for name, (kind, nullable, default) in fields.items():
            column = columns[name]
            if str(column["type"].compile(dialect=connection.dialect)) != kind or column["nullable"] != nullable or (default is not None and default not in (column["default"] or "")):
                raise RuntimeError("El esquema no coincide con la migración 004.")
    for zone in connection.scalars(text("SELECT DISTINCT timezone FROM organizations")):
        valid_timezone(zone)


if __name__ == "__main__":
    raise SystemExit(main())
