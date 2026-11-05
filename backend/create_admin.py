"""Create the single administrator account from environment variables."""

import os
import sys

from .main import PASSWORDS, connect, init_storage, stamp


def main() -> None:
    user_id = os.getenv("ADMIN_USER_ID", "").strip()
    password = os.getenv("ADMIN_PASSWORD", "")
    if not user_id or not password:
        sys.exit("Set ADMIN_USER_ID and ADMIN_PASSWORD in the environment first.")
    if len(password) < 12:
        sys.exit("ADMIN_PASSWORD must contain at least 12 characters.")
    init_storage(bootstrap_admin=False)
    with connect() as db:
        if db.execute("SELECT 1 FROM users WHERE role = 'admin'").fetchone():
            sys.exit("An admin already exists. The application permits only one admin account.")
        db.execute("INSERT INTO users(id, password_hash, role, active, created_at) VALUES (?, ?, 'admin', 1, ?)", (user_id, PASSWORDS.hash(password), stamp()))
    print(f"Created the single admin account: {user_id}")


if __name__ == "__main__":
    main()
