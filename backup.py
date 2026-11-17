"""Zaxira nusxa: baza (foydalanuvchilar, testlar, natijalar) + kalitsiz PDF lar → .zip.

Har kuni kechqurun avtomatik olinadi va bosh adminga Telegram'da yuboriladi,
oxirgi 14 tasi bot/backups papkasida ham saqlanadi.
"""
import asyncio
import logging
import zipfile
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile

import database
import timeutil
from config import BASE_DIR

log = logging.getLogger("backup")
BACKUP_DIR = BASE_DIR / "backups"
KEEP = 14
AUTO_HOUR = 21          # shu soatdan keyin kuniga bir marta

RESTORE_TXT = """ZAXIRADAN TIKLASH
=================
1) Botni to'xtating (ishga_tushirish.bat oynasini yoping).
2) Shu arxivdagi users.db faylini bot papkasiga ko'chiring (eskisini almashtiring).
3) content papkasini ham bot papkasiga ko'chiring (PDF fayllar).
4) Botni qayta ishga tushiring.

Videolar Telegram serverida saqlanadi — ular arxivga kirmaydi va yo'qolmaydi.
"""


def _make() -> Path:
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = timeutil.now().strftime("%Y-%m-%d_%H-%M")
    tmp = BACKUP_DIR / "_nusxa.db"
    database.backup_to(tmp)
    out = BACKUP_DIR / f"zaxira_{stamp}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(tmp, "users.db")
        for pdf in (BASE_DIR / "content").rglob("*.pdf"):
            if "_yuklangan" not in pdf.parts:
                z.write(pdf, pdf.relative_to(BASE_DIR).as_posix())
        z.writestr("TIKLASH.txt", RESTORE_TXT)
    tmp.unlink(missing_ok=True)
    for old in sorted(BACKUP_DIR.glob("zaxira_*.zip"))[:-KEEP]:
        old.unlink(missing_ok=True)
    return out


async def make_and_send(bot: Bot, chat_ids: set[int], reason: str) -> Path:
    path = await asyncio.to_thread(_make)
    database.set_setting("last_backup", timeutil.iso(timeutil.now()))
    s = database.stats()
    caption = (f"💾 <b>Zaxira nusxa</b> · {reason}\n"
               f"🕒 {timeutil.fmt(timeutil.now())}\n"
               f"👥 {s['users']} foydalanuvchi · 📘 {s['tests']} test · 📊 {s['results']} natija\n\n"
               "Bu faylni saqlab qo‘ying. Tiklash yo‘riqnomasi arxiv ichida (TIKLASH.txt).")
    for cid in chat_ids:
        try:
            await bot.send_document(cid, FSInputFile(path), caption=caption)
        except Exception as exc:
            log.warning("Zaxira %s ga yuborilmadi: %s", cid, exc)
    log.info("Zaxira olindi: %s (%.1f KB)", path.name, path.stat().st_size / 1024)
    return path


def auto_due() -> bool:
    """Avtomatik zaxira vaqti keldimi: kechqurun kuniga bir marta, yoki 2 kundan beri olinmagan bo'lsa."""
    if database.setting("backup_auto", "1") != "1":
        return False
    now = timeutil.now()
    last = timeutil.parse_iso(database.setting("last_auto_backup") or None)
    if last and last.date() == now.date():
        return False
    return now.hour >= AUTO_HOUR or (last is not None and (now - last).days >= 2)
