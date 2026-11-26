"""로컬 화면 캡처 (개발용).

    ADMIN_PASSWORD=... MEMBER_PASSWORD=... python tools/visual_qa.py

로컬 서버를 띄우고 로그인해서 로그인·회원·관리자 화면을 docs/screenshots 에 저장한다.
비밀번호는 환경변수로만 받는다(코드에 두지 않는다).
Playwright 와 `playwright install chromium` 이 필요하다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(os.environ.get("ONJUMPING_SHOT_DIR") or (ROOT / "docs" / "screenshots"))
PORT = int(os.environ.get("ONJUMPING_SHOT_PORT") or 8801)
BASE = f"http://127.0.0.1:{PORT}"

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
MEMBER_PASSWORD = os.environ.get("MEMBER_PASSWORD", "")


def login(page, username: str, password: str) -> None:
    page.goto(f"{BASE}/login", wait_until="domcontentloaded")
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('button:has-text("로그인")')
    page.wait_for_load_state("domcontentloaded")


def main() -> int:
    if not ADMIN_PASSWORD:
        print("ADMIN_PASSWORD 환경변수가 필요합니다.")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    server = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT)], cwd=str(ROOT), env=env)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 430, "height": 900})
            for _ in range(60):
                try:
                    page.goto(f"{BASE}/login", timeout=1000)
                    break
                except Exception:
                    page.wait_for_timeout(250)

            page.screenshot(path=str(OUT / "login.png"), full_page=True)
            print(f"saved {OUT / 'login.png'}")

            if MEMBER_PASSWORD:
                login(page, "minjae", MEMBER_PASSWORD)
                page.screenshot(path=str(OUT / "member.png"), full_page=True)
                print(f"saved {OUT / 'member.png'}")

            login(page, "admin", ADMIN_PASSWORD)
            page.screenshot(path=str(OUT / "admin.png"), full_page=True)
            print(f"saved {OUT / 'admin.png'}")
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
