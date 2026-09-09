"""Pruebas de seguridad y CLI en PostgreSQL; todas las filas se revierten."""

from contextlib import redirect_stderr, redirect_stdout
import getpass
import io
import secrets
import unittest
from unittest.mock import patch
from uuid import uuid4
import warnings

from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app import cli
from app.database import engine
from app.models import Organization, User
from app.security import InvalidPasswordError, hash_password, verify_password


class SecurityTests(unittest.TestCase):
    def test_hash_verification_and_random_salt(self):
        password = secrets.token_urlsafe(24)
        first, second = hash_password(password), hash_password(password)
        self.assertTrue(first != password and first != second)
        self.assertTrue(verify_password(password, first))
        self.assertFalse(verify_password(password + "!", first))
        self.assertFalse(verify_password(password, "invalid-hash"))
        self.assertFalse(verify_password("x" * 73, first))

    def test_password_limits_without_truncation(self):
        for password in ("x" * 11, " " * 12, "x" * 73, "á" * 37):
            with self.assertRaises(InvalidPasswordError):
                hash_password(password)
        password = "á" * 36
        self.assertTrue(verify_password(password, hash_password(password)))

    def test_visible_getpass_fallback_is_blocked(self):
        def unavailable_terminal(*args, **kwargs):
            warnings.warn("hidden input unavailable", getpass.GetPassWarning)
            self.fail("No debe continuar hacia una entrada visible.")
        with patch("app.cli.getpass.getpass", side_effect=unavailable_terminal) as prompt:
            with self.assertRaises(cli.CLIError):
                cli._read_password()
            self.assertEqual(prompt.call_count, 1)


class AdminCLITests(unittest.TestCase):
    def setUp(self):
        self.connection = engine.connect()
        self.addCleanup(self.connection.close)
        self.transaction = self.connection.begin()
        self.addCleanup(lambda: self.transaction.rollback() if self.transaction.is_active else None)
        self.assertEqual(self.connection.scalar(text("SELECT current_database()")), "atenea_db")
        self.baseline = self._counts()
        self.factory = sessionmaker(bind=self.connection, join_transaction_mode="create_savepoint")
        self.factory_patch = patch("app.database.SessionLocal", self.factory)
        self.factory_patch.start()
        self.addCleanup(self.factory_patch.stop)
        suffix = uuid4().hex
        self.organization = "CLI test " + suffix
        self.email = suffix + "@example.invalid"
        self.password = secrets.token_urlsafe(24)

    def _counts(self):
        return tuple(self.connection.scalar(select(func.count()).select_from(model)) for model in (Organization, User))

    def tearDown(self):
        self.transaction.rollback()
        self.assertEqual(self._counts(), self.baseline, "No deben quedar filas de prueba.")

    def run_cli(self, arguments, *, inputs=(), passwords=None):
        output = io.StringIO()
        passwords = passwords if passwords is not None else [self.password, self.password]
        with redirect_stdout(output), redirect_stderr(output), patch(
            "builtins.input", side_effect=inputs,
        ), patch("app.cli.getpass.getpass", side_effect=passwords):
            status = cli.main(arguments)
        self.assertTrue(self.password not in output.getvalue())
        return status, output.getvalue()

    def create(self, organization=None, email=None):
        return self.run_cli([
            "create-admin", "--organization", organization or self.organization,
            "--full-name", "Administrador de prueba", "--email", email or self.email,
        ])

    def test_interactive_create_and_list(self):
        status, creation_output = self.run_cli(
            ["create-admin"], inputs=[self.organization, "Administrador de prueba", self.email],
        )
        self.assertEqual(status, 0)
        with self.factory() as db:
            organization = db.scalar(select(Organization).where(Organization.name == self.organization))
            self.assertIsNotNone(organization)
            user = db.scalar(select(User).where(User.organization_id == organization.id))
            self.assertIsNotNone(user)
            self.assertEqual(user.role, "admin")
            self.assertEqual(user.full_name, "Administrador de prueba")
            self.assertEqual(user.email, self.email)
            self.assertTrue(user.is_active and organization.is_active)
            self.assertTrue(user.hashed_password != self.password)
            self.assertTrue(verify_password(self.password, user.hashed_password))
            self.assertTrue(user.hashed_password not in creation_output)
            status, listing = self.run_cli(["list-users"])
            self.assertEqual(status, 0)
            self.assertTrue(all(value in listing for value in (
                str(user.id), self.organization, user.full_name, self.email, "admin", "activo",
            )))
            self.assertTrue(user.hashed_password not in listing)
            user.is_active = False
            db.commit()
        self.assertIn("inactivo", self.run_cli(["list-users"])[1])

    def test_reuse_organization_and_reject_duplicate_without_changing_user(self):
        self.assertEqual(self.create()[0], 0)
        with self.factory() as db:
            user = db.scalar(select(User).where(User.email == self.email))
            organization_id = user.organization_id
            original_hash = user.hashed_password
            user.role = "user"
            db.commit()
        self.assertEqual(self.create("  " + self.organization.upper() + "  ", self.email.upper())[0], 1)
        second_email = "second-" + self.email
        self.assertEqual(self.create(self.organization.upper(), second_email)[0], 0)
        with self.factory() as db:
            users = db.scalars(select(User).where(User.organization_id == organization_id).order_by(User.id)).all()
            self.assertEqual(len(users), 2)
            self.assertEqual(users[0].role, "user")
            self.assertTrue(users[0].hashed_password == original_hash)
            self.assertEqual(users[1].role, "admin")
        self.assertEqual(self._counts()[0], self.baseline[0] + 1)

    def test_same_email_in_different_organizations(self):
        self.assertEqual(self.create()[0], 0)
        self.assertEqual(self.create(self.organization + " second")[0], 0)
        with self.factory() as db:
            users = db.scalars(select(User).where(User.email == self.email)).all()
            self.assertEqual(len(users), 2)
            self.assertNotEqual(users[0].organization_id, users[1].organization_id)

    def test_invalid_input_and_confirmation_do_not_create_rows(self):
        self.assertEqual(self.create(email="invalid")[0], 1)
        self.assertEqual(self.run_cli(
            ["create-admin"], inputs=[self.organization, "Test", self.email],
            passwords=[self.password, self.password + "!"],
        )[0], 1)
        self.assertEqual(self.run_cli(
            ["create-admin"], inputs=[self.organization, "Test", self.email],
            passwords=["short", "short"],
        )[0], 1)
        self.assertEqual(self._counts(), self.baseline)

    def test_inactive_and_ambiguous_organizations_are_rejected(self):
        with self.factory() as db, db.begin():
            db.add(Organization(name=self.organization, is_active=False))
        self.assertEqual(self.create()[0], 1)
        with self.factory() as db, db.begin():
            db.add(Organization(name=self.organization.upper(), is_active=True))
        self.assertEqual(self.create()[0], 1)
        self.assertEqual(self._counts()[1], self.baseline[1])

    def test_failure_after_organization_insert_rolls_back(self):
        from sqlalchemy.exc import SQLAlchemyError
        with patch("sqlalchemy.orm.Session.scalar", side_effect=SQLAlchemyError("test failure")):
            status, output = self.create()
        self.assertEqual(status, 1)
        self.assertNotIn("test failure", output)
        self.assertEqual(self._counts(), self.baseline)


if __name__ == "__main__":
    unittest.main()
