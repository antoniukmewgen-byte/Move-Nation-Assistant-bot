"""Verifies every mutation site pushes the right realtime invalidation event.

Doesn't re-test the underlying business logic (already covered by
test_group_service_sync.py, test_groups_routes.py, test_members_routes.py,
test_users_routes.py, test_telethon_auth.py, test_bot_messages.py) — only
that each hook added on top of it calls `app.services.realtime.notify_user`/
`notify_users`/`notify_group` with the expected recipients and event.

`realtime.notify_user` etc. are monkeypatched directly on the `app.services.
realtime` module object: every hook site does `from app.services import
realtime` and then calls `realtime.notify_user(...)` at call time (an
attribute lookup on the module), so patching the module's own attributes
here reaches all of them regardless of which module does the calling —
same trick `test_groups_routes.py` uses for `async_session`.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from telethon.sessions import StringSession

from app.api.routes import groups as groups_routes
from app.api.routes import members as members_routes
from app.api.routes import users as users_routes
from app.api.schemas import RemoveMemberRequest, RoleRequest, TagRequest
from app.bot.handlers import messages as messages_handlers
from app.db import crud
from app.db.models import CLIENT_TAG, Base, Role
from app.services import group_service, realtime, telethon_auth
from tests.bot_fakes import FakeChat, FakeMessage, FakeUser

pytestmark = pytest.mark.asyncio


class _NotifyRecorder:
    def __init__(self) -> None:
        self.notify_user_calls: list[tuple[int, dict]] = []
        self.notify_users_calls: list[tuple[set[int], dict]] = []
        self.notify_group_calls: list[tuple[int, dict]] = []

    async def notify_user(self, user_id: int, event: dict) -> None:
        self.notify_user_calls.append((user_id, event))

    async def notify_users(self, user_ids, event: dict) -> None:
        self.notify_users_calls.append((set(user_ids), event))

    async def notify_group(self, group_id: int, event: dict) -> None:
        self.notify_group_calls.append((group_id, event))


@pytest.fixture(autouse=True)
def _patch_realtime(monkeypatch: pytest.MonkeyPatch) -> _NotifyRecorder:
    recorder = _NotifyRecorder()
    monkeypatch.setattr(realtime, "notify_user", recorder.notify_user)
    monkeypatch.setattr(realtime, "notify_users", recorder.notify_users)
    monkeypatch.setattr(realtime, "notify_group", recorder.notify_group)
    return recorder


@pytest.fixture(autouse=True)
async def _patch_db(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    for module in (
        group_service,
        groups_routes,
        members_routes,
        users_routes,
        messages_handlers,
        telethon_auth,
    ):
        monkeypatch.setattr(module, "async_session", sessionmaker)
    yield sessionmaker
    await engine.dispose()


# --- group_service.py --------------------------------------------------------


async def test_create_group_notifies_every_tagged_staff_member(
    _patch_db, monkeypatch: pytest.MonkeyPatch, _patch_realtime: _NotifyRecorder
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_create_group_with_team(*_args):
        return 555

    monkeypatch.setattr(group_service, "create_group_with_team", fake_create_group_with_team)

    async def fake_sync_tag_to_telegram(*_args):
        return None

    monkeypatch.setattr(group_service, "sync_tag_to_telegram", fake_sync_tag_to_telegram)

    async def fake_welcome(*_args):
        return None

    monkeypatch.setattr(group_service, "_send_group_welcome_message", fake_welcome)

    await group_service.create_group(1, "New Group")

    assert _patch_realtime.notify_users_calls == [({1}, {"type": "groups_changed"})]


async def test_add_client_notifies_client_and_group(
    _patch_db, monkeypatch: pytest.MonkeyPatch, _patch_realtime: _NotifyRecorder
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 1, Role.MANAGER.value)
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await session.commit()

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_add_client_to_group(*_args):
        return 42, "New Client", None

    monkeypatch.setattr(group_service, "add_client_to_group", fake_add_client_to_group)

    async def fake_sync_tag_to_telegram(*_args):
        return None

    monkeypatch.setattr(group_service, "sync_tag_to_telegram", fake_sync_tag_to_telegram)

    await group_service.add_client(1, 100, "@newclient")

    assert _patch_realtime.notify_user_calls == [(42, {"type": "groups_changed"})]
    assert _patch_realtime.notify_group_calls == [(100, {"type": "members_changed", "group_id": 100})]


async def test_offboard_staff_notifies_every_group_they_were_removed_from(
    _patch_db, monkeypatch: pytest.MonkeyPatch, _patch_realtime: _NotifyRecorder
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 2, "bob", "Bob B.")
        await crud.set_user_role(session, 2, Role.TEAMLEAD)
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=True)
        await crud.create_group_record(session, 200, "Other Chat", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 2, Role.TEAMLEAD.value)
        await crud.add_member_tag(session, 200, 2, Role.TEAMLEAD.value)
        await session.commit()

    class FakeBot:
        async def ban_chat_member(self, *_args, **_kwargs):
            return None

        async def unban_chat_member(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(group_service, "bot", FakeBot())

    await group_service.offboard_staff(2)

    assert sorted(_patch_realtime.notify_group_calls) == [
        (100, {"type": "members_changed", "group_id": 100}),
        (200, {"type": "members_changed", "group_id": 200}),
    ]


async def test_sync_group_notifies_affected_users_and_the_group(
    _patch_db, monkeypatch: pytest.MonkeyPatch, _patch_realtime: _NotifyRecorder
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 1, Role.MANAGER.value)
        await crud.get_or_create_user(session, 2, "gone", "Gone Guy")
        await crud.add_member_tag(session, 100, 2, CLIENT_TAG)
        await session.commit()

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_scan(*_args):
        return [(1, "alice", "Alice A.", False)]

    monkeypatch.setattr(group_service, "scan_group_members", fake_scan)

    async def fake_sync_tag_to_telegram(*_args):
        return None

    monkeypatch.setattr(group_service, "sync_tag_to_telegram", fake_sync_tag_to_telegram)

    await group_service.sync_group(1, 100, "Team Chat")

    assert _patch_realtime.notify_users_calls == [({2}, {"type": "groups_changed"})]
    assert _patch_realtime.notify_group_calls == [(100, {"type": "members_changed", "group_id": 100})]


async def test_sync_group_with_no_changes_sends_no_notifications(
    _patch_db, monkeypatch: pytest.MonkeyPatch, _patch_realtime: _NotifyRecorder
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await crud.set_user_session(session, 1, "encrypted-session-string")
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 1, Role.MANAGER.value)
        await session.commit()

    monkeypatch.setattr(group_service, "decrypt_session", lambda s: s)

    async def fake_scan(*_args):
        return [(1, "alice", "Alice A.", False)]

    monkeypatch.setattr(group_service, "scan_group_members", fake_scan)

    await group_service.sync_group(1, 100, "Team Chat")

    assert _patch_realtime.notify_users_calls == []
    assert _patch_realtime.notify_group_calls == []


# --- API routes ---------------------------------------------------------------


async def test_remove_group_route_notifies_former_members(
    _patch_db, monkeypatch: pytest.MonkeyPatch, _patch_realtime: _NotifyRecorder
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.get_or_create_user(session, 2, "bob", "Bob B.")
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 1, Role.MANAGER.value)
        await crud.add_member_tag(session, 100, 2, CLIENT_TAG)
        await session.commit()

    await groups_routes.remove_group(100, requested_by=1)

    assert _patch_realtime.notify_users_calls == [({1, 2}, {"type": "groups_changed"})]


async def test_tag_member_route_notifies_the_group(
    _patch_db, _patch_realtime: _NotifyRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 1, Role.MANAGER.value)
        await session.commit()

    async def fake_sync_tag_to_telegram(*_args):
        return None

    monkeypatch.setattr(group_service, "sync_tag_to_telegram", fake_sync_tag_to_telegram)

    await members_routes.tag_member(TagRequest(group_id=100, user_id=1, tag="Тімлід"), user_id=1)

    assert _patch_realtime.notify_group_calls == [(100, {"type": "members_changed", "group_id": 100})]


async def test_remove_member_route_notifies_removed_user_and_the_group(
    _patch_db, _patch_realtime: _NotifyRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 1, Role.MANAGER.value)
        await crud.get_or_create_user(session, 2, "client", "Client C.")
        await crud.add_member_tag(session, 100, 2, CLIENT_TAG)
        await session.commit()

    async def fake_kick_via_assistant_bot(*_args):
        return True

    monkeypatch.setattr(members_routes, "_kick_via_assistant_bot", fake_kick_via_assistant_bot)

    await members_routes.remove_member(RemoveMemberRequest(group_id=100, user_id=2), requested_by=1)

    assert _patch_realtime.notify_user_calls == [(2, {"type": "groups_changed"})]
    assert _patch_realtime.notify_group_calls == [(100, {"type": "members_changed", "group_id": 100})]


async def test_set_role_route_notifies_own_profile_and_every_updated_group(
    _patch_db, _patch_realtime: _NotifyRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 1, Role.MANAGER.value)
        await session.commit()

    async def fake_sync_tag_to_telegram(*_args):
        return None

    monkeypatch.setattr(group_service, "sync_tag_to_telegram", fake_sync_tag_to_telegram)

    user = SimpleNamespace(id=1, username="alice", full_name="Alice A.")
    await users_routes.set_role(RoleRequest(role="TEAMLEAD"), user=user)

    assert _patch_realtime.notify_user_calls == [(1, {"type": "profile_changed"})]
    assert _patch_realtime.notify_group_calls == [(100, {"type": "members_changed", "group_id": 100})]


# --- telethon_auth.py ----------------------------------------------------------


async def test_finish_notifies_profile_changed(
    _patch_db, _patch_realtime: _NotifyRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await session.commit()

    monkeypatch.setattr(telethon_auth, "encrypt_session", lambda s: "encrypted-" + s)

    class FakeClient:
        session = StringSession()

        async def disconnect(self) -> None:
            return None

    result = await telethon_auth._finish(1, FakeClient())

    assert result.status == "connected"
    assert _patch_realtime.notify_user_calls == [(1, {"type": "profile_changed"})]


# --- bot handlers (app/bot/handlers/messages.py) -------------------------------


class _FakeBotAddedBot:
    """Minimal `event.bot` stub for on_bot_added_to_group's silent get_chat check."""

    async def get_chat(self, *_args, **_kwargs) -> None:
        return None


async def test_on_bot_added_to_group_notifies_the_staff_actor(
    _patch_db, _patch_realtime: _NotifyRecorder
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_role(session, 1, Role.MANAGER)
        await session.commit()

    event = SimpleNamespace(
        chat=FakeChat(id=100, type="supergroup", title="Team Chat"),
        bot=_FakeBotAddedBot(),
        from_user=FakeUser(id=1, username="alice", full_name="Alice A."),
    )

    await messages_handlers.on_bot_added_to_group(event)

    assert _patch_realtime.notify_users_calls == [({1}, {"type": "groups_changed"})]


async def test_on_bot_added_to_group_sends_no_notification_for_a_non_staff_actor(
    _patch_db, _patch_realtime: _NotifyRecorder
) -> None:
    # No Role assigned to user 1 — auto-registering (and thus notifying)
    # them would let an arbitrary Telegram user self-grant Mini App access
    # to any chat just by adding the bot to it (see the gate this covers in
    # on_bot_added_to_group).
    event = SimpleNamespace(
        chat=FakeChat(id=100, type="supergroup", title="Team Chat"),
        bot=_FakeBotAddedBot(),
        from_user=FakeUser(id=1, username="alice", full_name="Alice A."),
    )

    await messages_handlers.on_bot_added_to_group(event)

    assert _patch_realtime.notify_users_calls == []


async def test_on_bot_removed_from_group_notifies_former_members(
    _patch_db, _patch_realtime: _NotifyRecorder
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        group = await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=False)
        await crud.add_member_tag(session, group.id, 1, "Менеджер")
        await session.commit()

    event = SimpleNamespace(chat=FakeChat(id=100, type="supergroup"))

    await messages_handlers.on_bot_removed_from_group(event)

    assert _patch_realtime.notify_users_calls == [({1}, {"type": "groups_changed"})]


async def test_on_member_joined_group_notifies_the_group(
    _patch_db, _patch_realtime: _NotifyRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 2, "client", "Client C.")
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=False)
        await crud.add_member_tag(session, 100, 2, CLIENT_TAG, pending=True)
        await session.commit()

    async def fake_sync_tag_to_telegram(*_args):
        return None

    monkeypatch.setattr(group_service, "sync_tag_to_telegram", fake_sync_tag_to_telegram)

    event = SimpleNamespace(
        chat=FakeChat(id=100, type="supergroup"),
        new_chat_member=SimpleNamespace(user=SimpleNamespace(id=2)),
    )

    await messages_handlers.on_member_joined_group(event)

    assert _patch_realtime.notify_group_calls == [(100, {"type": "members_changed", "group_id": 100})]


async def test_on_member_joined_group_without_pending_record_tags_and_notifies(
    _patch_db, _patch_realtime: _NotifyRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No add_member_tag(..., pending=True) call for user_id=2 here — this
    # covers someone who joined via a link grabbed straight from the group
    # in Telegram, bypassing /add_client entirely (clear_pending finds
    # nothing, so tag_new_member's fallback path is what must tag them).
    async with _patch_db() as session:
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=False)
        await session.commit()

    async def fake_sync_tag_to_telegram(*_args):
        return None

    monkeypatch.setattr(group_service, "sync_tag_to_telegram", fake_sync_tag_to_telegram)

    event = SimpleNamespace(
        chat=FakeChat(id=100, type="supergroup"),
        new_chat_member=SimpleNamespace(user=SimpleNamespace(id=2, username="newclient", full_name="New Client")),
    )

    await messages_handlers.on_member_joined_group(event)

    assert _patch_realtime.notify_group_calls == [(100, {"type": "members_changed", "group_id": 100})]

    async with _patch_db() as session:
        members = await crud.get_group_members(session, 100)
        assert [(m.user_id, m.tag) for m in members] == [(2, CLIENT_TAG)]


async def test_on_member_joined_group_without_pending_record_tags_staff_with_their_role(
    _patch_db, _patch_realtime: _NotifyRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same as above, but the joiner is already a staff member in our DB
    # (e.g. someone invited straight into an existing group by another
    # employee) — they should get their role, not CLIENT_TAG.
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 2, "bob", "Bob B.")
        await crud.set_user_role(session, 2, Role.MANAGER)
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=False)
        await session.commit()

    async def fake_sync_tag_to_telegram(*_args):
        return None

    monkeypatch.setattr(group_service, "sync_tag_to_telegram", fake_sync_tag_to_telegram)

    event = SimpleNamespace(
        chat=FakeChat(id=100, type="supergroup"),
        new_chat_member=SimpleNamespace(user=SimpleNamespace(id=2, username="bob", full_name="Bob B.")),
    )

    await messages_handlers.on_member_joined_group(event)

    async with _patch_db() as session:
        members = await crud.get_group_members(session, 100)
        assert [(m.user_id, m.tag) for m in members] == [(2, Role.MANAGER.value)]


async def test_on_member_left_group_notifies_the_removed_user_and_the_group(
    _patch_db, _patch_realtime: _NotifyRecorder
) -> None:
    async with _patch_db() as session:
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=False)
        await crud.add_member_tag(session, 100, 2, "Клієнт")
        await session.commit()

    event = SimpleNamespace(
        chat=FakeChat(id=100, type="supergroup"),
        old_chat_member=SimpleNamespace(user=SimpleNamespace(id=2)),
    )

    await messages_handlers.on_member_left_group(event)

    assert _patch_realtime.notify_user_calls == [(2, {"type": "groups_changed"})]
    assert _patch_realtime.notify_group_calls == [(100, {"type": "members_changed", "group_id": 100})]


async def test_track_group_message_notifies_the_group_with_groups_changed(
    _patch_db, _patch_realtime: _NotifyRecorder
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 2, "client", "Client C.")
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=False)
        await crud.add_member_tag(session, 100, 2, "Клієнт")
        await session.commit()

    message = FakeMessage(chat=FakeChat(id=100, type="group"), from_user=FakeUser(id=2), text="hello")

    await messages_handlers.track_group_message(message)

    assert _patch_realtime.notify_group_calls == [(100, {"type": "groups_changed"})]
