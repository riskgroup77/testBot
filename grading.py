"""Javoblarni o'qish va tekshirish.

Test tuzilishi (standart): 1–35 — variantli (A–D), 36–45 — yozma, har birida a) va b) qismi.
Jami 35 + 20 = 55 ta javob, har biri 1 ball.

O'quvchi javoblarni BITTA xabarda yuboradi:
    1B 2D 3A 4D ... 35B
    36. a) 17 b) 22
    37. a) 5 b) 3,5
    ...
Variantli qism boshqa ko'rinishlarda ham tushuniladi: "1. a" (har qatorda), "1)b, 2-d", "BDAD..." (faqat harflar).
Yozma qism: "36. a) 17 b) 22", "36a) 17 36b) 22", "36 a: 17; b: 22".
Kirill harflar (а, в, с, д ...) lotinga o'giriladi. Javob yozilmagan savol XATO hisoblanadi.
"""
import re
from dataclasses import dataclass, field

from content import Test

# Kirill klaviaturada yozilgan harflarni lotinga o'giradi
CYR = str.maketrans({"а": "a", "б": "b", "в": "b", "с": "c", "ц": "c", "д": "d",
                     "е": "e", "э": "e", "ф": "f",
                     "А": "a", "Б": "b", "В": "b", "С": "c", "Ц": "c", "Д": "d",
                     "Е": "e", "Э": "e", "Ф": "f"})

# raqam + (ixtiyoriy belgi) + bitta harf; harfdan keyin yana harf kelmasligi kerak
# ("35ta", "2 dan" kabi so'zlar javob deb olinmaydi)
PAIR_RE = re.compile(r"(?<!\d)(\d{1,3})\s*[.)\-:=,]?\s*([a-zA-Z])(?![a-zA-Z])")
# faqat harflardan iborat ketma-ketlik: "BDADAC..." yoki "B D A D ..." yoki "b,d,a"
LETTERS_ONLY_RE = re.compile(r"^[A-Fa-f\s,.;]+$")
# yozma qism belgisi: "36. a)", "36a)", "b)", "b:" ...
OPEN_MARK = re.compile(r"(?:(?<![\d.,])(\d{1,3})\s*[.)\-:]?\s*)?(?<![A-Za-z])([abAB])\s*[).:\-]")

# Baholash shkalasi (55 ta javobli standart test uchun)
SCALE = [("C", 25, 27), ("C+", 28, 32), ("B", 33, 37), ("B+", 38, 45), ("A", 46, 50), ("A+", 51, 55)]
STANDARD_MAX = 55


def grade_letter(score: float) -> str | None:
    """52 -> 'A+'; 25 dan past -> None."""
    s = int(round(score))
    for g, lo, hi in SCALE:
        if lo <= s <= hi:
            return g
    return "A+" if s > SCALE[-1][2] else None


def open_sort(qid: str) -> tuple[int, str]:
    return int(qid[:-1]), qid[-1]


def open_label(qid: str) -> str:
    """'36a' -> '36 a)'"""
    return f"{qid[:-1]} {qid[-1]})"


@dataclass
class Parsed:
    answers: dict[int, str] = field(default_factory=dict)       # variantli: savol -> harf
    open: dict[str, str] = field(default_factory=dict)          # yozma: '36a' -> '17'
    unknown: list[str] = field(default_factory=list)     # testda yo'q savol raqamlari
    invalid: list[str] = field(default_factory=list)     # ruxsat etilmagan harflar
    bad: dict[int, str] = field(default_factory=dict)    # savol -> noto'g'ri yozilgan harf
    duplicates: list[int] = field(default_factory=list)
    positional: bool = False                             # faqat harflar ketma-ket yuborilgan

    @property
    def any(self) -> bool:
        return bool(self.answers or self.open)

    def stored(self) -> dict[str, str]:
        """Bazaga yoziladigan ko'rinish: {'1': 'B', ..., '36a': '17'}"""
        return {**{str(q): v for q, v in self.answers.items()}, **self.open}


@dataclass
class Result:
    correct: list[int]
    wrong: list[int]       # xato + javobsiz (javobsiz ham xato hisoblanadi)
    empty: list[int]       # shulardan javob yozilmaganlari
    score: float
    max_score: float
    open_correct: list[str] = field(default_factory=list)
    open_wrong: list[str] = field(default_factory=list)
    open_empty: list[str] = field(default_factory=list)

    @property
    def percent(self) -> int:
        return round(self.score / self.max_score * 100) if self.max_score else 0

    @property
    def n_correct(self) -> int:
        return len(self.correct) + len(self.open_correct)

    @property
    def n_wrong(self) -> int:
        return len(self.wrong) + len(self.open_wrong)

    @property
    def n_empty(self) -> int:
        return len(self.empty) + len(self.open_empty)


def split_answers(stored: dict) -> tuple[dict[int, str], dict[str, str]]:
    """Bazadagi {'1': 'B', '36a': '17'} -> ({1: 'B'}, {'36a': '17'})"""
    mc = {int(k): v for k, v in stored.items() if str(k).isdigit()}
    op = {str(k): v for k, v in stored.items() if not str(k).isdigit()}
    return mc, op


# ---------------------------------------------------------------- yozma javoblarni solishtirish
def _norm(s: str) -> str:
    s = (s or "").translate(CYR).lower().strip()
    s = re.sub(r"^[a-z][a-z0-9_]*\s*=\s*", "", s)          # "t=2 s" -> "2 s"
    s = s.replace("·", "*").replace("×", "*").replace("−", "-").replace("–", "-")
    s = re.sub(r"(?<=\d)\s+10\s*\^", "*10^", s)          # "2,1 10^8" -> "2,1*10^8"
    s = re.sub(r"\s+", "", s).replace(",", ".")
    s = re.sub(r"(?<=\d)x(?=10)", "*", s)                 # "2,1x10^8" -> "2,1*10^8"
    return s.strip(".;")


def _number(s: str) -> float | None:
    """Boshidagi sonni oladi: '2.1*10^8m/s' -> 2.1e8, '17' -> 17.0, '0.5kj' -> 0.5."""
    m = re.match(r"^([-+]?\d+(?:\.\d+)?)(?:(?:\*10\^?|e)([-+]?\d+))?", s)
    if not m:
        return None
    v = float(m.group(1))
    if m.group(2):
        v *= 10 ** int(m.group(2))
    return v


def same_answer(given: str | None, key: str) -> bool:
    """Yozma javob to'g'rimi. Kalitda bir nechta to'g'ri javob bo'lsa «|» bilan yoziladi: '0,5 | 1/2'."""
    if not given or not given.strip():
        return False
    g = _norm(given)
    for alt in key.split("|"):
        k = _norm(alt)
        if not k:
            continue
        if g == k:
            return True
        gn, kn = _number(g), _number(k)
        if gn is not None and kn is not None and abs(gn - kn) <= 1e-9 * max(1.0, abs(kn)):
            return True
    return False


# ---------------------------------------------------------------- matndan o'qish
def _open_marks(text: str, valid=lambda n: True) -> list[tuple[int | None, str, int, int]]:
    """Yozma qism belgilari: (savol raqami yoki None, 'a'|'b', belgi boshi, belgi oxiri).

    Belgi oldidagi son har doim ham savol raqami emas: "36. a) 17 b) 22" da 17 — a) ning JAVOBI.
    Shuning uchun son savol raqami deb olinadi faqat:
      • a) oldida — yangi (oldingisidan katta) raqam bo'lsa;
      • b) oldida — aynan joriy savol raqami bo'lsa ("36a) 17 36b) 22").
    Aks holda son javobga tegishli bo'lib qoladi.
    """
    out = []
    cur = None
    for m in OPEN_MARK.finditer(text):
        n = int(m.group(1)) if m.group(1) else None
        part = m.group(2).lower()
        start = m.start()
        if n is not None:
            ok = (part == "a" and (cur is None or n > cur) and valid(n)) or (part == "b" and n == cur)
            if not ok:
                n, start = None, m.start(2)
        if n is not None:
            cur = n
            out.append((n, part, start, m.end()))
        elif part == "b" and cur is not None:
            out.append((cur, part, start, m.end()))
        else:
            out.append((None, part, start, m.end()))
    return out


def _read_open(text: str, open_nums: set[int]) -> tuple[int, dict[str, str]]:
    """Yozma qismni o'qiydi. (yozma qism boshlanadigan joy, {'36a': '17', ...})"""
    marks = _open_marks(text, valid=lambda n: n in open_nums)
    start = next((s for n, _, s, _ in marks if n in open_nums), None)
    if start is None:
        return len(text), {}
    out: dict[str, str] = {}
    marks = [m for m in marks if m[2] >= start]
    for i, (n, part, s, e) in enumerate(marks):
        if n is None or n not in open_nums:
            continue
        end = marks[i + 1][2] if i + 1 < len(marks) else len(text)
        value = text[e:end].strip(" \t\r\n;,")
        if value:
            out[f"{n}{part}"] = value
    return start, out


def _open_numbers_in(text: str) -> set[int]:
    """Admin kalitida yozma savollar: a) VA b) qismi bor birinchi raqamdan boshlab,
    a) yoki b) belgisi qo'yilgan barcha raqamlar (birining qismi tushib qolsa ham — xato aytiladi)."""
    parts: dict[int, set[str]] = {}
    for n, part, _, _ in _open_marks(text):
        if n is not None:
            parts.setdefault(n, set()).add(part)
    both = [n for n, ps in parts.items() if {"a", "b"} <= ps]
    if not both:
        return set()
    first = min(both)
    # birinchi to'liq yozma savoldan oldin "a)" bilan yozilgan yakka savollar ham yozma hisoblanadi
    # (masalan 36 da b) unutilgan, 37 dan boshlab to'liq) — agar ular ketma-ket blok bo'lsa
    lone = sorted((n for n in parts if n < first), reverse=True)
    for n in lone:
        if n == first - 1 and "a" in parts[n]:
            first = n
    return {n for n in parts if n >= first}


def _add(p: Parsed, test: Test, num: int, letter: str) -> None:
    letter = letter.upper()
    if num not in test.key:
        if num not in test.open_numbers:
            p.unknown.append(str(num))
        return
    if letter not in test.options[num]:
        p.invalid.append(f"{num}{letter}")
        if num not in p.answers:
            p.bad[num] = letter
        return
    p.bad.pop(num, None)
    if num in p.answers and num not in p.duplicates:
        p.duplicates.append(num)
    p.answers[num] = letter          # takrorlansa — oxirgisi olinadi


def parse_answers(text: str, test: Test) -> Parsed:
    p = Parsed()
    text = text.translate(CYR)
    mc_text = text
    if test.open_key:
        start, p.open = _read_open(text, test.open_numbers)
        mc_text = text[:start]

    pairs = PAIR_RE.findall(mc_text)
    if pairs:
        for num_s, letter in pairs:
            _add(p, test, int(num_s), letter)
        return p

    # Raqamsiz, faqat harflar: 1-savoldan boshlab tartib bilan
    if LETTERS_ONLY_RE.match(mc_text.strip()):
        letters = re.findall(r"[A-Fa-f]", mc_text)
        if len(letters) >= 5:
            p.positional = True
            for num, letter in zip(sorted(test.key), letters):
                _add(p, test, num, letter)
            extra = len(letters) - len(test.key)
            if extra > 0:
                p.unknown.append(f"+{extra} ta ortiqcha harf")
    return p


def grade(answers: dict[int, str], test: Test, open_answers: dict[str, str] | None = None) -> Result:
    correct, wrong, empty = [], [], []
    for q in sorted(test.key):
        given = answers.get(q)
        if given == test.key[q]:
            correct.append(q)
        else:
            wrong.append(q)
            if given is None:
                empty.append(q)
    oc, ow, oe = [], [], []
    open_answers = open_answers or {}
    for qid in sorted(test.open_key, key=open_sort):
        given = open_answers.get(qid)
        if same_answer(given, test.open_key[qid]):
            oc.append(qid)
        else:
            ow.append(qid)
            if not given:
                oe.append(qid)
    score = round(sum(test.points[q] for q in correct) + sum(test.open_points.get(q, 1.0) for q in oc), 1)
    return Result(correct, wrong, empty, score, test.max_score, oc, ow, oe)


def fmt_num(x: float) -> str:
    """54.3 -> '54,3', 70.0 -> '70'"""
    return f"{round(x, 1):g}".replace(".", ",")


def _groups(mapping: dict[int, object]) -> dict[object, list[int]]:
    groups: dict[object, list[int]] = {}
    for q in sorted(mapping):
        groups.setdefault(mapping[q], []).append(q)
    return groups


def options_text(options: dict[int, str]) -> str:
    """'1–32: A–D · 33–35: A–F'"""
    return " · ".join(f"{fmt_nums(qs)}: {o[0]}–{o[-1]}" for o, qs in _groups(options).items())


def options_help(test: Test) -> str:
    return options_text(test.options)


def points_text(points: dict[int, float], open_points: dict[str, float] | None = None) -> str:
    """'1–3, 11: 1,3 · 4–10: 2,2' (+ yozma qism)"""
    g = _groups(points)
    if len(g) == 1:
        text = f"har biri {fmt_num(next(iter(g)))} ball"
    else:
        text = " · ".join(f"{fmt_nums(qs)}: {fmt_num(v)}" for v, qs in g.items())
    if open_points:
        vals = set(open_points.values())
        text += (f" · yozma: har javob {fmt_num(vals.pop())} ball" if len(vals) == 1
                 else f" · yozma: jami {fmt_num(sum(open_points.values()))} ball")
    return text


def key_text(key: dict[int, str], open_key: dict[str, str] | None = None, sep: str = "\n") -> str:
    """'1B 2D ...' va yozma qism: '36. a) 17 b) 22'"""
    text = " ".join(f"{q}{key[q]}" for q in sorted(key))
    if open_key:
        rows: dict[int, dict[str, str]] = {}
        for qid, v in open_key.items():
            rows.setdefault(int(qid[:-1]), {})[qid[-1]] = v
        text += sep + sep.join(f"{n}. a) {rows[n].get('a', '—')} b) {rows[n].get('b', '—')}" for n in sorted(rows))
    return text


# ---------------------------------------------------------------- admin: kalit, variant, ball
KEY_RE = re.compile(r"(?<!\d)(\d{1,3})\s*[.)\-:=,]?\s*([a-hA-H])(?![a-zA-Z])")
KEY_LETTERS_RE = re.compile(r"^[A-Ha-h\s,.;]+$")
NUMS_RE = re.compile(r"(\d+)\s*[-–—]\s*(\d+)|(\d+)")


def parse_key(text: str) -> tuple[dict[int, str], dict[str, str], list[str]]:
    """Admin yozgan kalit. (variantli kalit, yozma kalit, muammolar) qaytaradi.

    '1B 2D 3A ... 35B' + '36. a) 17 b) 22 37. a) 5 b) 3,5 ...'  yoki faqat harflar 'BDAD...'.
    """
    text = text.translate(CYR)
    open_nums = _open_numbers_in(text)
    start, open_key = _read_open(text, open_nums) if open_nums else (len(text), {})
    mc_text = text[:start]
    key: dict[int, str] = {}
    problems: list[str] = []
    pairs = KEY_RE.findall(mc_text)
    if pairs:
        dup = []
        for n, letter in pairs:
            n = int(n)
            if n in key:
                dup.append(n)
            key[n] = letter.upper()
        if dup:
            problems.append(f"ikki marta yozilgan: {fmt_nums(sorted(set(dup)))}")
    elif KEY_LETTERS_RE.match(mc_text.strip() or "x"):
        key = {i: ch.upper() for i, ch in enumerate(re.findall(r"[A-Ha-h]", mc_text), start=1)}
    if key:
        key.pop(0, None)
        missing = [q for q in range(1, max(key) + 1) if q not in key]
        if missing:
            problems.append(f"tushib qolgan savollar: {fmt_nums(missing)}")
    if open_key:
        nums = sorted({int(q[:-1]) for q in open_key})
        half = [f"{n} {p})" for n in nums for p in "ab" if f"{n}{p}" not in open_key]
        if half:
            problems.append("yozma javobi yo‘q: " + ", ".join(half))
        if key and nums[0] != max(key) + 1:
            problems.append(f"yozma savollar {max(key) + 1} dan boshlanishi kerak (hozir {nums[0]} dan)")
        gaps = [n for n in range(nums[0], nums[-1] + 1) if n not in nums]
        if gaps:
            problems.append(f"tushib qolgan yozma savollar: {fmt_nums(gaps)}")
    return key, open_key, problems


def detect_key(text: str) -> tuple[dict[int, str], dict[str, str]]:
    """PDF dagi KALITLAR sahifasidan variantli ('1 B 17 B') va yozma ('36 a) t=2 s b) h=5 m') javoblarni topadi."""
    found: dict[int, str] = {}
    for n, letter in re.findall(r"(?<![\w.,])(\d{1,3})\s*[.)]?\s+([A-H])(?![A-Za-z])", text):
        found.setdefault(int(n), letter)
    key, q = {}, 1
    while q in found:
        key[q] = found[q]
        q += 1
    key = key if len(key) >= 5 else {}
    open_key: dict[str, str] = {}
    open_nums = {n for n in _open_numbers_in(text) if not key or n > max(key)}
    if open_nums:
        _, raw = _read_open(text, open_nums)
        # PDF matnidagi keraksiz belgilar va ortiqcha bo'shliqlar olib tashlanadi
        open_key = {k: re.sub(r"\s+", " ", re.sub(r"[-]", "", v)).strip() for k, v in raw.items()}
        open_key = {k: v for k, v in open_key.items() if v}
    return key, open_key


def _nums(s: str, allowed: list[int]) -> list[int]:
    out = []
    for a, b, single in NUMS_RE.findall(s):
        out += list(range(int(a), int(b) + 1)) if a else [int(single)]
    return [q for q in out if q in allowed]


_REST_RE = re.compile(r"qolgan|boshqa|hammasi|barcha|\*", re.I)


def parse_options(text: str, questions: list[int], default: str = "ABCD") -> dict[int, str]:
    """'33-35: A-F' (qolganlari A–D). Xato bo'lsa ValueError."""
    opts = {q: default for q in questions}
    lines = [ln for ln in re.split(r"[;\n]+", text.translate(CYR)) if ln.strip()]
    lines.sort(key=lambda ln: not _REST_RE.search(ln.partition(":")[0]))  # "qolgan: ..." birinchi
    for line in lines:
        left, sep, right = line.partition(":")
        if not sep:
            raise ValueError(f"«{line.strip()}» — ikki nuqta (:) yo‘q. Masalan: 33-35: A-F")
        letters = re.findall(r"[A-Ha-h]", right)
        if not letters:
            raise ValueError(f"«{line.strip()}» — harflar topilmadi")
        last = max(ch.upper() for ch in letters)
        value = "ABCDEFGH"[: "ABCDEFGH".index(last) + 1]
        if len(value) < 2:
            raise ValueError(f"«{line.strip()}» — kamida 2 ta variant bo‘lishi kerak")
        targets = questions if _REST_RE.search(left) else _nums(left, questions)
        if not targets:
            raise ValueError(f"«{line.strip()}» — savol raqamlari topilmadi")
        for q in targets:
            opts[q] = value
    return opts


def parse_points(text: str, questions: list[int]) -> dict[int, float]:
    """'2,2' (hammasiga) yoki '1-3, 11: 1,3; qolgan: 2,2'. Xato bo'lsa ValueError."""
    def num(s: str) -> float:
        m = re.search(r"\d+(?:[.,]\d+)?", s)
        if not m:
            raise ValueError(f"«{s.strip()}» — ball topilmadi")
        v = float(m.group().replace(",", "."))
        if not 0 < v <= 100:
            raise ValueError(f"«{s.strip()}» — ball 0 dan katta bo‘lishi kerak")
        return v

    text = text.strip()
    if ":" not in text:
        v = num(text)
        return {q: v for q in questions}
    points: dict[int, float] = {}
    rest = None
    for line in re.split(r"[;\n]+", text):
        if not line.strip():
            continue
        left, sep, right = line.partition(":")
        if not sep:
            raise ValueError(f"«{line.strip()}» — ikki nuqta (:) yo‘q. Masalan: 1-3: 1,3")
        if _REST_RE.search(left):
            rest = num(right)
            continue
        targets = _nums(left, questions)
        if not targets:
            raise ValueError(f"«{line.strip()}» — savol raqamlari topilmadi")
        for q in targets:
            points[q] = num(right)
    missing = [q for q in questions if q not in points]
    if missing and rest is None:
        raise ValueError(f"Ball yozilmagan savollar: {fmt_nums(missing)}\n"
                         "Oxiriga «qolgan: 2» deb qo‘shsangiz, ularga shu ball beriladi.")
    for q in missing:
        points[q] = rest
    return points


def fmt_nums(nums: list[int]) -> str:
    """[3, 4, 5, 9, 12, 13] -> '3–5, 9, 12, 13'"""
    out, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(f"{nums[i]}–{nums[j]}" if j - i >= 2 else ", ".join(map(str, nums[i:j + 1])))
        i = j + 1
    return ", ".join(out)
