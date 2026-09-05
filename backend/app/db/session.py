"""
Async SQLAlchemy engine + session factory.

We use the async engine (asyncpg driver) rather than sync SQLAlchemy because
the whole app is built on FastAPI's async request path — a sync DB call here
would block the event loop and stall every other in-flight request (including
WebSocket message pumps once phase 3 lands). Keeping the DB layer async keeps
that guarantee intact from day one instead of retrofitting it later.
"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)

async_session_factory = async_sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: one session per request, always closed after."""
    async with async_session_factory() as session:
        yield session
