-- Ejecutar mediante app.migrate: comprueba duplicados con bloqueo antes del índice.
-- No normalizar ni eliminar filas históricas silenciosamente.
ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_version INTEGER NOT NULL DEFAULT 0;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'users'::regclass AND conname = 'ck_users_auth_version'
    ) THEN
        ALTER TABLE users ADD CONSTRAINT ck_users_auth_version CHECK (auth_version >= 0);
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_global ON users (lower(btrim(email)));
