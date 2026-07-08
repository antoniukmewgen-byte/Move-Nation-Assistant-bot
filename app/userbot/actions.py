"""MTProto (Telethon) actions executed on behalf of a specific staff member.

Every function here takes that staff member's own decrypted session string
and opens a short-lived :class:`TelegramClient` for the duration of the
call — there is no shared "userbot" account. This keeps the Telegram-level
ownership of groups/invites with the real person who triggered the action
in the bot or Mini App.
"""

from __future__ import annotations

import logging

from telethon import TelegramClient
from telethon.errors import (
    ChannelPrivateError,
    ChatAdminRequiredError,
    UsernameInvalidError,
    UserNotMutualContactError,
    UserNotParticipantError,
    UserPrivacyRestrictedError,
)
from telethon.sessions import StringSession
from telethon.tl.functions.channels import (
    DeleteChannelRequest,
    EditAdminRequest,
    EditPhotoRequest,
    InviteToChannelRequest,
)
from telethon.tl.functions.messages import CreateChatRequest, ExportChatInviteRequest, MigrateChatRequest
from telethon.tl.types import ChatAdminRights, InputChatUploadedPhoto
from telethon.utils import get_peer_id

from app.config import settings
from app.db.models import Role

logger = logging.getLogger(__name__)

ASSISTANT_ADMIN_RIGHTS = ChatAdminRights(
    change_info=True,
    post_messages=True,
    edit_messages=True,
    delete_messages=True,
    ban_users=True,
    invite_users=True,
    pin_messages=True,
    add_admins=True,
    anonymous=False,
    manage_call=True,
    other=True,
    manage_topics=True,
    manage_ranks=True,
)


def _client_for(session_string: str) -> TelegramClient:
    return TelegramClient(StringSession(session_string), settings.api_id, settings.api_hash)


async def _promote_assistant_bot(client: TelegramClient, channel) -> None:
    """Invite/promote our Bot API bot as admin so it keeps visibility for reminders.

    Requires resolving the bot's username to an input entity first — passing
    a raw username string straight into a hand-built MTProto request does
    not work the way the high-level Telethon helper methods do.
    """
    bot_entity = await client.get_entity(settings.bot_username)
    await client(
        EditAdminRequest(
            channel=channel,
            user_id=bot_entity,
            admin_rights=ASSISTANT_ADMIN_RIGHTS,
            rank="Assistant",
        )
    )


async def create_group_with_team(
    creator_session: str, title: str, staff: list[tuple[int, str | None, Role | None]]
) -> int:
    """Створює нову супергрупу від імені співробітника, додає основний состав і бота-асистента як адміна."""
    client = _client_for(creator_session)
    await client.connect()

    try:
        usernames = {f"@{username}" for _uid, username, _role in staff if username}
        usernames.add(f"@{settings.bot_username}")

        created = await client(CreateChatRequest(users=list(usernames), title=title))
        # Newer Telegram API layers wrap the resulting `Updates` inside a
        # `messages.InvitedUsers` envelope (`.updates` + `.missing_invitees`)
        # instead of returning it directly — `created.chats` no longer
        # exists, the chat list now lives one level deeper.
        basic_chat = created.updates.chats[0]

        # Мігруємо в супергрупу одразу, щоб надалі однаково працювати з channel-функціями
        migrated = await client(MigrateChatRequest(chat_id=basic_chat.id))
        channel = next(c for c in migrated.chats if getattr(c, "megagroup", False))

        if settings.default_logo_path.exists():
            file = await client.upload_file(str(settings.default_logo_path))
            await client(EditPhotoRequest(channel=channel, photo=InputChatUploadedPhoto(file)))
        else:
            logger.warning("Дефолтне лого не знайдено за шляхом %s, пропускаю", settings.default_logo_path)

        await _promote_assistant_bot(client, channel)

        return get_peer_id(channel)
    finally:
        await client.disconnect()


async def add_client_to_group(
    actor_session: str, group_id: int, identifier: str
) -> tuple[int | None, str | None, str | None]:
    """Додає клієнта за username в групу від імені співробітника, який ініціював дію.

    Повертає ``(user_id, full_name, invite_link)``. ``full_name`` береться з
    резолвнутої Telethon-entity (first_name + last_name), а не з самого
    ``identifier`` (це лише username) — інакше клієнт назавжди лишався б у
    нашій БД без full_name і всюди, де застосунок показує людей за іменем
    (team.py, Mini App), падав би назад на username.
    """
    client = _client_for(actor_session)
    await client.connect()

    try:
        channel = await client.get_entity(group_id)
        identifier = identifier.lstrip("@")

        try:
            entity = await client.get_entity(identifier)
        except (ValueError, UsernameInvalidError):
            return None, None, None

        full_name = " ".join(part for part in (entity.first_name, entity.last_name) if part) or None

        try:
            await client(InviteToChannelRequest(channel=channel, users=[entity]))
            return entity.id, full_name, None
        except (UserPrivacyRestrictedError, UserNotMutualContactError):
            link = await client(ExportChatInviteRequest(peer=channel))
            return entity.id, full_name, link.link
    finally:
        await client.disconnect()


async def remove_member_from_group(actor_session: str, group_id: int, user_id: int) -> bool:
    """Прибирає учасника з групи в Telegram від імені співробітника, який ініціював дію.

    Симетрично до add_client_to_group і delete_group вище: якщо в акаунта,
    чиєю сесією діємо, немає прав кікнути учасника (наприклад, звичайний
    учасник намагається видалити іншого), або цього учасника вже нема в
    чаті, або Telethon ще не бачив цей user_id у цьому чаті і не може
    резолвнути його в entity — просто повертаємо False замість падіння з
    винятком. Прибирання тега з нашої БД (app/db/crud.py::remove_member) не
    повинно залежати від того, чи вдалося кікнути людину в самому Telegram —
    інакше застарілі/недоступні записи неможливо було б прибрати з застосунку.
    """
    client = _client_for(actor_session)
    await client.connect()

    try:
        try:
            channel = await client.get_entity(group_id)
        except (ValueError, ChannelPrivateError):
            return False

        try:
            await client.kick_participant(channel, user_id)
            return True
        except (ChatAdminRequiredError, UserNotParticipantError, ValueError):
            return False
    finally:
        await client.disconnect()


async def scan_group_members(actor_session: str, group_id: int) -> list[tuple[int, str | None, str | None, bool]]:
    """Повертає ПОВНИЙ список учасників групи від імені співробітника, який ініціював /sync.

    Bot API дає лише `getChatAdministrators` — звичайних учасників чату він
    узагалі не бачить, тож так само, як і create_group_with_team/
    add_client_to_group вище, єдиний спосіб просканувати кожного —
    MTProto-сесія самого учасника. Повертає ``(user_id, username, full_name,
    is_bot)`` для кожного — викликач (`group_service.sync_group`) сам
    вирішує, кого з них і як тегувати.
    """
    client = _client_for(actor_session)
    await client.connect()

    try:
        channel = await client.get_entity(group_id)
        participants = await client.get_participants(channel)
        return [
            (
                p.id,
                p.username,
                " ".join(part for part in (p.first_name, p.last_name) if part) or None,
                bool(p.bot),
            )
            for p in participants
        ]
    finally:
        await client.disconnect()


async def delete_group(actor_session: str, group_id: int) -> bool:
    """Видаляє супергрупу в Telegram від імені співробітника, який ініціював дію.

    Telegram дозволяє повне видалення каналу/супергрупи лише творцю (або
    адміну з правом на це) — а в нашій БД немає інформації про те, хто саме
    є творцем конкретної групи. Тож якщо в акаунта, чиєю сесією діємо, немає
    достатніх прав, просто повертаємо False замість падіння з винятком:
    видалення запису з нашої БД (див. app/db/crud.py::delete_group) не
    повинно залежати від того, чи вдалося видалити сам чат у Telegram —
    інакше застарілі/недоступні групи неможливо було б прибрати з застосунку.
    """
    client = _client_for(actor_session)
    await client.connect()

    try:
        try:
            channel = await client.get_entity(group_id)
        except (ValueError, ChannelPrivateError):
            # Чат уже недоступний цьому акаунту (наприклад, його вже
            # прибрали чи акаунт видалили з групи іншим шляхом) — нема що
            # видаляти на боці Telegram, але прибрати з БД все одно треба.
            return False

        try:
            await client(DeleteChannelRequest(channel=channel))
            return True
        except (ChatAdminRequiredError, UserNotParticipantError):
            return False
    finally:
        await client.disconnect()
