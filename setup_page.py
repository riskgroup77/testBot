"""Token kiritish sahifasi: http://127.0.0.1:8765 (faqat shu kompyuterda ochiladi)."""
import logging
from html import escape

from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.utils.token import TokenValidationError
from aiohttp import web

import database
from config import BOT_NAME, SETUP_PORT, TOKEN_RE, load_token, mask_token, save_token

log = logging.getLogger("setup")

ALLOWED_ORIGINS = (f"http://127.0.0.1:{SETUP_PORT}", f"http://localhost:{SETUP_PORT}")

ICON = {
    "bot": '<path d="M12 8V4H8"/><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/>',
    "key": '<circle cx="7.5" cy="15.5" r="4.5"/><path d="m10.7 12.3 9.3-9.3M17 6l3 3"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "alert": '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
    "eye": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    "send": '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
    "lock": '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
}


def ic(name: str, size: int = 20) -> str:
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICON[name]}</svg>')


PAGE = """<!doctype html>
<html lang="uz"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bot sozlamalari</title>
<style>
:root{--bg:#F4F6FA;--card:#fff;--text:#1B2430;--muted:#5A6577;--p:#1E3A5F;--a:#E8A33D;--b:#E3E8F0;--b2:#C9D2E0;
--ok:#1F7A4D;--oks:#E6F4EC;--bad:#B42318;--bads:#FDECEA}
@media (prefers-color-scheme:dark){:root{--bg:#10161F;--card:#18212D;--text:#EEF2F7;--muted:#A3AFBF;--p:#8DB4E8;--b:#26323F;--b2:#3A4757;
--ok:#7BD389;--oks:rgba(123,211,137,.14);--bad:#F28B82;--bads:rgba(242,139,130,.14)}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:40px 16px}
.wrap{max-width:560px;margin:0 auto;display:flex;flex-direction:column;gap:18px}
.card{background:var(--card);border:1px solid var(--b);border-radius:16px;padding:28px}
.head{display:flex;align-items:center;gap:14px}
.logo{width:48px;height:48px;border-radius:12px;background:var(--a);color:#1E3A5F;display:flex;align-items:center;justify-content:center;flex:none}
h1{margin:0;font-size:22px}
.muted{color:var(--muted)}
.status{display:flex;gap:12px;align-items:flex-start;border-radius:12px;padding:14px 16px;margin-top:20px}
.status.ok{background:var(--oks);color:var(--ok)}
.status.bad{background:var(--bads);color:var(--bad)}
.status b{color:var(--text)}
label{display:block;font-weight:600;margin:22px 0 8px}
.field{display:flex;gap:8px}
input{flex:1;min-width:0;font:inherit;font-family:ui-monospace,Consolas,monospace;font-size:15px;padding:13px 14px;border:1px solid var(--b2);border-radius:10px;background:var(--card);color:var(--text)}
input:focus{outline:2px solid var(--p);outline-offset:1px}
button,.btn{font:inherit;font-weight:700;border:0;border-radius:10px;padding:13px 18px;cursor:pointer;display:inline-flex;gap:8px;align-items:center;justify-content:center;text-decoration:none}
.primary{background:#1E3A5F;color:#fff;width:100%;margin-top:18px;font-size:16px}
.ghost{background:transparent;border:1px solid var(--b2);color:var(--text);padding:0 14px}
.tg{background:#229ED9;color:#fff}
ol{margin:10px 0 0;padding-left:20px;color:var(--muted)}
ol li{margin:6px 0}
code{background:var(--bg);padding:2px 6px;border-radius:6px}
table{width:100%;border-collapse:collapse;font-size:14px;margin-top:12px}
th{text-align:left;color:var(--muted);font-weight:600;padding:8px 6px;border-bottom:1px solid var(--b)}
td{padding:9px 6px;border-bottom:1px solid var(--b)}
.note{display:flex;gap:8px;align-items:center;justify-content:center;font-size:14px}
</style></head><body><div class="wrap">
<div class="card">
  <div class="head"><div class="logo">{logo}</div><div><h1>{bot_name} — sozlamalar</h1><div class="muted">Bot tokenini kiriting, saqlang — bot o‘zi ishga tushadi.</div></div></div>
  {status}
  <form method="post" action="/save" autocomplete="off">
    <label for="token">{key_icon} Bot tokeni</label>
    <div class="field">
      <input id="token" name="token" type="password" required placeholder="1234567890:AAH...">
      <button type="button" class="ghost" aria-label="Tokenni ko‘rsatish" onclick="var i=document.getElementById('token');i.type=i.type==='password'?'text':'password'">{eye_icon}</button>
    </div>
    <button type="submit" class="primary">{check_icon} Saqlash va botni ishga tushirish</button>
  </form>
  <ol>
    <li>Telegram’da <b>@BotFather</b> ga kiring.</li>
    <li><code>/newbot</code> buyrug‘i bilan bot yarating (yoki <code>/mybots</code> dan mavjudini tanlang).</li>
    <li>Berilgan tokenni nusxa olib, yuqoriga qo‘ying.</li>
  </ol>
</div>
{users}
<div class="note muted">{lock_icon} Token faqat shu kompyuterdagi <code>bot/.env</code> faylida saqlanadi.</div>
</div></body></html>"""


def render(manager, message: str | None = None, ok: bool = True) -> str:
    if message:
        status = f'<div class="status {"ok" if ok else "bad"}">{ic("check" if ok else "alert", 22)}<div>{message}</div></div>'
    elif manager.running and manager.me:
        u = escape(manager.me.username or "")
        status = (f'<div class="status ok">{ic("check", 22)}<div><b>Bot ishlayapti: @{u}</b><br>'
                  f'Saqlangan token: <code>{escape(mask_token(load_token()))}</code><br>'
                  f'<a class="btn tg" style="margin-top:10px;padding:9px 14px" href="https://t.me/{u}" target="_blank" rel="noopener">{ic("send", 18)} Botni ochish</a></div></div>')
    elif manager.error:
        status = f'<div class="status bad">{ic("alert", 22)}<div><b>Bot to‘xtadi</b><br>{escape(manager.error)}</div></div>'
    else:
        status = f'<div class="status bad">{ic("alert", 22)}<div><b>Token hali kiritilmagan</b><br>Pastdagi maydonga BotFather bergan tokenni qo‘ying.</div></div>'

    import access
    if access.configured():
        admin_html = ("<b>Admin:</b> <code>.env</code> faylidagi <code>ADMIN_USERNAME</code> bo‘yicha. "
                      "Telegram’da <code>/admin</code> yozib panelni ochadi.")
    elif (demo := access.demo_owner()):
        u = database.user_row(demo)
        name = escape(((u["first_name"] or "") + " " + (u["last_name"] or "")).strip() if u else str(demo))
        uname = f" (@{escape(u['username'])})" if u and u["username"] else ""
        admin_html = (f"<b>Admin (sinov rejimi):</b> {name}{uname}<br>"
                      "<span class='muted'>Boshqa hech kim <code>/admin</code> orqali admin bo‘la olmaydi. "
                      "<code>.env</code> ga <code>ADMIN_USERNAME</code> yozilganda sinov rejimi o‘zi tugaydi.</span>"
                      '<form method="post" action="/reset_admin" style="margin-top:10px">'
                      '<button type="submit" class="ghost" style="padding:9px 14px">Adminlikni bekor qilish</button></form>')
    else:
        admin_html = ("<b>Admin hali yo‘q.</b> Telegram’da botga <code>/admin</code> deb yozing — "
                      "<b>birinchi yozgan odam</b> admin bo‘ladi.")
    admin_card = f'<div class="card"><div class="head" style="gap:10px">{ic("lock")}<div>{admin_html}</div></div></div>'

    rows = database.latest_users(5)
    if rows:
        body = "".join(
            f"<tr><td>{escape(((r['first_name'] or '') + ' ' + (r['last_name'] or '')).strip())}</td>"
            f"<td>{'@' + escape(r['username']) if r['username'] else '—'}</td>"
            f"<td>{escape(r['registered_at'].replace('T', ' '))}</td></tr>"
            for r in rows
        )
        users = (f'<div class="card"><div class="head" style="gap:10px">{ic("users")}<b>Ro‘yxatdan o‘tganlar: {database.count_users()} ta</b></div>'
                 f'<table><thead><tr><th>Ism</th><th>Username</th><th>Sana</th></tr></thead><tbody>{body}</tbody></table></div>')
    else:
        users = ""

    res = database.latest_results(10)
    if res:
        import content
        from grading import fmt_num
        TESTS = {t.id: t for t in content.all_tests()}
        body = "".join(
            f"<tr><td>{escape(((r['first_name'] or '') + ' ' + (r['last_name'] or '')).strip() or str(r['telegram_id']))}</td>"
            f"<td>{escape(TESTS[r['test_id']].title if r['test_id'] in TESTS else r['test_id'])}</td>"
            f"<td><b>{fmt_num(r['score'])} / {fmt_num(r['max_score'])}</b><br><span class='muted'>✅ {r['correct']} ❌ {r['wrong']}" + (f" (javobsiz {r['empty']})" if r['empty'] else "") + "</span></td>"
            f"<td>{escape(r['created_at'].replace('T', ' '))}</td></tr>"
            for r in res
        )
        users += (f'<div class="card"><div class="head" style="gap:10px">{ic("check")}<b>Oxirgi test natijalari</b></div>'
                  f'<table><thead><tr><th>O‘quvchi</th><th>Test</th><th>Ball</th><th>Sana</th></tr></thead><tbody>{body}</tbody></table></div>')

    return (PAGE.replace("{logo}", ic("bot", 26)).replace("{bot_name}", escape(BOT_NAME))
            .replace("{status}", status).replace("{users}", admin_card + users)
            .replace("{key_icon}", ic("key", 18)).replace("{eye_icon}", ic("eye", 18))
            .replace("{check_icon}", ic("check", 18)).replace("{lock_icon}", ic("lock", 16)))


def create_app(manager) -> web.Application:
    async def index(request: web.Request) -> web.Response:
        return web.Response(text=render(manager), content_type="text/html")

    async def save(request: web.Request) -> web.Response:
        origin = request.headers.get("Origin") or request.headers.get("Referer") or ""
        if origin and not origin.startswith(ALLOWED_ORIGINS):
            raise web.HTTPForbidden(text="Ruxsat yo'q")
        token = ((await request.post()).get("token") or "").strip()
        if not TOKEN_RE.match(token):
            return web.Response(text=render(manager, "<b>Token formati noto‘g‘ri.</b><br>U <code>1234567890:AAH...</code> ko‘rinishida bo‘lishi kerak.", ok=False), content_type="text/html")
        try:
            me = await manager.start(token)
        except TokenValidationError:
            return web.Response(text=render(manager, "<b>Token formati noto‘g‘ri.</b>", ok=False), content_type="text/html")
        except TelegramUnauthorizedError:
            return web.Response(text=render(manager, "<b>Telegram bu tokenni tanimadi.</b><br>Token noto‘g‘ri yoki BotFather’da yangilangan bo‘lishi mumkin — qaytadan nusxa olib qo‘ying.", ok=False), content_type="text/html")
        except TelegramNetworkError:
            return web.Response(text=render(manager, "<b>Telegram’ga ulanib bo‘lmadi.</b><br>Internet aloqasini tekshirib, qayta urinib ko‘ring.", ok=False), content_type="text/html")
        except Exception as exc:
            log.warning("Token tekshiruvidan o'tmadi: %s", exc)
            return web.Response(text=render(manager, f"<b>Telegram tokenni qabul qilmadi.</b><br>{escape(str(exc))}", ok=False), content_type="text/html")
        save_token(token)
        u = escape(me.username or "")
        msg = (f"<b>Token saqlandi va bot ishga tushdi: @{u}</b><br>Endi Telegram’da botga kirib <code>/start</code> bosing."
               f'<br><a class="btn tg" style="margin-top:10px;padding:9px 14px" href="https://t.me/{u}" target="_blank" rel="noopener">{ic("send", 18)} Botni ochish</a>')
        return web.Response(text=render(manager, msg, ok=True), content_type="text/html")

    async def reset_admin(request: web.Request) -> web.Response:
        origin = request.headers.get("Origin") or request.headers.get("Referer") or ""
        if origin and not origin.startswith(ALLOWED_ORIGINS):
            raise web.HTTPForbidden(text="Ruxsat yo'q")
        import access
        access.reset_demo()
        log.info("Sinov rejimidagi adminlik bekor qilindi (sozlash sahifasidan)")
        return web.Response(text=render(manager, "<b>Adminlik bekor qilindi.</b><br>Endi Telegram’da birinchi "
                                                 "<code>/admin</code> yozgan odam admin bo‘ladi.", ok=True),
                            content_type="text/html")

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_post("/save", save)
    app.router.add_post("/reset_admin", reset_admin)
    return app
