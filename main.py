"""Akmaljon yordamchi bot — ishga tushirish nuqtasi.

    python main.py              # sozlash sahifasi + bot
    python main.py --no-browser # brauzerni avtomatik ochmasdan
"""
import asyncio
import logging
import sys
import webbrowser

from aiogram.exceptions import TelegramNetworkError
from aiohttp import web

import content
import database
from config import LOG_PATH, SETUP_HOST, SETUP_PORT, SETUP_URL, load_token
from manager import BotManager
from setup_page import create_app


def setup_logging() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s", "%H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    file = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[console, file])
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


async def main() -> None:
    log = logging.getLogger("main")
    database.init()
    content.seed()
    manager = BotManager()

    runner = None
    if SETUP_PORT:
        runner = web.AppRunner(create_app(manager))
        await runner.setup()
        try:
            await web.TCPSite(runner, SETUP_HOST, SETUP_PORT).start()
            log.info("Sozlash sahifasi: %s", SETUP_URL)
        except OSError as exc:      # port band bo'lsa ham bot ishlayveradi
            log.warning("Sozlash sahifasini ochib bo'lmadi (%s) — bot baribir ishlaydi", exc)

    token = load_token()
    if token:
        # Internet vaqtincha uzilgan bo'lsa — taslim bo'lmasdan qayta urinadi (10 s, 20 s, ... 60 s gacha)
        delay = 10
        while True:
            try:
                await manager.start(token)
                break
            except (TelegramNetworkError, asyncio.TimeoutError, OSError) as exc:
                log.warning("Telegram'ga ulanib bo'lmadi (%s) — %s soniyadan keyin qayta urinaman", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
            except Exception as exc:
                log.error("Saqlangan token bilan ishga tushmadi: %s", exc)
                break
    else:
        log.info("Token topilmadi — sozlash sahifasida kiriting: %s", SETUP_URL)
        if SETUP_PORT and "--no-browser" not in sys.argv:
            webbrowser.open(SETUP_URL)

    try:
        await asyncio.Event().wait()
    finally:
        await manager.stop()
        if runner:
            await runner.cleanup()


if __name__ == "__main__":
    setup_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger("main").info("Bot to'xtatildi.")
