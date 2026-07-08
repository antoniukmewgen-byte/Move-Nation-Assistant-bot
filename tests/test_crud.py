import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import crud
from app.db.models import CLIENT_TAG, Role, User

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


async def test_sync_role_to_group_tags_updates_existing_staff_tags_and_reports_the_groups(
    db_session: AsyncSession,
) -> None:
    await crud.create_group_record(db_session, group_id=100, title="Group A", created_by_userbot=True)
    await crud.create_group_record(db_session, group_id=200, title="Group B", created_by_userbot=True)
    await crud.get_or_create_user(db_session, 1, "bob", "Bob B.")
    await crud.add_member_tag(db_session, 100, 1, Role.MANAGER.value)
    await crud.add_member_tag(db_session, 200, 1, Role.MANAGER.value)
    await db_session.flush()

    updated_group_ids = await crud.sync_role_to_group_tags(db_session, 1, Role.TEAMLEAD)

    assert sorted(updated_group_ids) == [100, 200]
    members_100 = await crud.get_group_members(db_session, 100)
    members_200 = await crud.get_group_members(db_session, 200)
    assert [m.tag for m in members_100] == [Role.TEAMLEAD.value]
    assert [m.tag for m in members_200] == [Role.TEAMLEAD.value]


async def test_sync_role_to_group_tags_never_touches_client_tags(db_session: AsyncSession) -> None:
    # A staff member could in principle also be tagged CLIENT_TAG in some
    # unrelated group (a separate, independent membership) — changing their
    # own staff role must never overwrite that.
    await crud.create_group_record(db_session, group_id=100, title="Group A", created_by_userbot=True)
    await crud.get_or_create_user(db_session, 1, "bob", "Bob B.")
    await crud.add_member_tag(db_session, 100, 1, CLIENT_TAG)
    await db_session.flush()

    updated_group_ids = await crud.sync_role_to_group_tags(db_session, 1, Role.TEAMLEAD)

    assert updated_group_ids == []
    members = await crud.get_group_members(db_session, 100)
    assert [m.tag for m in members] == [CLIENT_TAG]


async def test_sync_role_to_group_tags_is_a_noop_when_the_tag_already_matches(
    db_session: AsyncSession,
) -> None:
    await crud.create_group_record(db_session, group_id=100, title="Group A", created_by_userbot=True)
    await crud.get_or_create_user(db_session, 1, "bob", "Bob B.")
    await crud.add_member_tag(db_session, 100, 1, Role.MANAGER.value)
    await db_session.flush()

    updated_group_ids = await crud.sync_role_to_group_tags(db_session, 1, Role.MANAGER)

    assert updated_group_ids == []


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


async def test_remove_member_deletes_the_row_and_reports_success(db_session: AsyncSession) -> None:
    await crud.create_group_record(db_session, group_id=100, title="Group A", created_by_userbot=True)
    await crud.get_or_create_user(db_session, 2, "client", "Client")
    await crud.add_member_tag(db_session, 100, 2, CLIENT_TAG)
    await db_session.flush()

    removed = await crud.remove_member(db_session, 100, 2)

    assert removed is True
    assert await crud.get_group_members(db_session, 100) == []


async def test_remove_member_reports_false_when_not_a_member(db_session: AsyncSession) -> None:
    await crud.create_group_record(db_session, group_id=100, title="Group A", created_by_userbot=True)

    assert await crud.remove_member(db_session, 100, 999) is False


async def test_remove_member_also_deletes_the_user_row_when_it_was_their_last_group(
    db_session: AsyncSession,
) -> None:
    # A client (no role) removed from the only group they were tagged in has
    # nothing left tying them to the system — the `users` row should go too,
    # not just this one `group_members` row.
    await crud.create_group_record(db_session, group_id=100, title="Group A", created_by_userbot=True)
    await crud.get_or_create_user(db_session, 2, "client", "Client")
    await crud.add_member_tag(db_session, 100, 2, CLIENT_TAG)
    await db_session.flush()

    await crud.remove_member(db_session, 100, 2)

    assert await db_session.get(User, 2) is None


async def test_remove_member_keeps_the_user_row_if_still_a_member_elsewhere(
    db_session: AsyncSession,
) -> None:
    await crud.create_group_record(db_session, group_id=100, title="Group A", created_by_userbot=True)
    await crud.create_group_record(db_session, group_id=200, title="Group B", created_by_userbot=True)
    await crud.get_or_create_user(db_session, 2, "client", "Client")
    await crud.add_member_tag(db_session, 100, 2, CLIENT_TAG)
    await crud.add_member_tag(db_session, 200, 2, CLIENT_TAG)
    await db_session.flush()

    await crud.remove_member(db_session, 100, 2)

    assert await db_session.get(User, 2) is not None


async def test_remove_member_never_deletes_a_staff_member_even_without_remaining_groups(
    db_session: AsyncSession,
) -> None:
    # Losing membership in one specific group must never look like an
    # offboarding side effect for someone with an assigned role — that's a
    # deliberate, separate action (see group_service.offboard_staff).
    await crud.create_group_record(db_session, group_id=100, title="Group A", created_by_userbot=True)
    await crud.get_or_create_user(db_session, 1, "bob", "Bob B.")
    await crud.set_user_role(db_session, 1, Role.MANAGER)
    await crud.add_member_tag(db_session, 100, 1, Role.MANAGER.value)
    await db_session.flush()

    await crud.remove_member(db_session, 100, 1)

    assert await db_session.get(User, 1) is not None


async def test_delete_user_removes_memberships_in_every_group_and_the_user_row(
    db_session: AsyncSession,
) -> None:
    await crud.create_group_record(db_session, group_id=100, title="Group A", created_by_userbot=True)
    await crud.create_group_record(db_session, group_id=200, title="Group B", created_by_userbot=True)
    await crud.get_or_create_user(db_session, 1, "bob", "Bob B.")
    await crud.set_user_role(db_session, 1, Role.TEAMLEAD)
    await crud.add_member_tag(db_session, 100, 1, Role.TEAMLEAD.value)
    await crud.add_member_tag(db_session, 200, 1, Role.TEAMLEAD.value)
    await db_session.flush()

    deleted = await crud.delete_user(db_session, 1)

    assert deleted is True
    assert await crud.get_group_members(db_session, 100) == []
    assert await crud.get_group_members(db_session, 200) == []
    assert await db_session.get(User, 1) is None


async def test_delete_user_reports_false_for_an_unknown_user(db_session: AsyncSession) -> None:
    assert await crud.delete_user(db_session, 999) is False


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
