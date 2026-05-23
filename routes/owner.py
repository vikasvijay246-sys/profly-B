"""
Owner routes — thin layer: validate input → call service → respond.
No business logic, no direct DB queries (except simple reads in dashboard).
"""
import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from models import (db, User, Property, PropertyTenant, Payment, Room, RoomTenant,
                    VacateRequest, Worker, MaintenanceTask, TenantComplaint,
                    PropertyExpense, TaskStatusLog, now_utc)
from routes import role_required
from services import (generate_monthly_rent, mark_overdue_payments,
                      owner_payment_summary, push_notification, fmt_month,
                      TenantService, PaymentService)
from static.utils.validators import (
    validate_create_tenant, validate_create_payment,
    optional_date, optional_id, require_id, require_string,
    require_phone, require_amount, require_expense_type,
    require_expense_status, require_salary_type,
    require_task_priority, require_task_status,
    require_issue_category, require_payment_type,
    optional_rent_month, optional_string,
    require_payment_status, optional_amount)
from utils.errors import AppError, ValidationError
from services.worker_task import WorkerTaskService, VALID_PAY_STATUS

owner_bp = Blueprint("owner", __name__, url_prefix="/owner")
_worker_svc = WorkerTaskService()


def _socketio():
    from app import socketio
    return socketio
_tenant_svc  = TenantService()
_payment_svc = PaymentService()


@owner_bp.route("/dashboard")
@login_required
@role_required("owner")
def dashboard():
    props = Property.query.filter_by(owner_id=current_user.id, is_deleted=False).all()
    if len(props) == 1:
        return redirect(url_for('owner.property_dashboard', property_id=props[0].id))
    else:
        return redirect(url_for('owner.property_selection'))


@owner_bp.route("/property/<int:property_id>/dashboard")
@login_required
@role_required("owner")
def property_dashboard(property_id):
    prop = Property.query.filter_by(id=property_id, owner_id=current_user.id, is_deleted=False).first_or_404()

    mark_overdue_payments()

    rooms = Room.query.filter_by(property_id=property_id, is_active=True).all()
    tenants = PropertyTenant.query.filter_by(property_id=property_id, status="active").all()

    # Batch occupancy
    from sqlalchemy import func
    occ_map = {}
    if rooms:
        rows = (db.session.query(RoomTenant.room_id, func.count(RoomTenant.id))
                .filter(RoomTenant.is_active == True)
                .filter(RoomTenant.room_id.in_([r.id for r in rooms]))
                .group_by(RoomTenant.room_id).all())
        occ_map = {rid: cnt for rid, cnt in rows}

    stats = {
        "total_tenants": len(tenants),
        "total_rooms": len(rooms),
        "occupied_rooms": sum(1 for r in rooms if occ_map.get(r.id, 0) > 0),
        "vacant_rooms": sum(1 for r in rooms if occ_map.get(r.id, 0) == 0),
        "revenue": float(
            db.session.query(db.func.coalesce(db.func.sum(Payment.amount), 0))
            .filter(Payment.property_id == property_id, Payment.status == "completed")
            .scalar() or 0
        ),
        "pending_payments": Payment.query.filter_by(property_id=property_id, status="pending").count(),
        "overdue_payments": Payment.query.filter_by(property_id=property_id, status="overdue").count(),
    }

    room_data = []
    for r in rooms:
        assignments = RoomTenant.query.filter_by(room_id=r.id, is_active=True).all()
        paid_cnt = sum(1 for a in assignments if a.payment_status == "paid")
        room_data.append({
            "room": r, "assignments": assignments,
            "occupancy": len(assignments),
            "vacant": max(0, r.max_capacity - len(assignments)),
            "paid_count": paid_cnt,
        })

    target_month = request.args.get("month", fmt_month())
    pay_summary = _payment_svc.owner_summary(current_user.id, target_month, property_id=property_id)

    recent_payments = Payment.query.filter_by(property_id=property_id).order_by(Payment.created_at.desc()).limit(10).all()

    vacate_pending = VacateRequest.query.filter_by(
        owner_id=current_user.id,
        property_id=property_id,
        status="pending"
    ).order_by(VacateRequest.submitted_at.desc()).all()
    vacate_approved = VacateRequest.query.filter_by(
        owner_id=current_user.id,
        property_id=property_id,
        status="approved"
    ).order_by(VacateRequest.vacate_date.asc()).all()

    return render_template("owner/dashboard.html",
                           stats=stats, recent_payments=recent_payments,
                           props=[prop], room_data=room_data,
                           pay_summary=pay_summary, target_month=target_month,
                           current_property=prop,
                           vacate_pending=vacate_pending,
                           vacate_approved=vacate_approved)


@owner_bp.route("/property-selection")
@login_required
@role_required("owner")
def property_selection():
    mark_overdue_payments()

    props = Property.query.filter_by(owner_id=current_user.id, is_deleted=False).all()
    p_ids = [p.id for p in props]

    # Global stats across all properties
    stats = {
        "total_properties": len(props),
        "total_tenants": User.query.filter_by(owner_id=current_user.id, role="tenant", is_active=True).count(),
        "vacant_rooms": Room.query.filter_by(owner_id=current_user.id, is_active=True).count() - RoomTenant.query.filter(RoomTenant.is_active == True, RoomTenant.room_id.in_([r.id for r in Room.query.filter_by(owner_id=current_user.id, is_active=True).all()])).count(),
        "monthly_revenue": float(
            db.session.query(db.func.coalesce(db.func.sum(Payment.amount), 0))
            .filter(Payment.property_id.in_(p_ids), Payment.status == "completed")
            .scalar() or 0
        ) if p_ids else 0.0,
        "pending_payments": Payment.query.filter(
            Payment.property_id.in_(p_ids), Payment.status == "pending"
        ).count() if p_ids else 0,
        "maintenance_issues": 0,  # Placeholder, can add later
    }

    # Property list with summaries
    property_data = []
    for prop in props:
        tenant_count = PropertyTenant.query.filter_by(property_id=prop.id, status="active").count()
        rooms = Room.query.filter_by(property_id=prop.id, is_active=True).all()
        vacant_rooms = len(rooms) - RoomTenant.query.filter(RoomTenant.is_active == True, RoomTenant.room_id.in_([r.id for r in rooms])).count()
        revenue_this_month = float(
            db.session.query(db.func.coalesce(db.func.sum(Payment.amount), 0))
            .filter(Payment.property_id == prop.id, Payment.status == "completed", Payment.rent_month == fmt_month())
            .scalar() or 0
        )
        pending_dues = Payment.query.filter_by(property_id=prop.id, status="pending").count()

        # Status indicator
        if pending_dues > 0:
            status = "attention"
        elif vacant_rooms == 0:
            status = "critical"
        else:
            status = "healthy"

        property_data.append({
            "property": prop,
            "tenant_count": tenant_count,
            "vacant_rooms": vacant_rooms,
            "revenue_this_month": revenue_this_month,
            "pending_dues": pending_dues,
            "status": status,
        })

    from datetime import datetime
    return render_template(
        "owner/property_selection.html",
        stats=stats,
        property_data=property_data,
        now=datetime.now(),
        show_sidebar=False,
    )
@owner_bp.route("/generate-rent", methods=["POST"])
@login_required
@role_required("owner")
def generate_rent():
    month = request.form.get("month", fmt_month())
    try:
        created, skipped, errors = _payment_svc.generate_monthly_rent(
            owner_id=current_user.id, force_month=month
        )
        if errors:
            flash(f"Partial errors: {'; '.join(errors[:3])}", "error")
        flash(f"Rent for {month}: {created} created, {skipped} already existed.", "success")
    except AppError as e:
        flash(str(e), "error")
    return redirect(url_for("owner.dashboard"))


# ── Properties ────────────────────────────────────────────────────────────────
@owner_bp.route("/properties")
@login_required
@role_required("owner")
def properties():
    props = Property.query.filter_by(owner_id=current_user.id, is_deleted=False).all()
    return render_template("owner/properties.html", properties=props)


@owner_bp.route("/properties/add", methods=["POST"])
@login_required
@role_required("owner")
def add_property():
    try:
        from static.utils.validators import require_string, require_amount, optional_string
        from services.tenant_id import slug_property_code
        name    = require_string(request.form.get("name"),    "name", max_len=200)
        address = require_string(request.form.get("address"), "address")
        city    = require_string(request.form.get("city"),    "city", max_len=100)
        rent    = require_amount(request.form.get("monthly_rent"), "monthly_rent")
    except ValidationError as e:
        flash(str(e), "error")
        return redirect(url_for('owner.property_selection'))

    prop = Property(
        owner_id=current_user.id,
        name=name, address=address, city=city,
        state=request.form.get("state","").strip(),
        zip_code=request.form.get("zip_code","").strip(),
        unit_number=request.form.get("unit_number","").strip(),
        property_type=request.form.get("property_type","apartment"),
        bedrooms=int(request.form["bedrooms"]) if request.form.get("bedrooms") else None,
        bathrooms=int(request.form["bathrooms"]) if request.form.get("bathrooms") else None,
        area_sqft=float(request.form["area_sqft"]) if request.form.get("area_sqft") else None,
        monthly_rent=rent,
        description=request.form.get("description",""),
        status="available",
    )
    db.session.add(prop)
    try:
        db.session.flush()
        prop.short_code = slug_property_code(prop)[:16]
        db.session.commit()
        flash("Property added.", "success")
    except Exception:
        db.session.rollback()
        flash("Failed to save property. Please retry.", "error")
    return redirect(url_for("owner.properties"))


@owner_bp.route("/properties/<int:pid>/edit", methods=["POST"])
@login_required
@role_required("owner")
def edit_property(pid):
    prop = Property.query.filter_by(id=pid, owner_id=current_user.id).first_or_404()
    try:
        if request.form.get("monthly_rent"):
            prop.monthly_rent = require_amount(request.form["monthly_rent"])
    except ValidationError as e:
        flash(str(e), "error")
        return redirect(url_for("owner.properties"))

    prop.name   = request.form.get("name",   prop.name).strip()
    prop.address= request.form.get("address",prop.address).strip()
    prop.city   = request.form.get("city",   prop.city or "").strip()
    prop.state  = request.form.get("state",  prop.state or "").strip()
    prop.status = request.form.get("status", prop.status)
    try:
        db.session.commit()
        flash("Property updated.", "success")
    except Exception:
        db.session.rollback()
        flash("Update failed.", "error")
    return redirect(url_for("owner.properties"))


@owner_bp.route("/properties/<int:pid>/delete", methods=["POST"])
@login_required
@role_required("owner")
def delete_property(pid):
    prop = Property.query.filter_by(id=pid, owner_id=current_user.id).first_or_404()
    prop.is_deleted = True
    try:
        db.session.commit()
        flash("Property removed.", "success")
    except Exception:
        db.session.rollback()
        flash("Delete failed.", "error")
    return redirect(url_for("owner.properties"))


# ── Tenants ────────────────────────────────────────────────────────────────────
@owner_bp.route("/tenants")
@login_required
@role_required("owner")
def tenants():
    my_tenants = _tenant_svc.list_for_owner(current_user.id, include_inactive=False)
    my_props   = Property.query.filter_by(owner_id=current_user.id, is_deleted=False).all()
    return render_template("owner/tenants.html", tenants=my_tenants, properties=my_props)


@owner_bp.route("/vacate-notices")
@login_required
@role_required("owner")
def vacate_requests():
    requests = VacateRequest.query.filter_by(owner_id=current_user.id).order_by(
        VacateRequest.submitted_at.desc()
    ).all()
    pending = [r for r in requests if r.status == "pending"]
    approved = [r for r in requests if r.status == "approved"]
    return render_template(
        "owner/vacate_requests.html",
        requests=requests,
        pending=pending,
        approved=approved,
    )


@owner_bp.route("/vacate-notices/<int:rid>/action", methods=["POST"])
@login_required
@role_required("owner")
def vacate_request_action(rid):
    action = request.form.get("action")
    notes = request.form.get("notes", "").strip() or None
    try:
        vacate = _tenant_svc.review_vacate_request(rid, current_user.id, action, notes)
        try:
            from app import socketio
            status_label = {
                "approve": "approved",
                "reject": "rejected",
                "discuss": "needs attention",
                "finalize": "finalized"
            }.get(action, action)
            push_notification(
                socketio,
                vacate.tenant_id,
                "Vacate Notice Updated",
                f"Your vacate notice {vacate.request_id} has been {status_label}.",
                "general"
            )
        except Exception:
            pass
        flash(f"Vacate request {action}ed successfully.", "success")
    except AppError as e:
        flash(str(e), "error")
    return redirect(url_for("owner.vacate_requests"))


@owner_bp.route("/trash")
@login_required
@role_required("owner", "admin")
def tenant_trash():
    trash_tenants = _tenant_svc.list_trash_for_owner(current_user.id)
    return render_template("owner/tenant_trash.html", tenants=trash_tenants)


@owner_bp.route("/trash/<int:tid>/restore", methods=["POST"])
@login_required
@role_required("owner", "admin")
def restore_tenant(tid):
    try:
        _tenant_svc.restore_from_trash(tid, current_user.id)
        flash("Tenant restored from trash.", "success")
    except AppError as e:
        flash(str(e), "error")
    return redirect(url_for("owner.tenant_trash"))


@owner_bp.route("/api/property/<int:pid>/rooms")
@login_required
@role_required("owner")
def api_property_rooms(pid):
    """JSON: rooms with live occupancy for add-tenant form."""
    prop = Property.query.filter_by(
        id=pid, owner_id=current_user.id, is_deleted=False
    ).first_or_404()
    rooms = (
        Room.query.filter_by(property_id=pid, is_active=True)
        .order_by(Room.room_number)
        .all()
    )
    return jsonify(
        {
            "ok": True,
            "property_id": prop.id,
            "property_name": prop.name,
            "rooms": [
                {
                    "id": r.id,
                    "room_number": r.room_number,
                    "floor": r.floor,
                    "occupancy": r.get_occupancy(),
                    "max_capacity": r.max_capacity,
                    "vacant_slots": r.get_vacant_slots(),
                    "is_full": r.get_is_full(),
                }
                for r in rooms
            ],
        }
    )


@owner_bp.route("/tenants/add", methods=["POST"])
@login_required
@role_required("owner")
def add_tenant():
    try:
        data   = validate_create_tenant(request.form)
        
        # Add address to tenant data
        data["address"] = optional_string(request.form.get("address"), "address", max_len=500)
        
        tenant = _tenant_svc.create(data, owner_id=current_user.id)

        # Handle file uploads for photo and proof
        from flask import current_app
        from services import (save_uploaded_file, get_photo_filename, 
                             get_proof_filename, check_verification_status)
        
        upload_folder = current_app.config.get("UPLOAD_FOLDER", "static/uploads")
        
        photo_path = None
        proof_path = None
        
        # Save photo if uploaded
        if request.files.get("photo") and request.files["photo"].filename:
            photo_filename = get_photo_filename(tenant.full_name)
            photo_path = save_uploaded_file(
                request.files["photo"], 
                upload_folder, 
                photo_filename
            )
        
        # Save proof if uploaded
        if request.files.get("proof_id") and request.files["proof_id"].filename:
            proof_filename = get_proof_filename(tenant.full_name)
            proof_path = save_uploaded_file(
                request.files["proof_id"], 
                upload_folder, 
                proof_filename
            )
        
        # Update tenant with file paths and check verification
        if photo_path or proof_path or data.get("address"):
            from models import db
            tenant.photo = photo_path
            tenant.proof_id = proof_path
            tenant.is_verified = check_verification_status(
                data.get("address"), photo_path, proof_path
            )
            db.session.commit()

        # Optional property + room assignment
        prop_id = optional_id(request.form.get("property_id"), "property_id")
        room_raw = (request.form.get("room_id") or "").strip()
        room_id = None
        if room_raw:
            try:
                room_id = int(room_raw)
            except ValueError:
                room_id = None
        if room_id and not prop_id:
            flash("Select a property before choosing a room.", "error")
            return redirect(url_for("owner.tenants"))
        if prop_id:
            ls = optional_date(request.form.get("lease_start"), "lease_start")
            le = optional_date(request.form.get("lease_end"),   "lease_end")
            dep = optional_amount(request.form.get("deposit"), "deposit") if request.form.get("deposit") else None
            try:
                _tenant_svc.assign_to_property(
                    tenant.id, prop_id, current_user.id,
                    lease_start=ls, lease_end=le, deposit_amount=dep,
                    room_id=room_id,
                )
            except AppError as e:
                flash(f"Tenant created but property assignment failed: {e}", "error")
                return redirect(url_for("owner.tenants"))

        # Welcome notification
        try:
            from app import socketio
            u = User.query.get(tenant.id)
            extra = f" ID: {u.tenant_public_id}" if u and u.tenant_public_id else ""
            push_notification(
                socketio, tenant.id, "Welcome!",
                f"Hello {tenant.full_name}, your account is ready. Login: {tenant.phone}{extra}.",
                "chat" if (u and u.tenant_public_id) else "general",
            )
        except Exception:
            pass

        db_tenant = User.query.get(tenant.id)
        tid = db_tenant.tenant_public_id if db_tenant else None
        msg = f"Tenant '{tenant.full_name}' created. Login: {tenant.phone}"
        if tid:
            msg += f" · ID: {tid}"
        flash(msg, "success")

    except AppError as e:
        flash(str(e), "error")

    return redirect(url_for("owner.tenants"))


@owner_bp.route("/tenants/<int:tid>/edit", methods=["POST"])
@login_required
@role_required("owner")
def edit_tenant(tid):
    try:
        _tenant_svc.update(tid, current_user.id, {
            "full_name": request.form.get("full_name"),
            "password":  request.form.get("password"),
            "is_active": request.form.get("is_active") == "1",
        })
        flash("Tenant updated.", "success")
    except AppError as e:
        flash(str(e), "error")
    return redirect(url_for("owner.tenants"))


@owner_bp.route("/tenants/<int:tid>/delete", methods=["POST"])
@login_required
@role_required("owner")
def delete_tenant(tid):
    """Move tenant to trash: preserve history, hide from active dashboards."""
    try:
        _tenant_svc.archive(tid, current_user.id)
        flash("Tenant moved to trash. History preserved.", "success")
    except AppError as e:
        flash(str(e), "error")
    return redirect(url_for("owner.tenants"))


@owner_bp.route("/tenants/<int:tid>/history")
@login_required
@role_required("owner")
def tenant_history(tid):
    tenant = User.query.filter_by(id=tid, owner_id=current_user.id, role="tenant").first_or_404()
    history = _payment_svc.history(tenant.id, months=24)
    return render_template("owner/tenant_history.html", tenant=tenant, history=history)


@owner_bp.route("/tenants/<int:tid>/profile")
@login_required
@role_required("owner")
def tenant_profile(tid):
    """Display tenant profile with verification status."""
    tenant = User.query.filter_by(id=tid, owner_id=current_user.id, role="tenant").first_or_404()
    
    # Recalculate verification status
    from services import check_verification_status
    tenant.is_verified = check_verification_status(
        tenant.address, tenant.photo, tenant.proof_id
    )
    db.session.commit()
    
    # Get tenant's property assignments
    from models import PropertyTenant
    tenancies = PropertyTenant.query.filter_by(tenant_id=tid).all()
    
    # Get payment history
    history = _payment_svc.history(tenant.id, months=12)
    
    return render_template("owner/tenant_profile.html", 
                           tenant=tenant, 
                           tenancies=tenancies,
                           history=history)


@owner_bp.route("/tenants/<int:tid>/edit-profile", methods=["GET", "POST"])
@login_required
@role_required("owner")
def edit_tenant_profile(tid):
    """Edit tenant profile including address, photo, and proof ID."""
    tenant = User.query.filter_by(id=tid, owner_id=current_user.id, role="tenant").first_or_404()
    
    if request.method == "POST":
        try:
            from flask import current_app
            from services import save_uploaded_file, get_photo_filename, get_proof_filename, check_verification_status
            
            # Update basic info
            if request.form.get("full_name"):
                tenant.full_name = request.form.get("full_name")
            
            if request.form.get("address"):
                tenant.address = request.form.get("address")
            
            # Handle photo upload
            if request.files.get("photo") and request.files["photo"].filename:
                upload_folder = current_app.config.get("UPLOAD_FOLDER", "static/uploads")
                photo_filename = get_photo_filename(tenant.full_name)
                photo_path = save_uploaded_file(request.files["photo"], upload_folder, photo_filename)
                tenant.photo = photo_path
            
            # Handle proof ID upload
            if request.files.get("proof_id") and request.files["proof_id"].filename:
                upload_folder = current_app.config.get("UPLOAD_FOLDER", "static/uploads")
                proof_filename = get_proof_filename(tenant.full_name)
                proof_path = save_uploaded_file(request.files["proof_id"], upload_folder, proof_filename)
                tenant.proof_id = proof_path
            
            # Recalculate verification status
            tenant.is_verified = check_verification_status(
                tenant.address, tenant.photo, tenant.proof_id
            )
            
            db.session.commit()
            flash("✅ Profile updated successfully!", "success")
            return redirect(url_for("owner.tenant_profile", tid=tid))
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating profile: {str(e)}", "error")
            return redirect(url_for("owner.edit_tenant_profile", tid=tid))
    
    return render_template("owner/edit_tenant_profile.html", tenant=tenant)


# ── Payments ──────────────────────────────────────────────────────────────────
@owner_bp.route("/payments")
@login_required
@role_required("owner")
def payments():
    prop_ids = [p.id for p in Property.query.filter_by(
        owner_id=current_user.id, is_deleted=False).all()]
    sf = request.args.get("status", "")
    mf = request.args.get("month",  "")
    q  = (Payment.query.filter(Payment.property_id.in_(prop_ids))
          if prop_ids else Payment.query.filter_by(id=-1))
    if sf: q = q.filter_by(status=sf)
    if mf: q = q.filter_by(rent_month=mf)
    all_payments = q.order_by(Payment.rent_month.desc(),
                               Payment.created_at.desc()).all()
    months = [r[0] for r in
              db.session.query(Payment.rent_month)
              .filter(Payment.property_id.in_(prop_ids),
                      Payment.rent_month.isnot(None))
              .distinct().order_by(Payment.rent_month.desc()).all()
              ] if prop_ids else []

    my_tenants = User.query.filter_by(owner_id=current_user.id, role="tenant", is_active=True).all()
    my_props   = Property.query.filter_by(owner_id=current_user.id, is_deleted=False).all()
    return render_template("owner/payments.html",
                           payments=all_payments, tenants=my_tenants,
                           properties=my_props, status_filter=sf,
                           month_filter=mf, available_months=months)


@owner_bp.route("/payments/add", methods=["POST"])
@login_required
@role_required("owner")
def add_payment():
    try:
        data = validate_create_payment(request.form)
        pay  = _payment_svc.create(data, created_by_id=current_user.id)
        # Push real-time notification
        try:
            from app import socketio
            push_notification(socketio, pay.tenant_id,
                              "New Payment Due",
                              f"₹{pay.amount:,.0f} ({pay.payment_type}) for {pay.rent_month or 'rent'} due.",
                              "payment_due")
        except Exception:
            pass
        flash("Payment created.", "success")
    except AppError as e:
        flash(str(e), "error")
    return redirect(url_for("owner.payments"))


@owner_bp.route("/payments/<int:pid>/update-status", methods=["POST"])
@login_required
@role_required("owner")
def update_payment(pid):
    try:
        status = require_payment_status(request.form.get("status"), "status")
        _payment_svc.update_status(pid, status, updater_id=current_user.id)
        flash("Payment updated.", "success")
    except AppError as e:
        flash(str(e), "error")
    return redirect(url_for("owner.payments"))


@owner_bp.route("/payments/<int:pid>/delete", methods=["POST"])
@login_required
@role_required("owner")
def delete_payment(pid):
    try:
        _payment_svc.delete(pid, deleted_by_id=current_user.id)
        flash("Payment deleted.", "success")
    except AppError as e:
        flash(str(e), "error")
    return redirect(url_for("owner.payments"))


@owner_bp.route("/maintenance")
@login_required
@role_required("owner")
def maintenance():
    section = request.args.get("section", "overview")
    search = request.args.get("search", "").strip()
    property_id = None
    worker_id = None
    status_filter = request.args.get("status", "").strip().lower()
    category_filter = request.args.get("category", "").strip().lower()
    expense_type = request.args.get("expense_type", "").strip().lower()

    def parse_optional_int(value):
        try:
            return optional_id(value, "id")
        except ValidationError:
            return None

    try:
        property_id = parse_optional_int(request.args.get("property_id"))
        worker_id = parse_optional_int(request.args.get("worker_id"))
    except ValidationError:
        property_id = None
        worker_id = None

    properties = Property.query.filter_by(owner_id=current_user.id, is_deleted=False).order_by(Property.name).all()
    workers_q = Worker.query.filter_by(owner_id=current_user.id)
    if search:
        workers_q = workers_q.filter(
            db.or_(
                Worker.full_name.ilike(f"%{search}%"),
                Worker.role.ilike(f"%{search}%"),
                Worker.phone_number.ilike(f"%{search}%")
            )
        )
    if property_id:
        workers_q = workers_q.filter(Worker.assigned_properties.any(id=property_id))
    if status_filter in ("active", "inactive"):
        workers_q = workers_q.filter_by(active_status=(status_filter == "active"))
    worker_page = max(1, int(request.args.get("worker_page", 1)))
    workers = workers_q.order_by(Worker.active_status.desc(), Worker.full_name).paginate(page=worker_page, per_page=20, error_out=False)

    tasks_q = MaintenanceTask.query.filter_by(owner_id=current_user.id)
    if search:
        tasks_q = tasks_q.filter(
            db.or_(
                MaintenanceTask.title.ilike(f"%{search}%"),
                MaintenanceTask.description.ilike(f"%{search}%"),
                MaintenanceTask.room_number.ilike(f"%{search}%")
            )
        )
    if property_id:
        tasks_q = tasks_q.filter_by(property_id=property_id)
    if worker_id:
        tasks_q = tasks_q.filter_by(assigned_worker_id=worker_id)
    if status_filter in ("pending", "working", "completed", "cancelled"):
        tasks_q = tasks_q.filter_by(status=status_filter)
    task_page = max(1, int(request.args.get("task_page", 1)))
    tasks = tasks_q.order_by(MaintenanceTask.priority.desc(), MaintenanceTask.created_at.desc()).paginate(page=task_page, per_page=20, error_out=False)

    complaints_q = TenantComplaint.query.filter_by(owner_id=current_user.id)
    if search:
        complaints_q = complaints_q.filter(
            db.or_(
                TenantComplaint.issue_title.ilike(f"%{search}%"),
                TenantComplaint.issue_description.ilike(f"%{search}%"),
                TenantComplaint.room_number.ilike(f"%{search}%")
            )
        )
    if property_id:
        complaints_q = complaints_q.filter_by(property_id=property_id)
    if worker_id:
        complaints_q = complaints_q.filter_by(assigned_worker_id=worker_id)
    if category_filter:
        complaints_q = complaints_q.filter_by(issue_category=category_filter)
    if status_filter in ("pending", "resolved", "cancelled"):
        complaints_q = complaints_q.filter_by(status=status_filter)
    complaint_page = max(1, int(request.args.get("complaint_page", 1)))
    complaints = complaints_q.order_by(TenantComplaint.created_at.desc()).paginate(page=complaint_page, per_page=20, error_out=False)

    expenses_q = PropertyExpense.query.filter_by(owner_id=current_user.id)
    if search:
        expenses_q = expenses_q.filter(
            db.or_(
                PropertyExpense.paid_to.ilike(f"%{search}%"),
                PropertyExpense.notes.ilike(f"%{search}%")
            )
        )
    if property_id:
        expenses_q = expenses_q.filter_by(property_id=property_id)
    if worker_id:
        expenses_q = expenses_q.filter_by(worker_id=worker_id)
    if expense_type:
        expenses_q = expenses_q.filter_by(expense_type=expense_type)
    if status_filter in ("pending", "completed", "cancelled"):
        expenses_q = expenses_q.filter_by(payment_status=status_filter)
    expense_page = max(1, int(request.args.get("expense_page", 1)))
    expenses = expenses_q.order_by(PropertyExpense.expense_date.desc()).paginate(page=expense_page, per_page=20, error_out=False)

    active_workers = Worker.query.filter_by(owner_id=current_user.id, active_status=True).count()
    pending_tasks = MaintenanceTask.query.filter_by(owner_id=current_user.id, status="pending").count()
    urgent_complaints = TenantComplaint.query.filter_by(owner_id=current_user.id, status="pending").count()
    expenses_month = float(
        db.session.query(db.func.coalesce(db.func.sum(PropertyExpense.amount), 0))
        .filter(PropertyExpense.owner_id == current_user.id,
                db.func.strftime("%Y-%m", PropertyExpense.expense_date) == fmt_month())
        .scalar() or 0
    )
    today = db.func.date(now_utc())
    completed_today = MaintenanceTask.query.filter_by(owner_id=current_user.id, status="completed")
    completed_today = completed_today.filter(db.func.date(MaintenanceTask.completed_at) == db.func.date(now_utc())).count()

    return render_template(
        "owner/maintenance.html",
        section=section,
        properties=properties,
        current_property_id=property_id,
        current_worker_id=worker_id,
        current_status=status_filter,
        current_category=category_filter,
        current_expense_type=expense_type,
        search=search,
        workers=workers,
        tasks=tasks,
        complaints=complaints,
        expenses=expenses,
        active_workers=active_workers,
        pending_tasks=pending_tasks,
        urgent_complaints=urgent_complaints,
        monthly_expenses=expenses_month,
        completed_today=completed_today,
    )


@owner_bp.route("/maintenance/workers/add", methods=["POST"])
@login_required
@role_required("owner")
def add_worker():
    try:
        full_name = require_string(request.form.get("full_name"), "full_name", max_len=150)
        phone_number = require_phone(request.form.get("phone_number"), "phone_number")
        role = require_string(request.form.get("role"), "role", max_len=80)
        salary_type = require_salary_type(request.form.get("salary_type"), "salary_type") if request.form.get("salary_type") else "monthly"
        salary_amount = optional_amount(request.form.get("salary_amount"), "salary_amount")
        joined_date = optional_date(request.form.get("joined_date"), "joined_date")
        notes = optional_string(request.form.get("notes"), "notes", max_len=500)

        worker = Worker(
            owner_id=current_user.id,
            full_name=full_name,
            phone_number=phone_number,
            role=role,
            salary_type=salary_type,
            salary_amount=salary_amount,
            active_status=True,
            joined_date=joined_date.date() if joined_date else None,
            notes=notes,
        )

        property_ids = [optional_id(pid, "assigned_property_ids") for pid in request.form.getlist("assigned_property_ids") if pid]
        for pid in set(property_ids):
            prop = Property.query.filter_by(id=pid, owner_id=current_user.id, is_deleted=False).first()
            if not prop:
                raise ValidationError("Invalid property selected for worker assignment.")
            worker.assigned_properties.append(prop)

        is_temp = request.form.get("is_temp") == "1"
        worker.is_temp = is_temp
        portal_password = request.form.get("portal_password", "").strip()
        enable_login = request.form.get("enable_login") == "1"
        email = optional_string(request.form.get("email"), "email", max_len=120)
        if email:
            worker.email = email

        db.session.add(worker)
        db.session.flush()

        if enable_login and portal_password and not is_temp:
            _worker_svc.enable_portal_login(worker, portal_password, email=worker.email)
            flash("Worker added with app login.", "success")
        else:
            flash("Worker added.", "success")

        db.session.commit()
    except ValidationError as e:
        db.session.rollback()
        flash(str(e), "error")
    except Exception:
        db.session.rollback()
        flash("Unable to save worker. Please retry.", "error")
    return redirect(url_for("owner.maintenance", section="workers"))


@owner_bp.route("/maintenance/workers/<int:wid>/toggle", methods=["POST"])
@login_required
@role_required("owner")
def toggle_worker_status(wid):
    worker = Worker.query.filter_by(id=wid, owner_id=current_user.id).first_or_404()
    worker.active_status = not worker.active_status
    try:
        db.session.commit()
        flash("Worker status updated.", "success")
    except Exception:
        db.session.rollback()
        flash("Could not update worker status.", "error")
    return redirect(url_for("owner.maintenance", section="workers"))


@owner_bp.route("/maintenance/workers/<int:wid>")
@login_required
@role_required("owner")
def worker_dashboard(wid):
    worker = Worker.query.filter_by(id=wid, owner_id=current_user.id).first_or_404()
    recent_tasks = MaintenanceTask.query.filter_by(owner_id=current_user.id, assigned_worker_id=worker.id).order_by(MaintenanceTask.created_at.desc()).limit(30).all()
    recent_complaints = TenantComplaint.query.filter_by(owner_id=current_user.id, assigned_worker_id=worker.id).order_by(TenantComplaint.created_at.desc()).limit(30).all()
    recent_expenses = PropertyExpense.query.filter_by(owner_id=current_user.id, worker_id=worker.id).order_by(PropertyExpense.expense_date.desc()).limit(30).all()

    pending_tasks = MaintenanceTask.query.filter_by(owner_id=current_user.id, assigned_worker_id=worker.id, status="pending").count()
    completed_today = MaintenanceTask.query.filter_by(owner_id=current_user.id, assigned_worker_id=worker.id, status="completed")
    completed_today = completed_today.filter(db.func.date(MaintenanceTask.completed_at) == db.func.date(now_utc())).count()
    unresolved_complaints = TenantComplaint.query.filter_by(owner_id=current_user.id, assigned_worker_id=worker.id, status="pending").count()
    monthly_expense = float(
        db.session.query(db.func.coalesce(db.func.sum(PropertyExpense.amount), 0))
        .filter(PropertyExpense.owner_id == current_user.id,
                PropertyExpense.worker_id == worker.id,
                db.func.strftime("%Y-%m", PropertyExpense.expense_date) == fmt_month())
        .scalar() or 0
    )

    return render_template(
        "owner/worker_dashboard.html",
        worker=worker,
        recent_tasks=recent_tasks,
        recent_complaints=recent_complaints,
        recent_expenses=recent_expenses,
        pending_tasks=pending_tasks,
        completed_today=completed_today,
        unresolved_complaints=unresolved_complaints,
        monthly_expense=monthly_expense,
    )


@owner_bp.route("/maintenance/tasks/add", methods=["POST"])
@login_required
@role_required("owner")
def add_maintenance_task():
    try:
        title = require_string(request.form.get("title"), "title", max_len=220)
        description = optional_string(request.form.get("description"), "description", max_len=1000)
        property_id = require_id(request.form.get("property_id"), "property_id")
        prop = Property.query.filter_by(id=property_id, owner_id=current_user.id, is_deleted=False).first_or_404()
        room_number = optional_string(request.form.get("room_number"), "room_number", max_len=40)
        assigned_worker_id = optional_id(request.form.get("assigned_worker_id"), "assigned_worker_id") if request.form.get("assigned_worker_id") else None
        if assigned_worker_id:
            worker = Worker.query.filter_by(id=assigned_worker_id, owner_id=current_user.id).first_or_404()
        priority = require_task_priority(request.form.get("priority"), "priority")
        proof_images = optional_string(request.form.get("proof_images"), "proof_images", max_len=1000)
        proof_images_list = []
        if proof_images:
            proof_images_list = [u.strip() for u in proof_images.split(",") if u.strip()]

        task = MaintenanceTask(
            title=title,
            description=description,
            property_id=prop.id,
            room_number=room_number,
            assigned_worker_id=assigned_worker_id,
            priority=priority,
            status="pending",
            created_by_owner_id=current_user.id,
            proof_images=json.dumps(proof_images_list) if proof_images_list else None,
            owner_id=current_user.id,
        )
        db.session.add(task)
        db.session.flush()
        _worker_svc.notify_task_assigned(_socketio(), task)
        db.session.commit()
        flash("Task assigned.", "success")
    except ValidationError as e:
        db.session.rollback()
        flash(str(e), "error")
    except Exception:
        db.session.rollback()
        flash("Unable to create task.", "error")
    return redirect(url_for("owner.maintenance", section="tasks"))


@owner_bp.route("/maintenance/tasks/<int:tid>/verify", methods=["POST"])
@login_required
@role_required("owner")
def verify_task_completion(tid):
    task = MaintenanceTask.query.filter_by(id=tid, owner_id=current_user.id).first_or_404()
    try:
        task.owner_verified = True
        db.session.commit()
        if task.assigned_worker:
            _worker_svc.notify_worker(
                _socketio(), task.assigned_worker,
                "Work verified", f"Owner confirmed: {task.title}",
                "worker_verified",
            )
        flash("Completion verified.", "success")
    except Exception:
        db.session.rollback()
        flash("Could not verify.", "error")
    return redirect(url_for("owner.maintenance", section="tasks"))


@owner_bp.route("/maintenance/tasks/<int:tid>/status", methods=["POST"])
@login_required
@role_required("owner")
def update_task_status(tid):
    task = MaintenanceTask.query.filter_by(id=tid, owner_id=current_user.id).first_or_404()
    try:
        status = require_task_status(request.form.get("status"), "status")
        old = task.status
        task.status = status
        task.completed_at = now_utc() if status == "completed" else None
        if status == "completed":
            task.owner_verified = request.form.get("owner_verified") == "1"
        db.session.add(TaskStatusLog(
            task_id=task.id,
            worker_id=task.assigned_worker_id,
            old_status=old,
            new_status=status,
            notes="Owner update",
        ))
        if task.assigned_worker and status in ("pending", "working") and old != status:
            _worker_svc.notify_worker(
                _socketio(), task.assigned_worker,
                "Task updated", f"{task.title} → {status}",
                "worker_task",
            )
        db.session.commit()
        flash("Task status updated.", "success")
    except ValidationError as e:
        db.session.rollback()
        flash(str(e), "error")
    except Exception:
        db.session.rollback()
        flash("Unable to update task status.", "error")
    return redirect(url_for("owner.maintenance", section="tasks"))


@owner_bp.route("/maintenance/complaints/add", methods=["POST"])
@login_required
@role_required("owner")
def add_complaint():
    try:
        tenant_id = require_id(request.form.get("tenant_id"), "tenant_id")
        tenant = User.query.filter_by(id=tenant_id, owner_id=current_user.id, role="tenant").first_or_404()
        property_id = require_id(request.form.get("property_id"), "property_id")
        Property.query.filter_by(id=property_id, owner_id=current_user.id, is_deleted=False).first_or_404()
        room_number = optional_string(request.form.get("room_number"), "room_number", max_len=40)
        issue_title = require_string(request.form.get("issue_title"), "issue_title", max_len=220)
        issue_description = optional_string(request.form.get("issue_description"), "issue_description", max_len=1000)
        issue_category = require_issue_category(request.form.get("issue_category"), "issue_category")
        if issue_category == "other":
            custom_category = optional_string(request.form.get("issue_category_custom"), "issue_category_custom", max_len=60)
            if custom_category:
                issue_category = custom_category
        assigned_worker_id = optional_id(request.form.get("assigned_worker_id"), "assigned_worker_id") if request.form.get("assigned_worker_id") else None
        if assigned_worker_id:
            Worker.query.filter_by(id=assigned_worker_id, owner_id=current_user.id).first_or_404()

        complaint = TenantComplaint(
            tenant_id=tenant.id,
            property_id=property_id,
            room_number=room_number,
            issue_title=issue_title,
            issue_description=issue_description,
            issue_category=issue_category,
            status="pending",
            assigned_worker_id=assigned_worker_id,
            owner_id=current_user.id,
        )
        db.session.add(complaint)
        db.session.commit()
        flash("Complaint logged.", "success")
    except ValidationError as e:
        db.session.rollback()
        flash(str(e), "error")
    except Exception:
        db.session.rollback()
        flash("Unable to log complaint.", "error")
    return redirect(url_for("owner.maintenance", section="complaints"))


@owner_bp.route("/maintenance/expenses/add", methods=["POST"])
@login_required
@role_required("owner")
def add_expense():
    try:
        property_id = require_id(request.form.get("property_id"), "property_id")
        Property.query.filter_by(id=property_id, owner_id=current_user.id, is_deleted=False).first_or_404()
        expense_type = require_expense_type(request.form.get("expense_type"), "expense_type")
        amount = require_amount(request.form.get("amount"), "amount")
        payment_status = require_expense_status(request.form.get("payment_status"), "payment_status")
        paid_to = optional_string(request.form.get("paid_to"), "paid_to", max_len=120)
        worker_id = optional_id(request.form.get("worker_id"), "worker_id") if request.form.get("worker_id") else None
        if worker_id:
            Worker.query.filter_by(id=worker_id, owner_id=current_user.id).first_or_404()
        notes = optional_string(request.form.get("notes"), "notes", max_len=1000)
        expense_date = optional_date(request.form.get("expense_date"), "expense_date")

        expense = PropertyExpense(
            property_id=property_id,
            expense_type=expense_type,
            amount=amount,
            payment_status=payment_status,
            paid_to=paid_to,
            worker_id=worker_id,
            notes=notes,
            expense_date=expense_date.date(),
            owner_id=current_user.id,
        )
        db.session.add(expense)
        db.session.commit()
        flash("Expense recorded.", "success")
    except ValidationError as e:
        db.session.rollback()
        flash(str(e), "error")
    except Exception:
        db.session.rollback()
        flash("Unable to record expense.", "error")
    return redirect(url_for("owner.maintenance", section="expenses"))


# ── Quick Work (real-world fast entry) ────────────────────────────────────────
@owner_bp.route("/quick-work", methods=["GET", "POST"])
@login_required
@role_required("owner")
def quick_work():
    properties = Property.query.filter_by(
        owner_id=current_user.id, is_deleted=False
    ).order_by(Property.name).all()
    workers = Worker.query.filter_by(
        owner_id=current_user.id, active_status=True
    ).order_by(Worker.full_name).all()

    if request.method == "GET":
        return render_template(
            "owner/quick_work.html",
            properties=properties,
            workers=workers,
        )

    try:
        title = require_string(request.form.get("title"), "title", max_len=220)
        property_id = require_id(request.form.get("property_id"), "property_id")
        prop = Property.query.filter_by(
            id=property_id, owner_id=current_user.id, is_deleted=False
        ).first_or_404()
        room_number = optional_string(request.form.get("room_number"), "room_number", max_len=40)
        floor = optional_string(request.form.get("floor"), "floor", max_len=40)
        quantity = optional_string(request.form.get("quantity"), "quantity", max_len=60)
        scheduled_time = optional_string(request.form.get("scheduled_time"), "scheduled_time", max_len=40)
        amount = optional_amount(request.form.get("amount"), "amount")
        status = require_task_status(request.form.get("status") or "pending", "status")
        pay_status = (request.form.get("pay_status") or "none").strip().lower()
        if pay_status not in VALID_PAY_STATUS:
            pay_status = "none"
        note = optional_string(request.form.get("note"), "note", max_len=1000)
        worker_phone = optional_string(request.form.get("worker_phone"), "worker_phone", max_len=30)

        assigned_worker_id = None
        temp_worker_name = None
        worker_mode = request.form.get("worker_mode", "existing")
        if worker_mode == "existing":
            wid = request.form.get("assigned_worker_id")
            if wid:
                assigned_worker_id = optional_id(wid, "assigned_worker_id")
                Worker.query.filter_by(
                    id=assigned_worker_id, owner_id=current_user.id
                ).first_or_404()
        else:
            temp_name = require_string(request.form.get("temp_worker_name"), "temp_worker_name", max_len=150)
            temp_worker_name = temp_name
            temp_w = _worker_svc.find_or_create_temp_worker(
                current_user.id, temp_name, phone=worker_phone
            )
            assigned_worker_id = temp_w.id

        priority = "medium"
        if request.form.get("urgent") == "1":
            priority = "urgent"

        task = MaintenanceTask(
            title=title,
            description=note,
            property_id=prop.id,
            room_number=room_number,
            floor=floor,
            quantity=quantity,
            scheduled_time=scheduled_time,
            temp_worker_name=temp_worker_name,
            amount=amount,
            pay_status=pay_status,
            assigned_worker_id=assigned_worker_id,
            priority=priority,
            status=status,
            created_by_owner_id=current_user.id,
            owner_id=current_user.id,
            completed_at=now_utc() if status == "completed" else None,
            owner_verified=status == "completed",
        )
        db.session.add(task)
        db.session.flush()
        db.session.add(TaskStatusLog(
            task_id=task.id,
            worker_id=assigned_worker_id,
            old_status=None,
            new_status=status,
            notes="Quick work created",
        ))
        _worker_svc.notify_task_assigned(_socketio(), task)
        db.session.commit()
        flash("Work saved.", "success")
        return redirect(url_for("owner.quick_work"))
    except ValidationError as e:
        db.session.rollback()
        flash(str(e), "error")
    except Exception:
        db.session.rollback()
        flash("Could not save work.", "error")
    return redirect(url_for("owner.quick_work"))


@owner_bp.route("/maintenance/workers/<int:wid>/enable-portal", methods=["POST"])
@login_required
@role_required("owner")
def enable_worker_portal(wid):
    worker = Worker.query.filter_by(id=wid, owner_id=current_user.id).first_or_404()
    try:
        password = require_string(request.form.get("portal_password"), "portal_password", max_len=64)
        email = optional_string(request.form.get("email"), "email", max_len=120)
        _worker_svc.enable_portal_login(worker, password, email=email)
        db.session.commit()
        flash(f"App login enabled for {worker.full_name}.", "success")
    except ValidationError as e:
        db.session.rollback()
        flash(str(e), "error")
    except Exception:
        db.session.rollback()
        flash("Could not enable portal.", "error")
    return redirect(url_for("owner.maintenance", section="workers"))


# ── Notification ──────────────────────────────────────────────────────────────
@owner_bp.route("/notify", methods=["POST"])
@login_required
@role_required("owner")
def send_notification():
    from static.utils.validators import require_id, require_string
    try:
        tid   = require_id(request.form.get("tenant_id"), "tenant_id")
        title = require_string(request.form.get("title"), "title", max_len=255)
        body  = require_string(request.form.get("body"),  "body",  max_len=1000)
        try:
            from app import socketio
            push_notification(socketio, tid, title, body, "general")
        except Exception:
            push_notification(None, tid, title, body, "general")
        flash("Notification sent.", "success")
    except AppError as e:
        flash(str(e), "error")
    return redirect(url_for("owner.dashboard"))


# ── JSON API ──────────────────────────────────────────────────────────────────
@owner_bp.route("/api/stats")
@login_required
@role_required("owner")
def api_stats():
    props  = Property.query.filter_by(owner_id=current_user.id, is_deleted=False).all()
    p_ids  = [p.id for p in props]
    rooms  = Room.query.filter_by(owner_id=current_user.id, is_active=True).all()
    return jsonify({
        "ok": True,
        "data": {
            "total_rooms":    len(rooms),
            "occupied_rooms": sum(1 for r in rooms if r.get_occupancy() > 0),
            "vacant_rooms":   sum(1 for r in rooms if r.get_occupancy() == 0),
            "total_tenants":  User.query.filter_by(owner_id=current_user.id, role="tenant").count(),
            "pending_payments": Payment.query.filter(
                Payment.property_id.in_(p_ids), Payment.status == "pending"
            ).count() if p_ids else 0,
        }
    })
