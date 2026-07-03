"""Tests for `/start` and `/role` (app/bot/handlers/start.py)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bot.handlers import start as start_handlers
from app.db.models import Base, User
from tests.bot_fakes import FakeChat, FakeMessage, FakeUser

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _patch_db(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(start_handlers, "async_session", sessionmaker)
    yield sessionmaker
    await engine.dispose()


async def test_cmd_start_creates_user_and_greets(_patch_db) -> None:
    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1, username="alice"))

    await start_handlers.cmd_start(message)

    assert len(message.answers) == 1
    assert "MoveNation Assistant" in message.answers[0]

    async with _patch_db() as session:
        user = await session.get(User, 1)
        assert user is not None
        assert user.username == "alice"


async def test_cmd_role_points_to_mini_app() -> None:
    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1))

    await start_handlers.cmd_role(message)

    assert len(message.answers) == 1
    assert "Профіль" in message.answers[0]
