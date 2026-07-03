import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import crud
from app.db.models import CLIENT_TAG, Role

pytestmark = pytest.mark.asyncio


async def test_get_or_create_user_creates_then_updates(db_session: AsyncSession) -> None:
    user = await crud.get_or_create_user(db_session, 1, "alice", "Alice A.")
    assert user.id == 1
    assert user.username == "alice"
    assert user.role is None

    # Calling again with a new username/full_name should update, not duplicate.
    updated = await crud.get_or_create_user(db_session, 1, "alice_new", "Alice A.")
    assert updated.id == 1
    assert updated.username == "alice_new"


async def test_set_user_role(db_session: AsyncSession) -> None:
    await crud.get_or_create_user(db_session, 1, "alice", "Alice")
    await crud.set_user_role(db_session, 1, Role.MANAGER)
    await db_session.flush()

    staff = await crud.get_staff_users(db_session)
    assert [u.id for u in staff] == [1]
    assert staff[0].role == Role.MANAGER


async def test_session_string_round_trip(db_session: AsyncSession) -> None:
    await crud.get_or_create_user(db_session, 1, "alice", "Alice")
    assert await crud.get_user_session(db_session, 1) is None

    await crud.set_user_session(db_session, 1, "encrypted-blob")
    assert await crud.get_user_session(db_session, 1) == "encrypted-blob"


async def test_group_membership_and_client_tagging(db_session: AsyncSession) -> None:
    await crud.create_group_record(db_session, group_id=100, title="Client X", created_by_userbot=True)
    await crud.get_or_create_user(db_session, 1, "manager", "Manager")
    await crud.get_or_create_user(db_session, 2, "client", "Client")

    await crud.add_member_tag(db_session, 100, 1, Role.MANAGER.value)
    await crud.add_member_tag(db_session, 100, 2, CLIENT_TAG)
    await db_session.flush()

    assert await crud.user_is_group_member(db_session, 100, 1) is True
    assert await crud.user_is_group_member(db_session, 100, 999) is False

    assert await crud.is_client(db_session, 100, 2) is True
    assert await crud.is_client(db_session, 100, 1) is False

    groups = await crud.get_groups_for_user(db_session, 1)
    assert [g.id for g in groups] == [100]


async def test_notify_recipients_are_scoped_to_group_and_role(db_session: AsyncSession) -> None:
    await crud.create_group_record(db_session, group_id=100, title="Group A", created_by_userbot=True)
    await crud.create_group_record(db_session, group_id=200, title="Group B", created_by_userbot=True)

    await crud.get_or_create_user(db_session, 1, "manager_in_a", None)
    await crud.set_user_role(db_session, 1, Role.MANAGER)
    await crud.get_or_create_user(db_session, 2, "seo_in_a", None)
    await crud.set_user_role(db_session, 2, Role.SEO)
    await crud.get_or_create_user(db_session, 3, "manager_in_b", None)
    await crud.set_user_role(db_session, 3, Role.MANAGER)

    # Manager (1) and SEO (2) are both members of group A; only manager 3 is in group B.
    await crud.add_member_tag(db_session, 100, 1, Role.MANAGER.value)
    await crud.add_member_tag(db_session, 100, 2, Role.SEO.value)
    await crud.add_member_tag(db_session, 200, 3, Role.MANAGER.value)
    await db_session.flush()

    recipients_a = await crud.get_notify_recipients(db_session, 100)
    # SEO must never receive reminders, and a manager who isn't in this group must not either.
    assert {u.id for u in recipients_a} == {1}

    recipients_b = await crud.get_notify_recipients(db_session, 200)
    assert {u.id for u in recipients_b} == {3}


async def test_awaiting_response_lifecycle(db_session: AsyncSession) -> None:
    from datetime import datetime

    await crud.create_group_record(db_session, group_id=100, title="Group A", created_by_userbot=True)
    assert (await crud.get_groups_awaiting_response(db_session)) == []

    await crud.mark_awaiting_response(db_session, 100, from_user_id=2, at=datetime.utcnow())
    await db_session.flush()
    awaiting = await crud.get_groups_awaiting_response(db_session)
    assert [g.id for g in awaiting] == [100]

    await crud.clear_awaiting_response(db_session, 100)
    await db_session.flush()
    assert (await crud.get_groups_awaiting_response(db_session)) == []
