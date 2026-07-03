import logging
from datetime import datetime

from app.bot.bot_instance import bot
from app.db import crud
from app.db.session import async_session

logger = logging.getLogger(__name__)


async def send_reminders() -> None:
    async with async_session() as session:
        groups = await crud.get_groups_awaiting_response(session)

        for group in groups:
            recipients = await crud.get_notify_recipients(session, group.id)
            group.last_reminder_at = datetime.utcnow()

            for user in recipients:
                try:
                    await bot.send_message(
                        user.id,
                        f"Нагадування: у групі «{group.title}» є повідомлення клієнта без відповіді.",
                    )
                except Exception:
                    logger.warning("Не вдалося надіслати нагадування user_id=%s", user.id)

        await session.commit()
