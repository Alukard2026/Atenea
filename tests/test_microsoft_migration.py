"""Migración Microsoft en esquema temporal; todos los cambios se revierten."""

import unittest

from sqlalchemy import inspect, text

from app.migrate import migrate_microsoft
import test_task_migration


class MicrosoftMigrationTests(unittest.TestCase):
    def setUp(self):
        fixture = test_task_migration.TaskMigrationTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        self.connection, self.schema = fixture.connection, fixture.schema

    def test_create_repeat_preserve_existing_data_and_cache(self):
        self.assertEqual(migrate_microsoft(self.connection), 2)
        self.connection.execute(text("INSERT INTO microsoft_accounts (organization_id, user_id, microsoft_account_id, principal_name, is_active) VALUES (1, 1, 'mock', 'mock', false)"))
        self.assertEqual(migrate_microsoft(self.connection), 0)
        self.assertEqual(self.connection.scalar(text("SELECT microsoft_account_id FROM microsoft_accounts")), "mock")
        self.assertEqual(self.connection.scalar(text("SELECT count(*) FROM users")), 1)

    def test_transactional_rollback(self):
        savepoint = self.connection.begin_nested()
        migrate_microsoft(self.connection)
        savepoint.rollback()
        for name in ("microsoft_accounts", "microsoft_oauth_flows"):
            self.assertFalse(inspect(self.connection).has_table(name, schema=self.schema))

    def test_incompatible_schema_is_rejected_without_replacement(self):
        migrate_microsoft(self.connection)
        self.connection.execute(text("ALTER TABLE microsoft_accounts ALTER COLUMN principal_name TYPE TEXT"))
        with self.assertRaises(RuntimeError), self.connection.begin_nested():
            migrate_microsoft(self.connection)

    def test_missing_owner_constraint_is_rejected(self):
        migrate_microsoft(self.connection)
        self.connection.execute(text("ALTER TABLE microsoft_accounts DROP CONSTRAINT fk_microsoft_accounts_owner"))
        with self.assertRaises(RuntimeError), self.connection.begin_nested():
            migrate_microsoft(self.connection)
