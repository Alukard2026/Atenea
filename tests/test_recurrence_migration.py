import unittest

from sqlalchemy import inspect, text

from app.migrate import migrate_recurrence_timezone, migrate_tasks
import test_task_migration


class RecurrenceMigrationTests(unittest.TestCase):
    def setUp(self):
        fixture = test_task_migration.TaskMigrationTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.connection = fixture.connection
        self.schema = fixture.schema
        migrate_tasks(self.connection)
        self.connection.execute(text("INSERT INTO tasks (organization_id, user_id, title) VALUES (1, 1, 'Tarea existente')"))

    def test_existing_organization_and_task_defaults_preserve_data(self):
        migrate_recurrence_timezone(self.connection)
        self.assertEqual(self.connection.scalar(text("SELECT timezone FROM organizations WHERE id = 1")), "America/El_Salvador")
        row = self.connection.execute(text("SELECT title, recurrence_type, recurrence_interval, recurrence_index, parent_task_id FROM tasks")).one()
        self.assertEqual(tuple(row), ("Tarea existente", "none", 1, 0, None))

    def test_repeating_migration_does_not_reset_configuration(self):
        migrate_recurrence_timezone(self.connection)
        self.connection.execute(text("UPDATE organizations SET timezone = 'Asia/Tokyo' WHERE id = 1"))
        self.connection.execute(text("UPDATE tasks SET recurrence_type = 'daily', due_date = '2026-09-09', recurrence_anchor_date = '2026-09-09'"))
        migrate_recurrence_timezone(self.connection)
        self.assertEqual(self.connection.scalar(text("SELECT timezone FROM organizations WHERE id = 1")), "Asia/Tokyo")
        self.assertEqual(self.connection.scalar(text("SELECT recurrence_type FROM tasks")), "daily")

    def test_additions_can_be_rolled_back(self):
        savepoint = self.connection.begin_nested()
        migrate_recurrence_timezone(self.connection)
        savepoint.rollback()
        columns = {c["name"] for c in inspect(self.connection).get_columns("tasks", schema=self.schema)}
        self.assertNotIn("recurrence_type", columns)
        self.assertEqual(self.connection.scalar(text("SELECT title FROM tasks")), "Tarea existente")

    def test_incompatible_zone_schema_is_not_overwritten(self):
        self.connection.execute(text("ALTER TABLE organizations ADD COLUMN timezone TEXT NOT NULL DEFAULT 'Invalid/Zone'"))
        with self.assertRaises(RuntimeError):
            with self.connection.begin_nested():
                migrate_recurrence_timezone(self.connection)
        self.assertEqual(self.connection.scalar(text("SELECT timezone FROM organizations")), "Invalid/Zone")
