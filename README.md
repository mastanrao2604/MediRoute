# MediRoute

Real-time healthcare staffing platform connecting medical professionals with shift opportunities. Hospitals post urgent shifts, nurses receive instant dispatch offers via WebSocket + Firebase push notifications, and recruiters get live visibility into the dispatch pipeline.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI · Python 3.10+ · SQLAlchemy 2 · Alembic |
| Database | Supabase PostgreSQL (pooler port 6543) |
| Frontend | React 19 · Vite 8 · Tailwind CSS v4 |
| Mobile | Capacitor 8 · Android |
| Hosting | Render (backend + web frontend) |
| Push Notifications | Firebase FCM |
| OTP / SMS | MSG91 |
| Auth | JWT · Google OAuth |
| Monitoring | Sentry |

---

## Prerequisites

Install these on your machine before anything else:

| Tool | Download | Notes |
|---|---|---|
| Python 3.10+ | https://python.org | ✅ Add to PATH during install |
| Node.js LTS | https://nodejs.org | ✅ Add to PATH during install |
| Android Studio | https://developer.android.com/studio | Includes JDK 17 + Android SDK |
| Git | https://git-scm.com | |

**After Android Studio installs:**
- Open **SDK Manager** → install **Android SDK Platform Tools** (provides `adb`)
- `ANDROID_HOME` is set automatically by Android Studio
  - Default path: `%LOCALAPPDATA%\Android\Sdk`

---

## Getting Started (New Machine)

### 1. Clone the repository

```powershell
git clone https://github.com/mastanrao2604/MediRoute.git
cd MediRoute
```

### 2. Run one-time setup

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup1.ps1
```

This will:
- Check Node.js, Python, Java, Android SDK
- Create Python virtual environment (`mediroute-backend/venv`)
- Install all backend dependencies (`pip install -r requirements.txt`)
- Install all frontend dependencies (`npm install`)
- Create `mediroute-backend/.env` and `frontend/.env` from examples
- Open both `.env` files in Notepad for you to fill in

### 3. Fill in credentials

When Notepad opens, fill in these values:

**`mediroute-backend/.env`**

```env
DATABASE_URL=postgresql+psycopg2://<user>:<password>@<host>:6543/<dbname>
SECRET_KEY=<run: python -c "import secrets; print(secrets.token_hex(32))">
ADMIN_PHONE=<10-digit admin phone>
ADMIN_SECRET=<choose a strong password>
FIREBASE_CREDENTIALS_JSON=<full JSON from Firebase service account key — single line>
GOOGLE_CLIENT_ID=<from Google Cloud Console>
ALLOWED_ORIGINS=http://localhost:5173,capacitor://localhost
ENV=development
```

**`frontend/.env`**

```env
VITE_API_URL=http://localhost:8000
VITE_GOOGLE_CLIENT_ID=<from Google Cloud Console>
VITE_ADMIN_PHONE=<same as ADMIN_PHONE above>
```

> Get credentials from the project owner via a secure channel. Never share via plain email or chat.

### 4. Connect Android device

- Enable **Developer Options** on your Android device
- Enable **USB Debugging** inside Developer Options
- Connect via USB cable
- Accept the "Allow USB Debugging" prompt on the device

### 5. Build + install APK

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1
```

This runs the full pipeline:
`Vite build` → `Capacitor sync` → `Gradle build` → `adb install`

The app installs and opens on your device automatically.

---

## Scripts

| Script | When to run | What it does |
|---|---|---|
| `scripts\setup1.ps1` | Once per machine | Full setup + env scaffolding |
| `scripts\build-android.ps1` | Every APK build | Build frontend + sync + build APK + push to device |

### build-android.ps1 flags

```powershell
# Debug build (default)
powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1

# Release build (requires keystore.properties)
powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1 -Release

# Skip Vite + cap sync (native-only change)
powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1 -SkipBuild

# Build + install + launch app automatically
powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1 -Launch
```

---

## Release Signing (APK for Play Store)

1. **Generate a keystore** (run once — back up the `.jks` file securely):
   ```
   keytool -genkey -v -keystore mediroute-release.jks -keyalg RSA -keysize 2048 -validity 10000 -alias mediroute
   ```

2. **Create signing config** (gitignored — never committed):
   ```powershell
   copy frontend\android\keystore.properties.example frontend\android\keystore.properties
   # Edit keystore.properties with your .jks path and passwords
   ```

3. **Build release APK:**
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1 -Release
   ```

> ⚠️ If the keystore is lost, Play Store updates become impossible. Store it in a password manager or secure backup.

---

## Environment Variables Reference

### Backend (`mediroute-backend/.env`)

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | ✅ | PostgreSQL connection string (Supabase pooler port 6543) |
| `SECRET_KEY` | ✅ | JWT signing key — generate with `secrets.token_hex(32)` |
| `ADMIN_PHONE` | ✅ | 10-digit phone number for admin access |
| `ADMIN_SECRET` | ✅ | Header secret for admin API endpoints |
| `FIREBASE_CREDENTIALS_JSON` | ✅ | Full Firebase service account JSON (single line) |
| `GOOGLE_CLIENT_ID` | ✅ | Google OAuth web client ID |
| `ALLOWED_ORIGINS` | ✅ | Comma-separated CORS origins |
| `ENV` | ✅ | `development` or `production` |
| `MSG91_AUTH_KEY` | Production | MSG91 OTP API key |
| `MSG91_TEMPLATE_ID` | Production | Approved DLT template ID |
| `SENTRY_DSN` | Optional | Sentry error monitoring DSN |

### Frontend (`frontend/.env`)

| Variable | Required | Description |
|---|---|---|
| `VITE_API_URL` | ✅ APK builds | Backend URL (e.g. `https://your-app.onrender.com`) |
| `VITE_GOOGLE_CLIENT_ID` | ✅ | Google OAuth web client ID |
| `VITE_ADMIN_PHONE` | ✅ | Admin phone (non-secret, used in UI routing) |
| `VITE_SENTRY_DSN` | Optional | Sentry frontend DSN |

> For web builds on Render, `VITE_API_URL` can be omitted — it falls back to `window.location.origin` automatically.

---

## Production Deployment (Render)

Deployments are **automatic on every push to `main`** via `render.yaml`.

**First deploy — set secrets in Render Dashboard:**
1. Go to **Render Dashboard → your service → Environment**
2. Add all variables marked as `sync: false` in `render.yaml`
3. Update `ALLOWED_ORIGINS` to include your Render URL

**For APK builds pointing to production**, use a separate `frontend/.env.production`:
```env
VITE_API_URL=https://your-service-name.onrender.com
```
Then build with:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1
```

---

## Database Migrations

```powershell
# Run all pending migrations (also runs automatically on Render deploy)
cd mediroute-backend
venv\Scripts\alembic upgrade head

# Create a new migration after changing app/models.py
venv\Scripts\alembic revision --autogenerate -m "describe your change"
venv\Scripts\alembic upgrade head
```

---

## Project Structure

```
MediRoute/
├── mediroute-backend/          # FastAPI backend
│   ├── app/
│   │   ├── main.py             # App entry point, CORS, Sentry init
│   │   ├── models.py           # SQLAlchemy ORM models
│   │   ├── schemas.py          # Pydantic request/response schemas
│   │   ├── database.py         # DB session + engine setup
│   │   ├── dependencies.py     # Auth dependencies (require_user, require_admin)
│   │   ├── routes/             # API route modules
│   │   │   ├── auth.py         # OTP + Google OAuth + JWT
│   │   │   ├── jobs.py         # Job CRUD
│   │   │   ├── applications.py # Job applications
│   │   │   ├── profile.py      # Nurse/recruiter profiles
│   │   │   ├── resume.py       # Resume upload + PDF generation
│   │   │   └── preferences.py  # Notification preferences
│   │   ├── dispatch/           # Real-time dispatch engine
│   │   │   ├── engine.py       # Wave-based dispatch logic
│   │   │   ├── fcm.py          # Firebase FCM push delivery
│   │   │   └── websocket.py    # WebSocket connection manager
│   │   └── utils/
│   │       ├── pdf_generator.py
│   │       └── security.py
│   ├── requirements.txt        # Python dependencies
│   ├── .env.example            # Copy → .env
│   └── alembic/                # DB migration files
│
├── frontend/                   # React + Vite + Capacitor
│   ├── src/
│   │   ├── App.jsx             # Root: providers, WebSocket, DispatchManager
│   │   ├── pages/              # Route-level pages (Dashboard, Jobs, Profile...)
│   │   ├── components/         # Reusable UI (DispatchOfferModal, AvailabilityToggle...)
│   │   ├── context/            # React contexts (Auth, Availability, Dispatch)
│   │   ├── hooks/              # useWebSocket, usePushNotifications, useAuth
│   │   └── api/                # Axios instance with base URL + interceptors
│   ├── android/                # Capacitor Android project (Gradle)
│   ├── public/                 # Static assets + PWA icons
│   ├── .env.example            # Copy → .env
│   └── package.json
│
├── scripts/
│   ├── setup1.ps1              # One-time setup (run first)
│   └── build-android.ps1       # Build + install APK (run every time)
│
├── render.yaml                 # Render deployment config
├── ONBOARDING.md               # Detailed developer onboarding guide
└── generate_icons.py           # Generate app icons from a source PNG
```

---

## Secrets — What Is Never Committed

| File | Contains |
|---|---|
| `mediroute-backend/.env` | DB password, JWT key, FCM credentials, admin secret |
| `frontend/.env` / `.env.production` | API URL, Google client ID |
| `frontend/android/keystore.properties` | APK signing passwords |
| `frontend/android/*.jks` / `*.keystore` | Release keystore file |
| `mediroute-backend/venv/` | Python virtual environment |

All covered by `.gitignore`.

---

## Quick Reference

```powershell
# Fresh machine — run once
powershell -ExecutionPolicy Bypass -File scripts\setup1.ps1

# Build + push APK to connected device
powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1

# Build release APK
powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1 -Release
```
