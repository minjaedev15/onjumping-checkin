"""화면·동작 테스트 (HTTP 서버 없이 WSGI 앱을 직접 호출).

    python -m unittest discover tests -v

임시 SQLite 파일을 쓰고, 로그인 → 폼 전송 → 리다이렉트 → 화면 확인 순서로
실제 사용 흐름을 따라간다.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
import urllib.parse
from http.cookies import SimpleCookie
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["ONJUMPING_DATA_DIR"] = tempfile.mkdtemp(prefix="onjumping-test-")

import store  # noqa: E402
import webapp  # noqa: E402


class Client:
    """쿠키를 들고 다니는 최소 WSGI 클라이언트."""

    def __init__(self):
        self.cookies: dict[str, str] = {}

    def request(self, method: str, path: str, form: dict | None = None):
        body = urllib.parse.urlencode(form, doseq=True).encode("utf-8") if form else b""
        environ = {
            "REQUEST_METHOD": method.upper(),
            "PATH_INFO": path.split("?")[0],
            "QUERY_STRING": path.split("?", 1)[1] if "?" in path else "",
            "CONTENT_TYPE": "application/x-www-form-urlencoded",
            "CONTENT_LENGTH": str(len(body)),
            "wsgi.input": io.BytesIO(body),
            "HTTP_COOKIE": "; ".join(f"{k}={v}" for k, v in self.cookies.items()),
        }
        captured: dict = {}

        def start_response(status, headers):
            captured["status"] = int(status.split(" ")[0])
            captured["headers"] = headers

        raw = b"".join(webapp.app(environ, start_response))
        for name, value in captured["headers"]:
            if name.lower() != "set-cookie":
                continue
            for key, morsel in SimpleCookie(value).items():
                if morsel["max-age"] == "0" or morsel.value == "":
                    self.cookies.pop(key, None)
                else:
                    self.cookies[key] = morsel.value
        return captured["status"], dict(captured["headers"]), raw.decode("utf-8")

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, form=None):
        return self.request("POST", path, form or {})

    def login(self, username: str, password: str):
        status, headers, _ = self.post("/login", {"username": username, "password": password})
        assert status == 303 and headers.get("Location") == "/", (status, headers)
        return self


class AppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.init_db()

    def setUp(self):
        self.admin = Client().login("admin", "admin1234")
        self.member = Client().login("minjae", "1234")

    def cleanup_day(self):
        """테스트가 남긴 오늘 상태를 초기화한다."""
        store.set_day(store.kst_today(), closed_all=False, blocked=[])

    def tearDown(self):
        self.cleanup_day()

    # ---------------- 로그인 / 접근 ---------------- #
    def test_01_login_flow(self):
        guest = Client()
        status, headers, _ = guest.get("/")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/login")

        status, _, html = guest.get("/login")
        self.assertEqual(status, 200)
        self.assertIn("로그인", html)
        self.assertIn("minjae", html)

        status, _, html = guest.post("/login", {"username": "admin", "password": "nope"})
        self.assertEqual(status, 200)
        self.assertIn("아이디 또는 비밀번호가 틀렸습니다.", html)
        self.assertEqual(guest.cookies, {})

        status, headers, _ = guest.post("/login", {"username": "admin", "password": "admin1234"})
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/")
        self.assertIn("onjumping_uid", guest.cookies)

        status, _, html = guest.get("/")
        self.assertEqual(status, 200)
        self.assertIn("안녕하세요, 관리자님", html)
        self.assertIn('href="/admin"', html)
        self.assertIn("반가워요", html)

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
        status, headers, _ = self.member.get("/admin")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/")
        _, _, html = self.member.get("/")
        self.assertIn("관리자만 접근할 수 있어요.", html)
        self.assertNotIn('href="/admin"', html)

        status, _, _ = self.member.post("/admin/users", {"username": "sneak", "password": "1234"})
        self.assertEqual(status, 303)
        self.assertTrue(all(u["username"] != "sneak" for u in store.list_users()))

    # ---------------- 메인 화면 ---------------- #
    def test_04_main_layout(self):
        status, _, html = self.member.get("/")
        self.assertEqual(status, 200)
        self.assertIn("안녕하세요, 민재님", html)
        self.assertIn("년", html)
        self.assertIn("08:30", html)
        self.assertIn("09:30", html)
        self.assertIn("18:30", html)
        self.assertIn("19:40", html)
        self.assertIn("/ 11명", html)
        self.assertIn("📢 오늘의 공지", html)
        self.assertIn("✅ 출석 체크", html)
        self.assertIn("🗓️ 평일 자동 출석", html)
        self.assertIn('name="mon"', html)
        self.assertIn('name="fri"', html)
        self.assertNotIn('name="sat"', html)
        self.assertNotIn("대기", html)  # 대기열 기능은 제거됨

    # ---------------- 출석 ---------------- #
    def test_05_checkin_and_cancel(self):
        day = store.kst_today()
        before = store.get_day(day)["slots"][2]["taken"]

        status, headers, _ = self.member.post("/checkin", {"slot_id": "third"})
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/")
        self.assertEqual(store.get_day(day)["slots"][2]["taken"], before + 1)

        _, _, html = self.member.get("/")
        self.assertIn("18:30 출석 완료!", html)
        self.assertIn("(출석함)", html)

        # 같은 타임 중복
        self.member.post("/checkin", {"slot_id": "third"})
        _, _, html = self.member.get("/")
        self.assertIn("이미 18:30 타임에 출석했어요.", html)

        rows = [r for r in store.list_attendance(day) if r["username"] == "minjae"]
        self.assertEqual(len(rows), 1)
        status, _, html = self.member.post(f"/checkin/cancel/{rows[0]['id']}")
        self.assertEqual(status, 303)
        _, _, html = self.member.get("/")
        self.assertIn("출석을 취소했어요.", html)
        self.assertEqual(store.get_day(day)["slots"][2]["taken"], before)

    def test_06_cannot_cancel_someone_else(self):
        day = store.kst_today()
        self.member.post("/checkin", {"slot_id": "first"})
        record = next(r for r in store.list_attendance(day) if r["username"] == "minjae")

        other = Client().login("doyun", "1234")
        other.post(f"/checkin/cancel/{record['id']}")
        _, _, html = other.get("/")
        self.assertIn("본인 출석만 취소할 수 있어요.", html)
        self.assertEqual(len(store.list_attendance(day)), 1)

        self.admin.post(f"/checkin/cancel/{record['id']}")
        self.assertEqual(store.list_attendance(day), [])

    def test_07_full_slot_is_blocked(self):
        day = store.kst_today()
        made = [store.create_user(f"full{i}", "1234", f"가득{i}") for i in range(11)]
        try:
            for user in made:
                store.check_in(user["id"], day, "fourth")
            _, _, html = self.member.get("/")
            self.assertIn("(마감)", html)
            self.member.post("/checkin", {"slot_id": "fourth"})
            _, _, html = self.member.get("/")
            self.assertIn("19:40 타임은 정원이 다 찼어요", html)
        finally:
            for user in made:
                store.delete_user(user["id"])

    # ---------------- 휴강 / 타임 차단 ---------------- #
    def test_08_holiday_blocks_checkin(self):
        status, _, _ = self.admin.post("/admin/day", {"closed_all": "1"})
        self.assertEqual(status, 303)
        _, _, html = self.member.get("/")
        self.assertIn("🚫 오늘은 전체 휴강입니다.", html)
        self.assertIn("출석할 수 없습니다", html)

        self.member.post("/checkin", {"slot_id": "third"})
        _, _, html = self.member.get("/")
        self.assertIn("전체 휴강이라 출석할 수 없어요.", html)
        self.assertEqual(store.list_attendance(store.kst_today()), [])

        self.admin.post("/admin/day", {})
        _, _, html = self.member.get("/")
        self.assertNotIn("🚫 오늘은 전체 휴강입니다.", html)

    def test_09_slot_block_only_blocks_that_time(self):
        status, _, _ = self.admin.post("/admin/day", {"blocked": ["first", "second"]})
        self.assertEqual(status, 303)
        state = store.get_day(store.kst_today())
        self.assertEqual(sorted(state["blocked"]), ["first", "second"])

        _, _, html = self.member.get("/")
        self.assertIn("(휴강)", html)

        self.member.post("/checkin", {"slot_id": "first"})
        _, _, html = self.member.get("/")
        self.assertIn("08:30 타임은 오늘 휴강이에요.", html)

        status, _, _ = self.member.post("/checkin", {"slot_id": "third"})
        self.assertEqual(status, 303)
        self.assertEqual(store.get_day(store.kst_today())["slots"][2]["taken"], 1)

    # ---------------- 공지 ---------------- #
    def test_10_notice_register_and_delete(self):
        status, _, _ = self.admin.post("/admin/notice", {"body": "오늘 21시에 예약 글이 올라옵니다."})
        self.assertEqual(status, 303)
        _, _, html = self.member.get("/")
        self.assertIn("오늘 21시에 예약 글이 올라옵니다.", html)

        _, _, admin_html = self.admin.get("/admin")
        self.assertIn("오늘 21시에 예약 글이 올라옵니다.", admin_html)

        # 같은 날 다시 등록하면 내용만 바뀐다 (중복 카드 없음)
        self.admin.post("/admin/notice", {"body": "수정된 공지"})
        _, _, html = self.member.get("/")
        self.assertEqual(html.count("📢 오늘의 공지"), 1)
        self.assertIn("수정된 공지", html)

        self.admin.post("/admin/notice/delete")
        _, _, html = self.member.get("/")
        self.assertNotIn("📢 오늘의 공지", html)

        self.admin.post("/admin/notice", {"body": ""})
        _, _, admin_html = self.admin.get("/admin")
        self.assertIn("공지 내용을 입력해 주세요.", admin_html)

    # ---------------- 회원 관리 ---------------- #
    def test_11_admin_user_crud(self):
        status, _, _ = self.admin.post("/admin/users", {
            "username": "friend", "password": "1234", "nickname": "친구",
        })
        self.assertEqual(status, 303)
        _, _, admin_html = self.admin.get("/admin")
        self.assertIn("친구", admin_html)
        self.assertIn("@friend", admin_html)
        self.assertIn("👑 관리자", admin_html)

        friend = Client().login("friend", "1234")
        _, _, html = friend.get("/")
        self.assertIn("안녕하세요, 친구님", html)

        created = next(u for u in store.list_users() if u["username"] == "friend")
        self.admin.post(f"/admin/users/{created['id']}/delete")
        self.assertTrue(all(u["username"] != "friend" for u in store.list_users()))

        # 관리자 계정은 삭제되지 않는다
        admin_user = next(u for u in store.list_users() if u["username"] == "admin")
        self.admin.post(f"/admin/users/{admin_user['id']}/delete")
        self.assertTrue(store.get_user(admin_user["id"])["is_admin"])

        # 중복 아이디 / 짧은 비밀번호는 거부
        self.admin.post("/admin/users", {"username": "minjae", "password": "1234"})
        self.admin.post("/admin/users", {"username": "shorty", "password": "12"})
        self.assertTrue(all(u["username"] != "shorty" for u in store.list_users()))

    # ---------------- 내 설정 ---------------- #
    def test_12_nickname_and_weekly(self):
        status, _, _ = self.member.post("/profile", {"nickname": "민재짱"})
        self.assertEqual(status, 303)
        _, _, html = self.member.get("/")
        self.assertIn("안녕하세요, 민재짱님", html)

        self.member.post("/weekly", {"mon": "first", "tue": "third", "wed": "fourth", "thu": "", "fri": ""})
        user = store.authenticate("minjae", "1234")
        self.assertEqual(user["weekly"], {"mon": "first", "tue": "third", "wed": "fourth",
                                          "thu": None, "fri": None})
        _, _, html = self.member.get("/")
        self.assertIn("내 설정: 월 08:30 · 화 18:30 · 수 19:40 · 목 쉼 · 금 쉼", html)

        # 빈 닉네임은 거부
        self.member.post("/profile", {"nickname": "   "})
        _, _, html = self.member.get("/")
        self.assertIn("닉네임을 입력해 주세요.", html)
        self.assertIn("안녕하세요, 민재짱님", html)

    # ---------------- 기타 ---------------- #
    def test_13_health_and_404(self):
        status, _, body = self.member.get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, "OK")
        status, _, html = self.member.get("/nope")
        self.assertEqual(status, 404)
        self.assertIn("페이지를 찾을 수 없습니다", html)

    def test_14_passwords_are_hashed(self):
        with store.connect() as conn:
            rows = conn.execute("SELECT username, password_hash FROM users").fetchall()
        for row in rows:
            self.assertTrue(row["password_hash"].startswith("pbkdf2:sha256:"), row["username"])
            self.assertNotIn("admin1234", row["password_hash"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
