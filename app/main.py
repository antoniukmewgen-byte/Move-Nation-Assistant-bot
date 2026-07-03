import asyncio
import logging

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.api.app import api
from app.bot.bot_instance import bot, dp
from app.bot.handlers import add_client, connect, group_creation, messages, start, team
from app.config import settings
from app.db.session import engine
from app.logging_config import setup_logging
from app.services.reminders import send_reminders

setup_logging(settings.log_level)
logger = logging.getLogger(__name__)

dp.include_router(start.router)
dp.include_router(connect.router)
dp.include_router(group_creation.router)
dp.include_router(add_client.router)
dp.include_router(team.router)
dp.include_router(messages.router)


async def run_api(server: uvicorn.Server) -> None:
    await server.serve()


async def main() -> None:
    # This entrypoint assumes exactly one instance of the whole process is
    # running at any given time — see README.md, "⚠️ Лише один інстанс".
    # dp.start_polling(bot) below uses Telegram's getUpdates long-polling,
    # which multiple concurrent instances would fight over (lost/duplicated
    # updates), and app/services/telethon_auth.py keeps pending /connect
    # auth state in an in-memory dict scoped to this one process. Do not
    # add `workers=` to the uvicorn.Config below or run this under a
    # multi-replica orchestrator without first moving both of those to
    # shared storage.
    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_reminders, "interval", minutes=settings.reminder_interval_minutes)
    scheduler.start()

    uvicorn_config = uvicorn.Config(api, host=settings.api_host, port=settings.api_port, log_level="info")
    server = uvicorn.Server(uvicorn_config)

    try:
        await asyncio.gather(
            dp.start_polling(bot),
            run_api(server),
        )
    finally:
        logger.info("Завершую роботу: зупиняю планувальник, бота, API та з'єднання з БД…")
        scheduler.shutdown(wait=False)
        server.should_exit = True
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Зупинено користувачем.")
