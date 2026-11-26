"""Vercel 서버리스 진입점 (WSGI).

Vercel 은 이 파일의 `app` 을 호출한다. localhost 서버와 같은 app_core 를 쓰므로
API 동작은 동일하고, 저장소만 /tmp 로 옮긴다(서버리스 파일시스템은 읽기 전용이며
콜드 스타트마다 초기 시드 데이터로 다시 만들어진다).
"""

from __future__ import annotations

import json
import os
import sys
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Vercel 은 /tmp 만 쓸 수 있다. 콜드 스타트마다 시드 데이터가 다시 생성된다.
os.environ.setdefault("ONJUMPING_DATA_DIR", "/tmp/onjumping-data")

import app_core  # noqa: E402
import store  # noqa: E402

store.init_db()

STATIC_DIR = ROOT / "static"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def _respond(start_response, status: int, body: bytes, headers: dict):
    header_list = [("Content-Length", str(len(body))), ("Cache-Control", "no-store")]
    header_list.extend((k, v) for k, v in headers.items())
    start_response(f"{status} {_reason(status)}", header_list)
    return [body]


def _reason(status: int) -> str:
    return {
        200: "OK", 201: "Created", 202: "Accepted", 400: "Bad Request",
        401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
        405: "Method Not Allowed", 409: "Conflict", 500: "Internal Server Error",
    }.get(status, "OK")


def _file_response(start_response, path: Path):
    if not path.is_file():
        return _json(start_response, 404, {"ok": False, "error": "not found"}, {})
    ctype = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return _respond(start_response, 200, path.read_bytes(), {"Content-Type": ctype})


def _json(start_response, status: int, payload: dict, headers: dict):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _respond(start_response, status, body, {"Content-Type": "application/json; charset=utf-8", **headers})


def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO") or "/"
    query = parse_qs(environ.get("QUERY_STRING", ""))

    if method == "GET" and path in ("/", "/index.html"):
        return _file_response(start_response, STATIC_DIR / "index.html")
    if method == "GET" and path in ("/style.css", "/app.js"):
        return _file_response(start_response, STATIC_DIR / path.lstrip("/"))
    if method == "GET" and path.startswith("/static/"):
        return _file_response(start_response, STATIC_DIR / path[len("/static/"):])
    if not path.startswith("/api/"):
        return _json(start_response, 404, {"ok": False, "error": "not found"}, {})

    body: dict = {}
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length:
        raw = environ["wsgi.input"].read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
            body = parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}

    user_id = None
    cookie_header = environ.get("HTTP_COOKIE", "")
    if cookie_header:
        try:
            morsel = SimpleCookie(cookie_header).get("onjumping_uid")
            user_id = int(morsel.value) if morsel else None
        except (ValueError, TypeError):
            user_id = None

    try:
        status, payload, headers = app_core.handle(method, path, query, body, user_id)
    except Exception as exc:  # pragma: no cover - 서버리스에서 500 대신 JSON
        return _json(start_response, 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, {})
    return _json(start_response, status, payload, headers)


# 로컬에서 `vercel dev` 없이 확인할 때: python api/index.py
if __name__ == "__main__":
    from wsgiref.simple_server import make_server

    with make_server("127.0.0.1", 8766, app) as httpd:
        print("WSGI 서버: http://127.0.0.1:8766")
        httpd.serve_forever()
