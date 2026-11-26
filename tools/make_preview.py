"""샘플 데이터로 동작하는 단일 파일 미리보기를 만든다.

    python tools/make_preview.py   ->  docs/preview.html

API 서버 없이도 화면(레이아웃·이펙트)을 확인하거나 공유할 수 있게, CSS와 JS를
인라인하고 fetch 를 가짜 데이터로 대체한다. 배포용 서버와는 무관하다.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
OUT = ROOT / "docs" / "preview.html"

SLOTS = [
    {"id": "first", "name": "첫째타임", "time": "08:30", "label": "첫째타임 : 오전 8시30분", "capacity": 11},
    {"id": "second", "name": "둘째타임", "time": "09:30", "label": "둘째타임 : 오전 9시30분", "capacity": 11},
    {"id": "third", "name": "셋째타임", "time": "18:30", "label": "셋째타임 : 저녁 6시30분", "capacity": 11},
    {"id": "fourth", "name": "넷째타임", "time": "19:40", "label": "넷째타임 : 저녁 7시40분", "capacity": 11},
]

DAY = {
    "date": "2026-11-26",
    "closed_all": False,
    "note": "",
    "blocked": ["first"],
    "slots": [
        {**SLOTS[0], "blocked": True, "taken": 0, "waiting": 0, "full": False, "open": False},
        {**SLOTS[1], "blocked": False, "taken": 9, "waiting": 0, "full": False, "open": True},
        {**SLOTS[2], "blocked": False, "taken": 11, "waiting": 2, "full": True, "open": False},
        {**SLOTS[3], "blocked": False, "taken": 4, "waiting": 0, "full": False, "open": True},
    ],
}

CONFIG = {
    "band_name": "On점핑(탕정지중해마을)",
    "band_url": "https://www.band.us/band/80346953/post",
    "slot_id": "third",
    "days_ahead": 1,
    "user_id": 1,
    "headless": True,
    "schedule": {
        "enabled": True,
        "weekdays": ["mon", "tue", "wed", "thu", "fri"],
        "weekly": {d: "third" for d in ["mon", "tue", "wed", "thu", "fri"]},
        "at": "21:01",
    },
}

ADMIN = {
    "id": 1, "name": "황보민재", "nickname": "민재", "role": "admin",
    "target_slot": "third", "target_slot_label": SLOTS[2]["label"], "target_time": "18:30",
    "days": ["mon", "tue", "wed", "thu", "fri"],
    "weekly": {d: "third" for d in ["mon", "tue", "wed", "thu", "fri"]}, "auto": True, "active": True,
}
MEMBERS = [
    {"id": 2, "name": "김도윤", "nickname": "도윤", "role": "member", "target_slot": "second",
     "target_slot_label": SLOTS[1]["label"], "target_time": "09:30", "days": ["mon", "wed", "fri"],
     "weekly": {"mon": "second", "tue": None, "wed": "second", "thu": None, "fri": "second"},
     "auto": True, "active": True},
    {"id": 3, "name": "이서연", "nickname": "서연", "role": "member", "target_slot": "third",
     "target_slot_label": SLOTS[2]["label"], "target_time": "18:30", "days": ["tue", "thu"],
     "weekly": {"mon": None, "tue": "third", "wed": None, "thu": "third", "fri": None},
     "auto": False, "active": True},
]

NOTICE = {
    "id": 1, "title": "11/27(금) 출석 예약 안내",
    "body": "오늘 밤 9시에 11월 27일 금요일 출석 예약 글이 올라옵니다.\n타임은 첫째 08:30 · 둘째 09:30 · 셋째 18:30 · 넷째 19:40, 각 11명 선착순입니다.",
    "posted_on": "2026-11-26", "author": "윤미라",
    "created_at": "2026-11-26T09:00:00", "updated_at": "2026-11-26T09:00:00",
}

LOGS = [
    {"id": 3, "user_id": 1, "at": "2026-11-25T21:01:07", "target_date": "2026-11-26",
     "target_weekday": "목", "slot_id": "third", "slot": SLOTS[2]["label"], "status": "voted",
     "message": "셋째타임 출석 예약 완료.", "mode": "live", "taken": 8, "capacity": 11,
     "post_title": "11/26(목) 출석 예약", "duration_ms": 8420},
    {"id": 2, "user_id": 1, "at": "2026-11-24T21:01:04", "target_date": "2026-11-25",
     "target_weekday": "수", "slot_id": "third", "slot": SLOTS[2]["label"], "status": "full",
     "message": "셋째타임 정원 마감 (11/11).", "mode": "live", "taken": 11, "capacity": 11,
     "post_title": "11/25(수) 출석 예약", "duration_ms": 7310},
    {"id": 1, "user_id": 1, "at": "2026-11-24T20:58:11", "target_date": "2026-11-25",
     "target_weekday": "수", "slot_id": "third", "slot": SLOTS[2]["label"], "status": "no_post",
     "message": "11/25(수) 출석 예약 글을 찾지 못했습니다.", "mode": "dry", "taken": None,
     "capacity": 11, "post_title": None, "duration_ms": 2210},
]

CHECKIN_DATES = ["2026-11-20", "2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26"]


def fixture(role: str) -> dict:
    session = ADMIN if role == "admin" else MEMBERS[0]
    checkins = [
        {"id": i + 1, "user_id": session["id"], "target_date": d, "slot_id": "third",
         "status": "voted", "taken": 5 + i, "capacity": 11, "note": "",
         "created_at": f"{d}T21:01:00", "user_name": session["name"]}
        for i, d in enumerate(CHECKIN_DATES)
    ] if role != "admin" else [
        {"id": 1, "user_id": 1, "target_date": "2026-11-26", "slot_id": "third",
         "status": "voted", "taken": 8, "capacity": 11, "note": "",
         "created_at": "2026-11-25T21:01:07", "user_name": "황보민재"},
    ]
    data = {
        "ok": True,
        "now": {"iso": "2026-11-26T18:19:00+09:00", "date": "2026-11-26", "weekday": "목", "clock": "18:19"},
        "session": session,
        "accounts": [{"id": u["id"], "name": u["name"], "role": u["role"]} for u in [ADMIN] + MEMBERS],
        "notice": NOTICE,
        "config": CONFIG,
        "slots": SLOTS,
        "day": DAY,
        "status": {
            "next_run_at": "2026-11-27T21:01+09:00",
            "schedule": CONFIG["schedule"],
            "slot_id": "third", "slot": SLOTS[2]["label"], "slot_time": "18:30",
            "weekly": CONFIG["schedule"]["weekly"],
            "weekly_slots": SLOTS,
            "slots": SLOTS,
            "band": CONFIG["band_name"],
            "login_saved": True,
            "last_run": LOGS[0],
            "today": "2026-11-26",
            "serverless": False,
        },
    }
    if role == "admin":
        data.update({
            "users": [ADMIN] + MEMBERS,
            "notices": [NOTICE, {**NOTICE, "id": 2, "title": "11/25(수) 휴강 안내", "posted_on": "2026-11-24"}],
            "logs": LOGS,
            "checkins": [{"id": 9, "user_id": 2, "target_date": "2026-11-26", "slot_id": "second",
                          "status": "voted", "taken": 9, "capacity": 11, "note": "",
                          "created_at": "2026-11-26T08:10:00", "user_name": "김도윤"}],
            "shakes": [
                {"id": 1, "user_id": 2, "user_name": "김도윤", "nickname": "도윤", "flavor": "초코",
                 "target_date": "2026-11-26", "created_at": "2026-11-26T08:12:00"},
                {"id": 2, "user_id": 3, "user_name": "이서연", "nickname": "서연", "flavor": "쿠키",
                 "target_date": "2026-11-26", "created_at": "2026-11-26T09:02:00"},
            ],
        })
    else:
        data.update({
            "users": [session],
            "me": {"today": checkins[0], "upcoming": None, "recent": checkins[:3]},
            "logs": LOGS[:2],
            "checkins": checkins,
            "shake": {"id": 1, "user_id": session["id"], "flavor": "바닐라",
                      "target_date": "2026-11-26", "created_at": "2026-11-26T08:00:00"},
            "waitlist": [{"id": 1, "user_id": session["id"], "slot_id": "third",
                          "target_date": "2026-11-26", "created_at": "2026-11-26T07:50:00"}],
        })
    return data


MOCK_JS = """
<script>
(function () {
  const ROLE = new URLSearchParams(location.search).get("role") === "admin" ? "admin" : "member";
  const DATA = __DATA__;
  const data = DATA[ROLE];
  window.fetch = function (url, options) {
    const path = String(url);
    const body = options && options.body ? JSON.parse(options.body) : {};
    let payload = { ok: true };
    if (path.includes("/api/bootstrap")) payload = data;
    else if (path.includes("/api/session")) payload = { ok: true, session: data.session };
    else if (path.includes("/api/checkin")) payload = { ok: true, day: data.day, checkin: {} };
    else if (path.includes("/api/waitlist")) payload = { ok: true, day: data.day };
    else if (path.includes("/api/shake")) payload = { ok: true, shake: { flavor: body.flavor } };
    else if (path.includes("/api/profile")) payload = { ok: true, session: data.session };
    else if (path.includes("/api/day")) payload = { ok: true, day: data.day };
    else if (path.includes("/api/config")) payload = { ok: true, config: data.config };
    else if (path.includes("/api/run-dry")) payload = { ok: true, job_id: "preview" };
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve(payload),
    });
  };
})();
</script>
"""


def main() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="style.css" />', f"<style>\n{css}\n</style>")
    mock = MOCK_JS.replace("__DATA__", json.dumps({"admin": fixture("admin"), "member": fixture("member")}, ensure_ascii=False))
    html = html.replace('<script src="app.js"></script>', mock + f"\n<script>\n{js}\n</script>")
    html = html.replace(
        '<span>온점핑 출석 예약 · <span class="mono">python server.py</span></span>',
        '<span>온점핑 출석 예약 · 디자인 미리보기 (샘플 데이터)</span>',
    )
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT} ({len(html) // 1024} KB)")


if __name__ == "__main__":
    main()
