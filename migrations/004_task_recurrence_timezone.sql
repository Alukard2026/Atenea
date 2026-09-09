-- Aditiva. app.migrate aplica restricciones y verifica dentro de la misma transacción.
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS timezone VARCHAR(100) NOT NULL DEFAULT 'America/El_Salvador';
ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS recurrence_type VARCHAR(20) NOT NULL DEFAULT 'none',
    ADD COLUMN IF NOT EXISTS recurrence_interval INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS recurrence_end_date DATE,
    ADD COLUMN IF NOT EXISTS recurrence_anchor_date DATE,
    ADD COLUMN IF NOT EXISTS recurrence_index INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS parent_task_id INTEGER;
