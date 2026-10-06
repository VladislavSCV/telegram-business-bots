"""End-to-end handler tests: a real aiogram Dispatcher fed with updates; Telegram calls are recorded."""

import asyncio
import itertools
from datetime import datetime, timedelta
from pathlib import Path
from typing import get_args

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendDocument, SendMessage
from aiogram.types import Chat, Message, Update

from bot.booking import free_slots, service_by_code
from bot.config import Settings
from bot.context import now
from bot.handlers import build_router
from bot.rag import KnowledgeBase
from bot.storage import Storage

USER, MANAGER = 1001, 42
_ids = itertools.count(1)


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        returning = method.__returning__
        if returning is bool or bool in get_args(returning):
            return True
        chat_id = getattr(method, "chat_id", USER) or USER
        return Message(
            message_id=next(_ids),
            date=datetime.now(),
            chat=Chat(id=chat_id, type="private"),
            text=getattr(method, "text", None),
        )

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def close(self):
        pass


# Routers are module-level singletons and can be attached to one dispatcher only.
DISPATCHER = Dispatcher()
DISPATCHER.include_router(build_router())
KB = KnowledgeBase.from_directory(Path(__file__).parent.parent / "knowledge")


@pytest.fixture
def env():
    cfg = Settings(
        _env_file=None,
        bot_token="123:TEST",
        manager_chat_id=MANAGER,
        demo_reminder_seconds=0,
        owner_contact="https://t.me/owner",
    )
    session = RecordingSession()
    bot = Bot(cfg.bot_token, session=session)
    db = Storage(":memory:")
    DISPATCHER.workflow_data.update(cfg=cfg, db=db, kb=KB)
    DISPATCHER.fsm.storage = MemoryStorage()
    return bot, DISPATCHER, session, db, cfg


def user(uid=USER):
    return {"id": uid, "is_bot": False, "first_name": "Test", "username": f"user{uid}"}


async def send(env, text=None, uid=USER, contact=None):
    bot, dp, *_ = env
    message = {"message_id": next(_ids), "date": 0, "chat": {"id": uid, "type": "private"}, "from": user(uid)}
    if text is not None:
        message["text"] = text
        if text.startswith("/"):
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    if contact:
        message["contact"] = {"phone_number": contact, "first_name": "Test", "user_id": uid}
    await dp.feed_update(
        bot, Update.model_validate({"update_id": next(_ids), "message": message}, context={"bot": bot})
    )


async def press(env, data, uid=USER):
    bot, dp, *_ = env
    callback = {
        "id": str(next(_ids)),
        "from": user(uid),
        "chat_instance": "x",
        "data": data,
        "message": {"message_id": next(_ids), "date": 0, "chat": {"id": uid, "type": "private"}, "text": "…"},
    }
    await dp.feed_update(
        bot, Update.model_validate({"update_id": next(_ids), "callback_query": callback}, context={"bot": bot})
    )


def texts(session, to=None, kind=(SendMessage, EditMessageText)):
    return [c.text for c in session.calls if isinstance(c, kind) and (to is None or getattr(c, "chat_id", None) == to)]


def alerts(session):
    return [c.text for c in session.calls if isinstance(c, AnswerCallbackQuery) and c.text]


async def test_menu_and_deep_links(env):
    _, _, session, _, _ = env
    await send(env, "/start")
    assert "демо-бот" in texts(session, USER)[-1]
    await send(env, "/start sales")
    assert "AI-ассистент студии кухонь" in texts(session, USER)[-1]
    await send(env, "/start booking")
    assert "Выберите услугу" in texts(session, USER)[-1]
    visits = texts(session, MANAGER)
    assert any("AI-ассистент продаж" in t for t in visits) and any("Онлайн-запись" in t for t in visits)
    await send(env, "просто текст без сценария")
    assert "Выберите, что хотите попробовать" in texts(session, USER)[-1]


async def test_sales_answers_from_knowledge_base_and_collects_lead(env):
    _, _, session, db, _ = env
    await send(env, "/start sales")
    await send(env, "Есть ли рассрочка?")
    assert "Рассрочка 0%" in texts(session, USER)[-1]
    await send(env, "Сколько стоит пицца с ананасами?")
    assert "Менеджер ответит" in texts(session, USER)[-1] or "Цены" in texts(session, USER)[-1]
    await press(env, "lead:start")
    await send(env, "угловая, 3 метра, эмаль")
    await press(env, "lead:budget:b2")
    await press(env, "lead:deadline:d2")
    await send(env, contact="+79990000000")
    lead = db.recent_leads()[0]
    assert (lead["need"], lead["temperature"], lead["contact"]) == ("угловая, 3 метра, эмаль", "hot", "+79990000000")
    card = texts(session, MANAGER)[-1]
    assert "Новая заявка" in card and "горячая" in card and "+79990000000" in card
    assert "Заявка принята" in "\n".join(texts(session, USER)[-3:])


async def test_daily_question_limit(env):
    bot, dp, session, db, cfg = env
    cfg.daily_question_limit = 1
    await send(env, "/start sales")
    await send(env, "гарантия?")
    await send(env, "сроки?")
    assert "лимит вопросов" in texts(session, USER)[-1]


async def test_booking_flow_with_reminder_my_bookings_and_cancel(env):
    _, _, session, db, cfg = env
    await send(env, "/start booking")
    await press(env, "bk:svc:cut")
    await press(env, "bk:m:0")
    tomorrow = (now(cfg) + timedelta(days=1)).date()
    slot = free_slots(tomorrow, service_by_code("cut").duration_min, [], now(cfg))[0]
    await press(env, f"bk:d:{tomorrow.isoformat()}")
    await press(env, f"bk:t:{slot:%H%M}")
    assert "Проверьте запись" in texts(session, USER)[-1]
    await press(env, "bk:ok")
    await asyncio.sleep(0.05)  # demo reminder task (delay 0)
    booking = db.user_bookings(USER, now(cfg))[0]
    assert (booking.master, booking.starts_at) == ("Артём", slot)
    assert any("Вы записаны" in t for t in texts(session, USER))
    assert any("Напоминание (демо)" in t for t in texts(session, USER))
    assert any("Новая запись" in t for t in texts(session, MANAGER))

    # The same slot is no longer offered and cannot be booked twice.
    other = 2002
    await send(env, "/start booking", uid=other)
    await press(env, "bk:svc:cut", uid=other)
    await press(env, "bk:m:0", uid=other)
    await press(env, f"bk:d:{tomorrow.isoformat()}", uid=other)
    await press(env, f"bk:t:{slot:%H%M}", uid=other)
    await press(env, "bk:ok", uid=other)
    assert "только что заняли" in alerts(session)[-1]

    await press(env, "bk:my")
    assert "Артём" in texts(session, USER)[-1]
    await press(env, f"bk:cancel:{booking.id}", uid=other)
    assert db.user_bookings(USER, now(cfg))  # someone else cannot cancel it
    await press(env, f"bk:cancel:{booking.id}")
    assert not db.user_bookings(USER, now(cfg))
    assert any("отменена" in t for t in texts(session, MANAGER))


async def test_admin_commands_only_for_manager(env):
    _, _, session, db, cfg = env
    db.add_booking(USER, "u", "Мужская стрижка", "Илья", now(cfg) + timedelta(days=1), 60, now(cfg))
    before = len(session.calls)
    await send(env, "/admin")
    await send(env, "/export")
    assert len(session.calls) == before  # silent for regular users
    await send(env, "/admin", uid=MANAGER)
    assert "Ближайшие записи" in texts(session, MANAGER)[-1]
    await send(env, "/export", uid=MANAGER)
    assert isinstance(session.calls[-1], SendDocument)
