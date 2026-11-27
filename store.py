"""온점핑 출석 데이터 계층.

저장소는 두 가지이고, 위의 앱 로직은 어느 쪽에서도 똑같이 동작한다.

    프로덕션  Upstash Redis REST (Vercel Storage)
              KV_REST_API_URL / KV_REST_API_TOKEN
              (UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN 도 인식)
    로컬      SQLite (data/onjumping.db)

두 저장소 모두 앱 상태 전체를 하나의 JSON 문서로 보관한다. 문서를 읽은 뒤 원문
문자열을 조건으로 걸어 저장(CAS)하므로, 두 요청이 동시에 같은 문서를 고쳐도
한쪽만 반영되고 다른 쪽은 최신 문서를 다시 읽어 같은 변경을 다시 적용한다.

문서 구조

    schema         문서 형식 버전
    revision       저장할 때마다 1씩 증가
    seq            users / attendance / notices 의 다음 id
    users          [{id, username, password_hash, nickname, is_admin, weekly, created_at}]
    attendance     [{id, user_id, day, slot_id, created_at}]
    notices        [{id, body, image_data, posted_on, author, created_at, updated_at}]
                   image_data 는 선택 항목이며 `data:image/...;base64,...` 형식의 문자열이다.
    day_controls   {day: {closed_all, note, updated_at}}
    slot_blocks    {day: [slot_id, ...]}
    settings       {secret: ..., notice_seeded: true}
                   secret 은 SESSION_SECRET 환경변수가 없을 때만 만든다.
                   notice_seeded 는 기본 공지를 이미 한 번 넣었다는 표시다.

관리자 계정의 비밀번호는 문서에 저장하지 않는다. `admin` 로그인은 서버 환경변수
ADMIN_PASSWORD(또는 ADMIN_PASSWORD_HASH)로만 검증되고, 저장소에는 관리자 계정의
존재(명단·출석 기록용)만 남는다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("ONJUMPING_DATA_DIR") or (ROOT / "data"))
DB_PATH = DATA_DIR / "onjumping.db"

KST = timezone(timedelta(hours=9))

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_KO = {"mon": "월", "tue": "화", "wed": "수", "thu": "목", "fri": "금", "sat": "토", "sun": "일"}
WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri"]

# 밴드에 올라오는 실제 타임표 (선착순 11명)
SLOTS = [
    {"id": "first", "name": "첫째타임", "time": "08:30", "label": "첫째타임 : 오전 8시30분", "capacity": 11},
    {"id": "second", "name": "둘째타임", "time": "09:30", "label": "둘째타임 : 오전 9시30분", "capacity": 11},
    {"id": "third", "name": "셋째타임", "time": "18:30", "label": "셋째타임 : 저녁 6시30분", "capacity": 11},
    {"id": "fourth", "name": "넷째타임", "time": "19:40", "label": "넷째타임 : 저녁 7시40분", "capacity": 11},
]
SLOT_BY_ID = {s["id"]: s for s in SLOTS}
DEFAULT_SLOT_ID = "third"

DEFAULT_WEEKLY = {"mon": "third", "tue": "third", "wed": "third", "thu": "third", "fri": "third"}

SCHEMA_VERSION = 3
STATE_KEY = os.environ.get("ONJUMPING_STATE_KEY") or "onjumping:state:v1"

ADMIN_USERNAME = "admin"
ADMIN_NICKNAME = "관리자"
ENV_PASSWORD_MARK = "!env"  # 이 값이면 비밀번호는 환경변수로만 검증한다.

SEED_NOTICE = "온점핑 출석 앱이 새로워졌어요.\n타임은 08:30 · 09:30 · 18:30 · 19:40, 타임마다 11명 선착순입니다."

REDIS_URL_VARS = ("KV_REST_API_URL", "UPSTASH_REDIS_REST_URL", "ONJUMPING_REDIS_REST_URL")
REDIS_TOKEN_VARS = ("KV_REST_API_TOKEN", "UPSTASH_REDIS_REST_TOKEN", "ONJUMPING_REDIS_REST_TOKEN")


class StoreError(Exception):
    """저장소를 쓸 수 없을 때."""


class StoreBusy(StoreError):
    """같은 문서를 동시에 고쳐 몇 번 다시 시도해도 반영하지 못했을 때."""


class CheckinError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _clean_text(text: str) -> str:
    """폼/textarea 에서 올 수 있는 CRLF 를 LF 로 맞춘다."""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def kst_today() -> str:
    return datetime.now(KST).date().isoformat()


# --------------------------------------------------------------------------- #
# 저장소 백엔드
# --------------------------------------------------------------------------- #

class _SqliteBackend:
    """로컬 개발·테스트용. 한 행짜리 state 테이블에 JSON 을 넣는다."""

    name = "sqlite"

    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            yield conn
        finally:
            conn.close()

    def load_raw(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM state WHERE key = ?", (STATE_KEY,)).fetchone()
        return row[0] if row else None

    def save_raw(self, old: str | None, new: str) -> bool:
        """old 와 지금 저장된 값이 같을 때만 new 로 바꾼다."""
        with self._connect() as conn:
            if old is None:
                cur = conn.execute("INSERT OR IGNORE INTO state (key, value) VALUES (?, ?)", (STATE_KEY, new))
            else:
                cur = conn.execute(
                    "UPDATE state SET value = ? WHERE key = ? AND value = ?", (new, STATE_KEY, old)
                )
            return cur.rowcount > 0


CAS_SCRIPT = """
local cur = redis.call('GET', KEYS[1])
local old = ARGV[1]
if not cur then
  if old ~= '' then return 0 end
else
  if cur ~= old then return 0 end
end
redis.call('SET', KEYS[1], ARGV[2])
return 1
"""


class _RedisBackend:
    """Upstash Redis REST. 표준 라이브러리(urllib)만 쓴다."""

    name = "redis"

    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.token = token

    def _command(self, *args):
        body = json.dumps(list(args), ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise StoreError(f"저장소에 연결하지 못했습니다: {exc}") from exc
        except ValueError as exc:
            raise StoreError("저장소 응답을 읽지 못했습니다.") from exc
        if isinstance(payload, dict) and payload.get("error"):
            raise StoreError(f"저장소 오류: {payload['error']}")
        return payload.get("result") if isinstance(payload, dict) else None

    def load_raw(self) -> str | None:
        value = self._command("GET", STATE_KEY)
        return value if isinstance(value, str) else None

    def save_raw(self, old: str | None, new: str) -> bool:
        result = self._command("EVAL", CAS_SCRIPT, "1", STATE_KEY, old or "", new)
        return result == 1


_BACKENDS: dict[tuple, object] = {}


def _env(*names: str) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _backend():
    url = _env(*REDIS_URL_VARS)
    token = _env(*REDIS_TOKEN_VARS)
    if url and token:
        key = ("redis", url, token)
        if key not in _BACKENDS:
            _BACKENDS[key] = _RedisBackend(url, token)
        return _BACKENDS[key]
    if url or token:
        raise StoreError("영구 저장소 설정이 일부만 있습니다(KV_REST_API_URL / KV_REST_API_TOKEN).")
    key = ("sqlite", str(DB_PATH))
    if key not in _BACKENDS:
        _BACKENDS[key] = _SqliteBackend(DB_PATH)
    return _BACKENDS[key]


def backend_name() -> str:
    return _backend().name


def reset_cache() -> None:
    """저장소 종류나 설정이 바뀌었을 때(주로 테스트) 캐시를 비운다."""
    _BACKENDS.clear()
    begin_request()
    end_request()


# --------------------------------------------------------------------------- #
# 요청 단위 캐시
# --------------------------------------------------------------------------- #

_CACHE = threading.local()


def begin_request() -> None:
    _CACHE.active = True
    _CACHE.loaded = False
    _CACHE.raw = None


def end_request() -> None:
    _CACHE.active = False
    _CACHE.loaded = False
    _CACHE.raw = None


def _load_raw(fresh: bool = False) -> str | None:
    active = getattr(_CACHE, "active", False)
    if active and not fresh and getattr(_CACHE, "loaded", False):
        return _CACHE.raw
    raw = _backend().load_raw()
    if active:
        _CACHE.raw, _CACHE.loaded = raw, True
    return raw


def _remember(raw: str | None, new: str) -> None:
    if getattr(_CACHE, "active", False):
        _CACHE.raw, _CACHE.loaded = new, True


def _blank_state() -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "revision": 0,
        "seq": {"user": 1, "attendance": 1, "notice": 1},
        "users": [],
        "attendance": [],
        "notices": [],
        "day_controls": {},
        "slot_blocks": {},
        "settings": {},
    }


def _decode(raw: str | None) -> dict:
    if not raw:
        return _blank_state()
    try:
        doc = json.loads(raw)
    except ValueError:
        doc = _blank_state()
    if not isinstance(doc, dict):
        doc = _blank_state()
    for key, value in _blank_state().items():
        doc.setdefault(key, value)
    return doc


def _read(fn):
    """fn(doc) -> 값 또는 (값, 바뀌었는지). 읽기 전용이라 뒤쪽은 버린다."""
    result = fn(_decode(_load_raw()))
    return result[0] if isinstance(result, tuple) else result


def _mutate(fn):
    """fn(doc) -> (결과, 바뀌었는지). 저장이 겹치면 최신 문서로 다시 적용한다."""
    backend = _backend()
    for attempt in range(6):
        raw = _load_raw(fresh=attempt > 0)
        doc = _decode(raw)
        result, changed = fn(doc)
        if not changed:
            return result
        doc["revision"] = int(doc.get("revision") or 0) + 1
        new = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if backend.save_raw(raw, new):
            _remember(raw, new)
            return result
        time.sleep(0.03 + 0.04 * attempt)
    raise StoreBusy("저장이 잠시 겹쳤어요. 다시 시도해 주세요.")


# --------------------------------------------------------------------------- #
# 비밀번호 / 세션 키
# --------------------------------------------------------------------------- #

PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS)
    return f"pbkdf2:sha256:{PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def check_password(password_hash: str, password: str) -> bool:
    try:
        algo, salt, hexed = password_hash.split("$")
        _, digest_name, iterations = algo.split(":")
        digest = hashlib.pbkdf2_hmac(digest_name, password.encode("utf-8"), salt.encode("utf-8"), int(iterations))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(digest.hex(), hexed)


def get_secret() -> str:
    """세션 쿠키 서명 키. SESSION_SECRET 이 있으면 그것을 쓴다."""
    env_secret = _env("SESSION_SECRET")
    if env_secret:
        return env_secret

    def fn(doc):
        stored = doc["settings"].get("secret")
        if stored:
            return stored, False
        created = secrets.token_hex(32)
        doc["settings"]["secret"] = created
        return created, True

    return _mutate(fn)


# --------------------------------------------------------------------------- #
# 관리자 계정 (비밀번호는 환경변수에서만)
# --------------------------------------------------------------------------- #

def admin_password_configured() -> bool:
    return bool(_env("ADMIN_PASSWORD") or _env("ADMIN_PASSWORD_HASH"))


def _admin_password_ok(password: str) -> bool:
    password_hash = _env("ADMIN_PASSWORD_HASH")
    if password_hash:
        return check_password(password_hash, password)
    configured = _env("ADMIN_PASSWORD")
    if not configured:
        return False
    return hmac.compare_digest(configured.encode("utf-8"), (password or "").encode("utf-8"))


def _find_user(doc: dict, user_id: int | None) -> dict | None:
    if not user_id:
        return None
    return next((u for u in doc["users"] if u.get("id") == user_id), None)


def _find_username(doc: dict, username: str) -> dict | None:
    return next((u for u in doc["users"] if u.get("username") == username), None)


def _public(user: dict) -> dict:
    weekly = user.get("weekly") or {}
    return {
        "id": user["id"],
        "username": user["username"],
        "nickname": user.get("nickname") or user["username"],
        "is_admin": bool(user.get("is_admin")),
        "weekly": {d: (weekly.get(d) if weekly.get(d) in SLOT_BY_ID else None) for d in WEEKDAY_KEYS},
        "created_at": user.get("created_at", ""),
    }


def _next_id(doc: dict, name: str) -> int:
    value = int((doc.get("seq") or {}).get(name) or 1)
    doc["seq"][name] = value + 1
    return value


def _add_user(doc: dict, username: str, password_hash: str, nickname: str = "", is_admin: bool = False) -> dict:
    user = {
        "id": _next_id(doc, "user"),
        "username": username,
        "password_hash": password_hash,
        "nickname": nickname or username,
        "is_admin": bool(is_admin),
        "weekly": dict(DEFAULT_WEEKLY),
        "created_at": _now(),
    }
    doc["users"].append(user)
    return user


def _ensure_admin(doc: dict) -> bool:
    """관리자 계정 행을 준비한다. 비밀번호는 환경변수에만 둔다."""
    changed = False
    user = _find_username(doc, ADMIN_USERNAME)
    if user is None:
        if admin_password_configured():
            password_hash = ENV_PASSWORD_MARK
        else:
            # 로컬 개발: 한 번만 임시 비밀번호를 만들어 알려 준다. 코드에는 남기지 않는다.
            temporary = secrets.token_urlsafe(9)
            password_hash = hash_password(temporary)
            if not os.environ.get("VERCEL"):
                print(
                    f"[onjumping] ADMIN_PASSWORD 가 없어 관리자 임시 비밀번호를 만들었습니다: "
                    f"{ADMIN_USERNAME} / {temporary}",
                    file=sys.stderr,
                )
        _add_user(doc, ADMIN_USERNAME, password_hash, ADMIN_NICKNAME, is_admin=True)
        return True

    if not user.get("is_admin"):
        user["is_admin"] = True
        changed = True
    if user.get("nickname") != ADMIN_NICKNAME:
        user["nickname"] = ADMIN_NICKNAME
        changed = True
    if admin_password_configured() and user.get("password_hash") != ENV_PASSWORD_MARK:
        user["password_hash"] = ENV_PASSWORD_MARK
        changed = True
    if not admin_password_configured() and user.get("password_hash") == ENV_PASSWORD_MARK:
        temporary = secrets.token_urlsafe(9)
        user["password_hash"] = hash_password(temporary)
        changed = True
        if not os.environ.get("VERCEL"):
            print(
                f"[onjumping] ADMIN_PASSWORD 가 없어 관리자 임시 비밀번호를 다시 만들었습니다: "
                f"{ADMIN_USERNAME} / {temporary}",
                file=sys.stderr,
            )
    return changed


def _ensure_notice(doc: dict) -> bool:
    """기본 공지는 저장소 수명 동안 딱 한 번만 넣는다.

    `init_db()` 는 요청마다 불리므로, 관리자가 공지를 모두 지운 뒤 다음 요청에서
    기본 공지가 되살아나면 안 된다. 그래서 시드 여부를 settings["notice_seeded"]
    에 남기고, 이 플래그가 없던 예전 문서는 이미 만들어 둔 공지 번호
    (seq.notice > 1)로 판단한다.
    """
    settings = doc.setdefault("settings", {})
    changed = False
    if not settings.get("notice_seeded"):
        settings["notice_seeded"] = True
        changed = True
    if doc["notices"] or not SEED_NOTICE:
        return changed
    already_seeded = int((doc.get("seq") or {}).get("notice") or 1) > 1
    if already_seeded:
        return changed
    now = _now()
    doc["notices"].append({
        "id": _next_id(doc, "notice"),
        "body": SEED_NOTICE,
        "posted_on": kst_today(),
        "author": ADMIN_NICKNAME,
        "created_at": now,
        "updated_at": now,
    })
    return True


def init_db(seed: bool = True) -> None:
    def fn(doc):
        changed = False
        if doc.get("schema") != SCHEMA_VERSION:
            doc["schema"] = SCHEMA_VERSION
            changed = True
        if seed:
            if _ensure_admin(doc):
                changed = True
            if _ensure_notice(doc):
                changed = True
        return None, changed

    _mutate(fn)


# --------------------------------------------------------------------------- #
# users
# --------------------------------------------------------------------------- #

def list_users() -> list[dict]:
    return _read(lambda doc: [_public(u) for u in
                              sorted(doc["users"], key=lambda u: (not u.get("is_admin"), u.get("username", "")))])


def get_user(user_id: int | None) -> dict | None:
    user = _read(lambda doc: _find_user(doc, user_id))
    return _public(user) if user else None


def authenticate(username: str, password: str) -> dict | None:
    username = (username or "").strip().lower()
    if not username or not password:
        return None

    if username == ADMIN_USERNAME:
        if not _admin_password_ok(password):
            return None
        return _admin_login_user()

    def fn(doc):
        user = _find_username(doc, username)
        if user is None or user.get("password_hash") == ENV_PASSWORD_MARK:
            return None, False
        if not check_password(user.get("password_hash", ""), password):
            return None, False
        return _public(user), False

    return _read(fn)


def _admin_login_user() -> dict | None:
    def fn(doc):
        user = _find_username(doc, ADMIN_USERNAME)
        if user is None:
            _ensure_admin(doc)
            user = _find_username(doc, ADMIN_USERNAME)
            return (_public(user) if user else None), True
        return _public(user), False

    return _mutate(fn)


def username_taken(username: str) -> bool:
    username = (username or "").strip().lower()
    return _read(lambda doc: _find_username(doc, username) is not None)


def create_user(username: str, password: str, nickname: str = "", is_admin: bool = False) -> dict:
    username = (username or "").strip().lower()

    def fn(doc):
        if _find_username(doc, username) is not None:
            raise CheckinError("duplicate_user", "이미 존재하는 아이디입니다.")
        user = _add_user(doc, username, hash_password(password), (nickname or "").strip()[:20], is_admin)
        return _public(user), True

    return _mutate(fn)


def set_nickname(user_id: int, nickname: str) -> dict | None:
    nickname = (nickname or "").strip()[:20]
    if not nickname:
        return get_user(user_id)

    def fn(doc):
        user = _find_user(doc, user_id)
        if user is None:
            return None, False
        user["nickname"] = nickname
        return _public(user), True

    return _mutate(fn)


def set_weekly(user_id: int, weekly: dict) -> dict | None:
    clean = {d: (weekly.get(d) if weekly.get(d) in SLOT_BY_ID else None) for d in WEEKDAY_KEYS}

    def fn(doc):
        user = _find_user(doc, user_id)
        if user is None:
            return None, False
        user["weekly"] = clean
        return _public(user), True

    return _mutate(fn)


def delete_user(user_id: int) -> bool:
    def fn(doc):
        user = _find_user(doc, user_id)
        if user is None or user.get("is_admin"):
            return False, False
        doc["users"] = [u for u in doc["users"] if u["id"] != user_id]
        doc["attendance"] = [a for a in doc["attendance"] if a.get("user_id") != user_id]
        return True, True

    return _mutate(fn)


# --------------------------------------------------------------------------- #
# notices
# --------------------------------------------------------------------------- #

def _notice_public(notice: dict) -> dict:
    return {
        "id": notice["id"],
        "body": notice["body"],
        "image_data": notice.get("image_data") or "",
        "posted_on": notice.get("posted_on", ""),
        "author": notice.get("author", ""),
        "updated_at": notice.get("updated_at", ""),
    }


def latest_notice(today: str | None = None) -> dict | None:
    today = today or kst_today()

    def fn(doc):
        found = [n for n in doc["notices"] if n.get("posted_on", "") <= today]
        if not found:
            return None, False
        latest = max(found, key=lambda n: (n.get("posted_on", ""), n.get("id", 0)))
        return _notice_public(latest), False

    return _read(fn)


def list_notices() -> list[dict]:
    def fn(doc):
        ordered = sorted(doc["notices"], key=lambda n: (n.get("posted_on", ""), n.get("id", 0)), reverse=True)
        return [_notice_public(n) for n in ordered], False

    return _read(fn)


def get_notice(notice_id: int) -> dict | None:
    def fn(doc):
        found = next((n for n in doc["notices"] if n.get("id") == notice_id), None)
        return (_notice_public(found) if found else None), False

    return _read(fn)


def _apply_image(notice: dict, image_data: str | None) -> None:
    """이미지 변경. None 이면 그대로 두고, 빈 문자열이면 지운다."""
    if image_data is None:
        return
    if image_data:
        notice["image_data"] = image_data
    else:
        notice.pop("image_data", None)


def set_notice(body: str, author: str, posted_on: str | None = None,
               image_data: str | None = None) -> dict:
    """오늘 공지를 등록한다. 같은 날짜 공지가 이미 있으면 내용만 바꾼다.

    image_data 가 None 이면 기존 이미지를 그대로 두고, 빈 문자열이면 지운다.
    """
    posted_on = posted_on or kst_today()
    body = _clean_text(body)

    def fn(doc):
        same_day = [n for n in doc["notices"] if n.get("posted_on") == posted_on]
        now = _now()
        if same_day:
            notice = max(same_day, key=lambda n: n.get("id", 0))
            notice["body"] = body
            notice["author"] = author
            _apply_image(notice, image_data)
            notice["updated_at"] = now
            return _notice_public(notice), True
        notice = {
            "id": _next_id(doc, "notice"),
            "body": body,
            "posted_on": posted_on,
            "author": author,
            "created_at": now,
            "updated_at": now,
        }
        _apply_image(notice, image_data)
        doc["notices"].append(notice)
        return _notice_public(notice), True

    return _mutate(fn)


def update_notice(notice_id: int, body: str, image_data: str | None = None) -> dict | None:
    body = _clean_text(body)

    def fn(doc):
        notice = next((n for n in doc["notices"] if n.get("id") == notice_id), None)
        if notice is None or not body:
            return None, False
        notice["body"] = body
        _apply_image(notice, image_data)
        notice["updated_at"] = _now()
        return _notice_public(notice), True

    return _mutate(fn)


def delete_notice(notice_id: int) -> bool:
    def fn(doc):
        before = len(doc["notices"])
        doc["notices"] = [n for n in doc["notices"] if n.get("id") != notice_id]
        return (len(doc["notices"]) != before), (len(doc["notices"]) != before)

    return _mutate(fn)


# --------------------------------------------------------------------------- #
# 휴강 / 타임 차단
# --------------------------------------------------------------------------- #

def _count_slots(doc: dict, day: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in doc["attendance"]:
        if record.get("day") == day:
            counts[record["slot_id"]] = counts.get(record["slot_id"], 0) + 1
    return counts


def _day_state(doc: dict, day: str) -> dict:
    control = (doc.get("day_controls") or {}).get(day) or {}
    blocked = list((doc.get("slot_blocks") or {}).get(day) or [])
    counts = _count_slots(doc, day)
    closed_all = bool(control.get("closed_all"))
    slots = []
    for slot in SLOTS:
        taken = counts.get(slot["id"], 0)
        slots.append({
            **slot,
            "blocked": slot["id"] in blocked,
            "taken": taken,
            "full": taken >= slot["capacity"],
            "open": (not closed_all) and slot["id"] not in blocked and taken < slot["capacity"],
        })
    return {
        "date": day,
        "closed_all": closed_all,
        "note": control.get("note", ""),
        "blocked": blocked,
        "slots": slots,
    }


def get_day(day: str) -> dict:
    return _read(lambda doc: (_day_state(doc, day), False))


def set_day(day: str, closed_all: bool = False, blocked: list[str] | None = None, note: str | None = None) -> dict:
    blocked = [s for s in (blocked or []) if s in SLOT_BY_ID]

    def fn(doc):
        controls = doc.setdefault("day_controls", {})
        control = controls.get(day) or {}
        control["closed_all"] = bool(closed_all)
        if note is not None:
            control["note"] = note
        control["note"] = control.get("note", "")
        control["updated_at"] = _now()
        controls[day] = control
        blocks = doc.setdefault("slot_blocks", {})
        if blocked:
            blocks[day] = blocked
        else:
            blocks.pop(day, None)
        return _day_state(doc, day), True

    return _mutate(fn)


# --------------------------------------------------------------------------- #
# attendance
# --------------------------------------------------------------------------- #

def _attendance_public(doc: dict, record: dict) -> dict:
    user = _find_user(doc, record.get("user_id")) or {}
    return {
        "id": record["id"],
        "user_id": record["user_id"],
        "day": record["day"],
        "slot_id": record["slot_id"],
        "nickname": user.get("nickname") or user.get("username") or "",
        "username": user.get("username", ""),
        "created_at": record.get("created_at", ""),
    }


def get_attendance(attendance_id: int) -> dict | None:
    def fn(doc):
        record = next((a for a in doc["attendance"] if a.get("id") == attendance_id), None)
        return (_attendance_public(doc, record) if record else None), False

    return _read(fn)


def list_attendance(day: str) -> list[dict]:
    def fn(doc):
        records = [a for a in doc["attendance"] if a.get("day") == day]
        records.sort(key=lambda a: (a.get("created_at", ""), a.get("id", 0)))
        return [_attendance_public(doc, a) for a in records], False

    return _read(fn)


def check_in(user_id: int, day: str, slot_id: str) -> dict:
    """출석 체크. 하루 한 번만 출석할 수 있고, 휴강·차단·정원 초과를 저장 직전에 막는다."""

    def fn(doc):
        state = _day_state(doc, day)
        # 같은 날 이미 출석한 기록이 있으면 다른 타임이어도 거부한다(취소 후 다시 가능).
        mine_today = [a for a in doc["attendance"]
                      if a.get("user_id") == user_id and a.get("day") == day]
        if mine_today:
            first = min(mine_today, key=lambda a: (a.get("created_at", ""), a.get("id", 0)))
            time = (SLOT_BY_ID.get(first.get("slot_id"), {}) or {}).get("time", "")
            where = f"{time} 타임" if time else "오늘"
            raise CheckinError(
                "already_checked_in",
                f"오늘은 이미 {where}에 출석했어요. 다시 출석하려면 먼저 출석을 취소해 주세요.",
            )
        if state["closed_all"]:
            raise CheckinError("closed", "오늘은 전체 휴강이라 출석할 수 없어요.")
        slot = next((s for s in state["slots"] if s["id"] == slot_id), None)
        if slot is None:
            raise CheckinError("unknown_slot", "없는 타임이에요.")
        if slot["blocked"]:
            raise CheckinError("blocked", f"{slot['time']} 타임은 오늘 휴강이에요.")
        if slot["full"]:
            raise CheckinError("full", f"{slot['time']} 타임은 정원이 다 찼어요 ({slot['taken']}/{slot['capacity']}).")
        record = {
            "id": _next_id(doc, "attendance"),
            "user_id": user_id,
            "day": day,
            "slot_id": slot_id,
            "created_at": _now(),
        }
        doc["attendance"].append(record)
        return _attendance_public(doc, record), True

    return _mutate(fn)


def add_checkin(user_id: int, target_date: str, slot_id: str, status: str = "voted",
                taken: int | None = None, capacity: int | None = None, note: str = "") -> dict:
    """checker.py 같은 워커용. 이미 그날 기록이 있으면 기존 기록을 돌려준다."""
    try:
        return check_in(user_id, target_date, slot_id)
    except CheckinError as exc:
        if exc.code not in ("duplicate", "already_checked_in"):
            raise
    existing = [a for a in list_attendance(target_date) if a["user_id"] == user_id]
    if existing:
        return existing[0]
    raise CheckinError("duplicate", "이미 출석한 기록이 있어요.")


def cancel_checkin(attendance_id: int) -> bool:
    def fn(doc):
        before = len(doc["attendance"])
        doc["attendance"] = [a for a in doc["attendance"] if a.get("id") != attendance_id]
        removed = len(doc["attendance"]) != before
        return removed, removed

    return _mutate(fn)


def slot_info(slot_id: str) -> dict:
    return SLOT_BY_ID.get(slot_id, SLOT_BY_ID[DEFAULT_SLOT_ID])


if __name__ == "__main__":
    init_db()
    print(f"저장소: {backend_name()}")
    for user in list_users():
        print(f"  #{user['id']} {user['username']} ({user['nickname']})"
              f"{' 👑' if user['is_admin'] else ''}")
    print(kst_today(), [s["taken"] for s in get_day(kst_today())["slots"]])
