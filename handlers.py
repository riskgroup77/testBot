"""O'quvchi tomoni: /start, menyu, testlar, javoblar, video tahlillar."""
import logging
from html import escape
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BotCommand, BotCommandScopeChat, BufferedInputFile, CallbackQuery, Message,
                           ReplyKeyboardRemove)

import asyncio
import re

import access
import certificate
import content
import database
import export
import media
import storage
import subscribe
import timeutil
from config import BOT_NAME
from content import Test
from grading import (STANDARD_MAX, fmt_num, fmt_nums, grade, grade_letter, open_label, open_sort,
                     options_help, parse_answers, same_answer)
from ui import BTN_TEST, BTN_VIDEO, MENU_TEXTS, ikb, main_menu

log = logging.getLogger("bot")

main_router = Router(name="main")       # /start va menyu tugmalari (birinchi)
flow_router = Router(name="flow")       # test va video jarayoni
fallback_router = Router(name="fallback")  # tushunilmagan xabarlar (oxirgi)


class AnswerState(StatesGroup):
    waiting = State()


class CertName(StatesGroup):
    waiting = State()     # data: code


CERT_RE = r"(?i)^\s*AY-\d{4}-\d{6}\s*$"


def cert_kb(code: str):
    return ikb([("🖨 PDF (chop etish uchun)", f"cert:pdf:{code}")],
               [("✏️ Ismni tuzatish", f"cert:name:{code}")])


async def send_certificate(bot: Bot, user_id: int, fallback_name: str, test: Test,
                           result_id: int | None, grade: str, cert=None, note: str = "") -> None:
    """Sertifikat yaratadi (yoki mavjudini qayta chizadi) va o'quvchiga rasm qilib yuboradi."""
    try:
        if cert is None:
            cert = database.cert_for_result(result_id) if result_id else None
        if cert is None:
            name = certificate.display_name(database.user_row(user_id), fallback_name)
            cert = database.cert_create(user_id, test.id, result_id, name, grade)
        me = await bot.me()
        await bot.send_chat_action(user_id, ChatAction.UPLOAD_PHOTO)
        png = await certificate.render(certificate.html_for(cert, test, me.username))
        await bot.send_photo(
            user_id, BufferedInputFile(png, filename=f"Sertifikat {cert['code']}.png"),
            caption=(note or f"🏅 <b>Tabriklaymiz!</b> Sizga <b>{cert['grade']}</b> darajali sertifikat berildi.")
            + f"\n📄 Sertifikat № <code>{cert['code']}</code>\n\n"
              "Ismingiz noto‘g‘ri yozilgan bo‘lsa — «✏️ Ismni tuzatish» ni bosing.",
            reply_markup=cert_kb(cert["code"]))
        log.info("Sertifikat yuborildi: %s → %s (%s)", cert["code"], user_id, cert["grade"])
    except Exception:
        log.exception("Sertifikat yuborilmadi (%s, %s)", user_id, test.id)
        try:
            await bot.send_message(user_id, "⚠️ Sertifikatni tayyorlashda xatolik bo‘ldi. "
                                             "O‘qituvchingizga xabar bering — u qayta yuborib beradi.")
        except Exception:
            pass


BANNED_TEXT = "⛔ Siz botdan foydalanish huquqidan mahrum qilingansiz. Savollar bo‘lsa, o‘qituvchingizga murojaat qiling."
SHOW_LABELS = {"now": "darhol", "score": "ko‘rsatilmaydi", "after": "muddat tugagach"}


def answer_lines(test: Test, answers: dict[int, str], bad: dict[int, str] | None = None,
                 show_correct: bool = True, open_answers: dict[str, str] | None = None) -> list[str]:
    """Har bir savol bo'yicha: '2. A ❌ → D', yozma: '36 a) 17 ✅'."""
    bad = bad or {}
    lines = []
    for q in test.questions:
        given, right = answers.get(q), test.key[q]
        hint = f" → {right}" if show_correct else ""
        if given == right:
            lines.append(f"{q}. {given} ✅")
        elif given:
            lines.append(f"{q}. {given} ❌{hint}")
        elif q in bad:
            lines.append(f"{q}. {bad[q]} ❌{hint} <i>(bunday variant yo‘q)</i>")
        else:
            lines.append(f"{q}. — ❌{hint} <i>(javob yo‘q)</i>")
    if test.open_key:
        open_answers = open_answers or {}
        lines.append("\n<b>✍️ Yozma qism:</b>")
        for qid in sorted(test.open_key, key=open_sort):
            given, right = open_answers.get(qid), test.open_key[qid]
            hint = f" → {escape(right)}" if show_correct else ""
            label = open_label(qid)
            if same_answer(given, right):
                lines.append(f"{label} {escape(given)} ✅")
            elif given:
                lines.append(f"{label} {escape(given)} ❌{hint}")
            else:
                lines.append(f"{label} — ❌{hint} <i>(javob yo‘q)</i>")
    return lines


def attempts_left(test: Test, user_id: int) -> int | None:
    """None = cheksiz."""
    if not test.max_attempts:
        return None
    return max(0, test.max_attempts - database.attempts_count(user_id, test.id))


def closed_text(test: Test) -> str | None:
    """Test hozir javob qabul qilmasa — sababi."""
    st = test.status()
    if st == "upcoming":
        return f"⏳ «{test.title}» testi {timeutil.fmt(test.starts_at)} da ochiladi."
    if st == "closed":
        return f"⏹ «{test.title}» testining muddati tugagan ({timeutil.fmt(test.ends_at)}). Javoblar qabul qilinmaydi."
    return None


def menu_for(user) -> Any:
    return main_menu(access.is_admin(user))


def pdf_kb(test: Test):
    rows = [[("✍️ Javoblarni yuborish", f"ans:{test.id}")]]
    if test.videos:
        rows.append([("🎬 Video tahlilni ko‘rish", f"vid:{test.id}")])
    return ikb(*rows)


def open_test(test_id: str, user) -> Test | None:
    """Test faol bo'lsa (yoki admin bo'lsa) qaytaradi."""
    t = content.get(test_id)
    if t and (t.active or access.is_admin(user)):
        return t
    return None


def tests_for(user) -> list[Test]:
    """O'quvchiga ko'rinadigan testlar (faqat faollari)."""
    return content.all_tests(active_only=True)


def answer_sample(test: Test) -> str:
    """Namuna: 1A 2B 3C 4D 5A ... 35C (+ yozma qism). Haqiqiy kalit emas!"""
    nums = test.questions
    head = " ".join(f"{q}{'ABCD'[i % 4]}" for i, q in enumerate(nums[:6]))
    text = f"{head} ... {nums[-1]}{'ABCD'[(len(nums) - 1) % 4]}"
    if test.open_key:
        on = sorted(test.open_numbers)
        text += (f"\n{on[0]}. a) 17 b) 22\n{on[1]}. a) 5 b) 3,5\n..." if len(on) > 1
                 else f"\n{on[0]}. a) 17 b) 22")
        text += f"\n{on[-1]}. a) … b) …" if len(on) > 2 else ""
    return text


def answer_help(test: Test) -> str:
    last = test.last_question
    text = ("Javoblarni <b>bitta xabarda</b>, mana shunday yozing:\n"
            f"<code>{answer_sample(test)}</code>\n\n")
    if test.open_key:
        on = sorted(test.open_numbers)
        text += (f"📝 <b>{on[0]}–{on[-1]}</b>-savollarning har birida <b>a)</b> va <b>b)</b> qismi bor — "
                 "ikkalasining javobini yozing (son yoki qisqa javob).\n\n")
    else:
        text += "Har birini yangi qatordan yozsangiz ham bo‘ladi:\n<code>1. a\n2. b\n3. c</code>\n\n"
    text += (f"⚠️ 1 dan {last} gacha <b>hamma savolga</b> javob yozing — "
             "javob yozilmagan savol <b>xato</b> hisoblanadi.")
    return text


# ---------------------------------------------------------------- avtomatik ro'yxat
class RegisterMiddleware(BaseMiddleware):
    """Har bir xabar/tugmada foydalanuvchini Telegram ma'lumotlari bilan saqlaydi."""

    async def __call__(self, handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
                       event: Any, data: dict[str, Any]) -> Any:
        user = getattr(event, "from_user", None)
        if user and not user.is_bot:
            try:
                if database.register_user(user):
                    log.info("Yangi foydalanuvchi: %s (@%s, id=%s)",
                             user.full_name, user.username or "-", user.id)
                elif database.is_banned(user.id) and not access.is_admin(user):
                    if isinstance(event, CallbackQuery):
                        await event.answer(BANNED_TEXT, show_alert=True)
                    elif isinstance(event, Message) and event.chat.type == "private":
                        await event.answer(BANNED_TEXT, reply_markup=ReplyKeyboardRemove())
                    return None
            except Exception:
                log.exception("Foydalanuvchini saqlashda xato")
            # majburiy obuna (adminlar va «A'zo bo'ldim» tugmasi tekshirilmaydi)
            if (subscribe.channel() and not access.is_admin(user)
                    and not (isinstance(event, CallbackQuery) and event.data == "subcheck")):
                if not await subscribe.is_member(data["bot"], user.id):
                    text, kb = subscribe.prompt()
                    if isinstance(event, CallbackQuery):
                        await event.answer("📢 Avval kanalga a’zo bo‘ling!", show_alert=True)
                        await event.message.answer(text, reply_markup=kb)
                    elif isinstance(event, Message) and event.chat.type == "private":
                        await event.answer(text, reply_markup=kb)
                    return None
        return await handler(event, data)


def display_name(message: Message) -> str:
    user = message.from_user
    if user.username:
        return "@" + escape(user.username)
    return escape(user.full_name or "do‘stim")


# ---------------------------------------------------------------- /start va menyu
@main_router.callback_query(F.data == "subcheck")
async def sub_check(call: CallbackQuery, state: FSMContext) -> None:
    if not await subscribe.is_member(call.bot, call.from_user.id, fresh=True):
        await call.answer("❌ Hali a’zo bo‘lmagansiz. Kanalga o‘tib, «Qo‘shilish» ni bosing.", show_alert=True)
        return
    await call.answer("✅ Rahmat! Endi botdan foydalanishingiz mumkin.")
    try:
        await call.message.delete()
    except Exception:
        pass
    await state.clear()
    await call.message.answer(
        f"🎉 <b>Xush kelibsiz!</b>\n\n📋 Menyudan istagan xizmatingizni tanlang 👇",
        reply_markup=menu_for(call.from_user))


@main_router.callback_query(F.data == "home")
async def home_cb(call: CallbackQuery, state: FSMContext) -> None:
    """🏠 Bosh menyu tugmasi — /start dagi kabi bosh menyuni qaytaradi."""
    await state.clear()
    await call.answer()
    await call.message.answer("🏠 <b>Bosh menyu</b>\n\n📋 Menyudan istagan xizmatingizni tanlang 👇",
                              reply_markup=menu_for(call.from_user))


@main_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    admin = access.is_admin(message.from_user)
    if admin:
        await set_admin_commands(bot, message.chat.id)
    await message.answer(
        f"👋 <b>Assalomu alaykum, {display_name(message)}.</b>\n\n"
        f"Siz <b>{BOT_NAME}</b> botga xush kelibsiz! 🤖✨\n\n"
        "📋 Menyudan istagan xizmatingizni tanlang 👇"
        + ("\n\n⚙️ <i>Siz adminsiz — «Admin panel» tugmasi faqat sizga ko‘rinadi.</i>" if admin else ""),
        reply_markup=main_menu(admin),
    )


async def set_admin_commands(bot: Bot, chat_id: int) -> None:
    """Admin chatida menyuga /admin buyrug'ini qo'shadi (o'quvchilar menyusida u ko'rinmaydi)."""
    try:
        await bot.set_my_commands(
            [BotCommand(command="start", description="🏠 Bosh menyu"),
             BotCommand(command="admin", description="⚙️ Admin panel"),
             BotCommand(command="bekor", description="❌ Bekor qilish")],
            scope=BotCommandScopeChat(chat_id=chat_id))
        chats = _admin_cmd_chats()
        if chat_id not in chats:
            database.set_setting("admin_cmd_chats", ",".join(map(str, chats | {chat_id})))
    except Exception as exc:
        log.warning("Admin buyruqlarini o'rnatib bo'lmadi: %s", exc)


def _admin_cmd_chats() -> set[int]:
    raw = database.setting("admin_cmd_chats", "")
    return {int(x) for x in raw.split(",") if x.strip().lstrip("-").isdigit()}


async def cleanup_admin_access(bot: Bot) -> None:
    """Bot ishga tushganda: endi admin bo'lmaganlarning menyusidan /admin buyrug'ini olib tashlaydi.
    .env da bosh admin yozilgan bo'lsa, sinov rejimidagi (/admin orqali bo'lgan) adminlik o'chiriladi."""
    chats = _admin_cmd_chats()
    demo = database.setting("demo_owner", "")
    if demo.isdigit():
        chats.add(int(demo))
    if access.configured() and demo:
        access.reset_demo()
        log.info("Sinov rejimidagi admin (id=%s) o'chirildi — endi admin .env dagi ADMIN_USERNAME", demo)
    keep = access.admin_chat_ids()
    for cid in chats - keep:
        try:
            await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=cid))
            log.info("Admin buyruqlari olib tashlandi: id=%s", cid)
        except Exception as exc:
            log.warning("Buyruqlarni olib tashlab bo'lmadi (id=%s): %s", cid, exc)
    database.set_setting("admin_cmd_chats", ",".join(map(str, chats & keep)))


@main_router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext, bot: Bot) -> None:
    """/admin — admin panelni ochadi.
    Sinov rejimi: .env da admin hali yozilmagan bo'lsa, birinchi /admin yozgan odam admin bo'ladi."""
    user = message.from_user
    claimed = False
    if not access.is_admin(user):
        claimed = access.claim_demo(user)
        if not claimed:
            await message.answer("🤔 Kechirasiz, buni tushunmadim.\n\n📋 Iltimos, quyidagi menyudan xizmatni tanlang 👇",
                                 reply_markup=menu_for(user))
            return
    await state.clear()
    await set_admin_commands(bot, message.chat.id)
    if claimed:
        await message.answer(
            "✅ <b>Siz admin bo‘ldingiz!</b> <i>(sinov rejimi)</i>\n\n"
            "• Pastda <b>«⚙️ Admin panel»</b> tugmasi paydo bo‘ldi — istalgan payt shu yerdan kirasiz.\n"
            "• /admin buyrug‘i ham doim ishlaydi.\n"
            "• 🔒 Endi boshqa hech kim /admin orqali admin bo‘la olmaydi.",
            reply_markup=main_menu(True))
    from admin import home_view
    text, kb = home_view(user)
    await message.answer(text, reply_markup=kb)


@main_router.message(Command("bekor"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Bekor qilindi.\n\n📋 Menyudan xizmatni tanlang 👇",
                         reply_markup=menu_for(message.from_user))


def visible_tests(user) -> list[Test]:
    return content.all_tests() if access.is_admin(user) else tests_for(user)


def count_in(key: str, tests: list[Test]) -> int:
    c = content.CATEGORIES[key]
    keys = c.get("children") or [key]
    return sum(1 for t in tests if t.category in keys)


def category_view(key: str, user) -> tuple[str, Any]:
    """Bo'lim menyusi: ichida bo'limlar bo'lsa — ular, bo'lmasa — testlar ro'yxati."""
    tests = visible_tests(user)
    if key == "root":
        rows = [[(f"{content.CATEGORIES[k]['title']} ({count_in(k, tests)})", f"cat:{k}")] for k in content.ROOT]
        return "📚 <b>Test yechish</b>\n\nQaysi turdagi testni yechmoqchisiz? 👇", ikb(*rows)
    c = content.CATEGORIES[key]
    back = c.get("parent") or "root"
    if c.get("children"):
        rows = [[(f"{content.CATEGORIES[k]['title']} ({count_in(k, tests)})", f"cat:{k}")] for k in c["children"]]
        rows.append([("⬅️ Orqaga", f"cat:{back}")])
        return f"<b>{c['title']}</b>\n\nBo‘limni tanlang 👇", ikb(*rows)
    in_cat = [t for t in tests if t.category == key]
    rows = [[(f"📘 {t.title}" + ("" if t.active else " · ⚪ yashirin"), f"test:{t.id}")] for t in in_cat]
    rows.append([("⬅️ Orqaga", f"cat:{back}")])
    text = (f"<b>{content.category_path(key)}</b>\n\n"
            + ("Yechmoqchi bo‘lgan testingizni tanlang 👇" if in_cat
               else "📭 Hozircha bu bo‘limda test yo‘q. Tez orada qo‘shiladi! ⏳"))
    return text, ikb(*rows)


@main_router.message(F.text == BTN_TEST)
async def open_tests(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, kb = category_view("root", message.from_user)
    await message.answer(text, reply_markup=kb)


@main_router.callback_query(F.data.startswith("cat:"))
async def open_category(call: CallbackQuery, state: FSMContext) -> None:
    key = call.data.split(":", 1)[1]
    if key != "root" and key not in content.CATEGORIES:
        await call.answer("Bo‘lim topilmadi", show_alert=True)
        return
    await state.clear()
    await call.answer()
    text, kb = category_view(key, call.from_user)
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        await call.message.answer(text, reply_markup=kb)


@main_router.message(F.text == BTN_VIDEO)
async def open_videos(message: Message, state: FSMContext) -> None:
    await state.clear()
    rows = [[(f"🎬 {t.title} — tahlil", f"vid:{t.id}")] for t in tests_for(message.from_user) if t.videos]
    if not rows:
        await message.answer("🎬 Hozircha video tahlillar yo‘q. Tez orada qo‘shiladi! ⏳")
        return
    await message.answer("🎬 <b>Video analizlar</b>\n\nTomosha qilmoqchi bo‘lgan tahlilni tanlang 👇",
                         reply_markup=ikb(*rows))


# ---------------------------------------------------------------- test: PDF yuborish
@flow_router.callback_query(F.data.startswith("test:"))
async def send_test(call: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    test = open_test(call.data.split(":", 1)[1], call.from_user)
    if not test or not test.has_pdf:
        await call.answer("⏳ Bu test hozircha yopiq.", show_alert=True)
        return
    status = test.status()
    if status == "upcoming" and not access.is_admin(call.from_user):
        await call.answer(closed_text(test), show_alert=True)
        return
    await call.answer("📄 Test yuborilmoqda...")
    await state.clear()
    await bot.send_chat_action(call.message.chat.id, ChatAction.UPLOAD_DOCUMENT)
    left = attempts_left(test, call.from_user.id)
    rules = []
    if test.ends_at:
        rules.append(f"⏰ Muddat: <b>{timeutil.fmt(test.ends_at)}</b> gacha")
    if test.max_attempts:
        rules.append(f"🔢 Urinishlar: {test.max_attempts} ta (sizda {left} ta qoldi)")
    if test.show_mode == "after":
        rules.append("👀 To‘g‘ri javoblar muddat tugagach yuboriladi")
    if status == "closed":
        tail = f"\n⏹ <b>Test muddati tugagan</b> — javoblar endi qabul qilinmaydi."
        kb = ikb([("🎬 Video tahlilni ko‘rish", f"vid:{test.id}")]) if test.videos else None
    elif left == 0:
        tail = "\n🔢 Siz barcha urinishlardan foydalangansiz — javob qabul qilinmaydi."
        kb = ikb([("🎬 Video tahlilni ko‘rish", f"vid:{test.id}")]) if test.videos else None
    else:
        tail = "\n✍️ Tayyor bo‘lgach, pastdagi <b>«Javoblarni yuborish»</b> tugmasini bosing 👇"
        kb = pdf_kb(test)
    caption = (
        f"📘 <b>{escape(test.title)}</b>\n\n"
        "📄 Testni yuklab oling va daftaringizda yeching.\n"
        + (f"✅ Bot tekshiradi: <b>1–{test.last_question}</b>-savollar — {len(test.key)} ta variantli + "
           f"{len(test.open_key)} ta yozma javob (jami {fmt_num(test.max_score)} ball)\n"
           if test.open_key else
           f"✅ Bot tekshiradi: <b>1–{max(test.key)}</b>-savollar (jami {fmt_num(test.max_score)} ball)\n")
        + (f"📝 {escape(test.open_questions)}\n" if test.open_questions else "")
        + ("\n" + "\n".join(rules) + "\n" if rules else "")
        + tail
    )
    try:
        await media.send_media(bot, call.message.chat.id, "document", test.pdf_path, test.pdf_file_id,
                               filename=f"{export.safe_filename(test.title)}.pdf",
                               caption=caption, reply_markup=kb)
    except Exception:
        log.exception("PDF yuborishda xato (%s) — ombor kanaldan olinadi", test.id)
        if not await storage.recover(bot, call.message.chat.id, test.id, "pdf", caption=caption, reply_markup=kb):
            await call.message.answer("⚠️ Testni yuborishda xatolik bo‘ldi. Birozdan keyin qayta urinib ko‘ring.")


# ---------------------------------------------------------------- test: javoblarni qabul qilish
@flow_router.callback_query(F.data.startswith("ans:"))
async def ask_answers(call: CallbackQuery, state: FSMContext) -> None:
    test = open_test(call.data.split(":", 1)[1], call.from_user)
    if not test:
        await call.answer("⏳ Bu test hozircha yopiq.", show_alert=True)
        return
    reason = closed_text(test)
    if not reason and attempts_left(test, call.from_user.id) == 0:
        reason = f"🔢 Siz «{test.title}» testini {test.max_attempts} marta yechdingiz — boshqa urinish yo‘q."
    if reason:
        await call.answer(reason, show_alert=True)
        return
    await call.answer()
    await state.set_state(AnswerState.waiting)
    await state.update_data(test_id=test.id)
    left = attempts_left(test, call.from_user.id)
    await call.message.answer(
        f"✍️ <b>{escape(test.title)}</b> — javoblaringizni yuboring\n\n" + answer_help(test)
        + (f"\n\n🔢 Sizda <b>{left}</b> ta urinish qoldi — diqqat bilan tekshirib yuboring." if left else "")
        + (f"\n⏰ Muddat: {timeutil.fmt(test.ends_at)} gacha." if test.ends_at else ""),
        reply_markup=ikb([("❌ Bekor qilish", "cancel")]),
    )


@flow_router.callback_query(F.data == "cancel")
async def cancel_cb(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.answer("Bekor qilindi")
    await call.message.answer("❌ Bekor qilindi.\n\n📋 Menyudan xizmatni tanlang 👇",
                              reply_markup=menu_for(call.from_user))


@flow_router.message(AnswerState.waiting, F.text, ~F.text.in_(MENU_TEXTS), ~F.text.startswith("/"))
async def receive_answers(message: Message, state: FSMContext, bot: Bot) -> None:
    test = open_test((await state.get_data()).get("test_id"), message.from_user)
    if not test:
        await state.clear()
        await message.answer("⚠️ Bu test hozircha yopiq. Boshqasini tanlang 👇",
                             reply_markup=menu_for(message.from_user))
        return

    reason = closed_text(test)
    if not reason and attempts_left(test, message.from_user.id) == 0:
        reason = "🔢 Siz barcha urinishlardan foydalangansiz."
    if reason:
        await state.clear()
        await message.answer(reason, reply_markup=menu_for(message.from_user))
        return

    parsed = parse_answers(message.text, test)
    if not parsed.any:
        await message.answer(
            "🤔 Bu xabardan javoblarni topa olmadim.\n\n" + answer_help(test),
            reply_markup=ikb([("❌ Bekor qilish", "cancel")]),
        )
        return

    await state.clear()
    r = grade(parsed.answers, test, parsed.open)
    # noto'g'ri variant yozilganlar "javobsiz" emas (ular ham xato, lekin alohida ko'rsatiladi)
    r.empty = [q for q in r.empty if q not in parsed.bad]
    result_id = database.save_result(message.from_user.id, test.id, parsed.stored(), r)
    attempt = database.attempts_count(message.from_user.id, test.id)
    letter = grade_letter(r.score) if round(test.max_score) == STANDARD_MAX else None
    cert_grade = certificate.eligible(test, r.score)

    if r.percent >= 86:
        verdict = "🌟 A’lo natija! Barakalla!"
    elif r.percent >= 71:
        verdict = "👍 Yaxshi natija! Davom eting!"
    elif r.percent >= 56:
        verdict = "🙂 Qoniqarli. Yana ozgina harakat qiling!"
    else:
        verdict = "📚 Mavzularni takrorlash kerak — video tahlilni albatta ko‘ring!"

    visible = test.answers_visible()
    if visible:
        detail = "<b>📝 Javoblaringiz:</b>\n" + "\n".join(
            answer_lines(test, parsed.answers, parsed.bad, open_answers=parsed.open))
    elif test.show_mode == "after":
        detail = ("🔒 Qaysi savolda xato qilganingiz va to‘g‘ri javoblar "
                  + (f"<b>{timeutil.fmt(test.ends_at)}</b> da" if test.ends_at else "test muddati tugagach")
                  + " sizga avtomatik yuboriladi.")
    else:
        detail = "🔒 Bu testda to‘g‘ri javoblar ko‘rsatilmaydi."

    warn = []
    if r.empty or r.open_empty:
        parts = ([fmt_nums(r.empty)] if r.empty else []) + [open_label(q) for q in r.open_empty]
        warn.append(f"⚠️ Javob yozilmagan (xato hisoblandi): {', '.join(parts)}")
    if parsed.invalid:
        warn.append("⚠️ Bunday variant yo‘q (xato hisoblandi): " + ", ".join(parsed.invalid[:10])
                    + f"\n<i>Variantlar: {options_help(test)}</i>")
    if parsed.unknown:
        warn.append(f"⚠️ Testda yo‘q savol raqamlari: {', '.join(parsed.unknown[:10])}")
    if parsed.duplicates:
        warn.append(f"⚠️ Ikki marta yozilgan (oxirgisi olindi): {fmt_nums(parsed.duplicates)}")
    if parsed.positional:
        warn.append("ℹ️ Raqamsiz yuborildi — harflar 1-savoldan boshlab tartib bilan olindi.")

    wrong_line = f"❌ Xato: <b>{r.n_wrong}</b>"
    if r.n_empty:
        wrong_line += f" <i>(shundan {r.n_empty} tasi javobsiz)</i>"
    split_line = (f"   • variantli: {len(r.correct)} / {len(test.key)} · yozma: {len(r.open_correct)} / {len(test.open_key)}\n"
                  if test.open_key else "")
    text = (
        f"📊 <b>Natija · {escape(test.title)}</b>\n"
        f"<i>{attempt}-urinish</i>\n\n"
        f"🏆 Ball: <b>{fmt_num(r.score)} / {fmt_num(r.max_score)}</b> ({r.percent}%)\n"
        + (f"🎖 Daraja: <b>{letter}</b>\n" if letter else "")
        + f"✅ To‘g‘ri: <b>{r.n_correct}</b> / {test.total_answers}\n"
        f"{split_line}"
        f"{wrong_line}\n\n"
        f"{verdict}\n\n"
        + detail
        + ("\n\n" + "\n".join(warn) if warn else "")
    )
    left = attempts_left(test, message.from_user.id)
    if left is not None:
        text += f"\n\n🔢 Qolgan urinishlar: <b>{left}</b>"
    cert_later = bool(cert_grade) and test.show_mode == "after" and not test.answers_visible()
    if cert_grade:
        text += ("\n\n🏅 Sertifikatingiz test muddati tugagach tahlil bilan birga yuboriladi." if cert_later
                 else "\n\n🏅 Sertifikatingiz tayyorlanmoqda — bir necha soniyada keladi.")
    rows = [[("🔁 Qayta yechish", f"ans:{test.id}")]] if left != 0 else []
    if test.videos and not test.video_block_reason(solved=True):
        rows.insert(0, [("🎬 Video tahlilni ko‘rish", f"vid:{test.id}")])
    await message.answer(text, reply_markup=ikb(*rows))
    log.info("Natija: %s — %s: %s/%s", message.from_user.id, test.id, r.score, r.max_score)
    if cert_grade and not cert_later:
        asyncio.create_task(send_certificate(bot, message.from_user.id, message.from_user.full_name,
                                             test, result_id, cert_grade))

    if database.setting("notify", "1") != "1":
        return
    user = message.from_user
    for admin in access.admin_chat_ids() - {user.id}:
        try:
            await bot.send_message(
                admin,
                f"🆕 <b>Yangi natija</b>\n👤 {escape(user.full_name)}"
                + (f" (@{escape(user.username)})" if user.username else "")
                + f"\n📘 {escape(test.title)} · {attempt}-urinish"
                f"\n🏆 {fmt_num(r.score)} / {fmt_num(r.max_score)} ({r.percent}%)"
                + (f" · 🎖 {letter}" if letter else "")
                + f" · ✅ {r.n_correct} ❌ {r.n_wrong}"
                + (f" (javobsiz {r.n_empty})" if r.n_empty else ""),
                reply_markup=ikb([("🏆 Reyting", f"a:rank:{test.id}"), ("👤 O‘quvchi", f"a:u:{user.id}")]),
            )
        except Exception as exc:
            log.warning("Adminga yuborilmadi (%s): %s", admin, exc)


# ---------------------------------------------------------------- 🏅 sertifikat: PDF, ism, tekshirish
def _own_cert(call: CallbackQuery):
    cert = database.cert_get(call.data.rsplit(":", 1)[1])
    if cert and (cert["telegram_id"] == call.from_user.id or access.is_admin(call.from_user)):
        return cert
    return None


@flow_router.callback_query(F.data.startswith("cert:pdf:"))
async def cert_pdf(call: CallbackQuery, bot: Bot) -> None:
    cert = _own_cert(call)
    if not cert:
        await call.answer("Sertifikat topilmadi", show_alert=True)
        return
    await call.answer("🖨 PDF tayyorlanmoqda...")
    try:
        await bot.send_chat_action(call.message.chat.id, ChatAction.UPLOAD_DOCUMENT)
        me = await bot.me()
        pdf = await certificate.render(certificate.html_for(cert, content.get(cert["test_id"]), me.username), "pdf")
        await call.message.answer_document(
            BufferedInputFile(pdf, filename=f"Sertifikat {cert['code']}.pdf"),
            caption=f"🖨 Sertifikat № <code>{cert['code']}</code> — chop etish uchun (A4, yotiq).")
    except Exception:
        log.exception("Sertifikat PDF yaratilmadi")
        await call.message.answer("⚠️ PDF tayyorlashda xatolik bo‘ldi. Birozdan keyin qayta urinib ko‘ring.")


@flow_router.callback_query(F.data.startswith("cert:name:"))
async def cert_name_ask(call: CallbackQuery, state: FSMContext) -> None:
    cert = _own_cert(call)
    if not cert:
        await call.answer("Sertifikat topilmadi", show_alert=True)
        return
    await call.answer()
    await state.set_state(CertName.waiting)
    await state.update_data(code=cert["code"])
    await call.message.answer(
        "✏️ Sertifikatga yoziladigan <b>familiya va ismingizni</b> to‘liq yozing.\n"
        "Masalan: <code>Aliyev Valijon</code>\n\n"
        "💡 Bu ism keyingi sertifikatlaringizga ham yoziladi.",
        reply_markup=ikb([("❌ Bekor qilish", "cancel")]))


@flow_router.message(CertName.waiting, F.text, ~F.text.in_(MENU_TEXTS), ~F.text.startswith("/"))
async def cert_name_save(message: Message, state: FSMContext, bot: Bot) -> None:
    name = " ".join(message.text.split())
    if not (3 <= len(name) <= 60) or not re.search(r"[A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ‘’ʻʼ']{2}", name) or re.search(r"[<>@#\d]", name):
        await message.answer("⚠️ Ism 3–60 harfdan iborat bo‘lsin, raqam va belgilarsiz. Qayta yozing:",
                             reply_markup=ikb([("❌ Bekor qilish", "cancel")]))
        return
    code = (await state.get_data()).get("code")
    await state.clear()
    cert = database.cert_get(code or "")
    if not cert or cert["telegram_id"] != message.from_user.id and not access.is_admin(message.from_user):
        await message.answer("⚠️ Sertifikat topilmadi.")
        return
    database.set_cert_name(cert["telegram_id"], name)
    database.cert_rename(cert["code"], name)
    await message.answer("✅ Ism saqlandi. Yangilangan sertifikat tayyorlanmoqda...")
    test = content.get(cert["test_id"])
    await send_certificate(bot, cert["telegram_id"], name, test, cert["result_id"], cert["grade"],
                           cert=database.cert_get(cert["code"]),
                           note=f"✅ <b>Sertifikat yangilandi</b> — {escape(name)}")


@main_router.message(F.text.regexp(CERT_RE))
async def cert_check(message: Message) -> None:
    """Sertifikat raqamini yuborsa — haqiqiyligini tekshiradi."""
    cert = database.cert_get(message.text.strip())
    if not cert:
        await message.answer("❌ Bunday raqamli sertifikat topilmadi. Raqamni tekshirib qayta yuboring.")
        return
    t = content.get(cert["test_id"])
    await message.answer(
        "✅ <b>Sertifikat haqiqiy</b>\n\n"
        f"📄 № <code>{cert['code']}</code>\n"
        f"👤 {escape(cert['name'])}\n"
        f"📘 {escape(t.title) if t else cert['test_id']}\n"
        f"🎖 Daraja: <b>{cert['grade']}</b>\n"
        f"📅 Berilgan sana: {timeutil.parse_iso(cert['issued_at'][:16]).strftime('%d.%m.%Y')}")


# ---------------------------------------------------------------- video tahlil
_sending: set[int] = set()


@flow_router.callback_query(F.data.startswith("vid:"))
async def send_videos(call: CallbackQuery, bot: Bot) -> None:
    test = open_test(call.data.split(":", 1)[1], call.from_user)
    if not test or not test.videos:
        await call.answer("Video topilmadi", show_alert=True)
        return
    if not access.is_admin(call.from_user):
        reason = test.video_block_reason(solved=database.has_result(call.from_user.id, test.id))
        if reason:
            await call.answer(reason, show_alert=True)
            return
    chat_id = call.message.chat.id
    if chat_id in _sending:
        await call.answer("⏳ Video yuborilmoqda, biroz kuting...", show_alert=True)
        return
    await call.answer("🎬 Video yuborilmoqda...")
    _sending.add(chat_id)
    try:
        parts = test.videos
        n = len(parts)
        slow = any(media.needs_upload(v.path, v.file_id) for v in parts)
        await bot.send_message(
            chat_id,
            f"🎬 <b>{escape(test.title)} — video tahlil</b>\n"
            + (f"🎞 {n} qismdan iborat. " if n > 1 else "")
            + "Yuborilmoqda... ⏳"
            + ("\n\n<i>Birinchi marta yuklanayotgani uchun bir necha daqiqa vaqt olishi mumkin.</i>" if slow else ""),
        )
        for i, v in enumerate(parts, start=1):
            await bot.send_chat_action(chat_id, ChatAction.UPLOAD_VIDEO)
            caption = (f"🎬 <b>{escape(test.title)} — tahlil</b>"
                       + (f"\n🎞 {i}/{n}-qism" if n > 1 else "")
                       + (f" · {escape(v.title)}" if v.title != f"{i}-qism" else ""))
            try:
                await media.send_media(
                    bot, chat_id, v.kind, v.path, v.file_id,
                    filename=f"{export.safe_filename(test.title)} - {i}.mp4", caption=caption,
                    **({"supports_streaming": True} if v.kind == "video" else {}),
                )
            except Exception:
                log.exception("Video yuborishda xato (%s/%s) — ombor kanaldan olinadi", test.id, v.id)
                if not await storage.recover(bot, chat_id, test.id, "video", str(v.id), caption=caption):
                    raise
        await bot.send_message(
            chat_id, ("✅ Barcha qismlar yuborildi." if n > 1 else "✅ Video yuborildi.") + " Yoqimli tomosha! 🍿",
            reply_markup=ikb([("🏠 Bosh menyu", "home")]),
        )
    except Exception:
        log.exception("Video yuborishda xato")
        await bot.send_message(chat_id, "⚠️ Videoni yuborishda xatolik bo‘ldi. Birozdan keyin qayta urinib ko‘ring.")
    finally:
        _sending.discard(chat_id)


# ---------------------------------------------------------------- boshqa xabarlar
@fallback_router.message()
async def fallback(message: Message) -> None:
    await message.answer(
        "🤔 Kechirasiz, buni tushunmadim.\n\n📋 Iltimos, quyidagi menyudan xizmatni tanlang 👇",
        reply_markup=menu_for(message.from_user),
    )
