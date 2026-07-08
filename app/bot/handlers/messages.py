from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramMigrateToChat
from aiogram.filters import Command
from aiogram.filters.chat_member_updated import JOIN_TRANSITION, LEAVE_TRANSITION, ChatMemberUpdatedFilter
from aiogram.types import ChatMemberUpdated, Message

from app.bot.guards import require_text, require_user
from app.config import settings
from app.db import crud
from app.db.models import GroupStatus
from app.db.session import async_session
from app.services import group_service, reminders
from app.services.group_creation_registry import is_pending

router = Router()


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_bot_added_to_group(event: ChatMemberUpdated) -> None:
    # JOIN_TRANSITION matches any "not a member -> member-like" change, which
    # includes not just genuine external joins but also the member ->
    # administrator promotion our own userbot flow triggers right after
    # creating a group (app/userbot/actions.py::_promote_assistant_bot) — to
    # the Bot API, that promotion is the *first* status change it's ever seen
    # for the post-migration chat_id, so its "old" status defaults to left,
    # matching this same filter. The is_pending() check right below is what
    # actually filters that (and the pre-migration chat_id's own join) out —
    # see app/services/group_creation_registry.py.
    if event.chat.type not in ("group", "supergroup"):
        return

    if is_pending(event.from_user.id):
        # Це не зовнішнє додавання бота, а власний флоу створення групи
        # (app/services/group_service.py::create_group,
        # app/userbot/actions.py::create_group_with_team) — виконується
        # Telethon-сесією саме цього співробітника, тож Bot API репортує
        # його ж user_id як `from_user` і для "join" базової групи, і для
        # промоуту в адміни в супергрупі після міграції. Обидві ці події —
        # не зовнішнє додавання бота, а group_service.create_group однаково
        # створить авторитетний запис групи в БД і своє власне вітання
        # одразу після завершення цього флоу (див.
        # app/services/group_creation_registry.py).
        return

    async with async_session() as session:
        group = await crud.get_group(session, event.chat.id)
        if group is None:
            bot = event.bot
            if bot is None:
                return
            try:
                await bot.send_message(
                    event.chat.id,
                    "Я підключився до цієї групи. Щоб я міг стежити за повідомленнями клієнтів, "
                    "признач мене адміністратором і познач учасників тегами через /register та /tag.",
                )
            except TelegramMigrateToChat:
                # This chat_id belonged to a basic group that got migrated to a
                # supergroup between Telegram queuing this "bot was added"
                # update and us processing it — happens when our own
                # group-creation flow invites the bot at chat-creation time
                # and then immediately migrates it (see
                # app/userbot/actions.py::create_group_with_team). The real
                # group record (under the post-migration chat_id) is already
                # created by that flow, so there is nothing to register for
                # this stale, now-defunct chat_id.
                return
            except TelegramBadRequest as exc:
                # Same root cause as the TelegramMigrateToChat case above —
                # a stale, now-defunct chat_id after migration — but
                # depending on timing Telegram's Bot API reports it as a
                # plain "PEER_ID_INVALID" bad request instead of the
                # structured migrate-to-chat error. Any other bad-request
                # reason is a real problem and should still surface.
                if "PEER_ID_INVALID" not in exc.message:
                    raise
                return
            await crud.create_group_record(
                session, event.chat.id, event.chat.title or "Без назви", created_by_userbot=False
            )
            await session.commit()


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=LEAVE_TRANSITION))
async def on_bot_removed_from_group(event: ChatMemberUpdated) -> None:
    """Прибирає групу з нашої БД, коли бот перестає бути її учасником.

    LEAVE_TRANSITION (member-like -> left/kicked) покриває і явний кік
    бота з групи, і повне видалення самої групи в Telegram — в обох
    випадках Telegram надсилає боту цю саму транзицію для chat_id групи.
    Без бота в чаті ми однаково більше не можемо стежити за повідомленнями
    чи нагадуваннями, тож застарілий запис сенсу тримати немає (а якщо
    Telegram колись перевикористає цей chat_id для нової групи — краще,
    щоб старого запису вже не було).
    """
    if event.chat.type not in ("group", "supergroup"):
        return

    async with async_session() as session:
        await crud.delete_group(session, event.chat.id)
        await session.commit()


@router.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_member_joined_group(event: ChatMemberUpdated) -> None:
    """Знімає прапорець `pending` із клієнта, коли він дійсно приєднався за лінком.

    `group_service.add_client` (app/services/group_service.py) ставить
    `pending=True`, коли прямий додаток клієнта не вдався через приватність
    і йому лишилось надіслати лінк-запрошення — до цього моменту він ще не
    в чаті, тож Mini App/команда /register не повинні показувати його як
    повноцінного учасника. `router.chat_member` (на відміну від
    `router.my_chat_member` вище) якраз і стежить за змінами статусу *інших*
    учасників чату, а не самого бота, і спрацьовує, коли Telegram підтверджує
    фактичне приєднання.
    """
    if event.chat.type not in ("group", "supergroup"):
        return

    async with async_session() as session:
        tag = await crud.clear_pending(session, event.chat.id, event.new_chat_member.user.id)
        if tag is not None:
            await session.commit()

    if tag is not None:
        await group_service.sync_tag_to_telegram(event.chat.id, event.new_chat_member.user.id, tag)


@router.message(Command("register"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_register(message: Message) -> None:
    async with async_session() as session:
        group = await crud.get_group(session, message.chat.id)
        if group is None:
            await crud.create_group_record(
                session, message.chat.id, message.chat.title or "Без назви", created_by_userbot=False
            )
        else:
            group.status = GroupStatus.ACTIVE
        await session.commit()

    await message.answer(
        "Групу зареєстровано. Познач учасників тегами командою /tag "
        "у відповідь на повідомлення учасника, наприклад: /tag Менеджер"
    )


@router.message(Command("tag"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_tag(message: Message) -> None:
    if not message.reply_to_message:
        await message.answer(
            "Використовуй цю команду у відповідь на повідомлення учасника, якого хочеш позначити."
        )
        return

    text = require_text(message)
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Вкажи тег, наприклад: /tag Менеджер")
        return
    tag_value = parts[1].strip()

    target = require_user(message.reply_to_message)
    async with async_session() as session:
        await crud.get_or_create_user(session, target.id, target.username, target.full_name)
        await crud.add_member_tag(session, message.chat.id, target.id, tag_value)
        await session.commit()

    await group_service.sync_tag_to_telegram(message.chat.id, target.id, tag_value)
    await message.answer(f"{target.full_name} позначено тегом «{tag_value}».")


@router.message(F.chat.type.in_({"group", "supergroup"}), F.text, ~F.text.startswith("/"))
async def track_group_message(message: Message) -> None:
    """Стежить за станом "очікує відповіді" й (пере)планує нагадування для групи.

    Прив'язка нагадування — до часу *цього* повідомлення клієнта
    (`message.date`), а не до моменту старту процесу чи наступного тіку
    планувальника: `reminders.schedule_group_reminder` ставить job рівно на
    `message.date + інтервал`, з `replace_existing=True` — тож повторне
    повідомлення клієнта до того, як спрацювало попереднє нагадування, просто
    зсуває той самий job, а не плодить паралельні (див. reminders.py).
    Відповідь співробітника знімає job зовсім — клієнта більше не чекають.
    """
    async with async_session() as session:
        group = await crud.get_group(session, message.chat.id)
        if group is None:
            return

        sender = require_user(message)
        sender_is_client = await crud.is_client(session, message.chat.id, sender.id)

        if sender_is_client:
            message_at = message.date or datetime.utcnow()
            await crud.mark_awaiting_response(session, message.chat.id, sender.id, message_at)
        else:
            await crud.clear_awaiting_response(session, message.chat.id)

        await session.commit()

    if sender_is_client:
        interval = timedelta(minutes=settings.reminder_interval_minutes)
        reminders.schedule_group_reminder(message.chat.id, message_at + interval)
    else:
        reminders.cancel_group_reminder(message.chat.id)
