from aiogram import Router

from bot.handlers import admin, booking, hub, sales


def build_router() -> Router:
    router = Router()
    # Order matters: admin commands and state-specific handlers before generic text handlers.
    router.include_routers(admin.router, hub.router, booking.router, sales.router)
    return router
