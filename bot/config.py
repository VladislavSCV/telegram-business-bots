from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = ""
    # Bot API mirror for servers where api.telegram.org is blocked (e.g. a Cloudflare Worker).
    telegram_api_url: str = ""
    # Chat that receives leads, bookings and demo visit notices (the bot owner / manager).
    manager_chat_id: int | None = None
    # Link for the "Хочу такой бот" button, e.g. https://t.me/your_username. Hidden when empty.
    owner_contact: str = ""
    notify_demo_visits: bool = True

    # Yandex AI Studio (OpenAI-compatible API). Without a key the assistant answers with the best
    # matching knowledge-base fragment instead of a generated reply.
    yandex_api_key: str = ""
    yandex_folder_id: str = ""
    yandex_model: str = "yandexgpt-5-lite/latest"
    llm_base_url: str = "https://ai.api.cloud.yandex.net/v1"
    llm_timeout_seconds: float = 60

    # Optional CRM handoff: every lead is POSTed here as JSON (amoCRM/Bitrix24 webhook, n8n, Make, ...).
    crm_webhook_url: str = ""

    db_path: str = "data/bot.db"
    knowledge_dir: str = "knowledge"
    timezone: str = "Europe/Moscow"
    # Protects the LLM budget: questions per user per day.
    daily_question_limit: int = 30
    # After a booking the demo sends a sample reminder after this delay (real reminders: 2 h before).
    demo_reminder_seconds: int = 60
    reminder_before_minutes: int = 120


@lru_cache
def settings() -> Settings:
    return Settings()
