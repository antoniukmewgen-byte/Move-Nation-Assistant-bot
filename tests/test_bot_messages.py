"""Tests for group-chat handlers (app/bot/handlers/messages.py):
bot-added-to-group registration, `/register`, `/tag`, and the passive
awaiting-response tracker on every non-command group message.
"""

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bot.handlers import messages as messages_handlers
from app.db import crud
from app.db.models import Base, GroupStatus
from tests.bot_fakes import FakeChat, FakeMessage, FakeUser

pytestmark = pytest.mark.asyncio


class FakeBot:
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
    monkeypatch.setattr(messages_handlers, "async_session", sessionmaker)
    yield sessionmaker
    await engine.dispose()


# --- on_bot_added_to_group ---------------------------------------------------


async def test_on_bot_added_to_group_ignores_private_chats(_patch_db) -> None:
    bot = FakeBot()
    event = SimpleNamespace(chat=FakeChat(id=1, type="private"), bot=bot)

    await messages_handlers.on_bot_added_to_group(event)

    assert bot.sent == []
    async with _patch_db() as session:
        assert await crud.get_group(session, 1) is None


async def test_on_bot_added_to_group_registers_new_group_and_greets(_patch_db) -> None:
    bot = FakeBot()
    event = SimpleNamespace(chat=FakeChat(id=100, type="group", title="Team Chat"), bot=bot)

    await messages_handlers.on_bot_added_to_group(event)

    async with _patch_db() as session:
        group = await crud.get_group(session, 100)
        assert group is not None
        assert group.title == "Team Chat"

    assert len(bot.sent) == 1
    assert bot.sent[0][0] == 100


async def test_on_bot_added_to_group_is_a_noop_for_already_registered_group(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=False)
        await session.commit()

    bot = FakeBot()
    event = SimpleNamespace(chat=FakeChat(id=100, type="group", title="Team Chat"), bot=bot)

    await messages_handlers.on_bot_added_to_group(event)

    assert bot.sent == []


# --- on_bot_removed_from_group -----------------------------------------------


async def test_on_bot_removed_from_group_deletes_the_record(_patch_db) -> None:
    async with _patch_db() as session:
        group = await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=False)
        await crud.add_member_tag(session, group.id, 1, "Менеджер")
        await session.commit()

    event = SimpleNamespace(chat=FakeChat(id=100, type="supergroup"))

    await messages_handlers.on_bot_removed_from_group(event)

    async with _patch_db() as session:
        assert await crud.get_group(session, 100) is None


async def test_on_bot_removed_from_group_ignores_private_chats(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.create_group_record(session, 1, "Not a group", created_by_userbot=False)
        await session.commit()

    event = SimpleNamespace(chat=FakeChat(id=1, type="private"))

    await messages_handlers.on_bot_removed_from_group(event)

    async with _patch_db() as session:
        assert await crud.get_group(session, 1) is not None


async def test_on_bot_removed_from_group_is_a_noop_for_unregistered_group(_patch_db) -> None:
    event = SimpleNamespace(chat=FakeChat(id=999, type="group"))

    await messages_handlers.on_bot_removed_from_group(event)

    async with _patch_db() as session:
        assert await crud.get_group(session, 999) is None


# --- /register ----------------------------------------------------------------


async def test_cmd_register_creates_new_group(_patch_db) -> None:
    message = FakeMessage(chat=FakeChat(id=100, type="group", title="Team Chat"), from_user=FakeUser(id=1))

    await messages_handlers.cmd_register(message)

    async with _patch_db() as session:
        group = await crud.get_group(session, 100)
        assert group is not None
        assert group.status == GroupStatus.ACTIVE
    assert "зареєстровано" in message.answers[0]


async def test_cmd_register_reactivates_existing_group(_patch_db) -> None:
    async with _patch_db() as session:
        group = await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=False)
        group.status = GroupStatus.PENDING_SETUP
        await session.commit()

    message = FakeMessage(chat=FakeChat(id=100, type="group"), from_user=FakeUser(id=1))

    await messages_handlers.cmd_register(message)

    async with _patch_db() as session:
        group = await crud.get_group(session, 100)
        assert group is not None
        assert group.status == GroupStatus.ACTIVE


# --- /tag -----------------------------------------------------------------


async def test_cmd_tag_requires_a_reply(_patch_db) -> None:
    message = FakeMessage(chat=FakeChat(id=100, type="group"), from_user=FakeUser(id=1), text="/tag Менеджер")

    await messages_handlers.cmd_tag(message)

    assert "у відповідь" in message.answers[0]


async def test_cmd_tag_requires_a_tag_value(_patch_db) -> None:
    target_message = FakeMessage(chat=FakeChat(id=100, type="group"), from_user=FakeUser(id=2))
    message = FakeMessage(
        chat=FakeChat(id=100, type="group"),
        from_user=FakeUser(id=1),
        text="/tag",
        reply_to_message=target_message,
    )

    await messages_handlers.cmd_tag(message)

    assert "Вкажи тег" in message.answers[0]


async def test_cmd_tag_tags_the_replied_to_user(_patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    # sync_tag_to_telegram (app/services/group_service.py) talks to the real
    # Telegram Bot API through the module-level `bot` singleton — stub it out
    # the same way _kick_via_assistant_bot is stubbed in test_members_routes.py.
    async def fake_sync_tag_to_telegram(chat_id: int, user_id: int, tag: str) -> None:
        return None

    monkeypatch.setattr(messages_handlers.group_service, "sync_tag_to_telegram", fake_sync_tag_to_telegram)

    target_message = FakeMessage(
        chat=FakeChat(id=100, type="group"), from_user=FakeUser(id=2, full_name="Bob B.")
    )
    message = FakeMessage(
        chat=FakeChat(id=100, type="group"),
        from_user=FakeUser(id=1),
        text="/tag Тімлід",
        reply_to_message=target_message,
    )

    await messages_handlers.cmd_tag(message)

    async with _patch_db() as session:
        members = await crud.get_group_members(session, 100)
        assert [(m.user_id, m.tag) for m in members] == [(2, "Тімлід")]
    assert "Bob B." in message.answers[0]
    assert "Тімлід" in message.answers[0]


# --- passive tracker --------------------------------------------------------


async def test_track_group_message_ignores_unregistered_group(_patch_db) -> None:
    message = FakeMessage(
        chat=FakeChat(id=100, type="group"), from_user=FakeUser(id=1), text="hello", date=datetime.utcnow()
    )

    await messages_handlers.track_group_message(message)

    async with _patch_db() as session:
        assert await crud.get_group(session, 100) is None


async def test_track_group_message_from_client_marks_awaiting_response(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 2, "client", "Client C.")
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=False)
        await crud.add_member_tag(session, 100, 2, "Клієнт")
        await session.commit()

    message = FakeMessage(
        chat=FakeChat(id=100, type="group"),
        from_user=FakeUser(id=2),
        text="hello",
        date=datetime.utcnow(),
    )

    await messages_handlers.track_group_message(message)

    async with _patch_db() as session:
        group = await crud.get_group(session, 100)
        assert group is not None
        assert group.awaiting_response is True
        assert group.last_message_from_id == 2


async def test_track_group_message_from_staff_clears_awaiting_response(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        group = await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=False)
        group.awaiting_response = True
        await crud.add_member_tag(session, 100, 1, "Менеджер")
        await session.commit()

    message = FakeMessage(
        chat=FakeChat(id=100, type="group"),
        from_user=FakeUser(id=1),
        text="hello",
        date=datetime.utcnow(),
    )

    await messages_handlers.track_group_message(message)

    async with _patch_db() as session:
        group = await crud.get_group(session, 100)
        assert group is not None
        assert group.awaiting_response is False
