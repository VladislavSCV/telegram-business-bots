from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from bot import llm
from bot.booking import LEAD_TIME, SERVICES, free_slots, overlaps
from bot.config import Settings
from bot.leads import lead_card, send_to_crm, temperature
from bot.rag import KnowledgeBase, split_markdown, stem
from bot.storage import Storage

TZ = ZoneInfo("Europe/Moscow")
KB = KnowledgeBase.from_directory(Path(__file__).parent.parent / "knowledge")


@pytest.mark.parametrize(
    ("question", "expected_title"),
    [
        ("Какая гарантия на фурнитуру?", "Гарантия"),
        ("Сколько стоит угловая кухня?", "Цены на кухни"),
        ("Есть ли рассрочка?", "Оплата и рассрочка"),
        ("Какие сроки изготовления", "Сроки"),
        ("замер платный?", "Замер и дизайн-проект"),
        ("из чего делаете столешницы", "Столешницы"),
    ],
)
def test_retrieval_finds_the_right_section(question, expected_title):
    assert KB.search(question)[0][0].title == expected_title


def test_retrieval_returns_nothing_for_unrelated_question():
    assert KB.search("курс биткоина на завтра") == []


def test_stemming_and_chunking():
    assert stem("гарантии") == stem("гарантия") == stem("гарантию")
    chunks = split_markdown("# A\nтекст один\n\n# B\nтекст два", "src")
    assert [c.title for c in chunks] == ["A", "B"]


async def test_llm_fallback_without_key():
    cfg = Settings(_env_file=None)
    fragments = KB.search("гарантия")
    assert (await llm.answer("гарантия?", fragments, cfg)).startswith("Гарантия")
    assert await llm.answer("что-то", [], cfg) == llm.NO_ANSWER


async def test_llm_request_to_yandex():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["url"] = str(request.url)
        sent["headers"] = request.headers
        sent["body"] = request.read().decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "Гарантия 3 года."}}]})

    cfg = Settings(_env_file=None, yandex_api_key="key", yandex_folder_id="folder")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        reply = await llm.answer("гарантия?", KB.search("гарантия"), cfg, client)
    assert reply == "Гарантия 3 года."
    assert sent["url"] == "https://ai.api.cloud.yandex.net/v1/chat/completions"
    assert sent["headers"]["authorization"] == "Api-Key key" and sent["headers"]["openai-project"] == "folder"
    assert "gpt://folder/yandexgpt-5-lite/latest" in sent["body"] and "КОНТЕКСТ" in sent["body"]


async def test_llm_error_falls_back_to_knowledge_base():
    cfg = Settings(_env_file=None, yandex_api_key="key", yandex_folder_id="folder")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        reply = await llm.answer("гарантия?", KB.search("гарантия"), cfg, client)
    assert reply.startswith("Гарантия")


def test_free_slots_respect_bookings_closing_and_lead_time():
    now = datetime(2026, 10, 6, 9, 0, tzinfo=TZ)
    day = date(2026, 10, 6)
    busy = [(datetime(2026, 10, 6, 12, 0, tzinfo=TZ), 60)]
    slots = free_slots(day, 60, busy, now)
    times = [f"{s:%H:%M}" for s in slots]
    assert times[0] == "10:00" and "11:30" not in times and "12:00" not in times and "12:30" not in times
    assert "11:00" in times and "13:00" in times and times[-1] == "20:00"
    late = datetime(2026, 10, 6, 19, 10, tzinfo=TZ)
    assert all(s >= late + LEAD_TIME for s in free_slots(day, 30, [], late))
    assert free_slots(day, 90, [], datetime(2026, 10, 6, 20, 0, tzinfo=TZ)) == []


def test_overlaps():
    start = datetime(2026, 10, 6, 12, 0, tzinfo=TZ)
    assert overlaps(start, 60, start + timedelta(minutes=30), 30)
    assert not overlaps(start, 60, start + timedelta(minutes=60), 30)


def test_storage_prevents_double_booking_and_cancels():
    db = Storage(":memory:")
    now = datetime(2026, 10, 6, 9, 0, tzinfo=TZ)
    start = datetime(2026, 10, 7, 12, 0, tzinfo=TZ)
    first = db.add_booking(1, "a", SERVICES[0].title, "Артём", start, 60, now)
    assert first is not None
    assert db.add_booking(2, "b", SERVICES[0].title, "Артём", start + timedelta(minutes=30), 60, now) is None
    assert db.add_booking(2, "b", SERVICES[0].title, "Илья", start, 60, now) is not None
    assert [b.id for b in db.user_bookings(1, now)] == [first]
    assert not db.cancel_booking(first, user_id=2)
    assert db.cancel_booking(first, user_id=1)
    assert db.add_booking(2, "b", SERVICES[0].title, "Артём", start, 60, now) is not None


def test_reminders_are_due_once():
    db = Storage(":memory:")
    now = datetime(2026, 10, 6, 10, 0, tzinfo=TZ)
    soon = db.add_booking(1, "a", "Стрижка", "Марк", now + timedelta(minutes=90), 60, now)
    db.add_booking(1, "a", "Стрижка", "Марк", now + timedelta(hours=5), 60, now)
    due = db.due_reminders(now + timedelta(hours=2), now)
    assert [b.id for b in due] == [soon]
    db.mark_reminded(soon)
    assert db.due_reminders(now + timedelta(hours=2), now) == []


def test_usage_counter_per_day():
    db = Storage(":memory:")
    assert db.hit(1, "question", date(2026, 10, 6)) == 1
    assert db.hit(1, "question", date(2026, 10, 6)) == 2
    assert db.hit(1, "question", date(2026, 10, 7)) == 1


def test_lead_temperature_and_card():
    assert temperature("b3", "d1") == "hot"
    assert temperature("b1", "d1") == "hot"
    assert temperature("b1", "d2") == "warm"
    assert temperature("b0", "d2") == "warm"
    assert temperature("b3", "d3") == "cold"
    card = lead_card(7, "ivan", "угловая 3 м", "до 150 000 ₽", "1–3 месяца", "+7900", "warm")
    assert "#7" in card and "@ivan" in card and "тёплая" in card


async def test_crm_webhook():
    received = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request.read())
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await send_to_crm("https://crm.example/hook", {"id": 1}, client)
    assert received == [b'{"id":1}'] or received == [b'{"id": 1}']
    assert not await send_to_crm("", {"id": 1})


def test_empty_manager_chat_id_is_none(monkeypatch):
    monkeypatch.setenv("MANAGER_CHAT_ID", "")
    assert Settings(_env_file=None).manager_chat_id is None
    monkeypatch.setenv("MANAGER_CHAT_ID", "42")
    assert Settings(_env_file=None).manager_chat_id == 42
