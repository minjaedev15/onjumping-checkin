# 온점핑 출석 예약기

탕정지중해마을 **On점핑** 네이버 밴드의 "다음 날 출석 예약" 글을 확인해,
지정한 타임(기본 **셋째타임 · 저녁 6시30분**)에 선착순으로 출석을 잡아 주는
로컬 도구입니다. 밴드 조작은 Playwright로, 화면은 파이썬 표준 라이브러리만으로
동작합니다.

```
checker.py   밴드 접속 → 글 찾기 → 투표 (또는 --dry-run)
server.py    대시보드 + JSON API (127.0.0.1:8765)
store.py     SQLite 데이터 계층 (users / notices / checkins)
static/      에디토리얼 톤의 대시보드 UI
```

## 주요 기능

| 구분 | 내용 |
|---|---|
| 자동 예약 | 일~목 21:01에 올라오는 다음 날 예약 글에서 설정한 타임을 찾아 투표 |
| 미리 확인 | 대시보드에서 dry-run만 실행 — 실제 투표는 나가지 않음 |
| 역할 | **관리자**는 유저·공지·설정을 관리, **일반 유저**는 공지와 자기 예약 현황만 열람 |
| 유저 관리 | 일반 유저 추가 / 목표 타임 수정 / 삭제 (관리자) |
| 공지 | "오늘의 공지" 등록·수정·삭제 (관리자), 모든 사용자에게 상단 노출 |
| 기록 | 모든 실행 결과를 `logs/history.json` 과 `checkins` 테이블에 남김 |

## 설치

```bash
cd projects/onjumping-checkin
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## 1) 밴드 로그인 세션 저장 (최초 1회)

```bash
python checker.py --setup-login
```

브라우저가 열리면 네이버 밴드에 로그인하고 터미널에서 Enter를 누릅니다.
`storage_state.json` 에 세션이 저장되며, 이 파일은 `.gitignore` 에 포함되어 있습니다.
**이 파일에는 로그인 쿠키가 들어 있으니 절대 공유하거나 커밋하지 마세요.**

## 2) 확인 / 실행

```bash
python checker.py --dry-run   # 어떤 글·타임에 들어갈 수 있는지만 확인
python checker.py             # 실제 투표
python checker.py --debug-dump  # 선택자 조정용 HTML 저장 (logs/debug/)
```

결과는 한 줄로 출력되고 `logs/history.json` 에 기록됩니다.

| 상태 | 의미 |
|---|---|
| `voted` | 예약 완료 |
| `dry` | 확인만 함 (투표 안 함) |
| `already` | 이미 투표한 상태 |
| `full` | 정원 마감 |
| `no_post` / `no_slot` | 대상 글 또는 타임 항목을 못 찾음 |
| `no_session` | 세션 없음 → `--setup-login` 필요 |
| `unverified` | 제출 후 완료 표시를 확인하지 못함 (밴드에서 직접 확인) |

## 3) 대시보드

```bash
python server.py          # http://127.0.0.1:8765
python server.py --port 9000
```

첫 실행 시 `data/app.db` 가 만들어지고 기본 계정이 들어갑니다.

- 관리자: **황보민재** (기본 계정, 삭제 불가)
- 일반 유저: 김도윤, 이서연 (샘플)

계정 전환은 우측 상단 **계정 전환** 버튼에서 합니다. 로컬 전용 도구라 비밀번호는
없고, 선택한 사용자 id가 쿠키로 유지됩니다. 외부에 노출할 계획이라면 인증을 먼저
붙이세요(기본 바인딩은 `127.0.0.1`).

### 역할별 화면

- **관리자**: 오늘의 공지(수정·삭제) · 주간 시간표/자동 실행 토글 · 예약 설정 ·
  미리 확인 · 실행 로그 · 일반 유저 관리 · 공지 관리
- **일반 유저**: 오늘의 공지 · 자기 시간표 · 내 예약 현황 (유저/공지 관리 불가, 서버에서 403)

## 4) 정기 실행

cron 예시 (매일 21:01, 일~목만):

```cron
1 21 * * 0-4 cd /path/to/onjumping-checkin && .venv/bin/python checker.py >> logs/cron.log 2>&1
```

Windows 작업 스케줄러에서도 동일하게 `checker.py` 를 실행하면 됩니다.
서버(`server.py`)는 상시 켜 둘 필요 없이, 설정을 바꿀 때만 띄워도 됩니다.

## API

인증: 세션 쿠키(`onjumping_uid`). 관리자 전용 요청은 일반 유저에게 `403`.

| Method | Path | 권한 | 설명 |
|---|---|---|---|
| POST | `/api/session` | — | `{user_id}` 로 로그인 |
| DELETE | `/api/session` | 로그인 | 로그아웃 |
| GET | `/api/bootstrap` | — | 화면 초기 데이터 (역할별 필터) |
| GET | `/api/status` | 로그인 | 다음 실행 시각·밴드 세션 상태 |
| GET/POST | `/api/config` | 관리자 | 예약 설정 조회·저장 |
| GET | `/api/logs` | 로그인 | 실행 기록 |
| POST | `/api/run-dry` | 관리자 | dry-run 시작 → `job_id` |
| GET | `/api/run-dry/{id}` | 로그인 | 작업 상태·출력 |
| GET/POST | `/api/users` | 관리자 | 유저 목록·추가 |
| PATCH/DELETE | `/api/users/{id}` | 관리자 | 유저 수정·삭제 |
| GET/POST | `/api/notices` | 로그인 / 관리자 | 공지 목록·등록 |
| PATCH/DELETE | `/api/notices/{id}` | 관리자 | 공지 수정·삭제 |

## 데이터

```
data/app.db          users, notices, checkins (SQLite)
logs/history.json    checker.py 실행 기록 (JSON 배열)
storage_state.json   밴드 로그인 세션 (git 제외)
config.json          타임·정원·실행 요일/시각
```

## 주의

- 네이버 밴드는 공개 API가 없습니다. 화면 구조가 바뀌면 `checker.py` 의 선택자
  (`find_target_post`, `find_slot_option`)를 조정해야 하며, `--debug-dump` 로 저장한
  HTML을 보고 맞추면 됩니다.
- 정원이 이미 찼거나 예약 글이 없으면 투표하지 않고 그 사실만 기록합니다.
- 본인 계정으로 본인 출석을 잡는 용도입니다. 계정 공유·대리 예약에는 쓰지 마세요.
- 자동화는 사이트 정책과 다를 수 있으니 사용 전 밴드 운영진 안내를 확인하세요.

---

## English

`onjumping-checkin` is a small local tool that reserves a spot in the daily
"next-day attendance" poll of the **On점핑** Naver Band. It reads the band post with
Playwright using a saved session, picks the configured time slot (third slot, 18:30),
and votes only when seats remain.

```bash
pip install -r requirements.txt && playwright install chromium
python checker.py --setup-login   # one-time: save Band session
python checker.py --dry-run       # inspect without voting
python checker.py                 # vote
python server.py                  # dashboard at http://127.0.0.1:8765
```

The dashboard has two roles: an **admin** (manage members, notices, schedule) and
**members** (read the notice and their own reservation status). Auth is cookie-based
and intended for localhost use only. `storage_state.json`, `data/app.db` and
`logs/history.json` stay out of git.
