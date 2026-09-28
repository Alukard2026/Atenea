"""Migración 006 en esquema aislado y transacción revertida."""

import unittest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.migrate import migrate_client_domains
import test_task_migration


class ClientDomainsMigrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_task_migration.TaskMigrationTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.db, self.schema = self.fixture.connection, self.fixture.schema
        self.db.execute(text("INSERT INTO clients(id, organization_id) VALUES (1, 1), (2, 1), (3, 2)"))

    def test_additive_defaults_repeat_and_preserve(self):
        migrate_client_domains(self.db)
        rows = self.db.execute(text("SELECT id, email_domain, client_type FROM clients ORDER BY id")).all()
        self.assertEqual([tuple(r) for r in rows], [(1, None, "otro"), (2, None, "otro"), (3, None, "otro")])
        self.db.execute(text("UPDATE clients SET email_domain='dma.com.sv', client_type='empresa' WHERE id=1"))
        migrate_client_domains(self.db)
        self.assertEqual(tuple(self.db.execute(text("SELECT email_domain, client_type FROM clients WHERE id=1")).one()), ("dma.com.sv", "empresa"))

    def test_unique_in_org_but_same_domain_in_other_org(self):
        migrate_client_domains(self.db)
        self.db.execute(text("UPDATE clients SET email_domain='dma.com.sv' WHERE id IN (1, 3)"))
        with self.assertRaises(IntegrityError), self.db.begin_nested():
            self.db.execute(text("UPDATE clients SET email_domain='dma.com.sv' WHERE id=2"))

    def test_invalid_type_and_non_normalized_domain_rejected(self):
        migrate_client_domains(self.db)
        for sql in ("UPDATE clients SET client_type='unknown'", "UPDATE clients SET email_domain='DMA.COM.SV'", "UPDATE clients SET client_type=NULL"):
            with self.assertRaises(IntegrityError), self.db.begin_nested():
                self.db.execute(text(sql))

    def test_rollback_and_incompatible_schema(self):
        savepoint = self.db.begin_nested()
        migrate_client_domains(self.db)
        savepoint.rollback()
        self.assertNotIn("email_domain", {c["name"] for c in inspect(self.db).get_columns("clients", schema=self.schema)})
        self.db.execute(text("ALTER TABLE clients ADD COLUMN email_domain TEXT"))
        with self.assertRaises(RuntimeError), self.db.begin_nested():
            migrate_client_domains(self.db)
