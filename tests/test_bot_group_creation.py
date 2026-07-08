"""Tests for `/newgroup` (app/bot/handlers/group_creation.py)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from telethon.errors import FloodWaitError

from app.bot.handlers import group_creation as group_creation_handlers
from app.bot.states import GroupCreation
from app.db import crud
from app.db.models import Base, Role
from app.services import group_service
from tests.bot_fakes import FakeChat, FakeMessage, FakeUser, make_fsm_context

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _patch_db(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(group_creation_handlers, "async_session", sessionmaker)
    # process_group_title (below) delegates to group_service.create_group,
    # which owns its own async_session reference — must be patched too, or
    # it would fall through to the real (file-based) database.
    monkeypatch.setattr(group_service, "async_session", sessionmaker)
    yield sessionmaker
    await engine.dispose()


async def test_cmd_newgroup_requires_a_role_first(_patch_db) -> None:
    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1))
    state = make_fsm_context()

    await group_creation_handlers.cmd_newgroup(message, state)

    assert "обери посаду" in message.answers[0].lower()
    assert await state.get_state() is None


async def test_cmd_newgroup_requires_connected_account(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await session.commit()

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1))
    state = make_fsm_context()

    await group_creation_handlers.cmd_newgroup(message, state)

    assert "/connect" in message.answers[0]
    assert await state.get_state() is None


async def test_cmd_newgroup_prompts_for_title_when_ready(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1))
    state = make_fsm_context()

    await group_creation_handlers.cmd_newgroup(message, state)

    assert await state.get_state() == GroupCreation.waiting_for_title
    assert "назву" in message.answers[0].lower()


async def test_process_group_title_success_creates_group_and_tags_staff(
    _patch_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: "decrypted-" + s)

    # sync_tag_to_telegram talks to the real Bot API through the module-level
    # `bot` singleton — stub it so this test never hits the network (same
    # reasoning as create_group_with_team below).
    async def fake_sync_tag_to_telegram(chat_id: int, user_id: int, tag: str) -> None:
        return None

    monkeypatch.setattr(group_service, "sync_tag_to_telegram", fake_sync_tag_to_telegram)

    async def fake_create_group_with_team(session_string, title, staff):
        assert session_string == "decrypted-encrypted-session-string"
        assert title == "New Group"
        return 555

    monkeypatch.setattr(group_service, "create_group_with_team", fake_create_group_with_team)

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1), text="New Group")
    state = make_fsm_context()
    await state.set_state(GroupCreation.waiting_for_title)

    await group_creation_handlers.process_group_title(message, state)

    assert await state.get_state() is None
    assert "створено" in message.answers[-1].lower()

    async with _patch_db() as session:
        members = await crud.get_group_members(session, 555)
        assert [(m.user_id, m.tag) for m in members] == [(1, Role.MANAGER.value)]


async def test_process_group_title_requires_connected_account(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await session.commit()

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1), text="New Group")
    state = make_fsm_context()
    await state.set_state(GroupCreation.waiting_for_title)

    await group_creation_handlers.process_group_title(message, state)

    assert "/connect" in message.answers[-1]


async def test_process_group_title_reports_flood_wait(_patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_create_group_with_team(*_args):
        raise FloodWaitError(request=None, capture=20)

    monkeypatch.setattr(group_service, "create_group_with_team", fake_create_group_with_team)

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1), text="New Group")
    state = make_fsm_context()
    await state.set_state(GroupCreation.waiting_for_title)

    await group_creation_handlers.process_group_title(message, state)

    assert "Забагато запитів" in message.answers[-1]


async def test_process_group_title_reports_generic_failure_and_logs(
    _patch_db, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_create_group_with_team(*_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(group_service, "create_group_with_team", fake_create_group_with_team)

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1), text="New Group")
    state = make_fsm_context()
    await state.set_state(GroupCreation.waiting_for_title)

    with caplog.at_level("ERROR"):
        await group_creation_handlers.process_group_title(message, state)

    assert "Не вдалося створити групу" in message.answers[-1]
    assert "Не вдалося створити групу" in caplog.text
