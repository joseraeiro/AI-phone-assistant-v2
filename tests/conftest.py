"""Per-test database isolation for HTTP and WebSocket tests."""

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.db.database import Database
from app.db.dependencies import get_call_repository
from app.db.repository import CallRepository
from app.main import app


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path) -> Iterator[None]:
    """Prevent tests from sharing lifecycle rows through the default SQLite file."""

    path = tmp_path / "test.db"
    database = Database(f"sqlite+aiosqlite:///{path}")
    asyncio.run(database.create_schema())
    repository = CallRepository(database)
    app.dependency_overrides[get_call_repository] = lambda: repository
    yield
    app.dependency_overrides.pop(get_call_repository, None)
    asyncio.run(database.dispose())
