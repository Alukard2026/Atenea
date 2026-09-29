"""008: duplicados detectados sin tocar filas y restricción global comprobada."""
import unittest
from uuid import uuid4
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from app.database import engine
from app.migrate import migrate_global_user_email, DuplicateUserEmails


class UserIdentityMigrationTests(unittest.TestCase):
    def setUp(self):
        self.connection = engine.connect()
        self.addCleanup(self.connection.close)
        self.transaction = self.connection.begin()
        self.addCleanup(self.transaction.rollback)
        self.schema = "test_identity_" + uuid4().hex
        self.connection.exec_driver_sql(f'CREATE SCHEMA "{self.schema}"')
        self.connection.execute(text("SELECT set_config('search_path', :path, true)"), {"path": self.schema})
        self.connection.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY, organization_id INTEGER NOT NULL, email VARCHAR(320) NOT NULL, hashed_password TEXT NOT NULL)")

    def insert(self, identity, organization, email):
        self.connection.execute(text("INSERT INTO users VALUES (:id,:org,:email,'fixture-hash')"), {"id": identity, "org": organization, "email": email})

    def test_duplicates_case_spaces_cross_organization_stop_without_changes(self):
        self.insert(1, 1, " EXAMPLE@example.invalid ")
        self.insert(2, 2, "example@example.invalid")
        with self.assertRaises(DuplicateUserEmails) as caught:
            migrate_global_user_email(self.connection)
        self.assertEqual(caught.exception.groups, [[1, 2]])
        self.assertEqual(self.connection.scalar(text("SELECT count(*) FROM users")), 2)
        self.assertNotIn("auth_version", {c["name"] for c in inspect(self.connection).get_columns("users", schema=self.schema)})
        self.assertEqual(self.connection.scalar(text("SELECT email FROM users WHERE id=1")), " EXAMPLE@example.invalid ")

    def test_success_repeat_preserve_and_enforce_global_index(self):
        self.insert(1, 1, " EXAMPLE@example.invalid ")
        migrate_global_user_email(self.connection)
        migrate_global_user_email(self.connection)
        self.assertEqual(self.connection.execute(text("SELECT email, hashed_password, auth_version FROM users")).one(), (" EXAMPLE@example.invalid ", "fixture-hash", 0))
        with self.assertRaises(IntegrityError), self.connection.begin_nested():
            self.connection.execute(text("INSERT INTO users (id,organization_id,email,hashed_password) VALUES (2,2,'example@example.invalid','unused')"))

    def test_rollback_restores_schema(self):
        savepoint = self.connection.begin_nested()
        migrate_global_user_email(self.connection)
        savepoint.rollback()
        self.assertNotIn("auth_version", {c["name"] for c in inspect(self.connection).get_columns("users", schema=self.schema)})

    def test_wrong_existing_index_rejected(self):
        self.connection.exec_driver_sql("CREATE INDEX uq_users_email_global ON users (email)")
        with self.assertRaises(RuntimeError), self.connection.begin_nested():
            migrate_global_user_email(self.connection)
