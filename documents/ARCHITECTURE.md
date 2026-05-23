# PropFlow — System Architecture

> Property / PG / hostel management platform for the Indian rental market.  
> Single deployable monolith — server-rendered UI, session auth, real-time messaging.

---

## 1. System Overview

| Aspect | Description |
|--------|-------------|
| **Purpose** | End-to-end management of paying-guest and hostel properties: owners run properties, tenants pay rent and communicate, admins oversee the platform |
| **Deployment model** | One application process, one database, one file store for uploads |
| **User roles** | Admin · Owner · Tenant |
| **Primary markets** | PG, hostel, shared apartment rentals |

---

## 2. Architectural Style

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLIENT (Browser)                          │
│   Server-rendered pages · Vanilla scripts · Socket.IO client    │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP · WebSocket
┌────────────────────────────▼────────────────────────────────────┐
│                     APPLICATION MONOLITH                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ Presentation │  │   Routing    │  │   Real-Time Hub      │  │
│  │   Layer      │──│   Layer      │──│   (Socket events)    │  │
│  └──────────────┘  └──────┬───────┘  └──────────┬───────────┘  │
│                           │                      │              │
│  ┌────────────────────────▼──────────────────────▼───────────┐  │
│  │              Domain / Application Services                 │  │
│  └────────────────────────┬──────────────────────────────────┘  │
│                           │                                      │
│  ┌────────────────────────▼──────────────────────────────────┐  │
│  │              Persistence & Domain Models                   │  │
│  └────────────────────────┬──────────────────────────────────┘  │
└───────────────────────────┼──────────────────────────────────────┘
                            │
         ┌──────────────────┼──────────────────┐
         ▼                  ▼                  ▼
   Relational DB      Upload Store         Environment
   (SQLite / PG)      (local filesystem)   Configuration
```

**Pattern:** Layered monolith with thin controllers, fat domain services, and a single shared persistence model.

---

## 3. Technology Stack

| Layer | Technology |
|-------|------------|
| Runtime | Python 3.12 |
| Web framework | Flask 3 |
| Authentication | Flask-Login (session cookies) |
| ORM | SQLAlchemy 2 via Flask-SQLAlchemy |
| Database | SQLite (development) · PostgreSQL (production) |
| Real-time | Flask-SocketIO (threading mode) |
| Templating | Jinja2 |
| Client assets | Vanilla CSS · Vanilla JavaScript |
| Configuration | Environment variables (python-dotenv) |
| File handling | Werkzeug uploads · Pillow (image support in stack) |

**Not in scope:** Separate SPA, payment gateway, SMS/email providers, message queue, Redis, microservices, container orchestration definitions in-repo.

---

## 4. Layered Architecture

### 4.1 Application Bootstrap

| Document type | Responsibility |
|---------------|----------------|
| **Application Factory** | Builds the app, wires extensions, registers routes, attaches error handlers |
| **Process Entry** | Starts HTTP + WebSocket server, runs schema setup on boot |
| **Demo Seed Routine** | Populates sample admin, owners, tenants, properties, and payments on empty database |
| **Background Maintenance Job** | Daemon thread for scheduled tenant-trash cleanup |

---

### 4.2 Configuration Layer

| Document type | Responsibility |
|---------------|----------------|
| **Environment Profile** | Secret key, database URL, upload limits, admin bootstrap credentials |
| **Environment Template** | Documented variables for deployment setup |
| **Database Migration Guide** | PostgreSQL migration steps and schema notes |

---

### 4.3 Presentation Layer

| Document type | Responsibility |
|---------------|----------------|
| **Base Layout Template** | Shared chrome: navigation, sidebar, dark mode, Socket.IO initialization |
| **Authentication Views** | Login screen |
| **Admin Dashboard Views** | Platform overview, user management, property oversight, payments |
| **Owner Dashboard Views** | Property management, tenants, rent, maintenance, vacate, trash |
| **Tenant Dashboard Views** | Rent status, notifications, vacate notice |
| **Chat Views** | Direct messages and room group chat UI |
| **Room Management Views** | Room listing, assignment, occupancy |
| **Error Page Views** | HTTP 403, 404, 500 pages |
| **Global Stylesheet** | Application-wide visual design |
| **Client Script Bundle** | UI interactions, badge polling, chat helpers |

---

### 4.4 Routing Layer (HTTP Controllers)

Thin request handlers — validate session/role, delegate to services, return HTML or JSON.

| Document type | URL namespace | Responsibility |
|---------------|---------------|----------------|
| **Authentication Controller** | `/` · `/login` · `/logout` | Login, logout, role-based redirect |
| **Admin Controller** | `/admin/*` | Platform admin UI and admin JSON APIs |
| **Owner Controller** | `/owner/*` | Owner operations, rent generation, maintenance, trash |
| **Tenant Controller** | `/tenant/*` | Tenant dashboard, simulated rent payment, vacate |
| **Chat Controller** | `/chat/*` | Send, poll, unread counts; pairs with real-time events |
| **Room Controller** | `/rooms/*` | Room CRUD, tenant assignment, room summaries |

| Cross-cutting controller concern | Description |
|----------------------------------|-------------|
| **Role Guard Decorator** | Restricts routes by `admin` · `owner` · `tenant`; returns HTML 403 or JSON error for API paths |
| **Login Requirement** | Flask-Login session check on protected routes |

---

### 4.5 Real-Time Hub

| Document type | Responsibility |
|---------------|----------------|
| **Socket Connection Manager** | Threading async mode, CORS for browser clients |
| **User Room Subscription** | Per-user channel for notifications (`user_{id}`) |
| **Chat Room Subscription** | Per-conversation channel for messages (`chat_{room_key}`) |
| **Typing Indicator Events** | Ephemeral typing state in chat |
| **Read Receipt Events** | Message read status broadcast |
| **Push Emitters** | `new_message`, `notification`, `read_receipt` after DB commit |

**Fallback:** HTTP polling endpoints when WebSocket delivery is delayed (documented in chat receiver fix notes).

---

### 4.6 Domain / Application Services

Business logic lives here; controllers should not embed transactions or rules.

| Document type | Responsibility |
|---------------|----------------|
| **Base Service** | Shared DB session access, common patterns |
| **Payment Service** | Rent records, monthly generation, overdue marking, payment summaries |
| **Tenant Service** | Tenant CRUD, property assignment, soft-delete, trash restore, vacate workflow |
| **Room Service** | Room capacity (1–4), assign/remove tenants, occupancy guards |
| **Message Service** | DM and group chat, file attachments, pagination, read receipts |
| **Notification Service** | In-app alerts; best-effort Socket.IO push to user rooms |
| **Tenant ID Generator** | Public tenant identifiers (e.g. `TNR-SUNB-A203-4821`) |
| **Shared Helpers** | Date/month formatting, file upload utilities, verification checks |
| **Legacy Service Facade** | Backward-compatible function exports for older controller code |
| **Deprecated Root Service Module** | Superseded duplicate — new work uses the service package |

---

### 4.7 Persistence Layer (Domain Models)

Single canonical model registry. All times stored in **UTC**; display converted to **IST**.

#### Core identity

| Entity | Description |
|--------|-------------|
| **User** | Phone login, role (`admin` \| `owner` \| `tenant`), profile, verification, public tenant ID |

#### Property & occupancy

| Entity | Description |
|--------|-------------|
| **Property** | PG / hostel / apartment; short code; soft-delete flag |
| **Room** | Belongs to property; max capacity 1–4 |
| **Property Tenant** | Lease link between tenant and property (optional room) |
| **Room Tenant** | Room assignment with quick payment status |

#### Financial

| Entity | Description |
|--------|-------------|
| **Payment** | Monthly rent (`YYYY-MM`); unique per tenant + property + month |
| **Property Expense** | Owner expense ledger per property |

#### Communication

| Entity | Description |
|--------|-------------|
| **Message** | Chat message; room key for DM (`dm_{min}_{max}`) or group (`rgrp_{id}`) |
| **Notification** | In-app alert (payment due, chat, system, etc.) |

#### Operations & lifecycle

| Entity | Description |
|--------|-------------|
| **Vacate Request** | Tenant-initiated move-out workflow |
| **Tenant Trash** | Soft-deleted tenant with auto-purge date |
| **Worker** | Maintenance staff per owner |
| **Maintenance Task** | Owner-created maintenance jobs |
| **Tenant Complaint** | Issue tracking from tenants |

---

### 4.8 Infrastructure & Cross-Cutting

| Document type | Responsibility |
|---------------|----------------|
| **Error Taxonomy** | Application error hierarchy (`AppError` and variants) |
| **Global Error Handlers** | JSON for `/api/*` paths; HTML error pages otherwise |
| **Structured Logger** | Application logging |
| **Input Validators** | Tenant creation, forms, business rule checks |
| **Schema Upgrade Utility** | SQLite incremental column/table patches on startup |
| **Duplicate Validator Copy** | Legacy mirror under static assets (prefer canonical utils) |

---

### 4.9 External & Storage Integration

| Integration | Role |
|-------------|------|
| **Relational database** | Primary system of record |
| **Local upload store** | Profile photos, ID proofs, chat attachments |
| **Socket.IO CDN client** | Browser WebSocket library (loaded from CDN in base layout) |

---

## 5. Security & Access Model

```
                    ┌─────────────┐
                    │   Request   │
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
              No ──│  Session?   │── Yes
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
              No ──│ Role match? │── Yes
                    └──────┬──────┘
                           ▼
                    ┌─────────────┐
                    │  Controller │
                    │  → Service  │
                    └─────────────┘
```

| Concern | Mechanism |
|---------|-----------|
| **Authentication** | Phone + password; Werkzeug password hash |
| **Session** | Flask-Login cookie session |
| **Authorization** | Role guard on blueprints; tenant verification restricts chat scope |
| **Upload safety** | Size limits; stored under dedicated upload area |

---

## 6. Domain Hierarchy

```
Admin (platform)
    │
    └── oversees all Owners, Properties, Payments, Tenant Trash

Owner
    │
    ├── Properties
    │       ├── Rooms (capacity 1–4)
    │       │       └── Room Tenants
    │       ├── Property Tenants (leases)
    │       ├── Payments (rent)
    │       └── Property Expenses
    │
    ├── Workers → Maintenance Tasks
    └── Tenants (managed users)

Tenant
    │
    ├── Property / Room assignment
    ├── Payments (view & mark paid — simulated)
    ├── Notifications
    ├── Vacate Requests
    ├── Complaints
    └── Chat (DM + room groups)
```

---

## 7. Primary Data Flows

### 7.1 Authentication flow

```
Login form → User lookup → Session created → Redirect to role dashboard
```

### 7.2 Tenant onboarding (owner)

```
Create tenant → Validate → Persist user → Assign property/room
    → Generate public tenant ID → Push notification (DB + optional socket)
```

### 7.3 Monthly rent generation

```
Owner/Admin trigger → Payment service → One payment per active lease per month
    → Idempotent by month → Notify tenants
```

### 7.4 Rent payment (simulated)

```
Tenant marks paid → Update payment status + transaction reference
    → Notification row → Socket push to tenant user room
```

### 7.5 Real-time chat

```
Send (HTTP or socket) → Message service → DB commit
    → Emit to chat room → Clients poll as fallback
    → Mark read → Read receipt event
```

### 7.6 Tenant soft-delete & trash

```
Owner deletes tenant → Tenant trash record with expiry
    → Daily background job purges expired trash entries
```

---

## 8. API Surface (Conceptual)

| API family | Consumers | Format |
|------------|-----------|--------|
| **Admin JSON API** | Admin dashboard scripts | JSON |
| **Owner JSON API** | Owner dashboard scripts | JSON |
| **Tenant unread API** | Badge polling in layout | JSON |
| **Chat HTTP API** | Chat UI send/poll/unread | JSON |
| **Room summary API** | Room management widgets | JSON |

All other interactions are **server-rendered HTML** form posts and redirects.

---

## 9. Operational Concerns

| Concern | Approach |
|---------|----------|
| **Schema evolution** | `create_all` on boot + SQLite patch utility; PostgreSQL via manual migration guide |
| **Time zones** | UTC in database; IST for user-facing strings |
| **Concurrency** | SocketIO threading mode; no distributed lock |
| **Scalability ceiling** | Single-process design; horizontal scaling would need shared session store + SocketIO adapter |
| **Payments** | Simulated only — no external payment provider |

---

## 10. Document Index (Architecture Artifacts)

| Category | Document types in this system |
|----------|------------------------------|
| **Bootstrap** | Application Factory · Process Entry · Demo Seed · Background Job |
| **Config** | Environment Profile · Environment Template · Migration Guide |
| **UI** | Base Layout · Role Dashboards · Chat Views · Room Views · Error Pages · Styles · Client Scripts |
| **HTTP** | Auth · Admin · Owner · Tenant · Chat · Room Controllers |
| **Real-time** | Socket Hub · Room Subscriptions · Event Emitters |
| **Domain** | Payment · Tenant · Room · Message · Notification Services · ID Generator · Helpers |
| **Data** | User · Property · Room · Lease · Payment · Message · Notification · Vacate · Trash · Worker · Task · Complaint · Expense entities |
| **Infra** | Errors · Logging · Validation · Schema Upgrade |
| **Ops notes** | Chat receiver behavior · PostgreSQL migration |

---

*PropFlow — layered monolith architecture reference. Conceptual names only; implementation maps to the codebase by convention.*
