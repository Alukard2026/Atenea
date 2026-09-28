-- Aditiva: no inferir ni sobrescribir datos de clientes existentes.
ALTER TABLE clients
    ADD COLUMN IF NOT EXISTS email_domain VARCHAR(253),
    ADD COLUMN IF NOT EXISTS client_type VARCHAR(30) NOT NULL DEFAULT 'otro';
