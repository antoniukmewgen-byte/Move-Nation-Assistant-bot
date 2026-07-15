import contextlib
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramMigrateToChat
from aiogram.filters import Command
from aiogram.filters.chat_member_updated import JOIN_TRANSITION, LEAVE_TRANSITION, ChatMemberUpdatedFilter
from aiogram.types import ChatMemberUpdated, Message
from telethon.errors import FloodWaitError

from app.bot.guards import require_user
from app.config import settings
from app.db import crud
from app.db.models import User
from app.db.session import async_session
from app.services import group_service, realtime, reminders
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

    newly_registered_actor_id: int | None = None

    async with async_session() as session:
        group = await crud.get_group(session, event.chat.id)
        if group is None:
            bot = event.bot
            if bot is None:
                return
            try:
                # A silent existence check, not a message to the group —
                # we don't want the bot posting anything when it's added.
                # Still serves the same purpose the old welcome message's
                # network call used to: catching a stale, already-migrated
                # chat_id before registering a bogus duplicate group record
                # (see the except blocks below).
                await bot.get_chat(event.chat.id)
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

            # Без цього group_members лишається порожнім і crud.get_groups_for_user
            # (JOIN на group_members) взагалі не покаже цю групу в списку Mini
            # App — а без неї в списку недосяжна й тиха кнопка синхронізації
            # (GroupOut.needs_sync), бо вона рендериться лише для вже видимих
            # груп. Тож реєструємо того, хто додав бота, учасником одразу —
            # АЛЕ лише якщо це вже оформлений співробітник (є Role у БД),
            # той самий staff-гейт, що й у cmd_sync нижче. group_members —
            # єдина авторизація на /groups та /members (get_verified_user_id
            # у app/api/deps.py взагалі не дивиться на Role), тож без цього
            # гейту будь-який випадковий Telegram-користувач, додавши бота в
            # довільний чужий чат, сам собі видав би group_members-рядок і
            # разом з ним повний доступ через Mini App (перегляд/додавання
            # клієнтів, видалення учасників, видалення самої групи, sync) —
            # раніше цей шлях був недоступний без Role чи /connect. Людина
            # без Role тут просто нічого не отримує; group залишається
            # незареєстрованою для неї, доки її не звірить справжній
            # співробітник через /sync чи цю саму кнопку.
            actor = event.from_user
            db_user = await crud.get_or_create_user(session, actor.id, actor.username, actor.full_name)
            if db_user.role is not None:
                await crud.add_member_tag(session, event.chat.id, actor.id, db_user.role.value)
                newly_registered_actor_id = actor.id

            await session.commit()

    if newly_registered_actor_id is not None:
        await realtime.notify_users([newly_registered_actor_id], {"type": "groups_changed"})


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=LEAVE_TRANSITION))
async def on_bot_removed_from_group(event: ChatMemberUpdated) -> None:
    """Реагує на видалення бота — з групи чи (блокування) в особистих.

    LEAVE_TRANSITION (member-like -> left/kicked) покриває обидва випадки —
    Bot API надсилає боту той самий апдейт `my_chat_member`, різниться лише
    `event.chat.type`:

    - Групи/супергрупи: і явний кік бота з групи, і повне видалення самої
      групи в Telegram. Без бота в чаті ми однаково більше не можемо
      стежити за повідомленнями чи нагадуваннями, тож застарілий запис
      сенсу тримати немає (а якщо Telegram колись перевикористає цей
      chat_id для нової групи — краще, щоб старого запису вже не було).
    - Особисті: людина натиснула "Зупинити та заблокувати бота". На
      відміну від виходу з групи (де можливий випадковий клік чи технічний
      збій на боці Telegram), заблокувати бота в приваті можна лише
      окремою явною дією через системне меню чату — це усвідомлений крок,
      тож трактуємо це як самостійне звільнення співробітника:
      `group_service.offboard_staff` кидає з усіх поточних груп і видаляє
      рядок `users` повністю. Це єдиний спосіб звільнення — керівник не
      може зробити це за когось іншого (жодного окремого API для цього
      немає). `StaffNotFoundError` просто ігноруємо — заблокувати бота
      може будь-хто (клієнт, випадковий співрозмовник), а не лише
      зареєстрований співробітник.
    """
    if event.chat.type in ("group", "supergroup"):
        async with async_session() as session:
            # Captured before the delete — same reasoning as
            # app/api/routes/groups.py::remove_group.
            member_ids = [m.user_id for m in await crud.get_group_members(session, event.chat.id)]
            await crud.delete_group(session, event.chat.id)
            await session.commit()
        await realtime.notify_users(member_ids, {"type": "groups_changed"})
        return

    if event.chat.type == "private":
        with contextlib.suppress(group_service.StaffNotFoundError):
            await group_service.offboard_staff(event.chat.id)


@router.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_member_joined_group(event: ChatMemberUpdated) -> None:
    """Тегує учасника, коли Telegram підтверджує його фактичний вступ у групу.

    Два шляхи, залежно від того, чи проводили людину через `/add_client`
    заздалегідь:

    - Якщо є pending-запис (`group_service.add_client` ставить
      `pending=True`, коли прямий додаток не вдався через приватність і
      клієнту лишилось надіслати лінк-запрошення — до цього моменту він ще
      не в чаті, тож Mini App не повинен показувати його як повноцінного
      учасника) — знімаємо pending і синхронізуємо вже призначений тег.
    - Якщо pending-запису нема (людину додали в обхід бота — наприклад,
      узяли лінк групи напряму в Telegram) — тегуємо її з нуля тим самим
      правилом, що й повний /sync (`group_service.tag_new_member`).

    `router.chat_member` (на відміну від `router.my_chat_member` вище)
    якраз і стежить за змінами статусу *інших* учасників чату, а не самого
    бота, і спрацьовує, коли Telegram підтверджує фактичне приєднання —
    але лише якщо бот сам є адміністратором цього чату (інакше Telegram
    цю подію боту взагалі не надсилає).
    """
    if event.chat.type not in ("group", "supergroup"):
        return

    new_member = event.new_chat_member.user

    async with async_session() as session:
        tag = await crud.clear_pending(session, event.chat.id, new_member.id)
        if tag is not None:
            await session.commit()

    if tag is not None:
        await group_service.sync_tag_to_telegram(event.chat.id, new_member.id, tag)
        await realtime.notify_group(event.chat.id, {"type": "members_changed", "group_id": event.chat.id})
        return

    # Ніякого pending-запису не було — цю людину не проводили через
    # /add_client заздалегідь (наприклад, лінк групи взяли напряму в
    # Telegram, а не той, що видав бот). Тегуємо все одно, за тим самим
    # правилом, що й повний /sync (group_service.sync_group): роль із БД,
    # якщо є, інакше CLIENT_TAG.
    await group_service.tag_new_member(event.chat.id, new_member.id, new_member.username, new_member.full_name)


@router.chat_member(ChatMemberUpdatedFilter(member_status_changed=LEAVE_TRANSITION))
async def on_member_left_group(event: ChatMemberUpdated) -> None:
    """Прибирає учасника (тег) з групи, коли він сам вийшов або його кікнули.

    Дзеркальний обробник до `on_member_joined_group` вище, але для протилежної
    транзиції: без нього рядок `GroupMember` для такого користувача лишався
    б назавжди — /tag і Mini App продовжували б показувати його як
    учасника групи, хоча в самому Telegram-чаті його вже нема. Прибирає лише
    сам тег/членство — саму групу видаляти не треба (це робить окремо
    `on_bot_removed_from_group`, коли з чату видаляють самого бота).
    """
    if event.chat.type not in ("group", "supergroup"):
        return

    async with async_session() as session:
        removed = await crud.remove_member(session, event.chat.id, event.old_chat_member.user.id)
        if removed:
            await session.commit()

    if removed:
        left_user_id = event.old_chat_member.user.id
        await realtime.notify_user(left_user_id, {"type": "groups_changed"})
        await realtime.notify_group(event.chat.id, {"type": "members_changed", "group_id": event.chat.id})


@router.message(Command("sync"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_sync(message: Message) -> None:
    """Повна звірка складу групи — замінює колишні /register + /tag.

    Доступна лише співробітнику з призначеною посадою (звичайний клієнт чи
    ще не оформлений співробітник не повинен мати змоги перетегувати чужу
    групу). Сканує ПОВНИЙ список учасників через MTProto-сесію того, хто
    викликав команду (Bot API бачить лише адмінів), і для кожного виставляє
    актуальний тег — його роль із БД або CLIENT_TAG, якщо ролі нема — а тих,
    кого скан більше не бачить, прибирає з групи в нашій БД (див.
    app/services/group_service.py::sync_group).
    """
    actor = require_user(message)
    async with async_session() as session:
        db_user = await session.get(User, actor.id)
        if db_user is None or db_user.role is None:
            await message.answer("Цю команду може використати лише співробітник із призначеною посадою.")
            return

    await message.answer("Сканую учасників групи…")

    try:
        updated, removed = await group_service.sync_group(actor.id, message.chat.id, message.chat.title or "Без назви")
    except group_service.NotConnectedError:
        await message.answer("Спочатку підключи свій Telegram-акаунт: /connect")
        return
    except FloodWaitError as exc:
        await message.answer(f"Забагато запитів до Telegram, спробуй через {exc.seconds} с.")
        return
    except group_service.GroupSyncFailedError:
        await message.answer("Не вдалося просканувати учасників групи. Спробуй ще раз пізніше.")
        return

    if not updated and not removed:
        await message.answer("Усе вже актуально, змін не було.")
        return

    parts = []
    if updated:
        parts.append(f"оновлено тегів: {updated}")
    if removed:
        parts.append(f"прибрано з групи: {removed}")
    await message.answer("Готово, " + ", ".join(parts) + ".")


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
            await crud.mark_awaiting_response(session, message.chat.id, sender.id, message_at, message.text or "")
        else:
            await crud.clear_awaiting_response(session, message.chat.id)

        await session.commit()

    # The "очікує" badge lives on the group row in each member's /groups
    # list, not on a member row — so this is groups_changed, not
    # members_changed, unlike the other hooks in this file.
    await realtime.notify_group(message.chat.id, {"type": "groups_changed"})

    if sender_is_client:
        interval = timedelta(minutes=settings.reminder_interval_minutes)
        reminders.schedule_group_reminder(message.chat.id, message_at + interval)
    else:
        reminders.cancel_group_reminder(message.chat.id)
