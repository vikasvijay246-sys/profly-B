"""
Worker portal — separate mobile-first interface for maintenance workers.
"""
import json
import os
import uuid

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, jsonify, current_app,
)
from flask_login import login_required, current_user

from models import db, Worker, MaintenanceTask, Notification, PropertyExpense, now_utc
from routes import role_required
from services.worker_task import WorkerTaskService
from services.notification import NotificationService
from services.helpers import save_uploaded_file, is_allowed_proof
from utils.errors import ValidationError, AppError

worker_bp = Blueprint("worker", __name__, url_prefix="/worker")
_svc = WorkerTaskService()


@worker_bp.context_processor
def worker_context():
    if current_user.is_authenticated and getattr(current_user, "role", None) == "worker":
        try:
            unread = NotificationService().unread_count(current_user.id)
        except Exception:
            unread = 0
        return {"unread_count": unread}
    return {"unread_count": 0}


def _worker():
    return _svc.get_worker_for_user(current_user.id)


def _socketio():
    from app import socketio
    return socketio


@worker_bp.route("/dashboard")
@login_required
@role_required("worker")
def dashboard():
    w = _worker()
    today = _svc.list_tasks(w.id, today_only=True, limit=20)
    pending = _svc.list_tasks(w.id, status=("pending", "working"), limit=30)
    urgent = [t for t in pending if t.priority in ("urgent", "high")]
    completed = _svc.list_tasks(w.id, status="completed", limit=10)
    salary = _svc.salary_summary(w.id)
    unread = NotificationService().unread_count(current_user.id)
    return render_template(
        "worker/dashboard.html",
        worker=w,
        today_tasks=today,
        pending_tasks=pending,
        urgent_tasks=urgent,
        completed_tasks=completed,
        salary=salary,
        unread_count=unread,
    )


@worker_bp.route("/tasks")
@login_required
@role_required("worker")
def tasks():
    w = _worker()
    tab = request.args.get("tab", "pending")
    if tab == "today":
        items = _svc.list_tasks(w.id, today_only=True, limit=80)
    elif tab == "completed":
        items = _svc.list_tasks(w.id, status="completed", limit=80)
    elif tab == "urgent":
        items = _svc.list_tasks(w.id, status=("pending", "working"), urgent_only=True, limit=80)
    else:
        items = _svc.list_tasks(w.id, status=("pending", "working"), limit=80)
    return render_template(
        "worker/tasks.html", worker=w, tasks=items, tab=tab,
    )


@worker_bp.route("/tasks/<int:tid>")
@login_required
@role_required("worker")
def task_detail(tid):
    w = _worker()
    task = _svc.get_task(w.id, tid)
    return render_template("worker/task_detail.html", worker=w, task=task)


@worker_bp.route("/tasks/<int:tid>/status", methods=["POST"])
@login_required
@role_required("worker")
def task_status(tid):
    w = _worker()
    try:
        status = request.form.get("status") or request.json.get("status") if request.is_json else None
        status = status or request.form.get("status")
        notes = request.form.get("notes") or (request.json.get("notes") if request.is_json else None)
        key = request.form.get("idempotency_key") or request.headers.get("X-Idempotency-Key")
        task = _svc.update_status(w.id, tid, status, notes=notes, idempotency_key=key)
        if request.is_json or request.path.startswith("/worker/api"):
            return jsonify({"ok": True, "task": task.to_dict(for_worker=True)})
        flash("Status updated.", "success")
    except AppError as e:
        if request.is_json:
            return jsonify({"ok": False, "error": str(e)}), e.http_status
        flash(str(e), "error")
    except Exception:
        if request.is_json:
            return jsonify({"ok": False, "error": "Update failed"}), 500
        flash("Could not update task.", "error")
    return redirect(url_for("worker.task_detail", tid=tid))


@worker_bp.route("/tasks/<int:tid>/complete", methods=["POST"])
@login_required
@role_required("worker")
def task_complete(tid):
    w = _worker()
    try:
        notes = request.form.get("completion_notes", "").strip()
        key = request.form.get("idempotency_key") or str(uuid.uuid4())
        proof_paths = []
        upload_folder = current_app.config["UPLOAD_FOLDER"]
        files = request.files.getlist("proof_images") or []
        if request.files.get("proof_image"):
            files = files or [request.files.get("proof_image")]
        for i, f in enumerate(files):
            if f and f.filename and is_allowed_proof(f.filename):
                fname = f"task_{tid}_{w.id}_{uuid.uuid4().hex[:8]}"
                rel = save_uploaded_file(f, upload_folder, fname)
                if rel:
                    proof_paths.append(rel)
        task = _svc.complete_task(
            w.id, tid, notes=notes, proof_paths=proof_paths, idempotency_key=key,
        )
        owner_user = task.creator
        if owner_user:
            NotificationService().push(
                _socketio(), owner_user.id,
                "Work completed",
                f"{w.full_name} finished: {task.title}",
                "worker_completion",
            )
        if request.is_json or request.headers.get("Accept") == "application/json":
            return jsonify({"ok": True, "task": task.to_dict(for_worker=True)})
        flash("Marked complete. Owner will verify.", "success")
        return redirect(url_for("worker.tasks", tab="completed"))
    except AppError as e:
        if request.is_json:
            return jsonify({"ok": False, "error": str(e)}), e.http_status
        flash(str(e), "error")
    except Exception:
        flash("Could not complete task.", "error")
    return redirect(url_for("worker.task_detail", tid=tid))


@worker_bp.route("/notifications")
@login_required
@role_required("worker")
def notifications():
    w = _worker()
    items = (
        Notification.query.filter_by(user_id=current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(60)
        .all()
    )
    return render_template("worker/notifications.html", worker=w, notifications=items)


@worker_bp.route("/notifications/read", methods=["POST"])
@login_required
@role_required("worker")
def notifications_read():
    NotificationService().mark_all_read(current_user.id)
    if request.is_json:
        return jsonify({"ok": True})
    return redirect(url_for("worker.notifications"))


@worker_bp.route("/payments")
@login_required
@role_required("worker")
def payments():
    w = _worker()
    salary = _svc.salary_summary(w.id)
    expenses = (
        PropertyExpense.query.filter_by(worker_id=w.id)
        .order_by(PropertyExpense.expense_date.desc())
        .limit(40)
        .all()
    )
    task_payments = (
        MaintenanceTask.query.filter_by(assigned_worker_id=w.id)
        .filter(MaintenanceTask.amount.isnot(None))
        .order_by(MaintenanceTask.created_at.desc())
        .limit(30)
        .all()
    )
    return render_template(
        "worker/payments.html",
        worker=w,
        salary=salary,
        expenses=expenses,
        task_payments=task_payments,
    )


@worker_bp.route("/properties")
@login_required
@role_required("worker")
def properties():
    w = _worker()
    props = _svc.assigned_properties(w.id)
    return render_template("worker/properties.html", worker=w, properties=props)


@worker_bp.route("/api/unread")
@login_required
@role_required("worker")
def api_unread():
    return jsonify({
        "unread": NotificationService().unread_count(current_user.id),
    })


@worker_bp.route("/api/tasks/sync")
@login_required
@role_required("worker")
def api_tasks_sync():
    """Lightweight task list for offline refresh."""
    w = _worker()
    tasks = _svc.list_tasks(w.id, limit=100)
    return jsonify({
        "ok": True,
        "tasks": [t.to_dict(for_worker=True) for t in tasks],
        "ts": now_utc().isoformat(),
    })
