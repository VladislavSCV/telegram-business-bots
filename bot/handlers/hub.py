"""Entry point: /start with deep links (?start=sales, ?start=booking) and the demo menu."""

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message

from bot.config import Settings
from bot.context import contact_row, keyboard, notify_manager, now, who
from bot.handlers.booking import open_booking
from bot.handlers.sales import open_sales
from bot.storage import Storage

router = Router(name="hub")

INTRO = (
    "👋 Это демо-бот разработчика: здесь можно попробовать два готовых решения для бизнеса.\n\n"
    "🤖 <b>AI-ассистент продаж</b> — отвечает клиентам по базе знаний компании 24/7, "
    "квалифицирует заявку и передаёт её менеджеру.\n"
    "📅 <b>Онлайн-запись</b> — выбор услуги, мастера и времени, напоминания клиенту, "
    "расписание для администратора.\n\n"
    "Компании в демо вымышленные: можно смело нажимать всё подряд."
)
SCENARIOS = {"sales": "AI-ассистент продаж", "booking": "Онлайн-запись"}


def menu_keyboard(cfg: Settings):
    return keyboard(
        [InlineKeyboardButton(text="🤖 AI-ассистент продаж", callback_data="open:sales")],
        [InlineKeyboardButton(text="📅 Онлайн-запись", callback_data="open:booking")],
        contact_row(cfg),
    )


async def note_visit(bot: Bot, cfg: Settings, db: Storage, user, scenario: str):
    """Tell the owner that someone opened a demo (once per user and scenario per day)."""
    if not cfg.notify_demo_visits or user is None or user.id == cfg.manager_chat_id:
        return
    if db.hit(user.id, f"visit:{scenario}", now(cfg).date()) == 1:
        await notify_manager(bot, cfg, f"👀 Демо «{SCENARIOS.get(scenario, scenario)}» открыл {who(user)}")


@router.message(CommandStart())
async def start(message: Message, command: CommandObject, state: FSMContext, bot: Bot, cfg: Settings, db: Storage):
    await state.clear()
    scenario = (command.args or "").strip()
    await note_visit(bot, cfg, db, message.from_user, scenario or "menu")
    if scenario == "sales":
        await open_sales(message, state, cfg)
    elif scenario == "booking":
        await open_booking(message, state)
    else:
        await message.answer(INTRO, reply_markup=menu_keyboard(cfg))


@router.message(Command("menu", "help"))
async def menu(message: Message, state: FSMContext, cfg: Settings):
    await state.clear()
    await message.answer(INTRO, reply_markup=menu_keyboard(cfg))


@router.callback_query(F.data == "menu")
async def menu_callback(callback: CallbackQuery, state: FSMContext, cfg: Settings):
    await state.clear()
    await callback.message.answer(INTRO, reply_markup=menu_keyboard(cfg))
    await callback.answer()


@router.callback_query(F.data.startswith("open:"))
async def open_scenario(callback: CallbackQuery, state: FSMContext, bot: Bot, cfg: Settings, db: Storage):
    scenario = callback.data.split(":", 1)[1]
    await state.clear()
    await note_visit(bot, cfg, db, callback.from_user, scenario)
    if scenario == "sales":
        await open_sales(callback.message, state, cfg)
    else:
        await open_booking(callback.message, state)
    await callback.answer()


@router.message(StateFilter(None), F.text)
async def fallback(message: Message, cfg: Settings):
    """Free text outside a scenario: point to the demos instead of staying silent."""
    await message.answer("Выберите, что хотите попробовать:", reply_markup=menu_keyboard(cfg))
