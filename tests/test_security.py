import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_api import Client, webapp_token, ADMIN_PASSWORD, MEMBER_PASSWORD  # noqa: E402
import store  # noqa: E402

import re  # noqa: E402
import time  # noqa: E402
import unittest  # noqa: E402
import webapp  # noqa: E402


class SecurityTest(unittest.TestCase):
    """CSRF·세션·보안 헤더의 회귀를 검증한다."""

    def setUp(self):
        self.admin = Client().login('admin', ADMIN_PASSWORD)
        self.member = Client().login('minjae', MEMBER_PASSWORD)

    def tearDown(self):
        for row in store.list_attendance(store.kst_today()):
            store.cancel_checkin(row["id"])

    def test_01_checkin_without_token_is_rejected(self):
        """토큰 없는 출석 요청은 거부하고 출석 행도 만들지 않는다."""
        slot_id = store.SLOTS[0]["id"]
        status, _, _ = self.member.request("POST", "/checkin", {"slot_id": slot_id})
        self.assertEqual(status, 403)
        rows = store.list_attendance(store.kst_today())
        self.assertFalse(any(row["user_id"] == self._member_id() for row in rows))

    def test_02_checkin_rejects_bad_tokens_and_accepts_valid_token(self):
        """잘못된 토큰과 타인 토큰은 막고 내 토큰만 허용한다."""
        slot_id = store.SLOTS[0]["id"]
        status, _, _ = self.member.request(
            "POST", "/checkin", {"slot_id": slot_id, "csrf": "0" * 32})
        self.assertEqual(status, 403)
        other = Client().login("doyun", MEMBER_PASSWORD)
        status, _, _ = self.member.request(
            "POST", "/checkin", {"slot_id": slot_id, "csrf": other.csrf})
        self.assertEqual(status, 403)
        self.assertFalse(any(row["user_id"] == self._member_id()
                             for row in store.list_attendance(store.kst_today())))
        status, _, _ = self.member.post("/checkin", {"slot_id": slot_id})
        self.assertEqual(status, 303)
        self.assertTrue(any(row["user_id"] == self._member_id()
                            for row in store.list_attendance(store.kst_today())))

    def test_03_admin_posts_without_token_do_not_mutate(self):
        """관리자의 토큰 없는 변경 요청은 저장소를 바꾸지 않는다."""
        today = store.kst_today()
        before_closed = store.get_day(today)["closed_all"]
        status, _, _ = self.admin.request("POST", "/admin/day", {"closed_all": "1"})
        self.assertEqual(status, 403)
        self.assertEqual(store.get_day(today)["closed_all"], before_closed)

        status, _, _ = self.admin.request("POST", "/admin/users", {
            "username": "csrf_nope", "password": "pass1234", "nickname": "거부됨"})
        self.assertEqual(status, 403)
        self.assertFalse(store.username_taken("csrf_nope"))

        before_body = self._today_notice_body()
        status, _, _ = self.admin.request(
            "POST", "/admin/notices", {"body": "CSRF 없는 공지"})
        self.assertEqual(status, 403)
        self.assertEqual(self._today_notice_body(), before_body)

    def test_04_logout_revokes_old_cookie(self):
        """로그아웃 후 서명이 유효한 예전 쿠키도 폐기된다."""
        old_cookie = self.member.cookies["onjumping_uid"]
        status, _, _ = self.member.post("/logout")
        self.assertEqual(status, 303)
        self.member.cookies["onjumping_uid"] = old_cookie
        status, headers, _ = self.member.get("/")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/login")

    def test_05_expired_tampered_and_old_cookies_are_guests(self):
        """만료·변조·버전 불일치·구형 쿠키는 모두 로그인으로 보낸다."""
        uid = self._member_id()
        sv = store.get_session_version(uid)
        past = int(time.time()) - 10
        future = int(time.time()) + webapp.SESSION_MAX_AGE
        expired_payload = f"v1.{uid}.{past}.{sv}"
        version_payload = f"v1.{uid}.{future}.{sv + 99}"
        cookies = [
            f"{expired_payload}.{webapp._sign(expired_payload)}",
            f"v1.{uid}.{future}.{sv}.deadbeef",
            f"{version_payload}.{webapp._sign(version_payload)}",
            f"{uid}.deadbeefdeadbeefdeadbeefdeadbeef",
        ]
        for value in cookies:
            with self.subTest(cookie=value):
                guest = Client()
                guest.cookies["onjumping_uid"] = value
                status, headers, _ = guest.get("/")
                self.assertEqual(status, 303)
                self.assertEqual(headers["Location"], "/login")

    def test_06_login_cookie_has_secure_attributes_and_v1_format(self):
        """로그인 쿠키의 형식·만료·보안 속성을 확인한다."""
        guest = Client()
        status, headers, _ = guest.post(
            "/login", {"username": "minjae", "password": MEMBER_PASSWORD})
        self.assertEqual(status, 303)
        cookie = guest.cookies["onjumping_uid"]
        self.assertTrue(cookie.startswith("v1."))
        self.assertEqual(len(cookie.split(".")), 5)
        emitted = headers["Set-Cookie"]
        for attribute in ("Max-Age=604800", "HttpOnly", "SameSite=Lax", "Path=/"):
            self.assertIn(attribute, emitted)

    def test_07_csrf_is_user_bound_and_changes_after_logout(self):
        """CSRF 토큰은 사용자마다 다르고 세션 폐기 후 갱신된다."""
        self.assertNotEqual(self.admin.csrf, self.member.csrf)
        old_token = self.member.csrf
        self.member.post("/logout")
        self.member.login("minjae", MEMBER_PASSWORD)
        self.assertTrue(self.member.csrf)
        self.assertNotEqual(old_token, self.member.csrf)

    def test_08_forms_have_csrf_and_scripts_share_response_nonce(self):
        """POST 폼 페이지는 토큰을 포함하고 스크립트와 CSP의 nonce가 같다."""
        for path in ("/", "/checkin", "/auto", "/admin", "/admin/notices", "/admin/users/new"):
            with self.subTest(path=path):
                status, headers, html = self.admin.get(path)
                self.assertEqual(status, 200)
                self.assertIn('method="POST"', html)
                self.assertIn('name="csrf"', html)
                match = re.search(r'<script nonce="([0-9a-f]+)"', html)
                self.assertIsNotNone(match)
                self.assertIn(f"'nonce-{match.group(1)}'", headers["Content-Security-Policy"])

    def test_09_security_headers_on_pages_redirects_and_404(self):
        """정상 페이지·리다이렉트·404에 같은 보안 헤더를 적용한다."""
        responses = [Client().get("/login"), Client().get("/"), self.admin.get("/not-found")]
        self.assertEqual([item[0] for item in responses], [200, 303, 404])
        for status, headers, _ in responses:
            with self.subTest(status=status):
                csp = headers["Content-Security-Policy"]
                self.assertIn("frame-ancestors 'none'", csp)
                self.assertIn("object-src 'none'", csp)
                self.assertIn("nonce-", csp)
                self.assertEqual(headers["X-Frame-Options"], "DENY")
                self.assertIn("Permissions-Policy", headers)
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(headers["Referrer-Policy"], "same-origin")

    def test_10_login_post_is_csrf_exempt(self):
        """로그인 POST는 토큰 없이 성공하고 v1 세션을 발급한다."""
        guest = Client()
        status, _, _ = guest.post(
            "/login", {"username": "minjae", "password": MEMBER_PASSWORD})
        self.assertEqual(status, 303)
        self.assertTrue(guest.cookies["onjumping_uid"].startswith("v1."))

    def test_11_every_state_changing_post_requires_token(self):
        member_id = self._member_id()
        before_mon = store.get_user(member_id)["weekly"]["mon"]
        cases = [
            (self.member, "/auto", {"mon": "first"}),
            (self.member, "/profile", {"nickname": "x"}),
            (self.member, "/checkin/cancel/1", {}),
            (self.admin, "/admin", {"closed_all": "1"}),
            (self.admin, "/admin/notices/999/delete", {}),
            (self.admin, "/admin/users/999/delete", {}),
            (self.member, "/logout", {}),
        ]
        for client, path, form in cases:
            with self.subTest(path=path):
                status, _, _ = client.request("POST", path, form)
                self.assertEqual(status, 403)
                if path == "/auto":
                    self.assertEqual(store.get_user(member_id)["weekly"]["mon"], before_mon)

    def _member_id(self):
        return next(user["id"] for user in store.list_users() if user["username"] == "minjae")

    def _today_notice_body(self):
        today = store.kst_today()
        notice = next((item for item in store.list_notices() if item["posted_on"] == today), None)
        return notice["body"] if notice else None


if __name__ == "__main__":
    unittest.main()
