"""Online booking: service -> master -> day -> time -> confirm; list and cancel own bookings."""

import asyncio
import logging
from datetime import date, datetime

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message

from bot.booking import MASTERS, SERVICES, booking_days, day_label, free_slots, service_by_code
from bot.config import Settings
from bot.context import keyboard, menu_button, notify_manager, now, who
from bot.storage import Storage

log = logging.getLogger(__name__)
router = Router(name="booking")
_background: set[asyncio.Task] = set()

INTRO = (
    "📅 <b>Онлайн-запись в барбершоп «Blade»</b> <i>(демо, компания вымышленная)</i>\n\n"
    "Клиент сам выбирает услугу, мастера и свободное время — без звонков и переписки. "
    "Занятые слоты не показываются, двойная запись невозможна. "
    "Клиенту приходит напоминание, администратор видит расписание и выгружает его в таблицу."
)


def services_keyboard():
    rows = [
        [InlineKeyboardButton(text=f"{s.title} · {s.duration_min} мин · {s.price} ₽", callback_data=f"bk:svc:{s.code}")]
        for s in SERVICES
    ]
    return keyboard(*rows, [InlineKeyboardButton(text="🗓 Мои записи", callback_data="bk:my"), menu_button()])


async def open_booking(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(INTRO)
    await message.answer("Выберите услугу:", reply_markup=services_keyboard())


@router.callback_query(F.data == "bk:start")
async def restart(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("Выберите услугу:", reply_markup=services_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("bk:svc:"))
async def choose_service(callback: CallbackQuery, state: FSMContext):
    service = service_by_code(callback.data.split(":", 2)[2])
    if service is None:
        await callback.answer("Услуга не найдена")
        return
    await state.update_data(service=service.code)
    rows = [[InlineKeyboardButton(text=name, callback_data=f"bk:m:{i}")] for i, name in enumerate(MASTERS)]
    await callback.message.edit_text(
        f"Услуга: <b>{service.title}</b>\nВыберите мастера:",
        reply_markup=keyboard(*rows, [InlineKeyboardButton(text="⬅️ Назад", callback_data="bk:start")]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bk:m:"))
async def choose_master(callback: CallbackQuery, state: FSMContext, cfg: Settings):
    index = int(callback.data.split(":")[2])
    if not 0 <= index < len(MASTERS):
        await callback.answer()
        return
    await state.update_data(master=MASTERS[index])
    current = now(cfg)
    buttons = [
        InlineKeyboardButton(text=day_label(day, current.date()), callback_data=f"bk:d:{day.isoformat()}")
        for day in booking_days(current)
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    await callback.message.edit_text(
        f"Мастер: <b>{MASTERS[index]}</b>\nВыберите день:",
        reply_markup=keyboard(*rows, [InlineKeyboardButton(text="⬅️ Назад", callback_data="bk:start")]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bk:d:"))
async def choose_day(callback: CallbackQuery, state: FSMContext, cfg: Settings, db: Storage):
    data = await state.get_data()
    service = service_by_code(data.get("service", ""))
    master = data.get("master")
    if service is None or master is None:
        await callback.answer("Начните запись заново")
        return
    day = date.fromisoformat(callback.data.split(":", 2)[2])
    busy = [(b.starts_at, b.duration_min) for b in db.bookings_for_master(master, day)]
    slots = free_slots(day, service.duration_min, busy, now(cfg))
    if not slots:
        await callback.answer("На этот день свободного времени нет — выберите другой", show_alert=True)
        return
    await state.update_data(day=day.isoformat())
    buttons = [InlineKeyboardButton(text=f"{s:%H:%M}", callback_data=f"bk:t:{s:%H%M}") for s in slots]
    rows = [buttons[i : i + 4] for i in range(0, len(buttons), 4)]
    await callback.message.edit_text(
        f"{service.title} · {master} · {day_label(day, now(cfg).date())}\nСвободное время:",
        reply_markup=keyboard(
            *rows, [InlineKeyboardButton(text="⬅️ Другой день", callback_data=f"bk:m:{MASTERS.index(master)}")]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bk:t:"))
async def choose_time(callback: CallbackQuery, state: FSMContext, cfg: Settings):
    data = await state.get_data()
    service = service_by_code(data.get("service", ""))
    if service is None or "day" not in data:
        await callback.answer("Начните запись заново")
        return
    hhmm = callback.data.split(":", 2)[2]
    starts_at = datetime.fromisoformat(f"{data['day']}T{hhmm[:2]}:{hhmm[2:]}").replace(tzinfo=now(cfg).tzinfo)
    await state.update_data(starts_at=starts_at.isoformat())
    await callback.message.edit_text(
        f"Проверьте запись:\n\n<b>{service.title}</b> — {service.price} ₽\n"
        f"Мастер: {data['master']}\nКогда: {day_label(starts_at.date(), now(cfg).date())}, {starts_at:%H:%M}",
        reply_markup=keyboard(
            [InlineKeyboardButton(text="✅ Записаться", callback_data="bk:ok")],
            [InlineKeyboardButton(text="⬅️ Выбрать другое время", callback_data=f"bk:d:{data['day']}")],
        ),
    )
    await callback.answer()


async def _demo_reminder(bot: Bot, chat_id: int, text: str, delay: int):
    await asyncio.sleep(delay)
    try:
        await bot.send_message(chat_id, text)
    except Exception:
        log.exception("Demo reminder failed")


@router.callback_query(F.data == "bk:ok")
async def confirm(callback: CallbackQuery, state: FSMContext, bot: Bot, cfg: Settings, db: Storage):
    data = await state.get_data()
    service = service_by_code(data.get("service", ""))
    if service is None or "starts_at" not in data:
        await callback.answer("Начните запись заново")
        return
    starts_at = datetime.fromisoformat(data["starts_at"])
    user = callback.from_user
    booking_id = db.add_booking(
        user.id, user.username, service.title, data["master"], starts_at, service.duration_min, now(cfg)
    )
    if booking_id is None:
        await callback.answer("Это время только что заняли — выберите другое", show_alert=True)
        return
    await state.clear()
    when = f"{day_label(starts_at.date(), now(cfg).date())}, {starts_at:%H:%M}"
    await callback.message.edit_text(
        f"✅ Вы записаны!\n\n{service.title} · {data['master']}\n{when}\n\n"
        f"Напомню за {cfg.reminder_before_minutes // 60} ч до визита. "
        f"<i>Для демонстрации пример напоминания придёт через {cfg.demo_reminder_seconds} сек.</i>",
        reply_markup=keyboard(
            [InlineKeyboardButton(text="🗓 Мои записи", callback_data="bk:my")],
            [InlineKeyboardButton(text="➕ Ещё запись", callback_data="bk:start"), menu_button()],
        ),
    )
    await callback.answer("Готово!")
    await notify_manager(
        bot, cfg, f"📅 Новая запись #{booking_id}: {service.title}, {data['master']}, {when} — {who(user)}"
    )
    task = asyncio.create_task(
        _demo_reminder(
            bot,
            callback.message.chat.id,
            f"🔔 Напоминание (демо): вы записаны на «{service.title}» к мастеру {data['master']} — {when}. "
            "Если планы изменились, отмените запись в «Мои записи».",
            cfg.demo_reminder_seconds,
        )
    )
    _background.add(task)
    task.add_done_callback(_background.discard)


@router.callback_query(F.data == "bk:my")
async def my_bookings(callback: CallbackQuery, cfg: Settings, db: Storage):
    bookings = db.user_bookings(callback.from_user.id, now(cfg))
    if not bookings:
        await callback.message.answer(
            "У вас нет предстоящих записей.",
            reply_markup=keyboard([InlineKeyboardButton(text="➕ Записаться", callback_data="bk:start")]),
        )
        await callback.answer()
        return
    for b in bookings:
        await callback.message.answer(
            f"🗓 {b.service} · {b.master}\n{day_label(b.starts_at.date(), now(cfg).date())}, {b.starts_at:%H:%M}",
            reply_markup=keyboard([InlineKeyboardButton(text="❌ Отменить", callback_data=f"bk:cancel:{b.id}")]),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("bk:cancel:"))
async def cancel(callback: CallbackQuery, bot: Bot, cfg: Settings, db: Storage):
    booking_id = int(callback.data.rsplit(":", 1)[1])
    if db.cancel_booking(booking_id, callback.from_user.id):
        await callback.message.edit_text("Запись отменена, время снова свободно.")
        await notify_manager(bot, cfg, f"↩️ Запись #{booking_id} отменена клиентом {who(callback.from_user)}")
    await callback.answer()
