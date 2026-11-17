"""Kim admin ekanini aniqlash.

Bosh admin (owner) — .env faylida:  ADMIN_USERNAME=username   (bir nechta bo'lsa vergul bilan)
Yordamchi admin (helper) — bosh admin bot ichida «⚙️ Sozlamalar → 👮 Adminlar» dan qo'shadi.

Xavfsizlik: username keyin boshqa odamga o'tib qolishi mumkin. Shuning uchun bosh admin
botga birinchi marta yozganda uning Telegram ID si shu username'ga bog'lab qo'yiladi —
keyin faqat o'sha ID admin bo'ladi (username'ini o'zgartirsa ham admin bo'lib qoladi).
"""
import logging

import config
import database

log = logging.getLogger("bot")


def configured() -> bool:
    """.env da bosh admin (ADMIN_USERNAME yoki ADMIN_ID) yozilganmi."""
    return bool(config.admin_usernames() or config.admin_ids())


def demo_owner() -> int | None:
    """Sinov rejimidagi admin ID si (faqat .env da admin yozilmagan paytda amal qiladi)."""
    if configured():
        return None
    raw = database.setting("demo_owner")
    return int(raw) if raw.isdigit() else None


def claim_demo(user) -> bool:
    """Sinov rejimi: .env da admin yo'q va hali hech kim egallamagan bo'lsa —
    /admin yozgan BIRINCHI odam admin bo'ladi. Keyin boshqa hech kim bo'la olmaydi."""
    if configured() or database.setting("demo_owner"):
        return False
    database.set_setting("demo_owner", str(user.id))
    log.info("Sinov rejimi: /admin orqali admin bo'ldi — %s (@%s, id=%s)",
             user.full_name, user.username or "-", user.id)
    return True


def reset_demo() -> None:
    database.set_setting("demo_owner", "")


def _is_owner(user) -> bool:
    if user.id == demo_owner():
        return True
    if user.id in config.admin_ids():
        return True
    names = config.admin_usernames()
    if not names:
        return False
    bound = database.admin_bindings()
    if user.id in {bound[n] for n in names if n in bound}:
        return True
    username = (user.username or "").lower()
    if username in names and username not in bound:
        database.bind_admin(username, user.id)
        log.info("Bosh admin bog'landi: @%s (id=%s)", username, user.id)
        return True
    return False


def role(user) -> str | None:
    """'owner' (bosh admin), 'helper' (yordamchi) yoki None."""
    if not user or getattr(user, "is_bot", False):
        return None
    if _is_owner(user):
        return "owner"
    return database.staff_role(user.id)


def is_admin(user) -> bool:
    return role(user) is not None


def is_owner(user) -> bool:
    return role(user) == "owner"


def owner_ids() -> set[int]:
    names = config.admin_usernames()
    bound = database.admin_bindings()
    demo = demo_owner()
    return config.admin_ids() | {bound[n] for n in names if n in bound} | ({demo} if demo else set())


def admin_chat_ids() -> set[int]:
    """Bildirishnomalar uchun: bosh adminlar + yordamchilar."""
    return owner_ids() | {r["telegram_id"] for r in database.staff()}
