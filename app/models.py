"""Modelos del MVP con integridad referencial por organización.

Al crear entidades, asignar siempre organization u organization_id. Las relaciones
con usuarios, clientes y proyectos sincronizan sus IDs; las claves compuestas
rechazan referencias que no correspondan a la organización indicada.
"""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    false,
    func,
    true,
)
from sqlalchemy.orm import deferred, relationship, validates

from app.database import Base


class MicrosoftAccount(Base):
    __tablename__ = "microsoft_accounts"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_microsoft_accounts_owner"),
        ForeignKeyConstraint(["organization_id", "user_id"], ["users.organization_id", "users.id"], name="fk_microsoft_accounts_owner"),
        CheckConstraint("NOT is_active OR token_cache_encrypted IS NOT NULL", name="ck_microsoft_accounts_cache"),
    )
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    user_id = Column(Integer, nullable=False)
    microsoft_account_id = Column(String(255), nullable=False)
    principal_name = Column(String(320), nullable=False)
    email = Column(String(320), nullable=True)
    display_name = Column(String(255), nullable=True)
    tenant_id = Column(String(255), nullable=True)
    connected_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    is_active = Column(Boolean, nullable=False, server_default=true())
    token_cache_encrypted = deferred(Column(Text, nullable=True), raiseload=True)


class MicrosoftOAuthFlow(Base):
    """Un único intento pendiente por propietario; nunca guardar el flujo en la cookie."""
    __tablename__ = "microsoft_oauth_flows"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "user_id"], ["users.organization_id", "users.id"], name="fk_microsoft_oauth_flows_owner"),
    )
    organization_id = Column(Integer, ForeignKey("organizations.id"), primary_key=True)
    user_id = Column(Integer, primary_key=True)
    state_hash = Column(String(64), nullable=False)
    session_hash = Column(String(64), nullable=False)
    flow_encrypted = Column(Text, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    timezone = Column(String(100), nullable=False, server_default="America/El_Salvador")

    @validates("timezone")
    def validate_timezone(self, key, value):
        from app.timezones import valid_timezone
        return valid_timezone(value)
    is_active = Column(Boolean, nullable=False, server_default=true())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # Cada día es independiente; los defaults solo definen la configuración inicial.
    workday_monday = Column(Boolean, nullable=False, server_default=true())
    workday_tuesday = Column(Boolean, nullable=False, server_default=true())
    workday_wednesday = Column(Boolean, nullable=False, server_default=true())
    workday_thursday = Column(Boolean, nullable=False, server_default=true())
    workday_friday = Column(Boolean, nullable=False, server_default=true())
    workday_saturday = Column(Boolean, nullable=False, server_default=false())
    workday_sunday = Column(Boolean, nullable=False, server_default=false())

    users = relationship("User", back_populates="organization")
    clients = relationship("Client", back_populates="organization")
    projects = relationship("Project", back_populates="organization")
    time_entries = relationship("TimeEntry", back_populates="organization")
    tasks = relationship("Task", back_populates="organization")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_users_org_id"),
    )

    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    email = Column(String(320), nullable=False)
    full_name = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    # Valores esperados por la futura capa de aplicación: admin, user.
    role = Column(String(20), nullable=False, server_default="user")
    is_active = Column(Boolean, nullable=False, server_default=true())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    organization = relationship("Organization", back_populates="users")
    tasks = relationship("Task", back_populates="user", primaryjoin="and_(User.organization_id == Task.organization_id, User.id == foreign(Task.user_id))")
    time_entries = relationship(
        "TimeEntry",
        back_populates="user",
        primaryjoin="and_(User.organization_id == TimeEntry.organization_id, "
        "User.id == foreign(TimeEntry.user_id))",
    )

    # La misma dirección puede existir en empresas distintas.
    __table_args__ += (
        Index("uq_users_org_email", organization_id, func.lower(email), unique=True),
    )


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_clients_org_id"),
        UniqueConstraint("organization_id", "email_domain", name="uq_clients_org_email_domain"),
        CheckConstraint("client_type IN ('empresa', 'institucion_publica', 'otro')", name="ck_clients_type"),
        CheckConstraint("email_domain IS NULL OR (email_domain = lower(btrim(email_domain)) AND email_domain ~ '^[a-z0-9][a-z0-9.-]*[.][a-z0-9-]+$')", name="ck_clients_email_domain"),
    )

    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    code = Column(String(50), nullable=True)
    email_domain = Column(String(253), nullable=True)
    client_type = Column(String(30), nullable=False, server_default="otro")

    @validates("email_domain")
    def validate_email_domain(self, key, value):
        from app.client_domains import normalize_domain
        return normalize_domain(value)

    is_active = Column(Boolean, nullable=False, server_default=true())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    organization = relationship("Organization", back_populates="clients")
    tasks = relationship("Task", back_populates="client", primaryjoin="and_(Client.organization_id == Task.organization_id, Client.id == foreign(Task.client_id))")
    projects = relationship(
        "Project",
        back_populates="client",
        primaryjoin="and_(Client.organization_id == Project.organization_id, "
        "Client.id == foreign(Project.client_id))",
    )
    time_entries = relationship(
        "TimeEntry",
        back_populates="client",
        primaryjoin="and_(Client.organization_id == TimeEntry.organization_id, "
        "Client.id == foreign(TimeEntry.client_id))",
    )


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "client_id"],
            ["clients.organization_id", "clients.id"],
            name="fk_projects_org_client",
        ),
        UniqueConstraint("organization_id", "client_id", "id", name="uq_projects_org_client_id"),
        Index("ix_projects_org_client", "organization_id", "client_id"),
    )

    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    client_id = Column(Integer, nullable=False)
    name = Column(String(255), nullable=False)
    code = Column(String(50), nullable=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, server_default=true())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    organization = relationship("Organization", back_populates="projects")
    tasks = relationship("Task", back_populates="project", primaryjoin="and_(Project.organization_id == Task.organization_id, Project.client_id == Task.client_id, Project.id == foreign(Task.project_id))")
    client = relationship(
        "Client",
        back_populates="projects",
        primaryjoin="and_(Project.organization_id == Client.organization_id, "
        "foreign(Project.client_id) == Client.id)",
    )
    time_entries = relationship(
        "TimeEntry",
        back_populates="project",
        primaryjoin="and_(Project.organization_id == TimeEntry.organization_id, "
        "Project.client_id == TimeEntry.client_id, Project.id == foreign(TimeEntry.project_id))",
    )


class TimeEntry(Base):
    __tablename__ = "time_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "user_id"],
            ["users.organization_id", "users.id"],
            name="fk_time_entries_org_user",
        ),
        ForeignKeyConstraint(
            ["organization_id", "client_id"],
            ["clients.organization_id", "clients.id"],
            name="fk_time_entries_org_client",
        ),
        ForeignKeyConstraint(
            ["organization_id", "client_id", "project_id"],
            ["projects.organization_id", "projects.client_id", "projects.id"],
            name="fk_time_entries_org_client_project",
        ),
        CheckConstraint("hours > 0", name="ck_time_entries_hours_positive"),
        Index("ix_time_entries_org_date", "organization_id", "work_date"),
        Index("ix_time_entries_org_user_date", "organization_id", "user_id", "work_date"),
        Index("ix_time_entries_org_client_date", "organization_id", "client_id", "work_date"),
        Index("ix_time_entries_org_project_date", "organization_id", "project_id", "work_date"),
    )

    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(Integer, nullable=False)
    client_id = Column(Integer, nullable=False)
    project_id = Column(Integer, nullable=True)
    work_date = Column(Date, nullable=False)
    activity = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    hours = Column(Numeric(10, 2), nullable=False)
    billable = Column(Boolean, nullable=False, server_default=true())
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )

    organization = relationship("Organization", back_populates="time_entries")
    user = relationship(
        "User",
        back_populates="time_entries",
        primaryjoin="and_(TimeEntry.organization_id == User.organization_id, "
        "foreign(TimeEntry.user_id) == User.id)",
    )
    client = relationship(
        "Client",
        back_populates="time_entries",
        primaryjoin="and_(TimeEntry.organization_id == Client.organization_id, "
        "foreign(TimeEntry.client_id) == Client.id)",
    )
    project = relationship(
        "Project",
        back_populates="time_entries",
        primaryjoin="and_(TimeEntry.organization_id == Project.organization_id, "
        "TimeEntry.client_id == Project.client_id, foreign(TimeEntry.project_id) == Project.id)",
    )


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", "id", name="uq_tasks_org_user_id"),
        UniqueConstraint("parent_task_id", name="uq_tasks_parent"),
        ForeignKeyConstraint(["organization_id", "user_id", "parent_task_id"], ["tasks.organization_id", "tasks.user_id", "tasks.id"], name="fk_tasks_parent_owner"),
        CheckConstraint("recurrence_type IN ('none', 'daily', 'weekly', 'monthly', 'yearly')", name="ck_tasks_recurrence_type"),
        CheckConstraint("recurrence_interval BETWEEN 1 AND 365", name="ck_tasks_recurrence_interval"),
        CheckConstraint("recurrence_index >= 0", name="ck_tasks_recurrence_index"),
        CheckConstraint("recurrence_type = 'none' OR (due_date IS NOT NULL AND recurrence_anchor_date IS NOT NULL)", name="ck_tasks_recurrence_date"),
        CheckConstraint("recurrence_end_date IS NULL OR (recurrence_anchor_date IS NOT NULL AND recurrence_end_date >= recurrence_anchor_date)", name="ck_tasks_recurrence_end"),
        ForeignKeyConstraint(["organization_id", "user_id"], ["users.organization_id", "users.id"], name="fk_tasks_org_user"),
        ForeignKeyConstraint(["organization_id", "client_id"], ["clients.organization_id", "clients.id"], name="fk_tasks_org_client"),
        ForeignKeyConstraint(["organization_id", "client_id", "project_id"], ["projects.organization_id", "projects.client_id", "projects.id"], name="fk_tasks_org_client_project"),
        CheckConstraint("project_id IS NULL OR client_id IS NOT NULL", name="ck_tasks_project_client"),
        CheckConstraint("due_time IS NULL OR due_date IS NOT NULL", name="ck_tasks_time_date"),
        CheckConstraint("priority IN ('low', 'normal', 'high', 'urgent')", name="ck_tasks_priority"),
        CheckConstraint("status IN ('pending', 'completed', 'cancelled')", name="ck_tasks_status"),
        CheckConstraint("(status = 'completed' AND completed_at IS NOT NULL) OR (status <> 'completed' AND completed_at IS NULL)", name="ck_tasks_completion"),
        CheckConstraint("length(trim(title)) > 0", name="ck_tasks_title"),
        CheckConstraint("(source_type IS NULL AND source_id IS NULL) OR (source_type IS NOT NULL AND source_id IS NOT NULL)", name="ck_tasks_source"),
        Index("ix_tasks_org_user_status_due", "organization_id", "user_id", "status", "due_date"),
        Index("ix_tasks_org_user_reminder", "organization_id", "user_id", "status", "reminder_at"),
        Index("ix_tasks_org_client", "organization_id", "client_id"),
        Index("ix_tasks_org_project", "organization_id", "project_id"),
    )

    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    user_id = Column(Integer, nullable=False)
    client_id = Column(Integer, nullable=True)
    project_id = Column(Integer, nullable=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    due_date = Column(Date, nullable=True)
    due_time = Column(Time, nullable=True)  # Hora civil en la zona de la organización.
    recurrence_type = Column(String(20), nullable=False, server_default="none")
    recurrence_interval = Column(Integer, nullable=False, server_default="1")
    recurrence_end_date = Column(Date, nullable=True)
    recurrence_anchor_date = Column(Date, nullable=True)
    recurrence_index = Column(Integer, nullable=False, server_default="0")
    parent_task_id = Column(Integer, nullable=True)
    priority = Column(String(20), nullable=False, server_default="normal")
    status = Column(String(20), nullable=False, server_default="pending")
    reminder_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    source_type = Column(String(50), nullable=True)
    source_id = Column(String(1024), nullable=True)

    organization = relationship("Organization", back_populates="tasks")
    user = relationship("User", back_populates="tasks", primaryjoin="and_(Task.organization_id == User.organization_id, foreign(Task.user_id) == User.id)")
    client = relationship("Client", back_populates="tasks", primaryjoin="and_(Task.organization_id == Client.organization_id, foreign(Task.client_id) == Client.id)")
    project = relationship("Project", back_populates="tasks", primaryjoin="and_(Task.organization_id == Project.organization_id, Task.client_id == Project.client_id, foreign(Task.project_id) == Project.id)")
