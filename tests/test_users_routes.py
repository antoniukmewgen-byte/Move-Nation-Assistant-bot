"""Tests for the `/users/*` Mini App endpoints.

These call the route coroutines directly (bypassing the FastAPI/ASGI
transport layer, same spirit as `tests/test_crud.py`) against an in-memory
DB, with the `TelegramWebAppUser` that `get_verified_webapp_user` would
normally produce from a verified `initData` supplied directly — the
signature-verification itself is already covered by
`tests/test_telegram_auth.py`.
"""

from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes import users as users_routes
from app.api.schemas import RoleRequest
from app.db import crud
from app.db.models import CLIENT_TAG, Base, Role
from app.services import group_service
from app.services.telegram_auth import TelegramWebAppUser

pytestmark = pytest.mark.asyncio


class FakeBot:
    """Stubs `group_service.bot` — see `group_service.sync_tag_to_telegram`."""

    def __init__(self) -> None:
        self.tagged: list[tuple[int, int, str]] = []

    async def set_chat_member_tag(self, chat_id: int, user_id: int, tag: str, **_kwargs: Any) -> None:
        self.tagged.append((chat_id, user_id, tag))


@pytest.fixture(autouse=True)
async def _patch_db(monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(users_routes, "async_session", sessionmaker)
    yield sessionmaker
    await engine.dispose()


ALICE = TelegramWebAppUser(id=1, username="alice", full_name="Alice A.")


async def test_list_roles_returns_every_enum_member() -> None:
    roles = await users_routes.list_roles()
    assert {r.name for r in roles} == {r.name for r in Role}
    assert {r.value for r in roles} == {r.value for r in Role}


async def test_get_me_creates_a_fresh_user_with_no_role_and_not_connected() -> None:
    me = await users_routes.get_me(user=ALICE)
    assert me.id == 1
    assert me.username == "alice"
    assert me.role is None
    assert me.is_connected is False
    assert me.phone is None


async def test_get_me_reflects_a_phone_persisted_by_a_completed_connect_flow(_patch_db) -> None:
    # /users/me doesn't set the phone itself — telethon_auth._finish does,
    # once phone/code(/password) actually succeeds (see test_telethon_auth.py).
    # This only checks that get_me surfaces whatever crud already stored.
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.set_user_phone(session, 1, "+380000000")
        await session.commit()

    me = await users_routes.get_me(user=ALICE)
    assert me.phone == "+380000000"


async def test_set_role_persists_and_is_reflected_by_get_me() -> None:
    await users_routes.get_me(user=ALICE)

    updated = await users_routes.set_role(RoleRequest(role="MANAGER"), user=ALICE)
    assert updated.role == Role.MANAGER.value

    again = await users_routes.get_me(user=ALICE)
    assert again.role == Role.MANAGER.value


async def test_set_role_rejects_unknown_role_name() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await users_routes.set_role(RoleRequest(role="NOT_A_REAL_ROLE"), user=ALICE)
    assert exc_info.value.status_code == 400


async def test_set_role_works_even_without_a_prior_get_me_call() -> None:
    """The Mini App always calls /users/me first, but the endpoint shouldn't
    depend on request ordering — it must create the user row itself too."""
    updated = await users_routes.set_role(RoleRequest(role="SEO"), user=ALICE)
    assert updated.role == Role.SEO.value


async def test_set_role_updates_existing_group_tags_and_syncs_to_telegram(
    _patch_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Changing role in Settings must ripple out to every group where the
    # person was already tagged with the old role — both in our own DB and
    # as the native Telegram chat-member tag/badge.
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.create_group_record(session, 100, "Group One", created_by_userbot=True)
        await crud.create_group_record(session, 200, "Group Two", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 1, Role.MANAGER.value)
        await crud.add_member_tag(session, 200, 1, Role.MANAGER.value)
        await session.commit()

    fake_bot = FakeBot()
    monkeypatch.setattr(group_service, "bot", fake_bot)

    updated = await users_routes.set_role(RoleRequest(role="TEAMLEAD"), user=ALICE)
    assert updated.role == Role.TEAMLEAD.value

    async with _patch_db() as session:
        members_100 = await crud.get_group_members(session, 100)
        members_200 = await crud.get_group_members(session, 200)
        assert [m.tag for m in members_100] == [Role.TEAMLEAD.value]
        assert [m.tag for m in members_200] == [Role.TEAMLEAD.value]

    assert sorted(fake_bot.tagged) == [
        (100, 1, Role.TEAMLEAD.value),
        (200, 1, Role.TEAMLEAD.value),
    ]


async def test_set_role_does_not_touch_client_tags_in_other_groups(
    _patch_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _patch_db() as session:
        await crud.get_or_create_user(session, 1, "alice", "Alice A.")
        await crud.create_group_record(session, 100, "Group One", created_by_userbot=True)
        await crud.add_member_tag(session, 100, 1, CLIENT_TAG)
        await session.commit()

    fake_bot = FakeBot()
    monkeypatch.setattr(group_service, "bot", fake_bot)

    await users_routes.set_role(RoleRequest(role="MANAGER"), user=ALICE)

    async with _patch_db() as session:
        members = await crud.get_group_members(session, 100)
        assert [m.tag for m in members] == [CLIENT_TAG]

    assert fake_bot.tagged == []
