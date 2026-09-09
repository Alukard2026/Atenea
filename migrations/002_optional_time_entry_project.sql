-- Conserva filas y claves foráneas. MATCH SIMPLE permite project_id NULL,
-- mientras las FK independientes de cliente, usuario y organización permanecen.
ALTER TABLE time_entries ALTER COLUMN project_id DROP NOT NULL;
