# Krunal Study Hub

A responsive study-material library based on the supplied mockup. It has a React frontend, a FastAPI backend, SQLite metadata and sessions, and private local PDF storage. There are five sections, one administrator, and administrator-created viewer accounts. No sample PDFs or public registration are included.

## Requirements

- Python 3.10 or later
- Node.js 20.19 or later and npm

## Local setup

Open two terminals from the project root.

### 1. Configure the administrator

Copy `.env.example` to `.env`, then set a unique `ADMIN_USER_ID` and a strong `ADMIN_PASSWORD` (at least 12 characters). The backend hashes the password and creates the single admin account the first time it starts. `.env` is ignored by Git.

If the server has already started before credentials were configured, stop it, set both values in `.env`, and restart it. Once an admin exists, changing these variables does not replace its password.

### 2. Start the API

From the project root:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r backend\requirements.txt
py -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

The API creates `data\study_hub.sqlite3` and the private `uploads` directory on startup. Uploaded PDFs are stored under generated names and are not exposed as static files.

### 3. Start the frontend

In a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open the local URL printed by Vite. Its `/api` requests are proxied to FastAPI, so the browser uses same-origin session cookies.

### Run the integration check

From the project root:

```powershell
py -m pip install -r backend\requirements-dev.txt
py -m unittest backend.tests.test_api -v
```

## First admin and viewer accounts

The first admin is generated from `ADMIN_USER_ID` and `ADMIN_PASSWORD` during the first API startup. There is no admin-creation route and the database enforces one admin account.

Sign in as that admin, open **Admin**, and create viewer accounts. Enter a password or leave that field empty to generate one. The new password is shown once in the dashboard; copy and deliver it securely. Only a password hash is stored. Admins can activate or disable viewer accounts and can turn viewer downloads on or off.

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `ADMIN_USER_ID` | Initial administrator ID | unset |
| `ADMIN_PASSWORD` | Initial administrator password; minimum 12 characters | unset |
| `STUDY_HUB_DATA_DIR` | Database directory | `./data` |
| `STUDY_HUB_UPLOAD_DIR` | Private PDF storage directory | `./uploads` |
| `STUDY_HUB_MAX_UPLOAD_MB` | Maximum PDF upload size | `50` |
| `STUDY_HUB_SESSION_HOURS` | Session duration without “Remember me” | `8` |
| `STUDY_HUB_REMEMBER_DAYS` | Session duration with “Remember me” | `30` |
| `STUDY_HUB_COOKIE_SECURE` | Require HTTPS for the session cookie | `false` locally; set `true` in production |

## Features and access controls

- Session cookies are HTTP-only, SameSite strict, and stored server-side as hashes. Passwords use Argon2.
- Admin APIs enforce the admin role on the backend. Viewer routes cannot create users, upload, replace, delete, or change settings.
- Login attempts are limited to 10 tries per IP in a 10-minute window. The limiter is in-process, so run one API worker per instance with this SQLite setup.
- Uploads are limited by size, extension, and PDF signature. File records use generated IDs; original filenames are only display names.
- PDF metadata and file content require a valid session. The download route checks the admin-controlled download setting.
- The PDF.js viewer includes page/zoom controls and overlays the signed-in User ID on each displayed page. Browser-level copy/print deterrents cannot prevent screenshots, OS capture, cameras, or a determined user from retrieving content sent to their browser.
- Search matches PDF filenames. It does not index text inside PDF contents.

## Production deployment

Build the frontend with `npm run build` from `frontend`. Serve `frontend/dist` through an HTTPS reverse proxy and route `/api/*` to a single Uvicorn process (`uvicorn backend.main:app --host 127.0.0.1 --port 8000`). Configure the proxy to serve the SPA entry point for frontend routes such as `/section/1`, while passing `/api/*` directly to FastAPI.

Set `STUDY_HUB_COOKIE_SECURE=true`, keep `.env`, the SQLite database, and uploads in persistent directories outside the public web root, and back them up securely. Do not expose the upload directory through the proxy. For multi-instance or higher-volume deployments, replace the in-process login limiter and local SQLite/file storage with shared rate limiting and managed database/object storage.

The site discourages copying and printing and watermarks viewed pages, but the browser cannot block screenshots or all forms of content capture.
