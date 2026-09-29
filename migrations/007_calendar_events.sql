-- 007: aditiva y repetible. No modifica tablas existentes.

CREATE TABLE IF NOT EXISTS calendar_events (
	id SERIAL NOT NULL, 
	organization_id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	event_type VARCHAR(20) NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	description TEXT, 
	client_id INTEGER, 
	project_id INTEGER, 
	start_at TIMESTAMP WITH TIME ZONE, 
	end_at TIMESTAMP WITH TIME ZONE, 
	start_date DATE, 
	end_date DATE, 
	all_day BOOLEAN DEFAULT false NOT NULL, 
	location VARCHAR(255), 
	institution VARCHAR(255), 
	status VARCHAR(20) DEFAULT 'scheduled' NOT NULL, 
	reminder_at TIMESTAMP WITH TIME ZONE, 
	source_type VARCHAR(50), 
	source_id VARCHAR(1024), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT fk_calendar_owner FOREIGN KEY(organization_id, user_id) REFERENCES users (organization_id, id), 
	CONSTRAINT fk_calendar_client FOREIGN KEY(organization_id, client_id) REFERENCES clients (organization_id, id), 
	CONSTRAINT fk_calendar_project FOREIGN KEY(organization_id, client_id, project_id) REFERENCES projects (organization_id, client_id, id), 
	CONSTRAINT ck_calendar_project_client CHECK (project_id IS NULL OR client_id IS NOT NULL), 
	CONSTRAINT ck_calendar_type CHECK (event_type IN ('hearing', 'meeting', 'appointment', 'reminder', 'other')), 
	CONSTRAINT ck_calendar_status CHECK (status IN ('scheduled', 'cancelled')), 
	CONSTRAINT ck_calendar_title CHECK (length(trim(title)) > 0), 
	CONSTRAINT ck_calendar_allday_end CHECK (NOT all_day OR end_date IS NOT NULL), 
	CONSTRAINT ck_calendar_dates CHECK ((all_day AND start_date IS NOT NULL AND end_date > start_date AND start_at IS NULL AND end_at IS NULL) OR (NOT all_day AND start_at IS NOT NULL AND (end_at IS NULL OR end_at > start_at) AND start_date IS NULL AND end_date IS NULL)), 
	CONSTRAINT ck_calendar_source CHECK ((source_type IS NULL AND source_id IS NULL) OR (source_type IS NOT NULL AND source_id IS NOT NULL)), 
	FOREIGN KEY(organization_id) REFERENCES organizations (id)
)

;
CREATE INDEX IF NOT EXISTS ix_calendar_client ON calendar_events (organization_id, client_id);
CREATE INDEX IF NOT EXISTS ix_calendar_owner_day ON calendar_events (organization_id, user_id, status, start_date);
CREATE INDEX IF NOT EXISTS ix_calendar_owner_reminder ON calendar_events (organization_id, user_id, status, reminder_at);
CREATE INDEX IF NOT EXISTS ix_calendar_owner_start ON calendar_events (organization_id, user_id, status, start_at);
CREATE INDEX IF NOT EXISTS ix_calendar_project ON calendar_events (organization_id, project_id);
