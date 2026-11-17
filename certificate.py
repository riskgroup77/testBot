"""🏅 Sertifikat — tanlangan «Klassik» dizayn (ballsiz, faqat daraja).

HTML shablon to'ldiriladi va kompyuterdagi Chrome / Edge (headless) yordamida
PNG (Telegram'da ko'rish uchun) yoki PDF (chop etish uchun) qilinadi.
Serverga ko'chirilganda u yerga Chromium o'rnatiladi (apt install chromium).
"""
import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from html import escape
from pathlib import Path

import content
import database
import timeutil
from content import Test

log = logging.getLogger("certificate")
_sem = asyncio.Semaphore(2)          # bir vaqtda ko'pi bilan 2 ta brauzer

GRADE_COLORS = {"C": "#4B5563", "C+": "#B45309", "B": "#1D4ED8", "B+": "#0F766E", "A": "#15803D", "A+": "#8A6A12"}
DEFAULT_SUBJECT = "Fizika"

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def browser() -> str | None:
    for p in BROWSERS:
        if p and Path(p).exists():
            return p
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome", "msedge"):
        if found := shutil.which(name):
            return found
    return None


def enabled() -> bool:
    return database.setting("cert_on", "1") == "1"


def teacher() -> str:
    return database.setting("cert_teacher", "")


def subject_of(t: Test) -> str:
    return t.subject or database.setting("cert_subject", DEFAULT_SUBJECT) or DEFAULT_SUBJECT


def eligible(t: Test, score: float) -> str | None:
    """Sertifikat beriladimi: standart (55 ballik) test va daraja C dan past emas. Daraja yoki None."""
    from grading import STANDARD_MAX, grade_letter
    if not enabled() or round(t.max_score) != STANDARD_MAX:
        return None
    return grade_letter(score)


def display_name(user_row, fallback: str) -> str:
    if user_row is not None and user_row["cert_name"]:
        return user_row["cert_name"]
    return fallback


FONTS_DIR = Path(__file__).resolve().parent / "assets"


def _fonts_css() -> str:
    """Shriftlar bot papkasida (assets/fonts) — internetsiz, tez va ishonchli."""
    css_file = FONTS_DIR / "fonts.css"
    if not css_file.exists():
        return ""
    base = (FONTS_DIR / "fonts").as_uri() + "/"
    return css_file.read_text(encoding="utf-8").replace("url('fonts/", "url('" + base)


TEMPLATE = """<!doctype html><html lang="uz"><head><meta charset="utf-8">
<style>{{FONTS}}</style>
<style>
@page{size:1123px 794px;margin:0}
html,body{margin:0;width:1123px;height:794px;overflow:hidden;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.f-serif{font-family:'Playfair Display',Georgia,serif}
</style></head><body>
<div style="width:1123px;height:794px;box-sizing:border-box;background:#FBF7EE;padding:26px;font-family:'Inter',Arial,sans-serif;color:#1F2A44">
 <div style="height:100%;box-sizing:border-box;border:3px solid #1F2A44;padding:7px">
  <div style="height:100%;box-sizing:border-box;border:1px solid #B08D3C;padding:34px 64px 30px;display:flex;flex-direction:column;align-items:center;gap:14px">
   <div style="width:100%;display:flex;justify-content:space-between;align-items:center;font-size:13px;letter-spacing:3px;font-weight:600;color:#6B5A2E">
    <span>AKMALJON YORDAMCHI</span><span>{{HEADER}}</span>
   </div>
   <h1 class="f-serif" style="margin:6px 0 0;font-size:76px;font-weight:700;letter-spacing:12px;color:#1F2A44">SERTIFIKAT</h1>
   <div style="display:flex;align-items:center;gap:16px;color:#8A6A12">
    <span style="width:90px;height:1px;background:#B08D3C"></span>
    <span style="font-size:15px;letter-spacing:2px;font-weight:500">Ushbu sertifikat egasi</span>
    <span style="width:90px;height:1px;background:#B08D3C"></span>
   </div>
   <div class="f-serif" style="font-style:italic;font-size:{{NAME_SIZE}}px;font-weight:600;color:#1F2A44;max-width:950px;text-align:center;line-height:1.15">{{NAME}}</div>
   <p style="margin:0;max-width:760px;text-align:center;font-size:17px;line-height:1.55;color:#3A4356">«{{TITLE}}» testini muvaffaqiyatli topshirgani uchun taqdim etiladi.</p>
   <div style="display:flex;align-items:center;gap:64px;margin-top:10px">
    <div style="display:flex;flex-direction:column;align-items:center;gap:4px;min-width:170px">
     <span style="font-size:12px;letter-spacing:2px;color:#6B5A2E;font-weight:600">YO‘NALISH</span>
     <span class="f-serif" style="font-size:30px;font-weight:700">{{SUBJECT}}</span>
    </div>
    <div style="width:168px;height:168px;border-radius:50%;box-sizing:border-box;border:5px solid {{COLOR}};display:flex;flex-direction:column;align-items:center;justify-content:center;background:#FFFDF7;outline:1px solid #B08D3C;outline-offset:6px">
     <span style="font-size:12px;letter-spacing:2px;color:#6B5A2E;font-weight:600">DARAJA</span>
     <span class="f-serif" style="font-size:72px;font-weight:700;line-height:1;color:{{COLOR}}">{{GRADE}}</span>
    </div>
    <div style="display:flex;flex-direction:column;align-items:center;gap:4px;min-width:170px">
     <span style="font-size:12px;letter-spacing:2px;color:#6B5A2E;font-weight:600">BERILGAN SANA</span>
     <span class="f-serif" style="font-size:30px;font-weight:700">{{DATE}}</span>
    </div>
   </div>
   <div style="display:flex;gap:8px;margin-top:14px">{{LEVELS}}</div>
   <div style="width:100%;margin-top:auto;display:flex;justify-content:space-between;align-items:flex-end">
    <div style="display:flex;flex-direction:column;gap:6px;width:260px">
     <span style="border-bottom:1px solid #1F2A44;height:28px"></span>
     <span style="font-size:13px;color:#3A4356">O‘qituvchi{{TEACHER}}</span>
    </div>
    <div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px;font-size:13px;color:#3A4356">
     <span>Sertifikat № <b>{{CODE}}</b></span>
     <span>Tekshirish: @{{BOT}}</span>
    </div>
   </div>
  </div>
 </div>
</div></body></html>"""

LEVEL_BASE = ("display:flex;align-items:center;justify-content:center;width:62px;height:36px;"
              "border-radius:6px;font-weight:700;font-size:15px;")


def build_html(*, name: str, title: str, header: str, subject: str, grade: str, date: str,
               code: str, bot_username: str, teacher_name: str = "") -> str:
    color = GRADE_COLORS.get(grade, "#8A6A12")
    levels = "".join(
        f'<div style="{LEVEL_BASE}'
        + (f'background:{c};color:#FFFFFF;border:1px solid {c}' if g == grade
           else 'background:transparent;color:#1F2A44;border:1px solid #D6CBB0')
        + f'">{escape(g)}</div>'
        for g, c in GRADE_COLORS.items())
    n = len(name)
    size = 50 if n <= 22 else 42 if n <= 30 else 34 if n <= 40 else 28
    values = {
        "HEADER": escape(header.upper()), "NAME": escape(name), "NAME_SIZE": str(size),
        "TITLE": escape(title), "SUBJECT": escape(subject), "COLOR": color, "GRADE": escape(grade),
        "DATE": escape(date), "LEVELS": levels, "CODE": escape(code), "BOT": escape(bot_username),
        "TEACHER": (": " + escape(teacher_name)) if teacher_name else "",
        "FONTS": _fonts_css(),
    }
    html = TEMPLATE
    for k, v in values.items():
        html = html.replace("{{" + k + "}}", v)
    return html


def html_for(cert, t: Test | None, bot_username: str) -> str:
    title = t.title if t else cert["test_id"]
    category = content.CATEGORIES.get(t.category if t else "milliy", {})
    parent = category.get("parent")
    head = (content.CATEGORIES[parent]["short"] if parent else category.get("short", "Milliy sertifikat"))
    head = {"Milliy": "Milliy sertifikat"}.get(head, head)
    return build_html(
        name=cert["name"], title=title, header=f"{head} · {subject_of(t) if t else DEFAULT_SUBJECT}",
        subject=subject_of(t) if t else DEFAULT_SUBJECT, grade=cert["grade"],
        date=timeutil.parse_iso(cert["issued_at"][:16]).strftime("%d.%m.%Y"),
        code=cert["code"], bot_username=bot_username, teacher_name=teacher())


def _run_once(html: str, mode: str) -> bytes:
    exe = browser()
    if not exe:
        raise RuntimeError("Chrome yoki Edge topilmadi — sertifikat yasab bo'lmaydi")
    with tempfile.TemporaryDirectory(prefix="cert_", ignore_cleanup_errors=True) as tmp:
        page = Path(tmp) / "c.html"
        page.write_text(html, encoding="utf-8")
        out = Path(tmp) / ("c.png" if mode == "png" else "c.pdf")
        args = [exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                "--no-default-browser-check", "--disable-extensions", "--disable-background-networking",
                "--disable-sync", "--allow-file-access-from-files",
                f"--user-data-dir={Path(tmp) / 'profile'}", "--virtual-time-budget=3000"]
        if mode == "png":
            args += ["--force-device-scale-factor=2", "--window-size=1123,794", f"--screenshot={out}"]
        else:
            args += ["--no-pdf-header-footer", "--print-to-pdf-no-header", f"--print-to-pdf={out}"]
        try:
            subprocess.run(args + [page.as_uri()], capture_output=True, timeout=45)
        except subprocess.TimeoutExpired:
            log.warning("Brauzer javob bermadi (%s) — qayta urinamiz", mode)
        if not out.exists() or out.stat().st_size < 1000:
            raise RuntimeError("Sertifikat rasmi yaratilmadi")
        return out.read_bytes()


def _run(html: str, mode: str) -> bytes:
    try:
        return _run_once(html, mode)
    except RuntimeError:
        return _run_once(html, mode)      # bir marta qayta urinish


async def render(html: str, mode: str = "png") -> bytes:
    async with _sem:
        return await asyncio.to_thread(_run, html, mode)
