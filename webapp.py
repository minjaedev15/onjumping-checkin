"""온점핑 출석 — 서버 렌더링 화면 (WSGI).

로컬 server.py 와 Vercel api/index.py 가 같은 `app` 을 쓴다. 화면은 카드 한 줄
레이아웃이고, 폼 전송 → 리다이렉트 → 플래시 메시지 방식이라 자바스크립트가 없다.

    /            오늘 출석 화면 (로그인 필요)
    /login       로그인 / 로그아웃
    /admin       관리자 화면 (공지 · 휴강 · 회원)
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import urllib.parse
from datetime import date, timedelta
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

header {
    background: #5865f2;
    color: white;
    padding: 18px;
    text-align: center;
    font-size: 22px;
    font-weight: bold;
    letter-spacing: -0.3px;
}

.container {
    max-width: 650px;
    margin: 25px auto;
    padding: 15px;
}

.card {
    background: white;
    border-radius: 16px;
    padding: 20px;
    margin-bottom: 18px;
    box-shadow: 0 3px 12px rgba(0, 0, 0, 0.06);
}

h2 {
    margin: 0 0 12px;
    font-size: 20px;
    letter-spacing: -0.3px;
}

h2 .badge { margin-left: 4px; vertical-align: 2px; }

input, select, textarea {
    width: 100%;
    padding: 13px;
    margin: 7px 0;
    border: 1px solid #ddd;
    border-radius: 9px;
    font-size: 16px;
    font-family: inherit;
    background: white;
    color: #222;
}

textarea { min-height: 104px; resize: vertical; line-height: 1.55; }

input[type=checkbox] {
    width: 22px;
    height: 22px;
    margin: 0;
    accent-color: #5865f2;
    flex: 0 0 auto;
}

button, .button {
    display: inline-block;
    border: none;
    background: #5865f2;
    color: white;
    padding: 12px 16px;
    border-radius: 9px;
    font-size: 15px;
    font-family: inherit;
    cursor: pointer;
    text-decoration: none;
    text-align: center;
}

button:hover, .button:hover { opacity: 0.85; }

button:disabled { background: #c7cbd6; cursor: not-allowed; opacity: 1; }

.danger { background: #e74c3c; }
.gray { background: #777; }
.green { background: #2ecc71; }
.wide { width: 100%; display: block; }

.attendance-box {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
    padding: 13px;
    border-bottom: 1px solid #eee;
}

.attendance-box:last-child { border-bottom: none; }

.badge {
    display: inline-block;
    background: #e8ebff;
    color: #5865f2;
    padding: 5px 10px;
    border-radius: 20px;
    font-size: 13px;
    font-weight: 600;
    vertical-align: middle;
}

.badge.off { background: #f0f1f5; color: #8a8f9c; }
.badge.crown { background: #fff4d6; color: #b7791f; }

.message {
    padding: 12px;
    background: #fff3cd;
    border-radius: 8px;
    margin-bottom: 12px;
}

.banner {
    padding: 14px 16px;
    background: #fdecea;
    color: #c0392b;
    border-radius: 12px;
    margin-bottom: 18px;
    font-weight: bold;
    text-align: center;
}

.small { color: #777; font-size: 13px; margin: 6px 0 0; }
.notice-body { margin: 0; line-height: 1.6; white-space: pre-line; }

.nav {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 14px;
}

.row { display: flex; gap: 8px; align-items: center; margin-top: 14px; }
.row input { margin: 0; }
.row button { flex: 0 0 auto; }

.week-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px; }
.week-grid .day { font-size: 13px; font-weight: 600; color: #555; text-align: center; }
.week-grid select { padding: 11px 2px; margin: 4px 0 0; font-size: 14px; text-align: center; }

@media(max-width: 500px) {
    .container { padding: 10px; margin: 18px auto; }
    .card { padding: 15px; }
    header { font-size: 20px; padding: 16px; }
    .week-grid { gap: 4px; }
    .week-grid select { font-size: 13px; padding: 10px 1px; }
    .week-grid .day { font-size: 12px; }
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

    def set_flash(self, message: str) -> "Response":
        self.cookies[FLASH] = (urllib.parse.quote(message), 60)
        self.flash_set = True
        return self

    def __call__(self, environ, start_response):
        headers = [
            ("Content-Type", self.ctype),
            ("Content-Length", str(len(self.body))),
            ("Cache-Control", "no-store"),
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


def redirect(location: str, flash: str | None = None) -> Response:
    resp = Response("", 303).header("Location", location)
    if flash:
        resp.set_flash(flash)
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


def read_flash(environ) -> str:
    header = environ.get("HTTP_COOKIE", "")
    if not header:
        return ""
    try:
        morsel = SimpleCookie(header).get(FLASH)
    except Exception:
        return ""
    return urllib.parse.unquote(morsel.value) if morsel else ""


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

def page(content: str, title: str = "On점핑 출석", flash: str = "") -> str:
    flash_html = f'<div class="message">{esc(flash)}</div>' if flash else ""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ko">\n<head>\n<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
        "<header>🏃 On점핑 출석</header>\n"
        '<div class="container">\n'
        f"{flash_html}\n{content}\n"
        "</div>\n</body>\n</html>\n"
    )


def date_label(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.year}년 {d.month}월 {d.day}일 {store.WEEKDAY_KO[store.WEEKDAYS[d.weekday()]]}요일"


def slot_badge(slot: dict) -> str:
    return f'<span class="badge">{slot["taken"]} / {slot["capacity"]}명</span>'


# --------------------------------------------------------------------------- #
# 로그인 화면
# --------------------------------------------------------------------------- #

def render_login(flash: str = "") -> Response:
    content = """
<div class="card">
    <h2>로그인</h2>
    <form method="POST" action="/login">
        <input name="username" placeholder="아이디" autocomplete="username"
               autocapitalize="none" required>
        <input name="password" type="password" placeholder="비밀번호"
               autocomplete="current-password" required>
        <button class="wide">로그인</button>
    </form>
    <p class="small">테스트 계정: <b>minjae</b> / <b>1234</b></p>
    <p class="small">계정은 관리자에게 문의하세요.</p>
</div>
"""
    return Response(page(content, "로그인 · On점핑 출석", flash))


# --------------------------------------------------------------------------- #
# 메인 화면
# --------------------------------------------------------------------------- #

def render_index(user: dict, flash: str = "") -> Response:
    today = store.kst_today()
    state = store.get_day(today)
    notice = store.latest_notice(today)
    rows = store.list_attendance(today)
    mine = {row["slot_id"] for row in rows if row["user_id"] == user["id"]}
    by_slot: dict[str, list[dict]] = {slot["id"]: [] for slot in state["slots"]}
    for row in rows:
        by_slot.setdefault(row["slot_id"], []).append(row)

    parts: list[str] = []

    # 인사 카드
    admin_link = ' <a class="button" href="/admin">관리자</a>' if user["is_admin"] else ""
    parts.append(f"""
<div class="card">
    <h2>안녕하세요, {esc(user['nickname'])}님 👋</h2>
    <p class="small">{date_label(today)}</p>
    <div class="nav">
        <a class="button gray" href="/logout">로그아웃</a>{admin_link}
    </div>
    <form method="POST" action="/profile" class="row">
        <input name="nickname" value="{esc(user['nickname'])}" maxlength="20"
               placeholder="닉네임" aria-label="닉네임">
        <button class="green">닉네임 변경</button>
    </form>
</div>""")

    # 전체 휴강 배너
    if state["closed_all"]:
        parts.append('\n<div class="banner">🚫 오늘은 전체 휴강입니다.</div>')

    # 오늘의 공지
    if notice:
        parts.append(f"""
<div class="card">
    <h2>📢 오늘의 공지</h2>
    <p class="notice-body">{esc(notice['body'])}</p>
    <p class="small">{esc(notice['author'])} · {notice['posted_on']}</p>
</div>""")

    # 출석 체크
    options = []
    for slot in state["slots"]:
        marks = []
        disabled = False
        if state["closed_all"]:
            disabled = True
        if slot["blocked"]:
            marks.append("(휴강)")
            disabled = True
        if slot["full"]:
            marks.append("(마감)")
            disabled = True
        if slot["id"] in mine:
            marks.append("(출석함)")
            disabled = True
        suffix = (" " + " ".join(marks)) if marks else ""
        label = f'{slot["time"]} · {slot["name"]}{suffix}'
        options.append(f'<option value="{slot["id"]}"{" disabled" if disabled else ""}>{esc(label)}</option>')

    can_check = any(slot["open"] and slot["id"] not in mine for slot in state["slots"])
    if state["closed_all"]:
        hint = "오늘은 전체 휴강이라 출석할 수 없습니다."
    elif not can_check:
        hint = "지금 출석할 수 있는 타임이 없습니다."
    else:
        hint = "타임마다 11명 선착순이에요."
    parts.append(f"""
<div class="card">
    <h2>✅ 출석 체크</h2>
    <form method="POST" action="/checkin">
        <select name="slot_id" aria-label="출석 타임"{" disabled" if not can_check else ""}>
            {''.join(options)}
        </select>
        <button class="wide"{" disabled" if not can_check else ""}>출석하기</button>
    </form>
    <p class="small">{hint}</p>
</div>""")

    # 시간별 출석 명단
    for slot in state["slots"]:
        people = by_slot.get(slot["id"], [])
        blocked_badge = ' <span class="badge off">(휴강)</span>' if slot["blocked"] else ""
        if people:
            rows_html = ""
            for person in people:
                who = f"<b>{esc(person['nickname'])}</b>"
                if user["is_admin"]:
                    who += f' <span class="small">@{esc(person["username"])}</span>'
                cancel = ""
                if user["is_admin"] or person["user_id"] == user["id"]:
                    cancel = f"""
                    <form method="POST" action="/checkin/cancel/{person['id']}">
                        <button class="danger">취소</button>
                    </form>"""
                rows_html += f'\n    <div class="attendance-box"><div>{who}</div>{cancel}</div>'
        else:
            empty = "휴강으로 막힌 타임입니다." if slot["blocked"] else "아직 출석자가 없습니다."
            rows_html = f'\n    <p class="small">{empty}</p>'
        parts.append(f"""
<div class="card">
    <h2>🕒 {slot["time"]} {slot_badge(slot)}{blocked_badge}</h2>{rows_html}
</div>""")

    # 평일 자동 출석
    day_options = [("", "쉼")] + [(slot["id"], slot["time"]) for slot in store.SLOTS]
    columns = ""
    summary = []
    for key in store.WEEKDAY_KEYS:
        current = user["weekly"].get(key)
        opts = "".join(
            f'<option value="{value}"{" selected" if value == (current or "") else ""}>{label}</option>'
            for value, label in day_options
        )
        columns += f"""
        <div>
            <div class="day">{store.WEEKDAY_KO[key]}</div>
            <select name="{key}" aria-label="{store.WEEKDAY_KO[key]}요일 타임">{opts}</select>
        </div>"""
        summary.append(f"{store.WEEKDAY_KO[key]} {store.slot_info(current)['time'] if current else '쉼'}")
    parts.append(f"""
<div class="card">
    <h2>🗓️ 평일 자동 출석</h2>
    <form method="POST" action="/weekly">
        <div class="week-grid">{columns}
        </div>
        <button class="wide" style="margin-top:14px">저장</button>
    </form>
    <p class="small">내 설정: {' · '.join(summary)}</p>
</div>""")

    return Response(page("\n".join(parts), "On점핑 출석", flash))


# --------------------------------------------------------------------------- #
# 관리자 화면
# --------------------------------------------------------------------------- #

def render_admin(user: dict, flash: str = "") -> Response:
    today = store.kst_today()
    state = store.get_day(today)
    notice = store.latest_notice(today)
    users = store.list_users()

    parts = [f"""
<div class="card">
    <h2>🛠️ 관리자 페이지</h2>
    <p class="small">{date_label(today)}</p>
    <div class="nav">
        <a class="button gray" href="/">메인으로</a>
    </div>
</div>"""]

    # 오늘의 공지
    notice_value = esc(notice["body"]) if notice else ""
    delete_form = ""
    if notice:
        delete_form = """
    <form method="POST" action="/admin/notice/delete" style="margin-top:8px">
        <button class="danger wide">공지 삭제</button>
    </form>"""
    parts.append(f"""
<div class="card">
    <h2>📢 오늘의 공지</h2>
    <form method="POST" action="/admin/notice">
        <textarea name="body" placeholder="오늘의 공지를 입력하세요" required>{notice_value}</textarea>
        <button class="wide">공지 등록</button>
    </form>{delete_form}
</div>""")

    # 휴강 / 타임 차단
    slot_rows = ""
    for slot in state["slots"]:
        checked = " checked" if slot["blocked"] else ""
        slot_rows += f"""
    <label class="attendance-box">
        <span><b>{slot["time"]}</b> <span class="small">{slot["name"]}</span></span>
        <input type="checkbox" name="blocked" value="{slot["id"]}"{checked}>
    </label>"""
    parts.append(f"""
<div class="card">
    <h2>🚫 휴강 / 타임 차단</h2>
    <form method="POST" action="/admin/day">
        <label class="attendance-box">
            <span><b>오늘 전체 휴강</b></span>
            <input type="checkbox" name="closed_all" value="1"{" checked" if state["closed_all"] else ""}>
        </label>{slot_rows}
        <button class="wide" style="margin-top:14px">저장</button>
    </form>
</div>""")

    # 회원 생성
    parts.append("""
<div class="card">
    <h2>👤 회원 생성</h2>
    <form method="POST" action="/admin/users">
        <input name="username" placeholder="새 아이디" autocapitalize="none" required>
        <input name="password" type="password" placeholder="새 비밀번호" minlength="4" required>
        <input name="nickname" placeholder="닉네임(선택)" maxlength="20">
        <button class="wide">회원 생성</button>
    </form>
</div>""")

    # 회원 목록
    rows_html = ""
    for member in users:
        crown = ' <span class="badge crown">👑 관리자</span>' if member["is_admin"] else ""
        delete = ""
        if not member["is_admin"]:
            delete = f"""
        <form method="POST" action="/admin/users/{member['id']}/delete">
            <button class="danger">삭제</button>
        </form>"""
        rows_html += f"""
    <div class="attendance-box">
        <div><b>{esc(member['nickname'])}</b> <span class="small">@{esc(member['username'])}</span>{crown}</div>{delete}
    </div>"""
    parts.append(f"""
<div class="card">
    <h2>👥 회원 목록 <span class="badge">{len(users)}명</span></h2>{rows_html}
</div>""")

    return Response(page("\n".join(parts), "관리자 · On점핑 출석", flash))


# --------------------------------------------------------------------------- #
# 라우팅
# --------------------------------------------------------------------------- #

def route(method: str, path: str, form: dict[str, list[str]], user: dict | None, flash: str) -> Response:
    if path == "/health":
        return Response("OK", 200, "text/plain; charset=utf-8")

    if method == "GET" and path in ("/", "/index.html"):
        if not user:
            return redirect("/login")
        return render_index(user, flash)

    if method == "GET" and path == "/login":
        if user:
            return redirect("/")
        return render_login(flash)

    if method == "POST" and path == "/login":
        username = form_value(form, "username")
        password = form_value(form, "password")
        found = store.authenticate(username, password)
        if not found:
            return render_login("아이디 또는 비밀번호가 틀렸습니다.")
        resp = redirect("/", f"{found['nickname']}님, 반가워요!")
        return resp.set_cookie(COOKIE, session_cookie(found["id"]))

    if path == "/logout":
        resp = redirect("/login", "로그아웃했습니다.")
        return resp.clear_cookie(COOKIE)

    if method == "POST" and path == "/profile":
        if not user:
            return redirect("/login")
        nickname = form_value(form, "nickname")
        if not nickname:
            return redirect("/", "닉네임을 입력해 주세요.")
        store.set_nickname(user["id"], nickname)
        return redirect("/", "닉네임을 바꿨어요.")

    if method == "POST" and path == "/weekly":
        if not user:
            return redirect("/login")
        weekly = {key: form_value(form, key) for key in store.WEEKDAY_KEYS}
        store.set_weekly(user["id"], weekly)
        return redirect("/", "평일 자동 출석을 저장했어요.")

    if method == "POST" and path == "/checkin":
        if not user:
            return redirect("/login")
        slot_id = form_value(form, "slot_id")
        try:
            store.check_in(user["id"], store.kst_today(), slot_id)
        except store.CheckinError as exc:
            return redirect("/", exc.message)
        return redirect("/", f"{store.slot_info(slot_id)['time']} 출석 완료!")

    match = re.fullmatch(r"/checkin/cancel/(\d+)", path)
    if match and method == "POST":
        if not user:
            return redirect("/login")
        record = store.get_attendance(int(match.group(1)))
        if record is None:
            return redirect("/", "출석 기록을 찾을 수 없습니다.")
        if record["user_id"] != user["id"] and not user["is_admin"]:
            return redirect("/", "본인 출석만 취소할 수 있어요.")
        store.cancel_checkin(record["id"])
        return redirect("/", "출석을 취소했어요.")

    if path == "/admin":
        if not user:
            return redirect("/login")
        if not user["is_admin"]:
            return redirect("/", "관리자만 접근할 수 있어요.")

        if method == "POST":
            # notice/notice-delete/users/day 는 각자의 경로에서 처리한다.
            return route_post_admin(path, form, user)

        return render_admin(user, flash)

    if path.startswith("/admin/") and method == "POST":
        if not user:
            return redirect("/login")
        if not user["is_admin"]:
            return redirect("/", "관리자만 접근할 수 있어요.")
        return route_post_admin(path, form, user)

    return Response(page('<div class="card"><h2>페이지를 찾을 수 없습니다.</h2>'
                         '<p class="small">주소를 확인해 주세요.</p>'
                         '<div class="nav"><a class="button gray" href="/">메인으로</a></div></div>',
                         "404 · On점핑 출석"), 404)


def route_post_admin(path: str, form: dict[str, list[str]], user: dict) -> Response:
    today = store.kst_today()

    if path == "/admin":
        return redirect("/admin")

    if path == "/admin/notice":
        body = (form.get("body") or [""])[0].strip()
        if not body:
            return redirect("/admin", "공지 내용을 입력해 주세요.")
        store.set_notice(body, user["nickname"], today)
        return redirect("/admin", "오늘의 공지를 등록했어요.")

    if path == "/admin/notice/delete":
        notice = store.latest_notice(today)
        if not notice:
            return redirect("/admin", "삭제할 공지가 없습니다.")
        store.delete_notice(notice["id"])
        return redirect("/admin", "공지를 삭제했어요.")

    if path == "/admin/day":
        closed_all = "1" in (form.get("closed_all") or [])
        blocked = form.get("blocked") or []
        store.set_day(today, closed_all=closed_all, blocked=blocked)
        if closed_all:
            return redirect("/admin", "오늘을 전체 휴강으로 설정했어요.")
        if blocked:
            times = ", ".join(store.slot_info(s)["time"] for s in blocked if s in store.SLOT_BY_ID)
            return redirect("/admin", f"{times} 타임을 휴강으로 막았어요.")
        return redirect("/admin", "휴강 설정을 모두 풀었어요.")

    if path == "/admin/users":
        username = form_value(form, "username").lower()
        password = form_value(form, "password")
        nickname = form_value(form, "nickname")
        if not re.fullmatch(r"[a-z0-9_]{3,20}", username):
            return redirect("/admin", "아이디는 영문 소문자·숫자·밑줄 3~20자로 만들어 주세요.")
        if len(password) < 4:
            return redirect("/admin", "비밀번호는 4자 이상이어야 합니다.")
        if store.username_taken(username):
            return redirect("/admin", "이미 존재하는 아이디입니다.")
        store.create_user(username, password, nickname or username)
        return redirect("/admin", f"{nickname or username} 회원을 만들었어요.")

    match = re.fullmatch(r"/admin/users/(\d+)/delete", path)
    if match:
        if not store.delete_user(int(match.group(1))):
            return redirect("/admin", "삭제할 수 없는 계정입니다.")
        return redirect("/admin", "회원을 삭제했어요.")

    return Response(page('<div class="card"><h2>페이지를 찾을 수 없습니다.</h2></div>',
                         "404 · On점핑 출석"), 404)


def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO") or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    store.init_db()
    user = store.get_user(parse_session(environ))
    flash = read_flash(environ)
    form = read_form(environ) if method == "POST" else {}

    try:
        resp = route(method, path, form, user, flash)
    except Exception as exc:  # 서버리스에서 500 대신 안내 화면
        resp = Response(page(f'<div class="card"><h2>문제가 발생했습니다.</h2>'
                             f'<p class="small">{esc(type(exc).__name__)}: {esc(exc)}</p>'
                             f'<div class="nav"><a class="button gray" href="/">메인으로</a></div></div>',
                             "오류 · On점핑 출석"), 500)

    if flash and not resp.flash_set:
        resp.clear_cookie(FLASH)
    return resp(environ, start_response)
