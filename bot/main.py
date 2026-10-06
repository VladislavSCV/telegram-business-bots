"""Bot entry point: long polling, shared dependencies, background booking reminders."""

import asyncio
import logging
from datetime import timedelta

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from bot.booking import day_label
from bot.config import Settings, settings
from bot.context import now
from bot.handlers import build_router
from bot.rag import KnowledgeBase
from bot.storage import Storage

log = logging.getLogger(__name__)


def make_bot(cfg: Settings) -> Bot:
    session = AiohttpSession()
    if cfg.telegram_api_url:
        session.api = TelegramAPIServer.from_base(cfg.telegram_api_url.rstrip("/"))
    return Bot(cfg.bot_token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


async def send_reminders(bot: Bot, cfg: Settings, storage: Storage):
    """Real reminders: REMINDER_BEFORE_MINUTES before the visit, once per booking."""
    current = now(cfg)
    for booking in storage.due_reminders(current + timedelta(minutes=cfg.reminder_before_minutes), current):
        try:
            await bot.send_message(
                booking.user_id,
                f"🔔 Напоминание: {booking.service} у мастера {booking.master} — "
                f"{day_label(booking.starts_at.date(), current.date())}, {booking.starts_at:%H:%M}.",
            )
        except Exception:
            log.exception("Reminder for booking %s failed", booking.id)
        storage.mark_reminded(booking.id)


async def reminder_loop(bot: Bot, cfg: Settings, storage: Storage):
    while True:
        try:
            await send_reminders(bot, cfg, storage)
        except Exception:
            log.exception("Reminder loop iteration failed")
        await asyncio.sleep(30)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = settings()
    if not cfg.bot_token:
        raise SystemExit("BOT_TOKEN is not set")
    storage = Storage(cfg.db_path)
    kb = KnowledgeBase.from_directory(cfg.knowledge_dir)
    log.info(
        "Knowledge base: %s fragments; LLM: %s",
        len(kb.chunks),
        "YandexGPT" if cfg.yandex_api_key else "off (answers from the knowledge base)",
    )
    bot = make_bot(cfg)
    dp = Dispatcher(cfg=cfg, db=storage, kb=kb)
    dp.include_router(build_router())
    await bot.set_my_commands(
        [BotCommand(command="start", description="Выбрать демо"), BotCommand(command="menu", description="Меню")]
    )
    reminders = asyncio.create_task(reminder_loop(bot, cfg, storage))
    try:
        await dp.start_polling(bot)
    finally:
        reminders.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
