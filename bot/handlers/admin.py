"""Manager commands (only for MANAGER_CHAT_ID): schedule, leads, CSV export."""

import csv
import io

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from bot.booking import day_label
from bot.config import Settings
from bot.context import now
from bot.leads import TEMPERATURE_LABELS
from bot.storage import Storage

router = Router(name="admin")


def is_manager(message: Message, cfg: Settings) -> bool:
    return bool(cfg.manager_chat_id) and message.from_user is not None and message.from_user.id == cfg.manager_chat_id


@router.message(Command("admin"))
async def admin(message: Message, cfg: Settings, db: Storage):
    if not is_manager(message, cfg):
        return
    upcoming = db.upcoming_bookings(now(cfg), limit=15)
    lines = (
        ["<b>Ближайшие записи</b>"]
        + [
            f"• {day_label(b.starts_at.date(), now(cfg).date())} {b.starts_at:%H:%M} — {b.service}, {b.master}"
            for b in upcoming
        ]
        if upcoming
        else ["Записей пока нет."]
    )
    lines += ["", "/leads — последние заявки", "/export — все записи в CSV"]
    await message.answer("\n".join(lines))


@router.message(Command("leads"))
async def leads(message: Message, cfg: Settings, db: Storage):
    if not is_manager(message, cfg):
        return
    rows = db.recent_leads()
    if not rows:
        await message.answer("Заявок пока нет.")
        return
    text = "\n\n".join(
        f"#{r['id']} {TEMPERATURE_LABELS[r['temperature']]} · {r['created_at'][:16].replace('T', ' ')}\n"
        f"{r['need']} · {r['budget']} · {r['deadline']} · {r['contact']}"
        for r in rows
    )
    await message.answer(text, parse_mode=None)


@router.message(Command("export"))
async def export(message: Message, cfg: Settings, db: Storage):
    if not is_manager(message, cfg):
        return
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["id", "дата", "время", "услуга", "мастер", "статус", "клиент"])
    for b in db.all_bookings():
        writer.writerow(
            [
                b.id,
                f"{b.starts_at:%d.%m.%Y}",
                f"{b.starts_at:%H:%M}",
                b.service,
                b.master,
                b.status,
                f"@{b.username}" if b.username else b.user_id,
            ]
        )
    # UTF-8 with BOM opens correctly in Excel.
    await message.answer_document(
        BufferedInputFile(buffer.getvalue().encode("utf-8-sig"), filename="bookings.csv"), caption="Все записи"
    )
