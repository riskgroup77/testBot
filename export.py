"""Natijalarni Excel (.xlsx) faylga chiqarish."""
import io
import json
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import content
import database
from content import Test
from grading import STANDARD_MAX, grade_letter, open_label, open_sort, same_answer, split_answers

HEAD_FILL = PatternFill("solid", fgColor="1E3A5F")
HEAD_FONT = Font(bold=True, color="FFFFFF")
OK_FILL = PatternFill("solid", fgColor="D9F2E3")
BAD_FILL = PatternFill("solid", fgColor="FADBD8")
EMPTY_FILL = PatternFill("solid", fgColor="EEEEEE")
KEY_FILL = PatternFill("solid", fgColor="FFF2CC")
THIN = Side(style="thin", color="D0D0D0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center")


def safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "-", name).replace(" · ", " - ").strip() or "natijalar"


def _name(r) -> str:
    return " ".join(x for x in (r["first_name"], r["last_name"]) if x) or str(r["telegram_id"])


def _date(s: str) -> str:
    return (s or "").replace("T", " ")


def _sheet(wb: Workbook, title: str, headers: list[str], widths: list[int], first: bool = False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    ws.append(headers)
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for c in ws[1]:
        c.fill, c.font, c.alignment, c.border = HEAD_FILL, HEAD_FONT, CENTER, BORDER
    ws.freeze_panes = "A2"
    return ws


def _finish(ws, start_row: int = 2) -> None:
    for row in ws.iter_rows(min_row=start_row):
        for c in row:
            c.border = BORDER
    if ws.max_row > 1:
        ws.auto_filter.ref = ws.dimensions


def _bytes(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_workbook(t: Test) -> bytes:
    best = database.best_results(t.id)
    rows = database.test_results(t.id)
    wb = Workbook()
    standard = round(t.max_score) == STANDARD_MAX

    # 1) Reyting — har bir o'quvchining eng yaxshi natijasi
    ws = _sheet(wb, "Reyting", ["№", "O‘quvchi", "Username", "Telegram ID", "Ball", "Maks", "Foiz", "Daraja",
                                "To‘g‘ri", "Xato", "shundan javobsiz", "Urinishlar", "Sana"],
                [5, 28, 18, 14, 8, 8, 8, 8, 9, 8, 10, 11, 19], first=True)
    for i, r in enumerate(best, start=1):
        pct = round(r["score"] / r["max_score"] * 100) if r["max_score"] else 0
        ws.append([i, _name(r), f"@{r['username']}" if r["username"] else "", r["telegram_id"],
                   r["score"], r["max_score"], pct / 100,
                   (grade_letter(r["score"]) or "—") if standard else "",
                   r["correct"], r["wrong"], r["empty"], r["attempts"], _date(r["created_at"])])
        ws.cell(ws.max_row, 7).number_format = "0%"
    _finish(ws)

    # ustunlar: variantli savollar + yozma javoblar (36 a), 36 b) ...)
    cols = [(str(q), t.key[q], t.points[q], (lambda mc, op, q=q: mc.get(q)),
             (lambda g, q=q: g == t.key[q])) for q in t.questions]
    cols += [(open_label(qid), t.open_key[qid], t.open_points.get(qid, 1.0), (lambda mc, op, qid=qid: op.get(qid)),
              (lambda g, qid=qid: same_answer(g, t.open_key[qid])))
             for qid in sorted(t.open_key, key=open_sort)]

    # 2) Barcha urinishlar — har bir savolga bergan javobi (yashil/qizil)
    ws = _sheet(wb, "Barcha urinishlar",
                ["Sana", "O‘quvchi", "Username", "Ball", "Foiz"] + [c[0] for c in cols],
                [19, 28, 18, 8, 8] + [4 if c[0].isdigit() else 8 for c in cols])
    ws.append(["", "TO‘G‘RI JAVOB (kalit)", "", "", ""] + [c[1] for c in cols])
    for c in ws[2]:
        c.fill, c.font, c.alignment = KEY_FILL, Font(bold=True), CENTER
    for r in rows:
        mc, op = split_answers(json.loads(r["answers"]))
        pct = round(r["score"] / r["max_score"] * 100) if r["max_score"] else 0
        ws.append([_date(r["created_at"]), _name(r), f"@{r['username']}" if r["username"] else "",
                   r["score"], pct / 100] + [(c[3](mc, op) or "—") for c in cols])
        n = ws.max_row
        ws.cell(n, 5).number_format = "0%"
        for j, col in enumerate(cols, start=6):
            cell = ws.cell(n, j)
            cell.alignment = CENTER
            given = col[3](mc, op)
            cell.fill = EMPTY_FILL if not given else OK_FILL if col[4](given) else BAD_FILL
    ws.freeze_panes = "C3"
    _finish(ws)

    # 3) Savollar tahlili — eng yaxshi urinishlar bo'yicha qaysi savol qiyin bo'ldi
    ws = _sheet(wb, "Savollar tahlili", ["Savol", "To‘g‘ri javob", "Ball", "To‘g‘ri", "Xato",
                                         "Javobsiz", "To‘g‘ri %", "Eng ko‘p yozilgan xato"],
                [8, 14, 8, 9, 8, 10, 11, 22])
    best_answers = [split_answers(json.loads(r["answers"])) for r in best]
    for label, right, pts, get, check in cols:
        given = [get(mc, op) for mc, op in best_answers]
        ok = sum(bool(g) and check(g) for g in given)
        empty = sum(not g for g in given)
        bad = len(given) - ok - empty
        wrong_letters = [g.strip() for g in given if g and not check(g)]
        top = max(set(wrong_letters), key=wrong_letters.count) if wrong_letters else ""
        top_txt = f"{top} ({wrong_letters.count(top)} ta)" if top else ""
        ws.append([label, right, pts, ok, bad, empty, (ok / len(given)) if given else 0, top_txt])
        n = ws.max_row
        ws.cell(n, 7).number_format = "0%"
        if given:
            share = ok / len(given)
            ws.cell(n, 7).fill = OK_FILL if share >= 0.7 else BAD_FILL if share < 0.4 else KEY_FILL
    _finish(ws)
    return _bytes(wb)


def all_workbook() -> bytes:
    wb = Workbook()
    ws = _sheet(wb, "Foydalanuvchilar", ["№", "Ism", "Familiya", "Username", "Telegram ID",
                                         "Ro‘yxatdan o‘tgan", "Oxirgi faollik", "Yechgan testlar",
                                         "Urinishlar"],
                [5, 18, 18, 18, 14, 19, 19, 14, 11], first=True)
    for i, u in enumerate(database.all_users(), start=1):
        ws.append([i, u["first_name"] or "", u["last_name"] or "",
                   f"@{u['username']}" if u["username"] else "", u["telegram_id"],
                   _date(u["registered_at"]), _date(u["last_seen"]), u["tests"], u["attempts"]])
    _finish(ws)

    titles = {t.id: t.title for t in content.all_tests()}
    ws = _sheet(wb, "Natijalar", ["Sana", "O‘quvchi", "Username", "Test", "Ball", "Maks", "Foiz",
                                  "To‘g‘ri", "Xato", "shundan javobsiz"],
                [19, 28, 18, 26, 8, 8, 8, 9, 8, 10])
    for r in database.all_results():
        pct = round(r["score"] / r["max_score"] * 100) if r["max_score"] else 0
        ws.append([_date(r["created_at"]), _name(r), f"@{r['username']}" if r["username"] else "",
                   titles.get(r["test_id"], r["test_id"]), r["score"], r["max_score"], pct / 100,
                   r["correct"], r["wrong"], r["empty"]])
        ws.cell(ws.max_row, 7).number_format = "0%"
    _finish(ws)
    return _bytes(wb)
