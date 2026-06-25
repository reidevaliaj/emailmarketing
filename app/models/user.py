from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TZDateTime, pk_column, utcnow
from app.models.enums import UserRole


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = pk_column()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default=UserRole.USER, nullable=False)
    # Force a password change on first login from the seeded initial password.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
