"""Migración 003 en un esquema aislado, revertido al terminar cada prueba."""

import unittest
from uuid import uuid4

from sqlalchemy import inspect, text

from app.database import engine
from app.migrate import migrate_tasks


class TaskMigrationTests(unittest.TestCase):
    def setUp(self):
        self.connection = engine.connect()
        self.addCleanup(self.connection.close)
        self.transaction = self.connection.begin()
        self.addCleanup(self.transaction.rollback)
        self.schema = "test_tasks_" + uuid4().hex
        self.connection.exec_driver_sql(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(text("SELECT set_config('search_path', :path, true)"), {"path": f'"{self.schema}", public'})
        self.assertEqual(self.connection.scalar(text("SELECT current_schema()")), self.schema)
        self.connection.exec_driver_sql("""
            CREATE TABLE organizations (id INTEGER PRIMARY KEY);
            CREATE TABLE users (id INTEGER PRIMARY KEY, organization_id INTEGER, UNIQUE(organization_id, id));
            CREATE TABLE clients (id INTEGER PRIMARY KEY, organization_id INTEGER, UNIQUE(organization_id, id));
            CREATE TABLE projects (id INTEGER PRIMARY KEY, organization_id INTEGER, client_id INTEGER, UNIQUE(organization_id, client_id, id));
            INSERT INTO organizations VALUES (1);
            INSERT INTO users VALUES (1, 1);
        """)

    def test_create_repeat_and_preserve_data(self):
        self.assertTrue(migrate_tasks(self.connection))
        self.connection.execute(text("INSERT INTO tasks (organization_id, user_id, title) VALUES (1, 1, 'Histórico conservado')"))
        self.assertFalse(migrate_tasks(self.connection))
        row = self.connection.execute(text("SELECT title, priority, status, due_time FROM tasks")).one()
        self.assertEqual(tuple(row), ("Histórico conservado", "normal", "pending", None))
        self.assertEqual(self.connection.scalar(text("SELECT count(*) FROM users")), 1)

    def test_creation_can_be_rolled_back(self):
        savepoint = self.connection.begin_nested()
        migrate_tasks(self.connection)
        savepoint.rollback()
        self.assertFalse(inspect(self.connection).has_table("tasks", schema=self.schema))

    def test_incompatible_existing_schema_is_not_replaced(self):
        migrate_tasks(self.connection)
        self.connection.execute(text("ALTER TABLE tasks ALTER COLUMN title TYPE TEXT"))
        self.connection.execute(text("INSERT INTO tasks (organization_id, user_id, title) VALUES (1, 1, 'Preservar')"))
        with self.assertRaises(RuntimeError):
            with self.connection.begin_nested():
                migrate_tasks(self.connection)
        self.assertEqual(self.connection.scalar(text("SELECT title FROM tasks")), "Preservar")
