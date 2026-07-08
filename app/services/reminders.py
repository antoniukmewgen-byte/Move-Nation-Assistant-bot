import logging
from datetime import datetime, timedelta
from html import escape

from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.bot_instance import bot
from app.config import settings
from app.db import crud
from app.db.session import async_session
from app.services.scheduler import scheduler

logger = logging.getLogger(__name__)

REMINDER_STICKER_FILE_ID = "CAACAgIAAxkBAAFOjUZqTjtcAAHM3yK2EAMDk7k6Ao0F0iMAAiGpAAJihjhK3WFS7vThE6w8BA"


def _job_id(group_id: int) -> str:
    return f"reminder:{group_id}"


def _group_deep_link(group_id: int) -> str | None:
    """Telegram-посилання, яке відкриває саме цю групу в застосунку.

    `t.me/c/<internal_id>` працює лише для супергруп/каналів, чий chat_id
    Telegram кодує як `-100<internal_id>` (усі групи, створені через нашого
    userbot — див. app/userbot/actions.py::create_group_with_team — саме такі).
    Для звичайних (не-супер) груп без публічного username надійного способу
    послатись на конкретний чат немає, тож для них повертаємо None, і виклик
    просто не додає кнопку, замість надсилати неробочий лінк.
    """
    raw = str(group_id)
    if not raw.startswith("-100"):
        return None
    return f"https://t.me/c/{raw.removeprefix('-100')}"


def schedule_group_reminder(group_id: int, run_at: datetime) -> None:
    """Ставить (або переставляє) нагадування для групи рівно на `run_at`.

    На відміну від старого підходу (один глобальний job, що тікав кожні
    REMINDER_INTERVAL_MINUTES від старту процесу, з ручним гейтингом у
    send_reminders), тепер у кожної групи свій job з детермінованим часом
    спрацювання: момент повідомлення клієнта (чи попереднього нагадування)
    плюс інтервал. `id=_job_id(group_id)` + `replace_existing=True` означає,
    що повторний виклик (наприклад, клієнт написав ще раз до того, як
    спрацювало попереднє нагадування — див. track_group_message) просто
    зсуває вже існуючий job на новий момент, а не плодить паралельні.
    `misfire_grace_time=None` прибирає для цього job'а типовий ліміт
    APScheduler на запізнення спрацювання — потрібно для
    recover_pending_reminders(), де `run_at` може бути вже в минулому,
    якщо процес простоював довше за інтервал нагадувань.
    """
    scheduler.add_job(
        send_group_reminder,
        "date",
        run_date=run_at,
        args=[group_id],
        id=_job_id(group_id),
        replace_existing=True,
        misfire_grace_time=None,
    )


def cancel_group_reminder(group_id: int) -> None:
    """Знімає заплановане нагадування для групи (співробітник відповів клієнту)."""
    job_id = _job_id(group_id)
    if scheduler.get_job(job_id) is not None:
        scheduler.remove_job(job_id)


async def send_group_reminder(group_id: int) -> None:
    """Виконується планувальником рівно в момент, на який запланований job.

    Той факт, що job спрацював, сам по собі не гарантує, що нагадування й
    досі доречне — тому тут та сама гейт-перевірка, що була в старому
    глобальному send_reminders: якщо з моменту останньої значущої події
    (нагадування, якщо воно вже було в цьому циклі, інакше — самого
    повідомлення клієнта) минуло менше інтервалу, пропускаємо. Це підстраховка
    на випадок гонки (стан групи змінився між плануванням і спрацюванням job'а)
    чи відновленого після рестарту job'а (recover_pending_reminders), а не
    типовий шлях — у типовому шляху job і так спрацьовує точно вчасно.

    Якщо група й далі чекає на відповідь після відправки — одразу планує
    наступне нагадування через schedule_group_reminder.
    """
    interval = timedelta(minutes=settings.reminder_interval_minutes)
    now = datetime.utcnow()

    async with async_session() as session:
        group = await crud.get_group(session, group_id)
        if group is None or not group.awaiting_response:
            return

        reference_at = group.last_reminder_at or group.last_message_at
        if reference_at is not None and now - reference_at < interval:
            return

        recipients = await crud.get_notify_recipients(session, group_id)
        group.last_reminder_at = now

        reply_markup = None
        deep_link = _group_deep_link(group_id)
        if deep_link is not None:
            builder = InlineKeyboardBuilder()
            builder.button(text="↪️ Перейти в групу", url=deep_link)
            reply_markup = builder.as_markup()

        for user in recipients:
            try:
                await bot.send_sticker(user.id, REMINDER_STICKER_FILE_ID)
                await bot.send_message(
                    user.id,
                    f"🔔👀 Клієнт у групі «<b>{escape(group.title)}</b>» досі чекає на відповідь!\n"
                    "💬 Не змушуй його чекати довше 🙏",
                    reply_markup=reply_markup,
                )
            except Exception:
                logger.exception("Не вдалося надіслати нагадування user_id=%s", user.id)

        await session.commit()

    schedule_group_reminder(group_id, now + interval)


async def recover_pending_reminders() -> None:
    """Перевстановлює job'и нагадувань для груп, що вже чекали на відповідь.

    Job'и APScheduler живуть лише в пам'яті процесу — при рестарті всі
    заплановані нагадування губляться, хоча стан групи
    (`awaiting_response`/`last_message_at`/`last_reminder_at`) у БД лишається
    актуальним. Викликається один раз при старті (app/main.py), щоб для
    кожної такої групи запланувати job на момент останньої значущої події
    плюс інтервал — той самий, що й був би, якби процес не перезапускався.
    Якщо цей момент вже минув, job все одно спрацює одразу (див.
    `misfire_grace_time=None` у schedule_group_reminder).
    """
    interval = timedelta(minutes=settings.reminder_interval_minutes)

    async with async_session() as session:
        groups = await crud.get_groups_awaiting_response(session)
        pending = [(group.id, group.last_reminder_at or group.last_message_at) for group in groups]

    for group_id, reference_at in pending:
        run_at = (reference_at + interval) if reference_at is not None else datetime.utcnow()
        schedule_group_reminder(group_id, run_at)
