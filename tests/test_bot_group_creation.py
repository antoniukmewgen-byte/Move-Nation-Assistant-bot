"""Tests for `/newgroup` (app/bot/handlers/group_creation.py)."""

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from telethon.errors import FloodWaitError

from app.bot.handlers import group_creation as group_creation_handlers
from app.bot.states import GroupCreation
from app.db import crud
from app.db.models import Base, Role
from app.services import group_creation_registry, group_service
from tests.bot_fakes import FakeChat, FakeMessage, FakeUser, make_fsm_context

pytestmark = pytest.mark.asyncio


class FakeBot:
    """Stubs `group_service.bot` — see `_send_group_welcome_message`.

    Only `create_group` sends through this module-level `bot` singleton (the
    handler itself only ever calls `message.answer`), so patching it here is
    enough to keep the whole `create_group` call chain network-free.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **_kwargs: Any) -> None:
        self.sent.append((chat_id, text))


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


@pytest.fixture(autouse=True)
def _reset_group_creation_registry():
    # create_group (app/services/group_service.py) marks/unmarks the actor's
    # user_id in this shared, in-process registry — clear it around each test
    # so a test that fails mid-flow (leaving it marked) can't bleed into the
    # next one's is_pending() checks.
    group_creation_registry.unmark_pending(*group_creation_registry._pending_actor_ids)
    yield
    group_creation_registry.unmark_pending(*group_creation_registry._pending_actor_ids)


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
        # create_group must mark the actor as pending *before* this (the
        # first Telegram-touching call) even starts — that's what closes the
        # race with Bot API's own, independently-timed delivery of the
        # resulting my_chat_member updates (see
        # app/services/group_creation_registry.py).
        assert group_creation_registry.is_pending(1) is True
        return 555

    monkeypatch.setattr(group_service, "create_group_with_team", fake_create_group_with_team)

    # _send_group_welcome_message also talks to the real Bot API through the
    # same module-level `bot` singleton — stub it for the same reason.
    fake_bot = FakeBot()
    monkeypatch.setattr(group_service, "bot", fake_bot)

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1), text="New Group")
    state = make_fsm_context()
    await state.set_state(GroupCreation.waiting_for_title)

    await group_creation_handlers.process_group_title(message, state)

    assert await state.get_state() is None
    assert "створено" in message.answers[-1].lower()

    async with _patch_db() as session:
        members = await crud.get_group_members(session, 555)
        assert [(m.user_id, m.tag) for m in members] == [(1, Role.MANAGER.value)]

    # The group's own welcome message (app/services/group_service.py::
    # _send_group_welcome_message) must go to the new chat, name it, and list
    # the starting team roster — not the generic "/register, /tag" text that
    # on_bot_added_to_group sends for externally-added-to-group bots.
    assert len(fake_bot.sent) == 1
    welcome_chat_id, welcome_text = fake_bot.sent[0]
    assert welcome_chat_id == 555

    # Unmarked again once the whole flow (including the welcome message
    # above) has finished.
    assert group_creation_registry.is_pending(1) is False
    assert "New Group" in welcome_text
    assert "Alice A." in welcome_text
    assert Role.MANAGER.value in welcome_text


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

    # Must not leak the actor's pending mark when create_group_with_team
    # itself blows up — otherwise a later, genuinely external "bot added"
    # event for this same staff member would be wrongly suppressed forever.
    assert group_creation_registry.is_pending(1) is False


# --- _send_group_welcome_message ---------------------------------------------


async def test_send_group_welcome_message_lists_full_names_and_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_bot = FakeBot()
    monkeypatch.setattr(group_service, "bot", fake_bot)

    staff = [
        SimpleNamespace(id=1, username="alice", full_name="Alice A.", role=Role.MANAGER),
        SimpleNamespace(id=2, username="bob", full_name="Bob B.", role=Role.TEAMLEAD),
    ]

    await group_service._send_group_welcome_message(555, "New Group", staff)

    assert len(fake_bot.sent) == 1
    chat_id, text = fake_bot.sent[0]
    assert chat_id == 555
    assert "New Group" in text
    assert f"Alice A. — {Role.MANAGER.value}" in text
    assert f"Bob B. — {Role.TEAMLEAD.value}" in text


async def test_send_group_welcome_message_falls_back_to_username_then_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_bot = FakeBot()
    monkeypatch.setattr(group_service, "bot", fake_bot)

    staff = [
        SimpleNamespace(id=1, username="alice", full_name=None, role=Role.MANAGER),
        SimpleNamespace(id=2, username=None, full_name=None, role=Role.TEAMLEAD),
    ]

    await group_service._send_group_welcome_message(555, "New Group", staff)

    _chat_id, text = fake_bot.sent[0]
    assert f"alice — {Role.MANAGER.value}" in text
    assert f"2 — {Role.TEAMLEAD.value}" in text


async def test_send_group_welcome_message_swallows_send_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    # Best-effort, same as sync_tag_to_telegram — the group itself is already
    # created and committed by the time this runs, so a Telegram-side failure
    # here must not raise back into create_group.
    class FailingBot:
        async def send_message(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("boom")

    monkeypatch.setattr(group_service, "bot", FailingBot())

    staff = [SimpleNamespace(id=1, username="alice", full_name="Alice A.", role=Role.MANAGER)]

    await group_service._send_group_welcome_message(555, "New Group", staff)
