"""Tests for the WebSocket connection registry (app/services/realtime.py).

These exercise the registry in isolation — no FastAPI WebSocket, no route —
using a minimal fake that only implements the one method `notify_user` calls
(`send_json`), the same style as `FakeBot` in tests/test_group_service_sync.py
for the Bot API surface.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import crud
from app.db.models import Base
from app.services import realtime

pytestmark = pytest.mark.asyncio


class FakeWebSocket:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail = fail

    async def send_json(self, data: dict) -> None:
        if self.fail:
            raise RuntimeError("connection is closed")
        self.sent.append(data)


@pytest.fixture(autouse=True)
def _clear_registry():
    # `_connections` is process-wide module state (see realtime.py's module
    # docstring on the single-instance assumption) — tests must not leak
    # sockets registered by one test into the next.
    realtime._connections.clear()
    yield
    realtime._connections.clear()


@pytest.fixture(autouse=True)
async def _patch_db(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(realtime, "async_session", sessionmaker)
    yield sessionmaker
    await engine.dispose()


async def test_notify_user_sends_to_every_registered_socket_for_that_user() -> None:
    ws1, ws2 = FakeWebSocket(), FakeWebSocket()
    realtime.register(1, ws1)
    realtime.register(1, ws2)

    await realtime.notify_user(1, {"type": "groups_changed"})

    assert ws1.sent == [{"type": "groups_changed"}]
    assert ws2.sent == [{"type": "groups_changed"}]


async def test_notify_user_only_reaches_that_users_own_sockets() -> None:
    ws1, ws2 = FakeWebSocket(), FakeWebSocket()
    realtime.register(1, ws1)
    realtime.register(2, ws2)

    await realtime.notify_user(1, {"type": "groups_changed"})

    assert ws1.sent == [{"type": "groups_changed"}]
    assert ws2.sent == []


async def test_notify_user_is_a_noop_for_a_user_with_no_open_socket() -> None:
    # Must not raise — a user with the Mini App closed is the common case,
    # not an error condition.
    await realtime.notify_user(999, {"type": "groups_changed"})


async def test_notify_user_unregisters_a_socket_that_fails_to_send() -> None:
    dead = FakeWebSocket(fail=True)
    alive = FakeWebSocket()
    realtime.register(1, dead)
    realtime.register(1, alive)

    await realtime.notify_user(1, {"type": "groups_changed"})

    assert alive.sent == [{"type": "groups_changed"}]
    assert dead not in realtime._connections.get(1, set())

    # A second push shouldn't try (and fail) to send to the dead socket again.
    await realtime.notify_user(1, {"type": "groups_changed"})
    assert alive.sent == [{"type": "groups_changed"}, {"type": "groups_changed"}]


async def test_unregister_removes_only_the_given_socket() -> None:
    ws1, ws2 = FakeWebSocket(), FakeWebSocket()
    realtime.register(1, ws1)
    realtime.register(1, ws2)

    realtime.unregister(1, ws1)

    await realtime.notify_user(1, {"type": "groups_changed"})
    assert ws1.sent == []
    assert ws2.sent == [{"type": "groups_changed"}]


async def test_unregister_the_last_socket_drops_the_user_entry_entirely() -> None:
    ws = FakeWebSocket()
    realtime.register(1, ws)

    realtime.unregister(1, ws)

    assert 1 not in realtime._connections


async def test_unregister_a_socket_that_was_never_registered_is_a_noop() -> None:
    realtime.unregister(1, FakeWebSocket())


async def test_notify_users_notifies_every_listed_user() -> None:
    ws1, ws2, ws3 = FakeWebSocket(), FakeWebSocket(), FakeWebSocket()
    realtime.register(1, ws1)
    realtime.register(2, ws2)
    realtime.register(3, ws3)

    await realtime.notify_users([1, 3], {"type": "profile_changed"})

    assert ws1.sent == [{"type": "profile_changed"}]
    assert ws2.sent == []
    assert ws3.sent == [{"type": "profile_changed"}]


async def test_notify_group_resolves_current_members_and_notifies_each(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.get_or_create_user(session, 2, "bob", "Bob B.")
        await crud.create_group_record(session, 100, "Team Chat", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 1, "Менеджер")
        await crud.add_member_tag(session, 100, 2, "Клієнт")
        await session.commit()

    ws1, ws2 = FakeWebSocket(), FakeWebSocket()
    realtime.register(1, ws1)
    realtime.register(2, ws2)

    await realtime.notify_group(100, {"type": "members_changed", "group_id": 100})

    assert ws1.sent == [{"type": "members_changed", "group_id": 100}]
    assert ws2.sent == [{"type": "members_changed", "group_id": 100}]


async def test_notify_group_with_no_members_sends_nothing(_patch_db) -> None:
    async with _patch_db() as session:
        await crud.create_group_record(session, 100, "Empty Group", created_by_userbot=True)
        await session.commit()

    # Must not raise — an empty member list is just an empty fan-out.
    await realtime.notify_group(100, {"type": "members_changed", "group_id": 100})
