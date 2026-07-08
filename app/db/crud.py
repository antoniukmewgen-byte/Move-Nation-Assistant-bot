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


async def sync_role_to_group_tags(session: AsyncSession, user_id: int, role: Role) -> list[int]:
    """Приводить наявні staff-теги цього user_id у ВСІХ його групах до щойно
    зміненої ролі (`users.role`, вже виставленої через `set_user_role` вище).

    Без цього виклику зміна ролі в налаштуваннях Mini App (`POST /users/role`,
    app/api/routes/users.py::set_role) оновлює лише сам рядок `users` — усі
    вже наявні `group_members.tag`-рядки цієї людини (проставлені колись при
    створенні групи, через /tag чи /members/tag) назавжди лишаються зі
    старою роллю, і Mini App/нативний Telegram-бейдж і далі показують її
    застарілою.

    Свідомо чіпає лише рядки, де поточний тег — це якась ІНША staff-роль
    (`{r.value for r in Role}`), а не `CLIENT_TAG`: людина в принципі може
    одночасно бути співробітником і в якійсь іншій групі значитись клієнтом
    (окреме, самостійне членство) — таке відношення зміна власної посади
    зачіпати не повинна.

    Повертає список `group_id`, у яких тег дійсно змінився — виклик далі
    використовує це, щоб знати, для яких груп ще й дзеркалити зміну в сам
    Telegram через `group_service.sync_tag_to_telegram`.
    """
    role_values = {r.value for r in Role}
    result = await session.execute(select(GroupMember).where(GroupMember.user_id == user_id))
    updated_group_ids: list[int] = []
    for member in result.scalars():
        if member.tag in role_values and member.tag != role.value:
            member.tag = role.value
            updated_group_ids.append(member.group_id)

    if updated_group_ids:
        await session.flush()

    return updated_group_ids


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


async def add_member_tag(
    session: AsyncSession, group_id: int, user_id: int, tag: str, *, pending: bool = False
) -> GroupMember:
    """Додає тег учаснику групи, толерантно до повторної відправки того самого тега.

    `group_members` має `UniqueConstraint(group_id, user_id, tag)` (див.
    app/db/models.py), тож повторний виклик з тим самим (group_id, user_id, tag) —
    наприклад, подвійний тап "додати клієнта" в Mini App, або retry після
    таймауту мережі — раніше падав з `IntegrityError: UNIQUE constraint
    failed`. Той самий підхід, що й у create_group_record вище: хто встиг
    вставити рядок першим — виграє, повторний виклик отримує вже існуючий
    рядок замість помилки. Якщо повтор приніс інше значення `pending` (напр.
    клієнта спершу довелось запросити лінком, а за другим разом вдалось
    додати напряму), оновлюємо його на існуючому рядку.
    """
    member = GroupMember(group_id=group_id, user_id=user_id, tag=tag, pending=pending)
    session.add(member)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        result = await session.execute(
            select(GroupMember).where(
                GroupMember.group_id == group_id, GroupMember.user_id == user_id, GroupMember.tag == tag
            )
        )
        existing = result.scalar_one()
        if existing.pending != pending:
            existing.pending = pending
            await session.flush()
        return existing
    return member


async def clear_pending(session: AsyncSession, group_id: int, user_id: int) -> str | None:
    """Знімає прапорець pending, коли клієнт дійсно приєднався за пересланим лінком.

    add_member_tag (вище) ставить pending=True, коли пряме додавання клієнта
    не вдалось через приватність і йому лишилось надіслати лінк-запрошення
    (app/services/group_service.py::add_client) — до фактичного приєднання
    Mini App інакше показував би його як уже повноцінного учасника групи.
    Викликається з app/bot/handlers/messages.py::on_member_joined_group, коли
    Telegram підтверджує вступ. Повертає тег рядка (щоб той самий виклик міг
    ще й синхронізувати його в Telegram через group_service.sync_tag_to_telegram
    — до фактичного приєднання це було неможливо, учасника ще не було в чаті),
    або None, якщо для цього (group_id, user_id) не було жодного pending-рядка
    (типово — не клієнт, а штатний співробітник, чи клієнт, доданий напряму
    без pending).
    """
    result = await session.execute(
        select(GroupMember).where(
            GroupMember.group_id == group_id, GroupMember.user_id == user_id, GroupMember.pending.is_(True)
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        return None
    member.pending = False
    await session.flush()
    return member.tag


async def remove_member(session: AsyncSession, group_id: int, user_id: int) -> bool:
    """Прибирає учасника (тег) з конкретної групи.

    Якщо після цього в user_id не лишилось жодного членства в жодній іншій
    групі і в нього немає ролі (тобто це не співробітник, а клієнт чи
    будь-хто інший без посади) — прибирає заодно й сам рядок `users`: немає
    сенсу тримати навіки запис про людину, яка вже ніде не значиться і
    ніколи не була частиною команди.

    Співробітників (`User.role IS NOT NULL`) це не чіпає, навіть якщо в них
    теж не лишилось жодного членства — те, що людину прибрали з однієї
    конкретної групи, ще нічого не каже про її статус у компанії загалом
    (вона й далі в "стартовому складі" для будь-яких нових груп). Звільнення
    співробітника — окрема, явна дія (`group_service.offboard_staff`, зараз
    єдиний тригер — сама людина блокує бота в особистих, див.
    app/bot/handlers/messages.py::on_bot_removed_from_group), а не побічний
    ефект видалення з однієї групи.

    Повертає True, якщо рядок `group_members` дійсно існував і був
    видалений, False — якщо цього user_id вже не було серед учасників групи
    (нема що видаляти, а не помилка — той самий підхід, що й у delete_group).
    """
    result = await session.execute(
        select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
    )
    member = result.scalar_one_or_none()
    if member is None:
        return False
    await session.delete(member)
    await session.flush()

    user = await session.get(User, user_id)
    if user is not None and user.role is None:
        remaining = await session.execute(select(GroupMember).where(GroupMember.user_id == user_id))
        if remaining.scalar_one_or_none() is None:
            await session.delete(user)
            await session.flush()

    return True


async def delete_user(session: AsyncSession, user_id: int) -> bool:
    """Видаляє співробітника повністю — усі його теги/членства в БУДЬ-ЯКИХ
    групах, а тоді сам рядок `users` (роль, /connect-сесію, усе).

    Використовується при звільненні (див. app/services/group_service.py::
    offboard_staff) — на відміну від `remove_member` вище, який прибирає
    лише одну конкретну (group_id, user_id) пару. `GroupMember.user_id` — FK
    без ORM-каскаду з боку `User.memberships` (на відміну від `Group.members`,
    де є `cascade="all, delete-orphan"`, — див. app/db/models.py), тож усі
    членства цього user_id прибираємо явно, в тій самій транзакції, перш ніж
    видаляти сам рядок `users` — інакше видалення впало б на зовнішньому ключі.

    Повертає True, якщо рядок `users` дійсно існував і був видалений, False —
    якщо такого user_id вже нема (той самий підхід, що й у delete_group).
    """
    result = await session.execute(select(GroupMember).where(GroupMember.user_id == user_id))
    for member in result.scalars():
        await session.delete(member)

    user = await session.get(User, user_id)
    if user is None:
        return False
    await session.delete(user)
    await session.flush()
    return True


async def get_group(session: AsyncSession, group_id: int) -> Group | None:
    return await session.get(Group, group_id)


async def delete_group(session: AsyncSession, group_id: int) -> bool:
    """Прибирає групу з БД разом з усіма її учасниками (тегами).

    `Group.members` має `cascade="all, delete-orphan"` на рівні ORM (див.
    app/db/models.py), тож видалення через `session.delete()` саме заб'є
    пов'язані рядки `group_members` — окремо чистити їх не треба.
    """
    group = await session.get(Group, group_id)
    if group is None:
        return False
    await session.delete(group)
    await session.flush()
    return True


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
        # Новий (або черговий) неопрацьований меседж скидає лічильник нагадувань:
        # перше нагадування по цьому циклу знову має чекати повний інтервал від
        # останнього повідомлення клієнта, а не спиратись на час попереднього
        # нагадування з давнього циклу.
        group.last_reminder_at = None


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
