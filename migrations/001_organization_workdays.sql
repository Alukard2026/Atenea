-- Migración aditiva. El ejecutor app.migrate aporta transacción y bloqueo.
-- IF NOT EXISTS permite repetirla sin sobrescribir la configuración guardada.
ALTER TABLE organizations
    ADD COLUMN IF NOT EXISTS workday_monday BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS workday_tuesday BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS workday_wednesday BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS workday_thursday BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS workday_friday BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS workday_saturday BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS workday_sunday BOOLEAN NOT NULL DEFAULT FALSE;
