"""Sozlamalar: token .env faylida saqlanadi."""
import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
DB_PATH = BASE_DIR / "users.db"
LOG_PATH = BASE_DIR / "bot.log"

SETUP_HOST = "127.0.0.1"
SETUP_PORT = 8765
SETUP_URL = f"http://{SETUP_HOST}:{SETUP_PORT}"

BOT_NAME = "Akmaljon yordamchi"

# 🗄 Ombor kanal: testlar (PDF, video, ma'lumotlar) shu kanalga ham joylanadi.
# Admin panel → ⚙️ Sozlamalar → 🗄 Ombor kanal dan almashtirish mumkin.
DEFAULT_STORAGE_CHANNEL = "@akmaljonyordamchibotkanal"

TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,50}$")


def load_env(name: str) -> str | None:
    """bot/.env faylidan qiymat o'qiydi (bo'lmasa — tizim o'zgaruvchisidan)."""
    if ENV_PATH.exists():
        # utf-8-sig: Bloknot qo'shadigan BOM belgisini ham to'g'ri o'qiydi
        for line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, sep, value = line.partition("=")
            if sep and key.strip() == name:
                value = value.strip().strip('"').strip("'").strip()
                if value:
                    return value
    return os.environ.get(name, "").strip() or None


def load_token() -> str | None:
    return load_env("BOT_TOKEN")


def admin_ids() -> set[int]:
    """ADMIN_ID=123,456 — natijalar shu foydalanuvchilarga ham yuboriladi."""
    raw = load_env("ADMIN_ID") or ""
    return {int(x) for x in raw.replace(";", ",").split(",") if x.strip().isdigit()}


def admin_usernames() -> set[str]:
    """ADMIN_USERNAME=akmaljon yoki @akmaljon (bir nechta bo'lsa vergul bilan)."""
    raw = load_env("ADMIN_USERNAME") or ""
    return {x.strip().lstrip("@").lower() for x in raw.replace(";", ",").split(",") if x.strip().lstrip("@")}


def save_token(token: str) -> None:
    """BOT_TOKEN ni .env fayliga yozadi (boshqa qatorlarni saqlab qoladi)."""
    lines = []
    if ENV_PATH.exists():
        lines = [
            line for line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines()
            if line.strip().removeprefix("export ").partition("=")[0].strip() != "BOT_TOKEN"
        ]
    lines.append(f"BOT_TOKEN={token}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mask_token(token: str | None) -> str:
    if not token:
        return "—"
    bot_id, _, secret = token.partition(":")
    return f"{bot_id}:{secret[:4]}••••••••{secret[-3:]}"
