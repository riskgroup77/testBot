"""Vaqt: hamma joyda O'zbekiston vaqti (UTC+5), server qayerda bo'lishidan qat'i nazar."""
import re
from datetime import datetime, timedelta, timezone

UZ = timezone(timedelta(hours=5))
MONTHS = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust",
          "sentabr", "oktabr", "noyabr", "dekabr"]


def now() -> datetime:
    return datetime.now(UZ).replace(tzinfo=None, microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


def parse_iso(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def fmt(s: str | datetime | None, with_year: bool = False) -> str:
    """'2026-11-25T18:00' -> '25-noyabr, 18:00'"""
    dt = parse_iso(s) if isinstance(s, str) else s
    if not dt:
        return "—"
    year = f" {dt.year}" if with_year or dt.year != now().year else ""
    return f"{dt.day}-{MONTHS[dt.month - 1]}{year}, {dt:%H:%M}"


def parse(text: str) -> datetime:
    """'25.11 18:00', '25.11.2026 18:00', '25/11 9:30', '18:00' (bugun), 'ertaga 18:00'."""
    t = text.strip().lower().replace("–", "-")
    base = now()
    m_time = re.search(r"(\d{1,2}):(\d{2})\s*$", t)
    hour, minute = (int(m_time.group(1)), int(m_time.group(2))) if m_time else (23, 59)
    date_part = t[: m_time.start()] if m_time else t
    if "ertaga" in date_part:
        day = (base + timedelta(days=1)).date()
    elif "bugun" in date_part or not date_part.strip():
        day = base.date()
    else:
        m = re.search(r"(\d{1,2})\s*[./\-]\s*(\d{1,2})(?:\s*[./\-]\s*(\d{2,4}))?", date_part)
        if not m:
            raise ValueError("sana topilmadi")
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else base.year
        if y < 100:
            y += 2000
        day = datetime(y, mo, d).date()
        if not m.group(3) and datetime.combine(day, datetime.min.time()) < base - timedelta(days=180):
            day = day.replace(year=y + 1)       # "5.01" dekabrda yozilsa — keyingi yil
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("vaqt noto‘g‘ri")
    return datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)


def quick(code: str) -> datetime:
    base = now()
    end_of = lambda d: datetime.combine(d, datetime.min.time()).replace(hour=23, minute=59)  # noqa: E731
    return {
        "now": base.replace(second=0),
        "today": end_of(base.date()),
        "tomorrow": end_of((base + timedelta(days=1)).date()),
        "3d": end_of((base + timedelta(days=3)).date()),
        "7d": end_of((base + timedelta(days=7)).date()),
    }[code]
