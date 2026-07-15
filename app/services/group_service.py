"""Shared business logic for creating groups and adding clients.

Both the bot (`app/bot/handlers/group_creation.py`, `add_client.py`) and the
Mini App API (`app/api/routes/groups.py`, `members.py`) need to do exactly
the same sequence of steps — check the actor is connected/authorized, call
the Telethon action, then persist the result — and previously duplicated
that logic (including the FloodWaitError/generic-exception handling) in
four separate places. This module is the single owner of that sequence;
each surface only translates the exceptions below into its own
message/HTTP-status format.

`FloodWaitError` (telethon.errors) is deliberately *not* wrapped — callers
already need `exc.seconds` to build their own surface-appropriate message,
so it propagates as-is and each caller catches it directly.
"""

from __future__ import annotations

import logging
from html import escape

from aiogram.exceptions import TelegramBadRequest
from telethon.errors import FloodWaitError

from app.bot.bot_instance import bot
from app.db import crud
from app.db.models import CLIENT_TAG, Group, User
from app.db.session import async_session
from app.services import realtime
from app.services.crypto import decrypt_session
from app.services.group_creation_registry import mark_pending, unmark_pending
from app.userbot.actions import add_client_to_group, create_group_with_team, scan_group_members

logger = logging.getLogger(__name__)


async def _send_group_welcome_message(chat_id: int, title: str, staff: list[User]) -> None:
    """Вітальне повідомлення в щойно створеній через застосунок групі.

    На відміну від `on_bot_added_to_group` (app/bot/handlers/messages.py) —
    того загального "мене додали, признач адміном і затегай /register /tag"
    тексту — тут усе, про що там просять, уже зроблено самим `create_group`
    (бот вже адмін, стартовий склад вже затегований), тож повідомлення просто
    представляє бота і показує, хто вже в групі, замість застарілих інструкцій.
    Найкращий-варіант і без винятків назовні: сам факт створення групи вже
    закомічено викликачем, а це повідомлення — лише косметичне вітання.
    """
    roster_lines = []
    for user in staff:
        # `staff` comes from crud.get_staff_users(), which DB-filters to
        # `role IS NOT NULL` — real at runtime, but not visible to mypy
        # through the `list[User]` return type, hence the assert.
        assert user.role is not None
        name = escape(user.full_name or user.username or str(user.id))
        roster_lines.append(f"• {name} — {user.role.value}")
    roster = "\n".join(roster_lines)
    text = (
        f"👋 Привіт! Я асистент групи «<b>{escape(title)}</b>».\n"
        "Буду нагадувати, якщо клієнт довго чекає на відповідь.\n\n"
        f"Стартовий склад:\n{roster}\n\n"
        "Усе вже налаштовано — гарної роботи! 🎯"
    )
    try:
        await bot.send_message(chat_id, text)
    except Exception:
        logger.exception("Не вдалося надіслати вітальне повідомлення в групу %s", chat_id)


async def sync_tag_to_telegram(chat_id: int, user_id: int, tag: str) -> None:
    """Дзеркалить наш тег учасника в нативний Telegram-тег того ж учасника.

    Спирається на право `can_manage_tags` ("Зміна тегів учасників"), яке
    отримує бот-асистент при промоуті в адміни (див.
    app/userbot/actions.py::ASSISTANT_ADMIN_RIGHTS) — тож працює лише в
    групах, де в бота вже є ця роль. Telegram обмежує тег 16 символами й не
    дозволяє emoji. Best-effort і без винятків назовні: тег у нашій БД вже
    закомічений викликачем, а видимий у Telegram бейдж — лише косметика
    поверх нього, тож збій синхронізації не повинен ламати основний флоу.
    """
    try:
        await bot.set_chat_member_tag(chat_id, user_id, tag[:16])
    except TelegramBadRequest as exc:
        if "CHAT_CREATOR_REQUIRED" in exc.message:
            # Telegram ніколи не дозволяє промоутнутому адміну (навіть із
            # can_manage_tags) тегувати власника (creator) чату — це може
            # зробити лише сам власник. Ми не зберігаємо, хто саме створив
            # конкретну групу (див. app/userbot/actions.py::delete_group),
            # тож заздалегідь пропустити цей виклик для власника не можемо —
            # але це очікуваний, непоправний no-op для нього одного, а не
            # реальний збій, тож логуємо тихо, без warning і трейсбеку.
            logger.info(
                "Не можу встановити тег «%s» для user_id=%s у групі %s — це власник чату, "
                "Telegram дозволяє тегувати творця лише йому самому",
                tag,
                user_id,
                chat_id,
            )
            return
        logger.warning(
            "Не вдалося встановити тег «%s» у Telegram для user_id=%s у групі %s: %s",
            tag,
            user_id,
            chat_id,
            exc.message,
        )


class GroupServiceError(Exception):
    """Base for errors this module raises; callers translate these into user-facing responses."""


class NotConnectedError(GroupServiceError):
    """The acting user hasn't finished `/connect` yet."""


class GroupAccessDeniedError(GroupServiceError):
    """The acting user isn't a member of the group they're trying to act on."""


class ClientNotFoundError(GroupServiceError):
    """The given identifier couldn't be resolved to a Telegram user."""


class GroupCreationFailedError(GroupServiceError):
    """Wraps an unexpected (already-logged) failure from the Telegram-side group creation call."""


class AddClientFailedError(GroupServiceError):
    """Wraps an unexpected (already-logged) failure from the Telegram-side add-client call."""


class StaffNotFoundError(GroupServiceError):
    """No user row exists for the given user_id — nothing to offboard."""


class GroupSyncFailedError(GroupServiceError):
    """Wraps an unexpected (already-logged) failure from the Telegram-side membership scan."""


async def create_group(user_id: int, title: str) -> Group:
    """Creates a group in Telegram (via the user's own session) and records it.

    Raises :class:`NotConnectedError` if the user hasn't run `/connect`,
    lets :class:`telethon.errors.FloodWaitError` propagate as-is, and raises
    :class:`GroupCreationFailedError` for anything else that went wrong
    talking to Telegram (already logged with `logger.exception` here).
    """
    async with async_session() as session:
        encrypted_session = await crud.get_user_session(session, user_id)
        if encrypted_session is None:
            raise NotConnectedError()
        staff = await crud.get_staff_users(session)
        staff_ids = [(u.id, u.username, u.role) for u in staff]

    # `user_id`'s own Telethon session is what performs every Telegram-side
    # action below (group creation, migration, admin promotion) — Bot API
    # reports `user_id` as the `from_user` on the resulting `my_chat_member`
    # updates for the bot (see app/bot/handlers/messages.py::
    # on_bot_added_to_group). Marking *before* the first network call, rather
    # than the chat_id only once it's known (the previous approach), closes
    # the race: Bot API delivers those updates over its own polling loop,
    # independent of — and not necessarily after — our own MTProto calls
    # returning, so a chat_id learned "after the fact" could already be too
    # late. See app/services/group_creation_registry.py for the full story.
    mark_pending(user_id)
    try:
        try:
            chat_id = await create_group_with_team(decrypt_session(encrypted_session), title, staff_ids)
        except FloodWaitError:
            raise
        except Exception as exc:
            logger.exception("Не вдалося створити групу «%s» для user_id=%s", title, user_id)
            raise GroupCreationFailedError() from exc

        async with async_session() as session:
            group = await crud.create_group_record(session, chat_id, title, created_by_userbot=True)
            for staff_user_id, _username, role in staff_ids:
                if role is not None:
                    await crud.add_member_tag(session, chat_id, staff_user_id, role.value)
            await session.commit()
            await session.refresh(group)

        for staff_user_id, _username, role in staff_ids:
            if role is not None:
                await sync_tag_to_telegram(chat_id, staff_user_id, role.value)

        await _send_group_welcome_message(chat_id, title, staff)

        # Everyone tagged into the new group just gained a row in their own
        # /groups list — tell each of their Mini App sessions to refetch it.
        tagged_user_ids = [staff_user_id for staff_user_id, _username, role in staff_ids if role is not None]
        await realtime.notify_users(tagged_user_ids, {"type": "groups_changed"})

        return group
    finally:
        # Stays marked through the DB/tag-sync/welcome-message steps above
        # too — those are several more awaited round trips, giving the Bot
        # API polling loop plenty of headroom to have already delivered and
        # processed both `my_chat_member` updates by the time we unmark.
        unmark_pending(user_id)


async def add_client(user_id: int, group_id: int, identifier: str) -> tuple[int, str | None]:
    """Adds a client to a group on behalf of ``user_id`` and tags them as a client.

    Returns ``(client_user_id, invite_link)`` — ``invite_link`` is set only
    when a direct add failed due to the client's privacy settings and they
    need to be sent a link instead; the resulting membership row is marked
    `pending` until they actually join (see
    app/bot/handlers/messages.py::on_member_joined_group).

    Raises :class:`GroupAccessDeniedError` if ``user_id`` isn't a member of
    the group, :class:`NotConnectedError` if they haven't run `/connect`,
    :class:`ClientNotFoundError` if ``identifier`` doesn't resolve to a
    Telegram user, lets `FloodWaitError` propagate, and raises
    :class:`AddClientFailedError` for anything else.
    """
    async with async_session() as session:
        if not await crud.user_is_group_member(session, group_id, user_id):
            raise GroupAccessDeniedError()
        encrypted_session = await crud.get_user_session(session, user_id)
        if encrypted_session is None:
            raise NotConnectedError()

    try:
        client_user_id, client_full_name, invite_link = await add_client_to_group(
            decrypt_session(encrypted_session), group_id, identifier
        )
    except FloodWaitError:
        raise
    except Exception as exc:
        logger.exception(
            "Не вдалося додати клієнта «%s» у групу %s від імені user_id=%s", identifier, group_id, user_id
        )
        raise AddClientFailedError() from exc

    if client_user_id is None:
        raise ClientNotFoundError()

    async with async_session() as session:
        await crud.get_or_create_user(session, client_user_id, identifier.lstrip("@"), client_full_name)
        await crud.add_member_tag(session, group_id, client_user_id, CLIENT_TAG, pending=invite_link is not None)
        await session.commit()

    if invite_link is None:
        # Клієнта вже додано напряму — він учасник чату, тег можна виставити
        # одразу. Якщо ж лишився лише invite_link (pending=True вище), він
        # ще не в чаті, і setChatMemberTag впаде — синхронізуємо тег пізніше,
        # коли on_member_joined_group підтвердить фактичне приєднання
        # (app/bot/handlers/messages.py).
        await sync_tag_to_telegram(group_id, client_user_id, CLIENT_TAG)

    # The client just gained a row in their own /groups list, and everyone
    # already in the group needs their /members list to reflect the new row
    # too (whether the client is pending an invite-link join or already in).
    await realtime.notify_user(client_user_id, {"type": "groups_changed"})
    await realtime.notify_group(group_id, {"type": "members_changed", "group_id": group_id})

    return client_user_id, invite_link


async def tag_new_member(chat_id: int, user_id: int, username: str | None, full_name: str | None) -> None:
    """Автотегує учасника одразу після вступу, якщо для нього не було pending-запису.

    `on_member_joined_group` (app/bot/handlers/messages.py) викликає це,
    коли `crud.clear_pending` повернув None — тобто людину не проводили
    через `/add_client` заздалегідь (наприклад, її запросили просто лінком
    групи напряму в Telegram, без участі бота). Раніше такий учасник лишався
    б без офіційного тега аж до наступного ручного /sync; тепер тегуємо
    одразу тим самим правилом, що й повний скан у `sync_group`: якщо в БД
    для нього вже є призначена роль — це свій співробітник, якого просто
    додали в групу напряму, тегуємо його роллю; інакше вважаємо клієнтом
    (`CLIENT_TAG`).

    Best-effort і мовчазний no-op, якщо ця група взагалі не зареєстрована в
    нашій БД — нема куди записувати тег.
    """
    async with async_session() as session:
        if await crud.get_group(session, chat_id) is None:
            return
        db_user = await crud.get_or_create_user(session, user_id, username, full_name)
        desired_tag = db_user.role.value if db_user.role else CLIENT_TAG
        changed = await crud.set_member_tag(session, chat_id, user_id, desired_tag)
        await session.commit()

    if changed:
        await sync_tag_to_telegram(chat_id, user_id, desired_tag)
        await realtime.notify_group(chat_id, {"type": "members_changed", "group_id": chat_id})


async def offboard_staff(user_id: int) -> int:
    """Звільняє співробітника: прибирає його з усіх груп, де він зараз
    затегований, і повністю видаляє з БД (роль, /connect-сесію, усі теги).

    На відміну від "вийшов/його кікнули з однієї групи"
    (app/bot/handlers/messages.py::on_member_left_group) — той випадок
    стосується лише конкретного чату й нічого не каже про глобальний статус
    людини в компанії. Звільнення — навпаки, одразу закриває всі групи разом
    і прибирає людину зі "стартового складу" (`crud.get_staff_users`) для
    будь-яких НОВИХ груп, які будуть створені після цього. Єдиний спосіб це
    викликати — сама людина блокує бота в особистих
    (app/bot/handlers/messages.py::on_bot_removed_from_group, приватна
    гілка): жодного окремого API, яким хтось інший міг би звільнити
    співробітника, немає.

    Кік із кожної групи — best-effort і не блокує рештку: якщо бот не адмін
    у якійсь конкретній групі (наприклад, зареєстрованій через /register, а
    не створеній через застосунок) чи людини там уже нема, це не має заважати
    прибрати її зі решти груп і з самої БД — сенс дії саме в тому, щоб вона
    перестала бути співробітником, незалежно від нюансів однієї групи.

    Кидає :class:`StaffNotFoundError`, якщо такого user_id взагалі нема в
    БД. Повертає кількість груп, з яких людину прибрано.
    """
    async with async_session() as session:
        groups = await crud.get_groups_for_user(session, user_id)

    for group in groups:
        try:
            await bot.ban_chat_member(group.id, user_id)
            await bot.unban_chat_member(group.id, user_id, only_if_banned=True)
        except TelegramBadRequest:
            logger.warning(
                "Не вдалося видалити user_id=%s з групи %s під час звільнення "
                "(бот не адмін у цьому чаті чи учасника вже нема)",
                user_id,
                group.id,
                exc_info=True,
            )

    async with async_session() as session:
        deleted = await crud.delete_user(session, user_id)
        await session.commit()

    if not deleted:
        raise StaffNotFoundError()

    # Everyone else still in each of these groups needs their /members list
    # to drop this now ex-staff row.
    for group in groups:
        await realtime.notify_group(group.id, {"type": "members_changed", "group_id": group.id})

    return len(groups)


async def sync_group(actor_user_id: int, group_id: int, title: str) -> tuple[int, int]:
    """Повна звірка складу групи від імені `actor_user_id`, замінює /register+/tag.

    Bot API бачить лише адмінів, тож для повного списку учасників доводиться
    йти через MTProto-сесію самого співробітника, який запустив /sync (див.
    app/userbot/actions.py::scan_group_members). За результатом скану:

    - група реєструється в нашій БД, якщо ще не була (`created_by_userbot=False`,
      бо цей чат міг бути створений будь-як, задовго до підключення бота);
    - кожному знайденому учаснику виставляється "офіційний" тег — його
      поточна роль із БД, або CLIENT_TAG, якщо ролі нема (`crud.set_member_tag`,
      звірка/replace — Варіант А: перезаписує застарілий тег на актуальний,
      але ніколи не чіпає довільні кастомні теги, додані окремо через /tag чи
      Mini App);
    - кожен, кого скан більше не бачить серед учасників, повністю прибирається
      з group_members (і з БД, якщо це був єдиний запис про людину — Варіант А,
      повна звірка, на відміну від on_member_left_group, який стосується лише
      миттєвого виходу/кіку одного учасника).

    Повертає ``(updated_count, removed_count)``. Лишає FloodWaitError
    поширюватись як є (той самий підхід, що й create_group/add_client), і
    кидає :class:`GroupSyncFailedError` для решти несподіваних збоїв
    спілкування з Telegram.
    """
    async with async_session() as session:
        encrypted_session = await crud.get_user_session(session, actor_user_id)
        if encrypted_session is None:
            raise NotConnectedError()

    try:
        participants = await scan_group_members(decrypt_session(encrypted_session), group_id)
    except FloodWaitError:
        raise
    except Exception as exc:
        logger.exception("Не вдалося просканувати учасників групи %s від імені user_id=%s", group_id, actor_user_id)
        raise GroupSyncFailedError() from exc

    scanned_ids = {user_id for user_id, _username, _full_name, is_bot in participants if not is_bot}
    changed_tags: list[tuple[int, str]] = []
    removed_ids: list[int] = []

    async with async_session() as session:
        await crud.create_group_record(session, group_id, title, created_by_userbot=False)
        current_members = await crud.get_group_members(session, group_id)
        current_user_ids = {member.user_id for member in current_members}

        for user_id, username, full_name, is_bot in participants:
            if is_bot:
                continue
            db_user = await crud.get_or_create_user(session, user_id, username, full_name)
            desired_tag = db_user.role.value if db_user.role else CLIENT_TAG
            if await crud.set_member_tag(session, group_id, user_id, desired_tag):
                changed_tags.append((user_id, desired_tag))

        for user_id in current_user_ids - scanned_ids:
            if await crud.remove_member(session, group_id, user_id):
                removed_ids.append(user_id)

        await session.commit()

    for user_id, tag in changed_tags:
        await sync_tag_to_telegram(group_id, user_id, tag)

    # Anyone whose tag changed or who got removed just had their own
    # /groups list affected (a group appearing/disappearing, or its badge
    # changing); everyone currently in the group needs a /members refetch.
    affected_user_ids = {user_id for user_id, _tag in changed_tags} | set(removed_ids)
    if affected_user_ids:
        await realtime.notify_users(affected_user_ids, {"type": "groups_changed"})
    if changed_tags or removed_ids:
        await realtime.notify_group(group_id, {"type": "members_changed", "group_id": group_id})

    return len(changed_tags), len(removed_ids)
