"""
SQLite schema upgrades for additive columns (no Alembic).
Runs safely at startup; skips columns that already exist.
"""
from sqlalchemy import text


def _sqlite_columns(conn, table: str) -> set:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {r[1] for r in rows}


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table},
    ).fetchone()
    return row is not None


def upgrade_sqlite_schema(db):
    """Add missing columns/indexes for lightweight migrations."""
    bind = db.engine
    if bind.dialect.name != "sqlite":
        return

    with bind.connect() as conn:
        cols = _sqlite_columns(conn, "users")
        if "tenant_public_id" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN tenant_public_id VARCHAR(48)"))
        if "designation" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN designation VARCHAR(40)"))

        pcols = _sqlite_columns(conn, "properties")
        if "short_code" not in pcols:
            conn.execute(text("ALTER TABLE properties ADD COLUMN short_code VARCHAR(16)"))

        mcols = _sqlite_columns(conn, "messages")
        if "property_id" not in mcols:
            conn.execute(text("ALTER TABLE messages ADD COLUMN property_id INTEGER"))

        wcols = _sqlite_columns(conn, "workers") if _table_exists(conn, "workers") else set()
        for col, typ in [
            ("email", "VARCHAR(120)"),
            ("is_temp", "BOOLEAN DEFAULT 0"),
            ("can_login", "BOOLEAN DEFAULT 0"),
            ("user_id", "INTEGER"),
        ]:
            if wcols and col not in wcols:
                conn.execute(text(f"ALTER TABLE workers ADD COLUMN {col} {typ}"))

        tcols = _sqlite_columns(conn, "maintenance_tasks") if _table_exists(conn, "maintenance_tasks") else set()
        for col, typ in [
            ("floor", "VARCHAR(40)"),
            ("quantity", "VARCHAR(60)"),
            ("scheduled_time", "VARCHAR(40)"),
            ("temp_worker_name", "VARCHAR(150)"),
            ("amount", "NUMERIC(10,2)"),
            ("pay_status", "VARCHAR(20) DEFAULT 'none'"),
            ("owner_verified", "BOOLEAN DEFAULT 0"),
            ("completion_token", "VARCHAR(64)"),
            ("due_at", "DATETIME"),
        ]:
            if tcols and col not in tcols:
                conn.execute(text(f"ALTER TABLE maintenance_tasks ADD COLUMN {col} {typ}"))

        conn.commit()

        # Create vacate requests table if it does not already exist.
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS vacate_requests ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "request_id VARCHAR(32) NOT NULL UNIQUE, "
            "tenant_id INTEGER NOT NULL, "
            "owner_id INTEGER NOT NULL, "
            "property_id INTEGER NOT NULL, "
            "room_id INTEGER, "
            "room_number VARCHAR(20), "
            "vacate_date DATE NOT NULL, "
            "reason TEXT, "
            "status VARCHAR(20) NOT NULL DEFAULT 'pending', "
            "submitted_at DATETIME NOT NULL, "
            "processed_at DATETIME, "
            "decision_by INTEGER, "
            "decision_notes TEXT, "
            "vacated_at DATETIME, "
            "FOREIGN KEY(tenant_id) REFERENCES users(id) ON DELETE CASCADE, "
            "FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE CASCADE, "
            "FOREIGN KEY(property_id) REFERENCES properties(id) ON DELETE SET NULL, "
            "FOREIGN KEY(room_id) REFERENCES rooms(id) ON DELETE SET NULL, "
            "FOREIGN KEY(decision_by) REFERENCES users(id) ON DELETE SET NULL"
            ")"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_vacate_requests_owner_id ON vacate_requests(owner_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_vacate_requests_tenant_id ON vacate_requests(tenant_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_vacate_requests_status ON vacate_requests(status)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_vacate_requests_property_id ON vacate_requests(property_id)"
        ))

        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS task_status_logs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "task_id INTEGER NOT NULL, "
            "worker_id INTEGER, "
            "old_status VARCHAR(20), "
            "new_status VARCHAR(20) NOT NULL, "
            "notes TEXT, "
            "created_at DATETIME NOT NULL, "
            "FOREIGN KEY(task_id) REFERENCES maintenance_tasks(id) ON DELETE CASCADE, "
            "FOREIGN KEY(worker_id) REFERENCES workers(id) ON DELETE SET NULL"
            ")"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_task_status_logs_task_id ON task_status_logs(task_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_task_status_logs_worker_id ON task_status_logs(worker_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_maintenance_tasks_worker_status "
            "ON maintenance_tasks(assigned_worker_id, status)"
        ))
        conn.commit()

    # Unique partial index for tenant IDs (SQLite)
    with bind.connect() as conn:
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_tenant_public_id_unique "
            "ON users(tenant_public_id) WHERE tenant_public_id IS NOT NULL"
        ))
        conn.commit()
