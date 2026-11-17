"""Fayllarni Telegram'ga yuborish: bir marta yuklanadi, keyin file_id orqali tez yuboriladi."""
import asyncio
import logging
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, Message

import database

log = logging.getLogger("media")
_locks: dict[str, asyncio.Lock] = {}
UPLOAD_TIMEOUT = 1800  # katta video sekin internetda ham yuklansin


def _key(path: Path) -> str:
    st = path.stat()
    return f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}"


def is_cached(path: Path) -> bool:
    return database.get_file_id(_key(path)) is not None


async def send_media(bot: Bot, chat_id: int, kind: str, path: str | None = None,
                     file_id: str | None = None, filename: str | None = None, **kw) -> Message:
    """Fayl kompyuterda bo'lsa — o'shani (keshlab), bo'lmasa Telegram file_id ni yuboradi."""
    from content import abs_path
    if path and abs_path(path).exists():
        return await send_file(bot, chat_id, abs_path(path), kind, filename=filename, **kw)
    if not file_id:
        raise FileNotFoundError(path or "fayl yo'q")
    if kind == "document":
        return await bot.send_document(chat_id, document=file_id, **kw)
    return await bot.send_video(chat_id, video=file_id, **kw)


def needs_upload(path: str | None, file_id: str | None) -> bool:
    from content import abs_path
    return bool(path and abs_path(path).exists() and not is_cached(abs_path(path)))


async def send_file(bot: Bot, chat_id: int, path: Path, kind: str, filename: str | None = None, **kw) -> Message:
    """kind: 'document' yoki 'video'."""
    key = _key(path)
    method = bot.send_document if kind == "document" else bot.send_video
    field = "document" if kind == "document" else "video"

    file_id = database.get_file_id(key)
    if file_id:
        try:
            return await method(chat_id, **{field: file_id}, **kw)
        except TelegramBadRequest:
            log.warning("file_id eskirgan, qayta yuklanadi: %s", path.name)

    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        file_id = database.get_file_id(key)  # boshqa foydalanuvchi shu payt yuklagan bo'lishi mumkin
        if file_id:
            return await method(chat_id, **{field: file_id}, **kw)
        log.info("Telegram'ga yuklanmoqda: %s (%.1f MB)", path.name, path.stat().st_size / 1048576)
        msg = await method(chat_id, **{field: FSInputFile(path, filename=filename or path.name)},
                           request_timeout=UPLOAD_TIMEOUT, **kw)
        sent = msg.document if kind == "document" else msg.video
        if sent:
            database.set_file_id(key, sent.file_id)
        return msg
