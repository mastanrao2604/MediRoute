# MediRoute — Copilot Handoff Prompt
> Paste this entire file as your first message in the new Copilot/Claude window.
> It contains everything needed to understand the codebase and continue development.

---

## 1. Project Identity

**App name:** MediRoute  
**Tagline:** Real-Time Healthcare Staffing  
**Platform:** Android APK (Capacitor) + Web PWA + REST API backend  
**Production backend URL:** `https://mediroute-8az0.onrender.com`  
**GitHub repo:** `https://github.com/mastanrao2604/MediRoute.git` (branch: `main`)  
**Render service:** Standard plan ($25/mo, no sleep), auto-deploys on push to `main`

---

## 2. Repository Layout

```
MediRoute/                          ← workspace root (git root)
├── ARCHITECTURE.md                 ← Full system design doc (§1–§25, ~2945 lines)
├── COPILOT_HANDOFF.md              ← This file
├── frontend/                       ← React 19 + Vite + Capacitor
│   ├── .env                        ← VITE_API_URL, VITE_ADMIN_PHONE, VITE_GOOGLE_CLIENT_ID
│   ├── index.html                  ← <title> and <meta description>
│   ├── vite.config.js              ← Vite + PWA plugin config
│   ├── public/manifest.json        ← PWA manifest (name, description, icons)
│   ├── dist/                       ← Built output — committed to git for Render static serve
│   ├── android/                    ← Capacitor Android project (Gradle)
│   └── src/
│       ├── App.jsx                 ← Root router, all routes, lazy imports
│       ├── context/AuthContext.jsx ← JWT auth state, login/logout
│       ├── components/
│       │   ├── ProtectedRoute.jsx
│       │   ├── InstallPrompt.jsx
│       │   ├── UpdatePrompt.jsx
│       │   ├── DispatchOfferModal.jsx  ← NEW: WebSocket offer pop-up for nurses
│       │   └── ...
│       ├── hooks/
│       │   └── useWebSocket.js     ← NEW: persistent WS hook with auto-reconnect
│       ├── pages/
│       │   ├── Login.jsx
│       │   ├── Dashboard.jsx
│       │   ├── Jobs.jsx / JobDetail.jsx
│       │   ├── Profile.jsx
│       │   ├── ResumeBuilder.jsx
│       │   ├── AdminDashboard.jsx
│       │   ├── DispatchOps.jsx     ← NEW: admin ops dashboard (/admin/ops)
│       │   └── ...
│       ├── services/api.js         ← Axios instance (baseURL = VITE_API_URL)
│       └── utils/
│           ├── authNav.js
│           └── downloadPdf.js
└── mediroute-backend/
    ├── .env                        ← DATABASE_URL, SECRET_KEY, MSG91, FCM, SENTRY, etc.
    ├── requirements.txt
    ├── alembic/                    ← DB migrations
    │   └── versions/
    │       └── c3d4e5f6a7b8_add_dispatch_tables.py  ← NEW: shifts/offers/events/zones
    └── app/
        ├── main.py                 ← FastAPI app, startup, static serve, CORS
        ├── database.py             ← SQLAlchemy sync engine, SessionLocal, get_db
        ├── models.py               ← All ORM models
        ├── schemas.py              ← Pydantic schemas
        ├── crud.py                 ← DB helpers
        ├── dependencies.py         ← require_admin, get_current_user
        ├── dispatch/               ← NEW: entire dispatch subsystem
        │   ├── __init__.py
        │   ├── engine.py           ← Core dispatch loop, kill switch, metrics, semaphore
        │   ├── janitor.py          ← Background task: expire stale offers every 30s
        │   └── events.py           ← Event type constants (OFFER_SENT, ACCEPTED, etc.)
        ├── routes/
        │   ├── auth.py
        │   ├── profile.py
        │   ├── jobs.py
        │   ├── applications.py
        │   ├── resume.py
        │   ├── preferences.py
        │   ├── admin.py
        │   ├── dashboard.py
        │   ├── recruiter.py
        │   ├── share.py
        │   ├── user.py
        │   ├── legal.py            ← GET /privacy, GET /delete-account (Play Store compliance)
        │   ├── availability.py     ← NEW: nurse availability + device token registration
        │   ├── shifts.py           ← NEW: shift CRUD for hospitals/recruiters
        │   ├── dispatch_routes.py  ← NEW: WebSocket + offer accept/decline endpoints
        │   └── ops.py              ← NEW: admin ops endpoints (health, toggle, timeline, etc.)
        ├── utils/
        │   ├── security.py         ← JWT encode/decode
        │   ├── pdf_generator.py    ← Resume PDF
        │   └── fcm.py              ← NEW: Firebase Cloud Messaging push notifications
        └── ws_manager.py           ← NEW: WebSocket ConnectionManager (pong tracking, eviction)
```

---

## 3. Tech Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI + Uvicorn, Python 3 |
| ORM | SQLAlchemy 2.0 (sync), wrapped in `run_in_executor` for async endpoints |
| Database | PostgreSQL on Supabase (`aws-1-ap-south-1.pooler.supabase.com:6543`) |
| Auth | JWT — 30-min access token / 7-day refresh token |
| OTP SMS | MSG91 |
| Push | Firebase Cloud Messaging (FCM) via `firebase-admin>=6.0.0` |
| Error tracking | Sentry (production only, `ENV=production`) |
| Frontend | React 19, Vite, Tailwind CSS v4, React Router v7 |
| PWA | `vite-plugin-pwa` (VitePWA, `autoUpdate`, Workbox) |
| Mobile | Capacitor 8.3.1, `androidScheme: "https"` |
| Hosting | Render Standard (backend + static frontend), GitHub auto-deploy |

---

## 4. Critical Configuration

### `frontend/.env`
```
VITE_API_URL=https://mediroute-8az0.onrender.com
VITE_ADMIN_PHONE=9493206268
VITE_GOOGLE_CLIENT_ID=461943303725-lqravijl3j6l251fp76j27nrg50elapc.apps.googleusercontent.com
```
> **IMPORTANT:** `VITE_API_URL` was previously `http://localhost:8000` which broke all APK builds. It is now fixed. Never change it back.

### Admin Credentials
- **Phone:** `9493206268`
- **Secret:** `AraVinDaM@6268`
- **Session key in localStorage:** `mediroute_admin_secret`

### Local Tooling (Windows)
- **node.exe:** `C:\Users\mv250058\Videos\MediRoute\MediRoute\mediroute-backend\node.exe`
- **Vite binary:** `frontend\node_modules\vite\bin\vite.js`
- **Python venv:** `mediroute-backend\testenv\Scripts\python.exe`
- **Activate venv:** `.\mediroute-backend\testenv\Scripts\Activate.ps1`

### Build Commands
```powershell
# Frontend build (always run before committing)
$node = "C:\Users\mv250058\Videos\MediRoute\MediRoute\mediroute-backend\node.exe"
Set-Location "C:\Users\mv250058\Videos\MediRoute\MediRoute\frontend"
& $node ".\node_modules\vite\bin\vite.js" build

# Deploy (frontend build + git add/commit/push → Render auto-deploys backend)
git add -A
git commit -m "your message"
git push origin main
```

### Python syntax check
```powershell
Set-Location "C:\Users\mv250058\Videos\MediRoute\MediRoute\mediroute-backend"
.\testenv\Scripts\python.exe -m py_compile app\main.py app\dispatch\engine.py app\dispatch\janitor.py app\routes\ops.py
```

---

## 5. Architecture Overview

### Request Flow (Web / PWA)
```
Browser → Render CDN → FastAPI (static serve of dist/)
                     → FastAPI API routes → SQLAlchemy → Supabase PostgreSQL
```

### Request Flow (Android APK)
```
Capacitor WebView → HTTPS → Render FastAPI
                           → WebSocket (/ws/nurse/{user_id}) → dispatch push
```

### Dispatch System (Real-Time Staffing Core)
```
Hospital posts Shift
    → engine.py:start_dispatch()
        → asyncio.Semaphore (max 30 concurrent)
        → Query available nurses by proximity + specialty
        → Offer fatigue filter (skip nurses with N recent offers)
        → Create ShiftOffer in DB
        → Signal asyncio.Event (dispatch_events[session_id])
        → FCM push notification (background) + WebSocket push (if online)
        → Nurse sees DispatchOfferModal in app
        → Accept → nurse_assignment created, other offers cancelled
        → Decline / Timeout → janitor expires offer → next nurse
```

### Janitor (background task, runs forever)
- Started in `app.on_event("startup")` via `asyncio.create_task(run_janitor())`
- Runs every 30s, expires stale offers, signals waiting engines
- Health tracked: `get_janitor_health()` → `{alive, last_tick_age_sec, tick_count, error_count}`
- Outer try/except ensures loop NEVER dies on any exception

### Kill Switch
- `is_dispatch_enabled()` / `set_dispatch_enabled(bool)` in `engine.py`
- Toggle via `POST /admin/ops/dispatch-toggle` (admin auth required)
- Surfaced in DispatchOps.jsx dashboard

### WebSocket Manager (`ws_manager.py`)
- `ConnectionManager` class
- Pong tracking — marks connections stale if no pong in N seconds
- Stale eviction on every send cycle

---

## 6. All Routes (as of commit `12a5307`)

### Public
| Method | Path | Description |
|---|---|---|
| GET | `/privacy` | Privacy policy HTML (Play Store) |
| GET | `/delete-account` | Account deletion instructions (Play Store) |
| GET | `/share/job/{job_id}` | Public job share page |

### Auth
| Method | Path | Description |
|---|---|---|
| POST | `/auth/send-otp` | Send OTP via MSG91 |
| POST | `/auth/verify-otp` | Verify OTP, return JWT |
| POST | `/auth/google` | Google OAuth login |
| POST | `/auth/refresh` | Refresh access token |
| POST | `/auth/logout` | Invalidate session |

### Candidate
| GET/POST/PATCH | `/profile`, `/preferences`, `/applications`, `/resume`, `/dashboard` | Standard CRUD |

### Recruiter
| GET/POST | `/recruiter/dashboard`, `/recruiter/jobs`, `/recruiter/jobs/{id}/applicants` | Recruiter flows |

### Admin
| GET/POST/PATCH | `/admin/*` | User management, verification |
| GET | `/admin/ops/live-shifts` | Active shifts + dispatch state |
| GET | `/admin/ops/health-snapshot` | In-memory health (safe to poll every 10s) |
| POST | `/admin/ops/dispatch-toggle` | Enable/disable dispatch engine |
| GET | `/admin/ops/timeline/{shift_id}` | Chronological shift events |
| POST | `/admin/ops/expire-session/{session_id}` | Force-expire offers |
| GET | `/admin/ops/metrics` | AUSFT + funnel metrics |
| POST | `/admin/ops/manual-assign` | Force-assign nurse to shift |
| POST | `/admin/ops/re-dispatch/{shift_id}` | Restart dispatch for failed shift |
| GET | `/admin/ops/failed-shifts` | Expired/unfilled shifts |
| PATCH | `/admin/ops/zones/{zone_code}` | Pause/resume dispatch zone |

### Dispatch (Nurse-facing)
| Method | Path | Description |
|---|---|---|
| WS | `/ws/nurse/{user_id}` | WebSocket for real-time offers |
| POST | `/dispatch/offers/{offer_id}/accept` | Accept a shift offer |
| POST | `/dispatch/offers/{offer_id}/decline` | Decline a shift offer |

### Shifts / Availability
| Method | Path | Description |
|---|---|---|
| POST | `/shifts` | Hospital creates shift |
| GET | `/shifts` | List shifts (filtered) |
| GET | `/shifts/{id}` | Shift detail |
| PATCH | `/shifts/{id}` | Update shift |
| POST | `/availability` | Nurse sets availability window |
| POST | `/devices/register` | Register FCM device token |

---

## 7. Database Models (key ones)

| Model | Table | Purpose |
|---|---|---|
| `User` | `users` | All users (candidate/recruiter/admin), phone, role |
| `Job` | `jobs` | Job listings posted by recruiters |
| `Application` | `applications` | Candidate job applications |
| `Shift` | `shifts` | Real-time shift requests from hospitals |
| `ShiftOffer` | `shift_offers` | Individual offers sent to nurses |
| `NurseAssignment` | `nurse_assignments` | Confirmed nurse-to-shift assignments |
| `ShiftEvent` | `shift_events` | Audit log of all dispatch events |
| `NurseAvailability` | `nurse_availability` | Nurse availability windows |
| `Zone` | `zones` | Geographic dispatch zones |
| `DeviceToken` | `device_tokens` | FCM tokens per user/device |

---

## 8. Frontend Routes (App.jsx)

| Path | Component | Guard |
|---|---|---|
| `/` | → role-based redirect | ProtectedRoute |
| `/login` | Login | PublicRoute |
| `/verify-otp` | OTPVerify | PublicRoute |
| `/onboarding` | Onboarding | ProtectedRoute |
| `/dashboard` | Dashboard | ProtectedRoute |
| `/jobs` | Jobs | ProtectedRoute |
| `/jobs/:id` | JobDetail | ProtectedRoute |
| `/profile` | Profile | ProtectedRoute |
| `/resume-builder` | ResumeBuilder | ProtectedRoute |
| `/recruiter/dashboard` | RecruiterDashboard | ProtectedRoute |
| `/recruiter/post-job` | PostJob | ProtectedRoute |
| `/recruiter/jobs/:id/applicants` | Applicants | ProtectedRoute |
| `/recruiter/candidates/:id` | CandidateDetail | ProtectedRoute |
| `/recruiter/onboarding` | RecruiterOnboarding | ProtectedRoute |
| `/admin/dashboard` | AdminDashboard | AdminRoute |
| `/admin/ops` | DispatchOps | AdminRoute |
| `/phone-link-verify` | PhoneLinkVerify | ProtectedRoute |

---

## 9. Branding — FINAL STATE

| Touch-point | Current Value |
|---|---|
| Login page tagline | `Real-Time Healthcare Staffing` |
| `<title>` (index.html) | `MediRoute — Real-Time Healthcare Staffing` |
| PWA manifest `name` | `MediRoute — Real-Time Healthcare Staffing` |
| PWA manifest `short_name` | `MediRoute` (home screen icon — do NOT change) |
| PWA manifest `description` | `Real-Time Healthcare Staffing — connecting medical professionals with opportunities.` |
| vite.config.js manifest `name` | `MediRoute — Real-Time Healthcare Staffing` |
| vite.config.js manifest `description` | `Real-Time Healthcare Staffing — connecting medical professionals with opportunities.` |
| Capacitor `appName` | `MediRoute` (Play Store name — do NOT change) |
| Package ID | `com.mediroute.app` (do NOT change) |
| Internal keys | `mediroute_token`, `mediroute_admin_secret` (do NOT change) |

---

## 10. Safety Rules (NEVER violate these)

1. **Never change `VITE_API_URL`** back to `localhost:8000` — it kills the APK
2. **Never change `com.mediroute.app`** package ID — breaks Play Store linking
3. **Never change `mediroute_token` / `mediroute_admin_secret`** storage keys — breaks auth
4. **Always run `vite build` before committing** — `dist/` is served directly by Render
5. **Never use `asyncio.get_event_loop()`** in backend — always `asyncio.get_running_loop()`
6. **Never wrap SQLAlchemy calls in `async def` directly** — use `run_in_executor`
7. **Never push to a branch other than `main`** — Render only deploys from `main`
8. **Never disable the `_janitor_task`** startup without a replacement — stale offers will pile up
9. **Never expose `/docs` or `/openapi.json` in production** — gated by `ENV=production`
10. **Never modify `android/` build artifacts directly** — only rebuild via `cap sync android`

---

## 11. Completed Work (this session)

### Branding
- Replaced "Healthcare Jobs Platform" → "Real-Time Healthcare Staffing" everywhere
- Updated `Login.jsx`, `index.html`, `vite.config.js`, `public/manifest.json`
- Updated meta `description` in `index.html` and `vite.config.js`
- Updated `legal.py` branding references

### Bug Fix
- Fixed `frontend/.env` `VITE_API_URL=http://localhost:8000` → production URL
- This was causing 100% API failure inside Android WebView

### New Files Added
| File | Purpose |
|---|---|
| `app/dispatch/engine.py` | Core dispatch logic, kill switch, metrics, semaphore |
| `app/dispatch/janitor.py` | Background offer expiry, health tracking |
| `app/dispatch/events.py` | Event type constants |
| `app/routes/ops.py` | 8+ admin ops endpoints |
| `app/routes/shifts.py` | Shift CRUD |
| `app/routes/availability.py` | Nurse availability + device token |
| `app/routes/dispatch_routes.py` | WebSocket + offer accept/decline |
| `app/ws_manager.py` | WebSocket ConnectionManager |
| `app/utils/fcm.py` | FCM push notification utility |
| `frontend/src/pages/DispatchOps.jsx` | Admin ops dashboard |
| `frontend/src/components/DispatchOfferModal.jsx` | Nurse offer modal |
| `frontend/src/hooks/useWebSocket.js` | WS hook with reconnect |
| `alembic/versions/c3d4e5f6a7b8_add_dispatch_tables.py` | DB migration |
| `ARCHITECTURE.md` | Full system design, §1–§25 |

### Deployment
- All work committed in `12a5307` and pushed to `origin/main`
- Render backend auto-deploying
- `dist/` regenerated with all new assets

---

## 12. Suggested Next Steps

These are possibilities — wait for user direction before starting any:

1. **Run the Alembic migration on production** — `alembic upgrade head` needs to run against Supabase to create `shifts`, `shift_offers`, `nurse_assignments`, `shift_events`, `zones`, `device_tokens` tables
2. **Test the dispatch flow end-to-end** — create a shift, verify offer reaches nurse via WebSocket
3. **Android APK rebuild** — `npx cap sync android` then rebuild APK in Android Studio with new branding
4. **Add Terms of Service page** to `legal.py` (similar pattern to `/privacy`)
5. **ARCHITECTURE.md §25 implementation** — Redis, Kafka, ML dispatch modeling (gated: 100+ active users)

---

## 13. Key Render/Supabase Facts

- **Render service name:** `mediroute-8az0`
- **Render deploy:** auto-triggered by push to `main` on GitHub
- **Render serves:** FastAPI (port 8000) + mounts `frontend/dist/` as static at `/`
- **Supabase host:** `aws-1-ap-south-1.pooler.supabase.com:6543`
- **Supabase region:** ap-south-1 (Mumbai)
- **DB connection mode:** connection pooler (port 6543, not 5432)

---

## 14. How to Verify Deployment

```powershell
# Check the live title
Invoke-WebRequest "https://mediroute-8az0.onrender.com" | Select-Object -ExpandProperty Content | Select-String "Real-Time"

# Check backend health
Invoke-WebRequest "https://mediroute-8az0.onrender.com/admin/ops/health-snapshot" -Headers @{Authorization="Bearer <admin_token>"}

# Check git is clean and synced
git status
git log --oneline -3
```
