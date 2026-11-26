"""SQLite 데이터 계층.

테이블
    users        앱 사용자. role 은 'admin' | 'member'.
    notices      "오늘의 공지". 관리자만 쓰기 가능.
    checkins     출석 체크 기록 (slot_id 포함).
    day_controls 날짜별 "전체 휴강" 상태.
    slot_blocks  날짜별 타임 차단 (부분 휴강).

이 앱은 localhost에서만 돌리는 개인용 도구이므로 비밀번호 없이 사용자 id 쿠키로
역할을 구분한다. 외부에 노출할 계획이면 server.py 를 127.0.0.1 에 묶어 두는 것
(기본값) 이상의 인증을 먼저 붙여야 한다.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# 로컬은 프로젝트의 data/, Vercel 같은 읽기전용 파일시스템에서는 /tmp 를 쓴다.
DATA_DIR = Path(os.environ.get("ONJUMPING_DATA_DIR") or (ROOT / "data"))
DB_PATH = DATA_DIR / "app.db"

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAY_KO = {"mon": "월", "tue": "화", "wed": "수", "thu": "목", "fri": "금", "sat": "토", "sun": "일"}

# 밴드에 올라오는 실제 타임표 (선착순 11명)
SLOTS = [
    {"id": "first", "name": "첫째타임", "time": "08:30", "label": "첫째타임 : 오전 8시30분", "capacity": 11},
    {"id": "second", "name": "둘째타임", "time": "09:30", "label": "둘째타임 : 오전 9시30분", "capacity": 11},
    {"id": "third", "name": "셋째타임", "time": "18:30", "label": "셋째타임 : 저녁 6시30분", "capacity": 11},
    {"id": "fourth", "name": "넷째타임", "time": "19:40", "label": "넷째타임 : 저녁 7시40분", "capacity": 11},
]
SLOT_BY_ID = {s["id"]: s for s in SLOTS}
SCHEDULABLE = SLOTS
WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri"]
DEFAULT_SLOT_ID = "third"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    nickname    TEXT    NOT NULL DEFAULT '',
    role        TEXT    NOT NULL CHECK (role IN ('admin', 'member')),
    target_slot TEXT    NOT NULL DEFAULT 'third',
    days        TEXT    NOT NULL DEFAULT '["mon","tue","wed","thu","fri"]',
    auto        INTEGER NOT NULL DEFAULT 1,
    weekly      TEXT    NOT NULL DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS notices (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    posted_on  TEXT NOT NULL,
    author     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checkins (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_date TEXT    NOT NULL,
    slot_id     TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL,
    taken       INTEGER,
    capacity    INTEGER,
    note        TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS day_controls (
    day        TEXT PRIMARY KEY,
    closed_all INTEGER NOT NULL DEFAULT 0,
    note       TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS slot_blocks (
    day     TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    PRIMARY KEY (day, slot_id)
);

CREATE TABLE IF NOT EXISTS waitlist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_date TEXT    NOT NULL,
    slot_id     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    UNIQUE(user_id, target_date, slot_id)
);

CREATE TABLE IF NOT EXISTS shakes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_date TEXT    NOT NULL,
    flavor      TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    UNIQUE(user_id, target_date)
);

CREATE INDEX IF NOT EXISTS idx_checkins_user ON checkins(user_id, target_date);
CREATE INDEX IF NOT EXISTS idx_checkins_day ON checkins(target_date, slot_id);
CREATE INDEX IF NOT EXISTS idx_notices_posted ON notices(posted_on DESC);
"""

SEED_USERS = [
    ("황보민재", "민재", "admin", "third", '["mon","tue","wed","thu","fri"]'),
    ("김도윤", "도윤", "member", "second", '["mon","wed","fri"]'),
    ("이서연", "서연", "member", "third", '["tue","thu"]'),
]

SEED_NOTICES = [
    (
        "11/27(금) 출석 예약 안내",
        "오늘 밤 9시에 11월 27일 금요일 출석 예약 글이 올라옵니다.\n"
        "타임은 첫째 08:30 · 둘째 09:30 · 셋째 18:30 · 넷째 19:40, 각 11명 선착순입니다.",
        "2026-11-26",
        "윤미라",
    ),
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(seed: bool = True) -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        # 이전 버전 DB에는 nickname 컬럼이 없을 수 있다.
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        if "nickname" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN nickname TEXT NOT NULL DEFAULT ''")
        if "auto" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN auto INTEGER NOT NULL DEFAULT 1")
        if "weekly" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN weekly TEXT NOT NULL DEFAULT ''")
        if seed and conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO users (name, nickname, role, target_slot, days, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                [(*u, _now()) for u in SEED_USERS],
            )
            conn.executemany(
                "INSERT INTO notices (title, body, posted_on, author, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [(*n, _now(), _now()) for n in SEED_NOTICES],
            )


def slot_info(slot_id: str) -> dict:
    return SLOT_BY_ID.get(slot_id, SLOT_BY_ID[DEFAULT_SLOT_ID])


# --------------------------------------------------------------------------- #
# users
# --------------------------------------------------------------------------- #

def _user_row(row: sqlite3.Row) -> dict:
    slot = slot_info(row["target_slot"])
    return {
        "id": row["id"],
        "name": row["name"],
        "nickname": row["nickname"] or row["name"],
        "role": row["role"],
        "target_slot": slot["id"],
        "target_slot_label": slot["label"],
        "target_time": slot["time"],
        "days": json.loads(row["days"]),
        "auto": bool(row["auto"]),
        "weekly": json.loads(row["weekly"]) if row["weekly"] else None,
        "active": bool(row["active"]),
        "created_at": row["created_at"],
    }


def list_users() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY role = 'member', id").fetchall()
    return [_user_row(r) for r in rows]


def get_user(user_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _user_row(row) if row else None


def create_user(name: str, target_slot: str, days: list[str], role: str = "member",
                nickname: str = "") -> dict:
    clean_days = [d for d in days if d in WEEKDAYS] or ["mon"]
    slot_id = target_slot if target_slot in SLOT_BY_ID else DEFAULT_SLOT_ID
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO users (name, nickname, role, target_slot, days, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (name, (nickname or name)[:20], role, slot_id, json.dumps(clean_days), _now()),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _user_row(row)


def update_user(user_id: int, **fields) -> dict | None:
    allowed = {"name", "nickname", "target_slot", "days", "auto", "weekly", "active"}
    sets, values = [], []
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue
        if key == "target_slot":
            if value not in SLOT_BY_ID:
                continue
        if key == "nickname":
            value = str(value).strip()[:20]
            if not value:
                continue
        if key == "days":
            value = json.dumps([d for d in value if d in WEEKDAYS])
        if key == "weekly":
            value = json.dumps({d: v for d, v in dict(value).items() if d in WEEKDAYS})
        if key in ("active", "auto"):
            value = 1 if value else 0
        sets.append(f"{key} = ?")
        values.append(value)
    if not sets:
        return get_user(user_id)
    values.append(user_id)
    with connect() as conn:
        conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", values)
    return get_user(user_id)


def delete_user(user_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM users WHERE id = ? AND role = 'member'", (user_id,))
    return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# notices
# --------------------------------------------------------------------------- #

def _notice_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "body": row["body"],
        "posted_on": row["posted_on"],
        "author": row["author"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_notices(limit: int = 20) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM notices ORDER BY posted_on DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_notice_row(r) for r in rows]


def today_notice(today: str | None = None) -> dict | None:
    today = today or date.today().isoformat()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM notices WHERE posted_on <= ? ORDER BY posted_on DESC, id DESC LIMIT 1",
            (today,),
        ).fetchone()
    return _notice_row(row) if row else None


def get_notice(notice_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM notices WHERE id = ?", (notice_id,)).fetchone()
    return _notice_row(row) if row else None


def create_notice(title: str, body: str, posted_on: str, author: str) -> dict:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO notices (title, body, posted_on, author, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (title, body, posted_on, author, _now(), _now()),
        )
        row = conn.execute("SELECT * FROM notices WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _notice_row(row)


def update_notice(notice_id: int, **fields) -> dict | None:
    allowed = {"title", "body", "posted_on"}
    sets, values = [], []
    for key, value in fields.items():
        if key in allowed and value is not None:
            sets.append(f"{key} = ?")
            values.append(value)
    if not sets:
        return None
    sets.append("updated_at = ?")
    values.extend([_now(), notice_id])
    with connect() as conn:
        conn.execute(f"UPDATE notices SET {', '.join(sets)} WHERE id = ?", values)
        row = conn.execute("SELECT * FROM notices WHERE id = ?", (notice_id,)).fetchone()
    return _notice_row(row) if row else None


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
        blocks = conn.execute(
            "SELECT slot_id FROM slot_blocks WHERE day = ? ORDER BY slot_id", (day,)
        ).fetchall()
        waiting = {
            r["slot_id"]: r["cnt"]
            for r in conn.execute(
                "SELECT slot_id, COUNT(*) AS cnt FROM waitlist WHERE target_date = ? GROUP BY slot_id",
                (day,),
            ).fetchall()
        }
        counts = {
            r["slot_id"]: r["cnt"]
            for r in conn.execute(
                "SELECT slot_id, COUNT(*) AS cnt FROM checkins"
                " WHERE target_date = ? AND status = 'voted' GROUP BY slot_id",
                (day,),
            ).fetchall()
        }
    blocked = [r["slot_id"] for r in blocks]
    closed_all = bool(ctrl["closed_all"]) if ctrl else False
    slots = []
    for slot in SLOTS:
        taken = counts.get(slot["id"], 0)
        slots.append({
            **slot,
            "blocked": slot["id"] in blocked,
            "taken": taken,
            "waiting": waiting.get(slot["id"], 0),
            "full": taken >= slot["capacity"],
            "open": (not closed_all) and slot["id"] not in blocked and taken < slot["capacity"],
        })
    return {
        "date": day,
        "closed_all": closed_all,
        "note": ctrl["note"] if ctrl else "",
        "blocked": blocked,
        "slots": slots,
    }


def set_day(day: str, closed_all: bool | None = None, note: str | None = None) -> dict:
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO day_controls (day, closed_all, note, updated_at) VALUES (?, 0, '', ?)",
            (day, _now()),
        )
        if closed_all is not None:
            conn.execute(
                "UPDATE day_controls SET closed_all = ?, updated_at = ? WHERE day = ?",
                (1 if closed_all else 0, _now(), day),
            )
        if note is not None:
            conn.execute("UPDATE day_controls SET note = ?, updated_at = ? WHERE day = ?", (note, _now(), day))
    return get_day(day)


def set_slot_block(day: str, slot_id: str, blocked: bool) -> dict:
    if slot_id not in SLOT_BY_ID:
        raise ValueError("unknown slot")
    with connect() as conn:
        if blocked:
            conn.execute(
                "INSERT OR IGNORE INTO slot_blocks (day, slot_id) VALUES (?, ?)", (day, slot_id)
            )
        else:
            conn.execute("DELETE FROM slot_blocks WHERE day = ? AND slot_id = ?", (day, slot_id))
    return get_day(day)


def set_blocked_slots(day: str, slot_ids: list[str]) -> dict:
    valid = [s for s in slot_ids if s in SLOT_BY_ID]
    with connect() as conn:
        conn.execute("DELETE FROM slot_blocks WHERE day = ?", (day,))
        conn.executemany(
            "INSERT OR IGNORE INTO slot_blocks (day, slot_id) VALUES (?, ?)",
            [(day, s) for s in valid],
        )
    return get_day(day)


# --------------------------------------------------------------------------- #
# checkins
# --------------------------------------------------------------------------- #

class CheckinError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def add_checkin(user_id: int, target_date: str, slot_id: str, status: str = "voted",
                taken: int | None = None, capacity: int | None = None, note: str = "") -> dict:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO checkins (user_id, target_date, slot_id, status, taken, capacity, note, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, target_date, slot_id, status, taken, capacity, note, _now()),
        )
        row = conn.execute("SELECT * FROM checkins WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def member_checkin(user_id: int, target_date: str, slot_id: str) -> dict:
    """일반 유저 출석 체크. 휴강·차단·정원 초과를 서버에서 막는다."""
    day = get_day(target_date)
    if day["closed_all"]:
        raise CheckinError("closed", "오늘은 전체 휴강입니다.")
    slot = next((s for s in day["slots"] if s["id"] == slot_id), None)
    if slot is None:
        raise CheckinError("unknown_slot", "없는 타임입니다.")
    if slot["blocked"]:
        raise CheckinError("blocked", f"{slot['name']}은(는) 오늘 휴강입니다.")
    if slot["full"]:
        raise CheckinError("full", f"{slot['name']} 정원이 마감되었습니다. ({slot['taken']}/{slot['capacity']})")
    with connect() as conn:
        dup = conn.execute(
            "SELECT id FROM checkins WHERE user_id = ? AND target_date = ? AND slot_id = ?",
            (user_id, target_date, slot_id),
        ).fetchone()
    if dup:
        raise CheckinError("duplicate", "이미 이 타임에 체크했습니다.")
    return add_checkin(
        user_id, target_date, slot_id, status="voted",
        taken=slot["taken"] + 1, capacity=slot["capacity"],
    )


FLAVORS = ["초코", "바닐라", "쿠키", "딸기"]


def join_waitlist(user_id: int, target_date: str, slot_id: str) -> dict:
    day = get_day(target_date)
    slot = next((s for s in day["slots"] if s["id"] == slot_id), None)
    if slot is None:
        raise CheckinError("unknown_slot", "없는 타임입니다.")
    if day["closed_all"] or slot["blocked"]:
        raise CheckinError("closed", "휴강인 타임에는 대기를 받지 않습니다.")
    if not slot["full"]:
        raise CheckinError("open", "아직 자리가 남아 있어 대기 없이 바로 출석할 수 있습니다.")
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO waitlist (user_id, target_date, slot_id, created_at) VALUES (?, ?, ?, ?)",
            (user_id, target_date, slot_id, _now()),
        )
        row = conn.execute(
            "SELECT * FROM waitlist WHERE user_id = ? AND target_date = ? AND slot_id = ?",
            (user_id, target_date, slot_id),
        ).fetchone()
    return dict(row)


def leave_waitlist(user_id: int, target_date: str, slot_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM waitlist WHERE user_id = ? AND target_date = ? AND slot_id = ?",
            (user_id, target_date, slot_id),
        )
    return cur.rowcount > 0


def list_waitlist(target_date: str, slot_id: str | None = None) -> list[dict]:
    query = ("SELECT w.*, u.name AS user_name, u.nickname FROM waitlist w"
             " JOIN users u ON u.id = w.user_id WHERE w.target_date = ?")
    params: list = [target_date]
    if slot_id:
        query += " AND w.slot_id = ?"
        params.append(slot_id)
    query += " ORDER BY w.created_at"
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def set_shake(user_id: int, target_date: str, flavor: str) -> dict:
    if flavor not in FLAVORS:
        raise CheckinError("unknown_flavor", "없는 맛입니다.")
    with connect() as conn:
        conn.execute(
            "INSERT INTO shakes (user_id, target_date, flavor, created_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(user_id, target_date) DO UPDATE SET flavor = excluded.flavor,"
            " created_at = excluded.created_at",
            (user_id, target_date, flavor, _now()),
        )
        row = conn.execute(
            "SELECT * FROM shakes WHERE user_id = ? AND target_date = ?", (user_id, target_date)
        ).fetchone()
    return dict(row)


def my_shake(user_id: int, target_date: str) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM shakes WHERE user_id = ? AND target_date = ?", (user_id, target_date)
        ).fetchone()
    return dict(row) if row else None


def list_shakes(target_date: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT s.*, u.name AS user_name, u.nickname FROM shakes s"
            " JOIN users u ON u.id = s.user_id WHERE s.target_date = ? ORDER BY s.created_at",
            (target_date,),
        ).fetchall()
    return [dict(r) for r in rows]


def cancel_checkin(user_id: int, target_date: str, slot_id: str) -> bool:
    """본인 출석 취소. 휴강 여부와 무관하게 자기 기록은 지울 수 있다."""
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM checkins WHERE user_id = ? AND target_date = ? AND slot_id = ?",
            (user_id, target_date, slot_id),
        )
    return cur.rowcount > 0


def list_checkins(user_id: int | None = None, limit: int = 20) -> list[dict]:
    query = (
        "SELECT c.*, u.name AS user_name FROM checkins c JOIN users u ON u.id = c.user_id"
    )
    params: list = []
    if user_id is not None:
        query += " WHERE c.user_id = ?"
        params.append(user_id)
    query += " ORDER BY c.created_at DESC, c.id DESC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def checkin_summary(user_id: int) -> dict:
    """일반 유저 화면용: 다음 대상일과 최근 상태."""
    with connect() as conn:
        today = conn.execute(
            "SELECT * FROM checkins WHERE user_id = ? AND target_date = date('now')"
            " ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        upcoming = conn.execute(
            "SELECT * FROM checkins WHERE user_id = ? AND target_date > date('now')"
            " ORDER BY target_date LIMIT 1",
            (user_id,),
        ).fetchone()
        recent = conn.execute(
            "SELECT * FROM checkins WHERE user_id = ? ORDER BY created_at DESC LIMIT 5",
            (user_id,),
        ).fetchall()
    return {
        "today": dict(today) if today else None,
        "upcoming": dict(upcoming) if upcoming else None,
        "recent": [dict(r) for r in recent],
    }


if __name__ == "__main__":
    init_db()
    print(f"초기화 완료: {DB_PATH}")
    for u in list_users():
        print(f"  #{u['id']} {u['name']} ({u['role']}) {u['target_slot_label']}")
    print(date.today().isoformat(), get_day(date.today().isoformat()))
