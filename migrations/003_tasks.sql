-- Migración aditiva. Ejecutar mediante app.migrate dentro de una transacción.
CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    user_id INTEGER NOT NULL,
    client_id INTEGER,
    project_id INTEGER,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    due_date DATE,
    due_time TIME WITHOUT TIME ZONE,
    priority VARCHAR(20) NOT NULL DEFAULT 'normal',
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    reminder_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    source_type VARCHAR(50),
    source_id VARCHAR(1024),
    CONSTRAINT fk_tasks_org_user FOREIGN KEY (organization_id, user_id) REFERENCES users(organization_id, id),
    CONSTRAINT fk_tasks_org_client FOREIGN KEY (organization_id, client_id) REFERENCES clients(organization_id, id),
    CONSTRAINT fk_tasks_org_client_project FOREIGN KEY (organization_id, client_id, project_id) REFERENCES projects(organization_id, client_id, id),
    CONSTRAINT ck_tasks_project_client CHECK (project_id IS NULL OR client_id IS NOT NULL),
    CONSTRAINT ck_tasks_time_date CHECK (due_time IS NULL OR due_date IS NOT NULL),
    CONSTRAINT ck_tasks_priority CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    CONSTRAINT ck_tasks_status CHECK (status IN ('pending', 'completed', 'cancelled')),
    CONSTRAINT ck_tasks_completion CHECK ((status = 'completed' AND completed_at IS NOT NULL) OR (status <> 'completed' AND completed_at IS NULL)),
    CONSTRAINT ck_tasks_title CHECK (length(trim(title)) > 0),
    CONSTRAINT ck_tasks_source CHECK ((source_type IS NULL AND source_id IS NULL) OR (source_type IS NOT NULL AND source_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_tasks_org_user_status_due ON tasks (organization_id, user_id, status, due_date);
CREATE INDEX IF NOT EXISTS ix_tasks_org_user_reminder ON tasks (organization_id, user_id, status, reminder_at);
CREATE INDEX IF NOT EXISTS ix_tasks_org_client ON tasks (organization_id, client_id);
CREATE INDEX IF NOT EXISTS ix_tasks_org_project ON tasks (organization_id, project_id);
