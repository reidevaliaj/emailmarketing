from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TZDateTime, pk_column, utcnow
from app.models.enums import TemplateType


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(10), default=TemplateType.PLAIN, nullable=False)
    subject_default: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
