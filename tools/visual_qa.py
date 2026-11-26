"""로컬 화면 캡처 (개발용).

    python tools/visual_qa.py

로컬 서버를 띄우고 로그인해서 index/admin 화면을 docs/screenshots 에 저장한다.
Playwright 와 `playwright install chromium` 이 필요하다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
PORT = 8801
BASE = f"http://127.0.0.1:{PORT}"


def login(page, username: str, password: str) -> None:
    page.goto(f"{BASE}/login", wait_until="domcontentloaded")
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('button:has-text("로그인")')
    page.wait_for_load_state("domcontentloaded")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT)], cwd=str(ROOT))
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 420, "height": 900})
            for _ in range(40):
                try:
                    page.goto(f"{BASE}/login", timeout=1000)
                    break
                except Exception:
                    page.wait_for_timeout(250)

            login(page, "minjae", "1234")
            page.screenshot(path=str(OUT / "member.png"), full_page=True)
            print(f"saved {OUT / 'member.png'}")

            login(page, "admin", "admin1234")
            page.goto(f"{BASE}/admin", wait_until="domcontentloaded")
            page.screenshot(path=str(OUT / "admin.png"), full_page=True)
            print(f"saved {OUT / 'admin.png'}")
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
