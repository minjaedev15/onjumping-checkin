"""로컬 화면 캡처 (개발용).

    ADMIN_PASSWORD=... MEMBER_PASSWORD=... python tools/visual_qa.py

로컬 서버를 띄우고 로그인해서 홈 블록·출석·공지(이미지 포함)·관리자 화면을
docs/screenshots 에 저장한다. 비밀번호는 환경변수로만 받는다(코드에 두지 않는다).
Playwright 와 `playwright install chromium` 이 필요하다.

ONJUMPING_QA_IMAGE 로 공지 이미지 파일 경로를 지정하면 그 사진을 실제 폼으로
올려(브라우저 안에서 리사이즈·압축) 이미지 표시까지 확인한다.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("ONJUMPING_DATA_DIR", tempfile.mkdtemp(prefix="onjumping-qa-"))
os.environ.setdefault("SESSION_SECRET", secrets.token_hex(16))

OUT = Path(os.environ.get("ONJUMPING_SHOT_DIR") or (ROOT / "docs" / "screenshots"))
PORT = int(os.environ.get("ONJUMPING_SHOT_PORT") or 8801)
BASE = f"http://127.0.0.1:{PORT}"

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
MEMBER_PASSWORD = os.environ.get("MEMBER_PASSWORD", "")
SAMPLE_IMAGE = os.environ.get("ONJUMPING_QA_IMAGE", "")

NOTICE_TEXT = (
    "이번 주 금요일 점핑장 사정으로 18:30 타임만 운영해요.\n"
    "19:40 타임은 쉬어 갑니다. 미리 확인해 주세요!"
)

import store  # noqa: E402


def login(page, username: str, password: str) -> None:
    page.goto(f"{BASE}/login", wait_until="domcontentloaded")
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('button:has-text("로그인")')
    page.wait_for_load_state("domcontentloaded")


def wait_for_server(page) -> None:
    for _ in range(80):
        try:
            page.goto(f"{BASE}/login", timeout=1000)
            return
        except Exception:
            page.wait_for_timeout(250)
    raise RuntimeError("로컬 서버가 응답하지 않습니다.")


def shoot(page, name: str) -> None:
    path = OUT / name
    page.screenshot(path=str(path), full_page=True)
    print(f"saved {path}")


def main() -> int:
    if not ADMIN_PASSWORD or not MEMBER_PASSWORD:
        print("ADMIN_PASSWORD 와 MEMBER_PASSWORD 환경변수가 필요합니다.")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)

    store.reset_cache()
    store.init_db()
    if not any(u["username"] == "minjae" for u in store.list_users()):
        store.create_user("minjae", MEMBER_PASSWORD, "민재")

    server = subprocess.Popen([sys.executable, "server.py", "--port", str(PORT)],
                              cwd=str(ROOT), env=dict(os.environ))
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()

            # ---------- 모바일 (390px) ----------
            mobile = browser.new_context(viewport={"width": 390, "height": 844},
                                         device_scale_factor=2, is_mobile=True,
                                         has_touch=True)
            page = mobile.new_page()
            wait_for_server(page)
            shoot(page, "login.png")

            login(page, "admin", ADMIN_PASSWORD)
            shoot(page, "admin-home.png")

            # 공지 + 이미지 실제 업로드
            page.goto(f"{BASE}/admin/notices", wait_until="domcontentloaded")
            form = page.locator("form.notice-form").first
            form.locator("textarea").fill(NOTICE_TEXT)
            if SAMPLE_IMAGE and Path(SAMPLE_IMAGE).exists():
                form.locator('input[type="file"]').set_input_files(SAMPLE_IMAGE)
                page.wait_for_function(
                    "() => { var i = document.querySelector('form.notice-form input[name=image_data]');"
                    " return i && i.value.length > 500; }",
                    timeout=20000,
                )
            form.locator('button.wide').first.click()
            page.wait_for_load_state("domcontentloaded")
            shoot(page, "admin-notices.png")
            page.goto(f"{BASE}/notices", wait_until="domcontentloaded")
            shoot(page, "notices.png")
            page.goto(f"{BASE}/checkin", wait_until="domcontentloaded")
            shoot(page, "checkin.png")

            # 회원 화면
            mobile.clear_cookies()
            login(page, "minjae", MEMBER_PASSWORD)
            shoot(page, "member.png")
            page.goto(f"{BASE}/checkin", wait_until="domcontentloaded")
            shoot(page, "checkin-member.png")

            # ---------- 데스크톱 (1280px) ----------
            desktop = browser.new_context(viewport={"width": 1280, "height": 900})
            page = desktop.new_page()
            wait_for_server(page)
            login(page, "admin", ADMIN_PASSWORD)
            shoot(page, "home-desktop.png")
            page.goto(f"{BASE}/admin", wait_until="domcontentloaded")
            shoot(page, "admin.png")
            page.goto(f"{BASE}/admin/users", wait_until="domcontentloaded")
            shoot(page, "admin-users.png")

            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
