"""온점핑 출석 — 서버 렌더링 화면 (WSGI).

로컬 server.py 와 Vercel api/index.py 가 같은 `app` 을 쓴다. 자바스크립트 없이
폼 전송 → 리다이렉트 → 플래시 메시지로 동작하고, 화면은 페이지별로 나뉜다.

    /                 오늘 출석 (로그인 필요)
    /notices          공지
    /auto             평일 자동 출석 · 내 정보
    /login            로그인
    /admin            관리자: 오늘 출석 명단 · 휴강
    /admin/notices    관리자: 공지 관리
    /admin/users      관리자: 회원 관리
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import urllib.parse
from datetime import date
from http.cookies import SimpleCookie

import store

COOKIE = "onjumping_uid"
FLASH = "onjumping_flash"
SESSION_MAX_AGE = 60 * 60 * 24 * 30
SECURE_COOKIE = bool(os.environ.get("VERCEL"))

REASONS = {
    200: "OK", 201: "Created", 303: "See Other", 400: "Bad Request",
    401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 500: "Internal Server Error",
}


# --------------------------------------------------------------------------- #
# 스타일
# --------------------------------------------------------------------------- #

CSS = """
* { box-sizing: border-box; }

body {
    margin: 0;
    font-family: Pretendard, 'Noto Sans KR', -apple-system, BlinkMacSystemFont,
                 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
    background: #f3f5f9;
    color: #222;
    -webkit-text-size-adjust: 100%;
}

/* 상단 헤더 + 탭 */
.topbar { background: #5865f2; color: #fff; }
.topbar-inner {
    max-width: 980px;
    margin: 0 auto;
    padding: 12px 20px 10px;
    display: flex;
    align-items: center;
    gap: 14px;
    flex-wrap: wrap;
}
.brand {
    color: #fff;
    text-decoration: none;
    font-size: 19px;
    font-weight: 700;
    letter-spacing: -0.3px;
    white-space: nowrap;
}
.tabs { display: flex; gap: 4px; flex-wrap: wrap; flex: 1 1 auto; min-width: 0; }
.tab {
    color: #e3e6ff;
    text-decoration: none;
    padding: 7px 12px;
    border-radius: 999px;
    font-size: 14px;
    white-space: nowrap;
}
.tab:hover { background: rgba(255, 255, 255, 0.14); }
.tab.active { background: #fff; color: #5865f2; font-weight: 700; }
.tab.admin { border: 1px solid rgba(255, 255, 255, 0.45); }
.who {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 14px;
    color: #e3e6ff;
    white-space: nowrap;
}
.who a { color: #fff; text-decoration: none; opacity: 0.9; }
.who a:hover { opacity: 1; text-decoration: underline; }

.container { max-width: 720px; margin: 26px auto 48px; padding: 0 18px; }
.container.wide { max-width: 980px; }

.page-head { margin: 0 4px 16px; }
.page-head h1 { margin: 0; font-size: 24px; letter-spacing: -0.4px; }
.page-head p { margin: 6px 0 0; color: #6b7180; font-size: 14px; }

.card {
    background: #fff;
    border-radius: 16px;
    padding: 20px 22px;
    margin-bottom: 16px;
    box-shadow: 0 3px 12px rgba(0, 0, 0, 0.06);
}
.card > h2 { margin: 0 0 14px; font-size: 18px; letter-spacing: -0.3px; }
.card > h2:last-child { margin-bottom: 0; }

.slots { display: grid; grid-template-columns: repeat(auto-fit, minmax(215px, 1fr)); gap: 14px; }
.slots .card { margin-bottom: 0; }

h1, h2, h3 { font-weight: 700; }

input, select, textarea {
    width: 100%;
    padding: 12px 13px;
    margin: 6px 0;
    border: 1px solid #dde1e9;
    border-radius: 9px;
    font-size: 16px;
    font-family: inherit;
    background: #fff;
    color: #222;
}
input:focus, select:focus, textarea:focus { outline: 2px solid #c3c9ff; border-color: #5865f2; }
textarea { min-height: 104px; resize: vertical; line-height: 1.55; }

input[type=checkbox] { width: 22px; height: 22px; margin: 0; accent-color: #5865f2; flex: 0 0 auto; }

button, .button {
    display: inline-block;
    border: none;
    background: #5865f2;
    color: #fff;
    padding: 11px 16px;
    border-radius: 9px;
    font-size: 15px;
    font-family: inherit;
    cursor: pointer;
    text-decoration: none;
    text-align: center;
}
button:hover, .button:hover { filter: brightness(0.94); }
button:disabled { background: #c7cbd6; cursor: not-allowed; }
button.small, .button.small { padding: 8px 12px; font-size: 14px; border-radius: 8px; }
button.soft, .button.soft { background: #eef0ff; color: #5865f2; }
button.soft:hover, .button.soft:hover { background: #e3e7ff; }
button.danger { background: #e74c3c; }
button.ghost { background: #fff; color: #c0392b; border: 1px solid #f0c4bd; }
button.gray { background: #eef0f4; color: #4a4f5c; }
.wide { width: 100%; display: block; }

.form-row { display: flex; gap: 8px; align-items: center; margin-top: 10px; }
.form-row input { margin: 0; }
.form-row button { flex: 0 0 auto; }

.badge {
    display: inline-block;
    background: #e8ebff;
    color: #5865f2;
    padding: 4px 10px;
    border-radius: 20px;
    font-size: 13px;
    font-weight: 600;
    vertical-align: middle;
    white-space: nowrap;
}
.badge.off { background: #f0f1f5; color: #8a8f9c; }
.badge.crown { background: #fff4d6; color: #b7791f; }
.badge.today { background: #e6f7ee; color: #1e8f5b; }

.muted { color: #7b818e; font-size: 13px; }
.small { color: #777; font-size: 13px; margin: 8px 0 0; }

.message {
    padding: 12px 14px;
    background: #eef0ff;
    color: #3b43b8;
    border-radius: 10px;
    margin-bottom: 16px;
    font-size: 14px;
}
.message.error { background: #fdecea; color: #c0392b; }

.banner {
    padding: 13px 16px;
    background: #fdecea;
    color: #c0392b;
    border-radius: 12px;
    margin-bottom: 16px;
    font-weight: 700;
    text-align: center;
    font-size: 15px;
}

/* 타임 카드 */
.slot-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.slot-title { display: flex; align-items: baseline; gap: 7px; margin-right: auto; }
.slot-title b { font-size: 19px; letter-spacing: -0.3px; }
.slot-title .name { color: #8a8f9c; font-size: 13px; }
.people { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 14px; }
.chip {
    background: #f4f5f9;
    color: #4a4f5c;
    border-radius: 999px;
    padding: 5px 11px;
    font-size: 13px;
}
.chip.mine { background: #e8ebff; color: #434dc0; font-weight: 700; }

/* 목록 */
.list-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 0;
    border-bottom: 1px solid #eef0f4;
}
.list-row:last-child { border-bottom: none; padding-bottom: 0; }
.list-row:first-child { padding-top: 0; }
.list-main { min-width: 0; flex: 1 1 auto; }
.list-main b { font-size: 15px; }
.list-main .meta { display: block; color: #8a8f9c; font-size: 13px; margin-top: 3px; }
.list-row form { flex: 0 0 auto; margin: 0; }

.notice-body { margin: 0; line-height: 1.65; white-space: pre-line; font-size: 15px; }
.notice-card h3 { margin: 0 0 4px; font-size: 15px; }
.notice-card .meta { color: #8a8f9c; font-size: 13px; margin: 0 0 10px; }

.week-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px; }
.week-grid .day { font-size: 13px; font-weight: 700; color: #555; text-align: center; }
.week-grid select { padding: 11px 2px; margin: 4px 0 0; font-size: 14px; text-align: center; }

.toggle-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 11px 0;
    border-bottom: 1px solid #eef0f4;
}
.toggle-row:last-of-type { border-bottom: none; }

/* 로그인 */
.login-wrap { max-width: 380px; margin: 8vh auto 0; padding: 0 18px; }
.login-wrap .card { padding: 26px 24px; }
.login-wrap h1 { margin: 0 0 4px; font-size: 22px; letter-spacing: -0.4px; }
.login-wrap p { margin: 0 0 18px; color: #7b818e; font-size: 14px; }

.empty { color: #8a8f9c; font-size: 14px; margin: 10px 0 0; }

@media(max-width: 560px) {
    .topbar-inner { padding: 10px 14px 8px; gap: 8px; }
    .brand { font-size: 17px; }
    .tabs { gap: 2px; }
    .tab { padding: 6px 10px; font-size: 13px; }
    .who { font-size: 13px; }
    .container { padding: 0 12px; margin: 18px auto 36px; }
    .card { padding: 16px; border-radius: 14px; }
    .slots { grid-template-columns: 1fr; }
    .week-grid { gap: 4px; }
    .week-grid select { font-size: 13px; padding: 10px 1px; }
    .week-grid .day { font-size: 12px; }
    .page-head h1 { font-size: 21px; }
}
"""


# --------------------------------------------------------------------------- #
# 응답 / 쿠키
# --------------------------------------------------------------------------- #

class Response:
    def __init__(self, body: str = "", status: int = 200, ctype: str = "text/html; charset=utf-8"):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.ctype = ctype
        self.headers: list[tuple[str, str]] = []
        self.cookies: dict[str, tuple[str, int]] = {}
        self.flash_set = False

    def header(self, name: str, value: str) -> "Response":
        self.headers.append((name, value))
        return self

    def set_cookie(self, name: str, value: str, max_age: int = SESSION_MAX_AGE) -> "Response":
        self.cookies[name] = (value, max_age)
        return self

    def clear_cookie(self, name: str) -> "Response":
        self.cookies[name] = ("", 0)
        return self

    def set_flash(self, message: str, error: bool = False) -> "Response":
        payload = ("!" if error else "") + message
        self.cookies[FLASH] = (urllib.parse.quote(payload), 60)
        self.flash_set = True
        return self

    def __call__(self, environ, start_response):
        headers = [
            ("Content-Type", self.ctype),
            ("Content-Length", str(len(self.body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "same-origin"),
        ]
        headers.extend(self.headers)
        for name, (value, max_age) in self.cookies.items():
            attrs = f"{name}={value}; Path=/; Max-Age={max_age}; SameSite=Lax"
            if name != FLASH:
                attrs += "; HttpOnly"
            if SECURE_COOKIE:
                attrs += "; Secure"
            headers.append(("Set-Cookie", attrs))
        reason = REASONS.get(self.status, "OK")
        start_response(f"{self.status} {reason}", headers)
        return [self.body]


def redirect(location: str, flash: str | None = None, error: bool = False) -> Response:
    resp = Response("", 303).header("Location", location)
    if flash:
        resp.set_flash(flash, error)
    return resp


def esc(value) -> str:
    return (str(value)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def form_value(form: dict[str, list[str]], key: str, default: str = "") -> str:
    values = form.get(key) or []
    return values[0].strip() if values else default


# --------------------------------------------------------------------------- #
# 세션
# --------------------------------------------------------------------------- #

def _sign(value: str) -> str:
    return hmac.new(store.get_secret().encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def session_cookie(user_id: int) -> str:
    raw = str(user_id)
    return f"{raw}.{_sign(raw)}"


def parse_session(environ) -> int | None:
    header = environ.get("HTTP_COOKIE", "")
    if not header:
        return None
    try:
        morsel = SimpleCookie(header).get(COOKIE)
    except Exception:
        return None
    if not morsel or "." not in morsel.value:
        return None
    raw, signature = morsel.value.split(".", 1)
    if not raw.isdigit() or not hmac.compare_digest(_sign(raw), signature):
        return None
    return int(raw)


def read_flash(environ) -> tuple[str, bool]:
    header = environ.get("HTTP_COOKIE", "")
    if not header:
        return "", False
    try:
        morsel = SimpleCookie(header).get(FLASH)
    except Exception:
        return "", False
    if not morsel or not morsel.value:
        return "", False
    message = urllib.parse.unquote(morsel.value)
    if message.startswith("!"):
        return message[1:], True
    return message, False


def read_form(environ) -> dict[str, list[str]]:
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if not length:
        return {}
    raw = environ["wsgi.input"].read(length).decode("utf-8", "replace")
    return urllib.parse.parse_qs(raw, keep_blank_values=True)


# --------------------------------------------------------------------------- #
# 공통 HTML
# --------------------------------------------------------------------------- #

MEMBER_TABS = [
    ("checkin", "/checkin", "출석하기"),
    ("notices", "/notices", "공지"),
    ("auto", "/auto", "자동출석"),
]
ADMIN_TABS = [
    ("admin", "/admin", "출석·휴강"),
    ("admin-notices", "/admin/notices", "공지 관리"),
    ("admin-users", "/admin/users", "회원"),
    ("admin-users-new", "/admin/users/new", "회원 추가"),
]


def header(user: dict | None, active: str | None) -> str:
    tabs = []
    for key, href, label in MEMBER_TABS:
        cls = "tab active" if key == active else "tab"
        tabs.append(f'<a class="{cls}" href="{href}">{label}</a>')
    if user and user["is_admin"]:
        for key, href, label in ADMIN_TABS:
            cls = "tab admin active" if key == active else "tab admin"
            tabs.append(f'<a class="{cls}" href="{href}">{label}</a>')

    who = ""
    if user:
        who = (
            f'<div class="who"><span>{esc(user["nickname"])}님</span>'
            f'<a href="/logout">로그아웃</a></div>'
        )
    return (
        '<header class="topbar"><div class="topbar-inner">'
        '<a class="brand" href="/">🏃 On점핑 출석</a>'
        f'<nav class="tabs">{"".join(tabs)}</nav>'
        f'{who}'
        "</div></header>"
    )


def page(content: str, *, title: str = "On점핑 출석", user: dict | None = None,
         active: str | None = None, flash: str = "", error: bool = False, wide: bool = False) -> str:
    flash_html = ""
    if flash:
        flash_html = f'<div class="message{" error" if error else ""}">{esc(flash)}</div>'
    container = 'class="container wide"' if wide else 'class="container"'
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ko">\n<head>\n<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
        f"{header(user, active)}\n"
        f"<main {container}>\n{flash_html}\n{content}\n</main>\n"
        "</body>\n</html>\n"
    )


def date_label(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.year}년 {d.month}월 {d.day}일 {store.WEEKDAY_KO[store.WEEKDAYS[d.weekday()]]}요일"


def page_head(title: str, subtitle: str = "") -> str:
    sub = f"<p>{esc(subtitle)}</p>" if subtitle else ""
    return f'<div class="page-head"><h1>{esc(title)}</h1>{sub}</div>'


# --------------------------------------------------------------------------- #
# 로그인
# --------------------------------------------------------------------------- #

def render_login(flash: str = "", error: bool = False) -> Response:
    flash_html = f'<div class="message{" error" if error else ""}">{esc(flash)}</div>' if flash else ""
    content = f"""
<div class="login-wrap">
    {flash_html}
    <div class="card">
        <h1>로그인</h1>
        <p>온점핑 출석 관리</p>
        <form method="POST" action="/login">
            <input name="username" placeholder="아이디" autocomplete="username"
                   autocapitalize="none" required>
            <input name="password" type="password" placeholder="비밀번호"
                   autocomplete="current-password" required>
            <button class="wide" style="margin-top:8px">로그인</button>
        </form>
        <p class="small">계정은 관리자에게 문의해 주세요.</p>
    </div>
</div>
"""
    return Response(
        "<!DOCTYPE html>\n<html lang=\"ko\">\n<head>\n<meta charset=\"UTF-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>로그인 · On점핑 출석</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
        f"{header(None, None)}\n<main class=\"container\">{content}</main>\n"
        "</body>\n</html>\n"
    )


# --------------------------------------------------------------------------- #
# 회원: 오늘 출석 /
# --------------------------------------------------------------------------- #

def _people_chips(people: list[dict], me_id: int) -> str:
    if not people:
        return '<p class="empty">아직 출석자가 없습니다.</p>'
    chips = []
    for person in people:
        mine = person["user_id"] == me_id
        label = esc(person["nickname"]) + (" (나)" if mine else "")
        chips.append(f'<span class="chip{" mine" if mine else ""}">{label}</span>')
    return f'<div class="people">{"".join(chips)}</div>'


def render_index(user: dict, flash: str = "", error: bool = False) -> Response:
    today = store.kst_today()
    state = store.get_day(today)
    rows = store.list_attendance(today)

    mine_by_slot = {row["slot_id"]: row for row in rows if row["user_id"] == user["id"]}
    by_slot: dict[str, list[dict]] = {}
    for row in rows:
        by_slot.setdefault(row["slot_id"], []).append(row)

    parts = [page_head("출석하기", f"{date_label(today)} · 타임마다 11명 선착순")]

    today_notice = next((n for n in store.list_notices() if n["posted_on"] == today), None)
    if today_notice:
        parts.append(f"""
<div class="card">
    <h2>📢 오늘의 공지</h2>
    <p class="notice-body">{esc(today_notice['body'])}</p>
    <p class="small">{esc(today_notice['author'])} · {today_notice['posted_on']}</p>
</div>""")
    else:
        parts.append("""
<div class="card">
    <h2>📢 오늘의 공지</h2>
    <p class="empty">오늘 올라온 공지가 없습니다.</p>
</div>""")

    if state["closed_all"]:
        parts.append('<div class="banner">🚫 오늘은 전체 휴강입니다.</div>')

    # 시간 선택 출석 카드
    options = []
    for slot in state["slots"]:
        marks = []
        if slot["blocked"]:
            marks.append("휴강")
        if slot["full"]:
            marks.append("마감")
        if slot["id"] in mine_by_slot:
            marks.append("출석함")
        suffix = f" ({' · '.join(marks)})" if marks else ""
        unavailable = bool(marks) or state["closed_all"]
        options.append(
            f'<option value="{slot["id"]}"{" disabled" if unavailable else ""}>'
            f'{slot["time"]} {slot["name"]}{suffix}</option>'
        )
    open_slots = [s for s in state["slots"] if s["open"] and s["id"] not in mine_by_slot]
    select_disabled = "" if open_slots else " disabled"
    button_disabled = "" if open_slots else " disabled"
    if state["closed_all"]:
        hint = "오늘은 전체 휴강이라 출석할 수 없습니다."
    elif not open_slots:
        hint = "지금 선택할 수 있는 타임이 없습니다."
    else:
        hint = "타임을 고르고 출석하기를 누르세요. 타임마다 11명 선착순입니다."

    my_rows = ""
    for slot in state["slots"]:
        record = mine_by_slot.get(slot["id"])
        if not record:
            continue
        my_rows += f"""
        <div class="list-row">
            <div class="list-main"><b>{slot["time"]}</b> <span class="muted">{slot["name"]}</span>
                <span class="badge">출석함</span></div>
            <form method="POST" action="/checkin/cancel/{record["id"]}">
                <button class="small ghost">출석 취소</button>
            </form>
        </div>"""

    parts.append(f"""
<div class="card">
    <h2>✅ 시간 선택해서 출석하기</h2>
    <form method="POST" action="/checkin">
        <select name="slot_id" aria-label="출석 타임"{select_disabled}>
            {''.join(options)}
        </select>
        <button class="wide" style="margin-top:8px"{button_disabled}>출석하기</button>
    </form>
    <p class="small">{hint}</p>{('<div style="margin-top:12px">' + my_rows + "</div>") if my_rows else ""}
</div>""")

    cards = []
    for slot in state["slots"]:
        people = by_slot.get(slot["id"], [])
        action = ""
        if state["closed_all"]:
            action = '<span class="badge off">전체 휴강</span>'
        elif slot["blocked"]:
            action = '<span class="badge off">휴강</span>'
        elif slot["full"]:
            action = '<span class="badge off">마감</span>'
        cards.append(f"""
<section class="card">
    <div class="slot-head">
        <div class="slot-title"><b>{slot["time"]}</b><span class="name">{slot["name"]}</span></div>
        <span class="badge">{slot["taken"]} / {slot["capacity"]}</span>
        {action}
    </div>
    {_people_chips(people, user["id"])}
</section>""")
    parts.append(f'<div class="slots">{"".join(cards)}</div>')

    parts.append(f"""
<div class="card">
    <h2>🗓️ 자동 출석 · 지난 공지</h2>
    <p class="small">자동 출석 설정과 지난 공지 목록은 각각 따로 있어요.</p>
    <div class="form-row">
        <a class="button small soft" href="/auto">자동 출석 설정</a>
        <a class="button small soft" href="/notices">공지 목록</a>
    </div>
</div>""")

    return Response(page("\n".join(parts), title="출석하기 · On점핑 출석",
                         user=user, active="checkin", flash=flash, error=error))


# --------------------------------------------------------------------------- #
# 회원: 공지 /notices
# --------------------------------------------------------------------------- #

def render_notices(user: dict, flash: str = "", error: bool = False) -> Response:
    today = store.kst_today()
    notices = store.list_notices()
    today_notice = next((n for n in notices if n["posted_on"] == today), None)
    others = [n for n in notices if n is not today_notice]

    parts = [page_head("공지", "오늘의 공지와 지난 공지 목록")]
    if today_notice:
        parts.append(f"""
<div class="card">
    <h2>📢 오늘의 공지 <span class="badge today">오늘</span></h2>
    <p class="notice-body">{esc(today_notice['body'])}</p>
    <p class="small">{esc(today_notice['author'])} · {today_notice['posted_on']}</p>
</div>""")
    else:
        parts.append('<div class="card"><h2>📢 오늘의 공지</h2>'
                     '<p class="empty">오늘 올라온 공지가 없습니다.</p></div>')

    if others:
        rows = []
        for notice in others:
            rows.append(f"""
<div class="card notice-card">
    <h3>{notice['posted_on']}</h3>
    <p class="meta">{esc(notice['author'])}</p>
    <p class="notice-body">{esc(notice['body'])}</p>
</div>""")
        parts.append('<h2 style="margin:22px 4px 12px;font-size:17px">지난 공지</h2>' + "".join(rows))

    return Response(page("\n".join(parts), title="공지 · On점핑 출석", user=user, active="notices",
                         flash=flash, error=error))


# --------------------------------------------------------------------------- #
# 회원: 자동 출석 /auto
# --------------------------------------------------------------------------- #

def render_auto(user: dict, flash: str = "", error: bool = False) -> Response:
    summary = " · ".join(
        f"{store.WEEKDAY_KO[d]} {store.slot_info(user['weekly'][d])['time'] if user['weekly'][d] else '쉼'}"
        for d in store.WEEKDAY_KEYS
    )
    day_options = [("", "쉼")] + [(slot["id"], slot["time"]) for slot in store.SLOTS]
    columns = ""
    for key in store.WEEKDAY_KEYS:
        current = user["weekly"].get(key) or ""
        opts = "".join(
            f'<option value="{value}"{" selected" if value == current else ""}>{label}</option>'
            for value, label in day_options
        )
        columns += f"""
        <div>
            <div class="day">{store.WEEKDAY_KO[key]}</div>
            <select name="{key}" aria-label="{store.WEEKDAY_KO[key]}요일 타임">{opts}</select>
        </div>"""

    parts = [
        page_head("자동 출석", "평일(월~금)에 밴드 대신 잡아 둘 타임을 정해 두세요"),
        f"""
<div class="card">
    <h2>🗓️ 평일 자동 출석</h2>
    <form method="POST" action="/auto">
        <div class="week-grid">{columns}</div>
        <button class="wide" style="margin-top:14px">저장</button>
    </form>
    <p class="small">내 설정: {esc(summary)}</p>
    <p class="small">주말은 설정하지 않습니다. 비워 두면 그 요일은 자동 출석을 하지 않아요.</p>
</div>""",
        f"""
<div class="card">
    <h2>👤 내 정보</h2>
    <div class="list-row">
        <div class="list-main"><b>{esc(user['nickname'])}</b><span class="meta">@{esc(user['username'])}</span></div>
    </div>
    <form method="POST" action="/profile">
        <input name="nickname" value="{esc(user['nickname'])}" maxlength="20"
               placeholder="닉네임" aria-label="닉네임">
        <button class="wide" style="margin-top:8px">닉네임 변경</button>
    </form>
</div>""",
    ]
    return Response(page("\n".join(parts), title="자동 출석 · On점핑 출석", user=user, active="auto",
                         flash=flash, error=error))


# --------------------------------------------------------------------------- #
# 관리자: 출석·휴강 /admin
# --------------------------------------------------------------------------- #

def render_admin(user: dict, flash: str = "", error: bool = False) -> Response:
    today = store.kst_today()
    state = store.get_day(today)
    rows = store.list_attendance(today)
    by_slot: dict[str, list[dict]] = {}
    for row in rows:
        by_slot.setdefault(row["slot_id"], []).append(row)

    slot_rows = ""
    for slot in state["slots"]:
        checked = " checked" if slot["blocked"] else ""
        slot_rows += f"""
    <label class="toggle-row">
        <span><b>{slot["time"]}</b> <span class="muted">{slot["name"]}</span>
        <span class="badge">{slot["taken"]} / {slot["capacity"]}</span></span>
        <input type="checkbox" name="blocked" value="{slot["id"]}"{checked} aria-label="{slot["time"]} 휴강">
    </label>"""

    parts = [page_head("출석·휴강", f"{date_label(today)} · 전체 {len(rows)}명 출석")]
    parts.append(f"""
<div class="card">
    <h2>🚫 휴강 제어</h2>
    <form method="POST" action="/admin/day">
        <label class="toggle-row">
            <span><b>오늘 전체 휴강</b></span>
            <input type="checkbox" name="closed_all" value="1"{" checked" if state["closed_all"] else ""}
                   aria-label="전체 휴강">
        </label>{slot_rows}
        <button class="wide" style="margin-top:14px">저장</button>
    </form>
    <p class="small">체크한 타임은 오늘 출석할 수 없습니다. 전체 휴강이면 모든 타임이 닫힙니다.</p>
</div>""")

    cards = []
    for slot in state["slots"]:
        people = by_slot.get(slot["id"], [])
        head_badges = f'<span class="badge">{slot["taken"]} / {slot["capacity"]}</span>'
        if slot["blocked"]:
            head_badges += ' <span class="badge off">휴강</span>'
        elif slot["full"]:
            head_badges += ' <span class="badge off">마감</span>'
        rows_html = ""
        if people:
            for person in people:
                rows_html += f"""
        <div class="list-row">
            <div class="list-main"><b>{esc(person['nickname'])}</b>
                <span class="meta">@{esc(person['username'])} · {person['created_at'][11:16]}</span></div>
            <form method="POST" action="/checkin/cancel/{person['id']}">
                <button class="small ghost">취소</button>
            </form>
        </div>"""
        else:
            rows_html = '<p class="empty">아직 출석자가 없습니다.</p>'
        cards.append(f"""
<section class="card">
    <div class="slot-head">
        <div class="slot-title"><b>{slot["time"]}</b><span class="name">{slot["name"]}</span></div>
        {head_badges}
    </div>
    {rows_html}
</section>""")
    parts.append(f'<div class="slots">{"".join(cards)}</div>')

    return Response(page("\n".join(parts), title="출석·휴강 · On점핑 출석",
                         user=user, active="admin", flash=flash, error=error, wide=True))


# --------------------------------------------------------------------------- #
# 관리자: 공지 관리 /admin/notices
# --------------------------------------------------------------------------- #

def render_admin_notices(user: dict, flash: str = "", error: bool = False) -> Response:
    today = store.kst_today()
    notices = store.list_notices()
    today_notice = next((n for n in notices if n["posted_on"] == today), None)
    value = esc(today_notice["body"]) if today_notice else ""

    parts = [page_head("공지 관리", "오늘의 공지 등록과 지난 공지 수정·삭제")]
    parts.append(f"""
<div class="card">
    <h2>📢 오늘의 공지</h2>
    <form method="POST" action="/admin/notices">
        <textarea name="body" placeholder="오늘의 공지를 입력하세요" required>{value}</textarea>
        <button class="wide" style="margin-top:8px">{"공지 수정" if today_notice else "공지 등록"}</button>
    </form>
    <p class="small">하루에 한 건입니다. 오늘 공지를 다시 저장하면 내용만 바뀝니다.</p>
</div>""")

    if notices:
        blocks = []
        for notice in notices:
            badge = ' <span class="badge today">오늘</span>' if notice["posted_on"] == today else ""
            blocks.append(f"""
<div class="card notice-card">
    <h3>{notice['posted_on']}{badge}</h3>
    <p class="meta">{esc(notice['author'])} · 수정 {notice['updated_at'][:16].replace('T', ' ')}</p>
    <form method="POST" action="/admin/notices/{notice['id']}">
        <textarea name="body" required>{esc(notice['body'])}</textarea>
        <button class="wide" style="margin-top:8px">저장</button>
    </form>
    <form method="POST" action="/admin/notices/{notice['id']}/delete" style="margin-top:8px">
        <button class="wide ghost">삭제</button>
    </form>
</div>""")
        parts.append('<h2 style="margin:22px 4px 12px;font-size:17px">공지 목록</h2>' + "".join(blocks))
    else:
        parts.append('<div class="card"><p class="empty">등록된 공지가 없습니다.</p></div>')

    return Response(page("\n".join(parts), title="공지 관리 · On점핑 출석", user=user,
                         active="admin-notices", flash=flash, error=error, wide=True))


# --------------------------------------------------------------------------- #
# 관리자: 회원 관리 /admin/users
# --------------------------------------------------------------------------- #

def render_admin_users(user: dict, flash: str = "", error: bool = False) -> Response:
    users = store.list_users()
    rows_html = ""
    for member in users:
        crown = ' <span class="badge crown">👑 관리자</span>' if member["is_admin"] else \
                ' <span class="badge off">회원</span>'
        weekly = member["weekly"]
        summary = " · ".join(
            f"{store.WEEKDAY_KO[d]} {store.slot_info(weekly[d])['time'] if weekly[d] else '쉼'}"
            for d in store.WEEKDAY_KEYS
        )
        delete = ""
        if not member["is_admin"]:
            delete = f"""
        <form method="POST" action="/admin/users/{member['id']}/delete">
            <button class="small ghost">삭제</button>
        </form>"""
        rows_html += f"""
    <div class="list-row">
        <div class="list-main"><b>{esc(member['nickname'])}</b> <span class="muted">@{esc(member['username'])}</span>{crown}
            <span class="meta">자동 출석 · {esc(summary)}</span></div>{delete}
    </div>"""

    parts = [
        page_head("회원", f"전체 {len(users)}명"),
        """
<div class="card">
    <div class="slot-head">
        <h2 style="margin:0">👥 회원 목록</h2>
        <a class="button small" href="/admin/users/new">➕ 회원 추가하기</a>
    </div>
    <div style="margin-top:8px">%s</div>
</div>""" % rows_html,
    ]
    return Response(page("\n".join(parts), title="회원 관리 · On점핑 출석", user=user,
                         active="admin-users", flash=flash, error=error, wide=True))


def render_admin_user_new(user: dict, flash: str = "", error: bool = False) -> Response:
    parts = [
        page_head("회원 추가", "새 회원 계정을 만듭니다. 만든 뒤 회원 목록에서 확인할 수 있어요."),
        """
<div class="card">
    <h2>➕ 회원 만들기</h2>
    <form method="POST" action="/admin/users">
        <input name="username" placeholder="아이디 (영문 소문자·숫자·밑줄 3~20자)"
               autocapitalize="none" required>
        <input name="password" type="password" placeholder="비밀번호 (4자 이상)" minlength="4" required>
        <input name="nickname" placeholder="닉네임 (선택)" maxlength="20">
        <button class="wide" style="margin-top:8px">회원 생성</button>
    </form>
    <p class="small">아이디는 로그인에 쓰는 값이라 나중에 바꿀 수 없습니다.</p>
    <p class="small"><a href="/admin/users">← 회원 목록으로</a></p>
</div>""",
    ]
    return Response(page("\n".join(parts), title="회원 추가 · On점핑 출석", user=user,
                         active="admin-users-new", flash=flash, error=error, wide=True))


# --------------------------------------------------------------------------- #
# 라우팅
# --------------------------------------------------------------------------- #

def _guard_admin(user: dict | None, flash: str) -> Response | None:
    if not user:
        return redirect("/login")
    if not user["is_admin"]:
        return redirect("/", "관리자만 접근할 수 있어요.", error=True)
    return None


def route(method: str, path: str, form: dict[str, list[str]], user: dict | None, flash: str,
          flash_error: bool) -> Response:
    if path == "/health":
        return Response("OK", 200, "text/plain; charset=utf-8")

    # ---- 로그인 / 로그아웃 ----
    if path == "/login":
        if method == "GET":
            if user:
                return redirect("/")
            return render_login(flash, flash_error)
        if method == "POST":
            found = store.authenticate(form_value(form, "username"), form_value(form, "password"))
            if not found:
                return render_login("아이디 또는 비밀번호가 틀렸습니다.", error=True)
            target = "/admin" if found["is_admin"] else "/"
            resp = redirect(target, f"{found['nickname']}님, 반가워요!")
            return resp.set_cookie(COOKIE, session_cookie(found["id"]))

    if path == "/logout":
        resp = redirect("/login", "로그아웃했습니다.")
        return resp.clear_cookie(COOKIE)

    # ---- 회원 화면 ----
    if path in ("/", "/index.html", "/checkin"):
        if method == "GET":
            if not user:
                return redirect("/login")
            return render_index(user, flash, flash_error)

    if path == "/notices" and method == "GET":
        if not user:
            return redirect("/login")
        return render_notices(user, flash, flash_error)

    if path == "/auto":
        if not user:
            return redirect("/login")
        if method == "GET":
            return render_auto(user, flash, flash_error)
        weekly = {key: form_value(form, key) for key in store.WEEKDAY_KEYS}
        store.set_weekly(user["id"], weekly)
        return redirect("/auto", "자동 출석 설정을 저장했어요.")

    if path == "/profile" and method == "POST":
        if not user:
            return redirect("/login")
        nickname = form_value(form, "nickname")
        if not nickname:
            return redirect("/auto", "닉네임을 입력해 주세요.", error=True)
        store.set_nickname(user["id"], nickname)
        return redirect("/auto", "닉네임을 바꿨어요.")

    if path == "/checkin" and method == "POST":
        if not user:
            return redirect("/login")
        slot_id = form_value(form, "slot_id")
        try:
            store.check_in(user["id"], store.kst_today(), slot_id)
        except store.CheckinError as exc:
            return redirect("/", exc.message, error=True)
        return redirect("/", f"{store.slot_info(slot_id)['time']} 출석 완료!")

    match = re.fullmatch(r"/checkin/cancel/(\d+)", path)
    if match and method == "POST":
        if not user:
            return redirect("/login")
        record = store.get_attendance(int(match.group(1)))
        if record is None:
            return redirect("/", "출석 기록을 찾을 수 없습니다.", error=True)
        if record["user_id"] != user["id"] and not user["is_admin"]:
            return redirect("/", "본인 출석만 취소할 수 있어요.", error=True)
        store.cancel_checkin(record["id"])
        back = "/admin" if user["is_admin"] and record["user_id"] != user["id"] else "/"
        return redirect(back, "출석을 취소했어요.")

    # ---- 관리자 화면 ----
    if path == "/admin" or path.startswith("/admin/"):
        blocked = _guard_admin(user, flash)
        if blocked:
            return blocked

        if path == "/admin":
            if method == "POST":
                closed_all = "1" in (form.get("closed_all") or [])
                blocked_slots = form.get("blocked") or []
                store.set_day(store.kst_today(), closed_all=closed_all, blocked=blocked_slots)
                if closed_all:
                    return redirect("/admin", "오늘을 전체 휴강으로 설정했어요.")
                if blocked_slots:
                    times = ", ".join(store.slot_info(s)["time"] for s in blocked_slots
                                      if s in store.SLOT_BY_ID)
                    return redirect("/admin", f"{times} 타임을 휴강으로 막았어요.")
                return redirect("/admin", "휴강 설정을 모두 풀었어요.")
            return render_admin(user, flash, flash_error)

        if path == "/admin/day" and method == "POST":
            closed_all = "1" in (form.get("closed_all") or [])
            blocked_slots = form.get("blocked") or []
            store.set_day(store.kst_today(), closed_all=closed_all, blocked=blocked_slots)
            if closed_all:
                return redirect("/admin", "오늘을 전체 휴강으로 설정했어요.")
            if blocked_slots:
                times = ", ".join(store.slot_info(s)["time"] for s in blocked_slots
                                  if s in store.SLOT_BY_ID)
                return redirect("/admin", f"{times} 타임을 휴강으로 막았어요.")
            return redirect("/admin", "휴강 설정을 모두 풀었어요.")

        if path == "/admin/notices":
            if method == "POST":
                body = form_value(form, "body")
                if not body:
                    return redirect("/admin/notices", "공지 내용을 입력해 주세요.", error=True)
                store.set_notice(body, user["nickname"], store.kst_today())
                return redirect("/admin/notices", "오늘의 공지를 저장했어요.")
            return render_admin_notices(user, flash, flash_error)

        match = re.fullmatch(r"/admin/notices/(\d+)", path)
        if match and method == "POST":
            body = form_value(form, "body")
            if not body:
                return redirect("/admin/notices", "공지 내용을 입력해 주세요.", error=True)
            if not store.update_notice(int(match.group(1)), body):
                return redirect("/admin/notices", "공지를 찾을 수 없습니다.", error=True)
            return redirect("/admin/notices", "공지를 수정했어요.")

        match = re.fullmatch(r"/admin/notices/(\d+)/delete", path)
        if match and method == "POST":
            if not store.delete_notice(int(match.group(1))):
                return redirect("/admin/notices", "삭제할 공지가 없습니다.", error=True)
            return redirect("/admin/notices", "공지를 삭제했어요.")

        if path == "/admin/users/new":
            if method == "GET":
                return render_admin_user_new(user, flash, flash_error)

        if path == "/admin/users":
            if method == "POST":
                username = form_value(form, "username").lower()
                password = form_value(form, "password")
                nickname = form_value(form, "nickname")
                if not re.fullmatch(r"[a-z0-9_]{3,20}", username):
                    return redirect("/admin/users", "아이디는 영문 소문자·숫자·밑줄 3~20자로 만들어 주세요.",
                                    error=True)
                if len(password) < 4:
                    return redirect("/admin/users", "비밀번호는 4자 이상이어야 합니다.", error=True)
                if store.username_taken(username):
                    return redirect("/admin/users", "이미 존재하는 아이디입니다.", error=True)
                store.create_user(username, password, nickname or username)
                return redirect("/admin/users", f"{nickname or username} 회원을 만들었어요.")
            return render_admin_users(user, flash, flash_error)

        match = re.fullmatch(r"/admin/users/(\d+)/delete", path)
        if match and method == "POST":
            if not store.delete_user(int(match.group(1))):
                return redirect("/admin/users", "삭제할 수 없는 계정입니다.", error=True)
            return redirect("/admin/users", "회원을 삭제했어요.")

    content = ('<div class="card"><h2>페이지를 찾을 수 없습니다.</h2>'
               '<p class="small">주소를 확인해 주세요.</p>'
               '<p class="small"><a href="/">출석 화면으로 →</a></p></div>')
    return Response(page(content, title="404 · On점핑 출석", user=user, flash=flash), 404)


def _error_page(exc: Exception) -> Response:
    if isinstance(exc, store.StoreBusy):
        message = "저장이 잠시 겹쳤어요. 잠시 뒤 다시 시도해 주세요."
    elif isinstance(exc, store.StoreError):
        message = "저장소에 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요."
    else:
        message = "문제가 발생했습니다. 잠시 뒤 다시 시도해 주세요."
    detail = f"{type(exc).__name__}: {exc}" if not isinstance(exc, store.StoreError) else ""
    content = (f'<div class="card"><h2>😥 {esc(message)}</h2>'
               + (f'<p class="small">{esc(detail)}</p>' if detail else "")
               + '<p class="small"><a href="/">출석 화면으로 →</a></p></div>')
    return Response(page(content, title="오류 · On점핑 출석"), 500)


def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO") or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    store.begin_request()
    try:
        store.init_db()
        user = store.get_user(parse_session(environ))
        flash, flash_error = read_flash(environ)
        form = read_form(environ) if method == "POST" else {}

        try:
            resp = route(method, path, form, user, flash, flash_error)
        except Exception as exc:  # 서버리스에서도 안내 화면을 보여 준다
            resp = _error_page(exc)

        if flash and not resp.flash_set:
            resp.clear_cookie(FLASH)
        return resp(environ, start_response)
    finally:
        store.end_request()
