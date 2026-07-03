"""Tests for `/team` (app/bot/handlers/team.py)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bot.handlers import team as team_handlers
from app.db import crud
from app.db.models import Base
from tests.bot_fakes import FakeChat, FakeMessage, FakeUser

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _patch_db(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(team_handlers, "async_session", sessionmaker)
    yield sessionmaker
    await engine.dispose()


async def test_cmd_team_unregistered_group_prompts_register(_patch_db) -> None:
    message = FakeMessage(chat=FakeChat(id=100, type="group"), from_user=FakeUser(id=1))

    await team_handlers.cmd_team(message)

    assert "/register" in message.answers[0]


async def test_cmd_team_no_tagged_members(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.create_group_record(session, 100, "Group One", created_by_userbot=False)
        await session.commit()

    message = FakeMessage(chat=FakeChat(id=100, type="group"), from_user=FakeUser(id=1))

    await team_handlers.cmd_team(message)

    assert "немає тегованих" in message.answers[0]


async def test_cmd_team_lists_members_with_tags(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.create_group_record(session, 100, "Group One", created_by_userbot=False)
        await crud.add_member_tag(session, 100, 1, "Менеджер")
        await session.commit()

    message = FakeMessage(chat=FakeChat(id=100, type="group"), from_user=FakeUser(id=1))

    await team_handlers.cmd_team(message)

    assert message.answers[0] == "• Alice A. — Менеджер"
