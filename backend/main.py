from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import tempfile
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "").strip()
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "study-pdfs").strip() or "study-pdfs"
MAX_UPLOAD_BYTES = int(os.getenv("STUDY_HUB_MAX_UPLOAD_MB", "50")) * 1024 * 1024
SESSION_HOURS = int(os.getenv("STUDY_HUB_SESSION_HOURS", "8"))
REMEMBER_DAYS = int(os.getenv("STUDY_HUB_REMEMBER_DAYS", "30"))
COOKIE_SECURE = os.getenv("STUDY_HUB_COOKIE_SECURE", "false").lower() == "true"
COOKIE_NAME = "study_hub_session"
ALLOWED_ORIGINS_ENV = os.getenv("STUDY_HUB_ALLOWED_ORIGINS", "").strip()
PASSWORDS = PasswordHasher()
DUMMY_PASSWORD_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))
LOGIN_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)
LOGIN_LOCK = threading.Lock()
LOGGER = logging.getLogger("study_hub")


def normalize_origin(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        parsed.port
    except ValueError as exc:
        raise RuntimeError("STUDY_HUB_ALLOWED_ORIGINS must contain valid http(s) origins.") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("STUDY_HUB_ALLOWED_ORIGINS must contain plain http(s) origins.")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


if ALLOWED_ORIGINS_ENV:
    ALLOWED_ORIGINS = list(dict.fromkeys(normalize_origin(item) for item in ALLOWED_ORIGINS_ENV.split(",") if item.strip()))
else:
    ALLOWED_ORIGINS = [] if COOKIE_SECURE else ["http://localhost:5173", "http://127.0.0.1:5173"]

SECTION_INFO = [
    {"id": 1, "name": "Section 1", "description": "Notes, study material and important resources.", "tone": "blue"},
    {"id": 2, "name": "Section 2", "description": "Lectures, PDFs and reference material.", "tone": "violet"},
    {"id": 3, "name": "Section 3", "description": "Assignments, practicals and lab material.", "tone": "green"},
    {"id": 4, "name": "Section 4", "description": "Previous year papers, question banks and more.", "tone": "orange"},
    {"id": 5, "name": "Section 5", "description": "Extra resources, cheat sheets and revision notes.", "tone": "pink"},
]

app = FastAPI(title="Krunal Study Hub API", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Requested-With"],
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat(timespec="seconds")


def validate_runtime_configuration() -> None:
    missing = [
        name
        for name, value in (
            ("DATABASE_URL", DATABASE_URL),
            ("SUPABASE_URL", SUPABASE_URL),
            ("SUPABASE_SECRET_KEY", SUPABASE_SECRET_KEY),
        )
        if not value
    ]
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))
    if COOKIE_SECURE and not ALLOWED_ORIGINS:
        raise RuntimeError("Set STUDY_HUB_ALLOWED_ORIGINS to the exact production frontend origin(s).")


def connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required.")
    import psycopg
    from psycopg.rows import dict_row

    options = {"row_factory": dict_row, "connect_timeout": 10, "prepare_threshold": None}
    if COOKIE_SECURE:
        options["sslmode"] = "require"
    return psycopg.connect(DATABASE_URL, **options)


def init_storage(bootstrap_admin: bool = True) -> None:
    """Create missing PostgreSQL tables and indexes without dropping existing data."""
    with connect() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'viewer')),
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TEXT NOT NULL
            )
            """
        )
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_id_case_insensitive ON users (lower(id))")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_admin_only ON users(role) WHERE role = 'admin'")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS sessions_user_id ON sessions(user_id)")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS pdfs (
                id TEXT PRIMARY KEY,
                section SMALLINT NOT NULL CHECK (section BETWEEN 1 AND 5),
                filename TEXT NOT NULL,
                storage_name TEXT NOT NULL UNIQUE,
                size_bytes BIGINT NOT NULL,
                uploaded_at TEXT NOT NULL,
                uploaded_by TEXT NOT NULL REFERENCES users(id)
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS pdfs_section ON pdfs(section, uploaded_at DESC)")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        db.execute(
            "INSERT INTO settings(key, value) VALUES ('downloads_enabled', 'false') ON CONFLICT (key) DO NOTHING"
        )

        admin = db.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        if admin or not bootstrap_admin:
            return

        admin_id = os.getenv("ADMIN_USER_ID", "").strip()
        admin_password = os.getenv("ADMIN_PASSWORD", "")
        if not admin_id and not admin_password:
            LOGGER.warning(
                "No administrator exists. Set ADMIN_USER_ID and ADMIN_PASSWORD before startup, "
                "or run python -m backend.create_admin once."
            )
            return
        if not admin_id or not admin_password:
            raise RuntimeError("Set both ADMIN_USER_ID and ADMIN_PASSWORD to bootstrap the administrator.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,39}", admin_id):
            raise RuntimeError("ADMIN_USER_ID must be 3 to 40 letters, numbers, dots, dashes, or underscores.")
        if len(admin_password) < 12 or len(admin_password) > 128:
            raise RuntimeError("ADMIN_PASSWORD must contain 12 to 128 characters.")

        cursor = db.execute(
            """
            INSERT INTO users(id, password_hash, role, active, created_at)
            VALUES (%s, %s, 'admin', TRUE, %s)
            ON CONFLICT DO NOTHING
            """,
            (admin_id, PASSWORDS.hash(admin_password), stamp()),
        )
        if cursor.rowcount == 0:
            existing_admin = db.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
            if not existing_admin:
                raise RuntimeError("ADMIN_USER_ID is already in use by another account.")


_SUPABASE_CLIENT = None


def get_supabase_client():
    global _SUPABASE_CLIENT
    if _SUPABASE_CLIENT is None:
        validate_runtime_configuration()
        from supabase import create_client

        _SUPABASE_CLIENT = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)
    return _SUPABASE_CLIENT


def storage_bucket():
    return get_supabase_client().storage.from_(SUPABASE_BUCKET)


def storage_status_code(error: Exception) -> str:
    return str(getattr(error, "status_code", None) or getattr(error, "status", ""))


def ensure_private_bucket() -> None:
    """Create the bucket as private if missing, and refuse to use a public bucket."""
    storage = get_supabase_client().storage
    try:
        bucket = storage.get_bucket(SUPABASE_BUCKET)
    except Exception as exc:
        if storage_status_code(exc) != "404":
            raise RuntimeError("Could not verify the Supabase Storage bucket.") from exc
        try:
            storage.create_bucket(
                SUPABASE_BUCKET,
                options={
                    "public": False,
                    "file_size_limit": MAX_UPLOAD_BYTES,
                    "allowed_mime_types": ["application/pdf"],
                },
            )
            return
        except Exception:
            # Another worker may have created the private bucket concurrently.
            try:
                bucket = storage.get_bucket(SUPABASE_BUCKET)
            except Exception as verify_error:
                raise RuntimeError("Could not create or verify the private Supabase Storage bucket.") from verify_error

    public = bucket.get("public") if isinstance(bucket, dict) else getattr(bucket, "public", None)
    if public is not False:
        raise RuntimeError(f"Supabase bucket {SUPABASE_BUCKET!r} must have Public bucket turned off.")


def upload_storage_object(storage_name: str, staged_file: Path) -> None:
    try:
        with staged_file.open("rb") as source:
            storage_bucket().upload(
                path=storage_name,
                file=source,
                file_options={
                    "content-type": "application/pdf",
                    "cache-control": "0",
                    "upsert": "false",
                },
            )
    except Exception as exc:
        LOGGER.exception("Supabase Storage upload failed.")
        raise HTTPException(status_code=502, detail="PDF storage is temporarily unavailable.") from exc


def remove_storage_object(storage_name: str) -> None:
    try:
        storage_bucket().remove([storage_name])
    except Exception:
        # Any leftover object remains private even if cleanup fails.
        LOGGER.warning("Could not remove a Supabase Storage object.", exc_info=True)


def download_storage_object(storage_name: str) -> bytes:
    try:
        return storage_bucket().download(storage_name)
    except Exception as exc:
        if storage_status_code(exc) == "404":
            raise HTTPException(status_code=404, detail="PDF file is unavailable.") from exc
        LOGGER.exception("Supabase Storage download failed.")
        raise HTTPException(status_code=502, detail="PDF storage is temporarily unavailable.") from exc


def get_setting(db, key: str, default: str = "false") -> str:
    row = db.execute("SELECT value FROM settings WHERE key = %s", (key,)).fetchone()
    return row["value"] if row else default


def csrf_guard(request: Request) -> None:
    if request.headers.get("x-requested-with") != "StudyHub":
        raise HTTPException(status_code=403, detail="Request validation failed.")
    origin = request.headers.get("origin")
    if not origin:
        raise HTTPException(status_code=403, detail="Request validation failed.")
    try:
        normalized = normalize_origin(origin)
    except RuntimeError:
        raise HTTPException(status_code=403, detail="Request validation failed.")
    # The browser Origin is authoritative here. Behind a reverse proxy, Host
    # describes the backend destination and may differ from the frontend origin.
    if normalized not in ALLOWED_ORIGINS:
        raise HTTPException(status_code=403, detail="Request validation failed.")


def trim_attempts(key: str) -> int:
    now = time.monotonic()
    with LOGIN_LOCK:
        entries = LOGIN_ATTEMPTS[key]
        while entries and now - entries[0] > 600:
            entries.popleft()
        if len(entries) >= 10:
            return max(1, int(600 - (now - entries[0])))
        entries.append(now)
        return 0


def clear_attempts(key: str) -> None:
    with LOGIN_LOCK:
        LOGIN_ATTEMPTS.pop(key, None)


def current_user(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with connect() as db:
        row = db.execute(
            """
            SELECT users.id, users.role, users.active, sessions.expires_at
            FROM sessions JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = %s
            """,
            (token_hash,),
        ).fetchone()
        if not row or not row["active"] or datetime.fromisoformat(row["expires_at"]) <= utc_now():
            db.execute("DELETE FROM sessions WHERE token_hash = %s", (token_hash,))
            raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
    return {"id": row["id"], "role": row["role"]}


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return user


def valid_section(section: int) -> None:
    if section < 1 or section > 5:
        raise HTTPException(status_code=404, detail="Section not found.")


def to_pdf(row) -> dict:
    return {
        "id": row["id"],
        "section": row["section"],
        "filename": row["filename"],
        "size_bytes": row["size_bytes"],
        "uploaded_at": row["uploaded_at"],
    }


def safe_filename(value: str | None) -> str:
    name = (value or "study-material.pdf").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(char for char in name if char.isprintable() and char not in '"\\/\r\n')[:180].strip()
    if not name or not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="Upload a PDF file with a .pdf extension.")
    return name


async def stage_pdf(upload: UploadFile) -> tuple[Path, int]:
    safe_filename(upload.filename)
    staged: Path | None = None
    total = 0
    try:
        # Render's local filesystem is ephemeral; this file exists only for this upload request.
        with tempfile.NamedTemporaryFile(suffix=".part", delete=False) as target:
            staged = Path(target.name)
            first = await upload.read(5)
            if first != b"%PDF-":
                raise HTTPException(status_code=415, detail="The uploaded file is not a valid PDF.")
            target.write(first)
            total = len(first)
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"PDFs must be smaller than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                    )
                target.write(chunk)
        try:
            os.chmod(staged, 0o600)
        except OSError:
            pass
        return staged, total
    except Exception:
        if staged:
            staged.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


@app.on_event("startup")
def startup() -> None:
    validate_runtime_configuration()
    init_storage()
    ensure_private_bucket()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.get("/api/health")
def health():
    with connect() as db:
        db.execute("SELECT 1")
    return {"ok": True}


class LoginBody(BaseModel):
    user_id: str
    password: str
    remember_me: bool = False


class ViewerBody(BaseModel):
    user_id: str
    password: str | None = None


class ActiveBody(BaseModel):
    active: bool


class DownloadSettingBody(BaseModel):
    enabled: bool


@app.post("/api/auth/login")
def login(body: LoginBody, request: Request, response: Response):
    csrf_guard(request)
    client_ip = request.client.host if request.client else "unknown"
    retry = trim_attempts(client_ip)
    if retry:
        raise HTTPException(
            status_code=429,
            detail="Too many sign-in attempts. Try again later.",
            headers={"Retry-After": str(retry)},
        )
    with connect() as db:
        row = db.execute(
            "SELECT id, password_hash, role, active FROM users WHERE lower(id) = lower(%s)",
            (body.user_id.strip(),),
        ).fetchone()
        try:
            verified = PASSWORDS.verify(row["password_hash"] if row else DUMMY_PASSWORD_HASH, body.password)
        except (VerifyMismatchError, InvalidHashError):
            verified = False
        if not (row and row["active"] and verified):
            raise HTTPException(status_code=401, detail="The User ID or password is incorrect.")
        clear_attempts(client_ip)
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        lifetime = timedelta(days=REMEMBER_DAYS) if body.remember_me else timedelta(hours=SESSION_HOURS)
        expires = utc_now() + lifetime
        db.execute("DELETE FROM sessions WHERE expires_at <= %s", (stamp(),))
        db.execute(
            "INSERT INTO sessions(token_hash, user_id, expires_at, created_at) VALUES (%s, %s, %s, %s)",
            (token_hash, row["id"], stamp(expires), stamp()),
        )
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=int(lifetime.total_seconds()),
            httponly=True,
            secure=COOKIE_SECURE,
            samesite="strict",
            path="/api",
        )
        return {
            "user": {"id": row["id"], "role": row["role"]},
            "downloads_enabled": get_setting(db, "downloads_enabled") == "true",
            "expires_at": stamp(expires),
        }


@app.get("/api/auth/session")
def session(user: dict = Depends(current_user)):
    with connect() as db:
        return {"user": user, "downloads_enabled": get_setting(db, "downloads_enabled") == "true"}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, user: dict = Depends(current_user)):
    csrf_guard(request)
    token = request.cookies.get(COOKIE_NAME, "")
    with connect() as db:
        db.execute("DELETE FROM sessions WHERE token_hash = %s", (hashlib.sha256(token.encode("utf-8")).hexdigest(),))
    response.delete_cookie(COOKIE_NAME, path="/api", httponly=True, secure=COOKIE_SECURE, samesite="strict")
    return {"ok": True}


@app.get("/api/home")
def home(user: dict = Depends(current_user)):
    with connect() as db:
        counts = {row["section"]: row["count"] for row in db.execute("SELECT section, COUNT(*) AS count FROM pdfs GROUP BY section")}
        return {
            "user": user,
            "downloads_enabled": get_setting(db, "downloads_enabled") == "true",
            "sections": [{**section, "count": counts.get(section["id"], 0)} for section in SECTION_INFO],
        }


def escape_like(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


@app.get("/api/search")
def search_all(q: str = "", user: dict = Depends(current_user)):
    escaped = escape_like(q.strip()[:100])
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM pdfs WHERE lower(filename) LIKE lower(%s) ESCAPE '!' ORDER BY uploaded_at DESC LIMIT 30",
            (f"%{escaped}%",),
        ).fetchall()
        return {
            "files": [
                {**to_pdf(row), "section_name": SECTION_INFO[row["section"] - 1]["name"]}
                for row in rows
            ]
        }


@app.get("/api/sections/{section}/pdfs")
def section_pdfs(section: int, search: str = "", user: dict = Depends(current_user)):
    valid_section(section)
    escaped = escape_like(search.strip()[:100])
    with connect() as db:
        rows = db.execute(
            """
            SELECT * FROM pdfs
            WHERE section = %s AND lower(filename) LIKE lower(%s) ESCAPE '!'
            ORDER BY uploaded_at DESC, lower(filename)
            """,
            (section, f"%{escaped}%"),
        ).fetchall()
        return {
            "section": SECTION_INFO[section - 1],
            "files": [to_pdf(row) for row in rows],
            "count": len(rows),
            "downloads_enabled": get_setting(db, "downloads_enabled") == "true",
            "role": user["role"],
        }


@app.get("/api/pdfs/{pdf_id}")
def pdf_details(pdf_id: str, user: dict = Depends(current_user)):
    with connect() as db:
        row = db.execute("SELECT * FROM pdfs WHERE id = %s", (pdf_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="PDF not found.")
        return to_pdf(row)


@app.get("/api/pdfs/{pdf_id}/content")
def pdf_content(pdf_id: str, download: bool = False, user: dict = Depends(current_user)):
    with connect() as db:
        row = db.execute("SELECT * FROM pdfs WHERE id = %s", (pdf_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="PDF not found.")
        downloads_enabled = get_setting(db, "downloads_enabled") == "true"
        if download and user["role"] != "admin" and not downloads_enabled:
            raise HTTPException(status_code=403, detail="Downloads are currently disabled.")
    content = download_storage_object(row["storage_name"])
    disposition = "attachment" if download else "inline"
    encoded = quote(row["filename"], safe="")
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{encoded}",
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'self'; sandbox",
        },
    )


@app.get("/api/admin/dashboard")
def admin_dashboard(user: dict = Depends(require_admin)):
    with connect() as db:
        total = db.execute("SELECT COUNT(*) AS total FROM pdfs").fetchone()["total"]
        viewers = db.execute("SELECT COUNT(*) AS total FROM users WHERE role = 'viewer'").fetchone()["total"]
        active_viewers = db.execute(
            "SELECT COUNT(*) AS total FROM users WHERE role = 'viewer' AND active = TRUE"
        ).fetchone()["total"]
        counts = {row["section"]: row["count"] for row in db.execute("SELECT section, COUNT(*) AS count FROM pdfs GROUP BY section")}
        recent = db.execute("SELECT * FROM pdfs ORDER BY uploaded_at DESC LIMIT 8").fetchall()
        return {
            "total_pdfs": total,
            "total_viewers": viewers,
            "active_viewers": active_viewers,
            "downloads_enabled": get_setting(db, "downloads_enabled") == "true",
            "sections": [{**section, "count": counts.get(section["id"], 0)} for section in SECTION_INFO],
            "recent_uploads": [to_pdf(row) for row in recent],
        }


@app.get("/api/admin/viewers")
def list_viewers(user: dict = Depends(require_admin)):
    with connect() as db:
        rows = db.execute(
            "SELECT id, active, created_at FROM users WHERE role = 'viewer' ORDER BY created_at DESC"
        ).fetchall()
        return {"viewers": [dict(row) for row in rows]}


def is_unique_violation(error: Exception) -> bool:
    return getattr(error, "sqlstate", None) == "23505" or error.__class__.__name__ == "IntegrityError"


@app.post("/api/admin/viewers", status_code=201)
def create_viewer(body: ViewerBody, request: Request, user: dict = Depends(require_admin)):
    csrf_guard(request)
    user_id = body.user_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,39}", user_id):
        raise HTTPException(
            status_code=422,
            detail="User IDs must be 3 to 40 letters, numbers, dots, dashes, or underscores.",
        )
    password = body.password or secrets.token_urlsafe(12)
    if len(password) < 12 or len(password) > 128:
        raise HTTPException(status_code=422, detail="Passwords must be between 12 and 128 characters.")
    with connect() as db:
        try:
            db.execute(
                "INSERT INTO users(id, password_hash, role, active, created_at) VALUES (%s, %s, 'viewer', TRUE, %s)",
                (user_id, PASSWORDS.hash(password), stamp()),
            )
        except Exception as exc:
            if is_unique_violation(exc):
                raise HTTPException(status_code=409, detail="That User ID already exists.") from exc
            raise
    return {"user_id": user_id, "password": password, "created_at": stamp()}


@app.patch("/api/admin/viewers/{viewer_id}")
def set_viewer_active(viewer_id: str, body: ActiveBody, request: Request, user: dict = Depends(require_admin)):
    csrf_guard(request)
    with connect() as db:
        viewer = db.execute(
            "SELECT id FROM users WHERE lower(id) = lower(%s) AND role = 'viewer'",
            (viewer_id,),
        ).fetchone()
        if not viewer:
            raise HTTPException(status_code=404, detail="Viewer account not found.")
        db.execute("UPDATE users SET active = %s WHERE id = %s", (body.active, viewer["id"]))
        if not body.active:
            db.execute("DELETE FROM sessions WHERE user_id = %s", (viewer["id"],))
    return {"user_id": viewer["id"], "active": body.active}


@app.put("/api/admin/settings/downloads")
def set_downloads(body: DownloadSettingBody, request: Request, user: dict = Depends(require_admin)):
    csrf_guard(request)
    with connect() as db:
        db.execute(
            """
            INSERT INTO settings(key, value) VALUES ('downloads_enabled', %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            ("true" if body.enabled else "false",),
        )
    return {"downloads_enabled": body.enabled}


@app.post("/api/admin/pdfs", status_code=201)
async def upload_pdf(request: Request, section: int = Form(...), file: UploadFile = File(...), user: dict = Depends(require_admin)):
    csrf_guard(request)
    valid_section(section)
    filename = safe_filename(file.filename)
    staged, size = await stage_pdf(file)
    storage_name = f"{uuid.uuid4().hex}.pdf"
    pdf_id = uuid.uuid4().hex
    object_uploaded = False
    try:
        upload_storage_object(storage_name, staged)
        object_uploaded = True
        uploaded_at = stamp()
        with connect() as db:
            db.execute(
                """
                INSERT INTO pdfs(id, section, filename, storage_name, size_bytes, uploaded_at, uploaded_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (pdf_id, section, filename, storage_name, size, uploaded_at, user["id"]),
            )
    except Exception:
        if object_uploaded:
            remove_storage_object(storage_name)
        raise
    finally:
        staged.unlink(missing_ok=True)
    return {
        "id": pdf_id,
        "section": section,
        "filename": filename,
        "size_bytes": size,
        "uploaded_at": uploaded_at,
    }


@app.put("/api/admin/pdfs/{pdf_id}")
async def replace_pdf(pdf_id: str, request: Request, file: UploadFile = File(...), user: dict = Depends(require_admin)):
    csrf_guard(request)
    filename = safe_filename(file.filename)
    staged, size = await stage_pdf(file)
    storage_name = f"{uuid.uuid4().hex}.pdf"
    object_uploaded = False
    old_storage: str | None = None
    try:
        with connect() as db:
            old = db.execute("SELECT storage_name FROM pdfs WHERE id = %s", (pdf_id,)).fetchone()
        if not old:
            raise HTTPException(status_code=404, detail="PDF not found.")
        old_storage = old["storage_name"]
        upload_storage_object(storage_name, staged)
        object_uploaded = True
        with connect() as db:
            cursor = db.execute(
                """
                UPDATE pdfs
                SET filename = %s, storage_name = %s, size_bytes = %s, uploaded_at = %s, uploaded_by = %s
                WHERE id = %s
                """,
                (filename, storage_name, size, stamp(), user["id"], pdf_id),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="PDF not found.")
    except Exception:
        if object_uploaded:
            remove_storage_object(storage_name)
        raise
    finally:
        staged.unlink(missing_ok=True)
    if old_storage:
        remove_storage_object(old_storage)
    return {"id": pdf_id, "filename": filename, "size_bytes": size}


@app.delete("/api/admin/pdfs/{pdf_id}")
def delete_pdf(pdf_id: str, request: Request, user: dict = Depends(require_admin)):
    csrf_guard(request)
    with connect() as db:
        row = db.execute("SELECT storage_name FROM pdfs WHERE id = %s", (pdf_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="PDF not found.")
        db.execute("DELETE FROM pdfs WHERE id = %s", (pdf_id,))
    remove_storage_object(row["storage_name"])
    return {"ok": True}


@app.get("/api/admin/pdfs")
def list_all_pdfs(search: str = "", section: int | None = None, user: dict = Depends(require_admin)):
    clauses: list[str] = []
    values: list[object] = []
    if section is not None:
        valid_section(section)
        clauses.append("section = %s")
        values.append(section)
    if search.strip():
        clauses.append("lower(filename) LIKE lower(%s) ESCAPE '!'")
        values.append(f"%{escape_like(search.strip()[:100])}%")
    sql = "SELECT * FROM pdfs" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY uploaded_at DESC"
    with connect() as db:
        return {"files": [to_pdf(row) for row in db.execute(sql, values).fetchall()]}
