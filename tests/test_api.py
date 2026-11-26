"""API 스모크 테스트.

    python -m unittest tests.test_api -v

임시 SQLite 파일을 쓰고, server.Handler 를 임의 포트에 띄워 실제 HTTP 로 확인한다.
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="onjumping-test-")
os.environ["ONJUMPING_DATA_DIR"] = TMP

import server  # noqa: E402
import store  # noqa: E402


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.init_db()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    # ---------------- helpers ----------------
    def request(self, method, path, body=None, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        payload = json.dumps(body) if body is not None else None
        conn.request(method, path, body=payload, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        data = json.loads(raw) if raw else {}
        set_cookie = res.getheader("Set-Cookie") or ""
        conn.close()
        return res.status, data, set_cookie.split(";")[0]

    def login(self, user_id):
        status, data, cookie = self.request("POST", "/api/session", {"user_id": user_id})
        self.assertEqual(status, 200, data)
        return cookie

    # ---------------- tests ----------------
    def test_01_accounts_and_static(self):
        status, data, _ = self.request("GET", "/api/accounts")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["accounts"]), 3)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/")
        res = conn.getresponse()
        html = res.read().decode("utf-8")
        conn.close()
        self.assertEqual(res.status, 200)
        self.assertIn("출석 예약기", html)

    def test_02_slots_are_the_four_band_times(self):
        status, data, _ = self.request("GET", "/api/slots")
        self.assertEqual(status, 200)
        times = [(s["name"], s["time"], s["capacity"]) for s in data["slots"]]
        self.assertEqual(times, [
            ("첫째타임", "08:30", 11),
            ("둘째타임", "09:30", 11),
            ("셋째타임", "18:30", 11),
            ("넷째타임", "19:40", 11),
        ])

    def test_03_admin_bootstrap_has_management_data(self):
        cookie = self.login(1)
        status, data, _ = self.request("GET", "/api/bootstrap", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(data["session"]["role"], "admin")
        self.assertGreaterEqual(len(data["users"]), 3)
        self.assertGreaterEqual(len(data["notices"]), 1)
        self.assertEqual(len(data["day"]["slots"]), 4)
        self.assertEqual(data["status"]["slot_id"], "third")

    def test_04_admin_user_crud(self):
        cookie = self.login(1)
        status, data, _ = self.request("POST", "/api/users", {
            "name": "테스트유저", "target_slot": "fourth", "days": ["mon", "wed"],
        }, cookie=cookie)
        self.assertEqual(status, 201, data)
        uid = data["user"]["id"]
        self.assertEqual(data["user"]["target_slot_label"], "넷째타임 : 저녁 7시40분")

        status, data, _ = self.request("PATCH", f"/api/users/{uid}", {"target_slot": "first"}, cookie=cookie)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["user"]["target_slot"], "first")

        status, data, _ = self.request("DELETE", f"/api/users/{uid}", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(data["deleted"])
        self.assertIsNone(store.get_user(uid))

        status, _, _ = self.request("DELETE", "/api/users/1", cookie=cookie)
        self.assertEqual(status, 400)  # 관리자는 삭제 불가

    def test_05_notice_crud(self):
        cookie = self.login(1)
        status, data, _ = self.request("POST", "/api/notices", {
            "title": "테스트 공지", "body": "본문", "posted_on": date.today().isoformat(),
        }, cookie=cookie)
        self.assertEqual(status, 201, data)
        nid = data["notice"]["id"]
        self.assertEqual(data["notice"]["author"], "황보민재")

        status, data, _ = self.request("PATCH", f"/api/notices/{nid}", {"title": "수정 공지"}, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(data["notice"]["title"], "수정 공지")

        status, data, _ = self.request("GET", "/api/bootstrap", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(data["notice"]["title"], "수정 공지")

        status, _, _ = self.request("DELETE", f"/api/notices/{nid}", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertFalse(store.delete_notice(nid))

    def test_06_holiday_blocks_every_slot(self):
        admin = self.login(1)
        member = self.login(2)
        today = date.today().isoformat()

        status, data, _ = self.request("POST", "/api/day", {"date": today, "closed_all": True}, cookie=admin)
        self.assertEqual(status, 200)
        self.assertTrue(data["day"]["closed_all"])
        self.assertTrue(all(not s["open"] for s in data["day"]["slots"]))

        status, data, _ = self.request("POST", "/api/checkin", {"date": today, "slot_id": "third"}, cookie=member)
        self.assertEqual(status, 409)
        self.assertEqual(data["code"], "closed")

        self.request("POST", "/api/day", {"date": today, "closed_all": False}, cookie=admin)
        status, data, _ = self.request("GET", "/api/day?date=" + today, cookie=admin)
        self.assertEqual(status, 200)
        self.assertFalse(data["day"]["closed_all"])

    def test_07_partial_slot_block_and_checkin(self):
        admin = self.login(1)
        member = self.login(2)
        today = date.today().isoformat()

        for slot in ("first", "second"):
            status, data, _ = self.request("POST", "/api/day/slot", {
                "date": today, "slot_id": slot, "blocked": True,
            }, cookie=admin)
            self.assertEqual(status, 200, data)
        self.assertEqual(sorted(data["day"]["blocked"]), ["first", "second"])
        open_slots = [s["id"] for s in data["day"]["slots"] if s["open"]]
        self.assertEqual(open_slots, ["third", "fourth"])

        status, data, _ = self.request("POST", "/api/checkin", {"date": today, "slot_id": "first"}, cookie=member)
        self.assertEqual(status, 409)
        self.assertEqual(data["code"], "blocked")

        status, data, _ = self.request("POST", "/api/checkin", {"date": today, "slot_id": "third"}, cookie=member)
        self.assertEqual(status, 201, data)
        self.assertEqual(data["checkin"]["slot_id"], "third")
        self.assertEqual(data["day"]["slots"][2]["taken"], 1)

        status, data, _ = self.request("POST", "/api/checkin", {"date": today, "slot_id": "third"}, cookie=member)
        self.assertEqual(status, 409)
        self.assertEqual(data["code"], "duplicate")

        status, data, _ = self.request("POST", "/api/day/slot", {
            "date": today, "slot_id": "first", "blocked": False,
        }, cookie=admin)
        self.assertEqual(status, 200)
        self.assertNotIn("first", data["day"]["blocked"])

    def test_08_member_cannot_use_admin_apis(self):
        member = self.login(2)
        today = date.today().isoformat()
        status, _, _ = self.request("POST", "/api/users", {"name": "몰래"}, cookie=member)
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/notices", {"title": "몰래"}, cookie=member)
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/day", {"date": today, "closed_all": True}, cookie=member)
        self.assertEqual(status, 403)
        status, _, _ = self.request("GET", "/api/config", cookie=member)
        self.assertEqual(status, 403)
        status, data, _ = self.request("GET", "/api/bootstrap", cookie=member)
        self.assertEqual(status, 200)
        self.assertNotIn("notices", data)
        self.assertEqual(len(data["users"]), 1)

    def test_09_login_required(self):
        status, _, _ = self.request("GET", "/api/bootstrap")
        self.assertEqual(status, 200)  # 로그인 화면용 최소 응답
        status, _, _ = self.request("GET", "/api/users")
        self.assertEqual(status, 401)

    def test_11_nickname_and_weekly(self):
        member = self.login(2)
        status, data, _ = self.request("POST", "/api/profile", {"nickname": "도윤이"}, cookie=member)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["session"]["nickname"], "도윤이")
        weekly = {"mon": "third", "tue": "third", "wed": "fourth",
                  "thu": None, "fri": None}
        status, data, _ = self.request("POST", "/api/profile", {"weekly": weekly}, cookie=member)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["session"]["weekly"]["wed"], "fourth")
        self.assertIsNone(data["session"]["weekly"]["fri"])

        admin = self.login(1)
        status, data, _ = self.request("GET", "/api/status", cookie=admin)
        self.assertIsNotNone(data["next_run_at"])
        self.assertEqual(len(data["weekly_slots"]), 4)

    def test_12_waitlist_and_shake(self):
        admin = self.login(1)
        day = date.today().isoformat()
        for slot in ("first", "second", "third", "fourth"):
            self.request("POST", "/api/day/slot", {"date": day, "slot_id": slot, "blocked": False}, cookie=admin)
        member = self.login(3)
        # 자리가 남아 있으면 대기 불가
        status, data, _ = self.request("POST", "/api/waitlist", {"date": day, "slot_id": "second"}, cookie=member)
        self.assertEqual(status, 409)
        self.assertEqual(data["code"], "open")

        # 만석으로 만든 뒤 대기 신청
        for i in range(11):
            store.add_checkin(1, day, "second", status="voted", taken=i + 1, capacity=11)
        status, data, _ = self.request("POST", "/api/waitlist", {"date": day, "slot_id": "second"}, cookie=member)
        self.assertEqual(status, 201, data)
        self.assertGreaterEqual(data["day"]["slots"][1]["waiting"], 1)
        status, data, _ = self.request("DELETE", "/api/waitlist", {"date": day, "slot_id": "second"}, cookie=member)
        self.assertEqual(status, 200)
        self.assertTrue(data["left"])

        status, data, _ = self.request("POST", "/api/shake", {"date": day, "flavor": "쿠키"}, cookie=member)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["shake"]["flavor"], "쿠키")
        status, data, _ = self.request("POST", "/api/shake", {"date": day, "flavor": "없는맛"}, cookie=member)
        self.assertEqual(status, 400)

    def test_13_checkin_cancel(self):
        member = self.login(3)
        day = date.today().isoformat()
        slot = "fourth" if not store.get_day(day)["slots"][3]["full"] else "third"
        status, _, _ = self.request("POST", "/api/checkin", {"date": day, "slot_id": slot}, cookie=member)
        self.assertEqual(status, 201)
        status, data, _ = self.request("POST", "/api/checkin/cancel", {"date": day, "slot_id": slot}, cookie=member)
        self.assertEqual(status, 200)
        self.assertTrue(data["cancelled"])

    def test_10_capacity_full(self):
        admin = self.login(1)
        day = date.today().isoformat()
        slot = "fourth"
        for i in range(11):
            store.add_checkin(1, day, slot, status="voted", taken=i + 1, capacity=11)
        status, data, _ = self.request("GET", "/api/day?date=" + day, cookie=admin)
        fourth = next(s for s in data["day"]["slots"] if s["id"] == slot)
        self.assertTrue(fourth["full"])
        member = self.login(3)
        status, data, _ = self.request("POST", "/api/checkin", {"date": day, "slot_id": slot}, cookie=member)
        self.assertEqual(status, 409)
        self.assertEqual(data["code"], "full")


if __name__ == "__main__":
    unittest.main(verbosity=2)
