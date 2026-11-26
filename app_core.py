"""플랫폼 독립 API 코어.

로컬 서버(server.py, http.server)와 Vercel 서버리스(api/index.py, WSGI)가 같은
라우팅 로직을 공유한다. handle() 은 순수하게 (상태코드, JSON payload, 헤더)만
돌려주고 소켓이나 프레임워크를 모른다.

    status, payload, headers = handle("GET", "/api/bootstrap", {}, {}, user_id)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import uuid
from datetime import date, datetime, time as dtime, timedelta, timezone

import store
from checker import ROOT, load_config, read_logs

KST = timezone(timedelta(hours=9))
WEEKDAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
SERVERLESS = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
BAND_STATE = ROOT / "storage_state.json"

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# 계산 도우미
# --------------------------------------------------------------------------- #

def active_weekly(cfg: dict) -> dict:
    """요일별 타임 설정. 없으면 이전 형식(weekdays + slot_id)에서 만든다."""
    schedule = cfg.get("schedule") or {}
    weekly = schedule.get("weekly")
    if isinstance(weekly, dict) and weekly:
        return {d: (weekly.get(d) if (d in store.WEEKDAY_KEYS and weekly.get(d) in store.SLOT_BY_ID) else None)
                for d in WEEKDAY_INDEX}
    slot = cfg.get("slot_id") if cfg.get("slot_id") in store.SLOT_BY_ID else store.DEFAULT_SLOT_ID
    return {d: (slot if d in schedule.get("weekdays", []) and d in store.WEEKDAY_KEYS else None)
            for d in WEEKDAY_INDEX}


def next_run_at(cfg: dict) -> str | None:
    schedule = cfg.get("schedule") or {}
    if not schedule.get("enabled", True):
        return None
    weekly = active_weekly(cfg)
    days = [d for d, slot in weekly.items() if slot]
    if not days:
        return None
    try:
        hour, minute = (int(x) for x in str(schedule.get("at", "21:01")).split(":"))
    except ValueError:
        hour, minute = 21, 1
    now = datetime.now(KST)
    for offset in range(0, 8):
        day = now.date() + timedelta(days=offset)
        if day.weekday() not in {WEEKDAY_INDEX[d] for d in days}:
            continue
        candidate = datetime.combine(day, dtime(hour, minute), tzinfo=KST)
        if candidate > now:
            return candidate.isoformat(timespec="minutes")
    return None


def kst_now() -> dict:
    now = datetime.now(KST)
    return {
        "iso": now.isoformat(timespec="seconds"),
        "date": now.date().isoformat(),
        "weekday": store.WEEKDAY_KO[store.WEEKDAYS[now.weekday()]],
        "clock": now.strftime("%H:%M"),
    }


def valid_date(value) -> str:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return date.today().isoformat()


def parse_days(value) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,\s]+", value) if part.strip()]
    return []


def status_payload(cfg: dict) -> dict:
    logs = read_logs()
    slot = store.slot_info(cfg.get("slot_id", store.DEFAULT_SLOT_ID))
    return {
        "next_run_at": next_run_at(cfg),
        "schedule": cfg.get("schedule"),
        "slot_id": slot["id"],
        "slot": slot["label"],
        "slot_time": slot["time"],
        "weekly": active_weekly(cfg),
        "weekly_slots": store.SCHEDULABLE,
        "slots": store.SLOTS,
        "band": cfg.get("band_name"),
        "login_saved": BAND_STATE.exists(),
        "last_run": logs[-1] if logs else None,
        "today": date.today().isoformat(),
        "serverless": SERVERLESS,
    }


def start_dry_run(actor: dict) -> str | None:
    if SERVERLESS:
        return None
    job_id = uuid.uuid4().hex[:10]
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "state": "running",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "started_by": actor["name"],
            "output": [],
            "record": None,
        }

    def worker() -> None:
        cmd = [sys.executable, str(ROOT / "checker.py"), "--dry-run", "--json"]
        try:
            proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=180)
            lines = [l for l in (proc.stdout or "").strip().splitlines() if l]
            record = None
            for line in reversed(lines):
                if line.startswith("{"):
                    try:
                        record = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue
            with JOBS_LOCK:
                job = JOBS[job_id]
                job["output"] = lines + [l for l in (proc.stderr or "").splitlines() if l]
                job["record"] = record
                job["exit_code"] = proc.returncode
                job["state"] = "done" if proc.returncode in (0, 1) else "error"
                job["finished_at"] = datetime.now().isoformat(timespec="seconds")
        except subprocess.TimeoutExpired:
            with JOBS_LOCK:
                JOBS[job_id].update(state="error", output=["시간 초과 (180s)"])
        except Exception as exc:  # pragma: no cover
            with JOBS_LOCK:
                JOBS[job_id].update(state="error", output=[f"{type(exc).__name__}: {exc}"])

    threading.Thread(target=worker, daemon=True).start()
    return job_id


# --------------------------------------------------------------------------- #
# 응답 도우미
# --------------------------------------------------------------------------- #

def _ok(payload: dict, status: int = 200, headers: dict | None = None):
    return status, {"ok": True, **payload}, headers or {}


def _err(message: str, status: int = 400, code: str | None = None):
    body = {"ok": False, "error": message}
    if code:
        body["code"] = code
    return status, body, {}


def _require(user_id: int | None, role: str | None = None):
    user = store.get_user(user_id) if user_id else None
    if user is None:
        return None, _err("로그인이 필요합니다.", 401)
    if role == "admin" and user["role"] != "admin":
        return None, _err("관리자 권한이 필요합니다.", 403)
    return user, None


def bootstrap(user_id: int | None):
    user = store.get_user(user_id) if user_id else None
    cfg = load_config()
    day = store.get_day(kst_now()["date"])
    payload = {
        "now": kst_now(),
        "session": user,
        "accounts": [
            {"id": u["id"], "name": u["name"], "role": u["role"]}
            for u in store.list_users()
        ],
        "notice": store.today_notice(),
        "config": cfg,
        "slots": store.SLOTS,
        "day": day,
        "status": status_per_payload(cfg),
    }
    if user and user["role"] == "admin":
        payload["users"] = store.list_users()
        payload["notices"] = store.list_notices(30)
        payload["logs"] = read_logs()[-40:][::-1]
        payload["checkins"] = store.list_checkins(None, 30)
        payload["shakes"] = store.list_shakes(day["date"])
    elif user:
        payload["users"] = [u for u in store.list_users() if u["id"] == user["id"]]
        payload["me"] = store.checkin_summary(user["id"])
        payload["logs"] = [
            r for r in read_logs() if r.get("user_id") in (None, user["id"])
        ][-8:][::-1]
        payload["checkins"] = store.list_checkins(user["id"], 60)
        payload["shake"] = store.my_shake(user["id"], day["date"])
        payload["waitlist"] = store.list_waitlist(day["date"])
    else:
        payload["users"] = payload["accounts"]
    return _ok(payload)


def status_per_payload(cfg: dict) -> dict:
    return status_payload(cfg)


# --------------------------------------------------------------------------- #
# 라우팅
# --------------------------------------------------------------------------- #

def handle(method: str, path: str, query: dict, body: dict, user_id: int | None):
    """query: {key: [values]} 형태 (urllib.parse.parse_qs 결과)."""
    method = method.upper()

    # ---------- 읽기 ----------
    if method == "GET":
        if path == "/api/accounts":
            return _ok({"accounts": [
                {"id": u["id"], "name": u["name"], "role": u["role"]}
                for u in store.list_users()
            ]})

        if path == "/api/bootstrap":
            return bootstrap(user_id)

        if path == "/api/slots":
            return _ok({"slots": store.SLOTS})

        if path == "/api/day":
            user, error = _require(user_id)
            if error:
                return error
            day = valid_date((query.get("date") or [date.today().isoformat()])[0])
            return _ok({"day": store.get_day(day)})

        if path == "/api/status":
            user, error = _require(user_id)
            if error:
                return error
            return _ok(status_payload(load_config()))

        if path == "/api/config":
            user, error = _require(user_id, "admin")
            if error:
                return error
            return _ok({"config": load_config(), "slots": store.SLOTS})

        if path == "/api/logs":
            user, error = _require(user_id)
            if error:
                return error
            logs = read_logs()
            if user["role"] != "admin":
                logs = [r for r in logs if r.get("user_id") in (None, user["id"])]
            return _ok({"logs": logs[-60:][::-1]})

        if path == "/api/users":
            user, error = _require(user_id, "admin")
            if error:
                return error
            return _ok({"users": store.list_users()})

        if path == "/api/notices":
            user, error = _require(user_id)
            if error:
                return error
            return _ok({"notices": store.list_notices(30)})

        if path == "/api/checkins":
            user, error = _require(user_id)
            if error:
                return error
            uid = None if user["role"] == "admin" else user["id"]
            return _ok({"checkins": store.list_checkins(uid, 40)})

        m = re.fullmatch(r"/api/run-dry/([0-9a-f]+)", path)
        if m:
            user, error = _require(user_id)
            if error:
                return error
            with JOBS_LOCK:
                job = JOBS.get(m.group(1))
                job = dict(job) if job else None
            if not job:
                return _err("작업을 찾을 수 없습니다.", 404)
            return _ok({"job": job})

        return _err("not found", 404)

    # ---------- 세션 ----------
    if path == "/api/session" and method == "POST":
        try:
            user = store.get_user(int(body.get("user_id")))
        except (TypeError, ValueError):
            user = None
        if not user:
            return _err("사용자를 찾을 수 없습니다.", 404)
        cookie = f"onjumping_uid={user['id']}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000"
        return _ok({"session": user}, headers={"Set-Cookie": cookie})

    if path == "/api/session" and method == "DELETE":
        return _ok({}, headers={"Set-Cookie": "onjumping_uid=; Path=/; Max-Age=0"})

    # ---------- 설정 ----------
    if path == "/api/config" and method == "POST":
        user, error = _require(user_id, "admin")
        if error:
            return error
        if SERVERLESS:
            return _err("서버리스 배포에서는 예약 설정을 저장할 수 없습니다. 로컬 서버에서 실행하세요.", 409)
        cfg = load_config()
        if body.get("slot_id") in store.SLOT_BY_ID:
            cfg["slot_id"] = body["slot_id"]
        if isinstance(body.get("schedule"), dict):
            schedule = cfg.setdefault("schedule", {})
            at = str(body["schedule"].get("at", ""))
            if re.fullmatch(r"\d{2}:\d{2}", at):
                schedule["at"] = at
            if "enabled" in body["schedule"]:
                schedule["enabled"] = bool(body["schedule"]["enabled"])
            if isinstance(body["schedule"].get("weekdays"), list):
                days = [d for d in body["schedule"]["weekdays"] if d in WEEKDAY_INDEX]
                if days:
                    schedule["weekdays"] = days
            if isinstance(body["schedule"].get("weekly"), dict):
                weekly = {}
                for d, v in body["schedule"]["weekly"].items():
                    if d not in WEEKDAY_INDEX:
                        continue
                    weekly[d] = v if (v in store.SLOT_BY_ID and d in store.WEEKDAY_KEYS) else None
                if weekly:
                    schedule["weekly"] = weekly
        if "days_ahead" in body:
            try:
                cfg["days_ahead"] = max(0, min(7, int(body["days_ahead"])))
            except (TypeError, ValueError):
                pass
        if body.get("band_url"):
            cfg["band_url"] = str(body["band_url"]).strip()
        (ROOT / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return _ok({"config": cfg})

    if path == "/api/run-dry" and method == "POST":
        user, error = _require(user_id, "admin")
        if error:
            return error
        job_id = start_dry_run(user)
        if job_id is None:
            return _err("미리 확인은 로컬 서버나 워커에서만 실행됩니다.", 409)
        return _ok({"job_id": job_id}, 202)

    # ---------- 휴강 / 타임 차단 ----------
    if path == "/api/day" and method == "POST":
        user, error = _require(user_id, "admin")
        if error:
            return error
        day = valid_date(body.get("date"))
        if isinstance(body.get("blocked"), list):
            store.set_blocked_slots(day, [str(s) for s in body["blocked"]])
        closed = body.get("closed_all")
        store.set_day(day, None if closed is None else bool(closed), body.get("note"))
        return _ok({"day": store.get_day(day)})

    if path == "/api/day/slot" and method == "POST":
        user, error = _require(user_id, "admin")
        if error:
            return error
        day = valid_date(body.get("date"))
        slot_id = str(body.get("slot_id") or "")
        if slot_id not in store.SLOT_BY_ID:
            return _err("없는 타임입니다.", 400)
        return _ok({"day": store.set_slot_block(day, slot_id, bool(body.get("blocked")))})

    # ---------- 출석 체크 ----------
    if path == "/api/checkin" and method == "POST":
        user, error = _require(user_id)
        if error:
            return error
        day = valid_date(body.get("date"))
        slot_id = str(body.get("slot_id") or "")
        if slot_id not in store.SLOT_BY_ID:
            return _err("없는 타임입니다.", 400)
        try:
            record = store.member_checkin(user["id"], day, slot_id)
        except store.CheckinError as exc:
            return _err(exc.message, 409, exc.code)
        return _ok({"checkin": record, "day": store.get_day(day)}, 201)

    # ---------- 내 프로필 / 출석 취소 ----------
    if path == "/api/profile" and method == "POST":
        user, error = _require(user_id)
        if error:
            return error
        nickname = str(body.get("nickname") or "").strip()
        if "nickname" in body and not nickname:
            return _err("닉네임을 입력하세요.", 400)
        if len(nickname) > 20:
            return _err("닉네임은 20자까지 가능합니다.", 400)
        fields = {"nickname": nickname or None}
        if "target_slot" in body:
            fields["target_slot"] = body["target_slot"]
        if "auto" in body:
            fields["auto"] = bool(body["auto"])
        if isinstance(body.get("days"), list):
            fields["days"] = parse_days(body["days"])
        if isinstance(body.get("weekly"), dict):
            weekly = {d: (v if v in store.SLOT_BY_ID else None)
                      for d, v in body["weekly"].items() if d in store.WEEKDAY_KEYS}
            fields["weekly"] = weekly
        updated = store.update_user(user["id"], **fields)
        # 관리자의 요일별 설정은 자동 예약(checker.py)이 읽는 전역 설정과 같다.
        if user["role"] == "admin" and isinstance(body.get("weekly"), dict) and not SERVERLESS:
            cfg = load_config()
            cfg.setdefault("schedule", {})["weekly"] = fields["weekly"]
            cfg["schedule"]["weekdays"] = [d for d, v in fields["weekly"].items() if v]
            first = next((v for v in fields["weekly"].values() if v), None)
            if first:
                cfg["slot_id"] = first
            (ROOT / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return _ok({"session": updated})

    if path == "/api/checkin/cancel" and method == "POST":
        user, error = _require(user_id)
        if error:
            return error
        day = valid_date(body.get("date"))
        slot_id = str(body.get("slot_id") or "")
        done = store.cancel_checkin(user["id"], day, slot_id)
        if not done:
            return _err("취소할 출석 기록이 없습니다.", 404)
        return _ok({"cancelled": True, "day": store.get_day(day)})

    # ---------- 대기열 / 쉐이크 ----------
    if path == "/api/waitlist" and method == "POST":
        user, error = _require(user_id)
        if error:
            return error
        day = valid_date(body.get("date"))
        slot_id = str(body.get("slot_id") or "")
        try:
            entry = store.join_waitlist(user["id"], day, slot_id)
        except store.CheckinError as exc:
            return _err(exc.message, 409, exc.code)
        return _ok({"waitlist": entry, "day": store.get_day(day)}, 201)

    if path == "/api/waitlist" and method == "DELETE":
        user, error = _require(user_id)
        if error:
            return error
        day = valid_date(body.get("date"))
        slot_id = str(body.get("slot_id") or "")
        return _ok({"left": store.leave_waitlist(user["id"], day, slot_id),
                    "day": store.get_day(day)})

    if path == "/api/shake" and method == "POST":
        user, error = _require(user_id)
        if error:
            return error
        day = valid_date(body.get("date"))
        try:
            shake = store.set_shake(user["id"], day, str(body.get("flavor") or ""))
        except store.CheckinError as exc:
            return _err(exc.message, 400, exc.code)
        return _ok({"shake": shake})

    # ---------- 유저 ----------
    if path == "/api/users" and method == "POST":
        user, error = _require(user_id, "admin")
        if error:
            return error
        name = str(body.get("name") or "").strip()
        if not name:
            return _err("이름을 입력하세요.", 400)
        created = store.create_user(
            name=name,
            nickname=str(body.get("nickname") or ""),
            target_slot=str(body.get("target_slot") or store.DEFAULT_SLOT_ID),
            days=parse_days(body.get("days") or ["mon", "tue", "wed", "thu", "fri"]),
        )
        return _ok({"user": created}, 201)

    m = re.fullmatch(r"/api/users/(\d+)", path)
    if m:
        user, error = _require(user_id, "admin")
        if error:
            return error
        uid = int(m.group(1))
        target = store.get_user(uid)
        if not target:
            return _err("사용자를 찾을 수 없습니다.", 404)
        if method == "PATCH":
            updated = store.update_user(
                uid,
                name=body.get("name"),
                nickname=body.get("nickname"),
                target_slot=body.get("target_slot"),
                days=parse_days(body["days"]) if "days" in body else None,
                active=body.get("active"),
            )
            return _ok({"user": updated})
        if method == "DELETE":
            if target["role"] == "admin":
                return _err("관리자 계정은 삭제할 수 없습니다.", 400)
            return _ok({"deleted": store.delete_user(uid)})

    # ---------- 공지 ----------
    if path == "/api/notices" and method == "POST":
        user, error = _require(user_id, "admin")
        if error:
            return error
        title = str(body.get("title") or "").strip()
        if not title:
            return _err("공지 제목을 입력하세요.", 400)
        notice = store.create_notice(
            title=title,
            body=str(body.get("body") or "").strip(),
            posted_on=valid_date(body.get("posted_on") or None),
            author=user["name"],
        )
        return _ok({"notice": notice}, 201)

    m = re.fullmatch(r"/api/notices/(\d+)", path)
    if m:
        user, error = _require(user_id, "admin")
        if error:
            return error
        nid = int(m.group(1))
        if method == "PATCH":
            notice = store.update_notice(
                nid,
                title=body.get("title"),
                body=body.get("body"),
                posted_on=valid_date(body["posted_on"]) if body.get("posted_on") else None,
            )
            if not notice:
                return _err("공지를 찾을 수 없습니다.", 404)
            return _ok({"notice": notice})
        if method == "DELETE":
            return _ok({"deleted": store.delete_notice(nid)})

    return _err("not found", 404)
