"""store.py 저장소 테스트.

    python -m unittest discover tests -v

- 로컬 SQLite 문서 저장·재조회
- 저장이 겹쳤을 때(CAS 실패) 최신 문서로 다시 적용하는지
- KV_REST_API_URL / KV_REST_API_TOKEN 이 있는 환경에서는 실제 Upstash Redis 로
  같은 흐름을 한 번 더 확인한다(임시 키를 만들고 지운다).
"""

from __future__ import annotations

import os
import secrets
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SESSION_SECRET", secrets.token_hex(16))
os.environ.setdefault("ADMIN_PASSWORD", secrets.token_urlsafe(12))
os.environ["ONJUMPING_DATA_DIR"] = tempfile.mkdtemp(prefix="onjumping-store-test-")

import store  # noqa: E402

ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]


class _FlakyBackend:
    """첫 저장을 일부러 실패시켜 CAS 재시도를 확인한다."""

    def __init__(self, inner, fail_times: int = 1):
        self.inner = inner
        self.fail_times = fail_times
        self.name = "flaky"

    def load_raw(self):
        return self.inner.load_raw()

    def save_raw(self, old, new):
        if self.fail_times > 0:
            self.fail_times -= 1
            return False
        return self.inner.save_raw(old, new)


class StoreTest(unittest.TestCase):
    def setUp(self):
        store.reset_cache()
        store.init_db()

    def test_document_round_trip(self):
        user = store.create_user("storetest", secrets.token_urlsafe(8), "저장")
        day = store.kst_today()
        record = store.check_in(user["id"], day, "second")
        self.assertEqual(record["user_id"], user["id"])

        store.reset_cache()  # 다른 프로세스로 다시 읽는 것과 같다
        self.assertIsNotNone(store.get_user(user["id"]))
        self.assertEqual(len(store.list_attendance(day)), 1)

        store.delete_user(user["id"])
        store.reset_cache()
        self.assertIsNone(store.get_user(user["id"]))
        self.assertEqual(store.list_attendance(day), [])

    def test_ids_keep_increasing(self):
        first = store.create_user("seq1", secrets.token_urlsafe(8))
        second = store.create_user("seq2", secrets.token_urlsafe(8))
        self.assertLess(first["id"], second["id"])
        store.delete_user(first["id"])
        third = store.create_user("seq3", secrets.token_urlsafe(8))
        self.assertGreater(third["id"], second["id"])

    def test_cas_retry_applies_the_change_once(self):
        backend = _FlakyBackend(store._backend())
        with unittest.mock.patch.object(store, "_backend", return_value=backend):
            store.begin_request()
            try:
                user = store.create_user("casuser", secrets.token_urlsafe(8), "CAS")
                self.assertEqual(backend.fail_times, 0)
                self.assertIsNotNone(user)
                usernames = [u["username"] for u in store.list_users()]
                self.assertEqual(usernames.count("casuser"), 1)
            finally:
                store.end_request()
        store.delete_user(next(u for u in store.list_users() if u["username"] == "casuser")["id"])

    def test_day_controls_and_notices(self):
        day = store.kst_today()
        store.set_day(day, closed_all=False, blocked=["second"])
        store.reset_cache()
        state = store.get_day(day)
        self.assertEqual(state["blocked"], ["second"])
        self.assertFalse(state["slots"][1]["open"])
        self.assertTrue(state["slots"][2]["open"])

        store.set_day(day, closed_all=False, blocked=[])
        store.reset_cache()
        self.assertEqual(store.get_day(day)["blocked"], [])

        notice = store.set_notice("테스트 공지", "관리자")
        self.assertEqual(store.latest_notice()["body"], "테스트 공지")
        store.update_notice(notice["id"], "고친 공지")
        store.reset_cache()
        self.assertEqual(store.get_notice(notice["id"])["body"], "고친 공지")
        store.delete_notice(notice["id"])
        store.reset_cache()
        self.assertIsNone(store.get_notice(notice["id"]))

    def test_notice_image_round_trip(self):
        notice = store.set_notice("사진 공지", "관리자")
        self.assertEqual(notice["image_data"], "")

        image = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        store.update_notice(notice["id"], "사진 공지", image_data=image)
        store.reset_cache()
        self.assertEqual(store.get_notice(notice["id"])["image_data"], image)
        self.assertEqual(store.latest_notice()["image_data"], image)

        # image_data=None 이면 기존 이미지를 그대로 둔다
        store.update_notice(notice["id"], "본문만 고침")
        store.reset_cache()
        self.assertEqual(store.get_notice(notice["id"])["image_data"], image)

        # 빈 문자열이면 지운다
        store.update_notice(notice["id"], "사진 빠짐", image_data="")
        store.reset_cache()
        self.assertEqual(store.get_notice(notice["id"])["image_data"], "")
        self.assertNotIn("image_data", store._decode(store._backend().load_raw())["notices"][0])

        store.delete_notice(notice["id"])
        store.reset_cache()
        self.assertIsNone(store.get_notice(notice["id"]))

    def test_admin_password_comes_from_env_only(self):
        admin = store.authenticate("admin", ADMIN_PASSWORD)
        self.assertIsNotNone(admin)
        self.assertTrue(admin["is_admin"])
        self.assertIsNone(store.authenticate("admin", "wrong-password"))

        raw = store._backend().load_raw()
        self.assertNotIn(ADMIN_PASSWORD, raw)
        self.assertIn(store.ENV_PASSWORD_MARK, raw)


@unittest.skipUnless(os.environ.get("KV_REST_API_URL") and os.environ.get("KV_REST_API_TOKEN"),
                     "Upstash Redis 환경변수가 없어 실제 원격 저장소 테스트는 건너뜁니다")
class RedisStoreTest(unittest.TestCase):
    """실제 Upstash Redis REST 에 붙여 영구 저장·CAS 를 확인한다."""

    def setUp(self):
        self.key = f"onjumping:test:{secrets.token_hex(6)}"
        self._old_key = store.STATE_KEY
        store.STATE_KEY = self.key
        store.reset_cache()
        self.assertIsInstance(store._backend(), store._RedisBackend)
        store.init_db()

    def tearDown(self):
        store._backend()._command("DEL", self.key)
        store.STATE_KEY = self._old_key
        store.reset_cache()

    def test_persists_across_fresh_reads(self):
        user = store.create_user("remote", secrets.token_urlsafe(8), "원격")
        store.set_notice("원격 공지", "관리자")
        record = store.check_in(user["id"], store.kst_today(), "third")

        store.reset_cache()
        self.assertIsNotNone(store.get_user(user["id"]))
        self.assertEqual(store.latest_notice()["body"], "원격 공지")
        self.assertEqual(store.get_attendance(record["id"])["slot_id"], "third")

        # 저장소에 남은 원문을 직접 읽어 확인한다(다른 인스턴스가 보는 값)
        raw = store._RedisBackend(os.environ["KV_REST_API_URL"],
                                  os.environ["KV_REST_API_TOKEN"]).load_raw()
        self.assertIn("remote", raw)

    def test_cas_conflict_is_detected(self):
        backend = store._backend()
        raw = backend.load_raw()
        self.assertFalse(backend.save_raw("not-the-current-value", raw or "{}"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
