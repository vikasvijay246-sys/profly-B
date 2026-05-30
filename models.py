"""
PropFlow Models — Production-Grade
All times stored in UTC; use to_ist() for display.
"""
from datetime import datetime, timezone, timedelta
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import Index, UniqueConstraint, CheckConstraint, text

db = SQLAlchemy()

# ── Time helpers ──────────────────────────────────────────────────────────────
def now_utc():
    """Always store UTC in DB."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

def to_ist(dt):
    """Convert UTC datetime → IST string for display (DD-MM-YYYY HH:MM AM/PM)."""
    if not dt:
        return None
    ist = dt + timedelta(hours=5, minutes=30)
    return ist.strftime("%d-%m-%Y %I:%M %p")

def to_ist_time_only(dt):
    """Convert UTC datetime → IST time string (HH:MM AM/PM)."""
    if not dt:
        return None
    ist = dt + timedelta(hours=5, minutes=30)
    return ist.strftime("%I:%M %p")

def current_rent_month():
    """Return current month label: e.g. '2025-06'."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


# ── User ──────────────────────────────────────────────────────────────────────
class User(UserMixin, db.Model):
    __tablename__ = "users"

    id            = db.Column(db.Integer, primary_key=True)
    phone         = db.Column(db.String(30),  unique=True, nullable=False)
    full_name     = db.Column(db.String(150), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role          = db.Column(db.String(10),  nullable=False, default="tenant")  # admin|owner|tenant|worker
    is_active     = db.Column(db.Boolean, default=True,  nullable=False)
    # owner_id: who created this tenant / which owner manages this tenant
    owner_id      = db.Column(db.Integer,
                               db.ForeignKey("users.id", ondelete="SET NULL"),
                               nullable=True)
    # Profile fields
    address       = db.Column(db.Text, nullable=True)  # For verification
    photo         = db.Column(db.String(255), nullable=True)  # Path to profile photo
    proof_id      = db.Column(db.String(255), nullable=True)  # Path to ID proof document
    is_verified   = db.Column(db.Boolean, default=False, nullable=False)  # Verification status
    # Professional tenant-facing ID (set when assigned to a room), e.g. TNR-SUNB-A203-4821
    tenant_public_id = db.Column(db.String(48), nullable=True, unique=True)
    designation    = db.Column(db.String(40), nullable=True)  # Student, Employee, etc.
    created_at    = db.Column(db.DateTime, default=now_utc, nullable=False)
    updated_at    = db.Column(db.DateTime, default=now_utc, onupdate=now_utc, nullable=False)

    # ── Relationships ──────────────────────────────────────────────────────────
    owned_tenants     = db.relationship(
        "User", foreign_keys=[owner_id],
        backref=db.backref("owner_user", remote_side=[id]),
        lazy="dynamic"
    )
    properties        = db.relationship("Property",       back_populates="owner",
                                        foreign_keys="Property.owner_id",    lazy="dynamic")
    tenancies         = db.relationship("PropertyTenant", back_populates="tenant",
                                        foreign_keys="PropertyTenant.tenant_id", lazy="dynamic")
    payments          = db.relationship("Payment",        back_populates="tenant",
                                        foreign_keys="Payment.tenant_id",    lazy="dynamic")
    notifications     = db.relationship("Notification",   back_populates="user",   lazy="dynamic")
    sent_messages     = db.relationship("Message", back_populates="sender",
                                        foreign_keys="Message.sender_id",    lazy="dynamic")
    received_messages = db.relationship("Message", back_populates="receiver",
                                        foreign_keys="Message.receiver_id",  lazy="dynamic")
    room_assignments  = db.relationship("RoomTenant", back_populates="tenant",
                                        foreign_keys="RoomTenant.tenant_id", lazy="dynamic")

    # ── Methods ────────────────────────────────────────────────────────────────
    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    def to_dict(self):
        return {
            "id": self.id, "phone": self.phone, "full_name": self.full_name,
            "role": self.role, "is_active": self.is_active, "owner_id": self.owner_id,
            "tenant_public_id": self.tenant_public_id,
            "designation": self.designation,
            "created_at": to_ist(self.created_at),
        }

    __table_args__ = (
        Index("ix_users_phone", "phone"),
        Index("ix_users_role",  "role"),
        Index("ix_users_tenant_public_id", "tenant_public_id"),
        # composite: fast owner→tenant queries
        Index("ix_users_owner_role", "owner_id", "role"),
    )


# ── Tenant Trash Archive ──────────────────────────────────────────────────────
class TenantTrash(db.Model):
    __tablename__ = "tenant_trash"

    id               = db.Column(db.Integer, primary_key=True)
    phone            = db.Column(db.String(30),  nullable=False)
    full_name        = db.Column(db.String(150), nullable=False)
    password_hash    = db.Column(db.String(255), nullable=False)
    role             = db.Column(db.String(10),  nullable=False, default="tenant")
    is_active        = db.Column(db.Boolean, default=False, nullable=False)
    owner_id         = db.Column(db.Integer, nullable=True)
    address          = db.Column(db.Text, nullable=True)
    photo            = db.Column(db.String(255), nullable=True)
    proof_id         = db.Column(db.String(255), nullable=True)
    is_verified      = db.Column(db.Boolean, default=False, nullable=False)
    tenant_public_id = db.Column(db.String(48), nullable=True)
    designation      = db.Column(db.String(40), nullable=True)
    created_at       = db.Column(db.DateTime, default=now_utc, nullable=False)
    updated_at       = db.Column(db.DateTime, default=now_utc, onupdate=now_utc, nullable=False)
    deleted_at       = db.Column(db.DateTime, default=now_utc, nullable=False)
    auto_delete_date = db.Column(db.DateTime, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "phone": self.phone,
            "full_name": self.full_name,
            "role": self.role,
            "is_active": self.is_active,
            "owner_id": self.owner_id,
            "tenant_public_id": self.tenant_public_id,
            "designation": self.designation,
            "created_at": to_ist(self.created_at),
            "deleted_at": to_ist(self.deleted_at),
            "auto_delete_date": to_ist(self.auto_delete_date),
        }

    __table_args__ = (
        Index("ix_tenant_trash_owner_id", "owner_id"),
        Index("ix_tenant_trash_phone", "phone"),
    )


# ── Vacate Request ─────────────────────────────────────────────────────────────
class VacateRequest(db.Model):
    __tablename__ = "vacate_requests"

    id             = db.Column(db.Integer, primary_key=True)
    request_id     = db.Column(db.String(32), nullable=False, unique=True)
    tenant_id      = db.Column(db.Integer,
                                db.ForeignKey("users.id", ondelete="CASCADE"),
                                nullable=False)
    owner_id       = db.Column(db.Integer,
                                db.ForeignKey("users.id", ondelete="CASCADE"),
                                nullable=False)
    property_id    = db.Column(db.Integer,
                                db.ForeignKey("properties.id", ondelete="SET NULL"),
                                nullable=False)
    room_id        = db.Column(db.Integer,
                                db.ForeignKey("rooms.id", ondelete="SET NULL"),
                                nullable=True)
    room_number    = db.Column(db.String(20), nullable=True)
    vacate_date    = db.Column(db.Date, nullable=False)
    reason         = db.Column(db.Text, nullable=True)
    status         = db.Column(db.String(20), default="pending", nullable=False)
    submitted_at   = db.Column(db.DateTime, default=now_utc, nullable=False)
    processed_at   = db.Column(db.DateTime, nullable=True)
    decision_by    = db.Column(db.Integer,
                                db.ForeignKey("users.id", ondelete="SET NULL"),
                                nullable=True)
    decision_notes = db.Column(db.Text, nullable=True)
    vacated_at     = db.Column(db.DateTime, nullable=True)

    tenant   = db.relationship("User", foreign_keys=[tenant_id])
    owner    = db.relationship("User", foreign_keys=[owner_id])
    property = db.relationship("Property", foreign_keys=[property_id])
    room     = db.relationship("Room", foreign_keys=[room_id])

    def to_dict(self):
        return {
            "id": self.id,
            "request_id": self.request_id,
            "tenant_id": self.tenant_id,
            "owner_id": self.owner_id,
            "property_id": self.property_id,
            "room_id": self.room_id,
            "room_number": self.room_number,
            "vacate_date": self.vacate_date.isoformat() if self.vacate_date else None,
            "reason": self.reason,
            "status": self.status,
            "submitted_at": to_ist(self.submitted_at),
            "processed_at": to_ist(self.processed_at),
            "vacated_at": to_ist(self.vacated_at),
            "decision_by": self.decision_by,
            "decision_notes": self.decision_notes,
        }

    __table_args__ = (
        Index("ix_vacate_requests_owner_id", "owner_id"),
        Index("ix_vacate_requests_tenant_id", "tenant_id"),
        Index("ix_vacate_requests_status", "status"),
        Index("ix_vacate_requests_property_id", "property_id"),
    )


# ── Property ──────────────────────────────────────────────────────────────────
class Property(db.Model):
    __tablename__ = "properties"

    id            = db.Column(db.Integer, primary_key=True)
    name          = db.Column(db.String(200), nullable=False)
    address       = db.Column(db.Text,        nullable=False)
    city          = db.Column(db.String(100), nullable=False)
    state         = db.Column(db.String(50),  nullable=True)
    zip_code      = db.Column(db.String(20),  nullable=True)
    unit_number   = db.Column(db.String(50),  nullable=True)
    property_type = db.Column(db.String(50),  default="apartment")
    bedrooms      = db.Column(db.Integer,     nullable=True)
    bathrooms     = db.Column(db.Integer,     nullable=True)
    area_sqft     = db.Column(db.Float,       nullable=True)
    monthly_rent  = db.Column(db.Numeric(10, 2), nullable=False)
    description   = db.Column(db.Text,        nullable=True)
    status        = db.Column(db.String(20),  default="available", nullable=False)
    owner_id      = db.Column(db.Integer,
                               db.ForeignKey("users.id", ondelete="CASCADE"),
                               nullable=False)
    is_deleted    = db.Column(db.Boolean, default=False, nullable=False)
    # Short code for tenant IDs and search (e.g. SUNB, PG02); optional, auto-derived if empty
    short_code    = db.Column(db.String(16), nullable=True)
    created_at    = db.Column(db.DateTime, default=now_utc)
    updated_at    = db.Column(db.DateTime, default=now_utc, onupdate=now_utc)

    owner    = db.relationship("User",           back_populates="properties", foreign_keys=[owner_id])
    tenants  = db.relationship("PropertyTenant", back_populates="property",   lazy="dynamic")
    payments = db.relationship("Payment",        back_populates="property",   lazy="dynamic")
    rooms    = db.relationship("Room",           back_populates="prop",       lazy="dynamic")
    workers  = db.relationship("Worker",         secondary="worker_property_assignments",
                               back_populates="assigned_properties", lazy="dynamic")

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "address": self.address,
            "city": self.city, "state": self.state, "zip_code": self.zip_code,
            "unit_number": self.unit_number, "property_type": self.property_type,
            "bedrooms": self.bedrooms, "bathrooms": self.bathrooms,
            "area_sqft": float(self.area_sqft) if self.area_sqft else None,
            "monthly_rent": float(self.monthly_rent),
            "status": self.status, "owner_id": self.owner_id,
        }

    __table_args__ = (
        Index("ix_properties_owner_id",  "owner_id"),
        Index("ix_properties_status",    "status"),
    )


worker_property_assignments = db.Table(
    "worker_property_assignments",
    db.Column("worker_id", db.Integer,
              db.ForeignKey("workers.id", ondelete="CASCADE"),
              primary_key=True),
    db.Column("property_id", db.Integer,
              db.ForeignKey("properties.id", ondelete="CASCADE"),
              primary_key=True),
)


class Worker(db.Model):
    __tablename__ = "workers"

    id            = db.Column(db.Integer, primary_key=True)
    full_name     = db.Column(db.String(150), nullable=False)
    phone_number  = db.Column(db.String(30), nullable=False)
    email         = db.Column(db.String(120), nullable=True)
    role          = db.Column(db.String(80), nullable=False)
    salary_type   = db.Column(db.String(20), nullable=False, default="monthly")
    salary_amount = db.Column(db.Numeric(10, 2), nullable=True)
    active_status = db.Column(db.Boolean, default=True, nullable=False)
    is_temp       = db.Column(db.Boolean, default=False, nullable=False)
    can_login     = db.Column(db.Boolean, default=False, nullable=False)
    user_id       = db.Column(db.Integer,
                               db.ForeignKey("users.id", ondelete="SET NULL"),
                               nullable=True, unique=True)
    joined_date   = db.Column(db.Date, nullable=True)
    notes         = db.Column(db.Text, nullable=True)
    owner_id      = db.Column(db.Integer,
                               db.ForeignKey("users.id", ondelete="CASCADE"),
                               nullable=False)
    created_at    = db.Column(db.DateTime, default=now_utc, nullable=False)
    updated_at    = db.Column(db.DateTime, default=now_utc,
                               onupdate=now_utc, nullable=False)

    assigned_properties = db.relationship(
        "Property", secondary=worker_property_assignments,
        back_populates="workers", lazy="dynamic"
    )
    tasks = db.relationship("MaintenanceTask", back_populates="assigned_worker",
                            foreign_keys="MaintenanceTask.assigned_worker_id",
                            lazy="dynamic")
    complaints = db.relationship("TenantComplaint", back_populates="assigned_worker",
                                 foreign_keys="TenantComplaint.assigned_worker_id",
                                 lazy="dynamic")
    expenses = db.relationship("PropertyExpense", back_populates="worker",
                               foreign_keys="PropertyExpense.worker_id",
                               lazy="dynamic")
    owner = db.relationship("User", foreign_keys=[owner_id])
    portal_user = db.relationship("User", foreign_keys=[user_id],
                                  backref=db.backref("worker_profile", uselist=False))

    def portal_user_id(self):
        return self.user_id if self.can_login and self.active_status else None

    def to_dict(self):
        return {
            "id": self.id,
            "full_name": self.full_name,
            "phone_number": self.phone_number,
            "email": self.email,
            "role": self.role,
            "salary_type": self.salary_type,
            "salary_amount": float(self.salary_amount) if self.salary_amount else None,
            "active_status": self.active_status,
            "is_temp": self.is_temp,
            "can_login": self.can_login,
            "user_id": self.user_id,
            "joined_date": self.joined_date.isoformat() if self.joined_date else None,
            "notes": self.notes,
            "owner_id": self.owner_id,
            "created_at": to_ist(self.created_at),
            "updated_at": to_ist(self.updated_at),
        }

    __table_args__ = (
        Index("ix_workers_owner_id", "owner_id"),
        Index("ix_workers_role_owner", "role", "owner_id"),
        Index("ix_workers_user_id", "user_id"),
    )


class MaintenanceTask(db.Model):
    __tablename__ = "maintenance_tasks"

    id                 = db.Column(db.Integer, primary_key=True)
    title              = db.Column(db.String(220), nullable=False)
    description        = db.Column(db.Text, nullable=True)
    property_id        = db.Column(db.Integer,
                                   db.ForeignKey("properties.id", ondelete="SET NULL"),
                                   nullable=False)
    room_number        = db.Column(db.String(40), nullable=True)
    floor              = db.Column(db.String(40), nullable=True)
    quantity           = db.Column(db.String(60), nullable=True)
    scheduled_time     = db.Column(db.String(40), nullable=True)
    temp_worker_name   = db.Column(db.String(150), nullable=True)
    amount             = db.Column(db.Numeric(10, 2), nullable=True)
    pay_status         = db.Column(db.String(20), default="none", nullable=False)
    assigned_worker_id = db.Column(db.Integer,
                                   db.ForeignKey("workers.id", ondelete="SET NULL"),
                                   nullable=True)
    priority           = db.Column(db.String(20), default="medium", nullable=False)
    status             = db.Column(db.String(20), default="pending", nullable=False)
    created_by_owner_id= db.Column(db.Integer,
                                   db.ForeignKey("users.id", ondelete="CASCADE"),
                                   nullable=False)
    completion_notes   = db.Column(db.Text, nullable=True)
    proof_images       = db.Column(db.Text, nullable=True)
    owner_verified     = db.Column(db.Boolean, default=False, nullable=False)
    completion_token   = db.Column(db.String(64), nullable=True, unique=True)
    due_at             = db.Column(db.DateTime, nullable=True)
    created_at         = db.Column(db.DateTime, default=now_utc, nullable=False)
    updated_at         = db.Column(db.DateTime, default=now_utc,
                                   onupdate=now_utc, nullable=False)
    completed_at       = db.Column(db.DateTime, nullable=True)
    owner_id           = db.Column(db.Integer,
                                   db.ForeignKey("users.id", ondelete="CASCADE"),
                                   nullable=False)

    property = db.relationship("Property", foreign_keys=[property_id])
    assigned_worker = db.relationship("Worker", back_populates="tasks",
                                      foreign_keys=[assigned_worker_id])
    creator = db.relationship("User", foreign_keys=[created_by_owner_id])
    status_logs = db.relationship("TaskStatusLog", back_populates="task",
                                  cascade="all, delete-orphan", lazy="dynamic")

    def worker_display_name(self):
        if self.assigned_worker:
            return self.assigned_worker.full_name
        return self.temp_worker_name or "—"

    def get_proof_list(self):
        import json
        if not self.proof_images:
            return []
        try:
            data = json.loads(self.proof_images)
            return data if isinstance(data, list) else []
        except Exception:
            return [u for u in self.proof_images.split(",") if u.strip()]

    def to_dict(self, for_worker=False):
        d = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "property_id": self.property_id,
            "property_name": self.property.name if self.property else None,
            "room_number": self.room_number,
            "floor": self.floor,
            "quantity": self.quantity,
            "scheduled_time": self.scheduled_time,
            "temp_worker_name": self.temp_worker_name,
            "amount": float(self.amount) if self.amount else None,
            "pay_status": self.pay_status,
            "assigned_worker_id": self.assigned_worker_id,
            "worker_name": self.worker_display_name(),
            "priority": self.priority,
            "status": self.status,
            "owner_verified": self.owner_verified,
            "completion_notes": self.completion_notes,
            "proof_images": self.get_proof_list(),
            "created_at": to_ist(self.created_at),
            "updated_at": to_ist(self.updated_at),
            "completed_at": to_ist(self.completed_at),
            "due_at": to_ist(self.due_at),
        }
        if not for_worker:
            d["created_by_owner_id"] = self.created_by_owner_id
            d["owner_id"] = self.owner_id
        return d

    __table_args__ = (
        Index("ix_maintenance_tasks_owner_id", "owner_id"),
        Index("ix_maintenance_tasks_property_id", "property_id"),
        Index("ix_maintenance_tasks_status", "status"),
        Index("ix_maintenance_tasks_priority", "priority"),
        Index("ix_maintenance_tasks_worker_status", "assigned_worker_id", "status"),
    )


class TaskStatusLog(db.Model):
    """Worker task history — status changes."""
    __tablename__ = "task_status_logs"

    id         = db.Column(db.Integer, primary_key=True)
    task_id    = db.Column(db.Integer,
                            db.ForeignKey("maintenance_tasks.id", ondelete="CASCADE"),
                            nullable=False)
    worker_id  = db.Column(db.Integer,
                            db.ForeignKey("workers.id", ondelete="SET NULL"),
                            nullable=True)
    old_status = db.Column(db.String(20), nullable=True)
    new_status = db.Column(db.String(20), nullable=False)
    notes      = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=now_utc, nullable=False)

    task   = db.relationship("MaintenanceTask", back_populates="status_logs")
    worker = db.relationship("Worker", foreign_keys=[worker_id])

    __table_args__ = (
        Index("ix_task_status_logs_task_id", "task_id"),
        Index("ix_task_status_logs_worker_id", "worker_id"),
    )


class TenantComplaint(db.Model):
    __tablename__ = "tenant_complaints"

    id               = db.Column(db.Integer, primary_key=True)
    tenant_id        = db.Column(db.Integer,
                                 db.ForeignKey("users.id", ondelete="SET NULL"),
                                 nullable=False)
    property_id      = db.Column(db.Integer,
                                 db.ForeignKey("properties.id", ondelete="SET NULL"),
                                 nullable=False)
    room_number      = db.Column(db.String(40), nullable=True)
    issue_title      = db.Column(db.String(220), nullable=False)
    issue_description= db.Column(db.Text, nullable=True)
    issue_category   = db.Column(db.String(40), nullable=False)
    status           = db.Column(db.String(20), default="pending", nullable=False)
    assigned_worker_id = db.Column(db.Integer,
                                   db.ForeignKey("workers.id", ondelete="SET NULL"),
                                   nullable=True)
    created_at       = db.Column(db.DateTime, default=now_utc, nullable=False)
    resolved_at      = db.Column(db.DateTime, nullable=True)
    owner_id         = db.Column(db.Integer,
                                 db.ForeignKey("users.id", ondelete="CASCADE"),
                                 nullable=False)

    tenant = db.relationship("User", foreign_keys=[tenant_id])
    property = db.relationship("Property", foreign_keys=[property_id])
    assigned_worker = db.relationship("Worker", back_populates="complaints",
                                      foreign_keys=[assigned_worker_id])

    def to_dict(self):
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "property_id": self.property_id,
            "room_number": self.room_number,
            "issue_title": self.issue_title,
            "issue_description": self.issue_description,
            "issue_category": self.issue_category,
            "status": self.status,
            "assigned_worker_id": self.assigned_worker_id,
            "created_at": to_ist(self.created_at),
            "resolved_at": to_ist(self.resolved_at),
            "owner_id": self.owner_id,
        }

    __table_args__ = (
        Index("ix_tenant_complaints_owner_id", "owner_id"),
        Index("ix_tenant_complaints_status", "status"),
        Index("ix_tenant_complaints_category", "issue_category"),
    )


class PropertyExpense(db.Model):
    __tablename__ = "property_expenses"

    id             = db.Column(db.Integer, primary_key=True)
    property_id    = db.Column(db.Integer,
                               db.ForeignKey("properties.id", ondelete="SET NULL"),
                               nullable=False)
    expense_type   = db.Column(db.String(40), nullable=False)
    amount         = db.Column(db.Numeric(10, 2), nullable=False)
    payment_status = db.Column(db.String(20), default="pending", nullable=False)
    paid_to        = db.Column(db.String(120), nullable=True)
    worker_id      = db.Column(db.Integer,
                               db.ForeignKey("workers.id", ondelete="SET NULL"),
                               nullable=True)
    notes          = db.Column(db.Text, nullable=True)
    expense_date   = db.Column(db.Date, nullable=False)
    created_at     = db.Column(db.DateTime, default=now_utc, nullable=False)
    owner_id       = db.Column(db.Integer,
                               db.ForeignKey("users.id", ondelete="CASCADE"),
                               nullable=False)

    property = db.relationship("Property", foreign_keys=[property_id])
    worker   = db.relationship("Worker", back_populates="expenses",
                               foreign_keys=[worker_id])

    def to_dict(self):
        return {
            "id": self.id,
            "property_id": self.property_id,
            "expense_type": self.expense_type,
            "amount": float(self.amount),
            "payment_status": self.payment_status,
            "paid_to": self.paid_to,
            "worker_id": self.worker_id,
            "notes": self.notes,
            "expense_date": self.expense_date.isoformat() if self.expense_date else None,
            "created_at": to_ist(self.created_at),
            "owner_id": self.owner_id,
        }

    __table_args__ = (
        Index("ix_property_expenses_owner_id", "owner_id"),
        Index("ix_property_expenses_property_id", "property_id"),
        Index("ix_property_expenses_expense_type", "expense_type"),
    )


# ── PropertyTenant ────────────────────────────────────────────────────────────
class PropertyTenant(db.Model):
    """Maps a tenant to a property + optional room number."""
    __tablename__ = "property_tenants"

    id             = db.Column(db.Integer, primary_key=True)
    property_id    = db.Column(db.Integer,
                                db.ForeignKey("properties.id", ondelete="CASCADE"),
                                nullable=False)
    tenant_id      = db.Column(db.Integer,
                                db.ForeignKey("users.id", ondelete="CASCADE"),
                                nullable=False)
    # --- NEW: direct room reference (can be None for properties without rooms) ---
    room_id        = db.Column(db.Integer,
                                db.ForeignKey("rooms.id", ondelete="SET NULL"),
                                nullable=True)
    room_number    = db.Column(db.String(20), nullable=True)   # denormalised for fast display

    lease_start    = db.Column(db.DateTime, nullable=True)
    lease_end      = db.Column(db.DateTime, nullable=True)
    deposit_amount = db.Column(db.Numeric(10, 2), nullable=True)
    status         = db.Column(db.String(20), default="active")  # active|inactive|pending|vacated
    notes          = db.Column(db.Text, nullable=True)
    created_at     = db.Column(db.DateTime, default=now_utc)
    updated_at     = db.Column(db.DateTime, default=now_utc, onupdate=now_utc)

    property = db.relationship("Property", back_populates="tenants",  foreign_keys=[property_id])
    tenant   = db.relationship("User",     back_populates="tenancies", foreign_keys=[tenant_id])
    room     = db.relationship("Room",     foreign_keys=[room_id])

    def to_dict(self):
        return {
            "id": self.id, "property_id": self.property_id, "tenant_id": self.tenant_id,
            "room_id": self.room_id, "room_number": self.room_number,
            "status": self.status,
            "lease_start": to_ist(self.lease_start),
            "lease_end":   to_ist(self.lease_end),
            "deposit_amount": float(self.deposit_amount) if self.deposit_amount else None,
        }

    __table_args__ = (
        Index("ix_pt_tenant_id",   "tenant_id"),
        Index("ix_pt_property_id", "property_id"),
        Index("ix_pt_status",      "status"),
        Index("ix_pt_tenant_status", "tenant_id", "status"),  # for deactivate query
    )


# ── Payment ───────────────────────────────────────────────────────────────────
class Payment(db.Model):
    """Monthly rent record for a tenant. rent_month tracks the billing cycle."""
    __tablename__ = "payments"

    id             = db.Column(db.Integer, primary_key=True)
    tenant_id      = db.Column(db.Integer,
                                db.ForeignKey("users.id", ondelete="CASCADE"),
                                nullable=False)
    property_id    = db.Column(db.Integer,
                                db.ForeignKey("properties.id", ondelete="CASCADE"),
                                nullable=False)
    # --- NEW: billing month label e.g. "2025-06" (YYYY-MM) ---
    rent_month     = db.Column(db.String(7), nullable=True, index=True)

    amount         = db.Column(db.Numeric(10, 2), nullable=False)
    payment_type   = db.Column(db.String(30), default="rent")
    # status: pending | completed | overdue | failed | waived
    status         = db.Column(db.String(20), default="pending", nullable=False)
    due_date       = db.Column(db.DateTime, nullable=True)
    paid_at        = db.Column(db.DateTime, nullable=True)
    # transaction_id: nullable + unique (NULLs are not considered equal in PG)
    transaction_id = db.Column(db.String(100), unique=True, nullable=True)
    payment_method = db.Column(db.String(50),  nullable=True)
    description    = db.Column(db.Text, nullable=True)
    notes          = db.Column(db.Text, nullable=True)
    created_at     = db.Column(db.DateTime, default=now_utc)
    updated_at     = db.Column(db.DateTime, default=now_utc, onupdate=now_utc)

    tenant   = db.relationship("User",     back_populates="payments",  foreign_keys=[tenant_id])
    property = db.relationship("Property", back_populates="payments",  foreign_keys=[property_id])

    def get_is_paid(self):
        return self.status == "completed"

    def to_dict(self):
        return {
            "id": self.id,
            "amount": float(self.amount),
            "payment_type": self.payment_type,
            "status": self.status,
            "is_paid": self.get_is_paid(),
            "rent_month": self.rent_month,
            "due_date": to_ist(self.due_date),
            "paid_at":  to_ist(self.paid_at),
            "transaction_id": self.transaction_id,
            "description": self.description,
            "tenant_id": self.tenant_id,
            "property_id": self.property_id,
            "created_at": to_ist(self.created_at),
        }

    __table_args__ = (
        # Prevent duplicate monthly record per tenant per property
        UniqueConstraint("tenant_id", "property_id", "rent_month",
                         name="uq_payment_tenant_property_month"),
        Index("ix_payments_tenant_status",  "tenant_id", "status"),
        Index("ix_payments_property_month", "property_id", "rent_month"),
        Index("ix_payments_status",         "status"),
        Index("ix_payments_due_date",       "due_date"),
    )


# ── Room ──────────────────────────────────────────────────────────────────────
class Room(db.Model):
    """
    A room inside a property. Capacity is strictly enforced ≤ 4.
    room_number stored as String to support values like "101A", "G-1".
    """
    __tablename__ = "rooms"

    id           = db.Column(db.Integer,     primary_key=True)
    room_number  = db.Column(db.String(20),  nullable=False)   # "1", "101", "G-1" etc.
    # max_capacity: 1–4, enforced at DB level (CheckConstraint) AND route level
    max_capacity = db.Column(db.Integer,     default=4, nullable=False)
    description  = db.Column(db.String(200), nullable=True)
    floor        = db.Column(db.String(20),  nullable=True)    # e.g. "Ground", "1st"
    amenities    = db.Column(db.String(500), nullable=True)    # comma-separated tags
    property_id  = db.Column(db.Integer,
                              db.ForeignKey("properties.id", ondelete="CASCADE"),
                              nullable=True)
    owner_id     = db.Column(db.Integer,
                              db.ForeignKey("users.id", ondelete="CASCADE"),
                              nullable=False)
    is_active    = db.Column(db.Boolean, default=True, nullable=False)
    created_at   = db.Column(db.DateTime, default=now_utc)
    updated_at   = db.Column(db.DateTime, default=now_utc, onupdate=now_utc)

    prop         = db.relationship("Property",  back_populates="rooms", foreign_keys=[property_id])
    owner        = db.relationship("User",      foreign_keys=[owner_id])
    room_tenants = db.relationship("RoomTenant", back_populates="room",
                                   cascade="all, delete-orphan", lazy="dynamic")

    def get_occupancy(self):
        return self.room_tenants.filter_by(is_active=True).count()

    def get_is_full(self):
        return self.get_occupancy() >= self.max_capacity

    def get_vacant_slots(self):
        return max(0, self.max_capacity - self.get_occupancy())

    def to_dict(self):
        occ = self.get_occupancy()
        return {
            "id": self.id, "room_number": self.room_number,
            "max_capacity": self.max_capacity, "occupancy": occ,
            "vacant_slots": max(0, self.max_capacity - occ),
            "is_full": occ >= self.max_capacity,
            "description": self.description or "",
            "floor": self.floor or "",
            "property_id": self.property_id, "owner_id": self.owner_id,
            "is_active": self.is_active,
        }

    __table_args__ = (
        # Prevent duplicate room_number in same property
        UniqueConstraint("property_id", "room_number", name="uq_room_property_number"),
        # Enforce capacity ≤ 4 at database level
        CheckConstraint("max_capacity >= 1 AND max_capacity <= 4",
                        name="ck_room_capacity"),
        Index("ix_rooms_owner_id",    "owner_id"),
        Index("ix_rooms_property_id", "property_id"),
    )


# ── RoomTenant ────────────────────────────────────────────────────────────────
class RoomTenant(db.Model):
    """Assignment of a tenant to a specific room + monthly payment status."""
    __tablename__ = "room_tenants"

    id             = db.Column(db.Integer, primary_key=True)
    room_id        = db.Column(db.Integer,
                                db.ForeignKey("rooms.id", ondelete="CASCADE"),
                                nullable=False)
    tenant_id      = db.Column(db.Integer,
                                db.ForeignKey("users.id", ondelete="CASCADE"),
                                nullable=False)
    # current month payment status (quick-access field)
    payment_status = db.Column(db.String(20), default="not_paid", nullable=False)  # paid|not_paid
    assigned_at    = db.Column(db.DateTime, default=now_utc)
    vacated_at     = db.Column(db.DateTime, nullable=True)
    is_active      = db.Column(db.Boolean, default=True, nullable=False)

    room   = db.relationship("Room", back_populates="room_tenants", foreign_keys=[room_id])
    tenant = db.relationship("User", back_populates="room_assignments", foreign_keys=[tenant_id])

    def to_dict(self):
        return {
            "id": self.id, "room_id": self.room_id, "tenant_id": self.tenant_id,
            "payment_status": self.payment_status,
            "tenant_name": self.tenant.full_name if self.tenant else "?",
            "tenant_phone": self.tenant.phone if self.tenant else "",
            "assigned_at": to_ist(self.assigned_at),
            "is_active": self.is_active,
        }

    __table_args__ = (
        UniqueConstraint("room_id", "tenant_id", name="uq_room_tenant"),
        Index("ix_rt_tenant_id", "tenant_id"),
        Index("ix_rt_room_id",   "room_id"),
    )


# ── Message ───────────────────────────────────────────────────────────────────
class Message(db.Model):
    """
    Chat message between two users.
    file_type: image | video | audio | file
    Indexed for fast conversation loading (last 50 pagination).
    """
    __tablename__ = "messages"

    id          = db.Column(db.Integer, primary_key=True)
    sender_id   = db.Column(db.Integer,
                             db.ForeignKey("users.id", ondelete="CASCADE"),
                             nullable=False)
    receiver_id = db.Column(db.Integer,
                             db.ForeignKey("users.id", ondelete="CASCADE"),
                             nullable=True)
    property_id = db.Column(db.Integer,
                             db.ForeignKey("properties.id", ondelete="SET NULL"),
                             nullable=True)
    # room_id = "dm_{min}_{max}" for DMs, or "rgrp_{room_id}" for shared room chat
    room_id     = db.Column(db.String(100), nullable=True)
    content     = db.Column(db.Text,        nullable=True)
    # File fields
    file_url    = db.Column(db.String(500), nullable=True)
    file_name   = db.Column(db.String(255), nullable=True)
    file_type   = db.Column(db.String(20),  nullable=True)   # image|video|audio|file
    file_size   = db.Column(db.Integer,     nullable=True)   # bytes
    # Read receipt: True when receiver has seen it
    is_read     = db.Column(db.Boolean, default=False, nullable=False)
    is_deleted  = db.Column(db.Boolean, default=False, nullable=False)  # soft delete
    created_at  = db.Column(db.DateTime, default=now_utc)

    sender   = db.relationship("User", back_populates="sent_messages",    foreign_keys=[sender_id])
    receiver = db.relationship("User", back_populates="received_messages", foreign_keys=[receiver_id])

    def to_dict(self):
        return {
            "id": self.id,
            "sender_id":   self.sender_id,
            "sender_name": self.sender.full_name if self.sender else "?",
            "sender_tenant_code": (
                self.sender.tenant_public_id if self.sender else None
            ),
            "receiver_id": self.receiver_id,
            "property_id": self.property_id,
            "room_id":     self.room_id,
            "content":     self.content,
            "file_url":    self.file_url,
            "file_name":   self.file_name,
            "file_type":   self.file_type,
            "file_size":   self.file_size,
            "is_read":     self.is_read,
            "is_deleted":  self.is_deleted,
            "created_at":  (self.created_at.isoformat() + 'Z') if self.created_at else None,
            "created_ts":  int(self.created_at.timestamp()) if self.created_at else 0,
            "created_at_ist": to_ist_time_only(self.created_at),
        }

    __table_args__ = (
        # Composite index: load conversation fast, paginate by created_at
        Index("ix_msg_room_created",    "room_id", "created_at"),
        Index("ix_msg_receiver_unread", "receiver_id", "is_read"),
        Index("ix_msg_sender_id",       "sender_id"),
    )


# ── Notification ──────────────────────────────────────────────────────────────
class Notification(db.Model):
    __tablename__ = "notifications"

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer,
                            db.ForeignKey("users.id", ondelete="CASCADE"),
                            nullable=False)
    title      = db.Column(db.String(255), nullable=False)
    body       = db.Column(db.Text,        nullable=False)
    # notif_type: general | payment_due | payment_received | payment_overdue
    #             | chat | rent_reminder | lease_expiry
    notif_type = db.Column(db.String(50),  default="general", nullable=False)
    is_read    = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=now_utc)

    user = db.relationship("User", back_populates="notifications")

    def to_dict(self):
        return {
            "id": self.id, "title": self.title, "body": self.body,
            "notif_type": self.notif_type, "is_read": self.is_read,
            "created_at": to_ist(self.created_at),
        }

    __table_args__ = (
        # Composite: fast unread count per user
        Index("ix_notif_user_read",    "user_id", "is_read"),
        Index("ix_notif_user_created", "user_id", "created_at"),
    )
