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
from app.services.crypto import decrypt_session
from app.services.group_creation_registry import mark_pending, unmark_pending
from app.userbot.actions import add_client_to_group, create_group_with_team

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
    roster = "\n".join(f"• {escape(user.full_name or user.username or str(user.id))} — {user.role.value}" for user in staff)
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

    return client_user_id, invite_link
