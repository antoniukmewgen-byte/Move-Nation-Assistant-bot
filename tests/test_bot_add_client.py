"""Tests for `/add_client` (app/bot/handlers/add_client.py)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from telethon.errors import FloodWaitError

from app.bot.handlers import add_client as add_client_handlers
from app.bot.states import AddClient
from app.db import crud
from app.db.models import CLIENT_TAG, Base
from app.services import group_service
from tests.bot_fakes import FakeCallbackQuery, FakeChat, FakeMessage, FakeUser, make_fsm_context

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _patch_db(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(add_client_handlers, "async_session", sessionmaker)
    # process_contact (below) delegates to group_service.add_client, which
    # owns its own async_session reference — must be patched too, or it
    # would fall through to the real (file-based) database.
    monkeypatch.setattr(group_service, "async_session", sessionmaker)
    yield sessionmaker
    await engine.dispose()


async def test_cmd_add_client_requires_connected_account(_patch_db) -> None:
    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1))
    state = make_fsm_context()

    await add_client_handlers.cmd_add_client(message, state)

    assert "/connect" in message.answers[0]


async def test_cmd_add_client_requires_at_least_one_group(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1))
    state = make_fsm_context()

    await add_client_handlers.cmd_add_client(message, state)

    assert "жодної групи" in message.answers[0]


async def test_cmd_add_client_prompts_group_choice(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await crud.create_group_record(session, 100, "Group One", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 1, "Менеджер")
        await session.commit()

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1))
    state = make_fsm_context()

    await add_client_handlers.cmd_add_client(message, state)

    assert await state.get_state() == AddClient.choosing_group
    assert "Обери групу" in message.answers[0]


async def test_choose_group_stores_group_id_and_prompts_for_contact() -> None:
    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1))
    callback = FakeCallbackQuery(data="addclient_group:100", message=message, from_user=FakeUser(id=1))
    state = make_fsm_context()
    await state.set_state(AddClient.choosing_group)

    await add_client_handlers.choose_group(callback, state)

    assert await state.get_state() == AddClient.waiting_for_contact
    assert (await state.get_data())["group_id"] == 100
    assert callback.answered is True
    assert "username" in message.edits[0]


async def test_process_contact_requires_connected_account(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.create_group_record(session, 100, "Group One", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 1, "Менеджер")
        await session.commit()

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1), text="@newclient")
    state = make_fsm_context()
    await state.set_state(AddClient.waiting_for_contact)
    await state.update_data(group_id=100)

    await add_client_handlers.process_contact(message, state)

    assert "/connect" in message.answers[0]


async def test_process_contact_rejects_group_the_sender_is_not_a_member_of(_patch_db) -> None:
    # group_service.add_client (app/services/group_service.py) checks
    # membership before anything else — closes a gap where a crafted/stale
    # `addclient_group:<id>` callback could target a group the sender was
    # never tagged into, since choose_group (above) itself trusts the
    # callback_data's group_id without validating it server-side.
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await crud.create_group_record(session, 100, "Group One", created_by_userbot=True)
        await session.commit()

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1), text="@newclient")
    state = make_fsm_context()
    await state.set_state(AddClient.waiting_for_contact)
    await state.update_data(group_id=100)

    await add_client_handlers.process_contact(message, state)

    assert "Немає доступу" in message.answers[-1]


async def test_process_contact_success_persists_client_and_tags(
    _patch_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await crud.create_group_record(session, 100, "Group One", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 1, "Менеджер")
        await session.commit()

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: "decrypted-" + s)

    # sync_tag_to_telegram talks to the real Bot API through the module-level
    # `bot` singleton — stub it so this test never hits the network (same
    # reasoning as add_client_to_group below).
    async def fake_sync_tag_to_telegram(chat_id: int, user_id: int, tag: str) -> None:
        return None

    monkeypatch.setattr(group_service, "sync_tag_to_telegram", fake_sync_tag_to_telegram)

    async def fake_add_client_to_group(session_string, group_id, identifier):
        assert session_string == "decrypted-encrypted-session-string"
        assert group_id == 100
        assert identifier == "@newclient"
        return 42, "New Client", None

    monkeypatch.setattr(group_service, "add_client_to_group", fake_add_client_to_group)

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1), text="@newclient")
    state = make_fsm_context()
    await state.set_state(AddClient.waiting_for_contact)
    await state.update_data(group_id=100)

    await add_client_handlers.process_contact(message, state)

    assert await state.get_state() is None
    assert "додано" in message.answers[-1].lower()

    async with _patch_db() as session:
        members = await crud.get_group_members(session, 100)
        client_rows = [(m.user_id, m.tag) for m in members if m.tag == CLIENT_TAG]
        assert client_rows == [(42, CLIENT_TAG)]


async def test_process_contact_success_with_invite_link_when_direct_add_fails(
    _patch_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await crud.create_group_record(session, 100, "Group One", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 1, "Менеджер")
        await session.commit()

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_add_client_to_group(*_args):
        return 42, "New Client", "https://t.me/+invitelink"

    monkeypatch.setattr(group_service, "add_client_to_group", fake_add_client_to_group)

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1), text="@newclient")
    state = make_fsm_context()
    await state.set_state(AddClient.waiting_for_contact)
    await state.update_data(group_id=100)

    await add_client_handlers.process_contact(message, state)

    assert "https://t.me/+invitelink" in message.answers[-1]


async def test_process_contact_user_not_found(_patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await crud.create_group_record(session, 100, "Group One", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 1, "Менеджер")
        await session.commit()

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_add_client_to_group(*_args):
        return None, None, None

    monkeypatch.setattr(group_service, "add_client_to_group", fake_add_client_to_group)

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1), text="@ghost")
    state = make_fsm_context()
    await state.set_state(AddClient.waiting_for_contact)
    await state.update_data(group_id=100)

    await add_client_handlers.process_contact(message, state)

    assert "не знайдено" in message.answers[-1].lower()


async def test_process_contact_reports_flood_wait(_patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await crud.create_group_record(session, 100, "Group One", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 1, "Менеджер")
        await session.commit()

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_add_client_to_group(*_args):
        raise FloodWaitError(request=None, capture=10)

    monkeypatch.setattr(group_service, "add_client_to_group", fake_add_client_to_group)

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1), text="@newclient")
    state = make_fsm_context()
    await state.set_state(AddClient.waiting_for_contact)
    await state.update_data(group_id=100)

    await add_client_handlers.process_contact(message, state)

    assert "Забагато запитів" in message.answers[-1]


async def test_process_contact_reports_generic_failure_and_logs(
    _patch_db, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await crud.create_group_record(session, 100, "Group One", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 1, "Менеджер")
        await session.commit()

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_add_client_to_group(*_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(group_service, "add_client_to_group", fake_add_client_to_group)

    message = FakeMessage(chat=FakeChat(id=1), from_user=FakeUser(id=1), text="@newclient")
    state = make_fsm_context()
    await state.set_state(AddClient.waiting_for_contact)
    await state.update_data(group_id=100)

    with caplog.at_level("ERROR"):
        await add_client_handlers.process_contact(message, state)

    assert "Не вдалося додати клієнта" in message.answers[-1]
    assert "Не вдалося додати клієнта" in caplog.text
