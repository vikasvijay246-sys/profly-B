"""
PropFlow — Production Flask + SocketIO
Run: python app.py
"""
import os, uuid, logging, threading, time
from datetime import datetime, timedelta, timezone
from flask import Flask, jsonify, render_template, request
from flask_compress import Compress
from flask_login import LoginManager
from flask_socketio import SocketIO, join_room,test_client, emit, leave_room
from werkzeug.utils import secure_filename

from config import Config
from models import db, User
from utils.logger import configure_logging, get_logger
from utils.errors import AppError
from engineio import json as eio_json

log = get_logger(__name__)
socketio = SocketIO()


def create_app(config_class=Config):
    configure_logging("INFO")

    app = Flask(__name__)
    # Enable gzip/brotli compression for responses to reduce payload sizes
    # Compress(app)
    app.config.from_object(config_class)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)
    Compress(app)
    socketio.init_app(
        app,
        cors_allowed_origins="*",
        async_mode="threading",
        logger=False, engineio_logger=False,
        ping_timeout=60, ping_interval=25,
    )
    from routes.chat import register_socketio_events
    register_socketio_events(socketio)

    # ── Flask-Login ────────────────────────────────────────────────────────────
    lm = LoginManager()
    lm.init_app(app)
    lm.login_view = "auth.login"
    lm.login_message = "Please log in."
    lm.login_message_category = "info"

    @lm.user_loader
    def load_user(uid):
        try:
            return db.session.get(User, int(uid))
        except Exception:
            return None   # never crash the session loader

    # ── Blueprints ─────────────────────────────────────────────────────────────
    from routes.auth   import auth_bp
    from routes.admin  import admin_bp
    from routes.owner  import owner_bp
    from routes.tenant import tenant_bp
    from routes.chat   import chat_bp
    from routes.rooms  import rooms_bp
    from routes.worker import worker_bp

    for bp in [auth_bp, admin_bp, owner_bp, tenant_bp, chat_bp, rooms_bp, worker_bp]:
        app.register_blueprint(bp)

    @app.route('/sw.js')
    def service_worker():
        response = app.send_static_file('js/sw.js')
        response.headers['Content-Type'] = 'application/javascript'
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Service-Worker-Allowed'] = '/'
        return response

    @app.route('/manifest.json')
    def manifest():
        response = app.send_static_file('manifest.json')
        response.headers['Content-Type'] = 'application/manifest+json'
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response

    @app.route('/offline.html')
    def offline():
        response = app.send_static_file('offline.html')
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
        response.headers['Cache-Control'] = 'public, max-age=0, must-revalidate'
        return response

    @app.after_request
    def set_safe_cache_headers(response):
        path = request.path

        # Static assets: give them a short public cache but recommend fingerprinting
        if path.startswith('/static/'):
            if any(path.endswith(ext) for ext in ('.js', '.css', '.png', '.jpg', '.jpeg', '.svg', '.webp', '.woff2', '.woff', '.ttf', '.eot', '.otf')):
                # Non-fingerprinted assets: one week with stale-while-revalidate
                response.headers.setdefault('Cache-Control', 'public, max-age=604800, stale-while-revalidate=604800')
            return response

        # PWA core files: ensure browser always validates these
        if path in ['/sw.js', '/manifest.json', '/offline.html']:
            response.headers.setdefault('Cache-Control', 'no-cache, no-store, must-revalidate')
            return response

        # API and realtime endpoints must never be cached
        if path.startswith('/api/') or path.startswith('/chat') or path.startswith('/socket.io'):
            response.headers.setdefault('Cache-Control', 'no-store')
            return response

        # HTML pages should not be cached by intermediaries or browsers
        if response.content_type and response.content_type.startswith('text/html'):
            response.headers['Cache-Control'] = 'private, no-store, no-cache, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        return response

    # @app.after_request
    # def compress_response(response):
    #     """Enable gzip compression for text assets"""
    #     if response.content_length and response.content_length < 500:
    #         return response
    #     if not response.content_type or not any(ct in response.content_type for ct in ['text', 'json', 'javascript']):
    #         return response
    #     response.headers['Content-Encoding'] = 'gzip'
    #     return response

    # ── Global error handlers ──────────────────────────────────────────────────
    @app.errorhandler(Exception)
    def handle_exception(e):
        log.exception(f"Unhandled exception: {e}")
        if app.config["DEBUG"]:
            raise e  # in debug mode, let it crash for easier debugging
        return jsonify({"ok": False, "code": "INTERNAL_ERROR",
                        "error": "An unexpected error occurred."}), 500
        
    #  -----------------------------------------------   
    @app.errorhandler(AppError)
    def handle_app_error(err: AppError):
        err.log()
        return jsonify(err.to_dict()), err.http_status

    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({"ok": False, "code": "BAD_REQUEST",
                        "error": "Bad request"}), 400

    @app.errorhandler(403)
    def forbidden(e):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "code": "FORBIDDEN",
                            "error": "Access denied"}), 403
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "code": "NOT_FOUND",
                            "error": "Resource not found"}), 404
        return render_template("errors/404.html"), 404

    @app.errorhandler(413)
    def too_large(e):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "code": "FILE_TOO_LARGE",
                            "error": "File too large (max 10 MB)"}), 413
        return render_template("errors/413.html"), 413

    @app.errorhandler(500)
    def internal_error(e):
        db.session.rollback()   # always rollback on 500
        log.exception("Unhandled 500 error")
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "code": "INTERNAL_ERROR",
                            "error": "An internal error occurred."}), 500
        return render_template("errors/500.html"), 500

    @app.errorhandler(Exception)
    def handle_unhandled(exc):
        db.session.rollback()
        log.exception(f"Unhandled exception: {exc}")
        if app.config["DEBUG"]:
            raise exc  # in debug mode, let it crash for easier debugging
        return jsonify({"ok": False, "code": "INTERNAL_ERROR",
                        "error": "An unexpected error occurred."}), 500

    with app.app_context():
        db.create_all()
        from static.utils.schema_upgrade import upgrade_sqlite_schema
        upgrade_sqlite_schema(db)

    log.info("PropFlow app created", extra={"db": app.config["SQLALCHEMY_DATABASE_URI"][:30]})

    if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        _start_tenant_trash_cleanup(app)

    return app


def _start_tenant_trash_cleanup(app):
    def cleanup_loop():
        from services.tenant import TenantService
        with app.app_context():
            service = TenantService()
            while True:
                try:
                    deleted = service.cleanup_trash()
                    log.info("Tenant trash cleanup completed", extra={"deleted": deleted})
                except Exception as exc:
                    log.exception("Tenant trash cleanup failed", extra={"error": str(exc)})
                time.sleep(86400)

    thread = threading.Thread(target=cleanup_loop, daemon=True)
    thread.start()


# ── SocketIO event: join user's personal room ──────────────────────────────────
@socketio.on("join_user_room")
def handle_join_user_room(data=None):
    from flask_login import current_user
    if current_user.is_authenticated:
        try:
            # Join the user's personal room. Allow optional payload to join an extra room.
            join_room(f"user_{current_user.id}")
            if data and isinstance(data, dict):
                rid = data.get('receiver_id') or data.get('room')
                if rid:
                    join_room(f"user_{rid}")
        except ValueError as ve:
            log.info("join_user_room skipped: sid not connected", extra={"error": str(ve)})


# ── Seed data ──────────────────────────────────────────────────────────────────
def seed(app):
    with app.app_context():
        db.create_all()
        if User.query.count() > 0:
            log.info("DB already seeded")
            return

        from models import (Property, PropertyTenant, Payment,
                             Notification, Room, RoomTenant)
        from services.helpers import fmt_month

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        admin = User(phone=app.config["ADMIN_PHONE"],
                     full_name="System Admin", role="admin")
        admin.set_password(app.config["ADMIN_PASSWORD"])
        db.session.add(admin)

        o1 = User(phone="owner1", full_name="Rahul Sharma",  role="owner")
        o1.set_password("owner123")
        o2 = User(phone="owner2", full_name="Priya Mehta",   role="owner")
        o2.set_password("owner123")
        db.session.add_all([o1, o2])
        db.session.flush()

        tenants = []
        for ph, name in [("tenant1","Alice Johnson"),
                         ("tenant2","Bob Smith"),
                         ("tenant3","Carol Davis")]:
            t = User(phone=ph, full_name=name, role="tenant", owner_id=o1.id)
            t.set_password("tenant123")
            db.session.add(t)
            tenants.append(t)

        t4 = User(phone="tenant4", full_name="David Wilson", role="tenant", owner_id=o2.id)
        t4.set_password("tenant123")
        db.session.add(t4)
        db.session.flush()

        props = [
            Property(name="Sunrise PG Block-A", address="12 MG Road",
                     city="Bengaluru", state="KA", unit_number="A",
                     property_type="pg", bedrooms=4, bathrooms=2,
                     area_sqft=1200, monthly_rent=6000,
                     status="occupied", owner_id=o1.id),
            Property(name="Sunrise PG Block-B", address="12 MG Road",
                     city="Bengaluru", state="KA", unit_number="B",
                     property_type="pg", bedrooms=4, bathrooms=2,
                     area_sqft=1100, monthly_rent=5500,
                     status="occupied", owner_id=o1.id),
            Property(name="Green View Hostel", address="45 Anna Salai",
                     city="Chennai", state="TN", property_type="hostel",
                     bedrooms=6, bathrooms=3, area_sqft=2000,
                     monthly_rent=4500, status="available", owner_id=o1.id),
            Property(name="City PG Rooms", address="78 FC Road",
                     city="Pune", state="MH", property_type="pg",
                     bedrooms=3, bathrooms=2, area_sqft=900,
                     monthly_rent=7000, status="occupied", owner_id=o2.id),
        ]
        db.session.add_all(props)
        db.session.flush()
        from services.tenant_id import slug_property_code
        for _p in props:
            _p.short_code = slug_property_code(_p)[:16]

        rooms = [
            Room(room_number="101", max_capacity=4, description="Ground Floor A",
                 floor="Ground", property_id=props[0].id, owner_id=o1.id),
            Room(room_number="102", max_capacity=3, description="Ground Floor B",
                 floor="Ground", property_id=props[0].id, owner_id=o1.id),
            Room(room_number="201", max_capacity=4, description="First Floor",
                 floor="1st",    property_id=props[1].id, owner_id=o1.id),
            Room(room_number="301", max_capacity=4, description="Main Room",
                 floor="Ground", property_id=props[3].id, owner_id=o2.id),
        ]
        db.session.add_all(rooms)
        db.session.flush()

        db.session.add_all([
            PropertyTenant(property_id=props[0].id, tenant_id=tenants[0].id,
                room_id=rooms[0].id, room_number="101",
                lease_start=now - timedelta(days=180),
                lease_end=now + timedelta(days=185),
                deposit_amount=12000, status="active"),
            PropertyTenant(property_id=props[0].id, tenant_id=tenants[1].id,
                room_id=rooms[0].id, room_number="101",
                lease_start=now - timedelta(days=90),
                lease_end=now + timedelta(days=275),
                deposit_amount=11000, status="active"),
            PropertyTenant(property_id=props[3].id, tenant_id=t4.id,
                room_id=rooms[3].id, room_number="301",
                lease_start=now - timedelta(days=60),
                lease_end=now + timedelta(days=305),
                deposit_amount=14000, status="active"),
        ])
        db.session.add_all([
            RoomTenant(room_id=rooms[0].id, tenant_id=tenants[0].id, payment_status="paid"),
            RoomTenant(room_id=rooms[0].id, tenant_id=tenants[1].id, payment_status="not_paid"),
            RoomTenant(room_id=rooms[3].id, tenant_id=t4.id, payment_status="paid"),
        ])
        from services.tenant_id import generate_tenant_public_id
        tenants[0].tenant_public_id = generate_tenant_public_id(props[0], rooms[0])
        tenants[1].tenant_public_id = generate_tenant_public_id(props[0], rooms[0])
        t4.tenant_public_id = generate_tenant_public_id(props[3], rooms[3])

        cur_month  = fmt_month()
        prev_month = fmt_month(now - timedelta(days=32))

        def mkpay(tid, pid, amt, ptype, status, month, paid=False):
            yr, mo = int(month[:4]), int(month[5:])
            return Payment(
                tenant_id=tid, property_id=pid, amount=amt,
                payment_type=ptype, status=status, rent_month=month,
                due_date=datetime(yr, mo, 1),
                paid_at=now - timedelta(days=3) if paid else None,
                transaction_id=f"TXN-{uuid.uuid4().hex[:10].upper()}" if paid else None,
                payment_method="online" if paid else None,
                description=f"Rent — {month}",
            )

        db.session.add_all([
            mkpay(tenants[0].id, props[0].id, 6000, "rent", "completed", prev_month, True),
            mkpay(tenants[0].id, props[0].id, 6000, "rent", "pending",   cur_month),
            mkpay(tenants[1].id, props[0].id, 6000, "rent", "completed", prev_month, True),
            mkpay(tenants[1].id, props[0].id, 6000, "rent", "overdue",   cur_month),
            mkpay(t4.id,         props[3].id, 7000, "rent", "completed", prev_month, True),
            mkpay(t4.id,         props[3].id, 7000, "rent", "pending",   cur_month),
        ])
        db.session.add_all([
            Notification(user_id=tenants[0].id, title="Rent Due",
                body=f"Your rent of ₹6,000 for {cur_month} is due.",
                notif_type="payment_due"),
            Notification(user_id=tenants[1].id, title="⚠️ Payment Overdue",
                body=f"Your rent of ₹6,000 for {cur_month} is overdue.",
                notif_type="payment_overdue"),
            Notification(user_id=t4.id, title="Welcome!",
                body="Your room at City PG is ready. Login: tenant4 / tenant123",
                notif_type="general"),
        ])
        db.session.commit()

        log.info("Seed complete", extra={
            "users": User.query.count(),
            "properties": Property.query.count(),
            "rooms": Room.query.count(),
            "payments": Payment.query.count(),
        })
        log.info("Demo accounts: admin/admin123, owner1/owner123, tenant1/tenant123")


if __name__ == "__main__":
    app  = create_app()
    seed(app)
    port  = int(os.environ.get("PORT",  5000))
    debug = os.environ.get("DEBUG", "true").lower() == "true"
    log.info(f"Starting on port {port}", extra={"debug": debug})
    socketio.run(app, host="0.0.0.0", port=port,
                 debug=debug, allow_unsafe_werkzeug=True)
