"""
TenantService
-------------
All tenant lifecycle operations: create, update, safe-deactivate, hard-delete.

SAFETY RULES enforced here:
  1. phone uniqueness checked before insert
  2. owner_id verified to exist and be an owner
  3. Soft-delete preserves payments/messages/notifications
  4. Hard-delete only allowed by admin, with explicit flag
  5. Every write goes through self.transaction()
"""
import uuid
from datetime import timedelta
from models import (db, User, Property, PropertyTenant, Room,
                    Payment, RoomTenant, Notification, TenantTrash,
                    VacateRequest, now_utc)
from services.base import BaseService
from utils.errors import (
    ValidationError, NotFoundError, ConflictError, PermissionError_,
)
from static.utils.validators import validate_create_tenant


class TenantService(BaseService):

    # ── Create ─────────────────────────────────────────────────────────────────
    def create(self, data: dict, owner_id: int) -> User:
        """
        Create a tenant account owned by `owner_id`.
        `data` should already be validated via validate_create_tenant().
        """
        # Guard: owner must exist and be an owner
        owner = User.query.get(owner_id)
        if not owner:
            raise NotFoundError("Owner", owner_id)
        if owner.role not in ("owner", "admin"):
            raise PermissionError_("Only owners or admins can create tenants")

        phone     = data["phone"]
        full_name = data["full_name"]
        password  = data["password"]
        address   = data.get("address")  # Optional address for verification

        # Guard: phone must be unique
        if User.query.filter_by(phone=phone).first():
            raise ConflictError(
                f"Phone number '{phone}' is already registered",
                phone=phone,
            )

        designation = data.get("designation")

        tenant = User(
            phone       = phone,
            full_name   = full_name,
            role        = "tenant",
            owner_id    = owner_id,
            address     = address,
            designation = designation,
        )
        tenant.set_password(password)

        with self.transaction(f"create_tenant phone={phone}"):
            db.session.add(tenant)
            db.session.flush()   # populate tenant.id before commit
            if not tenant.tenant_public_id:
                from services.tenant_id import generate_tenant_public_id
                tenant.tenant_public_id = generate_tenant_public_id()

        self.log.info(
            "Tenant created",
            extra={"tenant_id": tenant.id, "phone": phone, "owner_id": owner_id},
        )
        return tenant

    # ── Assign to property ─────────────────────────────────────────────────────
    def assign_to_property(
        self,
        tenant_id:      int,
        property_id:    int,
        owner_id:       int,
        lease_start=None,
        lease_end=None,
        deposit_amount=None,
        room_id:        int | None = None,
    ) -> PropertyTenant:
        tenant = self._get_tenant(tenant_id, owner_id)
        prop   = self._get_property(property_id, owner_id)

        # Guard: not already active in this property
        existing = PropertyTenant.query.filter_by(
            tenant_id=tenant_id, property_id=property_id, status="active"
        ).first()
        if existing:
            raise ConflictError(
                f"Tenant {tenant.full_name} is already active in {prop.name}",
                tenant_id=tenant_id, property_id=property_id,
            )

        pt = PropertyTenant(
            tenant_id      = tenant_id,
            property_id    = property_id,
            lease_start    = lease_start,
            lease_end      = lease_end,
            deposit_amount = deposit_amount,
            status         = "active",
        )
        with self.transaction(
            f"assign_tenant tenant={tenant_id} prop={property_id} room={room_id}"
        ):
            db.session.add(pt)
            db.session.flush()
            prop.status = "occupied"
            if room_id:
                self._place_tenant_in_room(
                    tenant=tenant,
                    prop=prop,
                    property_tenant=pt,
                    room_id=room_id,
                    owner_id=owner_id,
                )

        self.log.info(
            "Tenant assigned to property",
            extra={
                "tenant_id": tenant_id,
                "property_id": property_id,
                "room_id": room_id,
            },
        )
        return pt

    def _place_tenant_in_room(
        self,
        tenant: User,
        prop: Property,
        property_tenant: PropertyTenant,
        room_id: int,
        owner_id: int,
    ) -> None:
        """Attach tenant to a room (capacity-checked), set public tenant ID."""
        room = Room.query.filter_by(id=room_id, is_active=True).first()
        if not room:
            raise NotFoundError("Room", room_id)
        if room.property_id != prop.id:
            raise ValidationError(
                "Selected room does not belong to this property",
            )
        caller = User.query.get(owner_id)
        if caller and caller.role == "owner" and room.owner_id != owner_id:
            raise PermissionError_("Room not owned by this owner")

        occ = RoomTenant.query.filter_by(room_id=room_id, is_active=True).count()
        if occ >= room.max_capacity:
            raise ConflictError("No Vacancy Available")

        # Single-active-room model: vacate other room assignments for this tenant
        prev = RoomTenant.query.filter_by(
            tenant_id=tenant.id, is_active=True
        ).all()
        for rt in prev:
            rt.is_active = False
            rt.vacated_at = now_utc()

        existing_rt = RoomTenant.query.filter_by(
            room_id=room_id, tenant_id=tenant.id
        ).first()
        if existing_rt:
            if existing_rt.is_active:
                raise ConflictError(
                    "Tenant is already assigned to this room",
                )
            existing_rt.is_active = True
            existing_rt.vacated_at = None
            existing_rt.payment_status = "not_paid"
        else:
            db.session.add(
                RoomTenant(
                    room_id=room_id,
                    tenant_id=tenant.id,
                    payment_status="not_paid",
                    is_active=True,
                )
            )

        property_tenant.room_id = room.id
        property_tenant.room_number = str(room.room_number)

        if not tenant.tenant_public_id:
            from services.tenant_id import generate_tenant_public_id

            tenant.tenant_public_id = generate_tenant_public_id(prop, room)

    # ── Update ─────────────────────────────────────────────────────────────────
    def update(self, tenant_id: int, owner_id: int, data: dict) -> User:
        """
        Update a tenant's full_name, password, or is_active status.
        Only the owning owner (or admin) may update.
        """
        tenant = self._get_tenant(tenant_id, owner_id)

        full_name  = data.get("full_name")
        password   = data.get("password")
        is_active  = data.get("is_active")   # None means "don't change"

        if full_name is not None:
            tenant.full_name = str(full_name).strip()[:150]
        if password:
            if len(password) < 6:
                raise ValidationError("New password must be at least 6 characters")
            tenant.set_password(password)
        if is_active is not None:
            tenant.is_active = bool(is_active)

        with self.transaction(f"update_tenant id={tenant_id}"):
            pass   # changes tracked by SQLAlchemy; transaction commits them

        self.log.info(
            "Tenant updated",
            extra={"tenant_id": tenant_id, "owner_id": owner_id,
                   "changed_fields": list(data.keys())},
        )
        return tenant

    # ── Safe deactivate (SOFT) ────────────────────────────────────────────────
    def deactivate(self, tenant_id: int, owner_id: int) -> User:
        """
        Soft-delete: marks is_active=False, vacates room assignments,
        sets property tenancies to 'vacated'.
        NEVER deletes payment history, messages, or notifications.
        """
        tenant = self._get_tenant(tenant_id, owner_id)

        with self.transaction(f"deactivate_tenant id={tenant_id}"):
            tenant.is_active = False

            # Vacate room assignments (preserve history)
            active_rooms = (RoomTenant.query
                            .filter_by(tenant_id=tenant_id, is_active=True)
                            .all())
            for rt in active_rooms:
                rt.is_active  = False
                rt.vacated_at = now_utc()
                self.log.info(
                    "Room assignment vacated",
                    extra={"room_id": rt.room_id, "tenant_id": tenant_id},
                )

            # Mark property tenancies as vacated
            active_pts = (PropertyTenant.query
                          .filter_by(tenant_id=tenant_id, status="active")
                          .all())
            
            # Batch: Get remaining active tenants per property in one query
            from sqlalchemy import func as sqlfunc
            remaining_per_property = db.session.query(
                PropertyTenant.property_id,
                sqlfunc.count(PropertyTenant.id).label('cnt')
            ).filter(
                PropertyTenant.status == "active",
                PropertyTenant.tenant_id != tenant_id
            ).group_by(PropertyTenant.property_id).all()
            remaining_dict = {r[0]: r[1] for r in remaining_per_property}
            
            for pt in active_pts:
                pt.status = "vacated"
                # Update property status if it has no remaining active tenants
                if pt.property_id and remaining_dict.get(pt.property_id, 0) == 0:
                    if pt.property:
                        pt.property.status = "available"

        self.log.info(
            "Tenant deactivated (soft-delete)",
            extra={"tenant_id": tenant_id, "owner_id": owner_id,
                   "rooms_vacated": len(active_rooms),
                   "tenancies_vacated": len(active_pts)},
        )
        return tenant

    # ── Move tenant to trash archive ────────────────────────────────────────────
    def archive(self, tenant_id: int, owner_id: int) -> User:
        tenant = self._get_tenant(tenant_id, owner_id)

        if TenantTrash.query.get(tenant_id):
            raise ConflictError("Tenant is already in trash.")

        deleted_at = now_utc()
        auto_delete_date = deleted_at + timedelta(days=100)

        with self.transaction(f"archive_tenant id={tenant_id}"):
            trash = TenantTrash(
                id=tenant.id,
                phone=tenant.phone,
                full_name=tenant.full_name,
                password_hash=tenant.password_hash,
                role=tenant.role,
                is_active=tenant.is_active,
                owner_id=tenant.owner_id,
                address=tenant.address,
                photo=tenant.photo,
                proof_id=tenant.proof_id,
                is_verified=tenant.is_verified,
                tenant_public_id=tenant.tenant_public_id,
                designation=tenant.designation,
                created_at=tenant.created_at,
                updated_at=tenant.updated_at,
                deleted_at=deleted_at,
                auto_delete_date=auto_delete_date,
            )
            db.session.add(trash)

            tenant.is_active = False

            active_rooms = (RoomTenant.query
                            .filter_by(tenant_id=tenant_id, is_active=True)
                            .all())
            for rt in active_rooms:
                rt.is_active = False
                rt.vacated_at = now_utc()
                self.log.info(
                    "Room assignment vacated",
                    extra={"room_id": rt.room_id, "tenant_id": tenant_id},
                )

            active_pts = (PropertyTenant.query
                          .filter_by(tenant_id=tenant_id, status="active")
                          .all())
            for pt in active_pts:
                pt.status = "vacated"
                if pt.property_id:
                    remaining = PropertyTenant.query.filter(
                        PropertyTenant.property_id == pt.property_id,
                        PropertyTenant.tenant_id != tenant_id,
                        PropertyTenant.status == "active",
                    ).count()
                    if remaining == 0 and pt.property:
                        pt.property.status = "available"

        self.log.info(
            "Tenant archived to trash",
            extra={"tenant_id": tenant_id, "owner_id": owner_id,
                   "rooms_vacated": len(active_rooms),
                   "tenancies_vacated": len(active_pts)},
        )
        return tenant

    def _generate_vacate_request_id(self, tenant: User) -> str:
        seed = tenant.tenant_public_id or tenant.phone or "TENANT"
        code = ''.join([c for c in seed if c.isalnum()])[-6:].upper()
        suffix = uuid.uuid4().hex[:8].upper()
        return f"VAC-{code}-{suffix}"

    def submit_vacate_request(self, tenant_id: int, vacate_date, reason: str | None = None):
        tenant = User.query.filter_by(id=tenant_id, role="tenant").first()
        if not tenant:
            raise NotFoundError("Tenant", tenant_id)
        if not tenant.is_active:
            raise ValidationError("Cannot submit a vacate notice for an inactive tenant.")
        if not tenant.owner_id:
            raise ValidationError("Tenant owner information is missing.")

        active_room = RoomTenant.query.filter_by(tenant_id=tenant_id, is_active=True).first()
        if not active_room:
            raise ValidationError("No active room assignment found. Contact your property owner.")

        existing = VacateRequest.query.filter(
            VacateRequest.tenant_id == tenant_id,
            VacateRequest.status.in_(("pending", "approved"))
        ).first()
        if existing:
            raise ConflictError(
                "A vacate notice is already in progress. Please wait for the owner to respond.",
                request_id=existing.request_id,
            )

        request_id = self._generate_vacate_request_id(tenant)
        with self.transaction(f"submit_vacate_request tenant={tenant_id}"):
            vacate = VacateRequest(
                request_id=request_id,
                tenant_id=tenant_id,
                owner_id=tenant.owner_id,
                property_id=active_room.room.property_id if active_room.room else None,
                room_id=active_room.room_id,
                room_number=str(active_room.room.room_number) if active_room.room else None,
                vacate_date=vacate_date,
                reason=reason.strip() if reason else None,
                status="pending",
            )
            db.session.add(vacate)
            db.session.flush()

        self.log.info(
            "Vacate request submitted",
            extra={"tenant_id": tenant_id, "request_id": request_id, "owner_id": tenant.owner_id},
        )
        return vacate

    def list_vacate_requests(self, owner_id: int, property_id: int | None = None, status: str | None = None):
        q = VacateRequest.query.filter_by(owner_id=owner_id)
        if property_id is not None:
            q = q.filter_by(property_id=property_id)
        if status is not None:
            q = q.filter_by(status=status)
        return q.order_by(VacateRequest.submitted_at.desc()).all()

    def get_vacate_request(self, request_id: int, owner_id: int | None = None, tenant_id: int | None = None):
        q = VacateRequest.query.filter_by(id=request_id)
        if owner_id is not None:
            q = q.filter_by(owner_id=owner_id)
        if tenant_id is not None:
            q = q.filter_by(tenant_id=tenant_id)
        return q.first()

    def review_vacate_request(self, request_id: int, owner_id: int, action: str, notes: str | None = None):
        request = self.get_vacate_request(request_id, owner_id=owner_id)
        if not request:
            raise NotFoundError("Vacate request", request_id)

        notes = notes.strip() if notes else None
        if action == "approve":
            if request.status != "pending":
                raise ConflictError("Only pending requests can be approved.")
            request.status = "approved"
            request.processed_at = now_utc()
            request.decision_by = owner_id
            request.decision_notes = notes

            lease = PropertyTenant.query.filter_by(
                tenant_id=request.tenant_id,
                property_id=request.property_id,
                status="active"
            ).first()
            if lease:
                lease.status = "vacating"

        elif action == "reject":
            if request.status not in ("pending", "approved"):
                raise ConflictError("Only pending or approved requests can be rejected.")
            request.status = "rejected"
            request.processed_at = now_utc()
            request.decision_by = owner_id
            request.decision_notes = notes

            lease = PropertyTenant.query.filter_by(
                tenant_id=request.tenant_id,
                property_id=request.property_id,
                status="vacating"
            ).first()
            if lease:
                lease.status = "active"

        elif action == "discuss":
            if request.status == "vacated":
                raise ConflictError("Cannot discuss a completed vacate request.")
            request.decision_notes = notes
            request.processed_at = now_utc()

        elif action == "finalize":
            if request.status != "approved":
                raise ConflictError("Only approved requests can be finalized.")
            self.archive(request.tenant_id, owner_id)
            request.status = "vacated"
            request.vacated_at = now_utc()
            request.processed_at = request.processed_at or now_utc()
            request.decision_by = owner_id
            request.decision_notes = notes or request.decision_notes

        else:
            raise ValidationError("Invalid vacate action.")

        with self.transaction(f"review_vacate_request id={request_id} action={action}"):
            pass

        self.log.info(
            "Vacate request updated",
            extra={"request_id": request_id, "action": action, "owner_id": owner_id},
        )
        return request

    def active_vacate_request_for_tenant(self, tenant_id: int):
        return VacateRequest.query.filter(
            VacateRequest.tenant_id == tenant_id,
            VacateRequest.status.in_(("pending", "approved", "rejected"))
        ).order_by(VacateRequest.submitted_at.desc()).first()

    def list_vacate_requests_for_tenant(self, tenant_id: int):
        return VacateRequest.query.filter_by(tenant_id=tenant_id).order_by(VacateRequest.submitted_at.desc()).all()

    def restore_from_trash(self, tenant_id: int, owner_id: int) -> User:
        tenant = self._get_tenant(tenant_id, owner_id)
        trash = TenantTrash.query.get(tenant_id)
        if not trash:
            raise NotFoundError("Trashed tenant", tenant_id)
        if tenant.is_active:
            raise ConflictError("Tenant is already active.")

        with self.transaction(f"restore_tenant id={tenant_id}"):
            tenant.is_active = True
            db.session.delete(trash)

        self.log.info(
            "Tenant restored from trash",
            extra={"tenant_id": tenant_id, "owner_id": owner_id},
        )
        return tenant

    def cleanup_trash(self) -> int:
        now = now_utc()
        expired = TenantTrash.query.filter(TenantTrash.auto_delete_date <= now).all()
        count = len(expired)
        if count > 0:
            for row in expired:
                db.session.delete(row)
            db.session.commit()
        return count

    def list_trash_for_owner(self, owner_id: int):
        return TenantTrash.query.filter_by(owner_id=owner_id).order_by(
            TenantTrash.full_name
        ).all()

    # ── Hard delete (admin only) ──────────────────────────────────────────────
    def hard_delete(self, tenant_id: int, admin_id: int) -> bool:
        """
        Permanently delete a tenant and cascade-delete related records.
        ONLY for admin users. Requires explicit call (never default).
        Logs a full audit record before deletion.
        """
        admin = User.query.get(admin_id)
        if not admin or admin.role != "admin":
            raise PermissionError_("Only admins can permanently delete tenants")

        tenant = User.query.filter_by(id=tenant_id, role="tenant").first()
        if not tenant:
            raise NotFoundError("Tenant", tenant_id)

        # Audit log before delete
        self.log.warning(
            "HARD DELETE tenant — PERMANENT",
            extra={
                "tenant_id":   tenant_id,
                "tenant_phone": tenant.phone,
                "tenant_name": tenant.full_name,
                "admin_id":    admin_id,
                "payments":    tenant.payments.count(),
                "messages":    tenant.sent_messages.count(),
            },
        )

        with self.transaction(f"hard_delete_tenant id={tenant_id}"):
            # ON DELETE CASCADE on all FKs handles child rows automatically.
            # We still deactivate first to update property status cleanly.
            try:
                self.deactivate(tenant_id, admin_id)
            except Exception:
                pass   # best-effort cleanup before actual delete
            db.session.delete(tenant)

        self.log.warning(
            "Tenant hard-deleted",
            extra={"tenant_id": tenant_id, "admin_id": admin_id},
        )
        return True

    # ── Query helpers ──────────────────────────────────────────────────────────
    def list_for_owner(self, owner_id: int, include_inactive: bool = False):
        q = User.query.filter_by(owner_id=owner_id, role="tenant")
        if not include_inactive:
            q = q.filter_by(is_active=True)
        return q.order_by(User.full_name).all()

    # ── Private guards ─────────────────────────────────────────────────────────
    def _get_tenant(self, tenant_id: int, owner_id: int) -> User:
        """Fetch tenant, verify it belongs to owner_id (or caller is admin)."""
        tenant = User.query.filter_by(id=tenant_id, role="tenant").first()
        if not tenant:
            raise NotFoundError("Tenant", tenant_id)

        caller = User.query.get(owner_id)
        if not caller:
            raise NotFoundError("Caller", owner_id)

        # Admin can manage any tenant; owner only their own
        if caller.role == "owner" and tenant.owner_id != owner_id:
            raise PermissionError_(
                f"Tenant {tenant_id} does not belong to owner {owner_id}",
                tenant_id=tenant_id, owner_id=owner_id,
            )
        return tenant

    def _get_property(self, property_id: int, owner_id: int) -> Property:
        prop = Property.query.filter_by(id=property_id, is_deleted=False).first()
        if not prop:
            raise NotFoundError("Property", property_id)
        caller = User.query.get(owner_id)
        if caller and caller.role == "owner" and prop.owner_id != owner_id:
            raise PermissionError_(
                f"Property {property_id} does not belong to owner {owner_id}"
            )
        return prop
