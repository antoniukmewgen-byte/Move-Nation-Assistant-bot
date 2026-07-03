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
from telethon.errors import UsernameInvalidError, UserNotMutualContactError, UserPrivacyRestrictedError
from telethon.sessions import StringSession
from telethon.tl.functions.channels import EditAdminRequest, EditPhotoRequest, InviteToChannelRequest
from telethon.tl.functions.messages import CreateChatRequest, ExportChatInviteRequest, MigrateChatRequest
from telethon.tl.types import ChatAdminRights, InputChatUploadedPhoto
from telethon.utils import get_peer_id

from app.config import settings
from app.db.models import Role

logger = logging.getLogger(__name__)

ASSISTANT_ADMIN_RIGHTS = ChatAdminRights(
    change_info=True,
    post_messages=False,
    edit_messages=False,
    delete_messages=True,
    ban_users=True,
    invite_users=True,
    pin_messages=True,
    add_admins=False,
    anonymous=False,
    manage_call=False,
    other=True,
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
) -> tuple[int | None, str | None]:
    """Додає клієнта за username в групу від імені співробітника, який ініціював дію."""
    client = _client_for(actor_session)
    await client.connect()

    try:
        channel = await client.get_entity(group_id)
        identifier = identifier.lstrip("@")

        try:
            entity = await client.get_entity(identifier)
        except (ValueError, UsernameInvalidError):
            return None, None

        try:
            await client(InviteToChannelRequest(channel=channel, users=[entity]))
            return entity.id, None
        except (UserPrivacyRestrictedError, UserNotMutualContactError):
            link = await client(ExportChatInviteRequest(peer=channel))
            return entity.id, link.link
    finally:
        await client.disconnect()
