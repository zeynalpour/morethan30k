"""Async SQLAlchemy engine and session factory.

A single engine/pool is shared across the whole process; every request or
service call opens a short-lived :class:`AsyncSession` via :func:`session_scope`
or the FastAPI :func:`get_session` dependency.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tme.config import settings

# `pool_pre_ping` guards against stale connections after Postgres restarts /
# idle timeouts — important for a long-lived gateway process.
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=10,
    future=True,
)

SessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Provide a transactional session scope for service-layer code.

    Commits on success, rolls back on error, and always closes the session::

        async with session_scope() as session:
            session.add(obj)
    """
    session = SessionFactory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session (transaction committed on exit)."""
    async with session_scope() as session:
        yield session


async def dispose_engine() -> None:
    """Dispose of the engine's connection pool (call on app shutdown)."""
    await engine.dispose()
