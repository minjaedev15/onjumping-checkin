"""Vercel 서버리스 진입점 (WSGI).

Vercel 은 이 파일의 `app` 을 호출한다. 로컬 server.py 와 같은 webapp.app 을 쓰므로
화면·동작이 같고, 저장소만 /tmp 로 옮긴다(서버리스 파일시스템은 읽기 전용이며
콜드 스타트마다 시드 계정으로 다시 만들어진다).
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
