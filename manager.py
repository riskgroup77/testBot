"""Botni ishga tushirish / to'xtatish (token o'zgarsa qayta ishga tushadi)."""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, User

import handlers
import jobs
import storage
import subscribe
from admin import admin_router
from handlers import RegisterMiddleware, fallback_router, flow_router, main_router

log = logging.getLogger("bot")


async def _safe(coro) -> None:
    """Fondagi ish xato bersa — bot to'xtamaydi, faqat jurnalga yoziladi."""
    try:
        await coro
    except Exception:
        log.exception("Fondagi vazifada xato")


class IgnoreStaleCallback(BaseRequestMiddleware):
    """Bot o'chiq paytda bosilgan tugma kech yetib kelsa, Telegram «query is too old» deydi.
    Bu xato e'tiborsiz qoldiriladi — tugma vazifasi (menyu, test, video) baribir bajariladi."""

    async def __call__(self, make_request, bot, method):
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as exc:
            if isinstance(method, AnswerCallbackQuery) and (
                    "query is too old" in str(exc) or "query ID is invalid" in str(exc)):
                return True
            raise


def _make_bot(token: str) -> Bot:
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.session.middleware(IgnoreStaleCallback())
    return bot


class BotManager:
    def __init__(self) -> None:
        self.dp = Dispatcher()
        self.dp.message.outer_middleware(RegisterMiddleware())
        self.dp.callback_query.outer_middleware(RegisterMiddleware())
        # tartib muhim: menyu → admin (faqat adminga) → test/video → tushunilmagan xabarlar
        self.dp.include_routers(main_router, admin_router, flow_router, fallback_router)
        self.bot: Bot | None = None
        self.me: User | None = None
        self.task: asyncio.Task | None = None
        self.jobs: asyncio.Task | None = None
        self.error: str | None = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    @staticmethod
    async def check_token(token: str) -> User:
        """Tokenni Telegram orqali tekshiradi. Noto'g'ri bo'lsa xato chiqaradi."""
        bot = _make_bot(token)
        try:
            return await bot.get_me()
        finally:
            await bot.session.close()

    async def start(self, token: str) -> User:
        me = await self.check_token(token)
        await self.stop()
        bot = _make_bot(token)
        await bot.delete_webhook(drop_pending_updates=False)
        await bot.set_my_commands([
            BotCommand(command="start", description="🏠 Bosh menyu"),
            BotCommand(command="bekor", description="❌ Bekor qilish"),
        ])
        self.bot, self.me, self.error = bot, me, None
        self.task = asyncio.create_task(self._poll(bot))
        self.jobs = asyncio.create_task(jobs.reveal_loop(bot))
        storage.run(storage.autosetup(bot))     # ombor kanalga birinchi marta ulanish (bir marta)
        asyncio.create_task(_safe(handlers.cleanup_admin_access(bot)))   # eski adminlarning /admin buyrug'i
        asyncio.create_task(_safe(subscribe.startup_check(bot)))         # majburiy obuna kanali tekshiruvi
        log.info("Bot ishga tushdi: @%s", me.username)
        return me

    async def _poll(self, bot: Bot) -> None:
        try:
            await self.dp.start_polling(bot, handle_signals=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = str(exc)
            log.exception("Bot to'xtab qoldi")

    async def stop(self) -> None:
        if self.jobs:
            self.jobs.cancel()
            self.jobs = None
        if self.running:
            try:
                await self.dp.stop_polling()
                await asyncio.wait_for(self.task, timeout=15)
            except (RuntimeError, asyncio.TimeoutError):
                self.task.cancel()
        if self.bot:
            await self.bot.session.close()
        self.bot, self.task = None, None
