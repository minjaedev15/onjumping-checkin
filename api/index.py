"""Vercel 서버리스 진입점 (WSGI).

Vercel 은 이 파일의 `app` 을 호출한다. 로컬 server.py 와 같은 webapp.app 을 쓴다.

저장소는 환경변수로 정해진다.
    KV_REST_API_URL / KV_REST_API_TOKEN (Vercel Storage: Upstash Redis) → 영구 저장소
    없으면 → /tmp SQLite (화면 확인용, 콜드 스타트마다 초기화)

관리자 비밀번호는 ADMIN_PASSWORD(또는 ADMIN_PASSWORD_HASH), 세션 서명 키는
SESSION_SECRET 환경변수로만 들어간다. 저장소에는 비밀번호가 남지 않는다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("ONJUMPING_DATA_DIR", "/tmp/onjumping-data")

import store  # noqa: E402
import webapp  # noqa: E402

store.init_db()

app = webapp.app
