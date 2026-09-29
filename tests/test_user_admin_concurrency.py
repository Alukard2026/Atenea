"""Dos transacciones reales: nunca retirar simultáneamente los dos últimos admins.

Usa un esquema exclusivo de prueba y lo elimina al terminar; no toca cuentas reales.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import unittest
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.database import Base, engine
from app.models import Organization, User
from app.users import save_user
from app.worklog import FormError


class LastAdminConcurrencyTests(unittest.TestCase):
    def test_two_simultaneous_self_demotions_leave_one_admin(self):
        schema = "test_admin_race_" + uuid4().hex
        with engine.begin() as c:
            c.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            c.execute(text("SELECT set_config('search_path', :schema, true)"), {"schema": schema})
            Base.metadata.create_all(c)
            with Session(bind=c) as db:
                org = Organization(name="Concurrency fixture")
                members = [User(organization=org, email=f"admin-{i}@example.invalid", full_name=f"Admin {i}", hashed_password="unused", role="admin", is_active=True) for i in (1, 2)]
                db.add_all(members)
                db.flush()
                identities = [u.id for u in members]
        try:
            ready = Barrier(2)
            def demote(identity):
                with engine.connect() as c, c.begin():
                    c.execute(text("SELECT set_config('search_path', :schema, true)"), {"schema": schema})
                    c.execute(text("SET LOCAL lock_timeout = '10s'"))
                    with Session(bind=c) as db:
                        admin = db.get(User, identity)
                        ready.wait(timeout=10)
                        try:
                            save_user(db, admin, {"full_name": admin.full_name, "email": admin.email, "role": "user", "is_active": "yes"}, identity)
                        except FormError:
                            return "rejected"
                        return "saved"
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(demote, identities))
            self.assertEqual(sorted(results), ["rejected", "saved"])
            with engine.begin() as c:
                c.execute(text("SELECT set_config('search_path', :schema, true)"), {"schema": schema})
                self.assertEqual(c.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))), 1)
        finally:
            # El nombre procede exclusivamente del prefijo fijo y uuid4, nunca del usuario.
            with engine.begin() as c:
                c.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
