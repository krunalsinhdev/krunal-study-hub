"""API integration coverage using isolated SQLite and PDF directories."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


_TEMP = tempfile.TemporaryDirectory(prefix="study-hub-tests-")
_ROOT = Path(_TEMP.name)
os.environ["STUDY_HUB_DATA_DIR"] = str(_ROOT / "data")
os.environ["STUDY_HUB_UPLOAD_DIR"] = str(_ROOT / "uploads")
os.environ["STUDY_HUB_DB_PATH"] = str(_ROOT / "data" / "test.sqlite3")
os.environ["STUDY_HUB_COOKIE_SECURE"] = "false"
os.environ["ADMIN_USER_ID"] = "test-admin"
os.environ["ADMIN_PASSWORD"] = "test-only-admin-password-123"

from backend.main import app  # noqa: E402


class AccessLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.admin = TestClient(app)
        self.viewer = TestClient(app)
        self.admin.__enter__()
        self.viewer.__enter__()
        self.addCleanup(self.viewer.__exit__, None, None, None)
        self.addCleanup(self.admin.__exit__, None, None, None)

    @staticmethod
    def post_headers():
        return {"X-Requested-With": "StudyHub"}

    def test_admin_viewer_upload_and_download_permissions(self):
        self.assertEqual(self.viewer.get("/api/admin/dashboard").status_code, 401)

        login = self.admin.post(
            "/api/auth/login",
            headers=self.post_headers(),
            json={"user_id": "test-admin", "password": "test-only-admin-password-123"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(login.json()["user"]["role"], "admin")
        self.assertTrue(login.cookies.get("study_hub_session"))

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
        self.assertEqual(self.viewer.get(f"/api/pdfs/{pdf_id}/content").status_code, 200)
        self.assertEqual(self.viewer.get(f"/api/pdfs/{pdf_id}/content?download=true").status_code, 403)

        enabled = self.admin.put(
            "/api/admin/settings/downloads",
            headers=self.post_headers(),
            json={"enabled": True},
        )
        self.assertEqual(enabled.status_code, 200)
        downloaded = self.viewer.get(f"/api/pdfs/{pdf_id}/content?download=true")
        self.assertEqual(downloaded.status_code, 200)
        self.assertIn("attachment", downloaded.headers.get("content-disposition", ""))

        disabled = self.admin.patch(
            "/api/admin/viewers/reader-01",
            headers=self.post_headers(),
            json={"active": False},
        )
        self.assertEqual(disabled.status_code, 200)
        self.assertEqual(self.viewer.get("/api/home").status_code, 401)
        self.assertEqual(self.viewer.get(f"/api/pdfs/{pdf_id}/content").status_code, 401)


if __name__ == "__main__":
    unittest.main()
