"""비주얼 QA: 로컬 서버를 띄우고 화면을 캡처한다.

    python tools/visual_qa.py

결과는 docs/qa-*.png 에 저장된다. (개발용 도구, 배포에는 영향 없음)
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs"
PORT = 8801
BASE = f"http://127.0.0.1:{PORT}"


def shot(page, name, full=True):
    OUT.mkdir(exist_ok=True)
    path = OUT / f"qa-{name}.png"
    page.screenshot(path=str(path), full_page=full)
    print(f"saved {path}")
    return path


def login(context, uid):
    context.clear_cookies()
    context.add_cookies([{
        "name": "onjumping_uid", "value": str(uid), "domain": "127.0.0.1", "path": "/",
    }])


def main() -> int:
    server = subprocess.Popen(
        [sys.executable, "server.py", "--port", str(PORT)], cwd=str(ROOT),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(1.6)
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()

            # ---- 일반 유저 · 데스크톱 ----
            ctx = browser.new_context(viewport={"width": 1440, "height": 1000},
                                      locale="ko-KR", timezone_id="Asia/Seoul")
            page = ctx.new_page()
            page.on("console", lambda m: errors.append(f"console[{m.type}] {m.text}") if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
            login(ctx, 2)
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_timeout(700)
            shot(page, "member-desktop")

            # 글씨 더 크게
            page.goto(f"{BASE}/#more", wait_until="networkidle")
            page.click("text=내 기록")
            page.wait_for_timeout(400)
            shot(page, "member-history")
            page.check("#big-font")
            page.wait_for_timeout(300)
            page.click("text=오늘 출석")
            page.wait_for_timeout(400)
            shot(page, "member-bigfont")
            page.uncheck("#big-font")

            # 출석 + 도장/컨페티
            page.wait_for_timeout(300)
            btn = page.locator("[data-checkin]").first
            if btn.count():
                btn.click()
                page.wait_for_timeout(700)
                shot(page, "member-checkin-effect", full=False)
                page.wait_for_timeout(1400)

            # ---- 일반 유저 · 모바일 ----
            mobile = browser.new_context(viewport={"width": 390, "height": 844},
                                         device_scale_factor=2, is_mobile=True,
                                         locale="ko-KR", timezone_id="Asia/Seoul")
            mpage = mobile.new_page()
            login(mobile, 2)
            mpage.goto(BASE, wait_until="networkidle")
            mpage.wait_for_timeout(600)
            shot(mpage, "member-mobile")

            # ---- 관리자 ----
            login(ctx, 1)
            page.goto(BASE, wait_until="networkidle")
            page.wait_for_timeout(700)
            shot(page, "admin-desktop")

            # 부분 휴강 상태
            page.locator("[data-block-slot]").first.click()
            page.wait_for_timeout(900)
            shot(page, "admin-partial-block")
            page.locator("[data-block-slot]").first.click()
            page.wait_for_timeout(600)

            page.click("text=운영")
            page.wait_for_timeout(500)
            shot(page, "admin-more")

            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    if errors:
        print("\n브라우저 오류:")
        for e in dict.fromkeys(errors):
            print(" -", e)
    else:
        print("\n브라우저 콘솔 오류 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
