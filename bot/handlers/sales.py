"""AI sales assistant: answers from the knowledge base (RAG) and qualifies leads."""

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from bot import llm
from bot.config import Settings
from bot.context import contact_row, keyboard, menu_button, notify_manager, now
from bot.leads import BUDGETS, DEADLINES, lead_card, send_to_crm, temperature
from bot.rag import KnowledgeBase
from bot.storage import Storage

router = Router(name="sales")
MAX_QUESTION_CHARS = 800

INTRO = (
    "🤖 <b>AI-ассистент студии кухонь «Форма»</b> <i>(демо, компания вымышленная)</i>\n\n"
    "Задайте вопрос, как задал бы клиент. Например:\n"
    "• Сколько стоит угловая кухня?\n"
    "• Какие сроки изготовления?\n"
    "• Есть ли рассрочка?\n"
    "• Какая гарантия на фурнитуру?\n\n"
    "Ассистент отвечает только по базе знаний компании и не выдумывает цены. "
    "Когда клиент готов — собирает заявку и передаёт менеджеру."
)


class Sales(StatesGroup):
    chatting = State()
    need = State()
    budget = State()
    deadline = State()
    contact = State()


def sales_keyboard(cfg: Settings):
    return keyboard(
        [InlineKeyboardButton(text="📝 Оставить заявку", callback_data="lead:start")],
        [menu_button(), *contact_row(cfg)],
    )


async def open_sales(message: Message, state: FSMContext, cfg: Settings):
    await state.set_state(Sales.chatting)
    await message.answer(INTRO, reply_markup=sales_keyboard(cfg))


@router.message(Sales.chatting, F.text)
async def question(message: Message, bot: Bot, cfg: Settings, db: Storage, kb: KnowledgeBase):
    text = (message.text or "").strip()
    if len(text) > MAX_QUESTION_CHARS:
        await message.answer("Слишком длинное сообщение — сформулируйте вопрос короче, пожалуйста.")
        return
    if db.hit(message.from_user.id, "question", now(cfg).date()) > cfg.daily_question_limit:
        await message.answer(
            "На сегодня лимит вопросов в демо исчерпан. Можно оставить заявку — менеджер ответит.",
            reply_markup=sales_keyboard(cfg),
        )
        return
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    fragments = kb.search(text)
    reply = await llm.answer(text, fragments, cfg)
    await message.answer(reply, reply_markup=sales_keyboard(cfg), parse_mode=None)


# --- lead qualification ---------------------------------------------------------------------


@router.callback_query(F.data == "lead:start")
async def lead_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Sales.need)
    await callback.message.answer(
        "Отлично, соберу заявку за 4 вопроса.\n<i>Это демо: данные увидит только разработчик бота, "
        "можно ввести вымышленные.</i>\n\n1/4. Какая кухня нужна? Например: «угловая, 3 метра, фасады эмаль»."
    )
    await callback.answer()


@router.message(Sales.need, F.text)
async def lead_need(message: Message, state: FSMContext):
    await state.update_data(need=(message.text or "")[:300])
    await state.set_state(Sales.budget)
    await message.answer(
        "2/4. Ориентировочный бюджет?",
        reply_markup=keyboard(
            *[
                [InlineKeyboardButton(text=label, callback_data=f"lead:budget:{code}")]
                for code, label in BUDGETS.items()
            ]
        ),
    )


@router.callback_query(Sales.budget, F.data.startswith("lead:budget:"))
async def lead_budget(callback: CallbackQuery, state: FSMContext):
    code = callback.data.rsplit(":", 1)[1]
    if code not in BUDGETS:
        await callback.answer()
        return
    await state.update_data(budget=code)
    await state.set_state(Sales.deadline)
    await callback.message.edit_text(f"2/4. Бюджет: {BUDGETS[code]}")
    await callback.message.answer(
        "3/4. Когда планируете установку?",
        reply_markup=keyboard(
            *[
                [InlineKeyboardButton(text=label, callback_data=f"lead:deadline:{code}")]
                for code, label in DEADLINES.items()
            ]
        ),
    )
    await callback.answer()


@router.callback_query(Sales.deadline, F.data.startswith("lead:deadline:"))
async def lead_deadline(callback: CallbackQuery, state: FSMContext):
    code = callback.data.rsplit(":", 1)[1]
    if code not in DEADLINES:
        await callback.answer()
        return
    await state.update_data(deadline=code)
    await state.set_state(Sales.contact)
    await callback.message.edit_text(f"3/4. Сроки: {DEADLINES[code]}")
    await callback.message.answer(
        "4/4. Как с вами связаться? Нажмите кнопку, чтобы отправить номер, или напишите удобный контакт.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📱 Отправить номер", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )
    await callback.answer()


@router.message(Sales.contact, F.contact | F.text)
async def lead_contact(message: Message, state: FSMContext, bot: Bot, cfg: Settings, db: Storage):
    contact = message.contact.phone_number if message.contact else (message.text or "")[:100]
    data = await state.get_data()
    budget_code, deadline_code = data.get("budget", "b0"), data.get("deadline", "d3")
    temp = temperature(budget_code, deadline_code)
    user = message.from_user
    created = now(cfg)
    lead_id = db.add_lead(
        user.id,
        user.username,
        data.get("need", ""),
        BUDGETS[budget_code],
        DEADLINES[deadline_code],
        contact,
        temp,
        created,
    )
    await notify_manager(
        bot,
        cfg,
        lead_card(
            lead_id, user.username, data.get("need", ""), BUDGETS[budget_code], DEADLINES[deadline_code], contact, temp
        ),
        parse_mode=None,
    )
    await send_to_crm(
        cfg.crm_webhook_url,
        {
            "id": lead_id,
            "source": "telegram",
            "telegram_user_id": user.id,
            "username": user.username,
            "need": data.get("need", ""),
            "budget": BUDGETS[budget_code],
            "deadline": DEADLINES[deadline_code],
            "contact": contact,
            "temperature": temp,
            "created_at": created.isoformat(),
        },
    )
    await state.set_state(Sales.chatting)
    await message.answer(
        "✅ Заявка принята! Менеджер свяжется с вами в рабочее время.", reply_markup=ReplyKeyboardRemove()
    )
    await message.answer(
        "Так это работает в демо: заявка с оценкой «горячая/тёплая/холодная» мгновенно пришла менеджеру "
        "в Telegram. В рабочем проекте она ещё и создаётся сделкой в CRM.\n\nМожно продолжить задавать вопросы.",
        reply_markup=sales_keyboard(cfg),
    )
