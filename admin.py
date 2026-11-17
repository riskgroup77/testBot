"""⚙️ Admin panel — faqat adminga ko'rinadi (.env: ADMIN_USERNAME=...).

Imkoniyatlar: test qo'shish (PDF + kalit + variantlar + ballar + izoh + video),
tahrirlash (kalit o'zgarsa natijalar qayta hisoblanadi), yashirish/ko'rsatish, o'chirish,
videolarni qo'shish/o'chirish, reyting, Excel, statistika.
"""
import asyncio
import json
import logging
import re
from html import escape
from pathlib import Path

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

import access
import backup
import certificate
import content
import database
import export
import grading
import media
import storage
import subscribe
import timeutil
from content import Test
from grading import fmt_num, fmt_nums
from ui import BTN_ADMIN, ikb

log = logging.getLogger("admin")
logging.getLogger("pypdf").setLevel(logging.ERROR)

TMP_DIR = content.CONTENT_DIR / "_yuklangan"
PDF_LIMIT = 20 * 1024 * 1024          # bot 20 MB gacha faylni yuklab, ichini tekshira oladi
CANCEL = [("❌ Bekor qilish", "a:cancel")]
STEPS = 6   # nom · bo'lim · PDF · kalit · izoh · video


class IsAdmin(BaseFilter):
    async def __call__(self, event) -> bool:
        return access.is_admin(getattr(event, "from_user", None))


admin_router = Router(name="admin")
admin_router.message.filter(IsAdmin())
admin_router.callback_query.filter(IsAdmin())


class NewTest(StatesGroup):
    title = State()
    category = State()
    pdf = State()
    key = State()
    options = State()
    points = State()
    note = State()
    videos = State()


class EditTest(StatesGroup):
    value = State()     # data: field, test_id
    pdf = State()       # data: test_id
    videos = State()    # data: test_id
    time = State()      # data: which (start|end), test_id


class Broadcast(StatesGroup):
    content = State()   # admin yuboradigan xabarni kutyapmiz
    url = State()       # "Nomi | https://..." havola tugmasi


class Users(StatesGroup):
    search = State()
    message = State()   # data: target


class Staff(StatesGroup):
    find = State()


class Sub(StatesGroup):
    channel = State()


class Store(StatesGroup):
    channel = State()


class CertCfg(StatesGroup):
    teacher = State()
    subject = State()


# ================================================================ yordamchilar
async def show(target: Message | CallbackQuery, text: str, kb=None) -> None:
    """Tugma bosilgan bo'lsa — o'sha xabarni almashtiradi, bo'lmasa yangi xabar yuboradi."""
    if isinstance(target, CallbackQuery):
        await target.answer()
        try:
            await target.message.edit_text(text, reply_markup=kb)
            return
        except TelegramBadRequest as exc:
            if "not modified" in str(exc):
                return
        await target.message.answer(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


def arg(call: CallbackQuery) -> str:
    return call.data.rsplit(":", 1)[1]


async def get_draft(state: FSMContext) -> dict:
    return (await state.get_data()).get("draft", {})


async def put_draft(state: FSMContext, **kw) -> dict:
    d = await get_draft(state)
    d.update(kw)
    await state.update_data(draft=d)
    return d


def regrade(t: Test) -> int:
    """Kalit yoki ballar o'zgarganda barcha natijalarni qayta hisoblaydi."""
    rows = database.test_results(t.id)
    for r in rows:
        mc, op = grading.split_answers(json.loads(r["answers"]))
        database.update_result(r["id"], grading.grade(mc, t, op))
    return len(rows)


def key_summary(key: dict, open_key: dict) -> str:
    """'35 ta variantli + 20 ta yozma javob (36–45)'"""
    text = f"{len(key)} ta variantli"
    if open_key:
        nums = sorted({int(q[:-1]) for q in open_key})
        text += f" + {len(open_key)} ta yozma javob ({nums[0]}–{nums[-1]}, a/b)"
    return text


def fit_to_key(key: dict[int, str], points: dict[int, float], options: dict[int, str]):
    """Savollar soni o'zgarsa ball/variantlarni moslaydi. (points, options, izohlar)."""
    notes = []
    common = max(set(points.values()), key=list(points.values()).count) if points else 1.0
    new_points = {q: points.get(q, common) for q in key}
    new_options = {}
    for q, letter in key.items():
        opt = options.get(q, "ABCD")
        if letter not in opt:
            opt = "ABCDEFGH"[: "ABCDEFGH".index(letter) + 1]
            notes.append(f"{q}-savol variantlari A–{letter} qilindi")
        new_options[q] = opt
    added = [q for q in key if q not in points]
    if added:
        notes.append(f"yangi savollar ({fmt_nums(added)}) ga {fmt_num(common)} ball berildi")
    return new_points, new_options, notes


def video_from(message: Message) -> tuple[str, str] | None:
    if message.video:
        return "video", message.video.file_id
    doc = message.document
    if doc and (doc.mime_type or "").startswith("video/"):
        return "document", doc.file_id
    return None


# ---------------------------------------------------------------- PDF
async def inspect_pdf(bot: Bot, doc) -> dict:
    """PDF ni yuklab oladi va javoblar kaliti bor sahifalarni qidiradi."""
    info = {"file_id": doc.file_id, "name": doc.file_name or "test.pdf", "tmp": None,
            "pages": 0, "key_pages": [], "key": {}, "note": ""}
    if (doc.file_size or 0) > PDF_LIMIT:
        info["note"] = "PDF 20 MB dan katta — ichini tekshira olmadim, shundayligicha qo‘yiladi."
        return info
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = TMP_DIR / f"{doc.file_unique_id}.pdf"
    await bot.download(doc, destination=tmp, timeout=120)
    info["tmp"] = str(tmp)

    def scan() -> list[str]:
        from pypdf import PdfReader
        texts = []
        for page in PdfReader(tmp).pages:
            try:
                texts.append(page.extract_text() or "")
            except Exception:
                texts.append("")
        return texts

    try:
        texts = await asyncio.to_thread(scan)
    except Exception:
        info["note"] = "PDF ni o‘qib bo‘lmadi — shundayligicha qo‘yiladi."
        return info
    info["pages"] = len(texts)
    pair_re = re.compile(r"(?<![\w.,])\d{1,3}\s*[.)]?\s+[A-H](?![A-Za-z])")
    key_pages = [i for i, t in enumerate(texts)
                 if re.search(r"KALIT|КАЛИТ|JAVOBLAR", t) and len(pair_re.findall(t)) >= 5]
    if len(key_pages) < len(texts):          # hamma sahifani hech qachon kesmaymiz
        info["key_pages"] = key_pages
        if key_pages:
            info["key"], info["open"] = grading.detect_key("\n".join(texts[i] for i in key_pages))
    return info


def finalize_pdf(info: dict, test_id: str, strip: bool) -> tuple[str | None, str | None]:
    """(pdf_path, pdf_file_id). Kesish kerak bo'lsa — kalitsiz nusxani content/<id>/test.pdf ga yozadi."""
    tmp = Path(info["tmp"]) if info.get("tmp") else None
    try:
        if strip and tmp and tmp.exists() and info["key_pages"]:
            from pypdf import PdfReader, PdfWriter
            writer = PdfWriter()
            for i, page in enumerate(PdfReader(tmp).pages):
                if i not in info["key_pages"]:
                    writer.add_page(page)
            folder = content.CONTENT_DIR / test_id
            folder.mkdir(parents=True, exist_ok=True)
            out = folder / "test.pdf"
            with open(out, "wb") as f:
                writer.write(f)
            return content.rel_path(out), None
        return None, info["file_id"]
    finally:
        if tmp and tmp.exists():
            tmp.unlink(missing_ok=True)


def pdf_report(info: dict) -> str:
    if info["note"]:
        return f"📄 PDF qabul qilindi.\nℹ️ {info['note']}"
    text = f"📄 PDF qabul qilindi: <b>{info['pages']}</b> sahifa."
    if info["key_pages"]:
        pages = ", ".join(str(i + 1) for i in info["key_pages"])
        text += (f"\n\n⚠️ <b>{pages}-sahifada javoblar kaliti bor!</b>\n"
                 "O‘quvchilarga yuboriladigan PDF dan shu sahifani olib tashlaymi?")
    else:
        text += "\n✅ Javoblar kaliti sahifasi topilmadi."
    return text


def strip_kb(prefix: str):
    return ikb([("✂️ Ha, olib tashlansin (tavsiya)", f"{prefix}:1")],
               [("Yo‘q, shundayligicha qolsin", f"{prefix}:0")], CANCEL)


# ---------------------------------------------------------------- yordam matnlari
KEY_HELP = ("Bitta xabarda yuboring — avval variantli (1–35), keyin yozma (36–45, a va b):\n"
            "<code>1B 2D 3A 4D 5A ... 35B\n"
            "36. a) 17 b) 22\n"
            "37. a) 5 b) 3,5\n"
            "...\n"
            "45. a) 600 b) 1,1</code>\n\n"
            "💡 Yozma javob son yoki qisqa so‘z bo‘lishi mumkin. Bir nechta to‘g‘ri javob bo‘lsa, "
            "<code>|</code> bilan ajrating: <code>0,5 | 1/2</code>\n"
            "Variantli qismni faqat harflar bilan ham yozsa bo‘ladi: <code>BDADAC...</code>")
OPTIONS_HELP = ("Qaysi savollarda nechta variant borligini yozing, masalan:\n"
                "<code>33-35: A-F</code>\n"
                "Yozilmagan savollar <b>A–D</b> bo‘ladi. Bir nechta qator ham mumkin:\n"
                "<code>1-20: A-D\n21-30: A-E\n31-35: A-F</code>")
POINTS_HELP = ("Hammasiga bir xil ball bo‘lsa, faqat sonni yozing: <code>2,2</code>\n\n"
               "Har xil bo‘lsa, har qatorga — savollar va ball:\n"
               "<code>1-3, 11, 12, 16-18, 26, 29: 1,3\nqolgan: 2,2</code>")


# ================================================================ bosh sahifa
def home_view(user=None) -> tuple[str, object]:
    s = database.stats()
    owner = access.is_owner(user) if user else True
    text = ("⚙️ <b>Admin panel</b>\n"
            f"<i>{'👑 Bosh admin' if owner else '🧑‍🏫 Yordamchi admin'}</i>\n\n"
            f"👥 Foydalanuvchilar: <b>{s['users']}</b> (bugun +{s['users_today']})\n"
            f"📘 Testlar: <b>{s['tests_active']}</b> faol / {s['tests']} ta\n"
            f"📊 Natijalar: <b>{s['results']}</b> (bugun {s['results_today']})\n\n"
            "Kerakli bo‘limni tanlang 👇")
    rows = [[("📚 Testlar", "a:tests"), ("➕ Yangi test", "a:new")],
            [("👥 Foydalanuvchilar", "a:users")]]
    if owner:
        rows += [[("📢 Xabar yuborish", "a:bc"), ("📊 Statistika", "a:stats")],
                 [("📥 Excel (hammasi)", "a:xall"), ("⚙️ Sozlamalar", "a:settings")]]
    else:
        rows += [[("📊 Statistika", "a:stats"), ("📥 Excel (hammasi)", "a:xall")]]
    return text, ikb(*rows)


def audit(user, action: str, details: str = "") -> None:
    """Admin harakatini jurnalga yozadi."""
    try:
        database.audit(user.id if user else None, action, details)
    except Exception:
        log.exception("Jurnalga yozib bo'lmadi")


# Yordamchi admin qila olmaydigan amallar (callback boshlanishi)
OWNER_ONLY = ("a:del", "a:bc", "a:ann:", "a:ban:", "a:unban:", "a:adm", "a:sub", "a:backup",
              "a:log", "a:settings", "a:notify", "a:stor", "a:cert")


class RoleGuard(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if (isinstance(event, CallbackQuery) and (event.data or "").startswith(OWNER_ONLY)
                and not access.is_owner(event.from_user)):
            await event.answer("🔒 Bu amal faqat bosh admin uchun.", show_alert=True)
            return None
        return await handler(event, data)


admin_router.callback_query.middleware(RoleGuard())


@admin_router.message(F.text == BTN_ADMIN)     # /admin buyrug'i handlers.cmd_admin da
async def admin_home(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, kb = home_view(message.from_user)
    await message.answer(text, reply_markup=kb)


@admin_router.callback_query(F.data == "a:home")
async def home_cb(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(call, *home_view(call.from_user))


@admin_router.callback_query(F.data == "a:cancel")
async def cancel_cb(call: CallbackQuery, state: FSMContext) -> None:
    d = await get_draft(state)
    if d.get("pdf", {}).get("tmp"):
        Path(d["pdf"]["tmp"]).unlink(missing_ok=True)
    data = await state.get_data()
    if data.get("pdf_info", {}).get("tmp"):
        Path(data["pdf_info"]["tmp"]).unlink(missing_ok=True)
    await state.clear()
    text, kb = home_view(call.from_user)
    await show(call, "❌ Bekor qilindi.\n\n" + text, kb)


@admin_router.callback_query(F.data == "a:notify")
async def notify_cb(call: CallbackQuery) -> None:
    on = database.setting("notify", "1") == "1"
    database.set_setting("notify", "0" if on else "1")
    await show(call, *settings_view())


# ================================================================ statistika va Excel
@admin_router.callback_query(F.data == "a:stats")
async def stats_cb(call: CallbackQuery) -> None:
    s = database.stats()
    lines = [
        "📊 <b>Statistika</b>\n",
        f"👥 Foydalanuvchilar: <b>{s['users']}</b>",
        f"   • yangi: bugun {s['users_today']} · 7 kunda {s['users_week']}",
        f"   • faol: bugun {s['active_today']} · 7 kunda {s['active_week']}\n",
        f"📝 Test yechishlar: <b>{s['results']}</b>",
        f"   • bugun {s['results_today']} · 7 kunda {s['results_week']}",
        f"   • test yechgan o‘quvchilar: {s['solvers']}",
        f"   • o‘rtacha natija: {round(s['avg_percent'])}%\n",
        f"📘 Testlar: {s['tests_active']} faol / {s['tests']} ta",
    ]
    for row in s["per_test"]:
        lines.append(f"   • {escape(row['title'])} — {row['n']} urinish, {row['people']} o‘quvchi"
                     + (f", o‘rtacha {round(row['avg'])}%" if row["n"] else ""))
    await show(call, "\n".join(lines), ikb([("📥 Excel (hammasi)", "a:xall")], [("⬅️ Orqaga", "a:home")]))


@admin_router.callback_query(F.data == "a:xall")
async def excel_all_cb(call: CallbackQuery) -> None:
    await call.answer("📥 Excel tayyorlanmoqda...")
    data = await asyncio.to_thread(export.all_workbook)
    await call.message.answer_document(
        BufferedInputFile(data, filename="Barcha natijalar.xlsx"),
        caption="📥 <b>Barcha foydalanuvchilar va natijalar</b>\n"
                "Varaqlar: Foydalanuvchilar · Natijalar")


# ================================================================ testlar ro'yxati va kartochka
@admin_router.callback_query(F.data == "a:tests")
async def tests_cb(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    tests = sorted(content.all_tests(), key=lambda t: content.LEAVES.index(t.category))
    rows = [[(f"{'🟢' if t.active else '⚪'} {t.title} · {content.CATEGORIES[t.category]['short']}", f"a:t:{t.id}")]
            for t in tests]
    rows += [[("➕ Yangi test", "a:new")], [("⬅️ Orqaga", "a:home")]]
    text = ("📚 <b>Testlar</b>\n\n🟢 — o‘quvchilarga ko‘rinadi · ⚪ — yashirin\n"
            "Har bir test yonida uning bo‘limi yozilgan.\n\nTestni tanlang 👇")
    if not tests:
        text = "📚 Hozircha test yo‘q. «➕ Yangi test» bilan qo‘shing."
    await show(call, text, ikb(*rows))


SHOW_NAMES = {"now": "natija bilan darhol ko‘rsatiladi",
              "after": "muddat tugagach hammaga avtomatik yuboriladi",
              "score": "hech qachon ko‘rsatilmaydi (faqat ball)"}
SHOW_SHORT = {"now": "darhol", "after": "muddat tugagach", "score": "ko‘rsatilmaydi"}
VIDEO_NAMES = {"none": "hammaga ochiq", "solved": "faqat testni yechganlarga",
               "deadline": "muddat tugagach ochiladi"}
VIDEO_SHORT = {"none": "hammaga", "solved": "yechganlarga", "deadline": "muddat tugagach"}
NEXT_SHOW = {"now": "after", "after": "score", "score": "now"}
NEXT_VIDEO = {"none": "solved", "solved": "deadline", "deadline": "none"}
STATUS_NAMES = {"upcoming": "⏳ hali ochilmagan", "open": "✅ ochiq", "closed": "⏹ muddati tugagan"}


def rules_summary(t: Test) -> str:
    parts = [STATUS_NAMES[t.status()]]
    if t.ends_at:
        parts.append(f"{timeutil.fmt(t.ends_at)} gacha")
    parts.append(f"{t.max_attempts} urinish" if t.max_attempts else "cheksiz urinish")
    parts.append(f"javoblar: {SHOW_SHORT[t.show_mode]}")
    if t.video_lock != "none":
        parts.append(f"video: {VIDEO_SHORT[t.video_lock]}")
    return " · ".join(parts)


def card_view(t: Test) -> tuple[str, object]:
    best = database.best_results(t.id)
    attempts = sum(r["attempts"] for r in best)
    avg = round(sum(r["score"] / r["max_score"] for r in best if r["max_score"]) / len(best) * 100) if best else 0
    text = (
        f"📘 <b>{escape(t.title)}</b>\n\n"
        f"📂 Bo‘lim: {content.category_path(t.category)}\n"
        f"Holati: {'🟢 Faol — o‘quvchilarga ko‘rinadi' if t.active else '⚪ Yashirin — o‘quvchilar ko‘rmaydi'}\n"
        f"❓ Savollar: {key_summary(t.key, t.open_key)} · Jami: <b>{fmt_num(t.max_score)}</b> ball\n"
        f"🔑 Kalit: <tg-spoiler>{escape(grading.key_text(t.key, t.open_key))}</tg-spoiler>\n"
        f"🎯 Ballar: {grading.points_text(t.points, t.open_points or {q: 1.0 for q in t.open_key})}\n"
        f"🔤 Variantlar: {grading.options_text(t.options)}\n"
        f"📄 PDF: {'✅ bor' if t.has_pdf else '❌ yo‘q'} · 🎬 Videolar: {len(t.videos)} ta\n"
        f"\n⚙️ <b>Qoidalar:</b> {rules_summary(t)}\n"
        + (f"📝 Izoh: {escape(t.open_questions)}\n" if t.open_questions else "")
        + f"\n📊 Natijalar: <b>{attempts}</b> urinish, {len(best)} o‘quvchi"
        + (f", o‘rtacha {avg}%" if best else "")
    )
    kb = ikb(
        [("🏆 Reyting", f"a:rank:{t.id}"), ("📈 Savollar tahlili", f"a:qa:{t.id}")],
        [("📥 Excel", f"a:x:{t.id}")],
        [("✏️ Nomi", f"a:e:title:{t.id}"), ("📂 Bo‘lim", f"a:cat:{t.id}")],
        [("🔑 Kalit", f"a:e:key:{t.id}")],
        [("🎯 Ballar", f"a:e:points:{t.id}"), ("🔤 Variantlar", f"a:e:options:{t.id}")],
        [("📄 PDF almashtirish", f"a:pdf:{t.id}"), ("🎬 Videolar", f"a:vids:{t.id}")],
        [("📝 Izoh", f"a:e:note:{t.id}"), ("👁 PDF ni ko‘rish", f"a:send:{t.id}")],
        [("⚙️ Qoidalar: muddat, urinish, javoblar", f"a:rules:{t.id}")],
        [("⚪ Yashirish" if t.active else "🟢 O‘quvchilarga ko‘rsatish", f"a:act:{t.id}"),
         ("📢 E’lon qilish", f"a:ann:{t.id}")],
        [("🗑 O‘chirish", f"a:del:{t.id}"), ("⬅️ Testlar", "a:tests")],
    )
    return text, kb


async def open_card(target: Message | CallbackQuery, test_id: str, prefix: str = "") -> None:
    t = content.get(test_id)
    if not t:
        await show(target, "⚠️ Test topilmadi.", ikb([("⬅️ Testlar", "a:tests")]))
        return
    text, kb = card_view(t)
    await show(target, prefix + text, kb)


@admin_router.callback_query(F.data.startswith("a:t:"))
async def card_cb(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await open_card(call, arg(call))


@admin_router.callback_query(F.data.startswith("a:act:"))
async def toggle_active(call: CallbackQuery) -> None:
    t = content.get(arg(call))
    if t:
        t.active = not t.active
        content.save(t)
        audit(call.from_user, "Test ko‘rsatildi" if t.active else "Test yashirildi", t.title)
        log.info("Test %s: active=%s", t.id, t.active)
    await open_card(call, arg(call))


@admin_router.callback_query(F.data.startswith("a:send:"))
async def preview_pdf(call: CallbackQuery, bot: Bot) -> None:
    t = content.get(arg(call))
    if not t or not t.has_pdf:
        await call.answer("PDF yo‘q", show_alert=True)
        return
    await call.answer("📄 Yuborilmoqda...")
    await media.send_media(bot, call.message.chat.id, "document", t.pdf_path, t.pdf_file_id,
                           filename=t.pdf_name or f"{export.safe_filename(t.title)}.pdf",
                           caption="👁 O‘quvchilar aynan shu PDF ni oladi.")


# ---------------------------------------------------------------- reyting va Excel
@admin_router.callback_query(F.data.startswith("a:rank:"))
async def rank_cb(call: CallbackQuery) -> None:
    t = content.get(arg(call))
    if not t:
        await call.answer("Test topilmadi", show_alert=True)
        return
    best = database.best_results(t.id)
    back = ikb([("📥 Excel", f"a:x:{t.id}"), ("⬅️ Orqaga", f"a:t:{t.id}")])
    if not best:
        await show(call, f"🏆 <b>{escape(t.title)}</b>\n\nHali hech kim yechmagan.", back)
        return
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    avg = round(sum(r["score"] / r["max_score"] for r in best) / len(best) * 100)
    lines = [f"🏆 <b>Reyting · {escape(t.title)}</b>",
             f"👥 {len(best)} o‘quvchi · o‘rtacha {avg}%\n"]
    for i, r in enumerate(best[:30], start=1):
        name = " ".join(x for x in (r["first_name"], r["last_name"]) if x) or str(r["telegram_id"])
        pct = round(r["score"] / r["max_score"] * 100)
        lines.append(f"{medals.get(i, f'{i}.')} {escape(name)}"
                     + (f" (@{escape(r['username'])})" if r["username"] else "")
                     + f" — <b>{fmt_num(r['score'])}</b> ({pct}%)"
                     + (f" · {r['attempts']} urinish" if r["attempts"] > 1 else ""))
    if len(best) > 30:
        lines.append(f"\n… yana {len(best) - 30} o‘quvchi — to‘liq ro‘yxat Excel’da.")
    await show(call, "\n".join(lines), back)


@admin_router.callback_query(F.data.startswith("a:x:"))
async def excel_test_cb(call: CallbackQuery) -> None:
    t = content.get(arg(call))
    if not t:
        await call.answer("Test topilmadi", show_alert=True)
        return
    await call.answer("📥 Excel tayyorlanmoqda...")
    data = await asyncio.to_thread(export.test_workbook, t)
    await call.message.answer_document(
        BufferedInputFile(data, filename=f"{export.safe_filename(t.title)} - natijalar.xlsx"),
        caption=f"📥 <b>{escape(t.title)}</b> natijalari\n"
                "Varaqlar: Reyting · Barcha urinishlar · Savollar tahlili")


# ---------------------------------------------------------------- o'chirish
@admin_router.callback_query(F.data.startswith("a:del:"))
async def delete_ask(call: CallbackQuery) -> None:
    t = content.get(arg(call))
    if not t:
        await call.answer("Test topilmadi", show_alert=True)
        return
    n = len(database.test_results(t.id))
    await show(call,
               f"🗑 <b>{escape(t.title)}</b> o‘chirilsinmi?\n\n"
               f"Test, uning videolari va <b>{n} ta natija</b> butunlay o‘chadi. Buni qaytarib bo‘lmaydi.\n\n"
               "💡 Faqat o‘quvchilardan yashirmoqchi bo‘lsangiz, «⚪ Yashirish» ni bosing.",
               ikb([("🗑 Ha, butunlay o‘chirish", f"a:delok:{t.id}")], [("⬅️ Yo‘q, orqaga", f"a:t:{t.id}")]))


@admin_router.callback_query(F.data.startswith("a:delok:"))
async def delete_ok(call: CallbackQuery) -> None:
    t = content.get(arg(call))
    if t:
        database.test_delete(t.id)
        audit(call.from_user, "Test o‘chirildi", t.title)
        storage.run(storage.note(call.bot, t, "🗑 <b>Test botdan o‘chirildi</b> (kanaldagi fayllar arxivda qoladi)"))
        folder = content.CONTENT_DIR / t.id      # bot yaratgan papka (kalitsiz PDF)
        if folder.is_dir() and folder.parent == content.CONTENT_DIR:
            import shutil
            shutil.rmtree(folder, ignore_errors=True)
        log.info("Test o'chirildi: %s (%s)", t.id, t.title)
    tests = content.all_tests()
    rows = [[(f"{'🟢' if x.active else '⚪'} {x.title}", f"a:t:{x.id}")] for x in tests]
    rows += [[("➕ Yangi test", "a:new")], [("⬅️ Orqaga", "a:home")]]
    await show(call, f"🗑 «{escape(t.title) if t else 'Test'}» o‘chirildi.\n\n📚 <b>Testlar</b>", ikb(*rows))


# ---------------------------------------------------------------- bo'limni o'zgartirish
@admin_router.callback_query(F.data.startswith("a:cat:"))
async def category_ask(call: CallbackQuery) -> None:
    t = content.get(arg(call))
    if not t:
        await call.answer("Test topilmadi", show_alert=True)
        return
    await show(call, f"📂 <b>{escape(t.title)}</b> qaysi bo‘limda chiqsin?\n\n"
                     f"Hozir: {content.category_path(t.category)}",
               category_kb(f"a:catset:{t.id}:", t.category, back=f"a:t:{t.id}"))


@admin_router.callback_query(F.data.startswith("a:catset:"))
async def category_set(call: CallbackQuery) -> None:
    _, _, tid, key = call.data.split(":", 3)
    t = content.get(tid)
    if not t or key not in content.LEAVES:
        await call.answer("Topilmadi", show_alert=True)
        return
    t.category = key
    content.save(t)
    audit(call.from_user, "Test bo‘limi o‘zgardi", f"{t.title} → {content.category_path(key)}")
    await open_card(call, t.id, f"✅ Bo‘lim: {content.category_path(key)}\n\n")


# ================================================================ tahrirlash
EDIT_LABELS = {"title": "nomi", "key": "kalit", "points": "ballar", "options": "variantlar", "note": "izoh"}
EDIT_PROMPTS = {
    "title": "✏️ Testning yangi nomini yozing:",
    "key": "🔑 Yangi kalitni yuboring.\n" + KEY_HELP
           + "\n\n⚠️ Kalit o‘zgarsa, shu testning <b>barcha natijalari qayta hisoblanadi</b>.",
    "points": "🎯 Yangi ballarni yozing.\n\n" + POINTS_HELP
              + "\n\n⚠️ Ballar o‘zgarsa, barcha natijalar qayta hisoblanadi.",
    "options": "🔤 Variantlarni yozing.\n\n" + OPTIONS_HELP,
    "note": "📝 O‘quvchilarga ko‘rinadigan izohni yozing (PDF bilan birga chiqadi).\n"
            "Masalan: <code>36–45 — ochiq savollar, ular video tahlilda yechib ko‘rsatiladi.</code>",
}


@admin_router.callback_query(F.data.startswith("a:e:"))
async def edit_ask(call: CallbackQuery, state: FSMContext) -> None:
    _, _, field, test_id = call.data.split(":", 3)
    t = content.get(test_id)
    if not t or field not in EDIT_PROMPTS:
        await call.answer("Test topilmadi", show_alert=True)
        return
    current = {
        "title": escape(t.title), "key": escape(grading.key_text(t.key, t.open_key)),
        "points": grading.points_text(t.points), "options": grading.options_text(t.options),
        "note": escape(t.open_questions) or "—",
    }[field]
    await state.set_state(EditTest.value)
    await state.update_data(field=field, test_id=t.id)
    rows = [[("🗑 Izohni olib tashlash", f"a:noteclear:{t.id}")]] if field == "note" and t.open_questions else []
    rows.append([("⬅️ Orqaga", f"a:t:{t.id}")])
    await show(call, f"{EDIT_PROMPTS[field]}\n\nHozirgi: <code>{current}</code>", ikb(*rows))


@admin_router.callback_query(F.data.startswith("a:noteclear:"))
async def note_clear(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    t = content.get(arg(call))
    if t:
        t.open_questions = ""
        content.save(t)
    await open_card(call, arg(call), "✅ Izoh olib tashlandi.\n\n")


@admin_router.message(EditTest.value, F.text)
async def edit_save(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    t = content.get(data.get("test_id"))
    if not t:
        await state.clear()
        await message.answer("⚠️ Test topilmadi.")
        return
    field, text = data["field"], message.text.strip()
    back = ikb([("⬅️ Orqaga", f"a:t:{t.id}")])
    done = "✅ Saqlandi."

    if field == "title":
        if not 2 <= len(text) <= 80:
            await message.answer("⚠️ Nom 2–80 belgi bo‘lishi kerak. Qayta yozing:", reply_markup=back)
            return
        t.title = text
    elif field == "note":
        t.open_questions = text[:500]
    elif field == "key":
        key, open_key, problems = grading.parse_key(text)
        if not key or problems:
            await message.answer("⚠️ Kalitda muammo: " + escape("; ".join(problems) or "javoblar topilmadi")
                                 + "\n\n" + KEY_HELP + "\n\nQayta yuboring:", reply_markup=back)
            return
        t.points, t.options, notes = fit_to_key(key, t.points, t.options)
        t.key = key
        t.open_key = open_key
        t.open_points = {q: t.open_points.get(q, 1.0) for q in open_key}
        content.save(t)
        n = regrade(t)
        done = (f"✅ Kalit saqlandi ({key_summary(key, open_key)}, jami {fmt_num(t.max_score)} ball).\n"
                f"🔁 {n} ta natija qayta hisoblandi."
                + ("\nℹ️ " + "; ".join(notes) if notes else ""))
    elif field == "points":
        try:
            t.points = grading.parse_points(text, t.questions)
        except ValueError as exc:
            await message.answer(f"⚠️ {escape(str(exc))}\n\n{POINTS_HELP}\n\nQayta yozing:", reply_markup=back)
            return
        content.save(t)
        n = regrade(t)
        done = f"✅ Ballar saqlandi: jami <b>{fmt_num(t.max_score)}</b> ball.\n🔁 {n} ta natija qayta hisoblandi."
    elif field == "options":
        try:
            options = grading.parse_options(text, t.questions)
        except ValueError as exc:
            await message.answer(f"⚠️ {escape(str(exc))}\n\n{OPTIONS_HELP}\n\nQayta yozing:", reply_markup=back)
            return
        bad = [f"{q}-savol kaliti {t.key[q]}" for q in t.questions if t.key[q] not in options[q]]
        if bad:
            await message.answer("⚠️ Kalitga mos emas: " + ", ".join(bad[:5])
                                 + f" — variantlar bunga yetmaydi.\n\n{OPTIONS_HELP}", reply_markup=back)
            return
        t.options = options
    content.save(t)
    await state.clear()
    audit(message.from_user, f"Test tahrirlandi ({EDIT_LABELS[field]})", t.title)
    if field in ("title", "key", "points"):
        storage.run(storage.note(message.bot, t, f"✏️ <b>Test tahrirlandi: {EDIT_LABELS[field]}</b>",
                                 with_key=field == "key"))
    await open_card(message, t.id, done + "\n\n")


# ---------------------------------------------------------------- PDF almashtirish
@admin_router.callback_query(F.data.startswith("a:pdf:"))
async def pdf_ask(call: CallbackQuery, state: FSMContext) -> None:
    t = content.get(arg(call))
    if not t:
        await call.answer("Test topilmadi", show_alert=True)
        return
    await state.set_state(EditTest.pdf)
    await state.update_data(test_id=t.id)
    await show(call, "📄 Yangi PDF faylni yuboring.\nKalit sahifasi bo‘lsa, bot o‘zi topib olib tashlashni taklif qiladi.",
               ikb([("⬅️ Orqaga", f"a:t:{t.id}")]))


@admin_router.message(EditTest.pdf, F.document)
async def pdf_receive(message: Message, state: FSMContext, bot: Bot) -> None:
    doc = message.document
    if not (doc.mime_type == "application/pdf" or (doc.file_name or "").lower().endswith(".pdf")):
        await message.answer("⚠️ Bu PDF emas. Iltimos, .pdf fayl yuboring.")
        return
    wait = await message.answer("⏳ PDF tekshirilmoqda...")
    info = await inspect_pdf(bot, doc)
    await state.update_data(pdf_info=info)
    await wait.delete()
    if info["key_pages"]:
        await message.answer(pdf_report(info), reply_markup=strip_kb("a:pstrip"))
    else:
        await apply_pdf(message, state, strip=False)


@admin_router.callback_query(EditTest.pdf, F.data.startswith("a:pstrip:"))
async def pdf_strip_cb(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await apply_pdf(call.message, state, strip=arg(call) == "1")


async def apply_pdf(message: Message, state: FSMContext, strip: bool) -> None:
    data = await state.get_data()
    t = content.get(data.get("test_id"))
    info = data.get("pdf_info")
    await state.clear()
    if not t or not info:
        await message.answer("⚠️ Test topilmadi.")
        return
    old_path = t.pdf_path
    t.pdf_path, t.pdf_file_id = finalize_pdf(info, t.id, strip)
    t.pdf_name = info["name"]
    content.save(t)
    if old_path and old_path != t.pdf_path:
        log.info("Eski PDF bazadan uzildi: %s", old_path)
    storage.run(storage.archive_pdf(message.bot, content.get(t.id)))
    msg = "✅ PDF almashtirildi" + (" (kalit sahifasi olib tashlandi)." if t.pdf_path and strip else ".")
    if not info["key_pages"]:
        msg = pdf_report(info) + "\n\n" + msg
    await open_card(message, t.id, msg + "\n\n")


@admin_router.message(EditTest.pdf)
async def pdf_wrong(message: Message) -> None:
    await message.answer("📄 PDF fayl yuboring (yoki «⬅️ Orqaga»).")


# ---------------------------------------------------------------- videolar
def videos_view(t: Test) -> tuple[str, object]:
    lines = [f"🎬 <b>Videolar · {escape(t.title)}</b>\n"]
    rows = []
    for i, v in enumerate(t.videos, start=1):
        lines.append(f"{i}. {escape(v.title)}")
        rows.append([(f"🗑 {i}. {v.title[:25]}", f"a:vdel:{v.id}")])
    if not t.videos:
        lines.append("Hozircha video yo‘q.")
    else:
        lines.append("\nO‘quvchilarga shu tartibda yuboriladi. O‘chirish uchun 🗑 ni bosing.")
    rows += [[("➕ Video qo‘shish", f"a:vadd:{t.id}")], [("⬅️ Orqaga", f"a:t:{t.id}")]]
    return "\n".join(lines), ikb(*rows)


@admin_router.callback_query(F.data.startswith("a:vids:"))
async def videos_cb(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    t = content.get(arg(call))
    if not t:
        await call.answer("Test topilmadi", show_alert=True)
        return
    await show(call, *videos_view(t))


@admin_router.callback_query(F.data.startswith("a:vdel:"))
async def video_delete(call: CallbackQuery) -> None:
    test_id = database.video_delete(int(arg(call)))
    t = content.get(test_id)
    if not t:
        await call.answer("Topilmadi", show_alert=True)
        return
    text, kb = videos_view(t)
    await show(call, "🗑 Video o‘chirildi.\n\n" + text, kb)


@admin_router.callback_query(F.data.startswith("a:vadd:"))
async def video_add_ask(call: CallbackQuery, state: FSMContext) -> None:
    t = content.get(arg(call))
    if not t:
        await call.answer("Test topilmadi", show_alert=True)
        return
    await state.set_state(EditTest.videos)
    await state.update_data(test_id=t.id)
    await show(call, "🎬 Videoni yuboring (yoki kanaldan forward qiling).\n"
                     "Bir nechta bo‘lsa — ketma-ket yuboravering. Izoh (caption) yozsangiz, u video nomi bo‘ladi.\n\n"
                     "💡 Siz yuborgan video hajmi cheklanmaydi va qismlarga bo‘linmaydi.",
               ikb([("✅ Tayyor", f"a:vids:{t.id}")]))


@admin_router.message(EditTest.videos)
async def video_add_receive(message: Message, state: FSMContext) -> None:
    t = content.get((await state.get_data()).get("test_id"))
    found = video_from(message)
    if not t:
        await state.clear()
        return
    if not found:
        await message.answer("🎬 Video yuboring (yoki «✅ Tayyor»).", reply_markup=ikb([("✅ Tayyor", f"a:vids:{t.id}")]))
        return
    kind, file_id = found
    title = (message.caption or "").strip()[:60] or f"{len(t.videos) + 1}-qism"
    database.video_add(t.id, kind, title, file_id=file_id)
    fresh = content.get(t.id)
    storage.run(storage.archive_video(message.bot, fresh, fresh.videos[-1]))
    await message.answer(f"✅ «{escape(title)}» qo‘shildi (jami {len(t.videos) + 1} ta).\n"
                         "Yana yuborishingiz yoki «✅ Tayyor» ni bosishingiz mumkin.",
                         reply_markup=ikb([("✅ Tayyor", f"a:vids:{t.id}")]))


# ================================================================ yangi test (7 qadam)
@admin_router.callback_query(F.data == "a:new")
async def new_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(NewTest.title)
    await state.update_data(draft={"id": content.new_id(), "videos": [], "note": ""})
    await show(call, f"➕ <b>Yangi test</b> · 1/{STEPS}\n\n✏️ Test nomini yozing.\n"
                     "Masalan: <code>9-loyiha · Fizika</code>", ikb(CANCEL))


@admin_router.message(NewTest.title, F.text)
async def new_title(message: Message, state: FSMContext) -> None:
    title = message.text.strip()
    if not 2 <= len(title) <= 80:
        await message.answer("⚠️ Nom 2–80 belgi bo‘lishi kerak. Qayta yozing:", reply_markup=ikb(CANCEL))
        return
    await put_draft(state, title=title)
    await state.set_state(NewTest.category)
    await message.answer(f"➕ <b>{escape(title)}</b> · 2/{STEPS}\n\n📂 Test qaysi bo‘limda chiqsin?",
                         reply_markup=category_kb("a:w:cat:"))


def category_kb(prefix: str, current: str | None = None, back: str | None = None):
    rows = [[(("✅ " if k == current else "") + content.category_path(k), f"{prefix}{k}")] for k in content.LEAVES]
    rows.append([("⬅️ Orqaga", back)] if back else CANCEL)
    return ikb(*rows)


@admin_router.callback_query(NewTest.category, F.data.startswith("a:w:cat:"))
async def new_category(call: CallbackQuery, state: FSMContext) -> None:
    key = arg(call)
    if key not in content.LEAVES:
        await call.answer("Bo‘lim topilmadi", show_alert=True)
        return
    d = await put_draft(state, category=key)
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await state.set_state(NewTest.pdf)
    await call.message.answer(f"✅ Bo‘lim: {content.category_path(key)}\n\n"
                              f"➕ <b>{escape(d['title'])}</b> · 3/{STEPS}\n\n📄 Test PDF faylini yuboring.\n"
                              "Agar ichida javoblar kaliti sahifasi bo‘lsa, bot uni o‘zi topadi.",
                              reply_markup=ikb(CANCEL))


@admin_router.message(NewTest.category)
async def new_category_wrong(message: Message) -> None:
    await message.answer("📂 Iltimos, yuqoridagi tugmalardan bo‘limni tanlang.", reply_markup=category_kb("a:w:cat:"))


@admin_router.message(NewTest.pdf, F.document)
async def new_pdf(message: Message, state: FSMContext, bot: Bot) -> None:
    doc = message.document
    if not (doc.mime_type == "application/pdf" or (doc.file_name or "").lower().endswith(".pdf")):
        await message.answer("⚠️ Bu PDF emas. Iltimos, .pdf fayl yuboring.", reply_markup=ikb(CANCEL))
        return
    wait = await message.answer("⏳ PDF tekshirilmoqda...")
    info = await inspect_pdf(bot, doc)
    await put_draft(state, pdf=info, strip=False)
    await wait.delete()
    if info["key_pages"]:
        await message.answer(pdf_report(info), reply_markup=strip_kb("a:w:strip"))
    else:
        await message.answer(pdf_report(info))
        await ask_key(message, state)


@admin_router.callback_query(NewTest.pdf, F.data.startswith("a:w:strip:"))
async def new_strip(call: CallbackQuery, state: FSMContext) -> None:
    strip = arg(call) == "1"
    await put_draft(state, strip=strip)
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("✂️ Kalit sahifasi o‘quvchilarga yuborilmaydi." if strip
                              else "📄 PDF shundayligicha yuboriladi.")
    await ask_key(call.message, state)


async def ask_key(message: Message, state: FSMContext) -> None:
    d = await get_draft(state)
    await state.set_state(NewTest.key)
    found = d.get("pdf", {}).get("key") or {}
    found_open = d.get("pdf", {}).get("open") or {}
    head = f"➕ <b>{escape(d['title'])}</b> · 4/{STEPS}\n\n"
    if found:
        await put_draft(state, found_key=found, found_open=found_open)
        await message.answer(head + f"🔑 PDF dan javoblar kaliti topildi ({key_summary(found, found_open)}):\n"
                             f"<code>{escape(grading.key_text(found, found_open))}</code>\n\n"
                             + ("⚠️ Yozma javoblarni diqqat bilan tekshiring — PDF dan o‘qilganda "
                                "daraja (10⁸ kabi) belgilari buzilishi mumkin.\n\n" if found_open else "")
                             + "To‘g‘rimi?",
                             reply_markup=ikb([("✅ Ha, to‘g‘ri", "a:w:keyok")],
                                              [("✏️ Yo‘q, o‘zim yozaman", "a:w:keyman")], CANCEL))
    else:
        await message.answer(head + "🔑 Javoblar kalitini yuboring.\n" + KEY_HELP, reply_markup=ikb(CANCEL))


@admin_router.callback_query(NewTest.key, F.data == "a:w:keyman")
async def new_key_manual(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("🔑 Javoblar kalitini yuboring.\n" + KEY_HELP, reply_markup=ikb(CANCEL))


@admin_router.callback_query(NewTest.key, F.data == "a:w:keyok")
async def new_key_ok(call: CallbackQuery, state: FSMContext) -> None:
    d = await get_draft(state)
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await key_accepted(call.message, state, d["found_key"], d.get("found_open") or {})


@admin_router.message(NewTest.key, F.text)
async def new_key(message: Message, state: FSMContext) -> None:
    key, open_key, problems = grading.parse_key(message.text)
    if not key or problems:
        await message.answer("⚠️ Kalitda muammo: " + escape("; ".join(problems) or "javoblar topilmadi")
                             + "\n\n" + KEY_HELP + "\n\nQayta yuboring:", reply_markup=ikb(CANCEL))
        return
    await key_accepted(message, state, key, open_key)


async def key_accepted(message: Message, state: FSMContext, key: dict[int, str],
                       open_key: dict[str, str] | None = None) -> None:
    open_key = open_key or {}
    d = await put_draft(state, key=key, open_key=open_key, open_points={q: 1.0 for q in open_key})
    top = max(key.values())
    if top <= "D":
        # standart: barcha variantli savollar A–D, har bir javob 1 ball — so'ralmaydi
        await options_accepted(message, state, {q: "ABCD" for q in key})
        return
    await state.set_state(NewTest.options)
    rows = [[("A–D (hammasida)", "a:w:opt:D"), ("A–E", "a:w:opt:E"), ("A–F", "a:w:opt:F")],
            [("✏️ Aralash (savolga qarab)", "a:w:opt:mix")], CANCEL]
    await message.answer(
        f"✅ Kalit: {key_summary(key, open_key)}.\n\n"
        f"➕ <b>{escape(d['title'])}</b> · 4/{STEPS}\n\n🔤 Savollarda nechta variant bor?"
        f"\n\nℹ️ Kalitda <b>{top}</b> harfi bor, demak ba’zi savollarda A–{top} gacha variant bor.",
        reply_markup=ikb(*rows))


@admin_router.callback_query(NewTest.options, F.data.startswith("a:w:opt:"))
async def new_options_btn(call: CallbackQuery, state: FSMContext) -> None:
    choice = arg(call)
    await call.answer()
    if choice == "mix":
        await call.message.edit_reply_markup(reply_markup=None)
        await call.message.answer("🔤 " + OPTIONS_HELP, reply_markup=ikb(CANCEL))
        return
    d = await get_draft(state)
    value = "ABCDEF"[: "ABCDEF".index(choice) + 1]
    bad = [q for q, a in d["key"].items() if a not in value]
    if bad:
        await call.message.answer(f"⚠️ {fmt_nums(sorted(bad))}-savollar kaliti A–{choice} dan tashqarida. "
                                  "«✏️ Aralash» ni tanlang yoki kattaroq variant tanlang.")
        return
    await call.message.edit_reply_markup(reply_markup=None)
    await options_accepted(call.message, state, {q: value for q in d["key"]})


@admin_router.message(NewTest.options, F.text)
async def new_options_text(message: Message, state: FSMContext) -> None:
    d = await get_draft(state)
    try:
        options = grading.parse_options(message.text, sorted(d["key"]))
    except ValueError as exc:
        await message.answer(f"⚠️ {escape(str(exc))}\n\n{OPTIONS_HELP}", reply_markup=ikb(CANCEL))
        return
    bad = [f"{q}-savol kaliti {a}" for q, a in sorted(d["key"].items()) if a not in options[q]]
    if bad:
        await message.answer("⚠️ Kalitga mos emas: " + ", ".join(bad[:5]) + f"\n\n{OPTIONS_HELP}",
                             reply_markup=ikb(CANCEL))
        return
    await options_accepted(message, state, options)


async def options_accepted(message: Message, state: FSMContext, options: dict[int, str]) -> None:
    """Variantlar tayyor → har bir javob 1 ball (standart, kartochkadan o'zgartirsa bo'ladi) → izoh."""
    d = await put_draft(state, options=options)
    await points_accepted(message, state, {q: 1.0 for q in d["key"]})


@admin_router.callback_query(NewTest.points, F.data.startswith("a:w:pts:"))
async def new_points_btn(call: CallbackQuery, state: FSMContext) -> None:
    choice = arg(call)
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    if choice == "mix":
        await call.message.answer("🎯 " + POINTS_HELP, reply_markup=ikb(CANCEL))
        return
    d = await get_draft(state)
    await points_accepted(call.message, state, {q: float(choice) for q in d["key"]})


@admin_router.message(NewTest.points, F.text)
async def new_points_text(message: Message, state: FSMContext) -> None:
    d = await get_draft(state)
    try:
        points = grading.parse_points(message.text, sorted(d["key"]))
    except ValueError as exc:
        await message.answer(f"⚠️ {escape(str(exc))}\n\n{POINTS_HELP}", reply_markup=ikb(CANCEL))
        return
    await points_accepted(message, state, points)


async def points_accepted(message: Message, state: FSMContext, points: dict[int, float]) -> None:
    d = await put_draft(state, points=points)
    await state.set_state(NewTest.note)
    total = sum(points.values()) + sum(d.get("open_points", {}).values())
    await message.answer(
        f"✅ Kalit qabul qilindi: {key_summary(d['key'], d.get('open_key', {}))}\n"
        f"🔤 Variantlar: {grading.options_text(d['options'])}\n"
        f"🎯 Har bir to‘g‘ri javob 1 ball · jami <b>{fmt_num(total)}</b> ball\n"
        "<i>(kerak bo‘lsa keyin test kartochkasidan o‘zgartirasiz)</i>\n\n"
        f"➕ <b>{escape(d['title'])}</b> · 5/{STEPS}\n\n"
        "📝 O‘quvchilarga izoh (ixtiyoriy). PDF bilan birga ko‘rinadi.\n"
        "Masalan: <code>36–45 — ochiq savollar, ular video tahlilda yechib ko‘rsatiladi.</code>",
        reply_markup=ikb([("⏭ Izohsiz davom etish", "a:w:nonote")], CANCEL))


@admin_router.message(NewTest.note, F.text)
async def new_note(message: Message, state: FSMContext) -> None:
    await put_draft(state, note=message.text.strip()[:500])
    await ask_videos(message, state)


@admin_router.callback_query(NewTest.note, F.data == "a:w:nonote")
async def new_no_note(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await ask_videos(call.message, state)


async def ask_videos(message: Message, state: FSMContext) -> None:
    d = await get_draft(state)
    await state.set_state(NewTest.videos)
    await message.answer(
        f"➕ <b>{escape(d['title'])}</b> · 6/{STEPS}\n\n"
        "🎬 Video tahlilni yuboring (yoki kanaldan forward qiling).\n"
        "Bir nechta bo‘lsa — ketma-ket yuboravering. Izoh (caption) yozsangiz, u video nomi bo‘ladi.\n\n"
        "💡 Siz yuborgan video hajmi cheklanmaydi va qismlarga bo‘linmaydi.",
        reply_markup=ikb([("⏭ Videosiz davom etish", "a:w:vdone")], CANCEL))


@admin_router.message(NewTest.videos)
async def new_video(message: Message, state: FSMContext) -> None:
    found = video_from(message)
    if not found:
        await message.answer("🎬 Video yuboring yoki «✅ Tayyor» ni bosing.",
                             reply_markup=ikb([("✅ Tayyor", "a:w:vdone")], CANCEL))
        return
    d = await get_draft(state)
    title = (message.caption or "").strip()[:60] or f"{len(d['videos']) + 1}-qism"
    d = await put_draft(state, videos=d["videos"] + [{"kind": found[0], "file_id": found[1], "title": title}])
    await message.answer(f"✅ «{escape(title)}» qabul qilindi (jami {len(d['videos'])} ta).\n"
                         "Yana yuborishingiz yoki «✅ Tayyor» ni bosishingiz mumkin.",
                         reply_markup=ikb([("✅ Tayyor", "a:w:vdone")], CANCEL))


@admin_router.callback_query(NewTest.videos, F.data == "a:w:vdone")
async def new_preview(call: CallbackQuery, state: FSMContext) -> None:
    d = await get_draft(state)
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    pdf = d.get("pdf", {})
    text = (
        "👀 <b>Tekshirib chiqing</b>\n\n"
        f"📘 Nomi: <b>{escape(d['title'])}</b>\n"
        f"📂 Bo‘lim: {content.category_path(d.get('category', content.DEFAULT_CATEGORY))}\n"
        f"📄 PDF: {escape(pdf.get('name', ''))}"
        + (" (kalit sahifasi olib tashlanadi ✂️)" if d.get("strip") and pdf.get("key_pages") else "") + "\n"
        f"❓ Savollar: {key_summary(d['key'], d.get('open_key', {}))}\n"
        f"🔑 Kalit: <tg-spoiler>{escape(grading.key_text(d['key'], d.get('open_key')))}</tg-spoiler>\n"
        f"🔤 Variantlar: {grading.options_text(d['options'])}\n"
        f"🎯 Ballar: {grading.points_text(d['points'], d.get('open_points'))} · jami "
        f"<b>{fmt_num(sum(d['points'].values()) + sum(d.get('open_points', {}).values()))}</b>\n"
        f"📝 Izoh: {escape(d['note']) or '—'}\n"
        f"🎬 Videolar: {len(d['videos'])} ta\n\n"
        "Hammasi to‘g‘ri bo‘lsa — e’lon qiling. Keyin ham istalgan narsani o‘zgartirish mumkin."
    )
    await call.message.answer(text, reply_markup=ikb(
        [("✅ E’lon qilish (o‘quvchilarga ko‘rinadi)", "a:w:pub:1")],
        [("💾 Yashirin saqlash", "a:w:pub:0")], CANCEL))


@admin_router.callback_query(NewTest.videos, F.data.startswith("a:w:pub:"))
async def new_publish(call: CallbackQuery, state: FSMContext) -> None:
    d = await get_draft(state)
    active = arg(call) == "1"
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    pdf_path, pdf_file_id = finalize_pdf(d["pdf"], d["id"], d.get("strip", False))
    t = Test(id=d["id"], title=d["title"], key=d["key"], points=d["points"], options=d["options"],
             open_questions=d["note"], active=active, pdf_path=pdf_path, pdf_file_id=pdf_file_id,
             pdf_name=d["pdf"]["name"], category=d.get("category", content.DEFAULT_CATEGORY),
             open_key=d.get("open_key", {}), open_points=d.get("open_points", {}))
    content.save(t)
    for v in d["videos"]:
        database.video_add(t.id, v["kind"], v["title"], file_id=v["file_id"])
    await state.clear()
    log.info("Yangi test: %s (%s), active=%s", t.id, t.title, active)
    audit(call.from_user, "Yangi test", t.title + ("" if active else " (yashirin)"))
    storage.run(storage.archive_test(call.bot, content.get(t.id), actor=user_name(call.from_user.id)))
    await open_card(call.message, t.id,
                    ("🎉 Test e’lon qilindi! O‘quvchilar «📝 Test yechish» bo‘limida ko‘radi."
                     if active else "💾 Test yashirin saqlandi. Tayyor bo‘lgach «🟢 O‘quvchilarga ko‘rsatish» ni bosing.")
                    + "\n\n")


# ================================================================ test qoidalari (2-bosqich)
def rules_view(t: Test) -> tuple[str, object]:
    lines = [
        f"⚙️ <b>Qoidalar · {escape(t.title)}</b>\n",
        f"Hozir: <b>{STATUS_NAMES[t.status()]}</b>",
        f"▶️ Ochilish: <b>{timeutil.fmt(t.starts_at) if t.starts_at else 'darhol'}</b>",
        f"⏹ Yopilish: <b>{timeutil.fmt(t.ends_at) if t.ends_at else 'muddatsiz'}</b>",
        f"🔢 Urinishlar: <b>{t.max_attempts if t.max_attempts else 'cheksiz'}</b>",
        f"👀 To‘g‘ri javoblar: <b>{SHOW_NAMES[t.show_mode]}</b>",
        f"🎬 Video tahlil: <b>{VIDEO_NAMES[t.video_lock]}</b>",
    ]
    if t.revealed:
        lines.append("\n📢 Tahlil o‘quvchilarga yuborib bo‘lingan.")
    if t.show_mode == "after" and not t.ends_at:
        lines.append("\n⚠️ Yopilish vaqti qo‘yilmagan — to‘g‘ri javoblar hech qachon yuborilmaydi.")
    if t.video_lock == "deadline" and not t.ends_at:
        lines.append("\n⚠️ Yopilish vaqti qo‘yilmagan — video hech qachon ochilmaydi.")
    if t.show_mode == "now":
        lines.append("\n💡 Javoblar darhol ko‘rsatilsa, birinchi yechgan o‘quvchi ularni boshqalarga "
                     "tarqatishi mumkin. Nazorat testlari uchun «muddat tugagach» ni tanlang.")
    lines.append("\nO‘zgartirish uchun tugmani bosing 👇")
    kb = ikb(
        [("▶️ Ochilish vaqti", f"a:rtime:start:{t.id}"), ("⏹ Yopilish vaqti", f"a:rtime:end:{t.id}")],
        [(f"🔢 Urinishlar: {t.max_attempts or '♾'}", f"a:ratt:{t.id}")],
        [(f"👀 Javoblar: {SHOW_SHORT[t.show_mode]} 🔄", f"a:rshow:{t.id}")],
        [(f"🎬 Video: {VIDEO_SHORT[t.video_lock]} 🔄", f"a:rvid:{t.id}")],
        [("🗑 Muddatni olib tashlash", f"a:rclear:{t.id}")] if (t.starts_at or t.ends_at) else [],
        [("⬅️ Orqaga", f"a:t:{t.id}")],
    )
    return "\n".join(lines), kb


async def open_rules(target, test_id: str, prefix: str = "") -> None:
    t = content.get(test_id)
    if not t:
        await show(target, "⚠️ Test topilmadi.", ikb([("⬅️ Testlar", "a:tests")]))
        return
    text, kb = rules_view(t)
    await show(target, prefix + text, kb)


@admin_router.callback_query(F.data.startswith("a:rules:"))
async def rules_cb(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await open_rules(call, arg(call))


@admin_router.callback_query(F.data.startswith("a:rshow:"))
async def rules_show_cycle(call: CallbackQuery) -> None:
    t = content.get(arg(call))
    if t:
        t.show_mode = NEXT_SHOW[t.show_mode]
        content.save(t)
    await open_rules(call, arg(call))


@admin_router.callback_query(F.data.startswith("a:rvid:"))
async def rules_video_cycle(call: CallbackQuery) -> None:
    t = content.get(arg(call))
    if t:
        t.video_lock = NEXT_VIDEO[t.video_lock]
        content.save(t)
    await open_rules(call, arg(call))


@admin_router.callback_query(F.data.startswith("a:ratt:"))
async def rules_attempts_ask(call: CallbackQuery) -> None:
    tid = arg(call)
    await show(call, "🔢 Har bir o‘quvchi bu testni necha marta yecha oladi?",
               ikb([(str(n), f"a:rattn:{n}:{tid}") for n in (1, 2, 3, 5)],
                   [("♾ Cheksiz", f"a:rattn:0:{tid}")], [("⬅️ Orqaga", f"a:rules:{tid}")]))


@admin_router.callback_query(F.data.startswith("a:rattn:"))
async def rules_attempts_set(call: CallbackQuery) -> None:
    _, _, n, tid = call.data.split(":", 3)
    t = content.get(tid)
    if t:
        t.max_attempts = int(n)
        content.save(t)
    await open_rules(call, tid, "✅ Saqlandi.\n\n")


@admin_router.callback_query(F.data.startswith("a:rclear:"))
async def rules_clear(call: CallbackQuery) -> None:
    t = content.get(arg(call))
    if t:
        t.starts_at = t.ends_at = None
        t.revealed = False
        content.save(t)
    await open_rules(call, arg(call), "🗑 Muddat olib tashlandi — test doim ochiq.\n\n")


TIME_HELP = ("Sana va vaqtni yozing (O‘zbekiston vaqti):\n"
             "<code>25.11 18:00</code> — 25-noyabr, soat 18:00\n"
             "<code>ertaga 08:30</code> · <code>bugun 21:00</code> · <code>25.11</code> (soat 23:59)")


@admin_router.callback_query(F.data.startswith("a:rtime:"))
async def rules_time_ask(call: CallbackQuery, state: FSMContext) -> None:
    _, _, which, tid = call.data.split(":", 3)
    if not content.get(tid):
        await call.answer("Test topilmadi", show_alert=True)
        return
    await state.set_state(EditTest.time)
    await state.update_data(which=which, test_id=tid)
    if which == "start":
        title = "▶️ <b>Test qachon ochilsin?</b>\nShu vaqtgacha o‘quvchilar testni ocha olmaydi."
        quick = [[("Hozirdan ochiq", f"a:rquick:start:now:{tid}")]]
    else:
        title = "⏹ <b>Test qachon yopilsin?</b>\nShu vaqtdan keyin javoblar qabul qilinmaydi."
        quick = [[("Bugun 23:59", f"a:rquick:end:today:{tid}"), ("Ertaga 23:59", f"a:rquick:end:tomorrow:{tid}")],
                 [("3 kundan keyin", f"a:rquick:end:3d:{tid}"), ("1 haftadan keyin", f"a:rquick:end:7d:{tid}")]]
    await show(call, f"{title}\n\n{TIME_HELP}\n\nHozir: {timeutil.fmt(timeutil.now())}",
               ikb(*quick, [("⬅️ Orqaga", f"a:rules:{tid}")]))


async def apply_time(target, state: FSMContext, which: str, tid: str, dt) -> None:
    t = content.get(tid)
    if not t:
        await state.clear()
        return
    start = dt if which == "start" else timeutil.parse_iso(t.starts_at)
    end = dt if which == "end" else timeutil.parse_iso(t.ends_at)
    if start and end and end <= start:
        await show(target, f"⚠️ Yopilish vaqti ({timeutil.fmt(end)}) ochilish vaqtidan "
                           f"({timeutil.fmt(start)}) keyin bo‘lishi kerak. Qayta yozing:",
                   ikb([("⬅️ Orqaga", f"a:rules:{tid}")]))
        return
    if which == "start":
        t.starts_at = timeutil.iso(dt) if dt > timeutil.now() else None
    else:
        t.ends_at = timeutil.iso(dt)
        t.revealed = False
    content.save(t)
    await state.clear()
    audit(target.from_user, "Muddat o‘zgardi", f"{t.title}: "
          + ("ochilish " + timeutil.fmt(t.starts_at) if which == "start" else "yopilish " + timeutil.fmt(t.ends_at)))
    note = "✅ Saqlandi."
    if which == "end" and dt <= timeutil.now():
        note = "⚠️ Bu vaqt o‘tib ketgan — test hozirdan yopiq hisoblanadi."
        if t.show_mode == "after":
            note += " Tahlil bir daqiqa ichida o‘quvchilarga yuboriladi."
    await open_rules(target, tid, note + "\n\n")


@admin_router.callback_query(F.data.startswith("a:rquick:"))
async def rules_time_quick(call: CallbackQuery, state: FSMContext) -> None:
    _, _, which, code, tid = call.data.split(":", 4)
    await apply_time(call, state, which, tid, timeutil.quick(code))


@admin_router.message(EditTest.time, F.text)
async def rules_time_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        dt = timeutil.parse(message.text)
    except (ValueError, OverflowError):
        await message.answer("⚠️ Sanani tushunmadim.\n\n" + TIME_HELP,
                             reply_markup=ikb([("⬅️ Orqaga", f"a:rules:{data['test_id']}")]))
        return
    await apply_time(message, state, data["which"], data["test_id"], dt)


# ================================================================ xabar yuborish (rassilka)
AUD_NAMES = {"all": "hammaga", "solved": "testni yechganlarga", "unsolved": "testni yechmaganlarga"}
_broadcasting: set[int] = set()


def bc_recipients(bc: dict, sender: int) -> list[int]:
    return [u for u in database.recipients(bc["aud"], bc.get("test_id")) if u != sender]


def bc_audience_text(bc: dict) -> str:
    text = AUD_NAMES[bc["aud"]]
    if bc.get("test_id"):
        t = content.get(bc["test_id"])
        text += f" («{escape(t.title) if t else bc['test_id']}»)"
    return text


def bc_markup(bc: dict):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    rows = []
    for text, kind, value in bc.get("buttons", []):
        rows.append([InlineKeyboardButton(text=text, url=value) if kind == "url"
                     else InlineKeyboardButton(text=text, callback_data=value)])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def bc_send_one(bot: Bot, chat_id: int, bc: dict) -> None:
    if bc.get("src"):
        await bot.copy_message(chat_id, bc["src"][0], bc["src"][1], reply_markup=bc_markup(bc))
    else:
        await bot.send_message(chat_id, bc["text"], reply_markup=bc_markup(bc))


@admin_router.callback_query(F.data == "a:bc")
async def bc_start(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    n = len(bc_recipients({"aud": "all"}, call.from_user.id))
    await show(call, "📢 <b>Xabar yuborish</b>\n\nKimga yuboramiz?",
               ikb([(f"👥 Hammaga ({n})", "a:bca:all")],
                   [("✅ Testni yechganlarga", "a:bca:solved")],
                   [("⏰ Testni yechmaganlarga (eslatma)", "a:bca:unsolved")],
                   [("⬅️ Orqaga", "a:home")]))


@admin_router.callback_query(F.data.startswith("a:bca:"))
async def bc_audience(call: CallbackQuery, state: FSMContext) -> None:
    aud = arg(call)
    if aud == "all":
        await bc_ask_content(call, state, {"aud": "all", "buttons": []})
        return
    rows = [[(t.title, f"a:bct:{aud}:{t.id}")] for t in content.all_tests()]
    await show(call, f"📘 Qaysi testni {'yechganlarga' if aud == 'solved' else 'yechmaganlarga'}?",
               ikb(*rows, [("⬅️ Orqaga", "a:bc")]))


@admin_router.callback_query(F.data.startswith("a:bct:"))
async def bc_audience_test(call: CallbackQuery, state: FSMContext) -> None:
    _, _, aud, tid = call.data.split(":", 3)
    await bc_ask_content(call, state, {"aud": aud, "test_id": tid, "buttons": []})


async def bc_ask_content(target, state: FSMContext, bc: dict) -> None:
    await state.set_state(Broadcast.content)
    await state.update_data(bc=bc)
    n = len(bc_recipients(bc, target.from_user.id))
    await show(target, f"📢 Kimga: <b>{bc_audience_text(bc)}</b> — {n} kishi\n\n"
                       "✍️ Endi xabarni yuboring: matn, rasm, video, fayl yoki ovozli xabar.\n"
                       "U aynan siz yuborgan ko‘rinishda boradi.", ikb(CANCEL))


@admin_router.message(Broadcast.content)
async def bc_content(message: Message, state: FSMContext) -> None:
    bc = (await state.get_data()).get("bc", {})
    bc["src"] = [message.chat.id, message.message_id]
    bc.pop("text", None)
    await state.update_data(bc=bc)
    await state.set_state(None)
    await bc_options(message, state)


async def bc_options(target, state: FSMContext, prefix: str = "") -> None:
    bc = (await state.get_data()).get("bc", {})
    btns = "\n".join(f"   • {escape(b[0])}" for b in bc.get("buttons", []))
    await show(target, prefix + "📢 Xabar tayyor."
               + (f"\nTugmalar:\n{btns}" if btns else "")
               + "\n\nTugma qo‘shasizmi yoki ko‘rib chiqamizmi?",
               ikb([("📘 «Testni ochish» tugmasi", "a:bcbtn:test"), ("🔗 Havola tugmasi", "a:bcbtn:url")],
                   [("👀 Ko‘rib chiqish va yuborish", "a:bcprev")], CANCEL))


@admin_router.callback_query(F.data == "a:bcbtn:test")
async def bc_btn_test(call: CallbackQuery) -> None:
    rows = [[(t.title, f"a:bcbt:{t.id}")] for t in content.all_tests(active_only=True)]
    await show(call, "📘 Qaysi test tugmasi qo‘shilsin?", ikb(*rows, [("⬅️ Orqaga", "a:bcopt")]))


@admin_router.callback_query(F.data.startswith("a:bcbt:"))
async def bc_btn_test_add(call: CallbackQuery, state: FSMContext) -> None:
    t = content.get(arg(call))
    bc = (await state.get_data()).get("bc")
    if not t or bc is None:
        await stale_button(call, state)
        return
    bc.setdefault("buttons", []).append([f"📘 {t.title}", "cb", f"test:{t.id}"])
    await state.update_data(bc=bc)
    await bc_options(call, state, "✅ Tugma qo‘shildi.\n\n")


@admin_router.callback_query(F.data == "a:bcopt")
async def bc_back_options(call: CallbackQuery, state: FSMContext) -> None:
    if (await state.get_data()).get("bc") is None:
        await stale_button(call, state)
        return
    await state.set_state(None)
    await bc_options(call, state)


@admin_router.callback_query(F.data == "a:bcbtn:url")
async def bc_btn_url(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Broadcast.url)
    await show(call, "🔗 Tugma nomi va havolani yozing:\n<code>Kanalimiz | https://t.me/kanal_nomi</code>",
               ikb([("⬅️ Orqaga", "a:bcopt")]))


@admin_router.message(Broadcast.url, F.text)
async def bc_btn_url_add(message: Message, state: FSMContext) -> None:
    name, sep, url = message.text.partition("|")
    name, url = name.strip(), url.strip()
    if not sep or not name or not re.match(r"^(https?://|tg://)\S+$", url):
        await message.answer("⚠️ Ko‘rinishi: <code>Nomi | https://...</code>", reply_markup=ikb([("⬅️ Orqaga", "a:bcopt")]))
        return
    data = await state.get_data()
    bc = data.get("bc", {})
    bc.setdefault("buttons", []).append([name[:40], "url", url])
    await state.update_data(bc=bc)
    await state.set_state(None)
    await bc_options(message, state, "✅ Tugma qo‘shildi.\n\n")


@admin_router.callback_query(F.data == "a:bcprev")
async def bc_preview(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    bc = (await state.get_data()).get("bc")
    if not bc or not (bc.get("src") or bc.get("text")):
        await stale_button(call, state)
        return
    await call.answer()
    await call.message.answer("👀 <b>O‘quvchilar xabarni mana shunday ko‘radi:</b>")
    await bc_send_one(bot, call.message.chat.id, bc)
    n = len(bc_recipients(bc, call.from_user.id))
    rows = [[(f"✅ Ha, {n} kishiga yuborish", "a:bcsend")]]
    if bc.get("test_id"):
        other = {"all": "unsolved", "unsolved": "solved", "solved": "all"}[bc["aud"]]
        rows.append([(f"👥 Kimga: {AUD_NAMES[bc['aud']]} 🔄", f"a:bcaud:{other}")])
    rows += [[("✏️ Boshqa xabar yozish", "a:bcre")], CANCEL]
    await call.message.answer(f"📢 Kimga: <b>{bc_audience_text(bc)}</b>\n👥 Qabul qiluvchilar: <b>{n}</b> kishi\n\n"
                              "Yuboraymi?", reply_markup=ikb(*rows))


@admin_router.callback_query(F.data.startswith("a:bcaud:"))
async def bc_switch_audience(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    bc = (await state.get_data()).get("bc")
    if not bc:
        await stale_button(call, state)
        return
    bc["aud"] = arg(call)
    await state.update_data(bc=bc)
    n = len(bc_recipients(bc, call.from_user.id))
    other = {"all": "unsolved", "unsolved": "solved", "solved": "all"}[bc["aud"]]
    await show(call, f"📢 Kimga: <b>{bc_audience_text(bc)}</b>\n👥 Qabul qiluvchilar: <b>{n}</b> kishi\n\nYuboraymi?",
               ikb([(f"✅ Ha, {n} kishiga yuborish", "a:bcsend")],
                   [(f"👥 Kimga: {AUD_NAMES[bc['aud']]} 🔄", f"a:bcaud:{other}")],
                   [("✏️ Boshqa xabar yozish", "a:bcre")], CANCEL))


@admin_router.callback_query(F.data == "a:bcre")
async def bc_rewrite(call: CallbackQuery, state: FSMContext) -> None:
    bc = (await state.get_data()).get("bc")
    if not bc:
        await stale_button(call, state)
        return
    bc.pop("src", None)
    bc.pop("text", None)
    await bc_ask_content(call, state, bc)


@admin_router.callback_query(F.data == "a:bcsend")
async def bc_send(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    bc = (await state.get_data()).get("bc")
    if not bc:
        await stale_button(call, state)
        return
    if call.from_user.id in _broadcasting:
        await call.answer("⏳ Oldingi xabar hali yuborilmoqda.", show_alert=True)
        return
    await state.clear()
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    ids = bc_recipients(bc, call.from_user.id)
    progress = await call.message.answer(f"📤 Yuborilmoqda... 0 / {len(ids)}")
    _broadcasting.add(call.from_user.id)
    asyncio.create_task(run_broadcast(bot, bc, ids, progress, call.from_user.id))


async def run_broadcast(bot: Bot, bc: dict, ids: list[int], progress: Message, admin_id: int) -> None:
    from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
    import time
    ok = blocked = failed = 0
    last = time.monotonic()
    try:
        for i, uid in enumerate(ids, start=1):
            for _ in range(2):
                try:
                    await bc_send_one(bot, uid, bc)
                    ok += 1
                    break
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(exc.retry_after + 1)
                except TelegramForbiddenError:
                    blocked += 1
                    database.set_bot_blocked(uid, True)
                    break
                except Exception as exc:
                    failed += 1
                    log.warning("Rassilka: %s ga yuborilmadi: %s", uid, exc)
                    break
            await asyncio.sleep(0.05)          # Telegram cheklovi: ~20 xabar/soniya
            if time.monotonic() - last > 3:
                last = time.monotonic()
                try:
                    await progress.edit_text(f"📤 Yuborilmoqda... {i} / {len(ids)}")
                except Exception:
                    pass
    finally:
        _broadcasting.discard(admin_id)
    log.info("Rassilka tugadi: %s ta, bloklagan %s, xato %s", ok, blocked, failed)
    database.audit(admin_id, "Xabar yuborildi", f"{bc_audience_text(bc)}: {ok} ta yetkazildi")
    report = (f"✅ <b>Xabar yuborildi</b>\n\n📬 Yetkazildi: <b>{ok}</b>\n"
              + (f"📵 Botni bloklagan: {blocked}\n" if blocked else "")
              + (f"⚠️ Xato: {failed}\n" if failed else ""))
    try:
        await progress.edit_text(report, reply_markup=ikb([("⚙️ Admin panel", "a:home")]))
    except Exception:
        await bot.send_message(admin_id, report)


@admin_router.callback_query(F.data.startswith("a:ann:"))
async def announce_test(call: CallbackQuery, state: FSMContext) -> None:
    t = content.get(arg(call))
    if not t:
        await call.answer("Test topilmadi", show_alert=True)
        return
    if not t.active:
        await call.answer("Test yashirin — avval «🟢 O‘quvchilarga ko‘rsatish» ni bosing.", show_alert=True)
        return
    text = (f"🆕 <b>Yangi test: {escape(t.title)}</b>\n\n"
            f"📂 {content.category_path(t.category)}\n"
            f"❓ {t.last_question} ta savol ({key_summary(t.key, t.open_key)}) · {fmt_num(t.max_score)} ball\n"
            + (f"▶️ Ochiladi: {timeutil.fmt(t.starts_at)}\n" if t.starts_at else "")
            + (f"⏰ Muddat: <b>{timeutil.fmt(t.ends_at)}</b> gacha\n" if t.ends_at else "")
            + (f"🔢 Urinishlar: {t.max_attempts} ta\n" if t.max_attempts else "")
            + "\n👇 Testni ochish uchun tugmani bosing. Omad! 🍀")
    await state.clear()
    await state.update_data(bc={"aud": "all", "test_id": t.id, "text": text,
                                "buttons": [[f"📘 {t.title}", "cb", f"test:{t.id}"]]})
    await call.message.answer("📢 E’lon tayyorlandi. Xabarni o‘zgartirmoqchi bo‘lsangiz, «✏️ Boshqa xabar yozish» ni bosing.")
    await bc_preview(call, state, call.bot)


# ================================================================ foydalanuvchilar (2-bosqich)
def user_label(u) -> str:
    name = " ".join(x for x in (u["first_name"], u["last_name"]) if x) or str(u["telegram_id"])
    return ("🚫 " if u["banned"] else "") + name + (f" @{u['username']}" if u["username"] else "")


def users_list_kb(rows, back: str = "a:users"):
    return ikb(*[[(user_label(u)[:60], f"a:u:{u['telegram_id']}")] for u in rows],
               [("🔍 Qidirish", "a:usearch"), ("⬅️ Orqaga", back)])


@admin_router.callback_query(F.data == "a:users")
async def users_cb(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    s = database.stats()
    await show(call,
               "👥 <b>Foydalanuvchilar</b>\n\n"
               f"Jami: <b>{s['users']}</b> · bugun faol: {s['active_today']}\n"
               f"🚫 Siz bloklaganlar: {s['banned']}\n"
               f"📵 Botni bloklaganlar: {s['bot_blocked']}\n\n"
               "Ism, @username yoki ID bo‘yicha qidiring 👇",
               ikb([("🔍 Qidirish", "a:usearch")],
                   [("🕒 Oxirgi faollar", "a:ulast"), (f"🚫 Bloklanganlar ({s['banned']})", "a:ubanned")],
                   [("⬅️ Orqaga", "a:home")]))


@admin_router.callback_query(F.data == "a:usearch")
async def users_search_ask(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Users.search)
    await show(call, "🔍 Ism, familiya, @username yoki Telegram ID ni yozing:", ikb([("⬅️ Orqaga", "a:users")]))


@admin_router.message(Users.search, F.text)
async def users_search(message: Message, state: FSMContext) -> None:
    rows = database.find_users(message.text)
    await state.clear()
    if not rows:
        await message.answer(f"🤷 «{escape(message.text)}» bo‘yicha hech kim topilmadi.",
                             reply_markup=ikb([("🔍 Qayta qidirish", "a:usearch"), ("⬅️ Orqaga", "a:users")]))
        return
    await message.answer(f"🔍 Topildi: {len(rows)} ta", reply_markup=users_list_kb(rows))


@admin_router.callback_query(F.data == "a:ulast")
async def users_last(call: CallbackQuery) -> None:
    rows = database.users_where("1", 15)
    await show(call, "🕒 <b>Oxirgi faol foydalanuvchilar</b>", users_list_kb(rows))


@admin_router.callback_query(F.data == "a:ubanned")
async def users_banned(call: CallbackQuery) -> None:
    rows = database.users_where("banned = 1", 30)
    await show(call, "🚫 <b>Bloklanganlar</b>" if rows else "🚫 Bloklangan foydalanuvchi yo‘q.", users_list_kb(rows))


def user_view(uid: int) -> tuple[str, object]:
    u = database.user_row(uid)
    if not u:
        return "⚠️ Foydalanuvchi topilmadi.", ikb([("⬅️ Orqaga", "a:users")])
    best = database.user_best_results(uid)
    titles = {t.id: t.title for t in content.all_tests()}
    status = "🚫 Siz bloklagansiz" if u["banned"] else "📵 Botni bloklagan" if u["bot_blocked"] else "✅ Faol"
    is_adm = uid in access.admin_chat_ids()
    lines = [
        f"👤 <b>{escape(' '.join(x for x in (u['first_name'], u['last_name']) if x) or '—')}</b>"
        + (f" (@{escape(u['username'])})" if u["username"] else "") + (" · ⚙️ admin" if is_adm else ""),
        f"🆔 <code>{uid}</code>",
        f"📅 Ro‘yxatdan o‘tgan: {timeutil.fmt(u['registered_at'][:16])}",
        f"🕒 Oxirgi faollik: {timeutil.fmt(u['last_seen'][:16])}",
        f"Holati: {status}",
        f"\n📝 Yechgan testlar: <b>{len(best)}</b> ta, {sum(r['attempts'] for r in best)} urinish",
    ]
    for r in best:
        pct = round(r["score"] / r["max_score"] * 100) if r["max_score"] else 0
        lines.append(f"   • {escape(titles.get(r['test_id'], r['test_id']))} — {fmt_num(r['score'])} / "
                     f"{fmt_num(r['max_score'])} ({pct}%)" + (f" · {r['attempts']} urinish" if r["attempts"] > 1 else ""))
    rows = []
    if not is_adm:
        rows.append([("✅ Blokdan chiqarish", f"a:unban:{uid}") if u["banned"] else ("🚫 Bloklash", f"a:ban:{uid}")])
    rows += [[("✉️ Shaxsiy xabar yozish", f"a:umsg:{uid}")], [("⬅️ Foydalanuvchilar", "a:users")]]
    return "\n".join(lines), ikb(*rows)


@admin_router.callback_query(F.data.startswith("a:u:"))
async def user_cb(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(call, *user_view(int(arg(call))))


@admin_router.callback_query(F.data.startswith("a:ban:") | F.data.startswith("a:unban:"))
async def user_ban(call: CallbackQuery) -> None:
    uid = int(arg(call))
    ban = call.data.startswith("a:ban:")
    if ban and uid in access.admin_chat_ids():
        await call.answer("Adminni bloklab bo‘lmaydi.", show_alert=True)
        return
    database.set_banned(uid, ban)
    log.info("Foydalanuvchi %s: banned=%s", uid, ban)
    audit(call.from_user, "Bloklandi" if ban else "Blokdan chiqarildi", user_name(uid))
    text, kb = user_view(uid)
    await show(call, ("🚫 Bloklandi — endi botdan foydalana olmaydi.\n\n" if ban else "✅ Blokdan chiqarildi.\n\n") + text, kb)


@admin_router.callback_query(F.data.startswith("a:umsg:"))
async def user_msg_ask(call: CallbackQuery, state: FSMContext) -> None:
    uid = int(arg(call))
    await state.set_state(Users.message)
    await state.update_data(target=uid)
    await show(call, "✉️ Xabarni yozing (matn, rasm, fayl...). U shu o‘quvchiga yuboriladi.",
               ikb([("⬅️ Orqaga", f"a:u:{uid}")]))


@admin_router.message(Users.message)
async def user_msg_send(message: Message, state: FSMContext, bot: Bot) -> None:
    uid = (await state.get_data()).get("target")
    await state.clear()
    try:
        await bot.send_message(uid, "✉️ <b>O‘qituvchidan xabar:</b>")
        await bot.copy_message(uid, message.chat.id, message.message_id)
        result = "✅ Xabar yetkazildi."
    except Exception as exc:
        from aiogram.exceptions import TelegramForbiddenError
        if isinstance(exc, TelegramForbiddenError):
            database.set_bot_blocked(uid, True)
            result = "📵 Yetkazilmadi — bu foydalanuvchi botni bloklagan."
        else:
            result = f"⚠️ Yetkazilmadi: {escape(str(exc))}"
    text, kb = user_view(uid)
    await message.answer(result + "\n\n" + text, reply_markup=kb)


def user_name(uid: int) -> str:
    u = database.user_row(uid)
    if not u:
        return str(uid)
    name = " ".join(x for x in (u["first_name"], u["last_name"]) if x) or str(uid)
    return name + (f" (@{u['username']})" if u["username"] else "")


# ================================================================ savollar tahlili
@admin_router.callback_query(F.data.startswith("a:qa:"))
async def question_analysis(call: CallbackQuery) -> None:
    t = content.get(arg(call))
    if not t:
        await call.answer("Test topilmadi", show_alert=True)
        return
    back = ikb([("📥 Excel (batafsil)", f"a:x:{t.id}"), ("⬅️ Orqaga", f"a:t:{t.id}")])
    best = database.best_results(t.id)
    if not best:
        await show(call, f"📈 <b>{escape(t.title)}</b>\n\nHali hech kim yechmagan.", back)
        return
    pairs = [grading.split_answers(json.loads(r["answers"])) for r in best]
    stats = []   # (foiz, tartib, nomi, ko'p tanlangan xato, soni)
    for q in t.questions:
        given = [mc.get(q) for mc, _ in pairs]
        ok = sum(g == t.key[q] for g in given)
        wrong = [g for g in given if g and g != t.key[q]]
        top = max(set(wrong), key=wrong.count) if wrong else None
        stats.append((round(ok / len(given) * 100), (q, ""), f"{q}-savol", top, wrong.count(top) if top else 0))
    for qid in sorted(t.open_key, key=grading.open_sort):
        given = [op.get(qid) for _, op in pairs]
        ok = sum(grading.same_answer(g, t.open_key[qid]) for g in given)
        wrong = [g.strip() for g in given if g and not grading.same_answer(g, t.open_key[qid])]
        top = max(set(wrong), key=wrong.count) if wrong else None
        stats.append((round(ok / len(given) * 100), grading.open_sort(qid),
                      f"{grading.open_label(qid)}", top, wrong.count(top) if top else 0))
    mark = lambda p: "🔴" if p < 40 else "🟡" if p < 70 else "🟢"  # noqa: E731
    hard = [s for s in sorted(stats, key=lambda s: (s[0], s[1])) if s[0] < 100][:10]
    easy = [s for s in sorted(stats, key=lambda s: (-s[0], s[1])) if s not in hard][:5]
    weak = [s[2] for s in sorted(stats, key=lambda s: s[1]) if s[0] < 70]
    lines = [f"📈 <b>Savollar tahlili · {escape(t.title)}</b>",
             f"👥 {len(best)} o‘quvchining eng yaxshi urinishi bo‘yicha\n",
             "<b>Eng qiyin savollar:</b>" if hard else "🎉 Hamma savollar to‘g‘ri yechilgan!"]
    for p, _, name, top, n in hard:
        lines.append(f"{mark(p)} {name} — {p}% to‘g‘ri"
                     + (f" · ko‘p yozilgan xato: <b>{escape(top[:20])}</b> ({n})" if top else ""))
    lines.append("\n<b>Eng oson savollar:</b>")
    lines += [f"{mark(p)} {name} — {p}% to‘g‘ri" for p, _, name, _, _ in easy]
    if weak:
        lines.append(f"\n💡 70% dan kam to‘g‘ri yechilganlar ({len(weak)} ta): <b>{escape(', '.join(weak))}</b>\n"
                     "Shu mavzularni qayta tushuntirish tavsiya etiladi.")
    else:
        lines.append("\n🎉 Barcha savollar 70% dan ko‘p to‘g‘ri yechilgan!")
    await show(call, "\n".join(lines), back)


# ================================================================ sozlamalar (faqat bosh admin)
def settings_view() -> tuple[str, object]:
    ch = subscribe.saved_channel()
    sub_on = database.setting("sub_on", "0") == "1"
    notify = database.setting("notify", "1") == "1"
    auto = database.setting("backup_auto", "1") == "1"
    last = database.setting("last_backup")
    text = ("⚙️ <b>Sozlamalar</b>\n\n"
            f"👮 Adminlar: {len(access.owner_ids())} bosh, {len(database.staff())} yordamchi\n"
            f"📌 Majburiy obuna: {('🟢 ' + escape(ch['title'])) if ch and sub_on else '⚪ o‘chirilgan'}\n"
            f"🗄 Ombor kanal: {('🟢 ' + escape(st['title'])) if (st := storage.channel()) else '⚪ o‘chirilgan'}\n"
            f"🏅 Sertifikat: {'🟢 beriladi' if certificate.enabled() else '⚪ berilmaydi'}"
            f" · {database.cert_count()} ta berilgan\n"
            f"💾 Avto-zaxira: {'har kuni kechqurun' if auto else 'o‘chirilgan'}"
            f" · oxirgisi: {timeutil.fmt(last) if last else '—'}\n"
            f"🔔 Natija xabarlari: {'yoqilgan' if notify else 'o‘chirilgan'}")
    kb = ikb([("👮 Adminlar", "a:admins"), ("📌 Majburiy obuna", "a:sub")],
             [("🗄 Ombor kanal", "a:stor"), ("🏅 Sertifikat", "a:cert")],
             [("💾 Zaxira nusxa olish", "a:backup"), (f"🔁 Avto-zaxira: {'yoqilgan' if auto else 'o‘chiq'}", "a:backupauto")],
             [(f"🔔 Natija xabarlari: {'yoqilgan' if notify else 'o‘chiq'}", "a:notify"),
              ("📜 Harakatlar jurnali", "a:log")],
             [("⬅️ Orqaga", "a:home")])
    return text, kb


@admin_router.callback_query(F.data == "a:settings")
async def settings_cb(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(call, *settings_view())


# ---------------------------------------------------------------- zaxira va jurnal
@admin_router.callback_query(F.data == "a:backup")
async def backup_cb(call: CallbackQuery, bot: Bot) -> None:
    await call.answer("💾 Zaxira tayyorlanmoqda...")
    await backup.make_and_send(bot, {call.from_user.id}, "qo‘lda olingan")
    audit(call.from_user, "Zaxira nusxa olindi")


@admin_router.callback_query(F.data == "a:backupauto")
async def backup_auto_cb(call: CallbackQuery) -> None:
    on = database.setting("backup_auto", "1") == "1"
    database.set_setting("backup_auto", "0" if on else "1")
    audit(call.from_user, "Avto-zaxira " + ("o‘chirildi" if on else "yoqildi"))
    await show(call, *settings_view())


@admin_router.callback_query(F.data == "a:log")
async def log_cb(call: CallbackQuery) -> None:
    rows = database.audit_rows(25)
    lines = ["📜 <b>Harakatlar jurnali</b> (oxirgi 25 ta)\n"]
    for r in rows:
        who = r["first_name"] or (f"@{r['username']}" if r["username"] else str(r["admin_id"] or "bot"))
        lines.append(f"<i>{timeutil.fmt(r['at'][:16])}</i> · {escape(who)}: {escape(r['action'])}"
                     + (f" — {escape(r['details'])}" if r["details"] else ""))
    if not rows:
        lines.append("Hozircha yozuv yo‘q.")
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3990] + "\n…"
    await show(call, text, ikb([("⬅️ Sozlamalar", "a:settings")]))


# ---------------------------------------------------------------- yordamchi adminlar
def admins_view() -> tuple[str, object]:
    lines = ["👮 <b>Adminlar</b>\n", "<b>👑 Bosh admin</b> (.env dagi ADMIN_USERNAME):"]
    owners = access.owner_ids()
    lines += [f"• {escape(user_name(o))}" for o in owners] or ["• —"]
    helpers = database.staff()
    lines.append("\n<b>🧑‍🏫 Yordamchi adminlar:</b>")
    lines += [f"• {escape(user_name(h['telegram_id']))}" for h in helpers] or ["• hozircha yo‘q"]
    lines.append("\nℹ️ Yordamchi admin: test qo‘shadi va tahrirlaydi, qoidalarni o‘zgartiradi, natijalar, "
                 "reyting, Excel va foydalanuvchilarni ko‘radi.\n"
                 "🔒 Qila olmaydi: test o‘chirish, xabar yuborish, bloklash, sozlamalar, adminlarni boshqarish.")
    rows = [[(f"❌ {user_name(h['telegram_id'])[:40]}", f"a:admdel:{h['telegram_id']}")] for h in helpers]
    rows += [[("➕ Yordamchi admin qo‘shish", "a:admadd")], [("⬅️ Sozlamalar", "a:settings")]]
    return "\n".join(lines), ikb(*rows)


@admin_router.callback_query(F.data == "a:admins")
async def admins_cb(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(call, *admins_view())


@admin_router.callback_query(F.data == "a:admadd")
async def admin_add_ask(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Staff.find)
    await show(call, "➕ Yangi yordamchi adminning ismi, @username yoki Telegram ID sini yozing.\n\n"
                     "⚠️ U avval botga kirib /start bosgan bo‘lishi kerak.",
               ikb([("⬅️ Orqaga", "a:admins")]))


@admin_router.message(Staff.find, F.text)
async def admin_add_find(message: Message, state: FSMContext) -> None:
    rows = database.find_users(message.text)
    await state.clear()
    if not rows:
        await message.answer("🤷 Topilmadi. U botga /start bosganiga ishonch hosil qiling.",
                             reply_markup=ikb([("🔍 Qayta qidirish", "a:admadd"), ("⬅️ Orqaga", "a:admins")]))
        return
    await message.answer("👇 Qaysi biri?",
                         reply_markup=ikb(*[[(user_label(u)[:60], f"a:admpick:{u['telegram_id']}")] for u in rows],
                                          [("⬅️ Orqaga", "a:admins")]))


@admin_router.callback_query(F.data.startswith("a:admpick:"))
async def admin_add(call: CallbackQuery, bot: Bot) -> None:
    uid = int(arg(call))
    if uid in access.owner_ids():
        await call.answer("U allaqachon bosh admin.", show_alert=True)
        return
    database.staff_add(uid, call.from_user.id)
    database.set_banned(uid, False)
    audit(call.from_user, "Yordamchi admin qo‘shildi", user_name(uid))
    try:
        await bot.send_message(uid, "🎉 Siz <b>yordamchi admin</b> etib tayinlandingiz!\n"
                                    "/start bosing — pastda «⚙️ Admin panel» tugmasi paydo bo‘ladi.")
    except Exception:
        pass
    text, kb = admins_view()
    await show(call, "✅ Qo‘shildi.\n\n" + text, kb)


@admin_router.callback_query(F.data.startswith("a:admdel:"))
async def admin_remove(call: CallbackQuery) -> None:
    uid = int(arg(call))
    database.staff_remove(uid)
    audit(call.from_user, "Yordamchi admin olib tashlandi", user_name(uid))
    text, kb = admins_view()
    await show(call, "❌ Olib tashlandi.\n\n" + text, kb)


# ---------------------------------------------------------------- majburiy obuna
def sub_view() -> tuple[str, object]:
    ch = subscribe.saved_channel()
    on = database.setting("sub_on", "0") == "1"
    lines = ["📌 <b>Majburiy obuna</b>\n"]
    if ch:
        lines += [f"Kanal: <b>{escape(ch['title'])}</b>",
                  f"Havola: {escape(ch.get('link') or '—')}",
                  f"Holati: {'🟢 yoqilgan — a’zo bo‘lmaganlar botdan foydalana olmaydi' if on else '⚪ o‘chirilgan'}"]
    else:
        lines.append("Kanal o‘rnatilmagan.")
    lines.append("\nℹ️ Yoqilsa, o‘quvchi kanalga a’zo bo‘lmaguncha botdan foydalana olmaydi. "
                 "Adminlar tekshirilmaydi.\n⚠️ Bot kanalda <b>admin</b> bo‘lishi shart.")
    rows = [[("✏️ Kanalni o‘rnatish" if not ch else "✏️ Kanalni almashtirish", "a:subset")]]
    if ch:
        rows.append([("⚪ O‘chirish" if on else "🟢 Yoqish", "a:subtoggle"), ("🗑 Olib tashlash", "a:subdel")])
    rows.append([("⬅️ Sozlamalar", "a:settings")])
    return "\n".join(lines), ikb(*rows)


@admin_router.callback_query(F.data == "a:sub")
async def sub_cb(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    text, kb = sub_view()
    if subscribe.saved_channel():
        reason = await subscribe.problem(bot)
        text += ("\n\n❗️ <b>Diqqat:</b> " + reason + ". Hozircha tekshiruv ishlamayapti."
                 if reason else "\n\n✅ Tekshiruv: bot kanalda admin — obuna to‘g‘ri tekshiriladi.")
    await show(call, text, kb)


@admin_router.callback_query(F.data == "a:subset")
async def sub_set_ask(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Sub.channel)
    await show(call, "📌 Kanalni ko‘rsating:\n"
                     "• ochiq kanal bo‘lsa — <code>@kanal_nomi</code> deb yozing\n"
                     "• yopiq kanal bo‘lsa — kanaldagi istalgan postni shu yerga <b>forward</b> qiling\n\n"
                     "⚠️ Avval botni kanalga <b>admin</b> qilib qo‘shing.",
               ikb([("⬅️ Orqaga", "a:sub")]))


@admin_router.message(Sub.channel)
async def sub_set(message: Message, state: FSMContext, bot: Bot) -> None:
    origin = getattr(message, "forward_origin", None)
    ident = getattr(getattr(origin, "chat", None), "id", None)
    if ident is None and message.text:
        m = re.search(r"(?:@|t\.me/)([A-Za-z0-9_]{4,})", message.text)
        ident = "@" + m.group(1) if m else None
    back = ikb([("⬅️ Orqaga", "a:sub")])
    if ident is None:
        await message.answer("⚠️ Kanalni aniqlay olmadim. <code>@kanal_nomi</code> yozing yoki kanaldan post forward qiling.",
                             reply_markup=back)
        return
    try:
        chat = await bot.get_chat(ident)
        me = await bot.me()
        member = await bot.get_chat_member(chat.id, me.id)
    except Exception as exc:
        await message.answer(f"⚠️ Kanalga ulana olmadim: {escape(str(exc))}\n\n"
                             "Bot kanalga admin qilib qo‘shilganini tekshiring.", reply_markup=back)
        return
    if member.status not in ("administrator", "creator"):
        await message.answer("⚠️ Bot bu kanalda <b>admin emas</b>. Kanal sozlamalaridan botni admin qilib, qayta urinib ko‘ring.",
                             reply_markup=back)
        return
    link = f"https://t.me/{chat.username}" if chat.username else chat.invite_link
    if not link:
        try:
            link = await bot.export_chat_invite_link(chat.id)
        except Exception:
            link = None
    database.set_setting("sub_channel", json.dumps({"id": chat.id, "title": chat.title or str(chat.id), "link": link}))
    database.set_setting("sub_on", "1")
    subscribe.reset_cache()
    await state.clear()
    audit(message.from_user, "Majburiy obuna o‘rnatildi", chat.title or str(chat.id))
    text, kb = sub_view()
    await message.answer("✅ Kanal o‘rnatildi va majburiy obuna yoqildi.\n\n" + text, reply_markup=kb)


@admin_router.callback_query(F.data == "a:subtoggle")
async def sub_toggle(call: CallbackQuery) -> None:
    on = database.setting("sub_on", "0") == "1"
    database.set_setting("sub_on", "0" if on else "1")
    subscribe.reset_cache()
    audit(call.from_user, "Majburiy obuna " + ("o‘chirildi" if on else "yoqildi"))
    await show(call, *sub_view())


@admin_router.callback_query(F.data == "a:subdel")
async def sub_delete(call: CallbackQuery) -> None:
    database.set_setting("sub_on", "0")
    database.set_setting("sub_channel", "")
    subscribe.reset_cache()
    audit(call.from_user, "Majburiy obuna olib tashlandi")
    await show(call, *sub_view())


# ---------------------------------------------------------------- 🗄 ombor kanal
def storage_view() -> tuple[str, object]:
    ch = storage.saved()
    on = storage.channel() is not None
    err = database.setting("storage_error")
    lines = ["🗄 <b>Ombor kanal</b>\n"]
    if ch:
        archived = len(database.storage_archived_tests(ch["id"]))
        total = len(content.all_tests())
        lines += [f"Kanal: <b>{escape(ch['title'])}</b>" + (f" (@{escape(ch['username'])})" if ch.get("username") else ""),
                  f"Holati: {'🟢 yoqilgan' if on else '⚪ o‘chirilgan'}",
                  f"📦 Kanalga joylangan testlar: <b>{archived}</b> / {total} · postlar: {database.storage_count(ch['id'])}"]
        if ch.get("username"):
            lines.append("\n⚠️ <b>Kanal ochiq (public)</b> — uni istalgan odam ko‘ra oladi. Shuning uchun "
                         "<b>javoblar kaliti kanalga yozilmaydi</b>. Kanalni yopiq (private) qilsangiz, "
                         "kalit ham joylanadi. Video tahlillar ham ochiq ko‘rinadi — buni hisobga oling.")
        else:
            lines.append("\n🔒 Kanal yopiq (private) — kalitlar ham joylanadi.")
    else:
        lines.append("Kanal ulanmagan.")
    lines.append("\nℹ️ Admin test qo‘shganda, PDF almashtirganda, video qo‘shganda yoki kalitni o‘zgartirganda "
                 "bot hammasini <b>avtomatik</b> shu kanalga ham joylaydi. Bot faylni o‘quvchiga yubora olmasa, "
                 "uni shu kanaldan qayta oladi.")
    if err:
        lines.append(f"\n❗ Oxirgi xato: {escape(err)}")
    rows = [[("✏️ Kanalni ulash" if not ch else "✏️ Kanalni almashtirish", "a:storset")]]
    if ch:
        rows.append([("⚪ O‘chirish" if on else "🟢 Yoqish", "a:stortoggle"),
                     ("📤 Joylanmaganlarni joylash", "a:storsync")])
    rows.append([("⬅️ Sozlamalar", "a:settings")])
    return "\n".join(lines), ikb(*rows)


@admin_router.callback_query(F.data == "a:stor")
async def storage_cb(call: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    ch = storage.saved()
    if ch:
        await storage._refresh(bot, ch)      # kanal ochiq/yopiqligini hozirning o'zida tekshiradi
    await show(call, *storage_view())


@admin_router.callback_query(F.data == "a:storset")
async def storage_set_ask(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Store.channel)
    await show(call, "🗄 Ombor kanalni ko‘rsating:\n"
                     "• ochiq kanal — <code>@kanal_nomi</code> yoki havolasini yozing\n"
                     "• yopiq kanal — kanaldagi istalgan postni shu yerga <b>forward</b> qiling\n\n"
                     "⚠️ Avval botni kanalga <b>admin</b> qilib qo‘shing (xabar joylash huquqi bilan).",
               ikb([("⬅️ Orqaga", "a:stor")]))


@admin_router.message(Store.channel)
async def storage_set(message: Message, state: FSMContext, bot: Bot) -> None:
    origin = getattr(message, "forward_origin", None)
    ident = getattr(getattr(origin, "chat", None), "id", None)
    if ident is None and message.text:
        m = re.search(r"(?:@|t\.me/)([A-Za-z0-9_]{4,})", message.text)
        ident = "@" + m.group(1) if m else None
    back = ikb([("⬅️ Orqaga", "a:stor")])
    if ident is None:
        await message.answer("⚠️ Kanalni aniqlay olmadim. <code>@kanal_nomi</code> yozing yoki kanaldan post forward qiling.",
                             reply_markup=back)
        return
    try:
        info = await storage.connect(bot, ident)
    except ValueError as exc:
        await message.answer(f"⚠️ {escape(str(exc))}", reply_markup=back)
        return
    await state.clear()
    audit(message.from_user, "Ombor kanal ulandi", info["title"])
    text, kb = storage_view()
    await message.answer("✅ Kanal ulandi. «📤 Joylanmaganlarni joylash» ni bossangiz, mavjud testlar ham joylanadi.\n\n"
                         + text, reply_markup=kb)


@admin_router.callback_query(F.data == "a:stortoggle")
async def storage_toggle(call: CallbackQuery) -> None:
    on = database.setting("storage_on", "0") == "1"
    database.set_setting("storage_on", "0" if on else "1")
    audit(call.from_user, "Ombor kanal " + ("o‘chirildi" if on else "yoqildi"))
    await show(call, *storage_view())


@admin_router.callback_query(F.data == "a:storsync")
async def storage_sync(call: CallbackQuery, bot: Bot) -> None:
    if not storage.channel():
        await call.answer("Avval kanalni yoqing.", show_alert=True)
        return
    await call.answer("📤 Joylanmoqda... Tugagach xabar beraman.")

    async def job():
        n = await storage.archive_missing(bot)
        text, kb = storage_view()
        await bot.send_message(call.from_user.id, f"✅ Kanalga {n} ta test joylandi.\n\n" + text, reply_markup=kb)
    storage.run(job())
    audit(call.from_user, "Testlar ombor kanalga joylandi")


# ---------------------------------------------------------------- 🏅 sertifikat sozlamalari
def cert_view() -> tuple[str, object]:
    on = certificate.enabled()
    t_name = certificate.teacher()
    subject = database.setting("cert_subject", certificate.DEFAULT_SUBJECT) or certificate.DEFAULT_SUBJECT
    has_browser = certificate.browser() is not None
    text = ("🏅 <b>Sertifikat</b>\n\n"
            f"Holati: {'🟢 beriladi' if on else '⚪ berilmaydi'}\n"
            f"👨‍🏫 O‘qituvchi (imzo ostida): <b>{escape(t_name) if t_name else '— (bo‘sh chiziq)'}</b>\n"
            f"📚 Yo‘nalish (fan): <b>{escape(subject)}</b>\n"
            f"📄 Berilgan sertifikatlar: <b>{database.cert_count()}</b> ta\n\n"
            "ℹ️ Sertifikat <b>55 ballik</b> testlarda daraja <b>C</b> va undan yuqori bo‘lsa beriladi "
            "(25–27 C · 28–32 C+ · 33–37 B · 38–45 B+ · 46–50 A · 51–55 A+). Ball yozilmaydi, faqat daraja.\n"
            "Javoblar «muddat tugagach» rejimidagi testda — sertifikat ham muddat tugagach yuboriladi.\n"
            "O‘quvchi sertifikat raqamini botga yuborsa, uning haqiqiyligini tekshiradi."
            + ("" if has_browser else "\n\n❗ Kompyuterda Chrome yoki Edge topilmadi — sertifikat yasab bo‘lmaydi."))
    kb = ikb([("⚪ O‘chirish" if on else "🟢 Yoqish", "a:certon")],
             [("✏️ O‘qituvchi ismi", "a:certt"), ("✏️ Yo‘nalish", "a:certs")],
             [("👁 Namuna olish", "a:certdemo")],
             [("⬅️ Sozlamalar", "a:settings")])
    return text, kb


@admin_router.callback_query(F.data == "a:cert")
async def cert_cb(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(call, *cert_view())


@admin_router.callback_query(F.data == "a:certon")
async def cert_toggle(call: CallbackQuery) -> None:
    on = certificate.enabled()
    database.set_setting("cert_on", "0" if on else "1")
    audit(call.from_user, "Sertifikat " + ("o‘chirildi" if on else "yoqildi"))
    await show(call, *cert_view())


@admin_router.callback_query(F.data.in_({"a:certt", "a:certs"}))
async def cert_edit_ask(call: CallbackQuery, state: FSMContext) -> None:
    teacher = call.data == "a:certt"
    await state.set_state(CertCfg.teacher if teacher else CertCfg.subject)
    rows = [[("🗑 Bo‘sh qoldirish", "a:certtclear")]] if teacher else []
    await show(call, ("✏️ Imzo ostiga yoziladigan o‘qituvchi ism-familiyasini yozing.\nMasalan: <code>Akmaljon Ochildinov</code>"
                      if teacher else "✏️ Sertifikatdagi yo‘nalish (fan) nomini yozing.\nMasalan: <code>Fizika</code>"),
               ikb(*rows, [("⬅️ Orqaga", "a:cert")]))


@admin_router.callback_query(F.data == "a:certtclear")
async def cert_teacher_clear(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    database.set_setting("cert_teacher", "")
    await show(call, *cert_view())


@admin_router.message(StateFilter(CertCfg.teacher, CertCfg.subject), F.text)
async def cert_edit_save(message: Message, state: FSMContext) -> None:
    teacher = await state.get_state() == CertCfg.teacher.state
    value = " ".join(message.text.split())[:40]
    if len(value) < 2:
        await message.answer("⚠️ Juda qisqa. Qayta yozing:", reply_markup=ikb([("⬅️ Orqaga", "a:cert")]))
        return
    database.set_setting("cert_teacher" if teacher else "cert_subject", value)
    await state.clear()
    audit(message.from_user, "Sertifikat sozlamasi", ("o‘qituvchi: " if teacher else "yo‘nalish: ") + value)
    text, kb = cert_view()
    await message.answer("✅ Saqlandi.\n\n" + text, reply_markup=kb)


@admin_router.callback_query(F.data == "a:certdemo")
async def cert_demo(call: CallbackQuery, bot: Bot) -> None:
    await call.answer("🏅 Namuna tayyorlanmoqda...")
    tests = [t for t in content.all_tests() if certificate.eligible(t, 55)] or content.all_tests()
    t = tests[0] if tests else None
    me = await bot.me()
    subject = (certificate.subject_of(t) if t else certificate.DEFAULT_SUBJECT)
    html = certificate.build_html(
        name=user_name(call.from_user.id).split(" (@")[0], title=t.title if t else "Namuna test",
        header=f"Milliy sertifikat · {subject}", subject=subject, grade="A+",
        date=timeutil.now().strftime("%d.%m.%Y"), code="AY-NAMUNA", bot_username=me.username,
        teacher_name=certificate.teacher())
    try:
        png = await certificate.render(html)
    except Exception as exc:
        await call.message.answer(f"⚠️ Namuna yasab bo‘lmadi: {escape(str(exc))}")
        return
    await call.message.answer_photo(BufferedInputFile(png, filename="Sertifikat namuna.png"),
                                    caption="👁 <b>Namuna</b> — o‘quvchilar sertifikati shunday ko‘rinadi.")


# ---------------------------------------------------------------- noto'g'ri turdagi xabarlar
@admin_router.message(NewTest.pdf)
async def new_pdf_wrong(message: Message) -> None:
    await message.answer("📄 PDF fayl yuboring.", reply_markup=ikb(CANCEL))


@admin_router.message(StateFilter(NewTest.title, NewTest.key, NewTest.options, NewTest.points,
                                  NewTest.note, EditTest.value, EditTest.time, Users.search, Broadcast.url,
                                  Staff.find, CertCfg.teacher, CertCfg.subject))
async def need_text(message: Message) -> None:
    await message.answer("✏️ Iltimos, matn ko‘rinishida yozing (yoki «❌ Bekor qilish»).", reply_markup=ikb(CANCEL))


@admin_router.callback_query(F.data.startswith("a:"))
async def stale_button(call: CallbackQuery, state: FSMContext) -> None:
    """Bot qayta ishga tushgan yoki jarayon tugagan bo'lsa — eski tugmalar shu yerga keladi."""
    await state.clear()
    await call.answer("Bu tugma eskirgan — admin panel qaytadan ochildi.", show_alert=True)
    text, kb = home_view(call.from_user)
    await call.message.answer(text, reply_markup=kb)
