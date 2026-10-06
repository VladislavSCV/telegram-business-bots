"""Shared helpers available to all handlers."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, User

from bot.config import Settings

log = logging.getLogger(__name__)


def now(cfg: Settings) -> datetime:
    return datetime.now(ZoneInfo(cfg.timezone)).replace(microsecond=0)


def menu_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")


def contact_row(cfg: Settings) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="💬 Хочу такой бот", url=cfg.owner_contact)] if cfg.owner_contact else []


def keyboard(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[row for row in rows if row])


def who(user: User | None) -> str:
    if user is None:
        return "неизвестный пользователь"
    return f"@{user.username}" if user.username else f"{user.full_name} (id {user.id})"


async def notify_manager(bot: Bot, cfg: Settings, text: str, **kwargs) -> bool:
    if not cfg.manager_chat_id:
        return False
    try:
        await bot.send_message(cfg.manager_chat_id, text, **kwargs)
        return True
    except Exception:
        log.exception("Manager notification failed")
        return False
