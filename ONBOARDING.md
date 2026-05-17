# MediRoute — Developer Onboarding

## Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI · Python 3.10+ · SQLAlchemy 2 · Alembic |
| Database | Supabase PostgreSQL (pooler port 6543) |
| Frontend | React 19 · Vite 8 · Tailwind CSS v4 |
| Mobile | Capacitor 8 · Android |
| Hosting | Render (backend) |
| Push | Firebase FCM |
| OTP/SMS | MSG91 |
| Auth | JWT + Google OAuth |

---

## Prerequisites

Install these once on your machine:

| Tool | Download | Notes |
|---|---|---|
| Python 3.10+ | https://python.org | Add to PATH |
| Node.js LTS | https://nodejs.org | Add to PATH |
| Android Studio | https://developer.android.com/studio | Includes JDK + SDK |
| Git | https://git-scm.com | |

After Android Studio installs:
- Open **SDK Manager** → install **Android SDK Platform Tools** (provides `adb`)
- Set `ANDROID_HOME` to your SDK location (Android Studio usually sets this automatically)
  - Default Windows path: `%LOCALAPPDATA%\Android\Sdk`

---

## Clone & Validate

```powershell
git clone https://github.com/mastanrao2604/MediRoute.git
cd MediRoute

# Validate your environment first
powershell -ExecutionPolicy Bypass -File scripts\check-env.ps1
```

Fix any `[ERR]` items before continuing.

---

## One-Time Setup

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

This creates the Python venv, installs all backend + frontend dependencies, and copies `.env.example` → `.env`.

---

## Configure Secrets

### Backend (`mediroute-backend/.env`)

```env
# PostgreSQL — get from Supabase project settings (use pooler port 6543)
DATABASE_URL=postgresql+psycopg2://postgres.[project-ref]:[password]@aws-0-ap-south-1.pooler.supabase.com:6543/postgres

# Generate a secure key: python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=your-random-64-char-hex

ADMIN_PHONE=10_digit_phone
ADMIN_SECRET=your-admin-secret

# Firebase FCM — paste the full JSON from your service account key file (single line)
FIREBASE_CREDENTIALS_JSON={"type":"service_account","project_id":"..."}

# MSG91 OTP (leave as 'log' for local dev — OTP prints to console)
SMS_PROVIDER=log
# MSG91_AUTH_KEY=...
# MSG91_TEMPLATE_ID=...

ALLOWED_ORIGINS=http://localhost:5173,capacitor://localhost
ENV=development
```

### Frontend (`frontend/.env`)

```env
# For local dev (backend running on port 8000):
VITE_API_URL=http://localhost:8000

# For APK builds pointing to production:
# VITE_API_URL=https://your-backend.onrender.com

VITE_GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
VITE_ADMIN_PHONE=your_admin_phone
```

---

## Local Development

### Backend only

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev-backend.ps1
# API running at http://localhost:8000
# Swagger docs at http://localhost:8000/docs  (development mode only)
```

### Frontend only

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev-frontend.ps1
# App running at http://localhost:5173
```

### Both (two terminals)

```powershell
# Terminal 1
powershell -ExecutionPolicy Bypass -File scripts\dev-backend.ps1

# Terminal 2
powershell -ExecutionPolicy Bypass -File scripts\dev-frontend.ps1
```

---

## Database Migrations

```powershell
# Run all pending migrations (also runs automatically on Render deploy)
cd mediroute-backend
..\testenv\Scripts\alembic.exe upgrade head  # or: venv\Scripts\alembic.exe

# Create a new migration after changing models.py
alembic revision --autogenerate -m "describe your change"
```

---

## Android APK Build

### Debug APK (development / testing)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1
```

This does: `vite build` → `cap sync android` → `./gradlew assembleDebug` → `adb install`.

### Release APK (Play Store / distribution)

1. Generate a release keystore (once):
   ```
   keytool -genkey -v -keystore mediroute-release.jks -keyalg RSA -keysize 2048 -validity 10000 -alias mediroute
   ```
   Store the `.jks` file **outside** the repo (back it up — it cannot be recovered).

2. Copy the signing config:
   ```
   cp frontend\android\keystore.properties.example frontend\android\keystore.properties
   # Edit keystore.properties with your .jks path and passwords
   ```

3. Build:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1 -Release
   ```

4. Build + install + launch in one step:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1 -Launch
   ```

### Useful flags

| Flag | Effect |
|---|---|
| `-Release` | Release build (signed) instead of debug |
| `-SkipBuild` | Skip Vite + cap sync; Gradle only |
| `-Launch` | Auto-launch app after install |

---

## Render Deployment (Production)

Render reads `render.yaml` at the repo root. Deployments are **automatic on every push to `main`**.

Manual steps after first deploy:
1. Set all secret env vars in **Render Dashboard → Environment** (DATABASE_URL, SECRET_KEY, FIREBASE_CREDENTIALS_JSON, etc.)
2. Update `ALLOWED_ORIGINS` to include your Render URL
3. For APK builds, update `frontend/.env.production`:
   ```
   VITE_API_URL=https://your-service-name.onrender.com
   ```

---

## Project Structure

```
MediRoute/
├── mediroute-backend/       # FastAPI backend
│   ├── app/
│   │   ├── main.py          # App entry point
│   │   ├── models.py        # SQLAlchemy models
│   │   ├── schemas.py       # Pydantic schemas
│   │   ├── routes/          # API route modules
│   │   ├── dispatch/        # Real-time dispatch engine
│   │   └── utils/           # PDF, security helpers
│   ├── requirements.txt
│   ├── .env.example         # Copy → .env
│   └── render.yaml          # Render deploy config (at repo root)
│
├── frontend/                # React + Vite + Capacitor
│   ├── src/
│   │   ├── App.jsx          # Root with WebSocket + providers
│   │   ├── pages/           # Route-level pages
│   │   ├── components/      # Reusable UI components
│   │   ├── context/         # React context providers
│   │   ├── hooks/           # Custom hooks (WebSocket, push, etc.)
│   │   └── api/             # Axios instance
│   ├── android/             # Capacitor Android project
│   ├── .env.example         # Copy → .env
│   └── package.json
│
├── scripts/                 # Developer automation
│   ├── check-env.ps1        # Validate environment
│   ├── setup.ps1            # Install all dependencies
│   ├── dev-backend.ps1      # Start FastAPI dev server
│   ├── dev-frontend.ps1     # Start Vite dev server
│   └── build-android.ps1   # Build + install APK
│
└── generate_icons.py        # Generate app icons from source PNG
```

---

## Secrets Checklist

The following are **never committed** (covered by `.gitignore`):

- `mediroute-backend/.env`
- `frontend/.env` / `frontend/.env.production`
- `frontend/android/keystore.properties`
- `frontend/android/*.jks` / `*.keystore`
- `mediroute-backend/testenv/` and `venv/` (virtual environments)

---

## Quick Reference

```powershell
# Full APK build + install (debug, most common)
powershell -ExecutionPolicy Bypass -File scripts\build-android.ps1

# Environment check
powershell -ExecutionPolicy Bypass -File scripts\check-env.ps1

# Fresh machine setup
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```
