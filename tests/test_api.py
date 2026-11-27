"""화면·동작 테스트 (HTTP 서버 없이 WSGI 앱을 직접 호출).

    python -m unittest discover tests -v

임시 SQLite 파일을 쓰고, 로그인 → 폼 전송 → 리다이렉트 → 화면 확인 순서로 실제
사용 흐름을 따라간다. 비밀번호는 코드에 두지 않고 실행할 때 환경변수로 만든다.
"""

from __future__ import annotations

import io
import json
import os
import secrets
import sys
import tempfile
import unittest
import urllib.parse
from http.cookies import SimpleCookie
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 테스트 전용 저장소·자격 증명 (코드에 평문으로 남기지 않는다)
# ONJUMPING_TEST_REMOTE=1 이면 환경에 있는 원격 저장소(Upstash)로 같은 흐름을 확인한다.
if not os.environ.get("ONJUMPING_TEST_REMOTE"):
    for _name in ("KV_REST_API_URL", "KV_REST_API_TOKEN", "UPSTASH_REDIS_REST_URL",
                  "UPSTASH_REDIS_REST_TOKEN", "ONJUMPING_REDIS_REST_URL", "ONJUMPING_REDIS_REST_TOKEN"):
        os.environ.pop(_name, None)
os.environ["ONJUMPING_DATA_DIR"] = tempfile.mkdtemp(prefix="onjumping-test-")
os.environ.setdefault("SESSION_SECRET", secrets.token_hex(16))
os.environ["ADMIN_PASSWORD"] = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(12)

ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
MEMBER_PASSWORD = secrets.token_urlsafe(9)

import store  # noqa: E402
import webapp  # noqa: E402


class Client:
    """쿠키를 들고 다니는 최소 WSGI 클라이언트."""

    def __init__(self):
        self.cookies: dict[str, str] = {}

    def request(self, method: str, path: str, form: dict | None = None,
                raw: bytes | None = None, ctype: str = "application/x-www-form-urlencoded",
                decode: bool = True):
        body = raw if raw is not None else (
            urllib.parse.urlencode(form, doseq=True).encode("utf-8") if form else b"")
        environ = {
            "REQUEST_METHOD": method.upper(),
            "PATH_INFO": path.split("?")[0],
            "QUERY_STRING": path.split("?", 1)[1] if "?" in path else "",
            "CONTENT_TYPE": ctype,
            "CONTENT_LENGTH": str(len(body)),
            "wsgi.input": io.BytesIO(body),
            "HTTP_COOKIE": "; ".join(f"{k}={v}" for k, v in self.cookies.items()),
        }
        captured: dict = {}

        def start_response(status, headers):
            captured["status"] = int(status.split(" ")[0])
            captured["headers"] = headers

        raw_body = b"".join(webapp.app(environ, start_response))
        for name, value in captured["headers"]:
            if name.lower() != "set-cookie":
                continue
            for key, morsel in SimpleCookie(value).items():
                if morsel["max-age"] == "0" or morsel.value == "":
                    self.cookies.pop(key, None)
                else:
                    self.cookies[key] = morsel.value
        body = raw_body.decode("utf-8") if decode else raw_body
        return captured["status"], dict(captured["headers"]), body

    def get(self, path, decode: bool = True):
        return self.request("GET", path, decode=decode)

    def post(self, path, form=None):
        return self.request("POST", path, form or {})

    def login(self, username: str, password: str):
        status, headers, _ = self.post("/login", {"username": username, "password": password})
        assert status == 303, (status, headers)
        self.last_login_target = headers.get("Location")
        return self


def flash_text(html: str) -> str:
    marker = '<div class="message'
    if marker not in html:
        return ""
    start = html.index(marker)
    return html[start:html.index("</div>", start)]


class AppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.init_db()
        store.create_user("minjae", MEMBER_PASSWORD, "민재")
        store.create_user("doyun", MEMBER_PASSWORD, "도윤")

    def setUp(self):
        self.admin = Client().login("admin", ADMIN_PASSWORD)
        self.member = Client().login("minjae", MEMBER_PASSWORD)

    def tearDown(self):
        store.set_day(store.kst_today(), closed_all=False, blocked=[])
        for notice in store.list_notices():
            store.delete_notice(notice["id"])
        store.set_notice(store.SEED_NOTICE, "관리자", store.kst_today())
        for record in store.list_attendance(store.kst_today()):
            store.cancel_checkin(record["id"])

    # ---------------- 로그인 / 접근 ---------------- #
    def test_01_login_flow_and_no_credential_hints(self):
        guest = Client()
        status, headers, _ = guest.get("/")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/login")

        status, _, html = guest.get("/login")
        self.assertEqual(status, 200)
        self.assertIn("로그인", html)
        # 비밀번호·테스트 계정 안내가 화면에 없어야 한다
        self.assertNotIn("테스트 계정", html)
        self.assertNotIn("비밀번호 힌트", html)
        self.assertNotIn(MEMBER_PASSWORD, html)
        self.assertNotIn('value="admin"', html)

        status, _, html = guest.post("/login", {"username": "admin", "password": "nope"})
        self.assertEqual(status, 200)
        self.assertIn("아이디 또는 비밀번호가 틀렸습니다.", html)
        self.assertEqual(guest.cookies, {})

        status, headers, _ = guest.post("/login", {"username": "admin", "password": ADMIN_PASSWORD})
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/admin")
        self.assertIn("onjumping_uid", guest.cookies)

        status, _, html = guest.get("/admin")
        self.assertEqual(status, 200)
        self.assertIn("출석·휴강", html)

        status, headers, _ = guest.get("/logout")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/login")
        status, headers, _ = guest.get("/")
        self.assertEqual(status, 303)

    def test_02_forged_cookie_is_rejected(self):
        guest = Client()
        guest.cookies["onjumping_uid"] = "1.deadbeefdeadbeefdeadbeefdeadbeef"
        status, headers, _ = guest.get("/")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/login")

    def test_03_member_cannot_reach_admin(self):
        for path in ("/admin", "/admin/notices", "/admin/users"):
            status, headers, _ = self.member.get(path)
            self.assertEqual(status, 303, path)
            self.assertEqual(headers["Location"], "/", path)
        _, _, html = self.member.get("/")
        self.assertIn("관리자만 접근할 수 있어요.", html)
        self.assertNotIn('href="/admin"', html)

        status, _, _ = self.member.post("/admin/users", {"username": "sneak", "password": MEMBER_PASSWORD})
        self.assertEqual(status, 303)
        self.assertTrue(all(u["username"] != "sneak" for u in store.list_users()))

        self.member.post("/admin/notices", {"body": "침입 공지"})
        self.assertFalse(any(n["body"] == "침입 공지" for n in store.list_notices()))

    # ---------------- 화면 분리 ----------------
    def test_04_member_pages_are_split(self):
        _, _, home = self.member.get("/")
        self.assertIn("출석하기", home)
        self.assertIn('class="blocks"', home)
        self.assertIn('href="/checkin"', home)
        self.assertIn('href="/notices"', home)
        self.assertIn('href="/auto"', home)
        # 홈은 큰 블록만 두고 출석 폼은 /checkin 으로 분리
        self.assertNotIn('name="slot_id"', home)
        self.assertNotIn("평일 자동 출석", home)
        # 상단 네비게이션 탭(navbar)은 없어진다
        self.assertNotIn('class="tabs"', home)
        self.assertNotIn("<nav", home)
        self.assertNotIn("회원 만들기", home)

        # 출석 전용 주소: 위에 공지, 아래에 시간 선택
        status, _, checkin = self.member.get("/checkin")
        self.assertEqual(status, 200)
        self.assertIn("📢 오늘의 공지", checkin)
        self.assertIn('name="slot_id"', checkin)
        for time_label in ("08:30", "09:30", "18:30", "19:40"):
            self.assertIn(time_label, checkin)
        self.assertIn("✅ 시간 선택해서 출석하기", checkin)
        self.assertLess(checkin.index("📢 오늘의 공지"), checkin.index('name="slot_id"'))
        self.assertIn("뒤로", checkin)
        self.assertNotIn('class="tabs"', checkin)

        _, _, notices = self.member.get("/notices")
        self.assertIn("오늘의 공지", notices)
        self.assertNotIn('name="slot_id"', notices)
        self.assertIn("뒤로", notices)

        _, _, auto = self.member.get("/auto")
        self.assertIn("평일 자동 출석", auto)
        self.assertIn('name="mon"', auto)
        self.assertIn('name="fri"', auto)
        self.assertNotIn('name="sat"', auto)
        self.assertIn("내 정보", auto)
        self.assertNotIn('name="slot_id"', auto)

    def test_04b_home_blocks_follow_role(self):
        _, _, member_home = self.member.get("/")
        self.assertIn('href="/checkin"', member_home)
        self.assertIn('href="/auto"', member_home)
        self.assertNotIn('href="/admin"', member_home)
        self.assertNotIn('href="/admin/notices"', member_home)

        _, _, admin_home = self.admin.get("/")
        for href in ('href="/admin"', 'href="/admin/notices"', 'href="/admin/users"',
                     'href="/admin/users/new"'):
            self.assertIn(href, admin_home)
        self.assertIn("관리자", admin_home)

    def test_05_admin_pages_are_split(self):
        _, _, attendance = self.admin.get("/admin")
        self.assertIn("휴강 제어", attendance)
        self.assertIn("18:30", attendance)
        self.assertNotIn("회원 만들기", attendance)
        self.assertNotIn("공지 목록", attendance)

        _, _, notices = self.admin.get("/admin/notices")
        self.assertIn("오늘의 공지", notices)
        self.assertIn("공지 목록", notices)
        self.assertNotIn("휴강 제어", notices)

        _, _, users = self.admin.get("/admin/users")
        self.assertIn("회원 목록", users)
        self.assertIn("@minjae", users)
        self.assertIn('href="/admin/users/new"', users)
        self.assertNotIn('action="/admin/users"', users)   # 생성 폼은 전용 페이지로
        self.assertNotIn("휴강 제어", users)

        status, _, new_user = self.admin.get("/admin/users/new")
        self.assertEqual(status, 200)
        self.assertIn("회원 만들기", new_user)
        self.assertIn('action="/admin/users"', new_user)
        self.assertNotIn("👥 회원 목록", new_user)
        self.assertNotIn('name="body"', new_user)      # 공지 폼 없음
        self.assertNotIn("휴강 제어", new_user)

        _, _, home = self.admin.get("/")
        for href in ('href="/admin"', 'href="/admin/notices"', 'href="/admin/users"'):
            self.assertIn(href, home)

    # ---------------- 출석 ---------------- #
    def test_06_checkin_and_cancel(self):
        day = store.kst_today()
        before = store.get_day(day)["slots"][2]["taken"]

        status, headers, _ = self.member.post("/checkin", {"slot_id": "third"})
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/checkin")
        self.assertEqual(store.get_day(day)["slots"][2]["taken"], before + 1)

        _, _, html = self.member.get("/checkin")
        self.assertIn("18:30 출석 완료!", html)
        self.assertIn("(나)", html)
        self.assertIn("출석 취소", html)
        self.assertIn("(출석함)", html)

        # 홈 블록에도 오늘 출석 상태가 보인다
        _, _, home = self.member.get("/")
        self.assertIn("출석 완료", home)
        self.assertIn("18:30", home)

        self.member.post("/checkin", {"slot_id": "third"})
        _, _, html = self.member.get("/checkin")
        self.assertIn("이미 18:30 타임에 출석했어요.", html)

        rows = [r for r in store.list_attendance(day) if r["username"] == "minjae"]
        self.assertEqual(len(rows), 1)
        status, headers, _ = self.member.post(f"/checkin/cancel/{rows[0]['id']}")
        self.assertEqual(status, 303)
        _, _, html = self.member.get("/checkin")
        self.assertIn("출석을 취소했어요.", html)
        self.assertEqual(store.get_day(day)["slots"][2]["taken"], before)

    def test_07_cannot_cancel_someone_else(self):
        day = store.kst_today()
        self.member.post("/checkin", {"slot_id": "first"})
        record = next(r for r in store.list_attendance(day) if r["username"] == "minjae")

        other = Client().login("doyun", MEMBER_PASSWORD)
        other.post(f"/checkin/cancel/{record['id']}")
        _, _, html = other.get("/")
        self.assertIn("본인 출석만 취소할 수 있어요.", html)
        self.assertEqual(len(store.list_attendance(day)), 1)

        self.admin.post(f"/checkin/cancel/{record['id']}")
        self.assertEqual(store.list_attendance(day), [])

    def test_08_full_slot_is_blocked(self):
        day = store.kst_today()
        made = [store.create_user(f"full{i}", MEMBER_PASSWORD, f"가득{i}") for i in range(11)]
        try:
            for user in made:
                store.check_in(user["id"], day, "fourth")
            _, _, html = self.member.get("/checkin")
            self.assertIn("마감", html)
            self.member.post("/checkin", {"slot_id": "fourth"})
            _, _, html = self.member.get("/checkin")
            self.assertIn("19:40 타임은 정원이 다 찼어요", html)
        finally:
            for user in made:
                store.delete_user(user["id"])

    def test_06b_only_one_checkin_per_day(self):
        day = store.kst_today()
        self.member.post("/checkin", {"slot_id": "third"})
        mine = [r for r in store.list_attendance(day) if r["username"] == "minjae"]
        self.assertEqual(len(mine), 1)

        # 다른 타임으로 또 출석하려 해도 거부된다
        status, headers, _ = self.member.post("/checkin", {"slot_id": "first"})
        self.assertEqual(status, 303)
        _, _, html = self.member.get("/checkin")
        self.assertIn("오늘은 이미 18:30 타임에 출석했어요", html)
        mine = [r for r in store.list_attendance(day) if r["username"] == "minjae"]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["slot_id"], "third")

        # 같은 타임으로 또 눌러도 한 번 더 쌓이지 않는다
        self.member.post("/checkin", {"slot_id": "third"})
        self.assertEqual(len([r for r in store.list_attendance(day) if r["username"] == "minjae"]), 1)

        # 버튼·타임 선택이 잠기고 "오늘 출석 완료"가 보인다
        self.assertIn("오늘 출석 완료", html)
        self.assertIn(">오늘 출석 완료</button>", html)
        self.assertIn('aria-label="출석 타임" disabled', html)
        self.assertIn('<option value="first" disabled>', html)

        _, _, home = self.member.get("/")
        self.assertIn("출석 완료", home)

        # 취소하면 다시 출석할 수 있다
        self.member.post(f"/checkin/cancel/{mine[0]['id']}")
        status, _, _ = self.member.post("/checkin", {"slot_id": "first"})
        self.assertEqual(status, 303)
        mine = [r for r in store.list_attendance(day) if r["username"] == "minjae"]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["slot_id"], "first")

    # ---------------- 휴강 ---------------- #
    def test_09_holiday_blocks_checkin(self):
        status, _, _ = self.admin.post("/admin/day", {"closed_all": "1"})
        self.assertEqual(status, 303)
        _, _, html = self.member.get("/")
        self.assertIn("🚫 오늘은 전체 휴강입니다.", html)
        _, _, checkin = self.member.get("/checkin")
        self.assertIn("🚫 오늘은 전체 휴강입니다.", checkin)
        self.assertIn("오늘은 전체 휴강이라 출석할 수 없습니다.", checkin)

        self.member.post("/checkin", {"slot_id": "third"})
        _, _, html = self.member.get("/checkin")
        self.assertIn("전체 휴강이라 출석할 수 없어요.", html)
        self.assertEqual(store.list_attendance(store.kst_today()), [])

        self.admin.post("/admin/day", {})
        _, _, html = self.member.get("/")
        self.assertNotIn("🚫 오늘은 전체 휴강입니다.", html)

    def test_10_slot_block_only_blocks_that_time(self):
        status, _, _ = self.admin.post("/admin/day", {"blocked": ["first", "second"]})
        self.assertEqual(status, 303)
        state = store.get_day(store.kst_today())
        self.assertEqual(sorted(state["blocked"]), ["first", "second"])

        _, _, html = self.member.get("/checkin")
        self.assertIn("휴강", html)

        self.member.post("/checkin", {"slot_id": "first"})
        _, _, html = self.member.get("/checkin")
        self.assertIn("08:30 타임은 오늘 휴강이에요.", html)

        status, _, _ = self.member.post("/checkin", {"slot_id": "third"})
        self.assertEqual(status, 303)
        self.assertEqual(store.get_day(store.kst_today())["slots"][2]["taken"], 1)

    # ---------------- 공지 ---------------- #
    def test_11_notice_register_edit_delete(self):
        status, _, _ = self.admin.post("/admin/notices", {"body": "오늘 21시에 예약 글이 올라옵니다."})
        self.assertEqual(status, 303)
        _, _, html = self.member.get("/notices")
        self.assertIn("오늘 21시에 예약 글이 올라옵니다.", html)
        self.assertIn("오늘", html)
        _, _, home = self.member.get("/")
        self.assertIn("오늘 21시에 예약 글이 올라옵니다.", home)
        self.assertIn("공지사항", home)

        # 같은 날 다시 등록하면 내용만 바뀐다 (중복 카드 없음)
        self.admin.post("/admin/notices", {"body": "수정된 공지"})
        _, _, html = self.member.get("/notices")
        self.assertEqual(html.count("📢 오늘의 공지"), 1)
        self.assertIn("수정된 공지", html)

        # 목록에서 특정 공지 수정·삭제
        today_notice = next(n for n in store.list_notices() if n["posted_on"] == store.kst_today())
        self.admin.post(f"/admin/notices/{today_notice['id']}", {"body": "다시 고친 공지"})
        self.assertIn("다시 고친 공지", [n["body"] for n in store.list_notices()])

        _, _, admin_html = self.admin.get("/admin/notices")
        self.assertIn("다시 고친 공지", admin_html)

        self.admin.post(f"/admin/notices/{today_notice['id']}/delete")
        self.assertNotIn(today_notice["id"], [n["id"] for n in store.list_notices()])

        self.admin.post("/admin/notices", {"body": "  "})
        _, _, admin_html = self.admin.get("/admin/notices")
        self.assertIn("공지 내용을 입력해 주세요.", admin_html)

    # ---------------- 공지 이미지 ---------------- #
    PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
           "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

    def _today_notice(self):
        return next(n for n in store.list_notices() if n["posted_on"] == store.kst_today())

    def test_11b_notice_image_upload_and_display(self):
        status, _, _ = self.admin.post("/admin/notices", {"body": "사진 있는 공지", "image_data": self.PNG})
        self.assertEqual(status, 303)
        notice = self._today_notice()
        self.assertEqual(notice["image_data"], self.PNG)

        _, _, html = self.member.get("/notices")
        self.assertIn('class="notice-img"', html)
        self.assertIn(self.PNG, html)

        _, _, home = self.member.get("/")
        self.assertIn('class="thumb"', home)
        self.assertIn(self.PNG, home)

        _, _, checkin = self.member.get("/checkin")
        self.assertIn('class="notice-img"', checkin)

        # 관리자 화면에는 파일 선택·미리보기·이미지 삭제 UI 가 있다
        _, _, admin_html = self.admin.get("/admin/notices")
        self.assertIn('type="file"', admin_html)
        self.assertIn('accept="image/*"', admin_html)
        self.assertIn('name="image_data"', admin_html)
        self.assertIn('name="remove_image"', admin_html)
        self.assertIn('class="image-current"', admin_html)
        self.assertIn('class="image-preview"', admin_html)

        # 이미지를 새로 고르지 않고 저장하면 기존 이미지가 남는다
        self.admin.post("/admin/notices", {"body": "본문만 고침", "image_data": ""})
        notice = self._today_notice()
        self.assertEqual(notice["image_data"], self.PNG)
        self.assertEqual(notice["body"], "본문만 고침")

        # 다른 공지 수정에서도 그림이 유지된다
        other = store.set_notice("지난 공지", "관리자", posted_on="2026-01-01", image_data=self.PNG)
        self.admin.post(f"/admin/notices/{other['id']}", {"body": "지난 공지 고침"})
        self.assertEqual(store.get_notice(other["id"])["image_data"], self.PNG)

        # 삭제 체크하면 이미지만 빠진다
        self.admin.post("/admin/notices", {"body": "사진 지움", "image_data": "", "remove_image": "1"})
        self.assertEqual(self._today_notice()["image_data"], "")
        _, _, html = self.member.get("/notices")
        self.assertNotIn('class="notice-img"', html.split("지난 공지")[0])

    def test_11c_notice_image_validation_and_permissions(self):
        # 지원하지 않는 형식
        self.admin.post("/admin/notices", {"body": "형식 오류",
                                            "image_data": "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="})
        _, _, admin_html = self.admin.get("/admin/notices")
        self.assertIn("이미지 형식이 올바르지 않아요", admin_html)
        self.assertEqual(self._today_notice()["body"], store.SEED_NOTICE)
        self.assertEqual(self._today_notice()["image_data"], "")

        # 800KB 초과
        huge = "data:image/png;base64," + "A" * (800 * 1024)
        self.admin.post("/admin/notices", {"body": "큰 이미지", "image_data": huge})
        _, _, admin_html = self.admin.get("/admin/notices")
        self.assertIn("이미지가 너무 커요", admin_html)
        self.assertEqual(self._today_notice()["body"], store.SEED_NOTICE)

        # base64 가 깨진 값
        self.admin.post("/admin/notices", {"body": "깨진 이미지", "image_data": "data:image/png;base64,AAA"})
        _, _, admin_html = self.admin.get("/admin/notices")
        self.assertIn("이미지를 읽지 못했어요", admin_html)
        self.assertEqual(self._today_notice()["body"], store.SEED_NOTICE)

        # data:image 가 아닌 값
        self.admin.post("/admin/notices", {"body": "이상한 값", "image_data": "javascript:alert(1)"})
        _, _, admin_html = self.admin.get("/admin/notices")
        self.assertIn("이미지 형식이 올바르지 않아요", admin_html)
        self.assertEqual(self._today_notice()["body"], store.SEED_NOTICE)

        # 일반 회원은 공지를 올릴 수 없다 (이미지 포함)
        self.member.post("/admin/notices", {"body": "회원 공지", "image_data": self.PNG})
        self.assertFalse(any(n["body"] == "회원 공지" for n in store.list_notices()))

        # multipart/form-data 요청은 읽지 않고 넘어가 기존 공지를 지우지 않는다
        raw = (b"--x\r\nContent-Disposition: form-data; name=\"body\"\r\n\r\nmultipart test\r\n"
               b"--x--\r\n")
        status, _, _ = self.admin.request("POST", "/admin/notices", raw=raw,
                                          ctype="multipart/form-data; boundary=x")
        self.assertEqual(status, 303)
        self.assertEqual(self._today_notice()["body"], store.SEED_NOTICE)

    # ---------------- 회원 관리 ---------------- #
    def test_12_admin_user_crud(self):
        status, _, _ = self.admin.post("/admin/users", {
            "username": "friend", "password": MEMBER_PASSWORD, "nickname": "친구",
        })
        self.assertEqual(status, 303)
        _, _, admin_html = self.admin.get("/admin/users")
        self.assertIn("친구", admin_html)
        self.assertIn("@friend", admin_html)
        self.assertIn("👑 관리자", admin_html)

        friend = Client().login("friend", MEMBER_PASSWORD)
        self.assertEqual(friend.last_login_target, "/")
        _, _, html = friend.get("/")
        self.assertIn("출석하기", html)

        created = next(u for u in store.list_users() if u["username"] == "friend")
        self.admin.post(f"/admin/users/{created['id']}/delete")
        self.assertTrue(all(u["username"] != "friend" for u in store.list_users()))

        # 관리자 계정은 삭제되지 않는다
        admin_user = next(u for u in store.list_users() if u["username"] == "admin")
        self.admin.post(f"/admin/users/{admin_user['id']}/delete")
        self.assertTrue(store.get_user(admin_user["id"])["is_admin"])

        # 중복 아이디 / 짧은 비밀번호 / 잘못된 아이디는 거부
        self.admin.post("/admin/users", {"username": "minjae", "password": MEMBER_PASSWORD})
        self.admin.post("/admin/users", {"username": "shorty", "password": "12"})
        self.admin.post("/admin/users", {"username": "한글", "password": MEMBER_PASSWORD})
        self.assertTrue(all(u["username"] not in ("shorty", "한글") for u in store.list_users()))

    # ---------------- 내 설정 ---------------- #
    def test_13_nickname_and_weekly(self):
        status, _, _ = self.member.post("/profile", {"nickname": "민재짱"})
        self.assertEqual(status, 303)
        _, _, html = self.member.get("/auto")
        self.assertIn("민재짱", html)

        self.member.post("/auto", {"mon": "first", "tue": "third", "wed": "fourth", "thu": "", "fri": ""})
        user = store.authenticate("minjae", MEMBER_PASSWORD)
        self.assertEqual(user["weekly"], {"mon": "first", "tue": "third", "wed": "fourth",
                                          "thu": None, "fri": None})
        _, _, auto = self.member.get("/auto")
        self.assertIn("월 08:30 · 화 18:30 · 수 19:40 · 목 쉼 · 금 쉼", auto)

        # 빈 닉네임은 거부
        self.member.post("/profile", {"nickname": "   "})
        _, _, html = self.member.get("/auto")
        self.assertIn("닉네임을 입력해 주세요.", html)
        self.assertIn("민재짱", html)
        self.member.post("/profile", {"nickname": "민재"})

    # ---------------- PWA ---------------- #
    def test_16_pwa_manifest_service_worker_icons(self):
        guest = Client()
        status, headers, body = guest.get("/manifest.webmanifest")
        self.assertEqual(status, 200)
        self.assertIn("manifest+json", headers["Content-Type"])
        manifest = json.loads(body)
        self.assertEqual(manifest["name"], "온점핑 출석")
        self.assertEqual(manifest["short_name"], "온점핑")
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["theme_color"], "#5865f2")
        self.assertEqual(manifest["background_color"], "#f3f5f9")
        self.assertEqual({icon["sizes"] for icon in manifest["icons"]}, {"192x192", "512x512"})
        for icon in manifest["icons"]:
            self.assertTrue(icon["src"].startswith("/icons/"))
            self.assertEqual(icon["type"], "image/png")

        # 서비스 워커: 자바스크립트 타입 + install/activate/fetch 핸들러
        status, headers, body = guest.get("/sw.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        for marker in ("install", "activate", "fetch", "skipWaiting", "clients.claim"):
            self.assertIn(marker, body)
        self.assertEqual(headers.get("Service-Worker-Allowed"), "/")

        # 아이콘: 실제 PNG 200 응답
        for path in ("/icons/icon-192.png", "/icons/icon-512.png", "/icons/apple-touch-icon.png"):
            status, headers, data = guest.get(path, decode=False)
            self.assertEqual(status, 200, path)
            self.assertEqual(headers["Content-Type"], "image/png", path)
            self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"), path)
            self.assertGreater(len(data), 1000, path)

        status, _, _ = guest.get("/icons/nope.png")
        self.assertEqual(status, 404)

    def test_17_pwa_meta_tags_and_install_ui(self):
        expected = (
            '<link rel="manifest" href="/manifest.webmanifest">',
            '<meta name="theme-color" content="#5865f2">',
            '<meta name="apple-mobile-web-app-capable" content="yes">',
            '<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">',
            "navigator.serviceWorker.register('/sw.js')",
        )
        pages = [Client().get("/login")[2], self.member.get("/")[2],
                 self.member.get("/checkin")[2], self.admin.get("/admin/notices")[2]]
        for html in pages:
            for tag in expected:
                self.assertIn(tag, html)
            # 네비게이션 탭은 어느 화면에도 없다
            self.assertNotIn('class="tabs"', html)

        login = Client().get("/login")[2]
        home = self.member.get("/")[2]
        for html in (login, home):
            self.assertIn("앱 다운로드", html)
            self.assertIn('data-install-open', html)
            self.assertIn('id="install-modal"', html)
            self.assertIn("앱 설치하기 (PWA)", html)
            self.assertIn("beforeinstallprompt", html)

    # ---------------- 기타 ---------------- #
    def test_18_deleted_notice_stays_deleted_across_requests(self):
        for notice in store.list_notices():
            store.delete_notice(notice["id"])

        # 요청마다 init_db() 가 다시 도는데도 기본 공지가 되살아나지 않는다
        _, _, checkin = self.member.get("/checkin")
        self.assertIn("오늘 올라온 공지가 없습니다.", checkin)
        self.assertNotIn("출석 앱이 새로워졌어요", checkin)

        _, _, notices = self.member.get("/notices")
        self.assertNotIn("출석 앱이 새로워졌어요", notices)
        self.assertEqual(store.list_notices(), [])

        # 관리자 화면에도 공지가 없음으로 보인다
        _, _, admin_html = self.admin.get("/admin/notices")
        self.assertNotIn("출석 앱이 새로워졌어요", admin_html)

    def test_19_checkin_keeps_selected_slot_visible(self):
        _, _, before = self.member.get("/checkin")
        # 출석 전에는 고를 수 있는 첫 타임이 선택되어 있다
        self.assertIn('<option value="first" selected>08:30 첫째타임</option>', before)

        self.member.post("/checkin", {"slot_id": "third"})
        _, _, html = self.member.get("/checkin")

        # 이미 출석한 타임이 selected 로 남아 콤보가 빈칸으로 보이지 않는다
        self.assertIn('<option value="third" selected>18:30 셋째타임 (출석함)</option>', html)
        self.assertNotIn('<option value="third" selected disabled>', html)
        for slot_id in ("first", "second", "fourth"):
            self.assertIn(f'<option value="{slot_id}" disabled>', html)

        # 선택·버튼은 그대로 잠기고 중복 출석도 계속 막힌다
        self.assertIn('aria-label="출석 타임" disabled', html)
        self.assertIn(">오늘 출석 완료</button>", html)
        self.member.post("/checkin", {"slot_id": "first"})
        self.assertEqual(len([r for r in store.list_attendance(store.kst_today())
                              if r["username"] == "minjae"]), 1)

    def test_20_admin_closed_all_shows_badge_on_slot_cards(self):
        _, _, before = self.admin.get("/admin")
        self.assertEqual(before.count('<span class="badge off">전체 휴강</span>'), 0)

        self.admin.post("/admin/day", {"closed_all": "1"})
        _, _, html = self.admin.get("/admin")
        self.assertIn('<div class="banner">🚫 오늘은 전체 휴강입니다.', html)
        # 타임별 카드 4개 모두에 '전체 휴강' 배지
        self.assertEqual(html.count('<span class="badge off">전체 휴강</span>'), 4)
        for time_label in ("08:30", "09:30", "18:30", "19:40"):
            self.assertIn(time_label, html)

        self.admin.post("/admin/day", {})
        _, _, html = self.admin.get("/admin")
        self.assertEqual(html.count('<span class="badge off">전체 휴강</span>'), 0)

    def test_14_health_and_404(self):
        status, _, body = self.member.get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, "OK")
        status, _, html = self.member.get("/nope")
        self.assertEqual(status, 404)
        self.assertIn("페이지를 찾을 수 없습니다", html)
        status, _, html = self.admin.get("/admin/nope")
        self.assertEqual(status, 404)

    def test_15_no_passwords_in_store_or_pages(self):
        raw = store._backend().load_raw() or ""
        self.assertTrue(raw)
        self.assertNotIn(ADMIN_PASSWORD, raw)
        self.assertNotIn(MEMBER_PASSWORD, raw)
        self.assertIn("pbkdf2:sha256:", raw)

        pages = [self.member.get(p)[2] for p in ("/", "/notices", "/auto")]
        pages += [self.admin.get(p)[2] for p in ("/admin", "/admin/notices", "/admin/users")]
        pages.append(Client().get("/login")[2])
        for html in pages:
            self.assertNotIn(ADMIN_PASSWORD, html)
            self.assertNotIn(MEMBER_PASSWORD, html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
