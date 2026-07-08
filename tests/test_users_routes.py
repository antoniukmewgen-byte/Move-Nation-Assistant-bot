"""Tests for the `/users/*` Mini App endpoints.

These call the route coroutines directly (bypassing the FastAPI/ASGI
transport layer, same spirit as `tests/test_crud.py`) against an in-memory
DB, with the `TelegramWebAppUser` that `get_verified_webapp_user` would
normally produce from a verified `initData` supplied directly — the
signature-verification itself is already covered by
`tests/test_telegram_auth.py`.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes import users as users_routes
from app.api.schemas import RoleRequest
from app.db.models import Base, Role
from app.services.telegram_auth import TelegramWebAppUser

pytestmark = pytest.mark.asyncio


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
