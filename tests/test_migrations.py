"""Migración sobre tablas temporales: nunca altera el esquema real en pruebas."""

import unittest

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.database import engine
from app.migrate import EXPECTED_DEFAULTS, migrate_optional_project, migrate_workdays


class WorkdaysMigrationTests(unittest.TestCase):
    def setUp(self):
        self.connection = engine.connect()
        self.addCleanup(self.connection.close)
        self.transaction = self.connection.begin()
        self.addCleanup(self.transaction.rollback)
        self.connection.execute(text("CREATE TEMP TABLE organizations (id INTEGER PRIMARY KEY, name TEXT NOT NULL) ON COMMIT DROP"))
        self.connection.execute(text("SET LOCAL search_path = pg_temp, public"))
        self.schema = self.connection.scalar(text("SELECT current_schema()"))
        self.assertTrue(self.schema.startswith("pg_temp_"))
        self.connection.execute(text("INSERT INTO organizations (id, name) VALUES (1, 'Organización existente')"))

    def test_migration_preserves_existing_data_and_initializes_defaults(self):
        added = migrate_workdays(self.connection)
        self.assertEqual(set(added), set(EXPECTED_DEFAULTS))
        row = self.connection.execute(text("SELECT * FROM organizations WHERE id = 1")).mappings().one()
        self.assertEqual(row["name"], "Organización existente")
        self.assertEqual(row["id"], 1)
        for field, expected in EXPECTED_DEFAULTS.items():
            self.assertEqual(row[field], expected == "true")

    def test_migration_is_repeatable_without_resetting_customization(self):
        migrate_workdays(self.connection)
        self.connection.execute(text("UPDATE organizations SET workday_monday = FALSE, workday_sunday = TRUE WHERE id = 1"))
        self.assertEqual(migrate_workdays(self.connection), [])
        row = self.connection.execute(text("SELECT workday_monday, workday_sunday FROM organizations WHERE id = 1")).one()
        self.assertEqual(tuple(row), (False, True))

    def test_workday_columns_reject_null(self):
        migrate_workdays(self.connection)
        with self.assertRaises(IntegrityError):
            with self.connection.begin_nested():
                self.connection.execute(text("UPDATE organizations SET workday_sunday = NULL WHERE id = 1"))

    def test_incompatible_partial_schema_rolls_back_additions(self):
        self.connection.execute(text("ALTER TABLE organizations ADD COLUMN workday_monday TEXT NOT NULL DEFAULT 'invalid'"))
        with self.assertRaises(RuntimeError):
            with self.connection.begin_nested():
                migrate_workdays(self.connection)
        columns = {column["name"] for column in inspect(self.connection).get_columns("organizations", schema=self.schema)}
        self.assertNotIn("workday_tuesday", columns)
        self.assertEqual(self.connection.scalar(text("SELECT name FROM organizations WHERE id = 1")), "Organización existente")


class OptionalProjectMigrationTests(unittest.TestCase):
    def test_nullable_migration_preserves_data_and_foreign_keys_and_is_repeatable(self):
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text("CREATE TEMP TABLE projects (id INTEGER PRIMARY KEY) ON COMMIT DROP"))
                connection.execute(text("CREATE TEMP TABLE time_entries (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), description TEXT) ON COMMIT DROP"))
                connection.execute(text("SET LOCAL search_path = pg_temp, public"))
                self.assertTrue(connection.scalar(text("SELECT current_schema()")).startswith("pg_temp_"))
                connection.execute(text("INSERT INTO projects VALUES (1)"))
                connection.execute(text("INSERT INTO time_entries VALUES (1, 1, 'Histórico conservado')"))
                self.assertTrue(migrate_optional_project(connection))
                self.assertFalse(migrate_optional_project(connection))
                self.assertEqual(tuple(connection.execute(text("SELECT * FROM time_entries WHERE id = 1")).one()), (1, 1, "Histórico conservado"))
                connection.execute(text("INSERT INTO time_entries VALUES (2, NULL, 'Sin proyecto')"))
                with self.assertRaises(IntegrityError):
                    with connection.begin_nested():
                        connection.execute(text("INSERT INTO time_entries VALUES (3, 999, 'FK invalida')"))
            finally:
                transaction.rollback()
