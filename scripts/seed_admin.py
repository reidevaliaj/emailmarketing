"""Seed the initial admin user from environment variables.

Idempotent: running twice won't create a duplicate. The admin is created with
``must_change_password=True`` so the initial password from the env is forced to
be changed at first login (Section 8).

Usage:  python -m scripts.seed_admin
"""

from __future__ import annotations

from sqlalchemy import select

from app.config import settings
from app.db import sync_session
from app.models.enums import UserRole
from app.models.user import User
from app.services.auth import hash_password, normalize_login


def main() -> None:
    email = normalize_login(settings.app_admin_email)
    with sync_session() as session:
        existing = session.scalar(select(User).where(User.email == email))
        if existing is not None:
            print(f"Admin user {email!r} already exists (id={existing.id}). Nothing to do.")
            return
        session.add(
            User(
                email=email,
                password_hash=hash_password(settings.app_admin_initial_password),
                role=UserRole.ADMIN.value,
                must_change_password=True,
            )
        )
        print(f"Created admin user {email!r}. You must change the password at first login.")


if __name__ == "__main__":
    main()
