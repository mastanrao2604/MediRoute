# MediRoute — Real-Time Healthcare Workforce Orchestration

> **Vision**: "Real-time healthcare workforce orchestration infrastructure" — Hospital posts urgent staffing need → nearby verified qualified professionals dispatched instantly → first acceptance wins → confirmed assignment in under 60 seconds. The Uber dispatch model, purpose-built for healthcare.
>
> **Business Parallel**: Uber (dispatch orchestration) + Swiggy (logistics + ops tooling) + Urban Company (verified professional trust) + Ola (city-shard geo marketplace) — adapted for emergency healthcare staffing.
>
> **Constraint**: No Kubernetes. No premature microservices. Evolve the existing stack safely. Preserve production stability. Build for 10M users, ship for 100. **Dispatch-first, not browsing-first.**
>
> **Core KPI**: Average Urgent Shift Fill Time (AUSFT) — target < 8 min standard, < 3 min emergency.

---

## Design Review — Issues Found & Fixed

The following issues were identified in review and are corrected inline throughout this document. Each item links to the section where the fix lives.

| # | Issue | Severity | Fixed In |
|---|---|---|---|
| 1 | `wait_for_acceptance()` was undefined — no `asyncio.Event` dict wired between dispatch coroutine and accept handler | **Critical** | [§4 Dispatch Engine](#4-real-time-matching-engine) |
| 2 | Wave timeout math: 3 waves × 90s = 4.5 min max wait with no hospital feedback between waves | **High** | [§4 Dispatch Engine](#4-real-time-matching-engine), [§11 UX](#11-mobile-ux-architecture) |
| 3 | `idempotency_key` referenced in code (§10) but missing from the SQL schema in §7 | **High** | [§7 Database Design](#7-database-design) |
| 4 | `CREATE EXTENSION postgis` via Alembic will fail on Supabase (requires superuser) — must use Supabase dashboard | **High** | [§5 Geolocation](#5-geolocation-architecture), [§14 MVP Plan](#14-mvp-execution-plan) |
| 5 | Sync SQLAlchemy inside asyncio dispatch engine blocks the event loop — was marked "future migration" but must be fixed before going live | **High** | [§8 Performance](#8-performance-engineering) |
| 6 | FCM token stored on `nurse_availability` — disappears when nurse goes offline; should be on a dedicated `device_tokens` table | **Medium** | [§6 Real-Time](#6-real-time-communication), [§7 Database Design](#7-database-design) |
| 7 | Hospital/recruiter verification has no dispatch gate — `is_verified` exists but was never checked before fanout | **Medium** | [§12 Trust & Verification](#12-trust--verification) |
| 8 | Fixed wave sizes (5→10→20) will exhaust entire nurse pool in a new city with low supply; needs adaptive sizing | **Medium** | [§4 Dispatch Engine](#4-real-time-matching-engine) |

---

## Table of Contents

1. [Deep Codebase Analysis](#1-deep-codebase-analysis)
2. [Core Domain Model](#2-core-domain-model)
3. [Architecture Style](#3-architecture-style)
4. [Real-Time Matching Engine](#4-real-time-matching-engine)
5. [Geolocation Architecture](#5-geolocation-architecture)
6. [Real-Time Communication](#6-real-time-communication)
7. [Database Design](#7-database-design)
8. [Performance Engineering](#8-performance-engineering)
9. [Infrastructure Evolution — 0 to 10M Users](#9-infrastructure-evolution)
10. [Failure & Reliability](#10-failure--reliability)
11. [Mobile UX Architecture](#11-mobile-ux-architecture)
12. [Trust & Verification](#12-trust--verification)
13. [Security & Compliance](#13-security--compliance)
14. [MVP Execution Plan](#14-mvp-execution-plan)
15. [Hardest Engineering Problems](#15-hardest-engineering-problems)
16. [Final Recommendation](#16-final-recommendation)
17. [Platform Strategy — Lessons from Uber/Swiggy/Urban Company/Ola](#17-platform-strategy)
18. [Event-Oriented Architecture](#18-event-oriented-architecture)
19. [Operations Dashboard](#19-operations-dashboard)
20. [Observability & KPIs](#20-observability--kpis)
21. [Anti-Fraud Systems](#21-anti-fraud-systems)
22. [City Sharding Strategy](#22-city-sharding-strategy)
23. [0→10M User Scaling Roadmap](#23-010m-user-scaling-roadmap)

---

## 1. Deep Codebase Analysis

### What You Have (The Good)

| Asset | Details | Value for Instant Staffing |
|---|---|---|
| **JWT Auth** | 30-min access, 7-day refresh, OTP + Google | Reuse as-is — dispatch requests require auth |
| **Role enum** | 12 healthcare roles (nurse, icu_nurse, ot_nurse, etc.) | Perfect for dispatch targeting by specialty |
| **User model** | Phone-verified, `is_verified` flag, company_name | Verification gate already exists |
| **Job model** | role_required, location, status | Evolves into ShiftRequest with minimal schema changes |
| **Application model** | UniqueConstraint (user_id, job_id) | Race condition prevention pattern is already there |
| **FastAPI async** | Native async/await, ASGI | Required for WebSocket + background tasks — no rewrite |
| **PostgreSQL (Supabase)** | pg on aws-ap-south-1 | Supports PostGIS, SKIP LOCKED, LISTEN/NOTIFY |
| **Alembic migrations** | 3 versions, working pipeline | Safe schema evolution path |
| **Sentry** | Already wired | Production error tracking from day 1 |
| **GZip middleware** | Enabled | Mobile data compression |

### Weaknesses / Scalability Blockers

| Problem | Current State | Impact |
|---|---|---|
| **No geolocation** | `current_location` is a plain String (city name) | Can't do proximity matching — must add lat/lng |
| **No availability system** | No table, no concept | Can't know who's online and willing to take shifts |
| **No real-time layer** | Zero WebSocket/SSE code | Dispatch requires push, not pull |
| **Synchronous DB sessions** | `SessionLocal()` via `get_db()` — sync SQLAlchemy | Blocks asyncio event loop in dispatch engine — must fix with `run_in_executor` before going live |
| **No dispatch state machine** | Jobs are static listings, not active dispatch sessions | The entire orchestration layer is missing |
| **No background task queue** | FastAPI `BackgroundTasks` only (fire-and-forget, no retry) | Dispatch fanout with timeout handling needs a queue |
| **SQLite fallback in DB** | `database.py` falls back to SQLite if no env var | Not a blocker (Render has PG), but risky in dev |
| **No connection pooling config for async** | `pool_size=10, max_overflow=20` | Fine for <500 concurrent users; revisit at scale |
| **OTP stored in DB** | `OTPCode` table, queried by phone | OK for current volume; Redis would be faster at scale |
| **No hospital verification gate** | `is_verified` exists on User but is not checked before dispatch | Any recruiter account can dispatch offers to nurses — fraud risk |

### What Is Completely Missing (Must Build)

1. `NurseAvailability` — online/offline toggle with geolocation
2. `ShiftRequest` — urgent request model (evolves from Job)
3. `DispatchSession` — orchestrates the fanout-and-wait cycle
4. `DispatchOffer` — individual offer sent to each candidate
5. `LiveAssignment` — confirmed booking record
6. `ReliabilityScore` — acceptance rate, no-show tracking
7. WebSocket connection manager
8. Background dispatch engine (asyncio tasks, not Celery yet)
9. Push notification integration (FCM for Android)

---

## 2. Core Domain Model

### New Entities (add via Alembic migrations)

```python
# Conceptual models — see Section 7 for full SQL

class NurseAvailability(Base):
    """A nurse declares they are open to instant shifts right now."""
    user_id          # FK → users.id
    is_available     # Boolean, toggled by nurse
    lat              # Float — current location
    lng              # Float
    radius_km        # Int — how far willing to travel (default 10)
    available_from   # DateTime (nullable = now)
    available_until  # DateTime (nullable = end of shift window)
    last_seen        # DateTime — heartbeat timestamp
    role             # UserRole enum — cached for fast filtering

class ShiftRequest(Base):
    """An urgent staffing need posted by a hospital/recruiter."""
    posted_by_user_id  # recruiter
    hospital_name
    role_required      # UserRole enum
    lat                # Float — hospital location
    lng                # Float
    start_time         # DateTime
    duration_hours     # Int
    pay_rate           # String
    description        # Text
    status             # ShiftStatus: open | dispatching | filled | expired | cancelled
    created_at
    expires_at         # when the request auto-expires if no one accepts

class DispatchSession(Base):
    """One fanout cycle for a ShiftRequest."""
    shift_request_id   # FK → shift_requests.id
    wave               # Int — dispatch wave number (wave 1: nearest 5, wave 2: next 10, etc.)
    dispatched_at      # DateTime
    expires_at         # DateTime (acceptance window close time, e.g. +90 seconds)
    status             # DispatchStatus: active | timed_out | filled | cancelled

class DispatchOffer(Base):
    """An individual offer sent to one nurse in a dispatch wave."""
    dispatch_session_id  # FK → dispatch_sessions.id
    nurse_user_id        # FK → users.id
    shift_request_id     # FK → shift_requests.id
    offered_at           # DateTime
    expires_at           # DateTime
    status               # OfferStatus: pending | accepted | declined | timed_out | cancelled
    distance_km          # Float — snapshot at dispatch time
    response_time_sec    # Int — null until responded (for reliability scoring)

class LiveAssignment(Base):
    """Confirmed booking — nurse accepted and was the first."""
    shift_request_id   # FK → shift_requests.id
    nurse_user_id      # FK → users.id
    dispatch_offer_id  # FK → dispatch_offers.id
    assigned_at        # DateTime
    check_in_at        # DateTime (nullable)
    check_out_at       # DateTime (nullable)
    status             # AssignmentStatus: confirmed | checked_in | completed | no_show | cancelled

class ReliabilityScore(Base):
    """Running reliability metrics per nurse."""
    user_id                # FK → users.id, unique
    total_offers           # Int
    accepted               # Int
    declined               # Int
    timed_out              # Int
    no_shows               # Int
    completed              # Int
    acceptance_rate        # Float (computed, stored for fast querying)
    completion_rate        # Float
    avg_response_time_sec  # Float
    score                  # Float 0–100 (composite, updated on each event)
    updated_at             # DateTime

class PresenceState(Base):
    """Fine-grained real-time online state — the 'supply inventory' of MediRoute.
    Distinct from NurseAvailability (intent) — PresenceState is ground truth.
    Modeled after Uber's driver presence infrastructure.
    At Stage 2, this moves from PostgreSQL to Redis with 5-min TTL."""
    user_id           # FK → users.id, unique
    state             # PresenceStatus: online_available | online_busy | offline | background
    last_heartbeat    # DateTime — last ping from device (dispatch eligibility expires after 5 min)
    last_location_at  # DateTime — when GPS was last updated (must be <2 min for dispatch)
    device_id         # String — device fingerprint (anti-fraud: detect location spoofing)
    connection_type   # String: websocket | fcm_only | offline
    city_id           # String — e.g. "HYD", "BLR" (city shard routing)

class DispatchZone(Base):
    """A hyperlocal geographic zone for city-localized dispatch.
    Modeled after Uber's supply zones and Swiggy's delivery clusters.
    Embodies the Density > Geography principle — launch one zone, not one city."""
    city_id           # String — parent city (HYD, BLR, MUM, DEL)
    zone_name         # String — e.g., "Banjara Hills", "Whitefield"
    boundary          # GEOGRAPHY(Polygon) — zone boundary for containment queries
    center_lat        # Float
    center_lng        # Float
    radius_km         # Float — default dispatch radius for this zone
    active_nurses     # Int — cached count, refreshed every 60s
    avg_fill_time_sec # Float — running AUSFT for this zone (primary KPI per zone)
    is_active         # Boolean — can be disabled without code deploy

class AcceptanceWindow(Base):
    """Configurable per-urgency dispatch parameters.
    Stored in DB so ops team can tune without code changes or redeployment."""
    urgency_type      # String: emergency | urgent | standard | planned
    timeout_seconds   # Int — 30 for emergency, 90 for standard, 300 for planned
    wave_size         # Int — nurses per wave (adaptive: min(wave_size, available))
    max_radius_km     # Float — max search radius before giving up
    max_waves         # Int — how many waves before marking expired
    hospital_notify_interval_sec  # Int — how often to push status update to hospital

class ShiftTimelineEvent(Base):
    """Immutable audit log of every state change in a shift's lifecycle.
    Critical for: debugging failed dispatches, reliability scoring, hospital
    reporting, compliance audit, and future Kafka event stream migration.
    Every event written here is a future Kafka message — zero code change at Stage 3."""
    shift_request_id  # FK → shift_requests.id
    event_type        # String — canonical event constants (see §17)
    actor_id          # FK → users.id (who triggered this event)
    actor_type        # String: nurse | recruiter | admin | system
    payload           # JSONB — event-specific snapshot data
    city_id           # String — city shard (for future partition routing)
    created_at        # DateTime — IMMUTABLE, never updated
```

### State Machines

**ShiftRequest.status**:
```
open → dispatching → filled
             ↓
         expired (no accepts after all waves)
             ↓
         cancelled (recruiter cancels)
```

**DispatchOffer.status**:
```
pending → accepted (triggers LiveAssignment creation + cancel all sibling offers)
        → declined (nurse says no)
        → timed_out (acceptance window expired with no response)
        → cancelled (another nurse accepted first)
```

**LiveAssignment.status**:
```
confirmed → checked_in → completed
          → no_show (nurse never checked in past start_time + grace period)
          → cancelled (before shift starts)
```

**PresenceState.state**:
```
offline → online_available    (nurse toggles ON + heartbeat received)
online_available → online_busy  (nurse accepts an assignment)
online_busy → online_available  (assignment completes or is cancelled)
online_available → background   (app backgrounded, WS drops — FCM-only mode)
background → offline            (heartbeat stops for >5 min — auto-expire)
* → offline                     (nurse explicitly toggles OFF)
```

**Dispatch eligibility rules**:
- `online_available` + `last_location_at` < 2 min ago = **eligible**
- `background` = **eligible via FCM push only** (no WebSocket delivery)
- `online_busy` = **not eligible** (already on an assignment)
- `offline` = **not eligible**

---

## 3. Architecture Style

### Recommendation: **Modular Monolith** (for now)

**Why NOT microservices now:**
- You have 1 backend service on Render Starter plan
- Zero DevOps team — microservices = 5x operational overhead
- Your bottleneck is features, not services

**Why NOT event-driven (Kafka/RabbitMQ) now:**
- Adds a broker to manage, deploy, monitor
- Adds latency to dispatch (broker hop)
- You don't have the volume to justify it

**Modular monolith means:**
- One FastAPI process
- Internal Python modules with clear boundaries: `auth`, `jobs`, `dispatch`, `geo`, `realtime`
- Modules communicate via function calls, NOT HTTP or queues
- Each module owns its DB tables — no cross-module FK queries in code (go through services)
- When you hit limits, split individual modules into services with minimal refactoring

**Split trigger points** (do not split before these):
| Service | Split when... |
|---|---|
| Dispatch engine | >1000 concurrent dispatch sessions, 1 monolith can't handle fanout |
| Geo service | PostGIS queries >50ms p95, need dedicated PG read replica |
| Notification service | >100K push notifications/day |
| WebSocket server | >10K concurrent connections on 1 Render instance |

### The Four Stages of MediRoute Architecture

| Stage | Active Users | Key Addition | Dispatch Signal | Codename |
|---|---|---|---|---|
| **Stage 1** | 0–1K | Dispatch engine + PostGIS | `asyncio.Event` in-process | “Validate” |
| **Stage 2** | 1K–50K | Redis presence + WS clusters + ARQ | Redis pub/sub | “Operate” |
| **Stage 3** | 50K–500K | Kafka events + Redis GEO + city shards | Kafka consumers | “Scale” |
| **Stage 4** | 500K–10M | Per-city dispatch clusters + ML ranking | Per-city Kafka | “Dominate” |

**The most important principle from Uber/Ola**: Stage 1 → Stage 2 is the hardest — not technically, but operationally. Real hospitals and real nurses depend on you. Every deployment has human consequences. Never rush this transition.

**Stage 1 success criteria** (before building anything in Stage 2):
- 10+ verified nurses active simultaneously in 1 zone
- 5+ hospitals onboarded, posting real shifts
- AUSFT measured and < 20 min (unoptimized is fine — measured is mandatory)
- Zero critical dispatch failures in 30 consecutive days
- Operations Dashboard live (manual override working)

**Architecture diagram (modular monolith):**
```
┌─────────────────────────────────────────────────────────────┐
│                     FastAPI Process                          │
│                                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │  auth/   │  │  jobs/   │  │dispatch/ │  │realtime/ │   │
│  │  routes  │  │  routes  │  │  engine  │  │  ws_mgr  │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘   │
│       │              │              │              │         │
│  ┌────┴──────────────┴──────────────┴──────────────┴──────┐ │
│  │                   Shared: DB Session, Models           │ │
│  └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
          │                              │
     PostgreSQL                      FCM / MSG91
   (Supabase PG)                  (push + SMS fallback)
```

---

## 4. Real-Time Matching Engine

### The Core Loop

```
Hospital posts ShiftRequest
      │
      ▼
dispatch_engine.trigger(shift_request_id)   ← called as asyncio background task
      │
      ├── 1. Query available nurses within radius, matching role, sorted by:
      │        a) distance (closest first)
      │        b) reliability_score DESC
      │        c) last_seen (most recent heartbeat first)
      │
      ├── 2. Take Wave 1 (top N nurses, e.g. N=5 for MVP)
      │
      ├── 3. Create DispatchSession (wave=1, expires_at = now + 90s)
      │
      ├── 4. Create DispatchOffer rows (status=pending) for each nurse in wave
      │
      ├── 5. Push offer to each nurse via:
      │        - WebSocket (if connected)
      │        - FCM push notification (if not connected)
      │        - SMS fallback (if no FCM token)
      │
      ├── 6. Wait for DispatchSession.expires_at (90 seconds)
      │
      ├── 7. If accepted → create LiveAssignment, cancel remaining offers, done
      │
      └── 8. If timed out → wave 2 (expand radius, next N nurses), repeat
                If all waves exhausted → ShiftRequest.status = expired, notify hospital
```

### First-Accept-Wins (Race Condition Safe)

The critical path — two nurses accept at the same millisecond:

```sql
-- Use SKIP LOCKED + UPDATE in a single atomic transaction
-- Only ONE nurse wins the race
WITH winner AS (
  SELECT id FROM dispatch_offers
  WHERE dispatch_session_id = $1
    AND nurse_user_id = $2
    AND status = 'pending'
  FOR UPDATE SKIP LOCKED  -- skip if another transaction already locked it
)
UPDATE dispatch_offers
SET status = 'accepted', response_time_sec = $3
FROM winner
WHERE dispatch_offers.id = winner.id
RETURNING dispatch_offers.id;
```

If `RETURNING` returns 0 rows → another nurse already accepted → tell this nurse "sorry, already filled."

Then in the same transaction:
```sql
-- Cancel all other pending offers in this session
UPDATE dispatch_offers
SET status = 'cancelled'
WHERE dispatch_session_id = $1
  AND status = 'pending'
  AND nurse_user_id != $2;

-- Create LiveAssignment
INSERT INTO live_assignments (...) VALUES (...);

-- Mark ShiftRequest filled
UPDATE shift_requests SET status = 'filled' WHERE id = $3;
```

### Dispatch Engine (Python asyncio)

> ⚠️ **Known Issue — `wait_for_acceptance()` must use `asyncio.Event`**: A module-level dict of events is required so the accept handler (arriving via WebSocket) can signal the dispatch coroutine (running as a background task). This is in-process only — it breaks across multiple Render instances. That is a hard blocker for Phase 2 scaling and is resolved by switching to Redis pub/sub (see Section 9 Phase 2).

```python
# app/dispatch/engine.py

import asyncio
from datetime import datetime, timedelta

# Module-level shared state — maps dispatch_session_id → asyncio.Event
# The accept handler sets the event; the dispatch coroutine awaits it.
# ⚠️ IN-PROCESS ONLY: breaks across multiple server instances (Phase 2: use Redis pub/sub)
dispatch_events: dict[int, asyncio.Event] = {}

WAVE_TIMEOUT_SEC = 90      # acceptance window per wave (seconds)
MAX_RADIUS_KM = [5, 15, 30]  # expand radius per wave

# Urgency modes: hospitals choose when posting
URGENCY_CONFIG = {
    "urgent":  {"wave_timeout": 30,  "wave_sizes": [3, 7, 15]},   # 30s window, fewer nurses per wave
    "standard": {"wave_timeout": 90,  "wave_sizes": [5, 10, 20]},  # default
    "planned":  {"wave_timeout": 300, "wave_sizes": [10, 20, 50]}, # 5 min window, bulk outreach
}

async def run_dispatch(shift_request_id: int, db, urgency: str = "standard"):
    shift = get_shift_request(db, shift_request_id)
    config = URGENCY_CONFIG[urgency]
    wave_timeout = config["wave_timeout"]
    wave_sizes = config["wave_sizes"]
    
    for wave_num, (wave_size, radius_km) in enumerate(zip(wave_sizes, MAX_RADIUS_KM)):
        # Adaptive wave sizing: if fewer nurses are available than wave_size, offer all of them
        all_available = count_available_nurses(
            db, shift.role_required, shift.lat, shift.lng,
            radius_km=radius_km, exclude_shift_id=shift_request_id
        )
        actual_wave_size = min(wave_size, all_available)
        
        if actual_wave_size == 0:
            # Notify hospital: no nurses in this radius, expanding...
            notify_hospital_wave_status(shift.posted_by_user_id, 
                f"No nurses found within {radius_km}km. Expanding search...")
            continue
        
        nurses = find_available_nurses(
            db, shift.role_required, shift.lat, shift.lng,
            radius_km=radius_km, limit=actual_wave_size,
            exclude_shift_id=shift_request_id
        )
        
        session = create_dispatch_session(
            db, shift_request_id, wave=wave_num+1,
            expires_at=datetime.utcnow() + timedelta(seconds=wave_timeout)
        )
        
        offers = create_dispatch_offers(db, session.id, nurses, shift_request_id)
        
        # Notify hospital: wave is live
        notify_hospital_wave_status(
            shift.posted_by_user_id,
            f"Wave {wave_num+1}: Offer sent to {actual_wave_size} nurses within {radius_km}km. "
            f"Waiting up to {wave_timeout}s..."
        )
        
        # Push offer to nurses (non-blocking)
        await notify_nurses(nurses, shift, session)
        
        # Wait for acceptance or timeout using asyncio.Event
        event = asyncio.Event()
        dispatch_events[session.id] = event
        try:
            await asyncio.wait_for(event.wait(), timeout=wave_timeout)
            accepted = True
        except asyncio.TimeoutError:
            accepted = False
        finally:
            dispatch_events.pop(session.id, None)
        
        if accepted:
            return  # done — LiveAssignment already created in handle_accept()
        
        # Time out — expire this wave's offers, move to next wave
        expire_wave_offers(db, session.id)
        notify_hospital_wave_status(
            shift.posted_by_user_id,
            f"Wave {wave_num+1} timed out. No response from {actual_wave_size} nurses."
        )
    
    # All waves exhausted
    mark_shift_expired(db, shift_request_id)
    notify_hospital_no_available_nurses(shift)
```

### Acceptance Handler (called from WebSocket route)

```python
async def handle_accept(nurse_user_id: int, dispatch_session_id: int, db):
    # Atomic accept with race condition protection
    result = db.execute("""
        WITH winner AS (
          SELECT id FROM dispatch_offers
          WHERE dispatch_session_id = :session_id
            AND nurse_user_id = :nurse_id
            AND status = 'pending'
          FOR UPDATE SKIP LOCKED
        )
        UPDATE dispatch_offers SET status = 'accepted'
        FROM winner WHERE dispatch_offers.id = winner.id
        RETURNING dispatch_offers.id, dispatch_offers.shift_request_id
    """, {"session_id": dispatch_session_id, "nurse_id": nurse_user_id})
    
    row = result.fetchone()
    if not row:
        return {"success": False, "reason": "already_filled"}
    
    # Cancel all sibling offers + create LiveAssignment + mark shift filled
    finalize_assignment(db, session_id=dispatch_session_id, 
                        nurse_id=nurse_user_id, shift_id=row.shift_request_id)
    
    # Signal the dispatch engine coroutine that we're done
    # This sets the asyncio.Event that run_dispatch() is awaiting
    event = dispatch_events.get(dispatch_session_id)
    if event:
        event.set()
    # If event is not found (e.g. server restarted mid-dispatch), that is fine:
    # the janitor will clean up the session on its next 15s tick.
    
    return {"success": True}
```

---

## 5. Geolocation Architecture

### Recommendation: **PostgreSQL + PostGIS** (no Redis GEO for MVP)

**Why PostGIS, not Redis GEO:**

| Factor | PostGIS | Redis GEO |
|---|---|---|
| Ops overhead | Zero (already have PG on Supabase) | +1 service to run, monitor, back up |
| Query richness | Full SQL joins (filter by role AND distance in 1 query) | Distance only — need 2nd query to join with user data |
| Data durability | Durable by default | Volatile unless persisted |
| MVP speed | Enables in 1 Alembic migration | Setup + Heroku/Redis Cloud account needed |
| Perf at scale | Fast to ~100K active nurses (with GIST index) | Faster at >1M — not your problem yet |

**PostGIS setup on Supabase:**

> ⚠️ **Do NOT run this via Alembic migration.** Supabase restricts `CREATE EXTENSION` to superuser. If run via Alembic it will fail with a permissions error. Instead:
> 1. Open Supabase dashboard → **Database** → **Extensions**
> 2. Search for `postgis`
> 3. Click **Enable** (one click, no SQL needed)
>
> Only after enabling PostGIS in the dashboard should you run migrations that use `GEOGRAPHY` columns or `ST_*` functions.

```sql
-- Verify it is enabled (run in Supabase SQL editor after dashboard enable)
SELECT extname FROM pg_extension WHERE extname = 'postgis';
-- Should return: postgis
```

**Geolocation columns on NurseAvailability:**
```sql
ALTER TABLE nurse_availability
ADD COLUMN location geography(Point, 4326);

-- GIST spatial index — required for fast proximity queries
CREATE INDEX idx_nurse_availability_location ON nurse_availability USING GIST(location);
```

**Proximity query (SQLAlchemy + GeoAlchemy2):**
```python
from geoalchemy2 import Geography
from geoalchemy2.functions import ST_DWithin, ST_Distance, ST_MakePoint

def find_available_nurses(db, role, lat, lng, radius_km, limit, exclude_shift_id):
    hospital_point = f"ST_MakePoint({lng}, {lat})::geography"
    radius_m = radius_km * 1000
    
    return (
        db.query(NurseAvailability, User)
        .join(User, NurseAvailability.user_id == User.id)
        .filter(
            NurseAvailability.is_available == True,
            NurseAvailability.role == role,
            NurseAvailability.last_seen > datetime.utcnow() - timedelta(minutes=5),
            ST_DWithin(NurseAvailability.location, hospital_point, radius_m),
            # Exclude nurses already offered this shift in any previous wave
            ~NurseAvailability.user_id.in_(
                db.query(DispatchOffer.nurse_user_id)
                .join(DispatchSession)
                .filter(DispatchSession.shift_request_id == exclude_shift_id)
            )
        )
        .order_by(ST_Distance(NurseAvailability.location, hospital_point))
        .limit(limit)
        .all()
    )
```

**Nurse location update (heartbeat):**
```python
# Called every 30 seconds from the nurse app when availability is ON
@router.put("/availability/location")
async def update_location(lat: float, lng: float, current_user=Depends(get_current_user), db=Depends(get_db)):
    db.execute(
        """UPDATE nurse_availability 
           SET location = ST_MakePoint(:lng, :lat)::geography, last_seen = NOW()
           WHERE user_id = :user_id""",
        {"lat": lat, "lng": lng, "user_id": current_user.id}
    )
    db.commit()
```

---

## 6. Real-Time Communication

### Layer Stack

```
Nurse App (Android/Web)          Hospital App (Android/Web)
        │                                │
        │ WebSocket                      │ WebSocket (or SSE)
        │                                │
        ▼                                ▼
  FastAPI WS endpoint              FastAPI WS endpoint
  /ws/nurse/{user_id}              /ws/hospital/{user_id}
        │                                │
        └──────────┬─────────────────────┘
                   │
         ConnectionManager (in-memory dict)
         {user_id: WebSocket}
```

### WebSocket vs SSE vs Push vs Polling

| Method | Use case | Verdict |
|---|---|---|
| **WebSocket** | Bidirectional (offer → accept/decline) | Use for nurse and hospital apps |
| **SSE** | Server → client only (hospital status updates) | Fine alternative for hospital side |
| **FCM Push** | App is backgrounded/closed | Required — can't rely on WS when app is closed |
| **SMS (MSG91)** | Last resort — nurse has no internet | Keep as fallback via OTP infrastructure |
| **Polling** | Never for real-time dispatch | Adds 2–30s latency, wastes battery |

### ConnectionManager (in-process, single server)

```python
# app/realtime/manager.py

from fastapi import WebSocket
from typing import Dict
import asyncio

class ConnectionManager:
    def __init__(self):
        self.connections: Dict[int, WebSocket] = {}  # user_id → websocket
    
    async def connect(self, user_id: int, ws: WebSocket):
        await ws.accept()
        self.connections[user_id] = ws
    
    def disconnect(self, user_id: int):
        self.connections.pop(user_id, None)
    
    async def send(self, user_id: int, message: dict) -> bool:
        """Returns True if delivered via WS, False if user not connected."""
        ws = self.connections.get(user_id)
        if ws:
            try:
                await ws.send_json(message)
                return True
            except Exception:
                self.disconnect(user_id)
        return False
    
    async def broadcast(self, user_ids: list[int], message: dict):
        results = await asyncio.gather(*[self.send(uid, message) for uid in user_ids])
        return list(zip(user_ids, results))

manager = ConnectionManager()  # singleton
```

### WebSocket Route (Nurse)

```python
# app/realtime/routes.py

@router.websocket("/ws/nurse/{user_id}")
async def nurse_ws(user_id: int, ws: WebSocket, token: str = Query(...)):
    # Validate JWT from query param (WS can't send headers easily)
    user = verify_token(token)
    if not user or user.id != user_id:
        await ws.close(code=4001)
        return
    
    await manager.connect(user_id, ws)
    try:
        while True:
            data = await ws.receive_json()
            event = data.get("event")
            
            if event == "accept_offer":
                result = await handle_accept(user_id, data["dispatch_session_id"], db)
                await ws.send_json({"event": "accept_result", **result})
            
            elif event == "decline_offer":
                await handle_decline(user_id, data["dispatch_session_id"], db)
            
            elif event == "location_update":
                await update_nurse_location(user_id, data["lat"], data["lng"], db)
            
            elif event == "heartbeat":
                await ws.send_json({"event": "pong"})
    
    except WebSocketDisconnect:
        manager.disconnect(user_id)
```

### Push Notification (FCM — Firebase Cloud Messaging)

```python
# When nurse is NOT connected via WebSocket, fall back to FCM
# FCM token is read from device_tokens table (NOT nurse_availability)
import httpx

FCM_SERVER_KEY = os.getenv("FCM_SERVER_KEY")

async def send_push_notification(fcm_token: str, title: str, body: str, data: dict):
    payload = {
        "to": fcm_token,
        "notification": {"title": title, "body": body},
        "data": data,
        "priority": "high",  # Wake up app immediately
        "android": {"priority": "HIGH", "ttl": "90s"}  # Expire if not delivered in 90s
    }
    async with httpx.AsyncClient() as client:
        await client.post(
            "https://fcm.googleapis.com/fcm/send",
            json=payload,
            headers={"Authorization": f"key={FCM_SERVER_KEY}"}
        )

# Update FCM token on every app launch (PUT /devices/token)
# This keeps the token fresh even if the nurse hasn't toggled availability
@router.put("/devices/token")
async def register_device_token(fcm_token: str, platform: str = "android",
                                current_user=Depends(get_current_user), db=Depends(get_db)):
    db.execute(
        """INSERT INTO device_tokens (user_id, fcm_token, platform, updated_at)
           VALUES (:user_id, :token, :platform, NOW())
           ON CONFLICT (user_id, platform) DO UPDATE
           SET fcm_token = :token, updated_at = NOW()""",
        {"user_id": current_user.id, "token": fcm_token, "platform": platform}
    )
    db.commit()
```

---

## 7. Database Design

### New Tables (full SQL)

```sql
-- Enable PostGIS
CREATE EXTENSION IF NOT EXISTS postgis;

-- Availability: who is online and where
CREATE TABLE nurse_availability (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    is_available BOOLEAN NOT NULL DEFAULT FALSE,
    location GEOGRAPHY(Point, 4326),  -- PostGIS point (lng, lat)
    radius_km INTEGER NOT NULL DEFAULT 10,
    role VARCHAR NOT NULL,  -- cached from users.role for fast filter
    available_from TIMESTAMPTZ,
    available_until TIMESTAMPTZ,
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    -- NOTE: fcm_token is NOT stored here. It belongs on device_tokens (see below)
    -- so it persists even when the nurse goes offline.
);
CREATE INDEX idx_nurse_avail_location ON nurse_availability USING GIST(location);
CREATE INDEX idx_nurse_avail_active ON nurse_availability(role, is_available, last_seen)
    WHERE is_available = TRUE;

-- Device tokens: FCM push tokens, one per device per user
-- Stored separately so tokens survive availability toggle ON/OFF cycles
CREATE TABLE device_tokens (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    fcm_token TEXT NOT NULL,
    platform VARCHAR NOT NULL DEFAULT 'android',  -- android | ios | web
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, platform)  -- one token per user per platform
);
CREATE INDEX idx_device_tokens_user ON device_tokens(user_id);

-- Shift requests: urgent staffing needs
CREATE TABLE shift_requests (
    id SERIAL PRIMARY KEY,
    posted_by_user_id INTEGER REFERENCES users(id),
    hospital_name TEXT NOT NULL,
    role_required VARCHAR NOT NULL,
    location GEOGRAPHY(Point, 4326),
    location_address TEXT,
    start_time TIMESTAMPTZ NOT NULL,
    duration_hours INTEGER NOT NULL,
    pay_rate TEXT,
    description TEXT,
    status VARCHAR NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,  -- auto-expire if no one accepts
    idempotency_key TEXT UNIQUE  -- SHA-256 of (posted_by_user_id:role:start_time), prevents double-posts
);
CREATE INDEX idx_shift_status ON shift_requests(status, created_at);
CREATE INDEX idx_shift_idempotency ON shift_requests(idempotency_key);

-- Dispatch sessions: one fanout wave per shift
CREATE TABLE dispatch_sessions (
    id SERIAL PRIMARY KEY,
    shift_request_id INTEGER NOT NULL REFERENCES shift_requests(id),
    wave INTEGER NOT NULL DEFAULT 1,
    dispatched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'active',  -- active | timed_out | filled | cancelled
    UNIQUE(shift_request_id, wave)
);
CREATE INDEX idx_dispatch_session_shift ON dispatch_sessions(shift_request_id, status);

-- Dispatch offers: one row per nurse per wave
CREATE TABLE dispatch_offers (
    id SERIAL PRIMARY KEY,
    dispatch_session_id INTEGER NOT NULL REFERENCES dispatch_sessions(id),
    shift_request_id INTEGER NOT NULL REFERENCES shift_requests(id),
    nurse_user_id INTEGER NOT NULL REFERENCES users(id),
    offered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'pending',  -- pending | accepted | declined | timed_out | cancelled
    distance_km FLOAT,
    response_time_sec INTEGER,
    UNIQUE(shift_request_id, nurse_user_id)  -- one offer per nurse per shift (all waves)
);
CREATE INDEX idx_dispatch_offer_session ON dispatch_offers(dispatch_session_id, status);
CREATE INDEX idx_dispatch_offer_nurse ON dispatch_offers(nurse_user_id, status);

-- Live assignments: confirmed bookings
CREATE TABLE live_assignments (
    id SERIAL PRIMARY KEY,
    shift_request_id INTEGER NOT NULL UNIQUE REFERENCES shift_requests(id),
    nurse_user_id INTEGER NOT NULL REFERENCES users(id),
    dispatch_offer_id INTEGER NOT NULL REFERENCES dispatch_offers(id),
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    check_in_at TIMESTAMPTZ,
    check_out_at TIMESTAMPTZ,
    status VARCHAR NOT NULL DEFAULT 'confirmed'  -- confirmed | checked_in | completed | no_show | cancelled
);
CREATE INDEX idx_assignment_nurse ON live_assignments(nurse_user_id, status);

-- Reliability scores: one row per nurse, updated on every event
CREATE TABLE reliability_scores (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    total_offers INTEGER NOT NULL DEFAULT 0,
    accepted INTEGER NOT NULL DEFAULT 0,
    declined INTEGER NOT NULL DEFAULT 0,
    timed_out INTEGER NOT NULL DEFAULT 0,
    no_shows INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    acceptance_rate FLOAT NOT NULL DEFAULT 1.0,
    completion_rate FLOAT NOT NULL DEFAULT 1.0,
    avg_response_time_sec FLOAT,
    score FLOAT NOT NULL DEFAULT 100.0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### Key Indexing Decisions

| Index | Purpose |
|---|---|
| `GIST(location)` on nurse_availability | O(log n) proximity queries — required |
| `(role, is_available, last_seen) WHERE is_available=TRUE` | Partial index, only indexes online nurses |
| `(dispatch_session_id, status)` on offers | Fast lookup: "pending offers in session X" |
| `UNIQUE(shift_request_id, nurse_user_id)` on offers | Prevents offering same nurse twice across waves |
| `UNIQUE(shift_request_id)` on live_assignments | Ensures only 1 nurse can be assigned per shift |
| `UNIQUE(idempotency_key)` on shift_requests | Prevents duplicate shift posts within 5 minutes |
| `UNIQUE(user_id, platform)` on device_tokens | One FCM token per user per platform |

### Transaction Strategy

- **Accept offer**: single `FOR UPDATE SKIP LOCKED` transaction — atomic, no app-level locks needed
- **Availability toggle**: simple `UPDATE` — not a critical path
- **Score update**: run in background after assignment finalized — not in the critical accept path

---

## 8. Performance Engineering

### Request Latency Budget (Dispatch Critical Path)

| Step | Target | Notes |
|---|---|---|
| Hospital posts shift | <200ms | Simple INSERT |
| Dispatch engine starts | <50ms | asyncio background task, no queue hop |
| Geo query (find nurses) | <30ms | PostGIS GIST index, <100K nurses |
| Create offers (5 nurses) | <50ms | Batch INSERT |
| Push to WebSocket clients | <10ms | In-memory dict lookup |
| FCM push delivery | 1–5 sec | FCM SLA, outside our control |
| Nurse sees offer | <6 sec total | 200+50+30+50+10ms + FCM |

### Bottleneck Analysis

1. **PostGIS geo query at scale**
   - Fast to ~100K concurrent nurses on 1 Supabase instance
   - Add read replica before splitting to microservices

2. **WebSocket connections**
   - FastAPI on Render Starter: ~1K concurrent WS connections per instance
   - Render allows multiple instances — scale out when needed
   - Risk: in-memory `ConnectionManager` doesn't work across instances (see Section 10)

3. **Dispatch engine coroutines**
   - Python asyncio: handles hundreds of concurrent dispatch sessions per process
   - Blocking DB calls inside async: must use `run_in_executor` or switch to async SQLAlchemy

4. **FCM push throughput**
   - FCM supports up to 100K sends/day free
   - Batch sends to 5 nurses = negligible

### Caching Strategy

| Data | Cache | TTL |
|---|---|---|
| Available nurse count (dashboard) | In-memory `functools.lru_cache` or simple dict | 30 sec |
| Hospital's own shift list | No cache needed — recruiter volume is low | — |
| Reliability scores | DB is fine — queried only at dispatch time | — |
| JWT public keys (Google OAuth) | In-memory at startup | 24h |

**Do NOT add Redis at MVP** — zero operational benefit for <10K users. Add it when:
- OTP verification needs sub-ms lookup at >1K req/sec
- WebSocket state needs sharing across multiple Render instances

### Async SQLAlchemy — Required for Dispatch Engine (MVP, not optional)

> ⚠️ **This is not a future concern — it must be addressed before the dispatch engine goes live.** Current code uses sync SQLAlchemy. The dispatch engine runs as an `asyncio` background task. A single blocking `db.query()` on a slow Supabase connection (100ms+ on first query) **blocks the entire asyncio event loop** — stalling all WebSocket messages and HTTP requests while that query runs.
>
> Fix all DB calls inside `run_dispatch()` and related dispatch functions with `run_in_executor`:

```python
# Required: wrap every blocking DB call inside the dispatch engine
import asyncio

loop = asyncio.get_event_loop()

# Instead of:
nurses = find_available_nurses(db, role, lat, lng, radius_km, limit)

# Use:
nurses = await loop.run_in_executor(
    None, find_available_nurses, db, role, lat, lng, radius_km, limit
)
```

The long-term upgrade path is async SQLAlchemy (`create_async_engine` + `AsyncSession`) but that requires rewriting all query functions. The `run_in_executor` approach is the correct MVP shortcut — it offloads the blocking call to a thread pool without blocking the event loop.

```python
# Full async SQLAlchemy (Phase 3 upgrade, not needed at MVP)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
```

---

## 9. Infrastructure Evolution

> **Full 0→10M roadmap is in [§23](#23-010m-user-scaling-roadmap).** This section covers the immediate 4-phase evolution for reference during implementation planning.

### Scale Tier Summary

| Tier | Users | Daily Shifts | Concurrent WS | Approach | Monthly Cost |
|---|---|---|---|---|---|
| **Stage 1** | 0–1K | <100 | <200 | Monolith + PostGIS | ~$50 |
| **Stage 2** | 1K–50K | <500 | <5K | +Redis pub/sub + ARQ | ~$150 |
| **Stage 3** | 50K–500K | <5K | <50K | +Redis GEO + city shards | ~$600 |
| **Stage 4** | 500K–10M | <200K | <2M | City clusters + Kafka | ~$15K+ |

### Stage 1: Validate (0 → 1,000 nurses)

```
Render Standard ($25/mo — never Starter; dispatch engine must not sleep)
  └── FastAPI monolith
       ├── Auth (existing)
       ├── Jobs (existing)
       ├── Dispatch engine (asyncio + dispatch_events dict + janitor)
       └── WebSocket manager (in-memory ConnectionManager)

Supabase PostgreSQL Pro ($25/mo)
  └── PostGIS (enabled via dashboard — NOT Alembic)
  └── All new tables: nurse_availability, shift_requests, dispatch_sessions,
      dispatch_offers, live_assignments, reliability_scores, device_tokens,
      dispatch_zones, shift_timeline_events, presence_state

FCM (free tier)
MSG91 OTP (existing)
Sentry (existing)
```

**Cost: ~$50/month**

**Dispatch signal**: `asyncio.Event` in `dispatch_events` dict (in-process, single instance)

**Trigger to Stage 2**: WebSocket connections approaching 1K, OR dispatch sessions >20 concurrent, OR PostGIS queries >30ms p95, OR you add a second Render instance.

---

### Stage 2: Operate (1K → 50K nurses)

**Problem solved**: In-memory `dispatch_events` dict breaks across 2 server instances.

**New additions:**
- **Redis** (Upstash $20/mo or Render Redis): replaces asyncio.Event for dispatch signaling, adds WS pub/sub, OTP caching
- **ARQ** (async Redis Queue): replaces asyncio tasks for dispatch engine — jobs survive server restarts
- **Second Render instance**: load balanced for API; sticky sessions for WebSocket

```
Render (2 Standard instances, $50/mo)
  ├── FastAPI instance 1
  └── FastAPI instance 2

Redis (Upstash or Render Redis, $20/mo)
  ├── WS pub/sub: PUBLISH user:{id} "{event:offer,...}"
  ├── Dispatch signal: SET dispatch:session:{id}:accepted "1" EX 90
  ├── Presence: SET nurse:{id}:online "1" EX 300 (5 min TTL)
  └── OTP cache: SET otp:{phone} "123456" EX 300

ARQ Workers (1 Render worker instance, $25/mo)
  └── Dispatch jobs from Redis queue — survive restarts

Supabase PostgreSQL Pro ($25/mo)
  └── PgBouncer connection pooler enabled
```

**Cost: ~$150/month**

**Key change — Redis replaces asyncio.Event:**
```python
# In handle_accept() — signal dispatch coroutine via Redis instead of asyncio.Event
await redis.set(f"dispatch:session:{session_id}:accepted", "1", ex=wave_timeout)

# In ARQ worker run_dispatch() — poll Redis instead of await event.wait()
for _ in range(wave_timeout):
    if await redis.exists(f"dispatch:session:{session_id}:accepted"):
        return  # accepted — done
    await asyncio.sleep(1)
```

**Trigger to Stage 3**: Redis memory >1GB, OR active nurses in a single city >5K concurrent, OR AUSFT degrading above 10 min.

---

### Stage 3: Scale (50K → 500K nurses)

**New additions:**
- **Redis GEO** replaces PostGIS for hot dispatch queries (PostGIS kept for writes + analytics)
- **Kafka** (Confluent Cloud) for event streaming — `ShiftTimelineEvent` becomes Kafka message
- **City sharding** begins: `nurses_geo:HYD`, `nurses_geo:BLR` separate Redis namespaces
- **ClickHouse** for analytics (AUSFT dashboards, funnel analysis)
- **Dedicated WebSocket tier** (sticky-session load balancer, separate from API tier)

```
Render or AWS ECS (4-8 instances, auto-scaling)
  ├── API instances (stateless, 3x)
  ├── WebSocket instances (sticky sessions, 2x)
  └── ARQ dispatch workers (2x for concurrency)

Redis Cluster (Upstash Pro or AWS ElastiCache, ~$150/mo)
  ├── nurses_geo:HYD, nurses_geo:BLR  ← Redis GEO per city
  ├── nurse:{id}:online:{city}         ← presence per city
  ├── dispatch signal + OTP + rate limiting
  └── WS pub/sub

Kafka (Confluent Cloud Starter, ~$100/mo)
  ├── topic: shift.created, shift.filled, shift.expired
  ├── topic: offer.sent, offer.accepted, offer.declined
  └── topic: nurse.online, assignment.completed

PostgreSQL (Supabase Pro or AWS RDS, ~$50/mo)
  └── Primary (writes) + Read replica (analytics joins)

ClickHouse (~$50/mo)
  └── Consumes all Kafka topics → AUSFT dashboards
```

**Cost: ~$600/month**

**Redis GEO dispatch query (replaces PostGIS for hot path):**
```python
# Add nurse to city geo index when they go online
await redis.geoadd(f"nurses_geo:{city_id}", longitude, latitude, f"nurse:{user_id}")
await redis.set(f"nurse:{user_id}:online:{city_id}", "1", ex=300)

# Dispatch: find nearby nurses (sub-millisecond, no SQL)
nearby = await redis.georadius(
    f"nurses_geo:{city_id}", hospital_lng, hospital_lat,
    radius_km, "km", withcoord=True, withdist=True, count=20, sort="ASC"
)
# Filter by role and reliability score in-app after geo lookup
```

**Trigger to Stage 4**: Any city approaching 100K concurrent nurses, OR AUSFT degrading due to cross-city query noise, OR Kafka topic lag consistently > 5K messages.

---

### Stage 4: Dominate (500K → 10M nurses)

> See §23 for full detail. Summary: city-isolated dispatch clusters, national shared services.

```
National API Gateway (AWS ALB or Cloudflare)
  └── Routes by city_id header or geo-IP

Per-city cluster (independent, one per major city):
  ├── Dedicated FastAPI dispatch workers (auto-scaling)
  ├── Dedicated WebSocket tier (100K+ connections)
  ├── Dedicated Redis GEO + presence store
  ├── City-local PostgreSQL shard (partitioned tables)
  └── City-local Kafka topics

Shared national services:
  ├── Auth + Identity (JWT validation, OTP)
  ├── Document verification
  ├── Payments
  ├── Admin + Operations Dashboard
  └── ClickHouse national analytics cluster
```

**Cost: ~$15,000–50,000/month** (depends on active city count)

**The key architectural insight**: Every table has `city_id` from Stage 1. Every Redis key is namespaced by city from Stage 2. Kafka topics are per-city from Stage 3. Stage 4 is a deployment configuration change, not a code rewrite.

---

## 10. Failure & Reliability Design

### WebSocket Disconnect Handling

```
Nurse app loses connection mid-offer
    │
    ▼
Server detects disconnect → manager.disconnect(user_id)
    │
    ▼
Offer is still "pending" in DB → dispatch timer still running
    │
    ▼
Nurse app reconnects → sends {event: "sync"} on connect
    │
    ▼
Server checks: any pending offers for this nurse? 
    YES → re-send offer immediately
    NO  → clean state
```

**Nurse app must implement reconnect with exponential backoff:**
```javascript
// On every ws.onclose, reconnect with backoff
let delay = 1000;
ws.onclose = () => {
    setTimeout(() => reconnect(), delay);
    delay = Math.min(delay * 2, 30000); // cap at 30s
};
ws.onopen = () => {
    delay = 1000; // reset on success
    ws.send(JSON.stringify({ event: "sync" })); // ask for pending offers
};
```

### Dispatch Timeout Reliability

```python
# Problem: asyncio.sleep() is not reliable across server restarts
# 
# MVP solution: periodic background task checks for expired sessions
# 
@app.on_event("startup")
async def start_dispatch_janitor():
    asyncio.create_task(dispatch_janitor())

async def dispatch_janitor():
    """Every 15 seconds, clean up expired dispatch sessions."""
    while True:
        await asyncio.sleep(15)
        try:
            expire_stale_sessions(db)    # mark timed_out offers
            trigger_next_waves(db)       # advance shifts to next wave
            expire_old_shifts(db)        # close shifts past expires_at
        except Exception as e:
            logger.error("Janitor error: %s", e)
```

### Race Condition Prevention Checklist

| Scenario | Prevention |
|---|---|
| Two nurses accept same offer | `FOR UPDATE SKIP LOCKED` in accept transaction |
| Dispatch offers same nurse twice | `UNIQUE(shift_request_id, nurse_user_id)` on DB |
| Two dispatches for same shift | `UNIQUE(shift_request_id)` on live_assignments |
| Hospital posts same shift twice | Idempotency key in ShiftRequest (hash of hospital+role+start_time) |
| Offer accepted after session expired | Check `expires_at` inside the FOR UPDATE transaction |

### Idempotency for Hospital Posts

```python
import hashlib

def create_shift_request(db, data, posted_by_user_id):
    # Prevent duplicate posts within 5 minutes
    idempotency_key = hashlib.sha256(
        f"{posted_by_user_id}:{data.role_required}:{data.start_time}".encode()
    ).hexdigest()
    
    existing = db.query(ShiftRequest).filter(
        ShiftRequest.idempotency_key == idempotency_key,
        ShiftRequest.created_at > datetime.utcnow() - timedelta(minutes=5)
    ).first()
    
    if existing:
        return existing  # return the already-created shift
    
    # create new shift...
```

### Render Cold Start Mitigation

Render Starter plan sleeps after inactivity. With WebSockets, this is a problem.

**Solutions:**
1. Upgrade to Render Standard ($25/mo) — no sleep
2. Use UptimeRobot to ping `/health` every 5 minutes (free workaround on Starter)
3. For dispatch, Standard plan is required — can't have the dispatch engine sleeping

---

## 11. Mobile UX Architecture

### Nurse App Flow

```
[Availability Toggle ON]
         │
         ▼
App sends PUT /availability {is_available: true, lat, lng}
App opens WebSocket to /ws/nurse/{user_id}?token=...
App starts sending location heartbeats every 30 seconds

         │
         ▼ [Shift Request arrives]

WebSocket pushes:
{
  "event": "shift_offer",
  "dispatch_session_id": 42,
  "offer_id": 107,
  "shift": {
    "hospital_name": "Apollo Hospital",
    "role": "icu_nurse",
    "start_time": "2025-09-15T14:00:00Z",
    "duration_hours": 8,
    "pay_rate": "₹1,200/hr",
    "distance_km": 3.2,
    "address": "Jubilee Hills, Hyderabad"
  },
  "expires_at": "2025-09-15T13:05:30Z"  // 90 second window
}

         │
         ▼ [Nurse sees full-screen modal]

┌────────────────────────────────────┐
│  🏥 Apollo Hospital                 │
│  ICU Nurse • 8 hrs • ₹9,600 total  │
│  📍 3.2 km away • Today 2:00 PM    │
│                                    │
│  [ACCEPT]          [DECLINE]       │
│                                    │
│  ⏱ Offer expires in: 01:23        │  ← countdown timer
└────────────────────────────────────┘

         │ Accept
         ▼

WS sends: { "event": "accept_offer", "dispatch_session_id": 42 }
Server responds: { "event": "accept_result", "success": true }

         │ Success
         ▼

Navigate to: AssignmentDetail screen
Show: Hospital address, start time, contact number, check-in button
```

### Hospital App Flow

```
[Post Shift] form:
- Role required (dropdown)
- Start time (datetime picker)
- Duration (1–24 hours)
- Pay rate
- Description

         │ Submit
         ▼

POST /shifts — creates ShiftRequest
Navigate to: ShiftStatus screen

         │
         ▼ [WebSocket or SSE push from server]

Live status updates:
"Searching for nurses nearby..." (dispatching)
"Offer sent to 3 nurses" (wave 1 active, countdown)
"Nurse Priya Sharma accepted!" (filled — show profile card)
"No nurses available nearby" (expired — suggest retry or expand radius)
```

### Acceptance Window UX

- **90 seconds** is the recommended window (short enough to feel urgent, long enough to read and decide)
- Show countdown timer prominently — triggers FOMO
- Push notification when app is backgrounded: "You have a shift offer! Accept within 90s"
- On expiry: automatically dismiss modal, show "Offer expired" toast

### Availability Toggle (Critical UX)

```
Nurse Profile screen:
┌────────────────────────────────────┐
│  Available for Shifts              │
│  [  OFF  ] ────── [  ON  ]         │
│                                    │
│  When ON:                          │
│  • Share your location             │
│  • Stay connected for offers       │
│  • Battery usage: moderate         │
└────────────────────────────────────┘
```

**Android implementation**: Use Foreground Service (not background service) when availability is ON. This keeps WebSocket alive and location updates running even when app is in background.

---

## 12. Trust & Verification System

### Reliability Score Algorithm

```python
def compute_reliability_score(stats: ReliabilityScore) -> float:
    """
    Score = 0-100, higher is better.
    Penalizes no-shows heavily, rewards fast acceptance.
    """
    if stats.total_offers == 0:
        return 100.0  # new nurse, benefit of the doubt
    
    # Acceptance rate (40% weight)
    acceptance_rate = stats.accepted / stats.total_offers
    acceptance_component = acceptance_rate * 40
    
    # Completion rate (40% weight) — accepted AND showed up
    if stats.accepted > 0:
        completion_rate = stats.completed / stats.accepted
    else:
        completion_rate = 1.0
    completion_component = completion_rate * 40
    
    # Response speed (20% weight) — faster = better
    if stats.avg_response_time_sec:
        # Full 20 points if responds in <30s, 0 points if >90s
        speed_factor = max(0, (90 - stats.avg_response_time_sec) / 60)
        speed_component = min(20, speed_factor * 20)
    else:
        speed_component = 20.0
    
    score = acceptance_component + completion_component + speed_component
    
    # No-show penalty: -20 points per no-show, non-recoverable for 30 days
    no_show_penalty = min(score, stats.no_shows * 20)
    score -= no_show_penalty
    
    return max(0.0, round(score, 1))
```

### Score Impact on Dispatch Priority

```python
# Nurses are sorted in dispatch by:
.order_by(
    ST_Distance(NurseAvailability.location, hospital_point),  # primary: distance
    ReliabilityScore.score.desc(),                             # secondary: trust score
    NurseAvailability.last_seen.desc()                         # tertiary: most recently active
)
```

### Verification Tiers

| Tier | Requirements | Privileges |
|---|---|---|
| **Unverified** | Phone OTP only | Can browse shifts, cannot accept |
| **Basic** | Phone + uploaded license/degree | Can accept low-acuity roles (front_office, driver) |
| **Verified** | Admin reviewed documents | Can accept clinical roles (nurse, icu_nurse, doctor) |
| **Trusted** | Verified + score ≥ 80 + 10 completed shifts | Priority dispatch (offered first regardless of distance) |

### Hospital / Recruiter Verification (Missing — Must Add)

> ⚠️ A fake hospital posting shifts is a bigger trust problem than a fake nurse. Without recruiter verification, anyone can create an account, post a fake urgent shift, lure nurses, and waste their time — destroying trust in the platform.

The `is_verified` flag already exists on `User`. Wire it as a gate on shift dispatch:

```python
# In dispatch/engine.py — add at the very start of run_dispatch()
def run_dispatch(shift_request_id: int, db, urgency: str = "standard"):
    shift = get_shift_request(db, shift_request_id)
    recruiter = get_user_by_id(db, shift.posted_by_user_id)
    
    if not recruiter.is_verified:
        # Do not dispatch — shift stays in 'open' status but no fanout happens
        logger.warning("Dispatch blocked: recruiter %s is not verified", recruiter.id)
        return
```

**Recruiter verification flow:**
1. New recruiter registers → `is_verified = False`
2. Recruiter submits hospital proof (GST certificate, registration letter) via upload endpoint
3. Admin reviews in AdminDashboard → clicks Verify → `is_verified = True`
4. Only then can their shift posts trigger dispatch

**Verification tiers for hospitals:**

| Tier | Requirements | Dispatch Privilege |
|---|---|---|
| **Unverified recruiter** | Phone + registered | Can post shifts (status=pending), no dispatch |
| **Verified recruiter** | Admin approved hospital docs | Shifts dispatched immediately on post |
| **Trusted hospital** | Verified + 10 completed assignments | Higher priority in nurse's offer feed |

### No-Show Handling

```python
# Background job runs every 15 min
async def check_no_shows():
    overdue = db.query(LiveAssignment).filter(
        LiveAssignment.status == 'confirmed',
        LiveAssignment.check_in_at == None,
        # Shift started more than 30 min ago with no check-in
        ShiftRequest.start_time < datetime.utcnow() - timedelta(minutes=30)
    ).join(ShiftRequest).all()
    
    for assignment in overdue:
        assignment.status = 'no_show'
        update_reliability_score(db, assignment.nurse_user_id, event='no_show')
        notify_hospital_nurse_no_show(assignment)
        # Re-dispatch the shift
        asyncio.create_task(run_dispatch(assignment.shift_request_id, db))
```

---

## 13. Security & Compliance

### Healthcare-Specific Risks

| Risk | Mitigation |
|---|---|
| **Impersonation** (fake nurse accepting shifts) | Mandatory document verification before clinical role dispatch |
| **Location tracking abuse** | Location only stored during availability window; auto-deleted after shift |
| **PII in logs** | Never log phone numbers, GPS coordinates, OTPs in production |
| **Shift fraud** (hospital posts fake shifts) | Recruiter account verification + admin review for new recruiters |
| **Offer manipulation** | All offer decisions via authenticated WebSocket with JWT validation |

### Location Privacy Implementation

```python
# Auto-delete location data after availability window closes
@router.put("/availability/toggle")
async def toggle_availability(is_available: bool, current_user=Depends(get_current_user), db=Depends(get_db)):
    if not is_available:
        # Wipe location when nurse goes offline
        db.execute(
            "UPDATE nurse_availability SET location = NULL, last_seen = NOW() WHERE user_id = :id",
            {"id": current_user.id}
        )
    db.commit()
```

### OWASP Top 10 Coverage

| Vulnerability | Defense |
|---|---|
| **A01 Broken Access Control** | `require_recruiter` / `get_current_user` deps on every protected route; nurse can only accept their own offers |
| **A02 Cryptographic Failures** | JWT with HS256 + secret from env; OTPs expire in 5 min; never store plaintext secrets |
| **A03 Injection** | SQLAlchemy parameterized queries; Pydantic validation on all inputs |
| **A04 Insecure Design** | `FOR UPDATE SKIP LOCKED` for race-safe accept; idempotency keys |
| **A05 Security Misconfiguration** | Docs disabled in production; security headers middleware (already in `main.py`) |
| **A07 Auth Failures** | 30-min JWT expiry; refresh token rotation; OTP 5-min expiry |
| **A09 Logging Failures** | Sentry in production; structured logging; slow-request alerting |
| **WS Auth** | Token passed in query param on WS connect; validated before any dispatch action |

### Data Retention

- OTP codes: auto-expire in 5 min, purge daily
- Location data: purge when availability = OFF
- Dispatch offer data: retain for 90 days (reliability scoring), then archive
- Assignment records: retain permanently (legal/payroll reference)

---

## 14. MVP Execution Plan

### Phase 1 — Foundation (Week 1–2)

**Goal**: Data model in place, availability toggle working, nurses can set their location.

Tasks:
1. Enable PostGIS in Supabase **dashboard** (Database → Extensions → postgis → Enable). Do NOT do this via Alembic — it requires superuser.
2. Create Alembic migration for: `device_tokens`, `nurse_availability`, `shift_requests` (with `idempotency_key`), `dispatch_sessions`, `dispatch_offers`, `live_assignments`, `reliability_scores`
3. Add `UserRole` to `NurseAvailability` on insert (sync from User.role)
4. Create API endpoints:
   - `PUT /availability/toggle` — go online/offline
   - `PUT /availability/location` — heartbeat with lat/lng
   - `GET /availability/status` — nurse checks their own status
   - `PUT /devices/token` — register/update FCM token on app launch
5. Frontend: Availability toggle screen with location permission request
6. Test geo query with sample data in Supabase

**Deliverable**: Nurses can go online with location. Recruiters can see "X nurses online near you" count.

---

### Phase 2 — Dispatch Engine (Week 3–4)

**Goal**: Full dispatch loop working end-to-end.

Tasks:
1. Implement `dispatch/engine.py` with `run_dispatch()` asyncio coroutine
2. Implement WebSocket connection manager (`realtime/manager.py`)
3. Add WebSocket routes: `/ws/nurse/{user_id}`, `/ws/hospital/{user_id}`
4. Implement `handle_accept()` with `FOR UPDATE SKIP LOCKED`
5. Implement `dispatch_janitor()` background task on startup
6. Integrate FCM push for offline nurses (using Firebase Admin SDK)
7. Wire `POST /shifts` to trigger `asyncio.create_task(run_dispatch(...))`
8. Create `POST /shifts/{id}/accept` HTTP fallback (for when WS is not available)
9. Frontend (Nurse): full-screen offer modal with countdown timer
10. Frontend (Hospital): shift status live updates

**Deliverable**: Hospital posts shift → nurses get push notification in <6 seconds → accept works → assignment confirmed.

---

### Phase 3 — Trust & Polish (Week 5–6)

**Goal**: Reliability system, check-in/out, production hardening.

Tasks:
1. Implement `ReliabilityScore` update logic (called after each offer event)
2. Add `compute_reliability_score()` and run on every offer outcome
3. Implement no-show detection background job
4. Add check-in/check-out endpoints (QR code or GPS-verified)
5. Hospital can see nurse's reliability score on assignment confirmation
6. Admin panel: verify nurses, view all active shifts, force-expire shifts
7. Upgrade Render to Standard plan (no sleep — required for WS/dispatch)
8. Enable Render autoscaling or second instance
9. Load test: simulate 50 concurrent dispatch sessions
10. Add alerting in Sentry for dispatch failures

**Deliverable**: Full instant staffing loop with trust signals. Ready for first real hospital pilot.

---

## 15. Hardest Engineering Problems

### 1. First-Accept Race Condition Under Load

**Problem**: 5 nurses all accept within milliseconds. You can't use Python-level locks (they don't work across processes). You can't use application-level queues (adds latency). 

**Solution**: `SELECT FOR UPDATE SKIP LOCKED` in PostgreSQL. This is the correct, battle-tested approach. One transaction wins the row lock, others get 0 rows back and know they lost.

**Hard part**: Testing this. You must write a concurrent test that fires 5 simultaneous accept requests and verifies exactly 1 succeeds.

```python
# Test: race condition
import concurrent.futures
import requests

def try_accept(nurse_id, session_id, token):
    return requests.post(f"/shifts/offers/{session_id}/accept",
                        headers={"Authorization": f"Bearer {token}"})

with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
    futures = [executor.submit(try_accept, i, session_id, tokens[i]) for i in range(5)]
    results = [f.result() for f in futures]

successes = [r for r in results if r.json()["success"]]
assert len(successes) == 1, f"Expected 1 winner, got {len(successes)}"
```

### 2. WebSocket State Across Multiple Server Instances

**Problem**: Nurse connects to instance A. Hospital is on instance B. Dispatch engine runs on instance A. It pushes the assignment confirmation to the hospital — but the hospital's WebSocket is on instance B. Instance A doesn't have that connection.

**Phase 2 solution**: Redis pub/sub.
```python
# Instance A (dispatch engine wins the accept):
redis.publish(f"user:{hospital_user_id}", json.dumps({"event": "shift_filled", ...}))

# Instance B (where hospital is connected):
# Background task subscribes to all user channels for connections on THIS instance
async def redis_subscriber():
    pubsub = redis.pubsub()
    await pubsub.psubscribe("user:*")
    async for message in pubsub.listen():
        user_id = extract_user_id(message.channel)
        if user_id in manager.connections:  # only deliver if connected here
            await manager.send(user_id, json.loads(message.data))
```

### 3. Location Privacy vs. Real-Time Performance

**Problem**: Updating nurse location every 30 seconds for thousands of online nurses means thousands of `UPDATE nurse_availability SET location = ...` queries per minute.

**Solution**: 
- Only update if nurse moved >100 meters (client-side check before sending)
- Use PostgreSQL `upsert` (`INSERT ... ON CONFLICT DO UPDATE`) — single query
- For MVP, this is fine. At 10K nurses, 1 update per nurse per 30s = 333 updates/second — PostgreSQL handles 10K writes/sec easily.

### 4. Dispatch Engine Durability (Server Restart)

**Problem**: Server restarts mid-dispatch. Active asyncio coroutines are gone. Dispatch sessions in DB show `active` but no code is running them.

**Solution**: The `dispatch_janitor()` (Section 10) handles this. On startup, it finds all `active` dispatch sessions where `expires_at` has passed and advances them (expire offers → trigger next wave or mark shift expired).

For truly durable dispatch (Phase 3+), migrate from asyncio tasks to Redis Queue (RQ) or ARQ — jobs survive restarts.

### 5. Nurse App Battery Life

**Problem**: Location tracking + open WebSocket is a battery drain. Nurses will complain and turn off the app.

**Solution**:
- Location update interval: 30s when active, pause when nurse hasn't moved (use `distanceBetween` on client)
- WebSocket keepalive: server-side ping every 25s, client pong — minimal data
- Android Foreground Service with notification: "MediRoute: Looking for shifts near you" — required to keep WS alive when app is backgrounded, and users can dismiss it to stop
- Use geofencing instead of continuous GPS when nurse is stationary (Android `GeofencingClient`)

---

## 16. Final Recommendation

### Architecture in One Sentence

**One FastAPI monolith, PostgreSQL with PostGIS, WebSockets for real-time, FCM for push, asyncio for dispatch — all on Render. Build phases, not features.**

### Technology Choices (Final)

| Component | Choice | Why |
|---|---|---|
| Backend framework | FastAPI (existing) | Async, WebSocket native, already deployed |
| Database | PostgreSQL + PostGIS | Geo queries + SKIP LOCKED + no extra service |
| Real-time (app open) | WebSocket (native FastAPI) | Bidirectional, <10ms latency |
| Real-time (app closed) | FCM push notifications | Android push, free, reliable |
| SMS fallback | MSG91 (existing) | Already wired for OTP |
| Dispatch execution | asyncio background tasks → ARQ (Phase 3) | No broker at MVP; upgrade when needed |
| Location caching | PostgreSQL `nurse_availability.location` (PostGIS) | No Redis at MVP |
| Hosting | Render Standard plan (1 instance) | Simple, no ops |
| Multi-instance state | In-memory now → Redis pub/sub in Phase 2 | Don't over-engineer until you have 2 instances |

### Things to NOT Build (yet)

- ❌ Kafka or RabbitMQ — you have 0 concurrent dispatch sessions today
- ❌ Kubernetes or containers — Render handles this
- ❌ Redis — PostgreSQL is sufficient at MVP
- ❌ Celery — asyncio tasks + janitor pattern is sufficient
- ❌ Separate microservices — one monolith is faster to iterate
- ❌ A/B testing infrastructure — premature
- ❌ Analytics pipeline — use Sentry + PostHog for now
- ❌ ML ranking — sort by distance + score is good enough for 10K nurses

### Priority Order for Implementation

```
1. Alembic migration (new tables + PostGIS)       ← unblocks everything
2. Availability toggle API + frontend              ← nurses can go online
3. Shift post API with dispatch trigger            ← hospitals can post
4. WebSocket manager + nurse offer delivery        ← core UX
5. Accept handler (FOR UPDATE SKIP LOCKED)         ← correctness critical
6. FCM push for backgrounded nurses               ← adoption critical
7. Dispatch janitor (timeout + wave progression)   ← reliability critical
8. Reliability score tracking                      ← trust critical
9. Hospital live status updates                    ← hospital UX
10. No-show detection + re-dispatch               ← trust critical
```

### The Real Moat

The hard part of this system is NOT the technology — it is:

1. **Getting verified nurses to turn on availability** — market problem, not tech
2. **Getting hospitals to trust the system enough to post urgent shifts** — trust takes time
3. **Geographic density** — "Uber effect": system is useless until you have enough nurses in each city

The tech described here can be built in 6 weeks. The network effect takes 6 months to a year. Start with one city, one hospital, and five manually-recruited verified nurses. Validate the loop works before scaling the platform.

---

---

## 24. Future Roadmap Enhancements

> **Status**: Approved for future phases. DO NOT implement now. Extensibility hooks are present in Phase 1 code (marked `# FUTURE:`). Build these when the trigger metric is hit.

| # | Enhancement | Phase | Trigger |
|---|---|---|---|
| 1 | Dispatch intelligence (ML ranking) | Stage 2+ | Acceptance rate < 40% despite good supply |
| 2 | Supply-demand heatmap | Stage 2 | Ops team needs zone visibility |
| 3 | Dispatch priority queues (P0–P3) | Stage 2 | Dispatch backlog > 20 concurrent |
| 4 | Nurse fatigue / notification load tracking | Stage 2 | Nurse churn > 15% / month |
| 5 | Shift conflict validator (AssignmentConflictValidator) | Phase 1 MVP | First overlap complaint |
| 6 | Hospital reliability scoring (HospitalReliabilityScore) | Stage 2 | Hospital cancellation > 5% |
| 7 | Incident management (IncidentReport, SafetyEscalation) | Stage 2 | First safety report |
| 8 | Full ShiftTimelineEvent replay capability | Stage 3 | ML training data needed |
| 9 | Offline resiliency (FCM fallback, sync-on-reconnect) | Phase 1 MVP | WS disconnect > 10% sessions |
| 10 | ZoneOperationalConfig (disable zones without deploy) | Stage 2 | Ops team requests it |

### 1. Dispatch Intelligence (Acceptance Probability Ranking)

Phase 1 ranks candidates by: `distance_km ASC, reliability_score DESC`.

Future ranking adds:
- `acceptance_probability` — predicted likelihood of accepting at this time/day
- `workload_balance` — spread offers across nurses, not just nearest
- `preference_match` — `preferred_shift_types` and `preferred_radius_km`

**Extensibility hooks in Phase 1 code:**
```python
# PresenceState model has these columns — populated by future ML pipeline
historical_preferences: JSON   # stores past shift acceptance patterns
preferred_shift_types:  JSON   # nurse-set or ML-inferred
preferred_radius_km:    Float  # dispatch only if distance < this value
```

### 2. Supply-Demand Heatmap

`SupplyDemandSnapshot` table is created in Phase 1 migration (no dispatch logic wired to it).

Future cron writes snapshots every 5 minutes per zone:
```python
# Future: POST /admin/ops/heatmap — reads SupplyDemandSnapshot
{ "zone": "HYD-BH", "online_nurses": 12, "pending_shifts": 3, "avg_fill_time_sec": 284 }
```

Foundation for surge pricing / incentive system.

### 3. Dispatch Priority Queues (P0–P3)

Phase 1 uses `URGENCY_CONFIG` dict in dispatch engine with per-urgency wave timeouts.

Future Stage 2+ adds ARQ priority queues:
```python
# ARQ queues: dispatch:emergency, dispatch:urgent, dispatch:standard, dispatch:planned
await redis_pool.enqueue_job("dispatch_shift", shift_id, urgency, _queue_name=f"dispatch:{urgency}")
```

### 4. Nurse Fatigue / Notification Load

Future `NurseNotificationLog` table tracks:
- `offers_sent_24h` — suppress dispatch if > 20 offers in 24h
- `consecutive_declines` — back-off if nurse declined 5+ in a row
- `last_notified_at` — minimum 2-minute gap between offers

### 5. Shift Conflict Validator

`AssignmentConflictValidator` is referenced in Phase 1 dispatch engine as a stub:
```python
# FUTURE: replace stub with full overlap + travel-time check
def _validate_no_overlap(db, nurse_id, shift_start, shift_end) -> bool:
    ...  # Phase 1 does basic datetime overlap only
```

### 6. Hospital Reliability Scoring

`HospitalReliabilityScore` table:
- `cancellation_rate` — hospital cancelled > 5% shifts post-assignment
- `payment_reliability` — payment disputes
- `nurse_feedback_avg` — nurse-submitted post-shift rating
- `safety_reports` — incident reports for this hospital

Gate: hospitals with `cancellation_rate > 0.2` get dispatch paused pending ops review.

### 7. Incident Management

`IncidentReport` and `SafetyEscalation` tables:
- Any nurse or hospital can file an incident (unsafe conditions, payment disputes, abuse)
- Auto-escalation: P0 safety incidents page on-call ops within 5 min
- Linked to `fraud_flags` table (§21) for pattern detection

### 8. ShiftTimelineEvent Replay

Phase 1 writes `ShiftTimelineEvent` for every significant action (immutable, append-only).

Future replay capability:
- **Dispatch debugging**: "Why did shift #1042 expire?" → replay all events
- **ML training**: 6 months of events → train acceptance probability model
- **Kafka migration**: at Stage 3, `ShiftTimelineEvent` writer wraps Kafka producer

### 9. Offline Resiliency

Phase 1 has FCM fallback: if WS send fails, FCM push is sent.

Future additions:
- **Reconnect queue**: offers missed during WS downtime delivered on reconnect via `/dispatch/pending-offers` endpoint
- **Local state recovery**: app caches last-known assignment state in localStorage
- **Sync-on-reconnect**: WS `hello` message triggers server to replay any pending events

### 10. ZoneOperationalConfig

`DispatchZone.dispatch_paused` and `DispatchZone.max_radius_km` columns are present in Phase 1.

Future `ZoneOperationalConfig` table:
```sql
-- Ops can change these without a deploy
ALTER dispatch_zones SET dispatch_paused = true WHERE zone_code = 'HYD-BH';
-- Or via API: PATCH /admin/ops/zones/HYD-BH { "dispatch_paused": true }
```

Full config: allowed roles, max wave count, wave timeouts, payment thresholds — all tunable per zone without redeploy.

---

*Generated based on analysis of the MediRoute codebase (FastAPI + SQLAlchemy + PostgreSQL) and platform architecture patterns from Uber, Swiggy, Urban Company, and Ola — adapted for real-time healthcare staffing dispatch at 10M user scale.*

---

## 17. Platform Strategy

Lessons that must drive every architecture decision in MediRoute — sourced from how Uber, Swiggy, Urban Company, and Ola scaled from 0 to 100M users.

### Lesson 1: Density > Geography (Uber/Ola)

Uber's first insight was counterintuitive: **a city with 10 drivers in 1km² is infinitely better than 100 drivers in 100km².** Density creates instant fulfillment. Sparse supply creates a dead product nobody trusts.

**MediRoute consequence**: Do NOT launch "Hyderabad". Launch "Banjara Hills / Jubilee Hills cluster" — Apollo, KIMS, Yashoda all within 5km. Get 20 verified ICU nurses in that 5km radius. Prove the loop works. Then expand to the rest of Hyderabad.

**Architecture requirement**: `DispatchZone` is a first-class entity from day 1. Every geo query filters by zone before radius. AUSFT is tracked per zone, not per city.

---

### Lesson 2: Presence Is the Heart of the Business (Uber)

Uber's supply engineering team spent years on one problem: **"Who is online, where are they, and can they take a job RIGHT NOW?"** They called this presence infrastructure and treated it as core product, not a feature.

**MediRoute consequence**: `PresenceState` is not a supporting table — it IS the supply inventory. Stale presence = false supply = failed dispatch = broken hospital trust.

**Presence freshness rules (enforced at query time)**:
- `last_heartbeat` > 5 min ago → nurse is `offline`, removed from dispatch pool
- `last_location_at` > 2 min ago → location stale, dispatch only for large radius / planned shifts
- `state = online_busy` → exclude from dispatch (already on assignment)

---

### Lesson 3: Trust > Scale (Urban Company)

Urban Company explicitly chose quality over quantity for years. They verified every professional manually, trained them, and only dispatched them after certification. Their smaller, vetted pool outperformed larger unvetted pools on every metric.

**MediRoute consequence**: Healthcare has zero tolerance for trust failures. A nurse with a 95 reliability score at 5km is worth more than 10 unverified nurses at 1km. **Reliability score is core dispatch infrastructure — not a dashboard metric.**

---

### Lesson 4: Manual Operations Are Not Optional (Swiggy)

Swiggy's first 6 months were held together by a "war room" — humans manually assigning delivery partners when the algorithm failed. Automation came second. **Manual operational tooling came first.**

**MediRoute consequence**: Before the dispatch algorithm is stable, the Operations Dashboard (§19) must let admins manually assign nurses to any shift in real time. A failed shift with no fallback = a hospital that never posts again.

---

### Lesson 5: Marketplace Liquidity Is the Only Real Risk (All Platforms)

None of these companies failed due to database scaling. They all nearly failed due to liquidity — not enough supply to fill demand, creating a death spiral where hospitals stop trusting the platform.

**The death spiral**: Hospital posts shift → no nurse accepts → hospital loses trust → hospital stops posting → nurses find no shifts → nurses stop being available → repeat.

**Prevention**: AUSFT is the only metric that matters in Year 1. Every engineering decision must answer: "Does this reduce AUSFT?"

---

### Lesson 6: Fraud Happens as Soon as There's Money (Uber/Ola)

Within months of monetization, GPS spoofing, fake accounts, ghost shifts, and attendance fraud appear. **Anti-fraud must be designed in at Stage 2, not bolted on at Stage 4.**

---

### Business Model Comparison

| Dimension | Uber/Ola | Swiggy | Urban Company | **MediRoute** |
|---|---|---|---|---|
| Supply type | Drivers | Delivery partners | Verified professionals | Verified healthcare workers |
| Demand type | Ride requests | Food orders | Home service bookings | Hospital shift requests |
| Matching speed | <30 sec | <2 min | Scheduled | **<60 sec (target)** |
| Trust mechanism | Rating + background | — | Certification + training | License + reliability score |
| Supply availability | Continuous toggle | Continuous toggle | Scheduled slots | Toggle-based (available now) |
| Key failure mode | No drivers nearby | Late delivery | Poor service | **No nurse = patient risk** |
| Primary KPI | ETA | Delivery time | Service quality | **AUSFT (fill time)** |
| Fraud risk | GPS spoofing | — | Fake professionals | **Credentials + location spoofing** |
| Regulatory risk | Low | Low | Medium | **Very High (healthcare compliance)** |

---

## 18. Event-Oriented Architecture

Even inside a modular monolith (Stage 1–2), every significant state change should be modeled as a domain event. This is the architectural decision that makes the Stage 3 Kafka migration a configuration swap, not a rewrite.

### Canonical Event Types

```python
# app/events/types.py — single source of truth for all events in the system

# Shift lifecycle
SHIFT_CREATED          = "shift.created"
SHIFT_DISPATCHING      = "shift.dispatching"
SHIFT_FILLED           = "shift.filled"
SHIFT_EXPIRED          = "shift.expired"
SHIFT_CANCELLED        = "shift.cancelled"

# Offer lifecycle
OFFER_SENT             = "offer.sent"
OFFER_ACCEPTED         = "offer.accepted"
OFFER_DECLINED         = "offer.declined"
OFFER_TIMED_OUT        = "offer.timed_out"
OFFER_CANCELLED        = "offer.cancelled"

# Nurse presence
NURSE_ONLINE           = "nurse.online"
NURSE_OFFLINE          = "nurse.offline"
NURSE_LOCATION_UPDATED = "nurse.location_updated"
NURSE_BUSY             = "nurse.busy"          # accepted an assignment
NURSE_AVAILABLE        = "nurse.available"     # released from assignment

# Assignment lifecycle
ASSIGNMENT_CREATED     = "assignment.created"
ASSIGNMENT_CHECKIN     = "assignment.checked_in"
ASSIGNMENT_CHECKOUT    = "assignment.checked_out"
ASSIGNMENT_COMPLETED   = "assignment.completed"
ASSIGNMENT_NO_SHOW     = "assignment.no_show"
ASSIGNMENT_CANCELLED   = "assignment.cancelled"

# Trust + operations
RELIABILITY_UPDATED    = "reliability.score_updated"
FRAUD_FLAGGED          = "fraud.flagged"
MANUAL_OVERRIDE        = "dispatch.manual_override"
```

### Internal Event Bus (Stage 1–2, in-process)

```python
# app/events/bus.py
# Stage 1-2: in-process function calls
# Stage 3: replace publish() body with Kafka producer — zero changes to callers

from dataclasses import dataclass, asdict
from datetime import datetime
from collections import defaultdict
from typing import Callable, List
import asyncio

@dataclass
class DomainEvent:
    event_type: str
    city_id: str
    payload: dict
    occurred_at: datetime = None

    def __post_init__(self):
        if not self.occurred_at:
            self.occurred_at = datetime.utcnow()

_handlers: dict[str, List[Callable]] = defaultdict(list)

def subscribe(event_type: str, handler: Callable):
    _handlers[event_type].append(handler)

async def publish(event: DomainEvent, db=None):
    # 1. Always write to ShiftTimelineEvent (immutable audit log)
    if db and event.payload.get("shift_request_id"):
        await _write_timeline_event(db, event)

    # 2. Fan out to all registered handlers
    handlers = _handlers.get(event.event_type, [])
    await asyncio.gather(
        *[handler(event) for handler in handlers],
        return_exceptions=True  # one failed handler doesn't break others
    )
```

### Event Subscriber Map

```
SHIFT_CREATED
  → dispatch_service.start_dispatch()
  → analytics.track_shift_created()

OFFER_SENT
  → notification_service.push_or_ws()
  → analytics.track_offer_sent()

OFFER_ACCEPTED
  → dispatch_service.finalize_assignment()
  → notification_service.notify_hospital_filled()
  → reliability_service.record_acceptance()
  → analytics.record_ausft()            ← AUSFT clock stops here

OFFER_DECLINED | OFFER_TIMED_OUT
  → reliability_service.record_non_acceptance()
  → dispatch_service.check_wave_complete()

ASSIGNMENT_NO_SHOW
  → dispatch_service.re_dispatch()
  → reliability_service.record_no_show()
  → notification_service.alert_hospital_no_show()
  → admin_service.flag_for_ops_review()

NURSE_ONLINE
  → presence_service.mark_available()
  → geo_service.add_to_geo_index()

NURSE_OFFLINE
  → presence_service.mark_offline()
  → geo_service.remove_from_geo_index()
  → dispatch_service.cancel_pending_offers()   ← nurse went offline mid-dispatch

FRAUD_FLAGGED
  → admin_service.create_fraud_flag()
  → notification_service.alert_ops_team()
```

### Stage 3: Kafka Drop-In

```python
# Replace bus.py publish() body — callers unchanged
from confluent_kafka import Producer
import json

_producer = Producer({"bootstrap.servers": os.getenv("KAFKA_BROKERS")})

async def publish(event: DomainEvent, db=None):
    if db and event.payload.get("shift_request_id"):
        await _write_timeline_event(db, event)  # audit log always written

    # Route to city-namespaced Kafka topic
    domain = event.event_type.split(".")[0]   # "shift", "offer", "nurse", etc.
    topic = f"mediroute.{event.city_id}.{domain}"

    _producer.produce(
        topic,
        key=str(event.payload.get("shift_request_id") or event.payload.get("user_id")),
        value=json.dumps(asdict(event), default=str)
    )
    _producer.flush()
# dispatch_service, reliability_service, notification_service — ZERO changes needed
```

---

## 19. Operations Dashboard

**This is mandatory, not optional.** Swiggy ran manual dispatch operations for months before their algorithm was reliable. MediRoute must have the same capability from the first real hospital goes live.

### Live Dispatch Monitor

```
┌────────────────────────────────────────────────────────────────────┐
│  ACTIVE SHIFTS — Banjara Hills Zone                  [Refresh: 5s] │
├────────────────────────────────────────────────────────────────────┤
│  #1042  ICU Nurse    Apollo BH    ⏱ Wave 2 · 38s left  [OVERRIDE]  │
│  #1039  OT Nurse     KIMS         ✅ FILLED 3m 12s ago              │
│  #1038  Front Off    Yashoda      ❌ EXPIRED — no nurses            │
├────────────────────────────────────────────────────────────────────┤
│  Online nurses: 47   Dispatching: 2   AUSFT today: 5.3 min        │
└────────────────────────────────────────────────────────────────────┘
```

### Manual Dispatch Override

```python
@router.post("/admin/ops/manual-assign")
async def manual_assign(
    shift_id: int,
    nurse_user_id: int,
    reason: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """
    Bypass dispatch engine. Admin directly assigns nurse to shift.
    - Records ShiftTimelineEvent(MANUAL_OVERRIDE)
    - Notifies nurse via push + hospital via WebSocket
    - Updates shift status to 'filled'
    """
    await finalize_assignment(db, shift_id, nurse_user_id, source="manual_override")
    await publish(DomainEvent(
        event_type=MANUAL_OVERRIDE,
        city_id=shift.city_id,
        payload={
            "shift_id": shift_id,
            "nurse_id": nurse_user_id,
            "admin_id": admin.id,
            "reason": reason
        }
    ), db=db)
    return {"success": True, "message": f"Nurse {nurse_user_id} manually assigned to shift {shift_id}"}
```

### Operations API Endpoints

```
GET  /admin/ops/live-shifts              — active shifts with dispatch state + wave timer
GET  /admin/ops/nurse-presence           — all online nurses with geo (map view)
POST /admin/ops/manual-assign            — force-assign nurse to any shift
POST /admin/ops/re-dispatch/{shift_id}   — restart dispatch for expired/failed shift
POST /admin/ops/expand-radius/{shift_id} — force re-dispatch with 2x radius
GET  /admin/ops/failed-shifts?hours=24   — expired/unfilled shifts for review
GET  /admin/ops/reliability-alerts       — nurses below score threshold
POST /admin/ops/suspend-nurse/{user_id}  — block nurse from all dispatch
GET  /admin/ops/ausft                    — real-time AUSFT for today/zone/city
```

### Operational SLAs

| Metric | Target | Auto-Alert Threshold |
|---|---|---|
| Urgent shift fill time | < 5 min | > 8 min → Slack alert to ops |
| Standard shift fill time | < 15 min | > 20 min → ops review |
| Online nurses in active zone | > 0 always | 0 nurses → immediate alert |
| Dispatch engine alive | 99.9% | Any crash → PagerDuty |
| Offer delivery (WS or FCM) | < 8 sec | > 15 sec → investigate |

---

## 20. Observability & KPIs

### The One Metric That Matters

$$AUSFT = \frac{\sum_{i=1}^{n}(assignment\_created\_at_i - shift\_created\_at_i)}{n}$$

Every engineering decision must answer: **"Does this reduce AUSFT?"**

### The Dispatch Funnel

Track conversion at every stage — this is your product health dashboard:

```
ShiftCreated                         (100% baseline)
    │
    ▼
DispatchStarted                      target: 100%  — if <100%, recruiter verification gate failing
    │
    ▼
Wave1_Offered (nurses notified)      — tracks supply density per zone
    │
    ▼
Wave1_Delivered (FCM open/WS recv)   — tracks notification delivery rate
    │
    ▼
Wave1_Accepted                       target: >60%  — if <60%, pay rate or trust issue
    │
    ▼
AssignmentCreated (filled)           target: >85% overall fill rate
    │
    ▼
CheckedIn (nurse arrived)            target: >95%  — if <95%, no-show problem
    │
    ▼
Completed                            target: >98%
```

### Key Funnel Metrics

| Metric | Formula | Target | Red Flag |
|---|---|---|---|
| Dispatch start rate | dispatches / shifts_created | 100% | < 95% |
| First-wave fill rate | filled_wave1 / dispatched | > 60% | < 40% |
| Overall fill rate | filled / dispatched | > 85% | < 70% |
| Acceptance rate | accepted / offers_sent | > 40% | < 25% |
| Attendance rate | checked_in / assignments | > 95% | < 90% |
| No-show rate | no_shows / assignments | < 3% | > 5% |

### Sentry Dispatch Integration

```python
async def run_dispatch(shift_request_id, db, urgency="standard"):
    start = datetime.utcnow()
    with sentry_sdk.start_transaction(op="dispatch", name=f"dispatch.{urgency}"):
        try:
            result = await _inner_dispatch(shift_request_id, db, urgency)
            sentry_sdk.set_tag("dispatch.outcome", result)  # filled | expired | error
        except Exception as e:
            sentry_sdk.capture_exception(e)
            sentry_sdk.set_tag("dispatch.outcome", "error")
            raise
        finally:
            duration_sec = (datetime.utcnow() - start).total_seconds()
            sentry_sdk.set_measurement("dispatch.duration_sec", duration_sec)
            if duration_sec > 480:  # > 8 min = AUSFT breach
                sentry_sdk.set_tag("dispatch.ausft_breach", "true")
```

### Dashboard Stack by Stage

| Stage | Tool | Purpose |
|---|---|---|
| Stage 1–2 | Sentry | Error tracking, dispatch duration |
| Stage 1–2 | Supabase built-in | SQL dashboards, nurse count |
| Stage 2+ | PostHog | Product analytics, funnel analysis |
| Stage 3+ | Grafana + ClickHouse | AUSFT trends, city comparisons, reliability heatmaps |

---

## 21. Anti-Fraud Systems

Fraud appears as soon as money flows. Healthcare adds unique risks. Build these defenses in at Stage 2 — not Stage 4.

### 1. Location Spoofing (GPS Faking)

**Risk**: Nurse uses a GPS mock app to appear near the hospital, accepts the shift, then doesn't show.

```python
def validate_location_plausibility(
    prev_lat, prev_lng, prev_time,
    curr_lat, curr_lng, curr_time
) -> bool:
    """Nurse cannot move faster than a car. If they did, it's spoofed."""
    distance_km = haversine(prev_lat, prev_lng, curr_lat, curr_lng)
    elapsed_hours = max((curr_time - prev_time).total_seconds() / 3600, 0.001)
    speed_kmh = distance_km / elapsed_hours

    if speed_kmh > 120:  # faster than city driving = impossible
        flag_fraud(user_id, "location_anomaly", {
            "speed_kmh": speed_kmh,
            "distance_km": distance_km
        })
        return False
    return True
```

**Additional defense**: GPS check-in required within 200m of hospital at shift start — dispatched from exact coordinates on file.

### 2. Attendance Fraud (Fake Check-In)

```python
def validate_checkin_location(
    nurse_lat, nurse_lng,
    hospital_lat, hospital_lng
):
    distance_m = haversine(nurse_lat, nurse_lng, hospital_lat, hospital_lng) * 1000
    if distance_m > 200:
        raise HTTPException(400,
            f"You are {distance_m:.0f}m from the hospital. "
            f"Check-in requires being within 200m of the facility.")
```

**Additional layers**: Hospital-side QR code per shift (generated on assignment, expires 15 min after shift start); hospital HR confirmation for critical roles.

### 3. Ghost Shifts (Recruiter Fraud)

```python
def can_post_shift(recruiter: User, db) -> tuple[bool, str]:
    if not recruiter.is_verified:
        return False, "Submit hospital documents to post shifts."

    # New recruiters: rate-limited for 30 days
    days_verified = (datetime.utcnow() - recruiter.verified_at).days
    if days_verified < 30:
        shifts_today = count_shifts_today(db, recruiter.id)
        if shifts_today >= 3:
            return False, "New accounts limited to 3 shifts/day for first 30 days."

    return True, ""
```

### 4. Multi-Account Abuse (Score Gaming)

Phone number is already unique. Add device fingerprinting:

```python
device_fingerprint = hashlib.sha256(
    f"{user_agent}:{ip_address}:{screen_resolution}:{timezone}".encode()
).hexdigest()

conflict = db.query(DeviceFingerprint).filter(
    DeviceFingerprint.fingerprint == device_fingerprint,
    DeviceFingerprint.user_id != current_user.id
).first()

if conflict:
    publish(DomainEvent(FRAUD_FLAGGED, city_id=..., payload={
        "type": "multi_account",
        "user_a": current_user.id,
        "user_b": conflict.user_id,
        "device": device_fingerprint
    }))
```

### 5. Nurse-Recruiter Collusion

```python
# Flag if >70% of a nurse's assignments come from one recruiter (90-day window)
def check_collusion_risk(db, nurse_id):
    total = count_assignments(db, nurse_id, days=90)
    if total < 5:
        return  # not enough data

    by_recruiter = count_assignments_by_recruiter(db, nurse_id, days=90)
    for recruiter_id, count in by_recruiter.items():
        if count / total > 0.7:
            publish(DomainEvent(FRAUD_FLAGGED, payload={
                "type": "collusion_risk",
                "nurse_id": nurse_id,
                "recruiter_id": recruiter_id,
                "concentration": count / total
            }))
```

### Anti-Fraud Schema

```sql
CREATE TABLE fraud_flags (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    flag_type VARCHAR NOT NULL,  -- location_anomaly | multi_account | ghost_shift | collusion
    severity VARCHAR NOT NULL,   -- low | medium | high | critical
    details JSONB NOT NULL,
    reviewed_by INTEGER REFERENCES users(id),
    resolution VARCHAR,          -- cleared | warned | suspended | banned
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);
CREATE INDEX idx_fraud_user ON fraud_flags(user_id, created_at);
CREATE INDEX idx_fraud_unresolved ON fraud_flags(severity) WHERE resolution IS NULL;

CREATE TABLE device_fingerprints (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, fingerprint)
);
CREATE INDEX idx_device_fp ON device_fingerprints(fingerprint);
```

---

## 22. City Sharding Strategy

Healthcare staffing is fundamentally local. A nurse in Hyderabad has zero relevance to a shift in Delhi. City sharding must be designed in from Stage 1 — not discovered at Stage 4.

### city_id in Every Dispatch Table (Do This Now)

Add `city_id VARCHAR(10) NOT NULL DEFAULT 'HYD'` to:
- `shift_requests`
- `nurse_availability`
- `presence_state`
- `dispatch_sessions`
- `dispatch_offers`
- `live_assignments`
- `shift_timeline_events`
- `dispatch_zones`

**City code convention**: `HYD`, `BLR`, `MUM`, `DEL`, `CHE`, `KOL`, `PUN`

**Sub-zone convention**: `HYD-BH` (Banjara Hills), `BLR-WF` (Whitefield), `MUM-BKC` (BKC) — for hyperlocal density tracking.

### Why Sub-Zones Before Cities

**Do NOT launch "Hyderabad".** Launch "Banjara Hills / Jubilee Hills cluster" (Apollo, KIMS, Yashoda all within 5km). The Density > Geography principle means 20 nurses in 5km beats 200 nurses spread across 50km.

### Stage-by-Stage City Implementation

**Stage 1 — City-aware queries:**
```sql
-- All dispatch queries filter city_id first
SELECT * FROM nurse_availability
WHERE city_id = 'HYD'
  AND is_available = TRUE
  AND last_seen > NOW() - INTERVAL '5 minutes'
  AND ST_DWithin(location, $hospital_point, $radius_m);
```

**Stage 2 — Redis namespacing by city:**
```python
# Separate geo index per city — no cross-city noise
await redis.geoadd(f"nurses_geo:{city_id}", lng, lat, f"nurse:{user_id}")
await redis.set(f"nurse:{user_id}:online:{city_id}", "1", ex=300)

nearby = await redis.georadius(
    f"nurses_geo:{city_id}", hospital_lng, hospital_lat,
    radius_km, "km", count=20, sort="ASC"
)
```

**Stage 3 — PostgreSQL table partitioning by city:**
```sql
CREATE TABLE shift_requests (
    id BIGSERIAL,
    city_id VARCHAR(10) NOT NULL,
    -- ... other columns
) PARTITION BY LIST (city_id);

CREATE TABLE shift_requests_HYD PARTITION OF shift_requests FOR VALUES IN ('HYD');
CREATE TABLE shift_requests_BLR PARTITION OF shift_requests FOR VALUES IN ('BLR');
CREATE TABLE shift_requests_MUM PARTITION OF shift_requests FOR VALUES IN ('MUM');
```

**Stage 4 — City microservices (one cluster per city):**
```
National API Gateway (routes by city_id header)
  ├── HYD cluster — dispatch + Redis + PG shard + Kafka
  ├── BLR cluster — dispatch + Redis + PG shard + Kafka
  └── MUM cluster — dispatch + Redis + PG shard + Kafka

Shared national services: Auth, Verification, Payments, Admin, Analytics
```

### India City Launch Priority

| Priority | City | Zone to Launch First | Hospital Targets |
|---|---|---|---|
| **P0** | Hyderabad | Banjara Hills / Jubilee Hills | Apollo, KIMS, Yashoda |
| **P1** | Bangalore | Whitefield / Indiranagar | Manipal, Fortis, Sakra |
| **P1** | Chennai | Anna Nagar / Adyar | Apollo, MIOT, Fortis |
| **P2** | Mumbai | BKC / Andheri | Kokilaben, Lilavati, Hiranandani |
| **P2** | Delhi NCR | Gurgaon cluster | Medanta, Fortis, Max |
| **P3** | Tier-2 | Pune, Ahmedabad, Kolkata | City's largest hospital first |

### City Launch Checklist (Hard Gates)

Before activating dispatch in any new city:
- [ ] DispatchZone record created (center, radius, boundary polygon)
- [ ] Minimum 20 verified nurses in the zone across 3+ roles
- [ ] Minimum 3 hospitals onboarded and verified
- [ ] Operations manager assigned (manual override coverage)
- [ ] Redis namespace initialized: `nurses_geo:{CITY_ID}`
- [ ] Admin alert configured: "0 online nurses in {CITY_ID}" → Slack

---

## 23. 0→10M User Scaling Roadmap

### Scale Tier Definitions

| Tier | Active Users | Concurrent Nurses Online | Geo Queries/sec | Dispatch Sessions | Monthly Infra Cost |
|---|---|---|---|---|---|
| **Tier 0** | 0–1K | <200 | <10 | <20 | ~$50 |
| **Tier 1** | 1K–50K | <5K | <100 | <500 | ~$150 |
| **Tier 2** | 50K–500K | <50K | <1K | <5K | ~$600 |
| **Tier 3** | 500K–5M | <500K | <10K | <50K | ~$5K |
| **Tier 4** | 5M–10M | <2M | <100K | <200K | ~$30K+ |

---

### Tier 0: Foundation (0 → 1,000 users)

**Stack**: FastAPI monolith on Render Standard (1 instance) + Supabase PostgreSQL with PostGIS + FCM + Sentry

**Dispatch signal**: `asyncio.Event` (in-process dict — single instance only)

**What to optimize**: AUSFT measurement. Get it below 20 min. Don't touch infra yet.

**Trigger**: WebSocket connections >500, OR AUSFT >20 min despite good supply, OR you deploy a second Render instance.

---

### Tier 1: Real-Time Marketplace (1K → 50K users)

**New problem**: `asyncio.Event` dict breaks across 2 Render instances. WS pub/sub needed.

**Additions**:
- Redis (Upstash ~$20/mo): WS pub/sub, dispatch signaling, OTP caching, presence TTL
- ARQ (async Redis Queue): dispatch jobs survive server restarts
- Second Render instance (sticky WS sessions)
- Presence moves from DB polling to Redis TTL (`SET nurse:{id}:online "1" EX 300`)

**Key code change — dispatch signaling via Redis:**
```python
# Signal acceptance (replaces asyncio.Event.set())
await redis.set(f"dispatch:accepted:{session_id}", "1", ex=wave_timeout)

# ARQ worker polls Redis (replaces asyncio.wait_for(event.wait()))
for _ in range(wave_timeout):
    if await redis.exists(f"dispatch:accepted:{session_id}"):
        return  # filled
    await asyncio.sleep(1)
```

**Trigger**: Redis memory >500MB, OR concurrent nurses >3K, OR AUSFT not improving despite supply.

---

### Tier 2: City-Scale Dispatch (50K → 500K users)

**New problem**: PostGIS proximity queries take >30ms p95. Single Redis instance near memory limits. Need Kafka for analytics pipeline.

**Additions**:
- Redis GEO replaces PostGIS for hot dispatch path (PostGIS retained for analytics writes)
- Kafka (Confluent Cloud ~$100/mo): `ShiftTimelineEvent` → Kafka → ClickHouse
- City namespace isolation: `nurses_geo:HYD`, `nurses_geo:BLR`
- PostgreSQL table partitioning by `city_id`
- ClickHouse (~$50/mo): AUSFT dashboards, funnel analytics
- Dedicated WebSocket tier (sticky sessions, separate from API tier)

**Redis GEO dispatch (sub-millisecond vs PostGIS 30ms):**
```python
# Find nurses within 5km (Tier 2 hot path)
candidates = await redis.georadius(
    f"nurses_geo:{city_id}", hospital_lng, hospital_lat,
    5, "km", withcoord=True, withdist=True, count=20, sort="ASC"
)
# Enrich with role/score from Redis hashes (single pipeline call)
pipeline = redis.pipeline()
for nurse_id, coords, dist in candidates:
    pipeline.hgetall(f"nurse:{nurse_id}:meta")
roles_and_scores = await pipeline.execute()
# Filter by role in Python — no SQL query at all
```

**Trigger**: Redis Cluster needed (>10GB), OR any city has >50K concurrent nurses, OR AUSFT increasing despite good supply density.

---

### Tier 3: Event-Streaming Platform (500K → 5M users)

**New problem**: Single-region dispatch. Cross-city Kafka fan-in creates latency. ML needed for dispatch ranking (distance + score not enough at this density).

**Additions**:
- Move from Render to AWS ECS or Cloud Run (not Kubernetes)
- Per-city Kafka topics: `mediroute.HYD.shift`, `mediroute.BLR.shift`
- ML dispatch ranking: train on historical AUSFT data → optimize nurse selection beyond distance + score
- City database shards (separate RDS per major city)
- National read replica for cross-city admin queries
- PagerDuty for operational alerting

**ML dispatch ranking (replaces distance+score sort):**
```python
# Features per candidate nurse:
features = {
    "distance_km": 3.2,
    "reliability_score": 87.5,
    "response_time_avg_sec": 28.0,
    "current_hour_acceptance_rate": 0.72,  # how likely they accept at this time of day
    "consecutive_declines": 0,
    "zone_familiarity": 1.0,  # has worked in this zone before
    "shift_duration_fit": 0.9,  # prefers 8hr shifts, this is 8hr
}
# Model output: probability of acceptance within 30s
# Sort candidates by predicted_acceptance_probability DESC (not just distance)
```

**Trigger**: Any city cluster approaching independent scale limits, OR need regulatory compliance per state, OR fundraising requires city P&L separation.

---

### Tier 4: National Healthcare Infrastructure (5M → 10M users)

**Architecture**: Independent city clusters connected by national shared services.

```
Cloudflare / AWS Global Accelerator
  └── Routes to nearest region by city_id

Per-city cluster (deployed independently):
  ├── ECS auto-scaling dispatch workers
  ├── WebSocket tier (100K+ connections, NLB with sticky sessions)
  ├── Redis Cluster (GEO + presence + pub/sub)
  ├── City-local PostgreSQL (Multi-AZ RDS)
  └── City-local Kafka cluster

National shared services:
  ├── Auth Service (JWT, OTP via MSG91)
  ├── Document Verification (ML + manual review)
  ├── Payments (Razorpay/Stripe integration)
  ├── Admin Operations Dashboard
  └── ClickHouse National Analytics (aggregates all city data)
```

**At 10M users, you have a dedicated infrastructure team.** The design decisions made at Tier 0 (city_id in every table, event-oriented modules, Redis-namespaced presence, Kafka-compatible event types) make this tier a growth exercise — not a rewrite.

### The Architectural Thread

Every tier is additive — nothing at Tier 0 blocks Tier 4:

| Decision | Tier 0 | Tier 4 |
|---|---|---|
| `city_id` in dispatch tables | Default `'HYD'` | Routes to city shard |
| Event types (§18) | In-process function calls | Kafka messages (same types) |
| Redis key naming | `nurses_geo:HYD` | Per-city Redis cluster |
| `ShiftTimelineEvent` | Written to PostgreSQL | Written + published to Kafka |
| Dispatch signal | `asyncio.Event` | Redis key / Kafka consumer |
| WebSocket delivery | In-memory `ConnectionManager` | Redis pub/sub → city WS cluster |

---

## 25. Post-Traction Roadmap

> ⛔ **DO NOT IMPLEMENT ANY OF THESE NOW.**
>
> These systems are only warranted **AFTER initial market validation** — defined as **100–1,000 active users** with real dispatch behaviour observed, real operational bottlenecks identified, and real user data available.
>
> **Implementation gate**: Every item below is blocked until ALL of the following are true:
> - Marketplace liquidity exists (nurses + hospitals posting real shifts)
> - AUSFT is measured and < 20 min in at least one zone
> - Zero critical dispatch failures in 30 consecutive days
> - Operations Dashboard is live and manual override is working
>
> The current architecture (Stage 1 monolith) is intentionally sufficient. Premature infrastructure is the most common way early-stage products fail. Build the market first.

---

### 25.1 Advanced Supply-Demand Heatmaps

**Trigger**: Ops team needs zone-level visibility to make staffing decisions.

| Sub-feature | Description |
|---|---|
| Zone stress visualization | Color-coded map of fill-rate per zone per hour |
| Fill-time heatmaps | Average AUSFT overlaid on geographic zones |
| Staffing shortage visualization | Zones where supply < demand over last N hours |
| Operational density tracking | Online nurse counts per zone, updated every 5 min |

**Foundation already in place**: `DispatchZone` table, `ShiftTimelineEvent` audit log, `SupplyDemandSnapshot` table (created in migration, no logic wired yet).

**Build when**: Ops team is managing > 3 zones and cannot reason about supply distribution manually.

---

### 25.2 Dynamic Incentive / Surge System

**Trigger**: Fill rate drops below 70% in any zone during peak hours, indicating a supply-demand price gap.

| Sub-feature | Description |
|---|---|
| Emergency staffing bonus | Auto-apply ₹X bonus to shifts that expire wave 1 unfilled |
| Night-shift multipliers | Configurable per-zone per-hour rate multipliers |
| Shortage incentives | Push notifications to offline nurses when zone is critically understaffed |
| Zone surge support | `DispatchZone.surge_multiplier` column (add via migration when needed) |

**Foundation already in place**: `DispatchZone` model supports `max_radius_km` overrides; `AcceptanceWindow` has per-urgency config; `ShiftTimelineEvent` captures wave exhaustion events for surge trigger detection.

**Build when**: Acceptance rate < 40% in a zone despite adequate nurse supply — indicating a pay-rate problem, not a supply problem.

---

### 25.3 Acceptance Probability Modeling

**Trigger**: Acceptance rate < 40% despite good supply, suggesting dispatch is hitting the wrong nurses.

| Sub-feature | Description |
|---|---|
| Nurse preference learning | Track which shift types / hospitals / hours each nurse historically accepts |
| Acceptance likelihood scoring | P(accept) model per nurse × shift combination |
| Dispatch optimization | Re-rank candidates by `P(accept) × reliability_score` instead of distance-only |
| Preference history modeling | Rolling 30-day window of nurse decisions |

**Foundation already in place**:
```python
# PresenceState model already has these columns — ready for ML pipeline to populate:
historical_preferences: JSON   # past shift acceptance patterns
preferred_shift_types:  JSON   # nurse-set or ML-inferred
preferred_radius_km:    Float  # dispatch only within this radius
```

**Build when**: 6+ months of offer/accept/decline data exists. Minimum viable dataset: ~10,000 offer decisions.

---

### 25.4 Advanced Notification Fatigue System

**Trigger**: Nurse churn > 15%/month, or nurses self-reporting feeling "overwhelmed by offers."

| Sub-feature | Description |
|---|---|
| Adaptive dispatch throttling | Suppress dispatch to nurses who declined > 5 offers in 24h |
| Notification saturation scoring | `offers_sent_24h` counter per nurse, reset daily |
| Burnout prevention logic | Mandatory quiet window after N consecutive non-acceptances |
| Engagement-aware dispatching | Deprioritise nurses who haven't accepted in 7+ days |

**Foundation already in place**: `ReliabilityScore.timed_out` and `declined` counters track disengagement signals. `_offer_fatigue` dict in `engine.py` already implements a basic version of this for in-session deduplication.

**Build when**: Nurse retention data shows a fatigue pattern, not before.

---

### 25.5 Hospital Reliability Scoring

**Trigger**: Hospital cancellation rate > 5% post-assignment, or first nurse complaint about non-payment.

| Sub-feature | Description |
|---|---|
| Cancellation rates | Track `LiveAssignment` cancellations initiated by hospital side |
| Payment reliability | Dispute flag on completed assignments |
| Nurse feedback aggregation | Post-shift 1–5 star rating from nurse on hospital |
| Trust-weighted hospital ranking | Lower-reliability hospitals get deprioritised in nurse offer feed |

**Foundation already in place**: `ShiftTimelineEvent` captures all cancellation events with actor. `ASSIGNMENT_CANCELLED` event type defined in `dispatch/events.py`.

**Table to add when ready**:
```sql
CREATE TABLE hospital_reliability_scores (
    posted_by_user_id INTEGER PRIMARY KEY REFERENCES users(id),
    total_shifts INTEGER NOT NULL DEFAULT 0,
    cancellation_count INTEGER NOT NULL DEFAULT 0,
    cancellation_rate FLOAT NOT NULL DEFAULT 0.0,
    payment_disputes INTEGER NOT NULL DEFAULT 0,
    nurse_feedback_avg FLOAT,
    trust_score FLOAT NOT NULL DEFAULT 100.0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Gate**: Hospitals with `trust_score < 60` get dispatch paused pending ops review.

---

### 25.6 Incident Management System

**Trigger**: First safety report, payment dispute, or conduct complaint from a nurse or hospital.

| Sub-feature | Description |
|---|---|
| Safety escalations | P0 incidents auto-page on-call ops within 5 min |
| Abuse reporting | Nurse or hospital can flag any interaction |
| Operational incident tracking | Linked to `ShiftTimelineEvent` for full context |
| Dispute management workflows | Resolution state machine: open → investigating → resolved / escalated |

**Foundation already in place**: `FRAUD_FLAGGED` event type defined. `ShiftTimelineEvent` audit trail provides full incident context without additional logging.

**Tables to add when ready**:
```sql
CREATE TABLE incident_reports (
    id SERIAL PRIMARY KEY,
    shift_request_id INTEGER REFERENCES shift_requests(id),
    reporter_user_id INTEGER NOT NULL REFERENCES users(id),
    subject_user_id INTEGER REFERENCES users(id),
    severity VARCHAR NOT NULL,  -- p0_safety | p1_payment | p2_conduct | p3_other
    description TEXT NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'open',  -- open | investigating | resolved | escalated
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);
```

---

### 25.7 Redis Presence Layer

**Trigger**: Second Render instance added (WebSocket state must cross instance boundary), OR active nurses > 1,000 concurrent.

| Sub-feature | Description |
|---|---|
| Distributed presence tracking | `SET nurse:{id}:online:{city} "1" EX 300` — survives multi-instance |
| Redis GEO | `GEOADD nurses_geo:{city} lng lat nurse:{id}` — replaces PostGIS hot path |
| WebSocket fanout support | `PUBLISH user:{id} {event}` — cross-instance delivery |
| Multi-instance real-time coordination | `asyncio.Event` → Redis key poll / Kafka consumer |

**Foundation already in place**: All Redis keys are pre-namespaced by `city_id` in the architecture design. `ws_manager.py` `ConnectionManager` is a single class — Redis pub/sub subscriber is a drop-in addition. `dispatch_events` dict is the only in-process coupling to replace.

**Migration path** (zero downtime):
```python
# Step 1: dual-write (asyncio.Event AND Redis) — backward compatible
# Step 2: remove asyncio.Event reads, keep writes for fallback
# Step 3: remove asyncio.Event entirely
```

**Do not add Redis before this trigger.** PostgreSQL + asyncio handles Stage 1 comfortably.

---

### 25.8 Kafka / Event Streaming

**Trigger**: Stage 3 — > 50K nurses, ClickHouse analytics needed, OR cross-city event fan-out required.

| Sub-feature | Description |
|---|---|
| Event bus architecture | Replace in-process `bus.py` publish with Kafka producer — zero caller changes |
| Streaming analytics | All `ShiftTimelineEvent` writes become Kafka messages consumed by ClickHouse |
| Dispatch event ingestion | `shift.*`, `offer.*`, `nurse.*` topics per city |
| Scalable replay infrastructure | Kafka retention enables ML training on historical dispatch decisions |

**Foundation already in place**: `dispatch/events.py` canonical event type constants are already Kafka-topic-compatible. `bus.py` `publish()` function is the only swap point — documented in §18.

**Drop-in replacement** (from §18):
```python
# Replace bus.py publish() body only — all callers unchanged
topic = f"mediroute.{event.city_id}.{event.event_type.split('.')[0]}"
_producer.produce(topic, value=json.dumps(asdict(event), default=str))
```

---

### 25.9 ML-Based Matching Engine

**Trigger**: Acceptance rate < 40% AND preference modeling (§25.3) data exists (min 10K decisions).

| Sub-feature | Description |
|---|---|
| Intelligent dispatch scoring | Composite score: `P(accept) × reliability × 1/distance` |
| Predictive acceptance models | Logistic regression or gradient boost on nurse × shift features |
| Shift recommendation engine | Proactive "shifts you might want" push to offline nurses |
| Marketplace optimization | Optimise for both fill-rate and nurse satisfaction simultaneously |

**Build when**: You have enough data. Building ML before data exists wastes 3 months. Sort by `distance ASC, reliability DESC` until then — it works.

---

### 25.10 City Sharding Architecture

**Trigger**: Any city approaching 100K concurrent nurses, OR AUSFT degrading due to cross-city query interference. Full design in §22.

| Sub-feature | Description |
|---|---|
| City-localized dispatch clusters | Independent FastAPI worker pool per city |
| Regional scaling | Per-city Redis GEO + Kafka topics |
| Geo partitioning | PostgreSQL table partitioning by `city_id` |
| Multi-city isolation | Failures in City A do not affect City B dispatch |

**Foundation already in place**: `city_id` column present on all dispatch tables from Stage 1. All Redis keys namespaced `:{city_id}`. Full migration path documented in §22.

---

### 25.11 Predictive Demand Forecasting

**Trigger**: Operations team needs to proactively recruit nurses before a shortage, not react after one.

| Sub-feature | Description |
|---|---|
| Staffing demand prediction | Forecast shifts expected per zone per day/week |
| Seasonal forecasting | Identify recurring patterns (flu season, holiday surges) |
| ICU shortage prediction | Early warning: ICU fill rate degrading → alert ops |
| Staffing surge anticipation | Pre-recruit and pre-notify nurses before predicted surge |

**Data requirement**: Minimum 90 days of shift history per zone before a useful forecast is possible.

---

### 25.12 Advanced Fraud Detection

**Trigger**: First confirmed GPS spoofing incident, OR > 5% no-show rate suggesting systematic abuse.

| Sub-feature | Description |
|---|---|
| GPS spoof detection | Speed plausibility check (§21) — already designed, wire when needed |
| Device fingerprinting | Flag multiple accounts on same device |
| Abnormal behaviour detection | Nurse accepts shift + goes offline immediately — flag for review |
| Multi-account fraud prevention | Phone + device fingerprint uniqueness enforcement |

**Foundation already in place**: `validate_location_plausibility()` function designed in §21. `FRAUD_FLAGGED` event type defined. `ShiftTimelineEvent` provides full audit trail for anomaly detection.

---

### 25.13 Advanced Operations Tooling

**Trigger**: Ops team managing > 5 simultaneous incidents or > 50 concurrent shifts.

| Sub-feature | Description |
|---|---|
| Dispatch replay | Step through `ShiftTimelineEvent` sequence for any shift to diagnose failures |
| Operational heatmaps | Live zone stress overlaid on map (requires §25.1) |
| Real-time workforce map | Pin-on-map of all online nurses + active shifts |
| Staffing simulation tools | "What if we had 5 more ICU nurses in BH zone?" scenario modelling |

**Foundation already in place**: `ShiftTimelineEvent` immutable audit log is the replay data source. `GET /admin/ops/timeline/{shift_id}` endpoint already built and live.

---

### 25.14 Marketplace Economics Engine

**Trigger**: Marketplace is liquid (> 85% fill rate) and optimisation becomes the constraint, not basic function.

| Sub-feature | Description |
|---|---|
| Liquidity balancing | Detect and correct supply/demand imbalances per zone before they cascade |
| Supply shaping | Incentivise nurses to shift availability to high-demand windows |
| Incentive optimisation | Find minimum effective bonus that achieves fill-rate target |
| Marketplace equilibrium systems | Dynamic pricing that clears the market without over-paying |

**Build absolutely last.** This requires: stable dispatch, reliable data, economist-level analysis of marketplace dynamics. Do not speculate about pricing before you have 1,000 real transactions.

---

### Post-Traction Implementation Order

When the market validation gate is passed, build in this order — each item unlocks the next:

```
Gate: 100+ active users, AUSFT measured, 30 days stable dispatch
         │
         ▼
1. Supply-Demand Heatmaps (§25.1)          ← ops visibility first
2. Hospital Reliability Scoring (§25.5)     ← trust enforcement
3. Notification Fatigue System (§25.4)      ← nurse retention
4. Incident Management (§25.6)              ← safety gate (required before scaling)
         │
Gate: 500+ active users, fill rate > 85%
         │
         ▼
5. Redis Presence Layer (§25.7)             ← required for second Render instance
6. Dynamic Incentive System (§25.2)         ← if fill rate < 70%
7. Acceptance Probability Modeling (§25.3)  ← requires §25.7 data pipeline
         │
Gate: 1,000+ active users, multi-city
         │
         ▼
8. Kafka / Event Streaming (§25.8)          ← analytics scale
9. City Sharding (§25.10)                   ← ops scale
10. ML Matching Engine (§25.9)              ← requires §25.3 + §25.8 data
11. Predictive Demand Forecasting (§25.11)  ← requires 90d of history
12. Advanced Fraud Detection (§25.12)       ← required before open marketplace
13. Advanced Ops Tooling (§25.13)           ← requires §25.8 replay infra
14. Marketplace Economics Engine (§25.14)   ← last, requires all above
```
