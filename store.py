"""SQLite 데이터 계층.

테이블
    users        아이디·비밀번호 해시·닉네임·관리자 여부·요일별 타임
    attendance   날짜별 출석 기록 (사람 × 날짜 × 타임 하나)
    notices      "오늘의 공지" (하루 한 건)
    day_controls 날짜별 "전체 휴강"
    slot_blocks  날짜별 타임 차단 (부분 휴강)
    settings     서명 쿠키용 비밀키 같은 앱 설정

로컬은 프로젝트의 data/onjumping.db, Vercel 같은 읽기 전용 파일시스템에서는
ONJUMPING_DATA_DIR(/tmp) 아래에 만든다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
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

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    nickname      TEXT    NOT NULL DEFAULT '',
    is_admin      INTEGER NOT NULL DEFAULT 0,
    weekly        TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS attendance (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day        TEXT    NOT NULL,
    slot_id    TEXT    NOT NULL,
    note       TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL,
    UNIQUE (user_id, day, slot_id)
);

CREATE TABLE IF NOT EXISTS notices (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    body       TEXT    NOT NULL,
    posted_on  TEXT    NOT NULL,
    author     TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS day_controls (
    day        TEXT PRIMARY KEY,
    closed_all INTEGER NOT NULL DEFAULT 0,
    note       TEXT    NOT NULL DEFAULT '',
    updated_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS slot_blocks (
    day     TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    PRIMARY KEY (day, slot_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attendance_day ON attendance(day, slot_id);
CREATE INDEX IF NOT EXISTS idx_attendance_user ON attendance(user_id, day);
CREATE INDEX IF NOT EXISTS idx_notices_posted ON notices(posted_on DESC, id DESC);
"""

# 첫 실행 시 들어가는 계정. 비밀번호는 저장할 때 해시한다.
SEED_USERS = [
    ("admin", "admin1234", "관리자", 1),
    ("minjae", "1234", "민재", 0),
    ("doyun", "1234", "도윤", 0),
]
SEED_NOTICE = "온점핑 출석 앱이 새로워졌어요.\n타임은 08:30 · 09:30 · 18:30 · 19:40, 타임마다 11명 선착순입니다."


# --------------------------------------------------------------------------- #
# 연결
# --------------------------------------------------------------------------- #

@contextmanager
def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def kst_today() -> str:
    return datetime.now(KST).date().isoformat()


# --------------------------------------------------------------------------- #
# 비밀번호 / 쿠키 서명
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
    """세션 쿠키 서명 키. DB 에 한 번 만들어 두고 계속 쓴다."""
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'secret'").fetchone()
        if row:
            return row["value"]
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('secret', ?)", (secrets.token_hex(32),))
        row = conn.execute("SELECT value FROM settings WHERE key = 'secret'").fetchone()
    return row["value"]


# --------------------------------------------------------------------------- #
# 초기화
# --------------------------------------------------------------------------- #

def init_db(seed: bool = True) -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        if seed and count == 0:
            for username, password, nickname, is_admin in SEED_USERS:
                conn.execute(
                    "INSERT INTO users (username, password_hash, nickname, is_admin, weekly, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (username, hash_password(password), nickname, is_admin,
                     json.dumps(DEFAULT_WEEKLY), _now()),
                )
            today = kst_today()
            conn.execute(
                "INSERT INTO notices (body, posted_on, author, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (SEED_NOTICE, today, "관리자", _now(), _now()),
            )


# --------------------------------------------------------------------------- #
# users
# --------------------------------------------------------------------------- #

def _user_row(row: sqlite3.Row) -> dict:
    weekly = json.loads(row["weekly"]) if row["weekly"] else {}
    return {
        "id": row["id"],
        "username": row["username"],
        "nickname": row["nickname"] or row["username"],
        "is_admin": bool(row["is_admin"]),
        "weekly": {d: (weekly.get(d) if weekly.get(d) in SLOT_BY_ID and d in WEEKDAY_KEYS else None)
                   for d in WEEKDAY_KEYS},
        "created_at": row["created_at"],
    }


def list_users() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY is_admin DESC, username").fetchall()
    return [_user_row(r) for r in rows]


def get_user(user_id: int | None) -> dict | None:
    if not user_id:
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _user_row(row) if row else None


def authenticate(username: str, password: str) -> dict | None:
    username = (username or "").strip().lower()
    if not username or not password:
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row is None or not check_password(row["password_hash"], password):
        return None
    return _user_row(row)


def username_taken(username: str) -> bool:
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM users WHERE username = ?", ((username or "").strip().lower(),)).fetchone()
    return row is not None


def create_user(username: str, password: str, nickname: str = "", is_admin: bool = False) -> dict:
    username = (username or "").strip().lower()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, nickname, is_admin, weekly, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (username, hash_password(password), (nickname or "").strip()[:20], 1 if is_admin else 0,
             json.dumps(DEFAULT_WEEKLY), _now()),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _user_row(row)


def set_nickname(user_id: int, nickname: str) -> dict | None:
    nickname = (nickname or "").strip()[:20]
    if not nickname:
        return get_user(user_id)
    with connect() as conn:
        conn.execute("UPDATE users SET nickname = ? WHERE id = ?", (nickname, user_id))
    return get_user(user_id)


def set_weekly(user_id: int, weekly: dict) -> dict | None:
    clean = {d: (weekly.get(d) if weekly.get(d) in SLOT_BY_ID else None) for d in WEEKDAY_KEYS}
    with connect() as conn:
        conn.execute("UPDATE users SET weekly = ? WHERE id = ?", (json.dumps(clean), user_id))
    return get_user(user_id)


def delete_user(user_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM users WHERE id = ? AND is_admin = 0", (user_id,))
    return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# notices (오늘의 공지 — 하루 한 건)
# --------------------------------------------------------------------------- #

def _notice_row(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "body": row["body"], "posted_on": row["posted_on"],
            "author": row["author"], "updated_at": row["updated_at"]}


def latest_notice(today: str | None = None) -> dict | None:
    today = today or kst_today()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM notices WHERE posted_on <= ? ORDER BY posted_on DESC, id DESC LIMIT 1", (today,)
        ).fetchone()
    return _notice_row(row) if row else None


def set_notice(body: str, author: str, posted_on: str | None = None) -> dict:
    """오늘 공지를 등록한다. 같은 날짜 공지가 이미 있으면 내용만 바꾼다."""
    posted_on = posted_on or kst_today()
    body = body.strip()
    with connect() as conn:
        row = conn.execute("SELECT * FROM notices WHERE posted_on = ? ORDER BY id DESC LIMIT 1",
                           (posted_on,)).fetchone()
        if row:
            conn.execute("UPDATE notices SET body = ?, author = ?, updated_at = ? WHERE id = ?",
                         (body, author, _now(), row["id"]))
            notice_id = row["id"]
        else:
            cur = conn.execute(
                "INSERT INTO notices (body, posted_on, author, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (body, posted_on, author, _now(), _now()),
            )
            notice_id = cur.lastrowid
        row = conn.execute("SELECT * FROM notices WHERE id = ?", (notice_id,)).fetchone()
    return _notice_row(row)


def delete_notice(notice_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM notices WHERE id = ?", (notice_id,))
    return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# 휴강 / 타임 차단
# --------------------------------------------------------------------------- #

def get_day(day: str) -> dict:
    with connect() as conn:
        ctrl = conn.execute("SELECT * FROM day_controls WHERE day = ?", (day,)).fetchone()
        blocked = [r["slot_id"] for r in conn.execute(
            "SELECT slot_id FROM slot_blocks WHERE day = ?", (day,)).fetchall()]
        counts = {r["slot_id"]: r["n"] for r in conn.execute(
            "SELECT slot_id, COUNT(*) AS n FROM attendance WHERE day = ? GROUP BY slot_id", (day,)).fetchall()}
    closed_all = bool(ctrl["closed_all"]) if ctrl else False
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
    return {"date": day, "closed_all": closed_all, "note": ctrl["note"] if ctrl else "",
            "blocked": blocked, "slots": slots}


def set_day(day: str, closed_all: bool = False, blocked: list[str] | None = None, note: str | None = None) -> dict:
    blocked = [s for s in (blocked or []) if s in SLOT_BY_ID]
    with connect() as conn:
        conn.execute(
            "INSERT INTO day_controls (day, closed_all, note, updated_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(day) DO UPDATE SET closed_all = excluded.closed_all,"
            " note = CASE WHEN ? IS NULL THEN day_controls.note ELSE excluded.note END,"
            " updated_at = excluded.updated_at",
            (day, 1 if closed_all else 0, note or "", _now(), note),
        )
        conn.execute("DELETE FROM slot_blocks WHERE day = ?", (day,))
        conn.executemany("INSERT OR IGNORE INTO slot_blocks (day, slot_id) VALUES (?, ?)",
                         [(day, s) for s in blocked])
    return get_day(day)


# --------------------------------------------------------------------------- #
# attendance
# --------------------------------------------------------------------------- #

class CheckinError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _attendance_row(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "user_id": row["user_id"], "day": row["day"], "slot_id": row["slot_id"],
            "nickname": row["nickname"] or row["username"], "username": row["username"],
            "created_at": row["created_at"]}


def get_attendance(attendance_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT a.*, u.username, u.nickname FROM attendance a JOIN users u ON u.id = a.user_id"
            " WHERE a.id = ?", (attendance_id,)).fetchone()
    return _attendance_row(row) if row else None


def list_attendance(day: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT a.*, u.username, u.nickname FROM attendance a JOIN users u ON u.id = a.user_id"
            " WHERE a.day = ? ORDER BY a.created_at, a.id", (day,)).fetchall()
    return [_attendance_row(r) for r in rows]


def check_in(user_id: int, day: str, slot_id: str) -> dict:
    """출석 체크. 휴강·차단·정원 초과·중복을 서버에서 막는다."""
    state = get_day(day)
    if state["closed_all"]:
        raise CheckinError("closed", "오늘은 전체 휴강이라 출석할 수 없어요.")
    slot = next((s for s in state["slots"] if s["id"] == slot_id), None)
    if slot is None:
        raise CheckinError("unknown_slot", "없는 타임이에요.")
    if slot["blocked"]:
        raise CheckinError("blocked", f"{slot['time']} 타임은 오늘 휴강이에요.")
    if slot["full"]:
        raise CheckinError("full", f"{slot['time']} 타임은 정원이 다 찼어요 ({slot['taken']}/{slot['capacity']}).")
    with connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO attendance (user_id, day, slot_id, created_at) VALUES (?, ?, ?, ?)",
                (user_id, day, slot_id, _now()),
            )
        except sqlite3.IntegrityError:
            raise CheckinError("duplicate", f"이미 {slot['time']} 타임에 출석했어요.") from None
        row = conn.execute(
            "SELECT a.*, u.username, u.nickname FROM attendance a JOIN users u ON u.id = a.user_id"
            " WHERE a.id = ?", (cur.lastrowid,)).fetchone()
    return _attendance_row(row)


def add_checkin(user_id: int, target_date: str, slot_id: str, status: str = "voted",
                taken: int | None = None, capacity: int | None = None, note: str = "") -> dict:
    """checker.py 같은 워커용. 이미 있으면 기존 기록을 돌려준다."""
    try:
        return check_in(user_id, target_date, slot_id)
    except CheckinError as exc:
        if exc.code == "duplicate":
            with connect() as conn:
                row = conn.execute(
                    "SELECT a.*, u.username, u.nickname FROM attendance a JOIN users u ON u.id = a.user_id"
                    " WHERE a.user_id = ? AND a.day = ? AND a.slot_id = ?",
                    (user_id, target_date, slot_id)).fetchone()
            if row:
                return _attendance_row(row)
        raise


def cancel_checkin(attendance_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM attendance WHERE id = ?", (attendance_id,))
    return cur.rowcount > 0


def slot_info(slot_id: str) -> dict:
    return SLOT_BY_ID.get(slot_id, SLOT_BY_ID[DEFAULT_SLOT_ID])


if __name__ == "__main__":
    init_db()
    print(f"초기화 완료: {DB_PATH}")
    for user in list_users():
        print(f"  #{user['id']} {user['username']} ({user['nickname']})"
              f"{' 👑' if user['is_admin'] else ''}")
    print(kst_today(), get_day(kst_today())["slots"])
