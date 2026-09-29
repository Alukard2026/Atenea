"""007 probada en esquema temporal, sin afectar datos existentes."""
import unittest
from uuid import uuid4
from sqlalchemy import inspect, text
from app.database import engine
from app.migrate import migrate_calendar


class CalendarMigrationTests(unittest.TestCase):
    def setUp(self):
        self.connection = engine.connect()
        self.addCleanup(self.connection.close)
        self.transaction = self.connection.begin()
        self.addCleanup(self.transaction.rollback)
        self.schema = "test_calendar_" + uuid4().hex
        self.connection.exec_driver_sql(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(text("SELECT set_config('search_path', :path, true)"), {"path": f'"{self.schema}", public'})
        self.connection.exec_driver_sql("""
          CREATE TABLE organizations (id INTEGER PRIMARY KEY);
          CREATE TABLE users (id INTEGER PRIMARY KEY, organization_id INTEGER, UNIQUE(organization_id,id));
          CREATE TABLE clients (id INTEGER PRIMARY KEY, organization_id INTEGER, UNIQUE(organization_id,id));
          CREATE TABLE projects (id INTEGER PRIMARY KEY, organization_id INTEGER, client_id INTEGER, UNIQUE(organization_id,client_id,id));
          INSERT INTO organizations VALUES (1); INSERT INTO users VALUES (1,1);
        """)

    def test_repeat_and_preserve(self):
        self.assertTrue(migrate_calendar(self.connection))
        self.connection.exec_driver_sql("INSERT INTO calendar_events (organization_id,user_id,event_type,title,start_at) VALUES (1,1,'hearing','Preservar','2026-10-05 10:00:00+00')")
        self.assertFalse(migrate_calendar(self.connection))
        self.assertEqual(self.connection.scalar(text("SELECT title FROM calendar_events")), "Preservar")
        self.assertEqual(self.connection.scalar(text("SELECT count(*) FROM users")), 1)

    def test_transaction_rollback(self):
        savepoint = self.connection.begin_nested()
        migrate_calendar(self.connection)
        savepoint.rollback()
        self.assertFalse(inspect(self.connection).has_table("calendar_events", schema=self.schema))

    def test_incompatible_table_not_replaced(self):
        migrate_calendar(self.connection)
        self.connection.exec_driver_sql("ALTER TABLE calendar_events ALTER COLUMN title TYPE TEXT")
        with self.assertRaises(RuntimeError), self.connection.begin_nested():
            migrate_calendar(self.connection)

    def test_missing_foreign_key_detected(self):
        migrate_calendar(self.connection)
        self.connection.exec_driver_sql("ALTER TABLE calendar_events DROP CONSTRAINT fk_calendar_owner")
        with self.assertRaises(RuntimeError), self.connection.begin_nested():
            migrate_calendar(self.connection)
