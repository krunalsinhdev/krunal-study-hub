"""API integration coverage with a test-only SQLite adapter and fake private storage."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


_TEMP = tempfile.TemporaryDirectory(prefix="study-hub-tests-")
_ROOT = Path(_TEMP.name)
_DB_PATH = _ROOT / "test.sqlite3"
os.environ["PYTHON_DOTENV_DISABLED"] = "true"
os.environ["DATABASE_URL"] = "unused-by-tests"
os.environ["SUPABASE_URL"] = "https://example.invalid"
os.environ.pop("SUPABASE_SECRET_KEY", None)
os.environ["SUPABASE_BUCKET"] = "study-pdfs"
os.environ["STUDY_HUB_COOKIE_SECURE"] = "false"
os.environ["STUDY_HUB_ALLOWED_ORIGINS"] = "http://localhost:5173,http://127.0.0.1:5173"
os.environ["ADMIN_USER_ID"] = "test-admin"
os.environ["ADMIN_PASSWORD"] = "test-only-admin-password-123"

from backend import main as api_module  # noqa: E402
from backend.main import app  # noqa: E402


class SQLiteTestConnection:
    """Adapt the production qmark-free SQL to a disposable SQLite test database."""

    def __init__(self):
        self.connection = sqlite3.connect(_DB_PATH, timeout=15)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    def execute(self, query, parameters=()):
        return self.connection.execute(query.replace("%s", "?"), parameters)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc_type:
                self.connection.rollback()
            else:
                self.connection.commit()
        finally:
            self.connection.close()


class MemoryStorage:
    def __init__(self):
        self.files = {}

    def upload(self, path, file, file_options):
        self.files[path] = file.read()

    def download(self, path):
        return self.files[path]

    def remove(self, paths):
        for path in paths:
            self.files.pop(path, None)


class AccessLifecycleTests(unittest.TestCase):
    def setUp(self):
        _DB_PATH.unlink(missing_ok=True)
        self.storage = MemoryStorage()
        self.connect_patch = patch.object(api_module, "connect", SQLiteTestConnection)
        self.bucket_patch = patch.object(api_module, "storage_bucket", lambda: self.storage)
        self.private_bucket_patch = patch.object(api_module, "ensure_private_bucket", lambda: None)
        self.config_patch = patch.object(api_module, "validate_runtime_configuration", lambda: None)
        self.connect_patch.start()
        self.bucket_patch.start()
        self.private_bucket_patch.start()
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.addCleanup(self.private_bucket_patch.stop)
        self.addCleanup(self.bucket_patch.stop)
        self.addCleanup(self.connect_patch.stop)
        self.admin = TestClient(app)
        self.viewer = TestClient(app)
        self.admin.__enter__()
        self.viewer.__enter__()
        self.addCleanup(self.viewer.__exit__, None, None, None)
        self.addCleanup(self.admin.__exit__, None, None, None)

    @staticmethod
    def post_headers():
        return {
            "X-Requested-With": "StudyHub",
            "Origin": "http://localhost:5173",
        }

    def test_vercel_proxy_login_validates_browser_origin_not_backend_host(self):
        origin = "https://krunal-study-hub.vercel.app"
        headers = {
            "X-Requested-With": "StudyHub",
            "Origin": origin,
            "Host": "krunal-study-hub-api.onrender.com",
        }
        with patch.object(api_module, "ALLOWED_ORIGINS", [origin]):
            response = self.admin.post(
                "/api/auth/login",
                headers=headers,
                json={"user_id": "test-admin", "password": "test-only-admin-password-123"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["user"]["role"], "admin")

    def test_csrf_rejects_invalid_or_missing_origin_and_missing_header(self):
        allowed_origin = "https://krunal-study-hub.vercel.app"
        payload = {"user_id": "test-admin", "password": "test-only-admin-password-123"}
        with patch.object(api_module, "ALLOWED_ORIGINS", [allowed_origin]):
            invalid_origin = self.admin.post(
                "/api/auth/login",
                headers={"X-Requested-With": "StudyHub", "Origin": "https://attacker.example"},
                json=payload,
            )
            missing_origin = self.admin.post(
                "/api/auth/login",
                headers={"X-Requested-With": "StudyHub"},
                json=payload,
            )
            missing_header = self.admin.post(
                "/api/auth/login",
                headers={"Origin": allowed_origin},
                json=payload,
            )

        self.assertEqual(invalid_origin.status_code, 403)
        self.assertEqual(missing_origin.status_code, 403)
        self.assertEqual(missing_header.status_code, 403)

    def test_admin_viewer_upload_and_download_permissions(self):
        with SQLiteTestConnection() as db:
            admin_count = db.execute("SELECT COUNT(*) AS total FROM users WHERE role = 'admin'").fetchone()["total"]
        self.assertEqual(admin_count, 1)
        self.assertEqual(self.viewer.get("/api/admin/dashboard").status_code, 401)

        blocked_login = self.admin.post(
            "/api/auth/login",
            headers={**self.post_headers(), "Origin": "https://attacker.example"},
            json={"user_id": "test-admin", "password": "test-only-admin-password-123"},
        )
        self.assertEqual(blocked_login.status_code, 403)

        login = self.admin.post(
            "/api/auth/login",
            headers=self.post_headers(),
            json={"user_id": "test-admin", "password": "test-only-admin-password-123", "remember_me": True},
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(login.json()["user"]["role"], "admin")
        self.assertTrue(login.cookies.get("study_hub_session"))
        self.assertIn("Max-Age=2592000", login.headers.get("set-cookie", ""))

        created = self.admin.post(
            "/api/admin/viewers",
            headers=self.post_headers(),
            json={"user_id": "reader-01", "password": "viewer-test-password-123"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["user_id"], "reader-01")

        viewer_login = self.viewer.post(
            "/api/auth/login",
            headers=self.post_headers(),
            json={"user_id": "reader-01", "password": "viewer-test-password-123"},
        )
        self.assertEqual(viewer_login.status_code, 200, viewer_login.text)
        self.assertEqual(self.viewer.get("/api/admin/dashboard").status_code, 403)

        denied_upload = self.viewer.post(
            "/api/admin/pdfs",
            headers=self.post_headers(),
            data={"section": "1"},
            files={"file": ("notes.pdf", b"%PDF-1.4\n", "application/pdf")},
        )
        self.assertEqual(denied_upload.status_code, 403)

        invalid_pdf = self.admin.post(
            "/api/admin/pdfs",
            headers=self.post_headers(),
            data={"section": "1"},
            files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
        )
        self.assertEqual(invalid_pdf.status_code, 415)

        uploaded = self.admin.post(
            "/api/admin/pdfs",
            headers=self.post_headers(),
            data={"section": "1"},
            files={"file": ("unit-1.pdf", b"%PDF-1.4\nTest PDF content\n%%EOF", "application/pdf")},
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        pdf_id = uploaded.json()["id"]
        self.assertEqual(self.admin.get("/api/home").json()["sections"][0]["count"], 1)
        self.assertEqual(len(self.viewer.get("/api/search?q=UNIT-1").json()["files"]), 1)
        self.assertEqual(self.viewer.get(f"/api/pdfs/{pdf_id}/content").status_code, 200)
        self.assertEqual(self.viewer.get(f"/api/pdfs/{pdf_id}/content").content, b"%PDF-1.4\nTest PDF content\n%%EOF")
        self.assertEqual(self.viewer.get(f"/api/pdfs/{pdf_id}/content?download=true").status_code, 403)

        replaced = self.admin.put(
            f"/api/admin/pdfs/{pdf_id}",
            headers=self.post_headers(),
            files={"file": ("revised.pdf", b"%PDF-1.5\nRevised content\n%%EOF", "application/pdf")},
        )
        self.assertEqual(replaced.status_code, 200, replaced.text)
        self.assertEqual(len(self.storage.files), 1)
        self.assertEqual(
            self.viewer.get(f"/api/pdfs/{pdf_id}/content").content,
            b"%PDF-1.5\nRevised content\n%%EOF",
        )

        enabled = self.admin.put(
            "/api/admin/settings/downloads",
            headers=self.post_headers(),
            json={"enabled": True},
        )
        self.assertEqual(enabled.status_code, 200)
        downloaded = self.viewer.get(f"/api/pdfs/{pdf_id}/content?download=true")
        self.assertEqual(downloaded.status_code, 200)
        self.assertIn("attachment", downloaded.headers.get("content-disposition", ""))

        deleted = self.admin.delete(f"/api/admin/pdfs/{pdf_id}", headers=self.post_headers())
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(self.admin.get("/api/home").json()["sections"][0]["count"], 0)
        self.assertEqual(self.storage.files, {})

        logged_out = self.viewer.post("/api/auth/logout", headers=self.post_headers())
        self.assertEqual(logged_out.status_code, 200)
        self.assertEqual(self.viewer.get("/api/home").status_code, 401)
        viewer_login = self.viewer.post(
            "/api/auth/login",
            headers=self.post_headers(),
            json={"user_id": "reader-01", "password": "viewer-test-password-123"},
        )
        self.assertEqual(viewer_login.status_code, 200, viewer_login.text)

        disabled = self.admin.patch(
            "/api/admin/viewers/reader-01",
            headers=self.post_headers(),
            json={"active": False},
        )
        self.assertEqual(disabled.status_code, 200)
        self.assertEqual(self.viewer.get("/api/home").status_code, 401)
        self.assertEqual(self.viewer.get(f"/api/pdfs/{pdf_id}/content").status_code, 401)

        activated = self.admin.patch(
            "/api/admin/viewers/reader-01",
            headers=self.post_headers(),
            json={"active": True},
        )
        self.assertEqual(activated.status_code, 200)
        viewer_login = self.viewer.post(
            "/api/auth/login",
            headers=self.post_headers(),
            json={"user_id": "reader-01", "password": "viewer-test-password-123"},
        )
        self.assertEqual(viewer_login.status_code, 200, viewer_login.text)


if __name__ == "__main__":
    unittest.main()
