"""Testlar va videolar — ma'lumotlar bazasida saqlanadi, admin bot orqali boshqaradi."""
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import database
import timeutil
from config import BASE_DIR

CONTENT_DIR = BASE_DIR / "content"

# ---------------------------------------------------------------- bo'limlar
# «📝 Test yechish» → 📜 Attestatsiya → (0-guruh | mock testlar)
#                  → 🏅 Milliy sertifikat
# Test faqat "barg" bo'limlarga (LEAVES) biriktiriladi.
CATEGORIES: dict[str, dict] = {
    "att":     {"title": "📜 Attestatsiya",             "short": "Attestatsiya", "children": ["att0", "attmock"]},
    "att0":    {"title": "📗 Attestatsiya 0-guruh",     "short": "Att. 0-guruh", "parent": "att"},
    "attmock": {"title": "📙 Attestatsiya mock testlar", "short": "Att. mock",    "parent": "att"},
    "milliy":  {"title": "🏅 Milliy sertifikat",        "short": "Milliy",       "children": []},
}
ROOT = ["att", "milliy"]
LEAVES = ["att0", "attmock", "milliy"]
DEFAULT_CATEGORY = "milliy"


def category_path(key: str) -> str:
    """'📜 Attestatsiya › 📗 Attestatsiya 0-guruh'"""
    c = CATEGORIES.get(key)
    if not c:
        return key
    parent = c.get("parent")
    return (CATEGORIES[parent]["title"] + " › " if parent else "") + c["title"]


@dataclass
class Video:
    id: int
    kind: str                     # 'video' yoki 'document'
    title: str
    path: str | None = None       # bot papkasiga nisbatan (masalan content/8-loyiha/video_1.mp4)
    file_id: str | None = None    # admin botga yuborgan video


@dataclass
class Test:
    id: str                       # callback uchun qisqa nom (lotin, bo'sh joysiz)
    title: str                    # tugmada ko'rinadigan nom
    key: dict[int, str]           # savol raqami -> to'g'ri javob harfi
    points: dict[int, float]      # savol raqami -> ball
    options: dict[int, str]       # savol raqami -> ruxsat etilgan harflar
    subject: str = ""
    open_questions: str = ""      # bot tekshirmaydigan ochiq savollar haqida izoh
    active: bool = True           # o'quvchilarga ko'rinadimi
    pdf_path: str | None = None
    pdf_file_id: str | None = None
    pdf_name: str = ""
    videos: list[Video] = field(default_factory=list)
    # --- qoidalar (2-bosqich)
    show_mode: str = "now"        # now: darhol · score: faqat ball · after: muddat tugagach
    starts_at: str | None = None  # '2026-11-25T08:00' (O'zbekiston vaqti) yoki None = darhol
    ends_at: str | None = None    # None = muddatsiz
    max_attempts: int = 0         # 0 = cheksiz
    video_lock: str = "none"      # none: hammaga · solved: yechganlarga · deadline: muddat tugagach
    revealed: bool = False        # muddat tugagach tahlil yuborilganmi
    category: str = DEFAULT_CATEGORY  # bo'lim: att0 | attmock | milliy
    # yozma savollar (36–45): '36a' -> '17', '36b' -> '22'; har biri 1 ball
    open_key: dict[str, str] = field(default_factory=dict)
    open_points: dict[str, float] = field(default_factory=dict)

    @property
    def open_numbers(self) -> set[int]:
        return {int(q[:-1]) for q in self.open_key}

    @property
    def total_answers(self) -> int:
        return len(self.key) + len(self.open_key)

    @property
    def last_question(self) -> int:
        return max([*self.key, *self.open_numbers] or [0])

    @property
    def show_correct(self) -> bool:
        return self.show_mode == "now"

    def status(self, now: datetime | None = None) -> str:
        """upcoming (hali ochilmagan) · open · closed (muddat tugagan)."""
        now = now or timeutil.now()
        if self.starts_at and now < timeutil.parse_iso(self.starts_at):
            return "upcoming"
        if self.ends_at and now >= timeutil.parse_iso(self.ends_at):
            return "closed"
        return "open"

    def answers_visible(self, now: datetime | None = None) -> bool:
        """Natijada to'g'ri javoblar ko'rsatiladimi."""
        return self.show_mode == "now" or (self.show_mode == "after" and self.status(now) == "closed")

    def video_block_reason(self, solved: bool, now: datetime | None = None) -> str:
        """Video yopiq bo'lsa — sababi, ochiq bo'lsa bo'sh satr."""
        if self.video_lock == "solved" and not solved:
            return "🔒 Video tahlil testni yechgandan keyin ochiladi. Avval javoblaringizni yuboring!"
        if self.video_lock == "deadline" and self.status(now) != "closed":
            when = f" ({timeutil.fmt(self.ends_at)})" if self.ends_at else ""
            return f"🔒 Video tahlil test muddati tugagach ochiladi{when}."
        return ""

    @property
    def max_score(self) -> float:
        return round(sum(self.points.values())
                     + sum(self.open_points.get(q, 1.0) for q in self.open_key), 1)

    @property
    def questions(self) -> list[int]:
        return sorted(self.key)

    @property
    def has_pdf(self) -> bool:
        return bool(self.pdf_file_id or (self.pdf_path and abs_path(self.pdf_path).exists()))

    @property
    def folder(self) -> Path:
        return CONTENT_DIR / self.id


def abs_path(rel: str) -> Path:
    return (BASE_DIR / rel).resolve()


def rel_path(p: Path) -> str:
    return p.resolve().relative_to(BASE_DIR).as_posix()


def _from_row(row) -> Test:
    data = json.loads(row["data"])
    return Test(
        id=row["id"], title=row["title"], subject=row["subject"],
        key={int(k): v for k, v in data["key"].items()},
        points={int(k): float(v) for k, v in data["points"].items()},
        options={int(k): v for k, v in data["options"].items()},
        open_key=dict(data.get("open_key", {})),
        open_points={k: float(v) for k, v in data.get("open_points", {}).items()},
        open_questions=row["open_questions"],
        active=bool(row["active"]), pdf_path=row["pdf_path"], pdf_file_id=row["pdf_file_id"],
        pdf_name=row["pdf_name"], show_mode=row["show_mode"], starts_at=row["starts_at"],
        ends_at=row["ends_at"], max_attempts=row["max_attempts"], video_lock=row["video_lock"],
        revealed=bool(row["revealed"]),
        category=row["category"] if row["category"] in LEAVES else DEFAULT_CATEGORY,
        videos=[Video(id=v["id"], kind=v["kind"], title=v["title"], path=v["path"], file_id=v["file_id"])
                for v in database.video_rows(row["id"])],
    )


def get(test_id: str | None) -> Test | None:
    row = database.test_row(test_id) if test_id else None
    return _from_row(row) if row else None


def all_tests(active_only: bool = False) -> list[Test]:
    return [t for t in map(_from_row, database.test_rows()) if t.active or not active_only]


def save(t: Test) -> None:
    """Testning o'zini saqlaydi (videolar alohida: database.video_add / video_delete)."""
    database.test_save({
        "id": t.id, "title": t.title, "subject": t.subject,
        "data": json.dumps({"key": t.key, "points": t.points, "options": t.options,
                            "open_key": t.open_key, "open_points": t.open_points}),
        "open_questions": t.open_questions, "show_correct": int(t.show_correct),
        "active": int(t.active), "pdf_path": t.pdf_path, "pdf_file_id": t.pdf_file_id,
        "pdf_name": t.pdf_name, "show_mode": t.show_mode, "starts_at": t.starts_at,
        "ends_at": t.ends_at, "max_attempts": t.max_attempts, "video_lock": t.video_lock,
        "revealed": int(t.revealed), "groups": "[]", "category": t.category,
    })


def new_id() -> str:
    while True:
        tid = "t" + secrets.token_hex(3)
        if not database.test_row(tid):
            return tid


# ---------------------------------------------------------------- boshlang'ich test
def seed() -> None:
    """Birinchi ishga tushishda 8-loyihani bazaga yozadi (bir marta)."""
    if database.setting("seeded") == "1":
        return
    folder = CONTENT_DIR / "8-loyiha"
    if (folder / "test.pdf").exists() and not database.test_row("l8"):
        key = {i: ch for i, ch in enumerate("BDADACBDAADBBDAD" "BDCADACABCCDBCBD" "ACB", start=1)}
        low = {1, 2, 3, 11, 12, 16, 17, 18, 26, 29}
        save(Test(
            id="l8", title="8-loyiha · Fizika", subject="Fizika", key=key,
            points={q: (1.3 if q in low else 2.2) for q in key},
            options={q: ("ABCDEF" if q >= 33 else "ABCD") for q in key},
            open_questions="36–45 — ochiq (yozma) savollar, ular video tahlilda yechib ko‘rsatiladi.",
            pdf_path=rel_path(folder / "test.pdf"), pdf_name="8-loyiha - Fizika.pdf",
        ))
        parts = sorted(folder.glob("video_*.mp4"), key=lambda p: int(p.stem.split("_")[1]))
        for i, p in enumerate(parts, start=1):
            database.video_add("l8", "video", f"{i}-qism", path=rel_path(p))
    database.set_setting("seeded", "1")
