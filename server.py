#!/usr/bin/env python3
"""온점핑 출석 예약기 — 로컬 서버 (http.server).

    python server.py            # http://127.0.0.1:8765
    python server.py --port 9000

라우팅은 app_core.py 에 있고, 이 파일은 소켓/정적 파일만 다룬다. 같은 코어를
Vercel 서버리스(api/index.py)가 공유한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import app_core
import store

STATIC_DIR = Path(__file__).resolve().parent / "static"
SESSION_COOKIE = "onjumping_uid"


class Handler(BaseHTTPRequestHandler):
    server_version = "OnJumpingCheckin/1.2"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # ---------------- helpers ----------------

    def user_id(self) -> int | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            morsel = SimpleCookie(raw).get(SESSION_COOKIE)
            return int(morsel.value) if morsel else None
        except (ValueError, TypeError):
            return None

    def read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def send_payload(self, status: int, payload: dict, headers: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path) -> None:
        if not path.is_file():
            return self.send_payload(404, {"ok": False, "error": "not found"}, {})
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(path.suffix.lower(), "application/octet-stream")
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------------- routing ----------------

    def dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if method == "GET" and path in ("/", "/index.html"):
            return self.send_file(STATIC_DIR / "index.html")
        if method == "GET" and path.startswith("/static/"):
            return self.send_file(STATIC_DIR / path[len("/static/"):])
        if not path.startswith("/api/"):
            return self.send_payload(404, {"ok": False, "error": "not found"}, {})

        body = self.read_body() if method in ("POST", "PATCH", "DELETE") else {}
        status, payload, headers = app_core.handle(
            method, path, parse_qs(parsed.query), body, self.user_id()
        )
        self.send_payload(status, payload, headers)

    def do_GET(self) -> None:
        self.dispatch("GET")

    def do_POST(self) -> None:
        self.dispatch("POST")

    def do_PATCH(self) -> None:
        self.dispatch("PATCH")

    def do_DELETE(self) -> None:
        self.dispatch("DELETE")


def main() -> int:
    parser = argparse.ArgumentParser(description="온점핑 출석 예약기 대시보드")
    parser.add_argument("--host", default="127.0.0.1", help="기본 127.0.0.1 (외부 노출 금지)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    store.init_db()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"온점핑 대시보드: http://{args.host}:{args.port}")
    print("종료: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
