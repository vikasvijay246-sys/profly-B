import uuid
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func as sqlfunc
from models import db, Payment, PropertyTenant, Notification, RoomTenant, Message, VacateRequest, now_utc
from routes import role_required
from services import tenant_payment_history, fmt_month, TenantService, push_notification
from utils.errors import AppError

_tenant_svc = TenantService()

tenant_bp = Blueprint("tenant", __name__, url_prefix="/tenant")


@tenant_bp.route("/dashboard")
@login_required
@role_required("tenant")
def dashboard():
    tenancies       = PropertyTenant.query.filter_by(tenant_id=current_user.id).all()
    room_assignment = (RoomTenant.query
                       .filter_by(tenant_id=current_user.id, is_active=True)
                       .first())

    # Aggregate payment counts in one query instead of 4 separate COUNT queries
    payment_stats = db.session.query(
        Payment.status,
        sqlfunc.count(Payment.id).label('cnt')
    ).filter_by(tenant_id=current_user.id).group_by(Payment.status).all()
    
    stats_dict = {s[0]: s[1] for s in payment_stats}
    stats = {
        "active_leases":    len(tenancies),
        "pending_payments": stats_dict.get('pending', 0),
        "overdue_payments": stats_dict.get('overdue', 0),
        "paid_payments":    stats_dict.get('completed', 0),
        "unread_notifs":    Notification.query.filter_by(user_id=current_user.id, is_read=False).count(),
    }

    # Current month rent status
    current_month_pay = (Payment.query
                         .filter_by(tenant_id=current_user.id,
                                    rent_month=fmt_month(),
                                    payment_type="rent")
                         .first())

    recent_payments = (Payment.query.filter_by(tenant_id=current_user.id)
                       .order_by(Payment.rent_month.desc(), Payment.created_at.desc())
                       .limit(6).all())
    vacate_request = _tenant_svc.active_vacate_request_for_tenant(current_user.id)

    return render_template("tenant/dashboard.html",
                           tenancies=tenancies,
                           room_assignment=room_assignment,
                           stats=stats,
                           current_month_pay=current_month_pay,
                           recent_payments=recent_payments,
                           vacate_request=vacate_request,
                           current_month=fmt_month())


@tenant_bp.route("/payments")
@login_required
@role_required("tenant")
def payments():
    sf = request.args.get("status", "")
    mf = request.args.get("month", "")
    q  = Payment.query.filter_by(tenant_id=current_user.id)
    if sf: q = q.filter_by(status=sf)
    if mf: q = q.filter_by(rent_month=mf)
    all_payments = q.order_by(Payment.rent_month.desc(), Payment.created_at.desc()).all()

    # Available months this tenant has records for
    months = [r[0] for r in
              db.session.query(Payment.rent_month)
              .filter_by(tenant_id=current_user.id)
              .filter(Payment.rent_month.isnot(None))
              .distinct().order_by(Payment.rent_month.desc()).all()]

    return render_template("tenant/payments.html",
                           payments=all_payments,
                           status_filter=sf,
                           month_filter=mf,
                           available_months=months)


@tenant_bp.route("/payments/history")
@login_required
@role_required("tenant")
def payment_history():
    history = tenant_payment_history(current_user.id, months=24)
    return render_template("tenant/payment_history.html", history=history)


@tenant_bp.route("/payments/<int:pid>/pay", methods=["POST"])
@login_required
@role_required("tenant")
def pay(pid):
    pay = Payment.query.filter_by(id=pid, tenant_id=current_user.id).first_or_404()
    if pay.status == "completed":
        flash("Already paid.", "error")
        return redirect(url_for("tenant.payments"))
    if pay.status not in ("pending", "overdue"):
        flash("This payment cannot be processed.", "error")
        return redirect(url_for("tenant.payments"))

    pay.status         = "completed"
    pay.paid_at        = now_utc()
    pay.payment_method = request.form.get("payment_method", "online")
    pay.transaction_id = f"TXN-{uuid.uuid4().hex[:10].upper()}"
    db.session.commit()

    notif = Notification(
        user_id=current_user.id,
        title="✅ Payment Successful",
        body=f"₹{pay.amount:,.0f} paid for {pay.rent_month or 'rent'}. Ref: {pay.transaction_id}",
        notif_type="payment_received",
    )
    db.session.add(notif)
    db.session.commit()

    try:
        from app import socketio
        socketio.emit("notification", notif.to_dict(), room=f"user_{current_user.id}")
    except Exception:
        pass

    flash("✅ Payment successful!", "success")
    return redirect(url_for("tenant.payments"))


@tenant_bp.route("/notifications")
@login_required
@role_required("tenant")
def notifications():
    notifs = (Notification.query.filter_by(user_id=current_user.id)
              .order_by(Notification.created_at.desc()).all())
    return render_template("tenant/notifications.html", notifications=notifs)


@tenant_bp.route("/vacate-notice", methods=["GET", "POST"])
@login_required
@role_required("tenant")
def vacate_notice():
    active_room = RoomTenant.query.filter_by(tenant_id=current_user.id, is_active=True).first()
    active_tenancy = PropertyTenant.query.filter_by(tenant_id=current_user.id, status="active").first()
    existing_request = _tenant_svc.active_vacate_request_for_tenant(current_user.id)
    owner_phone = current_user.owner_user.phone if getattr(current_user, 'owner_user', None) else None

    if request.method == "POST":
        vacate_date_raw = request.form.get("vacate_date", "").strip()
        reason = request.form.get("reason", "").strip()
        if not vacate_date_raw:
            flash("Please choose a vacate date before submitting.", "error")
            return redirect(url_for("tenant.vacate_notice"))

        try:
            vacate_date = datetime.strptime(vacate_date_raw, "%Y-%m-%d").date()
            if vacate_date < datetime.utcnow().date():
                raise ValueError("Past date")

            vacate = _tenant_svc.submit_vacate_request(current_user.id, vacate_date, reason)
            try:
                from app import socketio
                push_notification(
                    socketio,
                    vacate.owner_id,
                    "New Vacate Notice Received",
                    f"{current_user.full_name} submitted a vacate notice for room {vacate.room_number}.",
                    "general"
                )
            except Exception:
                pass
            flash("✅ Vacate notice submitted. The owner has been notified.", "success")
            return redirect(url_for("tenant.vacate_notice"))
        except AppError as e:
            flash(str(e), "error")
            return redirect(url_for("tenant.vacate_notice"))
        except ValueError:
            flash("Please choose a valid vacate date from today onwards.", "error")
            return redirect(url_for("tenant.vacate_notice"))

    return render_template(
        "tenant/vacate_notice.html",
        active_room=active_room,
        active_tenancy=active_tenancy,
        existing_request=existing_request,
        current_date=datetime.utcnow().strftime("%d-%m-%Y"),
        today_iso=datetime.utcnow().strftime("%Y-%m-%d"),
        owner_phone=owner_phone,
    )


@tenant_bp.route("/notifications/<int:nid>/read", methods=["POST"])
@login_required
@role_required("tenant")
def mark_read(nid):
    n = Notification.query.filter_by(id=nid, user_id=current_user.id).first_or_404()
    n.is_read = True
    db.session.commit()
    return redirect(url_for("tenant.notifications"))


@tenant_bp.route("/notifications/read-all", methods=["POST"])
@login_required
@role_required("tenant")
def mark_all_read():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    flash("All notifications marked as read.", "success")
    return redirect(url_for("tenant.notifications"))


# ── API: unread counts (polling badge) ───────────────────────────────────────
@tenant_bp.route("/api/unread")
@login_required
@role_required("tenant")
def api_unread():
    notif_count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    msg_count   = Message.query.filter_by(receiver_id=current_user.id, is_read=False,
                                          is_deleted=False).count()
    current_rent = (Payment.query
                    .filter_by(tenant_id=current_user.id,
                               rent_month=fmt_month(), payment_type="rent")
                    .first())
    return jsonify({
        "notifications":   notif_count,
        "messages":        msg_count,
        "rent_status":     current_rent.status if current_rent else "no_record",
        "rent_month":      fmt_month(),
    })
