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

from telethon.errors import FloodWaitError

from app.db import crud
from app.db.models import CLIENT_TAG, Group
from app.db.session import async_session
from app.services.crypto import decrypt_session
from app.userbot.actions import add_client_to_group, create_group_with_team

logger = logging.getLogger(__name__)


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
        return group


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
        client_user_id, invite_link = await add_client_to_group(
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
        await crud.get_or_create_user(session, client_user_id, identifier.lstrip("@"), None)
        await crud.add_member_tag(session, group_id, client_user_id, CLIENT_TAG, pending=invite_link is not None)
        await session.commit()

    return client_user_id, invite_link
