"""
Async SQLAlchemy engine, session factory, and base model.

Uses asyncpg driver for non-blocking Postgres I/O in FastAPI.
Connection pool is sized for typical production workload (20 connections).
"""

import os
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event, text

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/interview_scheduler",
)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


# Engine is created once at module load. asyncpg provides the non-blocking driver.
engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    pool_size=20,           # baseline connections kept open
    max_overflow=10,        # extra connections allowed under burst
    pool_pre_ping=True,     # verify connection is live before handing it out
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
)

# Session factory — do not use Session directly, use get_db() or db_session()
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,   # avoids lazy-load issues after commit in async context
    autoflush=False,
    autocommit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields a database session.

    Usage:
        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager for use outside of FastAPI request lifecycle
    (e.g. Celery tasks, CLI scripts).

    Usage:
        async with db_session() as db:
            result = await db.execute(...)
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def check_db_connection() -> bool:
    """Health-check helper. Returns True if Postgres is reachable."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("Database health check failed: %s", exc)
        return False
