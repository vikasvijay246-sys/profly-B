from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User, Worker

auth_bp = Blueprint("auth", __name__)


def _find_user_for_login(identifier: str):
    """Phone/username or worker email."""
    identifier = (identifier or "").strip()
    if not identifier:
        return None
    user = User.query.filter_by(phone=identifier).first()
    if user:
        return user
    if "@" in identifier:
        worker = Worker.query.filter(
            Worker.email == identifier,
            Worker.can_login == True,
            Worker.active_status == True,
        ).first()
        if worker and worker.user_id:
            return User.query.get(worker.user_id)
    return None


@auth_bp.route("/", methods=["GET"])
def index():
    if current_user.is_authenticated:
        return redirect(url_for(f"{current_user.role}.dashboard"))
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for(f"{current_user.role}.dashboard"))

    if request.method == "POST":
        phone    = request.form.get("phone", "").strip()
        password = request.form.get("password", "")

        user = _find_user_for_login(phone)
        if user and user.is_active and user.check_password(password):
            login_user(user, remember=True)
            return redirect(url_for(f"{user.role}.dashboard"))

        flash("Invalid phone / password.", "error")

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
