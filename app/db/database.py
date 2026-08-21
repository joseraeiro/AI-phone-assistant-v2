"""Async engine and session lifecycle."""

from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.db.models import Base


class Database:
    """Own the async SQLAlchemy engine and short-lived session factory."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.engine: AsyncEngine = create_async_engine(url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()

    def session(self) -> AsyncSession:
        return self.sessions()


@lru_cache
def database_for_url(url: str) -> Database:
    return Database(url)


def get_database(settings: Settings) -> Database:
    """Return the database configured for this process."""

    return database_for_url(settings.database_url)
