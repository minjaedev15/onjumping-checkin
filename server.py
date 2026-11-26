#!/usr/bin/env python3
"""On점핑 출석 — 로컬 서버.

    python server.py              # http://127.0.0.1:8765
    python server.py --port 9000

화면과 라우팅은 webapp.py 에 있고, 이 파일은 로컬 소켓만 연다. Vercel 은 같은
webapp.app 을 api/index.py 로 감싸서 쓴다.
"""

from __future__ import annotations

import argparse
import sys
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import store
import webapp


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class Handler(WSGIRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


def main() -> int:
    parser = argparse.ArgumentParser(description="On점핑 출석")
    parser.add_argument("--host", default="127.0.0.1", help="기본 127.0.0.1 (외부 노출 금지)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    store.init_db()
    with make_server(args.host, args.port, webapp.app,
                     server_class=ThreadingWSGIServer, handler_class=Handler) as httpd:
        print(f"On점핑 출석: http://{args.host}:{args.port}")
        print("관리자: admin / admin1234 · 회원: minjae / 1234")
        print("종료: Ctrl+C")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n종료합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
