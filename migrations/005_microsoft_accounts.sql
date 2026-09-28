-- Aditiva y transaccional. No modifica usuarios ni organizaciones existentes.
CREATE TABLE IF NOT EXISTS microsoft_accounts (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    user_id INTEGER NOT NULL,
    microsoft_account_id VARCHAR(255) NOT NULL,
    principal_name VARCHAR(320) NOT NULL,
    email VARCHAR(320),
    display_name VARCHAR(255),
    tenant_id VARCHAR(255),
    connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active BOOLEAN NOT NULL DEFAULT true,
    token_cache_encrypted TEXT,
    CONSTRAINT uq_microsoft_accounts_owner UNIQUE (organization_id, user_id),
    CONSTRAINT fk_microsoft_accounts_owner FOREIGN KEY (organization_id, user_id) REFERENCES users(organization_id, id),
    CONSTRAINT ck_microsoft_accounts_cache CHECK (NOT is_active OR token_cache_encrypted IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS microsoft_oauth_flows (
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    user_id INTEGER NOT NULL,
    state_hash VARCHAR(64) NOT NULL,
    session_hash VARCHAR(64) NOT NULL,
    flow_encrypted TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (organization_id, user_id),
    CONSTRAINT fk_microsoft_oauth_flows_owner FOREIGN KEY (organization_id, user_id) REFERENCES users(organization_id, id)
);
