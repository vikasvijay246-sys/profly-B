"""
WorkerTaskService — worker portal task access, status updates, notifications.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

from models import (
    db, Worker, MaintenanceTask, TaskStatusLog,
    PropertyExpense, Property, now_utc,
)
from services.base import BaseService
from services.notification import NotificationService
from utils.errors import ValidationError, NotFoundError, PermissionError_


VALID_WORKER_STATUSES = {"pending", "working", "completed"}
VALID_PAY_STATUS = {"none", "pending", "paid", "pending_payment"}


class WorkerTaskService(BaseService):

    def get_worker_for_user(self, user_id: int) -> Worker:
        worker = Worker.query.filter_by(
            user_id=user_id, can_login=True, active_status=True
        ).first()
        if not worker:
            raise PermissionError_("Worker account not found or inactive")
        return worker

    def _task_query(self, worker_id: int):
        return MaintenanceTask.query.filter_by(assigned_worker_id=worker_id)

    def list_tasks(self, worker_id: int, status=None, urgent_only=False,
                   today_only=False, limit=50):
        q = self._task_query(worker_id)
        if status:
            if isinstance(status, (list, tuple, set)):
                q = q.filter(MaintenanceTask.status.in_(status))
            else:
                q = q.filter_by(status=status)
        if urgent_only:
            q = q.filter(MaintenanceTask.priority.in_(("urgent", "high")))
        if today_only:
            start = now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
            q = q.filter(MaintenanceTask.created_at >= start)
        return (
            q.order_by(
                MaintenanceTask.priority.desc(),
                MaintenanceTask.created_at.desc(),
            )
            .limit(limit)
            .all()
        )

    def get_task(self, worker_id: int, task_id: int) -> MaintenanceTask:
        task = self._task_query(worker_id).filter_by(id=task_id).first()
        if not task:
            raise NotFoundError("Task", task_id)
        return task

    def _log_status(self, task, worker_id, old_status, new_status, notes=None):
        db.session.add(TaskStatusLog(
            task_id=task.id,
            worker_id=worker_id,
            old_status=old_status,
            new_status=new_status,
            notes=notes,
        ))

    def update_status(self, worker_id: int, task_id: int, new_status: str,
                      notes: str = None, idempotency_key: str = None):
        new_status = (new_status or "").strip().lower()
        if new_status not in VALID_WORKER_STATUSES:
            raise ValidationError(
                f"Status must be one of: {', '.join(sorted(VALID_WORKER_STATUSES))}"
            )

        task = self.get_task(worker_id, task_id)
        if not task.assigned_worker or not task.assigned_worker.active_status:
            raise PermissionError_("Worker is inactive")

        if task.status == "completed" and new_status == "completed":
            if task.completion_token and idempotency_key == task.completion_token:
                return task
            raise ValidationError("Task already completed")

        if idempotency_key and task.completion_token == idempotency_key:
            return task

        old = task.status
        task.status = new_status
        task.updated_at = now_utc()
        if new_status == "completed":
            task.completed_at = now_utc()
            task.completion_token = idempotency_key or str(uuid.uuid4())
            task.owner_verified = False
            if notes:
                task.completion_notes = notes
        elif new_status == "working" and notes:
            task.completion_notes = notes

        self._log_status(task, worker_id, old, new_status, notes)
        self.safe_commit(f"worker task status {task_id} -> {new_status}")
        return task

    def add_proof_images(self, worker_id: int, task_id: int, new_paths: list):
        task = self.get_task(worker_id, task_id)
        existing = task.get_proof_list()
        for p in new_paths:
            if p and p not in existing:
                existing.append(p)
        task.proof_images = json.dumps(existing) if existing else None
        self.safe_commit(f"worker task proof {task_id}")
        return task

    def complete_task(self, worker_id: int, task_id: int, notes: str = None,
                      proof_paths: list = None, idempotency_key: str = None):
        task = self.update_status(
            worker_id, task_id, "completed", notes=notes,
            idempotency_key=idempotency_key,
        )
        if proof_paths:
            self.add_proof_images(worker_id, task_id, proof_paths)
        return task

    def salary_summary(self, worker_id: int) -> dict:
        worker = Worker.query.get(worker_id)
        if not worker:
            return {}
        from services.helpers import fmt_month
        month = fmt_month()
        paid = db.session.query(db.func.coalesce(db.func.sum(PropertyExpense.amount), 0)).filter(
            PropertyExpense.worker_id == worker_id,
            PropertyExpense.payment_status == "completed",
            db.func.strftime("%Y-%m", PropertyExpense.expense_date) == month,
        ).scalar() or 0
        pending = db.session.query(db.func.coalesce(db.func.sum(PropertyExpense.amount), 0)).filter(
            PropertyExpense.worker_id == worker_id,
            PropertyExpense.payment_status == "pending",
        ).scalar() or 0
        task_pay_pending = db.session.query(
            db.func.coalesce(db.func.sum(MaintenanceTask.amount), 0)
        ).filter(
            MaintenanceTask.assigned_worker_id == worker_id,
            MaintenanceTask.pay_status == "pending_payment",
        ).scalar() or 0
        return {
            "salary_type": worker.salary_type,
            "salary_amount": float(worker.salary_amount) if worker.salary_amount else None,
            "paid_this_month": float(paid),
            "pending_expenses": float(pending),
            "pending_task_payments": float(task_pay_pending),
        }

    def assigned_properties(self, worker_id: int):
        worker = Worker.query.get(worker_id)
        if not worker:
            return []
        return list(worker.assigned_properties.all())

    def notify_worker(self, socketio, worker: Worker, title: str, body: str,
                      notif_type: str = "worker_task"):
        uid = worker.portal_user_id()
        if not uid:
            return None
        return NotificationService().push(
            socketio, uid, title, body, notif_type=notif_type
        )

    def notify_task_assigned(self, socketio, task: MaintenanceTask):
        if not task.assigned_worker:
            return
        w = task.assigned_worker
        priority = task.priority
        title = "New task" if priority not in ("urgent", "high") else "Urgent task"
        loc = task.room_number or task.floor or ""
        body = f"{task.title}"
        if loc:
            body += f" — {loc}"
        if task.property:
            body += f" @ {task.property.name}"
        self.notify_worker(socketio, w, title, body,
                           "worker_urgent" if priority in ("urgent", "high") else "worker_task")

    def mark_overdue_notifications(self, socketio, owner_id: int):
        """Notify workers about overdue pending tasks (due_at passed)."""
        now = now_utc()
        overdue = MaintenanceTask.query.filter(
            MaintenanceTask.owner_id == owner_id,
            MaintenanceTask.status.in_(("pending", "working")),
            MaintenanceTask.due_at.isnot(None),
            MaintenanceTask.due_at < now,
        ).all()
        for task in overdue:
            if task.assigned_worker:
                self.notify_worker(
                    socketio, task.assigned_worker,
                    "Task overdue", f"{task.title} is past due",
                    "worker_overdue",
                )

    @staticmethod
    def find_or_create_temp_worker(owner_id: int, name: str, phone: str = None) -> Worker:
        name = (name or "").strip()
        if not name:
            raise ValidationError("Worker name is required")
        phone = (phone or f"temp_{name[:12].replace(' ', '_')}").strip()[:30]
        existing = Worker.query.filter_by(
            owner_id=owner_id, full_name=name, is_temp=True
        ).first()
        if existing:
            return existing
        w = Worker(
            owner_id=owner_id,
            full_name=name,
            phone_number=phone,
            role="temp",
            salary_type="task-based",
            is_temp=True,
            can_login=False,
            active_status=True,
        )
        db.session.add(w)
        db.session.flush()
        return w

    @staticmethod
    def enable_portal_login(worker: Worker, password: str, email: str = None):
        """Create or link User account for worker portal."""
        from models import User
        if not password or len(password) < 4:
            raise ValidationError("Password must be at least 4 characters")
        login_phone = worker.phone_number.strip()
        if worker.user_id:
            user = User.query.get(worker.user_id)
            if user:
                user.set_password(password)
                user.is_active = True
                user.full_name = worker.full_name
                if email:
                    worker.email = email.strip()
                worker.can_login = True
                return user
        existing = User.query.filter_by(phone=login_phone).first()
        if existing and existing.role != "worker":
            raise ValidationError("Phone already used by another account")
        if existing:
            user = existing
            user.set_password(password)
            user.role = "worker"
            user.full_name = worker.full_name
            user.is_active = True
        else:
            user = User(
                phone=login_phone,
                full_name=worker.full_name,
                role="worker",
                is_active=True,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.flush()
        worker.user_id = user.id
        worker.can_login = True
        worker.is_temp = False
        if email:
            worker.email = email.strip()
        return user
