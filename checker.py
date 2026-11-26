#!/usr/bin/env python3
"""온점핑(탕정지중해마을) 밴드 출석 예약 체커.

네이버 밴드에 올라오는 "내일 출석 예약" 글을 찾아, 지정한 타임(기본: 셋째타임
저녁 6시30분)에 빈자리가 있으면 투표로 출석을 예약한다.

사용법
    python checker.py --setup-login   # 최초 1회: 브라우저에서 밴드 로그인 후 세션 저장
    python checker.py --dry-run       # 실제 투표 없이 "무엇을 할지"만 확인
    python checker.py                 # 실제 투표 실행
    python checker.py --debug-dump    # 글 HTML을 logs/debug/ 에 저장 (선택자 조정용)

설계 메모
- 밴드 웹은 공개 API가 없으므로 Playwright로 저장된 세션(storage_state.json)을
  사용해 실제 화면을 조작한다.
- 글/투표 영역의 DOM 구조는 밴드 개편에 따라 달라질 수 있어, 여러 선택자 후보를
  순서대로 시도하고 마지막에는 텍스트 기반 탐색으로 넘어간다.
- 모든 실행 결과는 logs/history.json 에 append 된다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import store

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "storage_state.json"
LOG_DIR = ROOT / "logs"
LOG_PATH = LOG_DIR / "history.json"
DEBUG_DIR = LOG_DIR / "debug"

WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]


# --------------------------------------------------------------------------- #
# 설정 / 로그
# --------------------------------------------------------------------------- #

DEFAULT_CONFIG = {
    "band_url": "https://www.band.us/band/80346953/post",
    "band_name": "On점핑(탕정지중해마을)",
    "slot_id": "third",
    "days_ahead": 1,
    "user_id": 1,
    "headless": True,
    "timeout_ms": 25000,
    "schedule": {
        "enabled": True,
        "weekdays": ["sun", "mon", "tue", "wed", "thu"],
        "weekly": {"mon": "third", "tue": "third", "wed": "third",
                   "thu": "third", "fri": "third", "sat": None, "sun": None},
        "at": "21:01",
    },
}


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if CONFIG_PATH.exists():
        try:
            user_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"config.json 형식 오류: {exc}")
        for key, value in user_cfg.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    return cfg


def ensure_log_dir() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def read_logs() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    try:
        data = json.loads(LOG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def append_log(record: dict) -> None:
    ensure_log_dir()
    logs = read_logs()
    logs.append(record)
    LOG_PATH.write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8")


def make_record(cfg: dict, status: str, message: str, **extra) -> dict:
    now = datetime.now()
    target = now.date() + timedelta(days=int(cfg.get("days_ahead", 1)))
    slot = store.slot_info(str(cfg.get("slot_id", store.DEFAULT_SLOT_ID)))
    record = {
        "user_id": extra.pop("user_id", cfg.get("user_id")),
        "at": now.isoformat(timespec="seconds"),
        "target_date": target.isoformat(),
        "target_weekday": WEEKDAY_KO[target.weekday()],
        "slot_id": extra.pop("slot_id", slot["id"]),
        "slot": extra.pop("slot_label", slot["label"]),
        "status": status,
        "message": message,
        "mode": extra.pop("mode", "live"),
        "taken": extra.pop("taken", None),
        "capacity": extra.pop("capacity", slot["capacity"]),
        "post_title": extra.pop("post_title", None),
        "duration_ms": extra.pop("duration_ms", None),
    }
    record.update(extra)
    return record


# --------------------------------------------------------------------------- #
# 날짜 파싱
# --------------------------------------------------------------------------- #

def korean_date_patterns(target: date) -> list[re.Pattern]:
    month, day = target.month, target.day
    wd = WEEKDAY_KO[target.weekday()]
    raw = [
        rf"{month}\s*[/.\-]\s*{day}",           # 11/27, 11.27
        rf"{month}\s*월\s*{day}\s*일",           # 11월 27일
        rf"{day}\s*일\s*\(?{wd}\)?",             # 27일(금)
        rf"\(?{wd}\)?\s*{day}\s*일",             # (금) 27일
    ]
    return [re.compile(p) for p in raw]


def text_mentions_date(text: str, target: date) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in korean_date_patterns(target))


def parse_spot_count(text: str) -> tuple[int | None, int | None]:
    """'11/11', '8명', '정원 11명 중 8명' 같은 표기에서 (참여, 정원)을 뽑는다."""
    if not text:
        return None, None
    m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d{1,2})\s*명\s*(?:참여|참가|신청)", text)
    if m:
        return int(m.group(1)), None
    return None, None


# --------------------------------------------------------------------------- #
# Playwright 동작
# --------------------------------------------------------------------------- #

class CheckResult(Exception):
    """투표를 진행할 수 없는 상황을 status/message 로 전달한다."""

    def __init__(self, status: str, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


def click_first(page, selectors: list[str], timeout: int = 3000):
    """후보 선택자를 순서대로 시도해 처음 클릭되는 요소를 반환한다."""
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if loc.count() == 0:
                continue
            if loc.is_visible():
                loc.click(timeout=timeout)
                return sel
        except Exception:
            continue
    return None


def open_login_setup(cfg: dict) -> int:
    from playwright.sync_api import sync_playwright

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    print("브라우저가 열리면 네이버 밴드에 로그인하세요. 로그인 후 이 창에서 엔터를 누르면 세션을 저장합니다.")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul")
        page = context.new_page()
        page.goto(cfg["band_url"], wait_until="domcontentloaded", timeout=cfg["timeout_ms"])
        input("로그인을 마쳤으면 엔터: ")
        context.storage_state(path=str(STATE_PATH))
        browser.close()
    print(f"세션 저장 완료: {STATE_PATH}")
    print("이 파일에는 로그인 쿠키가 들어 있으므로 절대 커밋하지 마세요. (.gitignore 처리됨)")
    return 0


def run_check(cfg: dict, dry_run: bool, debug_dump: bool) -> dict:
    from playwright.sync_api import sync_playwright

    started = time.time()
    KEY = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    target = date.today() + timedelta(days=int(cfg.get("days_ahead", 1)))
    schedule = cfg.get("schedule") or {}
    weekly = schedule.get("weekly") if isinstance(schedule.get("weekly"), dict) else None
    if weekly is not None:
        slot_id = weekly.get(KEY[target.weekday()])
        if not slot_id:
            raise CheckResult(
                "day_off",
                f"{KEY[target.weekday()]}요일은 자동 예약을 하지 않도록 설정되어 있습니다.",
            )
    else:
        slot_id = cfg.get("slot_id", store.DEFAULT_SLOT_ID)
    slot = store.slot_info(str(slot_id))
    slot_kw = slot["name"]
    capacity = int(slot["capacity"])
    headless = bool(cfg.get("headless", True))
    mode = "dry" if dry_run else "live"

    # 대시보드에서 건 휴강/타임 차단은 밴드를 열기 전에 먼저 존중한다.
    day_state = store.get_day(target.isoformat())
    if day_state["closed_all"]:
        raise CheckResult("closed", f"{target.month}/{target.day}은(는) 전체 휴강으로 설정되어 있습니다.")
    if slot["id"] in day_state["blocked"]:
        raise CheckResult("slot_blocked", f"{slot['name']}이(가) 휴강으로 차단되어 있습니다.")

    if not STATE_PATH.exists():
        raise CheckResult(
            "no_session",
            "storage_state.json 이 없습니다. 먼저 `python checker.py --setup-login` 으로 밴드 로그인 세션을 저장하세요.",
        )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            storage_state=str(STATE_PATH), locale="ko-KR", timezone_id="Asia/Seoul"
        )
        page = context.new_page()
        page.set_default_timeout(cfg["timeout_ms"])

        try:
            page.goto(cfg["band_url"], wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

            if "로그인" in (page.title() or "") or page.locator("text=로그인이 필요").count():
                raise CheckResult("no_session", "밴드 로그인이 풀렸습니다. --setup-login 으로 다시 저장하세요.")

            post = find_target_post(page, target)
            if debug_dump:
                DEBUG_DIR.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                (DEBUG_DIR / f"list-{stamp}.html").write_text(page.content(), encoding="utf-8")

            if not post:
                raise CheckResult(
                    "no_post",
                    f"{target.month}/{target.day}({WEEKDAY_KO[target.weekday()]}) 출석 예약 글을 찾지 못했습니다.",
                )

            page.goto(post["href"], wait_until="domcontentloaded")
            page.wait_for_timeout(1200)

            if debug_dump:
                DEBUG_DIR.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                (DEBUG_DIR / f"post-{stamp}.html").write_text(page.content(), encoding="utf-8")

            body_text = page.inner_text("body")
            if not text_mentions_date(body_text, target) and not text_mentions_date(post["title"], target):
                raise CheckResult(
                    "no_post",
                    f"최신 글 '{post['title'][:40]}'이 {target.month}/{target.day} 글이 아닙니다.",
                    post_title=post["title"],
                )

            option, spots_text = find_slot_option(page, slot_kw)
            if option is None:
                raise CheckResult(
                    "no_slot",
                    f"'{slot_kw}' 투표 항목을 찾지 못했습니다. (투표가 아닌 공지일 수 있음)",
                    post_title=post["title"],
                )

            taken, cap = parse_spot_count(spots_text)
            cap = cap or capacity

            if page.locator("text=투표 완료").count() or "이미 투표" in body_text:
                return make_record(
                    cfg, "already", "이미 투표한 글이거나 참여 완료 상태입니다.",
                    mode=mode, taken=taken, capacity=cap, post_title=post["title"],
                    duration_ms=int((time.time() - started) * 1000),
                )

            if taken is not None and taken >= cap:
                raise CheckResult(
                    "full",
                    f"{slot_kw} 정원 마감 ({taken}/{cap}).",
                    post_title=post["title"], taken=taken, capacity=cap,
                )

            if dry_run:
                return make_record(
                    cfg, "dry", f"투표 가능: {slot_kw} (현재 {taken}/{cap}) — 실제로는 누르지 않았습니다.",
                    mode="dry", taken=taken, capacity=cap, post_title=post["title"],
                    duration_ms=int((time.time() - started) * 1000),
                )

            option.click(timeout=8000)
            page.wait_for_timeout(600)
            submit = click_first(
                page,
                [
                    "button:has-text('투표하기')",
                    "text=투표하기",
                    "button:has-text('확인')",
                    "text=참여하기",
                ],
            )
            page.wait_for_timeout(1800)
            after = page.inner_text("body")
            done = bool(re.search(r"투표\s*완료|참여\s*완료|취소하기", after))

            if not done:
                raise CheckResult(
                    "unverified",
                    "투표 제출 후 완료 표시를 확인하지 못했습니다. 밴드에서 직접 확인이 필요합니다.",
                    post_title=post["title"], taken=taken, capacity=cap,
                )

            record = make_record(
                cfg, "voted", f"{slot_kw} 출석 예약 완료.",
                mode="live", taken=(taken + 1 if taken is not None else None), capacity=cap,
                post_title=post["title"], submitted=bool(submit),
                duration_ms=int((time.time() - started) * 1000),
            )
            try:
                store.init_db()
                store.add_checkin(
                    int(cfg.get("user_id") or 1), target.isoformat(), slot["id"],
                    status="voted", taken=record["taken"], capacity=cap,
                    note="checker.py 자동 예약",
                )
            except Exception:
                pass  # 기록 실패가 예약 결과를 뒤집지는 않는다.
            return record
        finally:
            context.close()
            browser.close()


def find_target_post(page, target: date) -> dict | None:
    """글 목록에서 대상 날짜가 제목/미리보기에 있는 최신 글을 고른다."""
    items = page.evaluate(
        """() => {
            const out = [];
            const anchors = document.querySelectorAll('a[href*="/post/"]');
            for (const a of anchors) {
                const href = a.href;
                if (!/\\/post\\/\\d+/.test(href)) continue;
                const title = (a.innerText || '').trim();
                if (!title) continue;
                out.push({href, title});
            }
            return out;
        }"""
    )
    seen = set()
    for item in items:
        if item["href"] in seen:
            continue
        seen.add(item["href"])
        if text_mentions_date(item["title"], target) and re.search(r"출석|예약|신청", item["title"]):
            return item
    # 제목이 날짜를 안 담는 경우가 있어, 본문 미리보기까지 확인한다.
    for item in items:
        if text_mentions_date(item["title"], target):
            return item
    return None


def find_slot_option(page, keyword: str):
    """투표 항목 후보를 찾아 (locator, 항목 텍스트)를 반환한다."""
    candidates = page.locator(
        "label:has-text('%s'), li:has-text('%s'), div[role=radio]:has-text('%s')" % (keyword, keyword, keyword)
    )
    count = candidates.count()
    for i in range(count):
        loc = candidates.nth(i)
        try:
            text = (loc.inner_text() or "").strip()
        except Exception:
            continue
        if keyword in text and len(text) < 200:
            return loc, text
    return None, ""


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="온점핑 밴드 출석 예약 체커")
    parser.add_argument("--setup-login", action="store_true", help="브라우저에서 밴드 로그인 후 세션 저장")
    parser.add_argument("--dry-run", action="store_true", help="실제 투표 없이 확인만")
    parser.add_argument("--debug-dump", action="store_true", help="페이지 HTML을 logs/debug/ 에 저장")
    parser.add_argument("--json", action="store_true", help="결과를 한 줄 JSON으로 출력")
    args = parser.parse_args(argv)

    cfg = load_config()

    if args.setup_login:
        return open_login_setup(cfg)

    try:
        record = run_check(cfg, dry_run=args.dry_run, debug_dump=args.debug_dump)
    except CheckResult as res:
        record = make_record(
            cfg, res.status, res.message,
            mode="dry" if args.dry_run else "live", **res.extra,
        )
        append_log(record)
        print(f"[{record['status']}] {record['message']}")
        if args.json:
            print(json.dumps(record, ensure_ascii=False))
        return 1
    except Exception as exc:  # 네트워크/DOM 오류
        record = make_record(cfg, "error", f"{type(exc).__name__}: {exc}", mode="dry" if args.dry_run else "live")
        append_log(record)
        print(f"[error] {record['message']}")
        if args.json:
            print(json.dumps(record, ensure_ascii=False))
        return 2

    append_log(record)
    icon = {"voted": "✓", "dry": "·", "already": "="}.get(record["status"], "!")
    print(f"{icon} [{record['status']}] {record['message']}")
    if args.json:
        print(json.dumps(record, ensure_ascii=False))
    return 0 if record["status"] in ("voted", "dry", "already") else 1


if __name__ == "__main__":
    sys.exit(main())
