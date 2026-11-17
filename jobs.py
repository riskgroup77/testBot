"""Fon vazifalari: test muddati tugaganda o'quvchilarga tahlil yuborish."""
import asyncio
import json
import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

import access
import backup
import content
import database
import timeutil
from grading import STANDARD_MAX, fmt_num, grade_letter, split_answers
import certificate
from handlers import answer_lines, send_certificate
from ui import ikb

log = logging.getLogger("jobs")
CHECK_EVERY = 30  # soniya


async def _send(bot: Bot, chat_id: int, text: str, kb=None) -> bool:
    for _ in range(2):
        try:
            await bot.send_message(chat_id, text, reply_markup=kb)
            return True
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
        except TelegramForbiddenError:
            database.set_bot_blocked(chat_id, True)
            return False
        except Exception as exc:
            log.warning("%s ga yuborilmadi: %s", chat_id, exc)
            return False
    return False


async def reveal_test(bot: Bot, test_id: str) -> int:
    """Muddat tugagan testning tahlilini har bir yechgan o'quvchiga yuboradi."""
    t = content.get(test_id)
    database.set_revealed(test_id)               # avval belgilaymiz — ikki marta yuborilmasin
    if not t:
        return 0
    best = database.best_results(t.id)
    video_kb = ikb([("🎬 Video tahlilni ko‘rish", f"vid:{t.id}")]) if t.videos else None
    sent = 0
    for place, r in enumerate(best, start=1):
        if database.is_banned(r["telegram_id"]):
            continue
        answers, open_answers = split_answers(json.loads(r["answers"]))
        pct = round(r["score"] / r["max_score"] * 100) if r["max_score"] else 0
        text = (
            f"📢 <b>«{escape(t.title)}» testi yakunlandi!</b>\n\n"
            f"🏆 Sizning eng yaxshi natijangiz: <b>{fmt_num(r['score'])} / {fmt_num(r['max_score'])}</b> ({pct}%)\n"
            f"🎖 O‘rningiz: <b>{place}</b> ({len(best)} o‘quvchidan)\n"
            f"✅ To‘g‘ri: {r['correct']} · ❌ Xato: {r['wrong']}\n"
            + (f"🎖 Daraja: <b>{grade_letter(r['score'])}</b>\n"
               if round(t.max_score) == STANDARD_MAX and grade_letter(r["score"]) else "")
            + "\n<b>📝 Javoblaringiz va to‘g‘ri javoblar:</b>\n"
            + "\n".join(answer_lines(t, answers, open_answers=open_answers))
        )
        if len(text) > 4000:
            text = text[:3990] + "\n…"
        if await _send(bot, r["telegram_id"], text, video_kb):
            sent += 1
            grade = certificate.eligible(t, r["score"])
            if grade:
                fallback = " ".join(x for x in (r["first_name"], r["last_name"]) if x) or "O‘quvchi"
                await send_certificate(bot, r["telegram_id"], fallback, t, r["id"], grade)
        await asyncio.sleep(0.05)
    log.info("Tahlil yuborildi: %s — %s/%s o'quvchi", t.id, sent, len(best))
    for admin in access.admin_chat_ids():
        await _send(bot, admin,
                    f"⏹ <b>«{escape(t.title)}» testi yakunlandi.</b>\n"
                    f"📢 {sent} ta o‘quvchiga natija va to‘g‘ri javoblar yuborildi.",
                    ikb([("🏆 Reyting", f"a:rank:{t.id}"), ("📥 Excel", f"a:x:{t.id}")]))
    return sent


async def reveal_loop(bot: Bot) -> None:
    while True:
        try:
            for test_id in database.tests_to_reveal(timeutil.iso(timeutil.now())):
                await reveal_test(bot, test_id)
            if backup.auto_due():
                database.set_setting("last_auto_backup", timeutil.iso(timeutil.now()))
                await backup.make_and_send(bot, access.owner_ids(), "kunlik avtomatik")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Fon vazifasida xato")
        await asyncio.sleep(CHECK_EVERY)
