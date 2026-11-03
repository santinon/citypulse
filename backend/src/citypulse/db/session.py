"""Async Psycopg engines and caller-owned sessions."""

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(url: str) -> AsyncEngine:
    """Create an engine whose connections start in UTC."""
    parsed_url = make_url(url)
    if parsed_url.drivername != "postgresql+psycopg":
        raise ValueError("Use a postgresql+psycopg database URL.")
    return create_async_engine(
        parsed_url,
        pool_pre_ping=True,
        connect_args={"options": "-c timezone=UTC", "connect_timeout": 5},
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create sessions without committing or closing the caller's transaction."""
    return async_sessionmaker(engine, expire_on_commit=False)
