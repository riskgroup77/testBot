"""Majburiy obuna: o'quvchi kanalga a'zo bo'lmaguncha botdan foydalana olmaydi.

Bot kanalda ADMIN bo'lishi shart — aks holda a'zolikni tekshira olmaydi
(bunday holatda hech kim bloklanib qolmasligi uchun tekshiruv o'tkazib yuboriladi).
"""
import json
import logging
import time

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import database

log = logging.getLogger("bot")
_cache: dict[int, tuple[bool, float]] = {}
OK_TTL, NO_TTL = 300, 10        # a'zo bo'lsa 5 daqiqa, bo'lmasa 10 soniya eslab qolinadi


def channel() -> dict | None:
    """Yoqilgan bo'lsa {'id', 'title', 'link'}, aks holda None."""
    if database.setting("sub_on", "0") != "1":
        return None
    raw = database.setting("sub_channel")
    return json.loads(raw) if raw else None


def saved_channel() -> dict | None:
    raw = database.setting("sub_channel")
    return json.loads(raw) if raw else None


def reset_cache() -> None:
    _cache.clear()


async def is_member(bot: Bot, user_id: int, fresh: bool = False) -> bool:
    ch = channel()
    if not ch:
        return True
    cached = _cache.get(user_id)
    if cached and not fresh and time.monotonic() - cached[1] < (OK_TTL if cached[0] else NO_TTL):
        return cached[0]
    try:
        m = await bot.get_chat_member(ch["id"], user_id)
        ok = m.status in ("member", "administrator", "creator") or (
            m.status == "restricted" and getattr(m, "is_member", False))
    except Exception as exc:
        # Bot kanalda admin bo'lmasa Telegram a'zolar ro'yxatini bermaydi.
        # Bunda hech kim nohaq bloklanmasligi uchun o'tkazib yuboramiz, adminlarga esa ogohlantirish boradi.
        log.warning("Obunani tekshirib bo'lmadi (bot kanalda adminmi?): %s", exc)
        await _alert_admins(bot, str(exc))
        _cache[user_id] = (True, time.monotonic() - OK_TTL + 60)   # 1 daqiqadan keyin qayta urinadi
        return True
    _cache[user_id] = (ok, time.monotonic())
    return ok


async def problem(bot: Bot) -> str | None:
    """Majburiy obuna ishlay oladimi? Muammo bo'lsa — tushuntirish matni, aks holda None."""
    ch = saved_channel()
    if not ch:
        return None
    try:
        me = await bot.me()
        m = await bot.get_chat_member(ch["id"], me.id)
    except Exception as exc:
        return f"bot kanal a’zolarini ko‘ra olmayapti ({exc}) — botni kanalga <b>admin</b> qiling"
    if m.status not in ("administrator", "creator"):
        return "bot kanalda <b>admin emas</b> — kanal sozlamalaridan botni admin qiling"
    return None


_alerted = 0.0


async def _alert_admins(bot: Bot, reason: str) -> None:
    """Bot kanalda admin bo'lmasa — bosh adminlarga ogohlantirish (6 soatda ko'pi bilan 1 marta)."""
    global _alerted
    if time.monotonic() - _alerted < 6 * 3600 and _alerted:
        return
    _alerted = time.monotonic()
    import access
    ch = saved_channel() or {}
    for uid in access.owner_ids():
        try:
            await bot.send_message(
                uid,
                "⚠️ <b>Majburiy obuna ishlamayapti!</b>\n\n"
                f"Bot «{ch.get('title', 'kanal')}» kanalida <b>admin emas</b>, shuning uchun "
                "o‘quvchilarning obunasini tekshira olmayapti — hozircha hamma botdan foydalana oladi.\n\n"
                "👉 Kanal → Sozlamalar → Administratorlar → botni qo‘shing "
                "(hech qanday huquq berish shart emas).")
        except Exception:
            pass


async def startup_check(bot: Bot) -> None:
    """Bot ishga tushganda: obuna yoqilgan bo'lsa, bot kanalda adminligini tekshiradi."""
    if not channel():
        return
    reason = await problem(bot)
    if reason:
        log.warning("Majburiy obuna: %s", reason)
        await _alert_admins(bot, reason)
    else:
        log.info("Majburiy obuna faol: %s", (saved_channel() or {}).get("title"))


def prompt() -> tuple[str, InlineKeyboardMarkup]:
    ch = channel() or {}
    text = ("📢 <b>Botdan foydalanish uchun kanalimizga a’zo bo‘ling!</b>\n\n"
            f"👉 {ch.get('title', 'Kanal')}\n\n"
            "A’zo bo‘lgach, «✅ A’zo bo‘ldim» tugmasini bosing.")
    rows = []
    if ch.get("link"):
        rows.append([InlineKeyboardButton(text="📢 Kanalga o‘tish", url=ch["link"])])
    rows.append([InlineKeyboardButton(text="✅ A’zo bo‘ldim", callback_data="subcheck")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)
