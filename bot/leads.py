"""Lead qualification for the sales assistant and CRM handoff."""

import logging

import httpx

log = logging.getLogger(__name__)

BUDGETS = {"b1": "до 150 000 ₽", "b2": "150 000 – 300 000 ₽", "b3": "больше 300 000 ₽", "b0": "пока не знаю"}
DEADLINES = {"d1": "в течение месяца", "d2": "1–3 месяца", "d3": "просто присматриваюсь"}
TEMPERATURE_LABELS = {"hot": "🔥 горячая", "warm": "🟡 тёплая", "cold": "❄️ холодная"}


def temperature(budget_code: str, deadline_code: str) -> str:
    """Simple, explainable scoring the sales team can trust: budget is known and the client wants
    to start within 3 months -> hot; only browsing -> cold; everything else -> warm."""
    if deadline_code == "d3":
        return "cold"
    if budget_code in {"b2", "b3"} or (budget_code == "b1" and deadline_code == "d1"):
        return "hot"
    return "warm"


def lead_card(
    lead_id: int, username: str | None, need: str, budget: str, deadline: str, contact: str, temp: str
) -> str:
    who = f"@{username}" if username else "без username"
    return (
        f"📥 Новая заявка #{lead_id} · {TEMPERATURE_LABELS[temp]}\n"
        f"Клиент: {who}\n"
        f"Что нужно: {need}\n"
        f"Бюджет: {budget}\n"
        f"Сроки: {deadline}\n"
        f"Контакт: {contact}\n\n"
        "В рабочем проекте заявка сразу создаётся сделкой в amoCRM / Битрикс24 со всеми полями."
    )


async def send_to_crm(url: str, payload: dict, client: httpx.AsyncClient | None = None) -> bool:
    if not url:
        return False
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return True
    except httpx.HTTPError:
        log.exception("CRM webhook failed")
        return False
    finally:
        if own_client:
            await client.aclose()
