"""Ma'lumotlar bazasi (SQLite): foydalanuvchilar, testlar, videolar, natijalar."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

import config


@contextmanager
def _connect():
    """Ulanishni ochadi, o'zgarishlarni saqlaydi va har doim yopadi."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _now() -> str:
    from timeutil import now
    return now().isoformat(timespec="seconds")


def _add_columns(conn, table: str, columns: list[tuple[str, str]]) -> list[str]:
    """Eski bazaga yangi ustunlarni qo'shadi. Qo'shilganlar ro'yxatini qaytaradi."""
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    added = []
    for col, decl in columns:
        if col not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            added.append(col)
    return added


def init() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id    INTEGER PRIMARY KEY,
                username       TEXT,
                first_name     TEXT,
                last_name      TEXT,
                language_code  TEXT,
                is_premium     INTEGER DEFAULT 0,
                registered_at  TEXT NOT NULL,
                last_seen      TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS results (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id  INTEGER NOT NULL,
                test_id      TEXT NOT NULL,
                answers      TEXT NOT NULL,
                correct      INTEGER NOT NULL,
                wrong        INTEGER NOT NULL,
                empty        INTEGER NOT NULL,
                score        REAL NOT NULL,
                max_score    REAL NOT NULL,
                created_at   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_results_test ON results(test_id);
            -- Telegram'ga bir marta yuklangan fayllarning file_id si (qayta yuklamaslik uchun)
            CREATE TABLE IF NOT EXISTS media_cache (path TEXT PRIMARY KEY, file_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tests (
                id              TEXT PRIMARY KEY,
                title           TEXT NOT NULL,
                subject         TEXT NOT NULL DEFAULT '',
                data            TEXT NOT NULL,          -- {"key":{}, "points":{}, "options":{}}
                open_questions  TEXT NOT NULL DEFAULT '',
                show_correct    INTEGER NOT NULL DEFAULT 1,
                active          INTEGER NOT NULL DEFAULT 1,
                pdf_path        TEXT,                   -- bot papkasiga nisbatan yo'l
                pdf_file_id     TEXT,                   -- yoki Telegram file_id
                pdf_name        TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS videos (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id   TEXT NOT NULL,
                kind      TEXT NOT NULL DEFAULT 'video',  -- 'video' yoki 'document'
                path      TEXT,
                file_id   TEXT,
                title     TEXT NOT NULL DEFAULT '',
                position  INTEGER NOT NULL DEFAULT 0
            );
            -- ADMIN_USERNAME birinchi marta yozganda uning Telegram ID si shu yerga bog'lanadi
            CREATE TABLE IF NOT EXISTS admins (username TEXT PRIMARY KEY, telegram_id INTEGER NOT NULL,
                                               bound_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        # 2-bosqich: test qoidalari
        added = _add_columns(conn, "tests", [
            ("show_mode", "TEXT NOT NULL DEFAULT 'now'"),      # now | score | after
            ("starts_at", "TEXT"),                              # ochilish vaqti (bo'sh = darhol)
            ("ends_at", "TEXT"),                                # yopilish vaqti (bo'sh = muddatsiz)
            ("max_attempts", "INTEGER NOT NULL DEFAULT 0"),     # 0 = cheksiz
            ("video_lock", "TEXT NOT NULL DEFAULT 'none'"),     # none | solved | deadline
            ("revealed", "INTEGER NOT NULL DEFAULT 0"),         # muddat tugagach tahlil yuborildimi
        ])
        if "show_mode" in added:
            conn.execute("UPDATE tests SET show_mode = CASE WHEN show_correct = 1 THEN 'now' ELSE 'score' END")
        # 2-bosqich: bloklash
        _add_columns(conn, "users", [
            ("banned", "INTEGER NOT NULL DEFAULT 0"),          # admin bloklagan
            ("bot_blocked", "INTEGER NOT NULL DEFAULT 0"),     # foydalanuvchi botni bloklagan
            ("group_id", "INTEGER"),                           # 3-bosqich: sinf/guruh
            ("cert_name", "TEXT"),                             # sertifikatga yoziladigan ism
        ])
        conn.execute(
            """CREATE TABLE IF NOT EXISTS certificates (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                code         TEXT UNIQUE,
                telegram_id  INTEGER NOT NULL,
                test_id      TEXT NOT NULL,
                result_id    INTEGER,
                name         TEXT NOT NULL,
                grade        TEXT NOT NULL,
                issued_at    TEXT NOT NULL
            )""")
        # 3-bosqich: guruhlar, yordamchi adminlar, harakatlar jurnali
        _add_columns(conn, "tests", [("groups", "TEXT NOT NULL DEFAULT '[]'")])  # [] = hammaga (ishlatilmaydi)
        # bo'lim: att0 (Attestatsiya 0-guruh) | attmock (Attestatsiya mock) | milliy (Milliy sertifikat)
        _add_columns(conn, "tests", [("category", "TEXT NOT NULL DEFAULT 'milliy'")])
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS groups (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                code        TEXT NOT NULL UNIQUE,
                created_at  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS staff (
                telegram_id  INTEGER PRIMARY KEY,
                role         TEXT NOT NULL DEFAULT 'helper',
                added_by     INTEGER,
                added_at     TEXT NOT NULL
            );
            -- ombor kanalga joylangan postlar (fayl yo'qolsa shu yerdan qayta olinadi)
            CREATE TABLE IF NOT EXISTS storage_posts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id     TEXT NOT NULL,
                kind        TEXT NOT NULL,      -- header | pdf | video | note
                ref         TEXT NOT NULL DEFAULT '',  -- video id
                chat_id     INTEGER NOT NULL,
                message_id  INTEGER NOT NULL,
                at          TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                at        TEXT NOT NULL,
                admin_id  INTEGER,
                action    TEXT NOT NULL,
                details   TEXT NOT NULL DEFAULT ''
            );
            """
        )


# ---------------------------------------------------------------- sozlamalar
def setting(key: str, default: str = "") -> str:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


# ---------------------------------------------------------------- adminlar
def admin_bindings() -> dict[str, int]:
    with _connect() as conn:
        return {r[0]: r[1] for r in conn.execute("SELECT username, telegram_id FROM admins")}


def bind_admin(username: str, telegram_id: int) -> None:
    with _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO admins (username, telegram_id, bound_at) VALUES (?, ?, ?)",
                     (username, telegram_id, _now()))


# ---------------------------------------------------------------- testlar
def test_rows() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute("SELECT * FROM tests ORDER BY created_at, id").fetchall()


def test_row(test_id: str) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute("SELECT * FROM tests WHERE id = ?", (test_id,)).fetchone()


def test_save(row: dict) -> None:
    cols = ["id", "title", "subject", "data", "open_questions", "show_correct", "active",
            "pdf_path", "pdf_file_id", "pdf_name", "show_mode", "starts_at", "ends_at",
            "max_attempts", "video_lock", "revealed", "groups", "category"]
    values = [row[c] for c in cols]
    with _connect() as conn:
        conn.execute(
            f"INSERT INTO tests ({', '.join(cols)}, created_at) VALUES ({', '.join('?' * len(cols))}, ?) "
            "ON CONFLICT(id) DO UPDATE SET " + ", ".join(f"{c} = excluded.{c}" for c in cols[1:]),
            values + [_now()],
        )


def test_delete(test_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM tests WHERE id = ?", (test_id,))
        conn.execute("DELETE FROM videos WHERE test_id = ?", (test_id,))
        conn.execute("DELETE FROM results WHERE test_id = ?", (test_id,))


def video_rows(test_id: str) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute("SELECT * FROM videos WHERE test_id = ? ORDER BY position, id",
                            (test_id,)).fetchall()


def video_add(test_id: str, kind: str, title: str, path: str | None = None,
              file_id: str | None = None) -> None:
    with _connect() as conn:
        pos = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM videos WHERE test_id = ?",
                           (test_id,)).fetchone()[0]
        conn.execute("INSERT INTO videos (test_id, kind, path, file_id, title, position) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (test_id, kind, path, file_id, title, pos))


def video_delete(video_id: int) -> str | None:
    """O'chiradi va qaysi testga tegishli ekanini qaytaradi."""
    with _connect() as conn:
        row = conn.execute("SELECT test_id FROM videos WHERE id = ?", (video_id,)).fetchone()
        conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
        return row[0] if row else None


# ---------------------------------------------------------------- natijalar
def save_result(telegram_id: int, test_id: str, answers: dict, r) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO results (telegram_id, test_id, answers, correct, wrong, empty,
                                    score, max_score, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (telegram_id, test_id, json.dumps(answers, ensure_ascii=False), r.n_correct, r.n_wrong,
             r.n_empty, r.score, r.max_score, _now()),
        )
        return cur.lastrowid


def update_result(result_id: int, r) -> None:
    with _connect() as conn:
        conn.execute("UPDATE results SET correct=?, wrong=?, empty=?, score=?, max_score=? WHERE id=?",
                     (r.n_correct, r.n_wrong, r.n_empty, r.score, r.max_score, result_id))


def attempts_count(telegram_id: int, test_id: str) -> int:
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM results WHERE telegram_id = ? AND test_id = ?",
            (telegram_id, test_id),
        ).fetchone()[0]


_RESULT_SELECT = """SELECT r.*, u.first_name, u.last_name, u.username, u.group_id
                    FROM results r LEFT JOIN users u ON u.telegram_id = r.telegram_id"""


def latest_results(limit: int = 10) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(f"{_RESULT_SELECT} ORDER BY r.id DESC LIMIT ?", (limit,)).fetchall()


def test_results(test_id: str) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(f"{_RESULT_SELECT} WHERE r.test_id = ? ORDER BY r.id", (test_id,)).fetchall()


def all_results() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(f"{_RESULT_SELECT} ORDER BY r.id").fetchall()


def best_results(test_id: str) -> list[dict]:
    """Har bir o'quvchining eng yaxshi natijasi (teng bo'lsa — birinchisi), ball bo'yicha tartiblangan."""
    best: dict[int, dict] = {}
    for row in test_results(test_id):
        d = dict(row)
        cur = best.get(d["telegram_id"])
        if cur is None:
            d["attempts"] = 1
            best[d["telegram_id"]] = d
        else:
            attempts = cur["attempts"] + 1
            if d["score"] > cur["score"]:
                d["attempts"] = attempts
                best[d["telegram_id"]] = d
            else:
                cur["attempts"] = attempts
    return sorted(best.values(), key=lambda d: (-d["score"], d["created_at"]))


# ---------------------------------------------------------------- foydalanuvchilar
def register_user(user) -> bool:
    """Telegram ma'lumotlari bilan foydalanuvchini ro'yxatga oladi yoki yangilaydi.

    Yangi foydalanuvchi bo'lsa True qaytaradi.
    """
    now = _now()
    with _connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM users WHERE telegram_id = ?", (user.id,)
        ).fetchone()
        conn.execute(
            """
            INSERT INTO users (telegram_id, username, first_name, last_name,
                               language_code, is_premium, registered_at, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username      = excluded.username,
                first_name    = excluded.first_name,
                last_name     = excluded.last_name,
                language_code = excluded.language_code,
                is_premium    = excluded.is_premium,
                last_seen     = excluded.last_seen,
                bot_blocked   = 0
            """,
            (
                user.id, user.username, user.first_name, user.last_name,
                user.language_code, int(bool(user.is_premium)), now, now,
            ),
        )
    return exists is None


def count_users() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def latest_users(limit: int = 5) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM users ORDER BY registered_at DESC LIMIT ?", (limit,)
        ).fetchall()


def all_users() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            """SELECT u.*, (SELECT COUNT(*) FROM results r WHERE r.telegram_id = u.telegram_id) AS attempts,
                      (SELECT COUNT(DISTINCT test_id) FROM results r WHERE r.telegram_id = u.telegram_id) AS tests
               FROM users u ORDER BY u.registered_at"""
        ).fetchall()


def stats() -> dict:
    from timeutil import now
    today = now().date().isoformat()
    week = (now() - timedelta(days=7)).isoformat(timespec="seconds")
    with _connect() as conn:
        one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
        return {
            "users": one("SELECT COUNT(*) FROM users"),
            "banned": one("SELECT COUNT(*) FROM users WHERE banned = 1"),
            "no_group": one("SELECT COUNT(*) FROM users WHERE group_id IS NULL"),
            "bot_blocked": one("SELECT COUNT(*) FROM users WHERE bot_blocked = 1"),
            "users_today": one("SELECT COUNT(*) FROM users WHERE registered_at >= ?", today),
            "users_week": one("SELECT COUNT(*) FROM users WHERE registered_at >= ?", week),
            "active_today": one("SELECT COUNT(*) FROM users WHERE last_seen >= ?", today),
            "active_week": one("SELECT COUNT(*) FROM users WHERE last_seen >= ?", week),
            "results": one("SELECT COUNT(*) FROM results"),
            "results_today": one("SELECT COUNT(*) FROM results WHERE created_at >= ?", today),
            "results_week": one("SELECT COUNT(*) FROM results WHERE created_at >= ?", week),
            "solvers": one("SELECT COUNT(DISTINCT telegram_id) FROM results"),
            "avg_percent": one("SELECT COALESCE(AVG(score * 100.0 / max_score), 0) FROM results WHERE max_score > 0"),
            "tests": one("SELECT COUNT(*) FROM tests"),
            "tests_active": one("SELECT COUNT(*) FROM tests WHERE active = 1"),
            "per_test": conn.execute(
                """SELECT t.title, COUNT(r.id) AS n, COUNT(DISTINCT r.telegram_id) AS people,
                          COALESCE(AVG(r.score * 100.0 / r.max_score), 0) AS avg
                   FROM tests t LEFT JOIN results r ON r.test_id = t.id
                   GROUP BY t.id ORDER BY n DESC"""
            ).fetchall(),
        }


def tests_to_reveal(now_iso: str) -> list[str]:
    with _connect() as conn:
        return [r[0] for r in conn.execute(
            "SELECT id FROM tests WHERE show_mode = 'after' AND ends_at IS NOT NULL "
            "AND ends_at <= ? AND revealed = 0", (now_iso,))]


def set_revealed(test_id: str, value: int = 1) -> None:
    with _connect() as conn:
        conn.execute("UPDATE tests SET revealed = ? WHERE id = ?", (value, test_id))


def has_result(telegram_id: int, test_id: str) -> bool:
    return attempts_count(telegram_id, test_id) > 0


# ---------------------------------------------------------------- bloklash, qidiruv, rassilka
def user_row(telegram_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()


def is_banned(telegram_id: int) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT banned FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        return bool(row and row[0])


def set_banned(telegram_id: int, value: bool) -> None:
    with _connect() as conn:
        conn.execute("UPDATE users SET banned = ? WHERE telegram_id = ?", (int(value), telegram_id))


def set_bot_blocked(telegram_id: int, value: bool) -> None:
    with _connect() as conn:
        conn.execute("UPDATE users SET bot_blocked = ? WHERE telegram_id = ?", (int(value), telegram_id))


def find_users(query: str, limit: int = 10) -> list[sqlite3.Row]:
    q = query.strip().lstrip("@")
    with _connect() as conn:
        if q.isdigit():
            rows = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (int(q),)).fetchall()
            if rows:
                return rows
        like = f"%{q.lower()}%"
        return conn.execute(
            """SELECT * FROM users
               WHERE lower(COALESCE(username, '')) LIKE ?
                  OR lower(COALESCE(first_name, '') || ' ' || COALESCE(last_name, '')) LIKE ?
               ORDER BY last_seen DESC LIMIT ?""", (like, like, limit)).fetchall()


def users_where(sql: str = "1", limit: int = 10) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(f"SELECT * FROM users WHERE {sql} ORDER BY last_seen DESC LIMIT ?", (limit,)).fetchall()


def user_best_results(telegram_id: int) -> list[dict]:
    """Foydalanuvchining har bir test bo'yicha eng yaxshi natijasi."""
    best: dict[str, dict] = {}
    with _connect() as conn:
        for row in conn.execute("SELECT * FROM results WHERE telegram_id = ? ORDER BY id", (telegram_id,)):
            d = dict(row)
            cur = best.get(d["test_id"])
            n = (cur["attempts"] if cur else 0) + 1
            if cur is None or d["score"] > cur["score"]:
                best[d["test_id"]] = d
            best[d["test_id"]]["attempts"] = n
    return list(best.values())


def recipients(audience: str, test_id: str | None = None, group_id: int | None = None) -> list[int]:
    """audience: all | solved | unsolved | group. Admin bloklaganlarga hech qachon yuborilmaydi."""
    with _connect() as conn:
        if audience == "group":
            return [r[0] for r in conn.execute(
                "SELECT telegram_id FROM users WHERE banned = 0 AND group_id = ?", (group_id,))]
        if audience == "solved":
            sql = ("SELECT u.telegram_id FROM users u WHERE u.banned = 0 AND EXISTS "
                   "(SELECT 1 FROM results r WHERE r.telegram_id = u.telegram_id AND r.test_id = ?)")
            return [r[0] for r in conn.execute(sql, (test_id,))]
        if audience == "unsolved":
            sql = ("SELECT u.telegram_id FROM users u WHERE u.banned = 0 AND NOT EXISTS "
                   "(SELECT 1 FROM results r WHERE r.telegram_id = u.telegram_id AND r.test_id = ?)")
            return [r[0] for r in conn.execute(sql, (test_id,))]
        return [r[0] for r in conn.execute("SELECT telegram_id FROM users WHERE banned = 0")]


# ---------------------------------------------------------------- guruhlar (sinflar)
def groups() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            """SELECT g.*, (SELECT COUNT(*) FROM users u WHERE u.group_id = g.id) AS members
               FROM groups g ORDER BY g.name""").fetchall()


def group(group_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute(
            """SELECT g.*, (SELECT COUNT(*) FROM users u WHERE u.group_id = g.id) AS members
               FROM groups g WHERE g.id = ?""", (group_id,)).fetchone()


def group_by_code(code: str) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute("SELECT * FROM groups WHERE code = ?", (code,)).fetchone()


def group_create(name: str, code: str) -> int:
    with _connect() as conn:
        return conn.execute("INSERT INTO groups (name, code, created_at) VALUES (?, ?, ?)",
                            (name, code, _now())).lastrowid


def group_update(group_id: int, **fields) -> None:
    with _connect() as conn:
        for k, v in fields.items():
            if k in ("name", "code"):
                conn.execute(f"UPDATE groups SET {k} = ? WHERE id = ?", (v, group_id))


def group_delete(group_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE users SET group_id = NULL WHERE group_id = ?", (group_id,))
        conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))


def set_user_group(telegram_id: int, group_id: int | None) -> None:
    with _connect() as conn:
        conn.execute("UPDATE users SET group_id = ? WHERE telegram_id = ?", (group_id, telegram_id))


def user_group(telegram_id: int) -> int | None:
    with _connect() as conn:
        row = conn.execute("SELECT group_id FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        return row[0] if row else None


def users_in_group(group_id: int, limit: int = 100) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute("SELECT * FROM users WHERE group_id = ? ORDER BY first_name LIMIT ?",
                            (group_id, limit)).fetchall()


# ---------------------------------------------------------------- yordamchi adminlar
def staff() -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            """SELECT s.*, u.first_name, u.last_name, u.username FROM staff s
               LEFT JOIN users u ON u.telegram_id = s.telegram_id ORDER BY s.added_at""").fetchall()


def staff_role(telegram_id: int) -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT role FROM staff WHERE telegram_id = ?", (telegram_id,)).fetchone()
        return row[0] if row else None


def staff_add(telegram_id: int, added_by: int, role: str = "helper") -> None:
    with _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO staff (telegram_id, role, added_by, added_at) VALUES (?, ?, ?, ?)",
                     (telegram_id, role, added_by, _now()))


def staff_remove(telegram_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM staff WHERE telegram_id = ?", (telegram_id,))


# ---------------------------------------------------------------- harakatlar jurnali
def audit(admin_id: int | None, action: str, details: str = "") -> None:
    with _connect() as conn:
        conn.execute("INSERT INTO audit (at, admin_id, action, details) VALUES (?, ?, ?, ?)",
                     (_now(), admin_id, action, details[:500]))


def audit_rows(limit: int = 25) -> list[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            """SELECT a.*, u.first_name, u.username FROM audit a
               LEFT JOIN users u ON u.telegram_id = a.admin_id ORDER BY a.id DESC LIMIT ?""",
            (limit,)).fetchall()


def storage_add(test_id: str, kind: str, ref: str, chat_id: int, message_id: int) -> None:
    with _connect() as conn:
        conn.execute("INSERT INTO storage_posts (test_id, kind, ref, chat_id, message_id, at) VALUES (?, ?, ?, ?, ?, ?)",
                     (test_id, kind, ref, chat_id, message_id, _now()))


def storage_find(test_id: str, kind: str, ref: str = "") -> sqlite3.Row | None:
    """Shu fayl kanalga joylangan oxirgi post."""
    with _connect() as conn:
        return conn.execute("SELECT * FROM storage_posts WHERE test_id = ? AND kind = ? AND ref = ? "
                            "ORDER BY id DESC LIMIT 1", (test_id, kind, ref)).fetchone()


def storage_archived_tests(chat_id: int) -> set[str]:
    with _connect() as conn:
        return {r[0] for r in conn.execute(
            "SELECT DISTINCT test_id FROM storage_posts WHERE kind = 'header' AND chat_id = ?", (chat_id,))}


def storage_count(chat_id: int) -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM storage_posts WHERE chat_id = ?", (chat_id,)).fetchone()[0]


def cert_create(telegram_id: int, test_id: str, result_id: int | None, name: str, grade: str) -> sqlite3.Row:
    """Yangi sertifikat: raqami AY-<yil>-<6 xonali tartib raqam>."""
    now = _now()
    with _connect() as conn:
        cid = conn.execute("INSERT INTO certificates (telegram_id, test_id, result_id, name, grade, issued_at) "
                           "VALUES (?, ?, ?, ?, ?, ?)", (telegram_id, test_id, result_id, name, grade, now)).lastrowid
        code = f"AY-{now[:4]}-{cid:06d}"
        conn.execute("UPDATE certificates SET code = ? WHERE id = ?", (code, cid))
        return conn.execute("SELECT * FROM certificates WHERE id = ?", (cid,)).fetchone()


def cert_get(code: str) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute("SELECT * FROM certificates WHERE code = ?", (code.upper(),)).fetchone()


def cert_for_result(result_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute("SELECT * FROM certificates WHERE result_id = ?", (result_id,)).fetchone()


def cert_rename(code: str, name: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE certificates SET name = ? WHERE code = ?", (name, code))


def cert_count() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM certificates").fetchone()[0]


def set_cert_name(telegram_id: int, name: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE users SET cert_name = ? WHERE telegram_id = ?", (name, telegram_id))


def backup_to(path) -> None:
    """Ishlab turgan bazaning to'liq nusxasi (SQLite backup API — xavfsiz)."""
    src = sqlite3.connect(config.DB_PATH)
    dst = sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


# ---------------------------------------------------------------- fayl keshi
def get_file_id(path: str) -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT file_id FROM media_cache WHERE path = ?", (path,)).fetchone()
        return row[0] if row else None


def set_file_id(path: str, file_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO media_cache (path, file_id) VALUES (?, ?) "
            "ON CONFLICT(path) DO UPDATE SET file_id = excluded.file_id",
            (path, file_id),
        )
