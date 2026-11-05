from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
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
from fastapi.responses import FileResponse
from pydantic import BaseModel


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
DATA_DIR = Path(os.getenv("STUDY_HUB_DATA_DIR", str(ROOT / "data"))).resolve()
UPLOAD_DIR = Path(os.getenv("STUDY_HUB_UPLOAD_DIR", str(ROOT / "uploads"))).resolve()
DB_PATH = Path(os.getenv("STUDY_HUB_DB_PATH", str(DATA_DIR / "study_hub.sqlite3"))).resolve()
MAX_UPLOAD_BYTES = int(os.getenv("STUDY_HUB_MAX_UPLOAD_MB", "50")) * 1024 * 1024
SESSION_HOURS = int(os.getenv("STUDY_HUB_SESSION_HOURS", "8"))
REMEMBER_DAYS = int(os.getenv("STUDY_HUB_REMEMBER_DAYS", "30"))
COOKIE_SECURE = os.getenv("STUDY_HUB_COOKIE_SECURE", "false").lower() == "true"
COOKIE_NAME = "study_hub_session"
PASSWORDS = PasswordHasher()
DUMMY_PASSWORD_HASH = PASSWORDS.hash(secrets.token_urlsafe(32))
LOGIN_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)
LOGIN_LOCK = threading.Lock()

SECTION_INFO = [
    {"id": 1, "name": "Section 1", "description": "Notes, study material and important resources.", "tone": "blue"},
    {"id": 2, "name": "Section 2", "description": "Lectures, PDFs and reference material.", "tone": "violet"},
    {"id": 3, "name": "Section 3", "description": "Assignments, practicals and lab material.", "tone": "green"},
    {"id": 4, "name": "Section 4", "description": "Previous year papers, question banks and more.", "tone": "orange"},
    {"id": 5, "name": "Section 5", "description": "Extra resources, cheat sheets and revision notes.", "tone": "pink"},
]

app = FastAPI(title="Krunal Study Hub API", docs_url=None, redoc_url=None, openapi_url=None)


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc, traceback):
        try:
            return super().__exit__(exc_type, exc, traceback)
        finally:
            self.close()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH, timeout=15, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    return db


def init_storage(bootstrap_admin: bool = True) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(DATA_DIR, 0o700)
        os.chmod(UPLOAD_DIR, 0o700)
    except OSError:
        pass
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'viewer')),
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_admin_only
                ON users(role) WHERE role = 'admin';
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS sessions_user_id ON sessions(user_id);
            CREATE TABLE IF NOT EXISTS pdfs (
                id TEXT PRIMARY KEY,
                section INTEGER NOT NULL CHECK (section BETWEEN 1 AND 5),
                filename TEXT NOT NULL,
                storage_name TEXT NOT NULL UNIQUE,
                size_bytes INTEGER NOT NULL,
                uploaded_at TEXT NOT NULL,
                uploaded_by TEXT NOT NULL REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS pdfs_section ON pdfs(section, uploaded_at DESC);
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT OR IGNORE INTO settings(key, value) VALUES ('downloads_enabled', 'false');
            """
        )
        admin = db.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        if not admin and bootstrap_admin:
            admin_id = os.getenv("ADMIN_USER_ID", "").strip()
            admin_password = os.getenv("ADMIN_PASSWORD", "")
            if admin_id and admin_password:
                if len(admin_password) < 12:
                    raise RuntimeError("ADMIN_PASSWORD must contain at least 12 characters.")
                db.execute(
                    "INSERT INTO users(id, password_hash, role, active, created_at) VALUES (?, ?, 'admin', 1, ?)",
                    (admin_id, PASSWORDS.hash(admin_password), stamp()),
                )
            else:
                print("No admin account exists. Set ADMIN_USER_ID and ADMIN_PASSWORD before startup, or run `python -m backend.create_admin`.")


def get_setting(db: sqlite3.Connection, key: str, default: str = "false") -> str:
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def csrf_guard(request: Request) -> None:
    if request.headers.get("x-requested-with") != "StudyHub":
        raise HTTPException(status_code=403, detail="Request validation failed.")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc.lower() != request.headers.get("host", "").lower():
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
            """SELECT users.id, users.role, users.active, sessions.expires_at
               FROM sessions JOIN users ON users.id = sessions.user_id
               WHERE sessions.token_hash = ?""",
            (token_hash,),
        ).fetchone()
        if not row or not row["active"] or datetime.fromisoformat(row["expires_at"]) <= utc_now():
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")
    return {"id": row["id"], "role": row["role"]}


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return user


def valid_section(section: int) -> None:
    if section < 1 or section > 5:
        raise HTTPException(status_code=404, detail="Section not found.")


def to_pdf(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "section": row["section"], "filename": row["filename"],
        "size_bytes": row["size_bytes"], "uploaded_at": row["uploaded_at"],
    }


def safe_filename(value: str | None) -> str:
    name = (value or "study-material.pdf").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(char for char in name if char.isprintable() and char not in '\"\\/\r\n')[:180].strip()
    if not name or not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="Upload a PDF file with a .pdf extension.")
    return name


async def stage_pdf(upload: UploadFile) -> tuple[Path, int]:
    safe_filename(upload.filename)
    staged: Path | None = None
    total = 0
    try:
        with tempfile.NamedTemporaryFile(dir=UPLOAD_DIR, suffix=".part", delete=False) as target:
            staged = Path(target.name)
            first = await upload.read(5)
            if first != b"%PDF-":
                raise HTTPException(status_code=415, detail="The uploaded file is not a valid PDF.")
            target.write(first)
            total = len(first)
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=f"PDFs must be smaller than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
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
    init_storage()


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
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again later.", headers={"Retry-After": str(retry)})
    with connect() as db:
        row = db.execute("SELECT id, password_hash, role, active FROM users WHERE id = ? COLLATE NOCASE", (body.user_id.strip(),)).fetchone()
        try:
            verified = PASSWORDS.verify(row["password_hash"] if row else DUMMY_PASSWORD_HASH, body.password)
        except (VerifyMismatchError, InvalidHashError):
            verified = False
        ok = bool(row and row["active"] and verified)
        if not ok:
            raise HTTPException(status_code=401, detail="The User ID or password is incorrect.")
        clear_attempts(client_ip)
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        lifetime = timedelta(days=REMEMBER_DAYS) if body.remember_me else timedelta(hours=SESSION_HOURS)
        expires = utc_now() + lifetime
        db.execute("DELETE FROM sessions WHERE expires_at <= ?", (stamp(),))
        db.execute("INSERT INTO sessions(token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)", (token_hash, row["id"], stamp(expires), stamp()))
        response.set_cookie(COOKIE_NAME, token, max_age=int(lifetime.total_seconds()), httponly=True, secure=COOKIE_SECURE, samesite="strict", path="/api")
        return {"user": {"id": row["id"], "role": row["role"]}, "downloads_enabled": get_setting(db, "downloads_enabled") == "true", "expires_at": stamp(expires)}


@app.get("/api/auth/session")
def session(user: dict = Depends(current_user)):
    with connect() as db:
        return {"user": user, "downloads_enabled": get_setting(db, "downloads_enabled") == "true"}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, user: dict = Depends(current_user)):
    csrf_guard(request)
    token = request.cookies.get(COOKIE_NAME, "")
    with connect() as db:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (hashlib.sha256(token.encode("utf-8")).hexdigest(),))
    response.delete_cookie(COOKIE_NAME, path="/api", httponly=True, secure=COOKIE_SECURE, samesite="strict")
    return {"ok": True}


@app.get("/api/home")
def home(user: dict = Depends(current_user)):
    with connect() as db:
        counts = {row["section"]: row["count"] for row in db.execute("SELECT section, COUNT(*) AS count FROM pdfs GROUP BY section")}
        return {"user": user, "downloads_enabled": get_setting(db, "downloads_enabled") == "true", "sections": [{**section, "count": counts.get(section["id"], 0)} for section in SECTION_INFO]}


@app.get("/api/search")
def search_all(q: str = "", user: dict = Depends(current_user)):
    query = q.strip()[:100]
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with connect() as db:
        rows = db.execute("SELECT * FROM pdfs WHERE filename LIKE ? ESCAPE '\\' ORDER BY uploaded_at DESC LIMIT 30", (f"%{escaped}%",)).fetchall()
        return {"files": [{**to_pdf(row), "section_name": SECTION_INFO[row["section"] - 1]["name"]} for row in rows]}


@app.get("/api/sections/{section}/pdfs")
def section_pdfs(section: int, search: str = "", user: dict = Depends(current_user)):
    valid_section(section)
    query = search.strip()[:100]
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with connect() as db:
        rows = db.execute("SELECT * FROM pdfs WHERE section = ? AND filename LIKE ? ESCAPE '\\' ORDER BY uploaded_at DESC, filename COLLATE NOCASE", (section, f"%{escaped}%")).fetchall()
        return {"section": SECTION_INFO[section - 1], "files": [to_pdf(row) for row in rows], "count": len(rows), "downloads_enabled": get_setting(db, "downloads_enabled") == "true", "role": user["role"]}


@app.get("/api/pdfs/{pdf_id}")
def pdf_details(pdf_id: str, user: dict = Depends(current_user)):
    with connect() as db:
        row = db.execute("SELECT * FROM pdfs WHERE id = ?", (pdf_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="PDF not found.")
        return to_pdf(row)


@app.get("/api/pdfs/{pdf_id}/content")
def pdf_content(pdf_id: str, download: bool = False, user: dict = Depends(current_user)):
    with connect() as db:
        row = db.execute("SELECT * FROM pdfs WHERE id = ?", (pdf_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="PDF not found.")
        downloads_enabled = get_setting(db, "downloads_enabled") == "true"
        if download and user["role"] != "admin" and not downloads_enabled:
            raise HTTPException(status_code=403, detail="Downloads are currently disabled.")
    stored = (UPLOAD_DIR / row["storage_name"]).resolve()
    if stored.parent != UPLOAD_DIR or not stored.is_file():
        raise HTTPException(status_code=404, detail="PDF file is unavailable.")
    disposition = "attachment" if download else "inline"
    encoded = quote(row["filename"], safe="")
    return FileResponse(stored, media_type="application/pdf", headers={"Content-Disposition": f"{disposition}; filename*=UTF-8''{encoded}", "Content-Security-Policy": "default-src 'none'; frame-ancestors 'self'; sandbox"})


@app.get("/api/admin/dashboard")
def admin_dashboard(user: dict = Depends(require_admin)):
    with connect() as db:
        total = db.execute("SELECT COUNT(*) FROM pdfs").fetchone()[0]
        viewers = db.execute("SELECT COUNT(*) FROM users WHERE role = 'viewer'").fetchone()[0]
        active_viewers = db.execute("SELECT COUNT(*) FROM users WHERE role = 'viewer' AND active = 1").fetchone()[0]
        counts = {row["section"]: row["count"] for row in db.execute("SELECT section, COUNT(*) AS count FROM pdfs GROUP BY section")}
        recent = db.execute("SELECT * FROM pdfs ORDER BY uploaded_at DESC LIMIT 8").fetchall()
        return {"total_pdfs": total, "total_viewers": viewers, "active_viewers": active_viewers, "downloads_enabled": get_setting(db, "downloads_enabled") == "true", "sections": [{**section, "count": counts.get(section["id"], 0)} for section in SECTION_INFO], "recent_uploads": [to_pdf(row) for row in recent]}


@app.get("/api/admin/viewers")
def list_viewers(user: dict = Depends(require_admin)):
    with connect() as db:
        rows = db.execute("SELECT id, active, created_at FROM users WHERE role = 'viewer' ORDER BY created_at DESC").fetchall()
        return {"viewers": [dict(row) for row in rows]}


@app.post("/api/admin/viewers", status_code=201)
def create_viewer(body: ViewerBody, request: Request, user: dict = Depends(require_admin)):
    csrf_guard(request)
    user_id = body.user_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,39}", user_id):
        raise HTTPException(status_code=422, detail="User IDs must be 3–40 letters, numbers, dots, dashes, or underscores.")
    password = body.password or secrets.token_urlsafe(12)
    if len(password) < 12 or len(password) > 128:
        raise HTTPException(status_code=422, detail="Passwords must be between 12 and 128 characters.")
    with connect() as db:
        try:
            db.execute("INSERT INTO users(id, password_hash, role, active, created_at) VALUES (?, ?, 'viewer', 1, ?)", (user_id, PASSWORDS.hash(password), stamp()))
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="That User ID already exists.")
    return {"user_id": user_id, "password": password, "created_at": stamp()}


@app.patch("/api/admin/viewers/{viewer_id}")
def set_viewer_active(viewer_id: str, body: ActiveBody, request: Request, user: dict = Depends(require_admin)):
    csrf_guard(request)
    with connect() as db:
        cursor = db.execute("UPDATE users SET active = ? WHERE id = ? AND role = 'viewer'", (1 if body.active else 0, viewer_id))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Viewer account not found.")
        if not body.active:
            db.execute("DELETE FROM sessions WHERE user_id = ?", (viewer_id,))
    return {"user_id": viewer_id, "active": body.active}


@app.put("/api/admin/settings/downloads")
def set_downloads(body: DownloadSettingBody, request: Request, user: dict = Depends(require_admin)):
    csrf_guard(request)
    with connect() as db:
        db.execute("INSERT INTO settings(key, value) VALUES ('downloads_enabled', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", ("true" if body.enabled else "false",))
    return {"downloads_enabled": body.enabled}


@app.post("/api/admin/pdfs", status_code=201)
async def upload_pdf(request: Request, section: int = Form(...), file: UploadFile = File(...), user: dict = Depends(require_admin)):
    csrf_guard(request)
    valid_section(section)
    filename = safe_filename(file.filename)
    staged, size = await stage_pdf(file)
    storage_name = f"{uuid.uuid4().hex}.pdf"
    target = UPLOAD_DIR / storage_name
    pdf_id = uuid.uuid4().hex
    try:
        os.replace(staged, target)
        with connect() as db:
            db.execute("INSERT INTO pdfs(id, section, filename, storage_name, size_bytes, uploaded_at, uploaded_by) VALUES (?, ?, ?, ?, ?, ?, ?)", (pdf_id, section, filename, storage_name, size, stamp(), user["id"]))
    except Exception:
        staged.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    return {"id": pdf_id, "section": section, "filename": filename, "size_bytes": size, "uploaded_at": stamp()}


@app.put("/api/admin/pdfs/{pdf_id}")
async def replace_pdf(pdf_id: str, request: Request, file: UploadFile = File(...), user: dict = Depends(require_admin)):
    csrf_guard(request)
    filename = safe_filename(file.filename)
    staged, size = await stage_pdf(file)
    storage_name = f"{uuid.uuid4().hex}.pdf"
    target = UPLOAD_DIR / storage_name
    old_storage: str | None = None
    try:
        with connect() as db:
            old = db.execute("SELECT storage_name FROM pdfs WHERE id = ?", (pdf_id,)).fetchone()
            if not old:
                raise HTTPException(status_code=404, detail="PDF not found.")
            old_storage = old["storage_name"]
            os.replace(staged, target)
            db.execute("UPDATE pdfs SET filename = ?, storage_name = ?, size_bytes = ?, uploaded_at = ?, uploaded_by = ? WHERE id = ?", (filename, storage_name, size, stamp(), user["id"], pdf_id))
        if old_storage:
            (UPLOAD_DIR / old_storage).unlink(missing_ok=True)
    except Exception:
        staged.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    return {"id": pdf_id, "filename": filename, "size_bytes": size}


@app.delete("/api/admin/pdfs/{pdf_id}")
def delete_pdf(pdf_id: str, request: Request, user: dict = Depends(require_admin)):
    csrf_guard(request)
    with connect() as db:
        row = db.execute("SELECT storage_name FROM pdfs WHERE id = ?", (pdf_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="PDF not found.")
        db.execute("DELETE FROM pdfs WHERE id = ?", (pdf_id,))
    (UPLOAD_DIR / row["storage_name"]).unlink(missing_ok=True)
    return {"ok": True}


@app.get("/api/admin/pdfs")
def list_all_pdfs(search: str = "", section: int | None = None, user: dict = Depends(require_admin)):
    clauses: list[str] = []
    values: list[object] = []
    if section is not None:
        valid_section(section)
        clauses.append("section = ?")
        values.append(section)
    if search.strip():
        clauses.append("filename LIKE ? ESCAPE '\\'")
        escaped = search.strip()[:100].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        values.append(f"%{escaped}%")
    sql = "SELECT * FROM pdfs" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY uploaded_at DESC"
    with connect() as db:
        return {"files": [to_pdf(row) for row in db.execute(sql, values).fetchall()]}
