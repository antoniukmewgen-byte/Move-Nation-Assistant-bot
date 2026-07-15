"""Tests for group-chat handlers (app/bot/handlers/messages.py):
bot-added-to-group registration, `/sync`, and the passive awaiting-response
tracker on every non-command group message.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from telethon.errors import FloodWaitError

from app.bot.handlers import messages as messages_handlers
from app.db import crud
from app.db.models import Base, Role
from app.services import group_service
from tests.bot_fakes import FakeChat, FakeMessage, FakeUser

pytestmark = pytest.mark.asyncio


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.got_chat: list[int] = []

    async def send_message(self, chat_id: int, text: str, **_kwargs: Any) -> None:
        self.sent.append((chat_id, text))

    async def get_chat(self, chat_id: int, **_kwargs: Any) -> None:
        # on_bot_added_to_group's silent stale-chat_id check (see the
        # TelegramMigrateToChat/TelegramBadRequest handling around it) —
        # the bot no longer posts a welcome message when added to a group,
        # so this replaces send_message as the network call used to detect
        # a chat_id that's already migrated away by the time we process it.
        self.got_chat.append(chat_id)


class FakeGroupServiceBot:
    """Stubs `group_service.bot` for the blocked-in-private -> offboard_staff path.

    See `tests/test_users_routes.py::FakeBot` — same shape, duplicated here
    rather than shared since these tests live in a different module.
    """

    def __init__(self) -> None:
        self.banned: list[tuple[int, int]] = []
        self.unbanned: list[tuple[int, int]] = []

    async def ban_chat_member(self, chat_id: int, user_id: int, **_kwargs: Any) -> None:
        self.banned.append((chat_id, user_id))

    async def unban_chat_member(self, chat_id: int, user_id: int, **_kwargs: Any) -> None:
        self.unbanned.append((chat_id, user_id))


class FakeReminders:
    """Stubs `reminders.schedule_group_reminder`/`cancel_group_reminder`.

    `track_group_message` (app/bot/handlers/messages.py) now drives per-group
    APScheduler jobs through the real `app.services.reminders` module (which
    itself talks to the shared, un-started-in-tests `app.services.scheduler`
    singleton). Stubbing it here keeps these tests focused on the DB-state
    transition it's responsible for and lets us assert *what* it asked the
    scheduler to do without depending on APScheduler internals.
    """

    def __init__(self) -> None:
        self.scheduled: list[tuple[int, datetime]] = []
        self.cancelled: list[int] = []

    def schedule_group_reminder(self, group_id: int, run_at: datetime) -> None:
        self.scheduled.append((group_id, run_at))

    def cancel_group_reminder(self, group_id: int) -> None:
        self.cancelled.append(group_id)


@pytest.fixture(autouse=True)
async def _patch_db(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(messages_handlers, "async_session", sessionmaker)
    # on_bot_removed_from_group delegates the "blocked in private" case to
    # group_service.offboard_staff, which owns its own async_session
    # reference — must be patched too, or it would fall through to the real
    # (file-based) database.
    monkeypatch.setattr(group_service, "async_session", sessionmaker)
    yield sessionmaker
    await engine.dispose()


@pytest.fixture(autouse=True)
def _patch_reminders(monkeypatch: pytest.MonkeyPatch) -> FakeReminders:
    fake_reminders = FakeReminders()
    monkeypatch.setattr(messages_handlers, "reminders", fake_reminders)
    return fake_reminders


# --- on_bot_added_to_group ---------------------------------------------------


async def test_on_bot_added_to_group_ignores_private_chats(_patch_db) -> None:
    bot = FakeBot()
    event = SimpleNamespace(chat=FakeChat(id=1, type="private"), bot=bot)

    await messages_handlers.on_bot_added_to_group(event)

    assert bot.sent == []
    async with _patch_db() as session:
        assert await crud.get_group(session, 1) is None


async def test_on_bot_added_to_group_registers_new_group_silently(_patch_db) -> None:
    bot = FakeBot()
    event = SimpleNamespace(
        chat=FakeChat(id=100, type="group", title="Team Chat"),
        bot=bot,
        from_user=FakeUser(id=1, username="alice", full_name="Alice A."),
    )

    await messages_handlers.on_bot_added_to_group(event)

    async with _patch_db() as session:
        group = await crud.get_group(session, 100)
        assert group is not None
        assert group.title == "Team Chat"
        # The actor has no Role in the DB (not a known staff member), so
        # they must NOT get an auto-membership row — group_members is the
        # only authorization check on /groups and /members (see
        # app/api/deps.py::get_verified_user_id, which never looks at
        # Role), so auto-registering an arbitrary Telegram user here would
        # let anyone self-grant full Mini App control over any chat just by
        # adding the bot to it. The group stays registered but invisible/
        # uncontrollable until a real staff member syncs it.
        members = await crud.get_group_members(session, 100)
        assert members == []

    # No message posted to the group — just the silent existence check.
    assert bot.sent == []
    assert bot.got_chat == [100]


async def test_on_bot_added_to_group_registers_actor_with_an_existing_role(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await session.commit()

    bot = FakeBot()
    event = SimpleNamespace(
        chat=FakeChat(id=100, type="group", title="Team Chat"),
        bot=bot,
        from_user=FakeUser(id=1, username="alice", full_name="Alice A."),
    )

    await messages_handlers.on_bot_added_to_group(event)

    async with _patch_db() as session:
        members = await crud.get_group_members(session, 100)
        assert [(m.user_id, m.tag) for m in members] == [(1, Role.MANAGER.value)]


async def test_on_bot_added_to_group_skips_actors_pending_our_own_group_creation(
    _patch_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Both the pre- and post-migration "bot added" events for a group we're
    # creating ourselves (app/services/group_service.py::create_group) look
    # like a genuine external join to aiogram's JOIN_TRANSITION filter, and
    # both carry the acting staff member's user_id as `from_user` (it's their
    # own Telethon session performing every step) — this is the registry-based
    # fix for that duplicate-welcome-message bug.
    monkeypatch.setattr(messages_handlers, "is_pending", lambda actor_user_id: True)

    bot = FakeBot()
    event = SimpleNamespace(
        chat=FakeChat(id=100, type="supergroup", title="Team Chat"),
        bot=bot,
        from_user=FakeUser(id=42, username="staffer", full_name="Staff Member"),
    )

    await messages_handlers.on_bot_added_to_group(event)

    assert bot.sent == []
    async with _patch_db() as session:
        assert await crud.get_group(session, 100) is None


async def test_on_bot_added_to_group_is_a_noop_for_already_registered_group(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=False)
        await session.commit()

    bot = FakeBot()
    event = SimpleNamespace(
        chat=FakeChat(id=100, type="group", title="Team Chat"), bot=bot, from_user=SimpleNamespace(id=1)
    )

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


async def test_on_bot_removed_from_group_offboards_a_staff_member_who_blocked_the_bot(
    _patch_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Private chat_id == the user's own id (Bot API) — blocking the bot in
    # personal messages is a deliberate action (unlike leaving a group, which
    # can happen by accident), so it's treated as self-offboarding: kicked
    # from every group they're currently tagged in, and their `users` row
    # deleted entirely — same end state as a Керівник calling
    # POST /users/{id}/offboard (app/api/routes/users.py).
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 2, "bob", "Bob B.")
        await crud.set_user_role(session, 2, Role.TEAMLEAD)
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 2, Role.TEAMLEAD.value)
        await session.commit()

    fake_bot = FakeGroupServiceBot()
    monkeypatch.setattr(group_service, "bot", fake_bot)

    event = SimpleNamespace(chat=FakeChat(id=2, type="private"))

    await messages_handlers.on_bot_removed_from_group(event)

    assert fake_bot.banned == [(100, 2)]
    async with _patch_db() as session:
        assert await crud.get_group_members(session, 100) == []
        from app.db.models import User

        assert await session.get(User, 2) is None


async def test_on_bot_removed_from_group_is_a_noop_when_a_non_staff_user_blocks_the_bot(
    _patch_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Anyone can block the bot in personal messages — a client, a stranger who
    # never registered — not just staff. Must not raise or affect anything.
    fake_bot = FakeGroupServiceBot()
    monkeypatch.setattr(group_service, "bot", fake_bot)

    event = SimpleNamespace(chat=FakeChat(id=999, type="private"))

    await messages_handlers.on_bot_removed_from_group(event)

    assert fake_bot.banned == []


async def test_on_bot_removed_from_group_is_a_noop_for_unregistered_group(_patch_db) -> None:
    event = SimpleNamespace(chat=FakeChat(id=999, type="group"))

    await messages_handlers.on_bot_removed_from_group(event)

    async with _patch_db() as session:
        assert await crud.get_group(session, 999) is None


# --- on_member_left_group ----------------------------------------------------


async def test_on_member_left_group_removes_the_member(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=False)
        await crud.add_member_tag(session, 100, 2, "Клієнт")
        await session.commit()

    event = SimpleNamespace(
        chat=FakeChat(id=100, type="supergroup"),
        old_chat_member=SimpleNamespace(user=SimpleNamespace(id=2)),
    )

    await messages_handlers.on_member_left_group(event)

    async with _patch_db() as session:
        assert await crud.get_group_members(session, 100) == []


async def test_on_member_left_group_ignores_private_chats(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.create_group_record(session, 1, "Not a group", created_by_userbot=False)
        await crud.add_member_tag(session, 1, 2, "Клієнт")
        await session.commit()

    event = SimpleNamespace(
        chat=FakeChat(id=1, type="private"),
        old_chat_member=SimpleNamespace(user=SimpleNamespace(id=2)),
    )

    await messages_handlers.on_member_left_group(event)

    async with _patch_db() as session:
        members = await crud.get_group_members(session, 1)
        assert [m.user_id for m in members] == [2]


async def test_on_member_left_group_is_a_noop_for_an_untracked_member(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=False)
        await session.commit()

    event = SimpleNamespace(
        chat=FakeChat(id=100, type="supergroup"),
        old_chat_member=SimpleNamespace(user=SimpleNamespace(id=999)),
    )

    await messages_handlers.on_member_left_group(event)

    async with _patch_db() as session:
        assert await crud.get_group_members(session, 100) == []


# --- /sync -----------------------------------------------------------------


async def test_cmd_sync_rejects_a_user_with_no_role(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await session.commit()

    message = FakeMessage(chat=FakeChat(id=100, type="group"), from_user=FakeUser(id=1))

    await messages_handlers.cmd_sync(message)

    assert "лише співробітник" in message.answers[-1]


async def test_cmd_sync_rejects_an_unknown_user(_patch_db) -> None:
    message = FakeMessage(chat=FakeChat(id=100, type="group"), from_user=FakeUser(id=999))

    await messages_handlers.cmd_sync(message)

    assert "лише співробітник" in message.answers[-1]


async def test_cmd_sync_reports_not_connected(_patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await session.commit()

    async def fake_sync_group(*_args, **_kwargs):
        raise group_service.NotConnectedError()

    monkeypatch.setattr(messages_handlers.group_service, "sync_group", fake_sync_group)

    message = FakeMessage(chat=FakeChat(id=100, type="group", title="Team Chat"), from_user=FakeUser(id=1))

    await messages_handlers.cmd_sync(message)

    assert "/connect" in message.answers[-1]


async def test_cmd_sync_reports_flood_wait(_patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await session.commit()

    async def fake_sync_group(*_args, **_kwargs):
        raise FloodWaitError(request=None, capture=20)

    monkeypatch.setattr(messages_handlers.group_service, "sync_group", fake_sync_group)

    message = FakeMessage(chat=FakeChat(id=100, type="group", title="Team Chat"), from_user=FakeUser(id=1))

    await messages_handlers.cmd_sync(message)

    assert "Забагато запитів" in message.answers[-1]


async def test_cmd_sync_reports_generic_failure(_patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await session.commit()

    async def fake_sync_group(*_args, **_kwargs):
        raise group_service.GroupSyncFailedError()

    monkeypatch.setattr(messages_handlers.group_service, "sync_group", fake_sync_group)

    message = FakeMessage(chat=FakeChat(id=100, type="group", title="Team Chat"), from_user=FakeUser(id=1))

    await messages_handlers.cmd_sync(message)

    assert "Не вдалося просканувати" in message.answers[-1]


async def test_cmd_sync_reports_no_changes(_patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await session.commit()

    async def fake_sync_group(*_args, **_kwargs):
        return 0, 0

    monkeypatch.setattr(messages_handlers.group_service, "sync_group", fake_sync_group)

    message = FakeMessage(chat=FakeChat(id=100, type="group", title="Team Chat"), from_user=FakeUser(id=1))

    await messages_handlers.cmd_sync(message)

    assert "актуально" in message.answers[-1]


async def test_cmd_sync_reports_updated_and_removed_counts(_patch_db, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await session.commit()

    captured: dict[str, Any] = {}

    async def fake_sync_group(actor_user_id: int, group_id: int, title: str):
        captured["args"] = (actor_user_id, group_id, title)
        return 3, 2

    monkeypatch.setattr(messages_handlers.group_service, "sync_group", fake_sync_group)

    message = FakeMessage(chat=FakeChat(id=100, type="group", title="Team Chat"), from_user=FakeUser(id=1))

    await messages_handlers.cmd_sync(message)

    assert captured["args"] == (1, 100, "Team Chat")
    last_answer = message.answers[-1]
    assert "оновлено тегів: 3" in last_answer
    assert "прибрано з групи: 2" in last_answer


# --- passive tracker --------------------------------------------------------


async def test_track_group_message_ignores_unregistered_group(_patch_db, _patch_reminders) -> None:
    message = FakeMessage(
        chat=FakeChat(id=100, type="group"), from_user=FakeUser(id=1), text="hello", date=datetime.utcnow()
    )

    await messages_handlers.track_group_message(message)

    async with _patch_db() as session:
        assert await crud.get_group(session, 100) is None
    assert _patch_reminders.scheduled == []
    assert _patch_reminders.cancelled == []


async def test_track_group_message_from_client_marks_awaiting_response(_patch_db, _patch_reminders) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 2, "client", "Client C.")
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=False)
        await crud.add_member_tag(session, 100, 2, "Клієнт")
        await session.commit()

    message_date = datetime.utcnow()
    message = FakeMessage(
        chat=FakeChat(id=100, type="group"),
        from_user=FakeUser(id=2),
        text="hello",
        date=message_date,
    )

    await messages_handlers.track_group_message(message)

    async with _patch_db() as session:
        group = await crud.get_group(session, 100)
        assert group is not None
        assert group.awaiting_response is True
        assert group.last_message_from_id == 2

    # Reminder must be tied to *this* message's own timestamp (message_date +
    # interval), not to whenever a scheduler tick happens to land — that's
    # the whole point of the per-group scheduling this replaced.
    from app.config import settings

    assert _patch_reminders.scheduled == [
        (100, message_date + timedelta(minutes=settings.reminder_interval_minutes))
    ]
    assert _patch_reminders.cancelled == []


async def test_track_group_message_from_staff_clears_awaiting_response(_patch_db, _patch_reminders) -> None:
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
    assert _patch_reminders.cancelled == [100]
    assert _patch_reminders.scheduled == []
