import logging
from datetime import datetime, timedelta
from html import escape

from app.bot.bot_instance import bot
from app.config import settings
from app.db import crud
from app.db.session import async_session

logger = logging.getLogger(__name__)

REMINDER_STICKER_FILE_ID = "CAACAgIAAxkBAAFOjUZqTjtcAAHM3yK2EAMDk7k6Ao0F0iMAAiGpAAJihjhK3WFS7vThE6w8BA"


async def send_reminders() -> None:
    """Надсилає нагадування по групах, де клієнт досі чекає на відповідь.

    Планувальник (app/main.py) тікає раз на REMINDER_INTERVAL_MINUTES, але
    прив'язаний до моменту старту процесу, а не до того, коли саме прийшло
    повідомлення клієнта — тому без ручного гейтингу перше нагадування могло
    прилетіти вже за хвилину після повідомлення (якщо тік стався невдовзі
    після нього), а повторні летіли б на кожен тік без жодного стримування.
    Тому рахуємо для кожної групи час від останньої значущої події
    (нагадування, якщо воно вже було в цьому циклі, інакше — саме повідомлення
    клієнта) і шлемо нове, лише якщо минуло не менше інтервалу. last_reminder_at
    скидається в crud.mark_awaiting_response при кожному новому повідомленні
    клієнта, тож новий цикл завжди чекає повний інтервал з нуля.
    """
    interval = timedelta(minutes=settings.reminder_interval_minutes)
    now = datetime.utcnow()

    async with async_session() as session:
        groups = await crud.get_groups_awaiting_response(session)

        for group in groups:
            reference_at = group.last_reminder_at or group.last_message_at
            if reference_at is not None and now - reference_at < interval:
                continue

            recipients = await crud.get_notify_recipients(session, group.id)
            group.last_reminder_at = now

            for user in recipients:
                try:
                    await bot.send_sticker(user.id, REMINDER_STICKER_FILE_ID)
                    await bot.send_message(
                        user.id,
                        f"🔔👀 Клієнт у групі «<b>{escape(group.title)}</b>» досі чекає на відповідь!\n"
                        "💬 Не змушуй його чекати довше 🙏",
                    )
                except Exception:
                    logger.warning("Не вдалося надіслати нагадування user_id=%s", user.id)

        await session.commit()
