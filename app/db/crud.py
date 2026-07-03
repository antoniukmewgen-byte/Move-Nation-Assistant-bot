from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import CLIENT_TAG, NOTIFY_ROLES, Group, GroupMember, GroupStatus, Role, User


async def get_or_create_user(
    session: AsyncSession, user_id: int, username: str | None, full_name: str | None
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        user = User(id=user_id, username=username, full_name=full_name)
        session.add(user)
        await session.flush()
    else:
        user.username = username
        user.full_name = full_name
    return user


async def set_user_role(session: AsyncSession, user_id: int, role: Role) -> None:
    user = await session.get(User, user_id)
    if user:
        user.role = role


async def set_user_session(session: AsyncSession, user_id: int, encrypted_session: str) -> None:
    user = await session.get(User, user_id)
    if user:
        user.session_string = encrypted_session


async def get_user_session(session: AsyncSession, user_id: int) -> str | None:
    user = await session.get(User, user_id)
    return user.session_string if user else None


async def get_staff_users(session: AsyncSession) -> list[User]:
    result = await session.execute(select(User).where(User.role.is_not(None)))
    return list(result.scalars())


async def create_group_record(
    session: AsyncSession, group_id: int, title: str, created_by_userbot: bool
) -> Group:
    """Реєструє нову групу, толерантно до перегонів між двома джерелами реєстрації.

    Один і той самий (пост-міграційний) chat_id може одночасно прийти сюди
    двома шляхами: із запиту `POST /groups` (`app/api/routes/groups.py`,
    одразу після `create_group_with_team`) і з `my_chat_member`-івенту, який
    aiogram отримує про той самий чат (`on_bot_added_to_group` в
    `app/bot/handlers/messages.py`) — Telegram надсилає боту легітимний
    апдейт вже для нового supergroup id, не лише застарілий для старого
    базового чату. Хто встиг вставити рядок першим — виграє; той, хто
    запізнився, раніше падав з `IntegrityError: UNIQUE constraint failed:
    groups.id`. Замість цього повертаємо вже існуючий рядок.
    """
    group = Group(id=group_id, title=title, created_by_userbot=created_by_userbot, status=GroupStatus.ACTIVE)
    session.add(group)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        existing = await session.get(Group, group_id)
        if existing is None:
            raise
        return existing
    return group


async def add_member_tag(session: AsyncSession, group_id: int, user_id: int, tag: str) -> GroupMember:
    member = GroupMember(group_id=group_id, user_id=user_id, tag=tag)
    session.add(member)
    await session.flush()
    return member


async def get_group(session: AsyncSession, group_id: int) -> Group | None:
    return await session.get(Group, group_id)


async def get_group_members(session: AsyncSession, group_id: int) -> list[GroupMember]:
    result = await session.execute(
        select(GroupMember).where(GroupMember.group_id == group_id).options(selectinload(GroupMember.user))
    )
    return list(result.scalars())


async def get_groups_for_user(session: AsyncSession, user_id: int) -> list[Group]:
    result = await session.execute(
        select(Group)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(GroupMember.user_id == user_id)
    )
    return list(result.scalars())


async def user_is_group_member(session: AsyncSession, group_id: int, user_id: int) -> bool:
    result = await session.execute(
        select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
    )
    return result.scalar_one_or_none() is not None


async def is_client(session: AsyncSession, group_id: int, user_id: int) -> bool:
    result = await session.execute(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == user_id,
            GroupMember.tag == CLIENT_TAG,
        )
    )
    return result.scalar_one_or_none() is not None


async def mark_awaiting_response(
    session: AsyncSession, group_id: int, from_user_id: int, at: datetime
) -> None:
    group = await session.get(Group, group_id)
    if group:
        group.last_message_from_id = from_user_id
        group.last_message_at = at
        group.awaiting_response = True


async def clear_awaiting_response(session: AsyncSession, group_id: int) -> None:
    group = await session.get(Group, group_id)
    if group:
        group.awaiting_response = False


async def get_groups_awaiting_response(session: AsyncSession) -> list[Group]:
    result = await session.execute(select(Group).where(Group.awaiting_response.is_(True)))
    return list(result.scalars())


async def get_notify_recipients(session: AsyncSession, group_id: int) -> list[User]:
    result = await session.execute(
        select(User)
        .join(GroupMember, GroupMember.user_id == User.id)
        .where(GroupMember.group_id == group_id, User.role.in_(NOTIFY_ROLES))
    )
    return list(result.scalars())
