"""Declarative base and shared column conventions for all ORM models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Project-wide declarative base.

    All models inherit ``id`` plus ``created_at`` / ``updated_at`` timestamps
    so auditing is uniform across tables.
    """

    # BigInteger because Telegram IDs and a 30k-bot / millions-of-users scale
    # will happily exceed 32-bit ranges over time.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
