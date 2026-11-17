"""🗄 Ombor kanal — testlarning PDF, video va ma'lumotlari Telegram kanalga ham joylanadi.

Nima uchun:
  • Fayllar Telegram'da doim saqlanib turadi (kompyuter/server buzilsa ham yo'qolmaydi).
  • Bot o'quvchiga faylni yubora olmasa (file_id eskirgan, fayl o'chgan, bot tokeni almashgan),
    faylni shu kanaldan avtomatik qayta oladi.
  • Admin barcha testlarni bir joyda — kanalda ko'rib turadi.

Xavfsizlik: kanal OCHIQ (username bor) bo'lsa, javoblar kaliti kanalga YOZILMAYDI —
aks holda o'quvchilar kanalni topib, javoblarni ko'rib olishi mumkin.
Bot kanalda admin (post yozish huquqi bilan) bo'lishi shart.
"""
import asyncio
import json
import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import Message

import config
import database
import timeutil
from content import Test, category_path

log = logging.getLogger("storage")
PAUSE = 1.2            # kanalga postlar orasidagi tanaffus (Telegram cheklovi)
_lock = asyncio.Lock()  # postlar ketma-ket, aralashib ketmasin


def channel() -> dict | None:
    """Yoqilgan bo'lsa {'id', 'title', 'username'}, aks holda None."""
    if database.setting("storage_on", "0") != "1":
        return None
    return saved()


def saved() -> dict | None:
    raw = database.setting("storage_channel")
    return json.loads(raw) if raw else None


def link(ch: dict | None) -> str:
    if not ch:
        return ""
    return f"https://t.me/{ch['username']}" if ch.get("username") else ch.get("invite", "") or ""


async def connect(bot: Bot, ident) -> dict:
    """Kanalni tekshiradi (bot admin va post yoza oladimi). Xato bo'lsa ValueError (o'zbekcha)."""
    try:
        chat = await bot.get_chat(ident)
    except Exception as exc:
        raise ValueError(f"Kanal topilmadi yoki bot unga qo‘shilmagan ({exc}).")
    if getattr(chat, "type", "channel") not in ("channel", "supergroup"):
        raise ValueError("Bu kanal emas.")
    me = await bot.me()
    try:
        member = await bot.get_chat_member(chat.id, me.id)
    except Exception as exc:
        raise ValueError(f"Bot kanal a’zolarini ko‘ra olmadi ({exc}).")
    if member.status != "administrator" and member.status != "creator":
        raise ValueError("Bot bu kanalda admin emas. Kanal sozlamalaridan botni admin qiling.")
    if member.status == "administrator" and getattr(member, "can_post_messages", True) is False:
        raise ValueError("Botda kanalga post yozish huquqi yo‘q. Admin huquqlaridan «Xabar joylash» ni yoqing.")
    info = {"id": chat.id, "title": chat.title or str(chat.id), "username": chat.username or ""}
    database.set_setting("storage_channel", json.dumps(info))
    database.set_setting("storage_on", "1")
    database.set_setting("storage_error", "")
    log.info("Ombor kanal ulandi: %s (%s)", info["title"], info["id"])
    return info


async def _refresh(bot: Bot, ch: dict) -> dict:
    """Kanal ochiq/yopiqligini har safar yangidan tekshiradi (admin keyin o'zgartirishi mumkin)."""
    try:
        chat = await bot.get_chat(ch["id"])
        if (chat.username or "") != ch.get("username", ""):
            ch = {**ch, "username": chat.username or "", "title": chat.title or ch["title"]}
            database.set_setting("storage_channel", json.dumps(ch))
    except Exception:
        pass
    return ch


async def _call(fn, *a, **kw):
    for attempt in range(3):
        try:
            return await fn(*a, **kw)
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 1)
    return await fn(*a, **kw)


def _tag(t_id: str) -> str:
    return f"#{t_id}"


def header_text(t: Test, title: str, public: bool, actor: str = "") -> str:
    from grading import fmt_num, key_text, options_text, points_text
    lines = [
        f"{title} · {_tag(t.id)}",
        f"📘 <b>{escape(t.title)}</b>",
        f"📂 {category_path(t.category)}\n",
        f"❓ {len(t.key)} ta variantli" + (f" + {len(t.open_key)} ta yozma javob" if t.open_key else "")
        + f" · {fmt_num(t.max_score)} ball",
        f"🔤 Variantlar: {options_text(t.options)}",
        f"🎯 Ballar: {points_text(t.points)}",
    ]
    rules = []
    if t.starts_at:
        rules.append(f"ochiladi {timeutil.fmt(t.starts_at)}")
    if t.ends_at:
        rules.append(f"yopiladi {timeutil.fmt(t.ends_at)}")
    if t.max_attempts:
        rules.append(f"{t.max_attempts} urinish")
    if rules:
        lines.append("⚙️ " + " · ".join(rules))
    if t.open_questions:
        lines.append(f"📝 {escape(t.open_questions)}")
    lines.append(f"🎬 Videolar: {len(t.videos)} ta · Holati: {'🟢 faol' if t.active else '⚪ yashirin'}")
    if public:
        lines.append("\n🔒 <i>Kalit ochiq kanalga yozilmadi (xavfsizlik). U botda saqlangan.</i>")
    else:
        lines.append(f"\n🔑 Kalit: <tg-spoiler>{escape(key_text(t.key, t.open_key))}</tg-spoiler>")
    lines.append(f"\n🕒 {timeutil.fmt(timeutil.now())}" + (f" · 👤 {escape(actor)}" if actor else ""))
    return "\n".join(lines)


async def _post_pdf(bot: Bot, ch: dict, t: Test) -> None:
    import media
    from export import safe_filename
    if not t.has_pdf:
        return
    msg = await _call(media.send_media, bot, ch["id"], "document", t.pdf_path, t.pdf_file_id,
                      filename=t.pdf_name if t.pdf_name.lower().endswith(".pdf") else f"{safe_filename(t.title)}.pdf",
                      caption=f"📄 <b>{escape(t.title)}</b> — test PDF (o‘quvchilar nusxasi)\n{_tag(t.id)}")
    database.storage_add(t.id, "pdf", "", ch["id"], msg.message_id)


async def _post_video(bot: Bot, ch: dict, t: Test, v, i: int, n: int) -> None:
    import media
    msg = await _call(media.send_media, bot, ch["id"], v.kind, v.path, v.file_id,
                      caption=f"🎬 <b>{escape(t.title)}</b> — video tahlil"
                              + (f" · {i}/{n}" if n > 1 else "") + f"\n{escape(v.title)} · {_tag(t.id)}",
                      **({"supports_streaming": True} if v.kind == "video" else {}))
    database.storage_add(t.id, "video", str(v.id), ch["id"], msg.message_id)


async def archive_test(bot: Bot, t: Test, title: str = "🆕 <b>Yangi test</b>", actor: str = "") -> bool:
    """Testni to'liq joylaydi: ma'lumot + PDF + barcha videolar."""
    ch = channel()
    if not ch:
        return False
    async with _lock:
        ch = await _refresh(bot, ch)
        msg = await _call(bot.send_message, ch["id"], header_text(t, title, bool(ch.get("username")), actor))
        database.storage_add(t.id, "header", "", ch["id"], msg.message_id)
        await asyncio.sleep(PAUSE)
        await _post_pdf(bot, ch, t)
        for i, v in enumerate(t.videos, start=1):
            await asyncio.sleep(PAUSE)
            await _post_video(bot, ch, t, v, i, len(t.videos))
    log.info("Kanalga joylandi: %s (%s)", t.id, t.title)
    return True


async def archive_pdf(bot: Bot, t: Test, actor: str = "") -> None:
    ch = channel()
    if not ch:
        return
    async with _lock:
        await _call(bot.send_message, ch["id"], f"📄 <b>PDF almashtirildi</b> · {_tag(t.id)}\n📘 {escape(t.title)}"
                    + (f"\n👤 {escape(actor)}" if actor else ""))
        await asyncio.sleep(PAUSE)
        await _post_pdf(bot, ch, t)


async def archive_video(bot: Bot, t: Test, v) -> None:
    ch = channel()
    if not ch:
        return
    async with _lock:
        await _post_video(bot, ch, t, v, len(t.videos), len(t.videos))


async def note(bot: Bot, t: Test | None, text: str, with_key: bool = False) -> None:
    """Qisqa xabar: kalit o'zgardi, test o'chirildi va h.k."""
    ch = channel()
    if not ch:
        return
    async with _lock:
        ch = await _refresh(bot, ch)
        body = text
        if t is not None:
            body += f" · {_tag(t.id)}\n📘 {escape(t.title)}"
            if with_key:
                from grading import key_text
                body += ("\n🔒 <i>Kalit ochiq kanalga yozilmadi.</i>" if ch.get("username")
                         else f"\n🔑 Yangi kalit: <tg-spoiler>{escape(key_text(t.key, t.open_key))}</tg-spoiler>")
        msg = await _call(bot.send_message, ch["id"], body)
        if t is not None:
            database.storage_add(t.id, "note", "", ch["id"], msg.message_id)


def run(coro) -> None:
    """Fonda bajaradi — admin kutib qolmaydi. Xato bo'lsa jurnalga va sozlamalarga yoziladi."""
    async def wrapper():
        try:
            await coro
            database.set_setting("storage_error", "")
        except Exception as exc:
            log.exception("Ombor kanalga joylab bo'lmadi")
            database.set_setting("storage_error", f"{timeutil.fmt(timeutil.now())}: {exc}"[:300])
    asyncio.create_task(wrapper())


async def archive_missing(bot: Bot) -> int:
    """Kanalga hali joylanmagan barcha testlarni joylaydi."""
    import content
    ch = channel()
    if not ch:
        return 0
    done = database.storage_archived_tests(ch["id"])
    n = 0
    for t in content.all_tests():
        if t.id not in done:
            await archive_test(bot, t, "📦 <b>Test arxivga joylandi</b>")
            n += 1
            await asyncio.sleep(PAUSE)
    return n


async def recover(bot: Bot, chat_id: int, test_id: str, kind: str, ref: str = "", **kw) -> Message | None:
    """Fayl yuborilmasa — uni ombor kanaldagi postdan nusxa qilib yuboradi."""
    post = database.storage_find(test_id, kind, ref)
    if not post:
        return None
    try:
        res = await bot.copy_message(chat_id, post["chat_id"], post["message_id"],
                                     caption=kw.get("caption"), reply_markup=kw.get("reply_markup"))
        log.info("Fayl ombor kanaldan tiklandi: %s/%s/%s", test_id, kind, ref)
        return res
    except Exception as exc:
        log.warning("Ombor kanaldan ham olib bo'lmadi (%s/%s): %s", test_id, kind, exc)
        return None


async def autosetup(bot: Bot) -> None:
    """Birinchi ishga tushishda standart kanalga ulanadi va mavjud testlarni joylaydi (bir marta)."""
    if database.setting("storage_autosetup") == "1" or saved() or not config.DEFAULT_STORAGE_CHANNEL:
        return
    try:
        await connect(bot, config.DEFAULT_STORAGE_CHANNEL)
    except ValueError as exc:
        log.warning("Ombor kanalga ulanib bo'lmadi: %s", exc)
        database.set_setting("storage_error", f"Ulanish: {exc}"[:300])
        return
    database.set_setting("storage_autosetup", "1")
    n = await archive_missing(bot)
    log.info("Ombor kanal: %s ta mavjud test joylandi", n)
