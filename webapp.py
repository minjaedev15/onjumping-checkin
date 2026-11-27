"""온점핑 출석 — 서버 렌더링 화면 (WSGI).

로컬 server.py 와 Vercel api/index.py 가 같은 `app` 을 쓴다. 자바스크립트 없이
폼 전송 → 리다이렉트 → 플래시 메시지로 동작하고, 화면은 페이지별로 나뉜다.

    /                 홈 블록 (로그인 필요)
    /checkin          출석하기 (타임 선택 + 공지)
    /notices          공지
    /auto             평일 자동 출석 · 내 정보
    /login            로그인
    /admin            관리자: 오늘 출석 명단 · 휴강
    /admin/notices    관리자: 공지 관리
    /admin/users      관리자: 회원 관리
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
import urllib.parse
from datetime import date
from http.cookies import SimpleCookie
from pathlib import Path

import store

COOKIE = "onjumping_uid"
FLASH = "onjumping_flash"
SESSION_MAX_AGE = 60 * 60 * 24 * 30
SECURE_COOKIE = bool(os.environ.get("VERCEL"))

REASONS = {
    200: "OK", 201: "Created", 303: "See Other", 400: "Bad Request",
    401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 500: "Internal Server Error",
}


# --------------------------------------------------------------------------- #
# 스타일
# --------------------------------------------------------------------------- #

CSS = """
* { box-sizing: border-box; }

body {
    margin: 0;
    font-family: Pretendard, 'Noto Sans KR', -apple-system, BlinkMacSystemFont,
                 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
    background: #f3f5f9;
    color: #222;
    -webkit-text-size-adjust: 100%;
}

/* ---------- 홈 상단 바 ---------- */
.topbar { background: #5865f2; color: #fff; }
.topbar-inner {
    max-width: 720px;
    margin: 0 auto;
    padding: 14px 18px;
    display: flex;
    align-items: center;
    gap: 12px;
}
.topbar.wide .topbar-inner { max-width: 980px; }
.brand {
    color: #fff;
    text-decoration: none;
    font-size: 19px;
    font-weight: 800;
    letter-spacing: -0.4px;
    white-space: nowrap;
}
.who {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    color: #dfe3ff;
    white-space: nowrap;
}
.who b { color: #fff; font-weight: 700; }
.who a {
    color: #dfe3ff;
    text-decoration: none;
    padding: 7px 10px;
    border-radius: 9px;
}
.who a:hover { background: rgba(255, 255, 255, 0.14); }

/* ---------- 서브 화면 앱바 (← 뒤로 + 제목) ---------- */
.appbar {
    background: #fff;
    border-bottom: 1px solid #e9ecf3;
    position: sticky;
    top: 0;
    z-index: 10;
}
.appbar-inner {
    max-width: 720px;
    margin: 0 auto;
    padding: 9px 12px;
    display: flex;
    align-items: center;
    gap: 6px;
}
.appbar.wide .appbar-inner { max-width: 980px; }
.back {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 8px 11px;
    border-radius: 11px;
    color: #4a4f5c;
    font-size: 15px;
    font-weight: 600;
    text-decoration: none;
    white-space: nowrap;
}
.back:hover { background: #f1f3f8; }
.appbar h1 {
    margin: 0;
    font-size: 17px;
    font-weight: 700;
    letter-spacing: -0.3px;
}

.container { max-width: 720px; margin: 18px auto 48px; padding: 0 16px; }
.container.wide { max-width: 980px; }

.dash-head { margin: 4px 2px 16px; }
.dash-head h1 { margin: 0; font-size: 22px; letter-spacing: -0.5px; }
.dash-head p { margin: 6px 0 0; color: #7b818e; font-size: 14px; }

.page-note { margin: 2px 4px 14px; color: #7b818e; font-size: 13.5px; }

/* ---------- 앱 블록 (홈 대시보드) ---------- */
.blocks {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
}
.block {
    position: relative;
    display: flex;
    flex-direction: column;
    min-height: 118px;
    padding: 16px;
    background: #fff;
    border-radius: 20px;
    box-shadow: 0 3px 12px rgba(0, 0, 0, 0.06);
    color: #222;
    text-decoration: none;
    transition: transform 0.08s ease, box-shadow 0.12s ease;
}
.block:active { transform: scale(0.98); }
.block:hover { box-shadow: 0 6px 18px rgba(0, 0, 0, 0.09); }
.block-icon {
    width: 46px;
    height: 46px;
    margin-bottom: 12px;
    border-radius: 15px;
    background: #eef0ff;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 23px;
    line-height: 1;
}
.block-title {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: auto;
    font-size: 16px;
    font-weight: 700;
    letter-spacing: -0.3px;
}
.block-sub { margin-top: 4px; color: #8a8f9c; font-size: 12.5px; line-height: 1.45; }
.block-sub.strong { color: #4a4f5c; font-weight: 600; }

.block.notice {
    grid-column: 1 / -1;
    flex-direction: row;
    align-items: center;
    gap: 14px;
    min-height: 0;
    padding: 16px;
}
.block.notice .thumb {
    width: 78px;
    height: 78px;
    flex: 0 0 auto;
    border-radius: 16px;
    object-fit: cover;
}
.block.notice .thumb-empty {
    width: 78px;
    height: 78px;
    flex: 0 0 auto;
    border-radius: 16px;
    background: #eef0ff;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 30px;
}
.block.notice .notice-text { min-width: 0; flex: 1 1 auto; }
.block.notice .block-title { margin-top: 0; }
.block.notice .block-sub {
    margin-top: 5px;
    color: #4a4f5c;
    font-size: 14px;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
}
.block.notice .block-meta { margin-top: 5px; color: #9aa0ac; font-size: 12px; }

.block.admin .block-icon { background: #f1ecff; }
.section-label {
    margin: 24px 4px 10px;
    color: #8a8f9c;
    font-size: 12.5px;
    font-weight: 700;
    letter-spacing: 0.2px;
}

/* ---------- 카드 · 폼 ---------- */
.card {
    background: #fff;
    border-radius: 16px;
    padding: 20px 22px;
    margin-bottom: 16px;
    box-shadow: 0 3px 12px rgba(0, 0, 0, 0.06);
}
.card > h2 { margin: 0 0 14px; font-size: 18px; letter-spacing: -0.3px; }
.card > h2:last-child { margin-bottom: 0; }

.slots { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.container.wide .slots { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.slots .card { margin-bottom: 0; }

h1, h2, h3 { font-weight: 700; }

input, select, textarea {
    width: 100%;
    padding: 12px 13px;
    margin: 6px 0;
    border: 1px solid #dde1e9;
    border-radius: 9px;
    font-size: 16px;
    font-family: inherit;
    background: #fff;
    color: #222;
}
input:focus, select:focus, textarea:focus { outline: 2px solid #c3c9ff; border-color: #5865f2; }
textarea { min-height: 104px; resize: vertical; line-height: 1.55; }

input[type=checkbox] { width: 22px; height: 22px; margin: 0; accent-color: #5865f2; flex: 0 0 auto; }

button, .button {
    display: inline-block;
    border: none;
    background: #5865f2;
    color: #fff;
    padding: 11px 16px;
    border-radius: 9px;
    font-size: 15px;
    font-family: inherit;
    cursor: pointer;
    text-decoration: none;
    text-align: center;
}
button:hover, .button:hover { filter: brightness(0.94); }
button:disabled { background: #c7cbd6; cursor: not-allowed; }
button.small, .button.small { padding: 8px 12px; font-size: 14px; border-radius: 8px; }
button.soft, .button.soft { background: #eef0ff; color: #5865f2; }
button.soft:hover, .button.soft:hover { background: #e3e7ff; }
button.danger { background: #e74c3c; }
button.ghost { background: #fff; color: #c0392b; border: 1px solid #f0c4bd; }
button.gray { background: #eef0f4; color: #4a4f5c; }
.wide { width: 100%; display: block; }

.form-row { display: flex; gap: 8px; align-items: center; margin-top: 10px; flex-wrap: wrap; }
.form-row input { margin: 0; }
.form-row button { flex: 0 0 auto; }

.badge {
    display: inline-block;
    background: #e8ebff;
    color: #5865f2;
    padding: 4px 10px;
    border-radius: 20px;
    font-size: 13px;
    font-weight: 600;
    vertical-align: middle;
    white-space: nowrap;
}
.badge.off { background: #f0f1f5; color: #8a8f9c; }
.badge.crown { background: #fff4d6; color: #b7791f; }
.badge.today { background: #e6f7ee; color: #1e8f5b; }
.badge.warn { background: #fdecea; color: #c0392b; }

.muted { color: #7b818e; font-size: 13px; }
.small { color: #777; font-size: 13px; margin: 8px 0 0; }

.message {
    padding: 12px 14px;
    background: #eef0ff;
    color: #3b43b8;
    border-radius: 10px;
    margin-bottom: 16px;
    font-size: 14px;
}
.message.error { background: #fdecea; color: #c0392b; }

.banner {
    padding: 13px 16px;
    background: #fdecea;
    color: #c0392b;
    border-radius: 12px;
    margin-bottom: 16px;
    font-weight: 700;
    text-align: center;
    font-size: 15px;
}

/* 타임 카드 */
.slot-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.slot-title { display: flex; align-items: baseline; gap: 7px; margin-right: auto; }
.slot-title b { font-size: 19px; letter-spacing: -0.3px; }
.slot-title .name { color: #8a8f9c; font-size: 13px; }
.people { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 14px; }
.chip {
    background: #f4f5f9;
    color: #4a4f5c;
    border-radius: 999px;
    padding: 5px 11px;
    font-size: 13px;
}
.chip.mine { background: #e8ebff; color: #434dc0; font-weight: 700; }

/* 목록 */
.list-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 0;
    border-bottom: 1px solid #eef0f4;
}
.list-row:last-child { border-bottom: none; padding-bottom: 0; }
.list-row:first-child { padding-top: 0; }
.list-main { min-width: 0; flex: 1 1 auto; }
.list-main b { font-size: 15px; }
.list-main .meta { display: block; color: #8a8f9c; font-size: 13px; margin-top: 3px; }
.list-row form { flex: 0 0 auto; margin: 0; }

.notice-body { margin: 0; line-height: 1.65; white-space: pre-line; font-size: 15px; }
.notice-card h3 { margin: 0 0 4px; font-size: 15px; }
.notice-card .meta { color: #8a8f9c; font-size: 13px; margin: 0 0 10px; }

/* 공지 이미지 */
.notice-img {
    display: block;
    width: 100%;
    max-width: 100%;
    height: auto;
    margin: 12px 0 6px;
    border-radius: 14px;
    border: 1px solid #eef0f4;
    background: #f7f8fb;
}
.notice-img.small-thumb { width: 72px; height: 72px; object-fit: cover; margin: 10px 0 0; }

.image-field { margin-top: 10px; }
.file-pick {
    position: relative;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 10px 14px;
    border-radius: 10px;
    background: #eef0ff;
    color: #5865f2;
    font-size: 14px;
    font-weight: 700;
    cursor: pointer;
}
.file-pick:hover { background: #e3e7ff; }
.file-pick input[type=file] {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: 0;
    opacity: 0;
    overflow: hidden;
}
.image-preview { display: flex; align-items: flex-start; gap: 10px; margin-top: 10px; }
.image-preview[hidden] { display: none; }
.image-preview img {
    width: 108px;
    height: 108px;
    object-fit: cover;
    border-radius: 12px;
    border: 1px solid #e8ebf2;
    background: #f7f8fb;
}
.image-current { display: flex; align-items: center; gap: 12px; margin-top: 10px; flex-wrap: wrap; }
.image-current img {
    width: 76px;
    height: 76px;
    object-fit: cover;
    border-radius: 12px;
    border: 1px solid #e8ebf2;
}
.check-row {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 14px;
    color: #4a4f5c;
}

.week-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px; }
.week-grid .day { font-size: 13px; font-weight: 700; color: #555; text-align: center; }
.week-grid select { padding: 11px 2px; margin: 4px 0 0; font-size: 14px; text-align: center; }

.toggle-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 11px 0;
    border-bottom: 1px solid #eef0f4;
}
.toggle-row:last-of-type { border-bottom: none; }

/* 로그인 */
.login-wrap { max-width: 380px; margin: 8vh auto 0; padding: 0 18px; }
.login-wrap .card { padding: 26px 24px; }
.login-wrap h1 { margin: 0 0 4px; font-size: 22px; letter-spacing: -0.4px; }
.login-wrap p { margin: 0 0 18px; color: #7b818e; font-size: 14px; }

.empty { color: #8a8f9c; font-size: 14px; margin: 10px 0 0; }

/* ---------- 앱 설치 (PWA) ---------- */
[hidden] { display: none !important; }
.install-row { margin-top: 18px; }
.install-open {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    background: #fff;
    color: #5865f2;
    border: 1px solid #dfe3ff;
    border-radius: 16px;
    padding: 15px 16px;
    font-size: 15px;
    font-weight: 700;
    box-shadow: 0 3px 12px rgba(0, 0, 0, 0.05);
}
.install-open:hover { filter: none; background: #f7f8ff; }
.install-modal {
    position: fixed;
    inset: 0;
    z-index: 50;
    display: flex;
    align-items: flex-end;
    justify-content: center;
}
.install-backdrop { position: absolute; inset: 0; background: rgba(20, 24, 40, 0.45); }
.install-sheet {
    position: relative;
    width: 100%;
    max-width: 430px;
    background: #fff;
    border-radius: 22px 22px 0 0;
    padding: 18px 20px 22px;
    box-shadow: 0 -6px 30px rgba(0, 0, 0, 0.18);
}
.install-grip {
    width: 44px;
    height: 5px;
    margin: 0 auto 16px;
    border-radius: 999px;
    background: #e4e7ef;
}
.install-sheet h2 { margin: 0 0 6px; font-size: 19px; letter-spacing: -0.3px; }
.install-desc { margin: 0 0 16px; color: #6b7180; font-size: 14px; line-height: 1.55; }
.install-sheet .install-primary { margin-top: 4px; min-height: 48px; font-size: 16px; }
.install-steps { margin: 14px 0 0; padding: 14px 16px; background: #f5f6fb; border-radius: 12px; }
.install-steps ol { margin: 0; padding-left: 18px; color: #4a4f5c; font-size: 14px; line-height: 1.75; }
.install-note { margin: 12px 0 0; color: #8a8f9c; font-size: 13px; line-height: 1.5; }
.install-sheet .install-close {
    margin-top: 12px;
    background: #f1f3f8;
    color: #4a4f5c;
    min-height: 46px;
}

@media(min-width: 640px) {
    .install-modal { align-items: center; }
    .install-sheet { border-radius: 22px; }
}

@media(max-width: 860px) {
    .container.wide .slots { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media(max-width: 560px) {
    .topbar-inner { padding: 12px 14px; }
    .brand { font-size: 18px; }
    .who { font-size: 12.5px; }
    .container { padding: 0 13px; margin: 14px auto 36px; }
    .card { padding: 16px; border-radius: 14px; }
    .slots, .container.wide .slots { grid-template-columns: 1fr; }
    .blocks { gap: 10px; }
    .block { min-height: 104px; padding: 14px; border-radius: 18px; }
    .block-icon { width: 42px; height: 42px; border-radius: 13px; font-size: 21px; margin-bottom: 10px; }
    .block.notice { padding: 14px; gap: 12px; }
    .block.notice .thumb, .block.notice .thumb-empty { width: 64px; height: 64px; border-radius: 14px; }
    .week-grid { gap: 4px; }
    .week-grid select { font-size: 13px; padding: 10px 1px; }
    .week-grid .day { font-size: 12px; }
    .dash-head h1 { font-size: 20px; }
}
"""


# 관리자 공지 폼: 고른 이미지를 캔버스로 줄여 hidden input 에 넣는다.
IMAGE_JS = """
(function () {
  function loadImage(file) {
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () { URL.revokeObjectURL(url); resolve(img); };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error('read')); };
      img.src = url;
    });
  }
  function render(img, maxSide, quality) {
    var srcW = img.naturalWidth || img.width;
    var srcH = img.naturalHeight || img.height;
    var scale = Math.min(1, maxSide / Math.max(srcW, srcH));
    var w = Math.max(1, Math.round(srcW * scale));
    var h = Math.max(1, Math.round(srcH * scale));
    var canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    var ctx = canvas.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(img, 0, 0, w, h);
    return canvas.toDataURL('image/jpeg', quality);
  }
  async function shrink(file) {
    var img = await loadImage(file);
    var maxSide = 1080;
    var quality = 0.82;
    var data = render(img, maxSide, quality);
    while (data.length > 350 * 1024 && (maxSide > 480 || quality > 0.4)) {
      if (quality > 0.55) { quality -= 0.12; } else { maxSide = Math.round(maxSide * 0.85); quality = 0.7; }
      data = render(img, maxSide, quality);
    }
    return data;
  }
  function kb(n) { return Math.round(n / 1024) + 'KB'; }

  Array.prototype.forEach.call(document.querySelectorAll('form.notice-form'), function (form) {
    var input = form.querySelector('input[type=file]');
    var hidden = form.querySelector('input[name=image_data]');
    var preview = form.querySelector('.image-preview');
    var previewImg = preview ? preview.querySelector('img') : null;
    var clear = form.querySelector('.image-clear');
    var status = form.querySelector('.image-status');
    var remove = form.querySelector('input[name=remove_image]');
    if (!input || !hidden) return;

    input.addEventListener('change', async function () {
      var file = input.files && input.files[0];
      if (!file) return;
      if (!/^image\\//.test(file.type)) {
        if (status) status.textContent = '이미지 파일만 올릴 수 있어요.';
        input.value = '';
        return;
      }
      if (status) status.textContent = '이미지 줄이는 중…';
      try {
        var data = await shrink(file);
        if (data.length > 800 * 1024) {
          if (status) status.textContent = '이미지가 너무 커요. 다른 사진을 골라 주세요.';
          input.value = '';
          return;
        }
        hidden.value = data;
        if (previewImg) previewImg.src = data;
        if (preview) preview.hidden = false;
        if (remove) remove.checked = false;
        if (status) status.textContent = '새 이미지 준비 완료 · ' + kb(data.length);
      } catch (e) {
        if (status) status.textContent = '이미지를 읽지 못했어요.';
        input.value = '';
      }
    });

    if (clear) clear.addEventListener('click', function () {
      hidden.value = '';
      input.value = '';
      if (previewImg) previewImg.removeAttribute('src');
      if (preview) preview.hidden = true;
      if (status) status.textContent = '저장하면 새 이미지를 넣지 않아요.';
    });
  });
})();
"""

IMAGE_MAX_CHARS = 800 * 1024
IMAGE_PATTERN = re.compile(r"^data:image/(png|jpe?g|webp|gif);base64,([A-Za-z0-9+/=\s]+)$")


def clean_image_data(raw: str) -> tuple[str, str]:
    """공지 이미지 data URI 를 검증한다. (정리된 값, 오류 메시지)"""
    value = (raw or "").strip()
    if not value:
        return "", ""
    if len(value) > IMAGE_MAX_CHARS:
        return "", "이미지가 너무 커요. 800KB 이하로 줄여 주세요."
    match = IMAGE_PATTERN.match(value)
    if not match:
        return "", "이미지 형식이 올바르지 않아요. (PNG · JPG · WEBP · GIF)"
    payload = re.sub(r"\s+", "", match.group(2))
    try:
        binary = base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error):
        return "", "이미지를 읽지 못했어요. 다시 선택해 주세요."
    if not binary:
        return "", "이미지가 비어 있어요."
    return f"data:image/{match.group(1)};base64,{payload}", ""


# PWA: 설치 가능한 웹앱 메타 tags 와 설치 버튼/패널 동작
HEAD_TAGS = """
<meta name="theme-color" content="#5865f2">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="온점핑">
<meta name="mobile-web-app-capable" content="yes">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<link rel="icon" type="image/png" sizes="192x192" href="/icons/icon-192.png">
"""

PWA_JS = """
(function () {
  if (window.__onjumpingPwa) return;
  window.__onjumpingPwa = true;

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('/sw.js').catch(function () {});
    });
  }

  var deferred = null;
  var installed = false;
  window.addEventListener('beforeinstallprompt', function (event) {
    event.preventDefault();
    deferred = event;
    refresh();
  });
  window.addEventListener('appinstalled', function () {
    installed = true;
    deferred = null;
    refresh();
  });

  function el(id) { return document.getElementById(id); }
  function standalone() {
    return (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) ||
           window.navigator.standalone === true;
  }
  function isIos() {
    var ua = navigator.userAgent || '';
    return /iPad|iPhone|iPod/.test(ua) ||
           (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  }

  function setSteps(html) {
    var steps = el('install-steps');
    if (!steps) return;
    steps.innerHTML = html;
    steps.hidden = !html;
  }
  function setNote(text) {
    var note = el('install-note');
    if (!note) return;
    note.textContent = text || '';
    note.hidden = !text;
  }

  function refresh() {
    var action = el('install-action');
    var desc = el('install-desc');
    if (!action) return;
    setSteps('');
    setNote('');

    if (standalone() || installed) {
      action.hidden = true;
      if (desc) desc.textContent = '이미 앱으로 실행 중이에요. 홈 화면의 온점핑 아이콘으로 열 수 있어요.';
      return;
    }
    action.hidden = false;

    if (deferred) {
      action.textContent = '앱 설치하기 (PWA)';
      if (desc) desc.textContent = '아래 버튼을 누르면 설치 확인 창이 열려요.';
      return;
    }
    if (isIos()) {
      action.textContent = '설치 방법 보기';
      if (desc) desc.textContent = 'Safari에서는 아래 순서대로 홈 화면에 추가하면 앱처럼 쓸 수 있어요.';
      setSteps('<ol><li>Safari 하단의 공유 버튼(□↑)을 누르세요.</li>' +
               '<li>목록에서 <b>홈 화면에 추가</b>를 선택하세요.</li>' +
               '<li>오른쪽 위 <b>추가</b>를 누르면 설치가 끝나요.</li></ol>');
      return;
    }
    action.textContent = '앱 설치하기 (PWA)';
    if (desc) desc.textContent = '브라우저 메뉴에서 설치할 수 있어요.';
    setSteps('<ol><li>브라우저 오른쪽 위 메뉴(⋮ 또는 …)를 여세요.</li>' +
             '<li><b>앱 설치</b> 또는 <b>홈 화면에 추가</b>를 선택하세요.</li>' +
             '<li>화면 안내에 따라 설치를 마치세요.</li></ol>');
    setNote('설치 창이 바로 뜨지 않으면 잠시 뒤 다시 눌러 주세요. (HTTPS에서만 설치할 수 있어요)');
  }

  function open() {
    var modal = el('install-modal');
    if (!modal) return;
    modal.hidden = false;
    document.body.style.overflow = 'hidden';
    refresh();
  }
  function close() {
    var modal = el('install-modal');
    if (!modal) return;
    modal.hidden = true;
    document.body.style.overflow = '';
  }

  document.addEventListener('click', function (event) {
    var target = event.target;
    if (!(target instanceof Element)) return;
    if (target.closest('[data-install-open]')) { open(); return; }
    if (target.closest('[data-install-close]')) { close(); return; }
    if (target.closest('#install-action')) {
      if (!deferred) { refresh(); return; }
      var promptEvent = deferred;
      deferred = null;
      promptEvent.prompt();
      if (promptEvent.userChoice && promptEvent.userChoice.then) {
        promptEvent.userChoice.then(function (choice) {
          setNote(choice && choice.outcome === 'accepted'
            ? '설치를 시작했어요. 홈 화면에서 온점핑을 확인해 주세요.'
            : '설치를 취소했어요. 나중에 다시 설치할 수 있어요.');
          refresh();
        }).catch(function () {});
      }
    }
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') close();
  });
})();
"""


def install_button(label: str = "📲 앱 다운로드") -> str:
    return f'<button class="install-open" type="button" data-install-open>{label}</button>'


def install_modal() -> str:
    return """
<div class="install-modal" id="install-modal" hidden>
    <div class="install-backdrop" data-install-close></div>
    <div class="install-sheet" role="dialog" aria-modal="true" aria-label="온점핑 앱 설치">
        <div class="install-grip"></div>
        <h2>온점핑 앱 설치</h2>
        <p class="install-desc" id="install-desc">홈 화면에 설치하면 앱처럼 전체 화면으로 바로 열 수 있어요.</p>
        <button class="wide install-primary" type="button" id="install-action">앱 설치하기 (PWA)</button>
        <div class="install-steps" id="install-steps" hidden></div>
        <p class="install-note" id="install-note" hidden></p>
        <button class="wide install-close" type="button" data-install-close>닫기</button>
    </div>
</div>"""


# 정적 PWA 파일 (매니페스트 · 서비스 워커 · 아이콘)
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json; charset=utf-8",
                              "public, max-age=3600"),
    "/sw.js": ("sw.js", "application/javascript; charset=utf-8", "no-cache"),
    "/icons/icon-192.png": ("icons/icon-192.png", "image/png", "public, max-age=86400"),
    "/icons/icon-512.png": ("icons/icon-512.png", "image/png", "public, max-age=86400"),
    "/icons/apple-touch-icon.png": ("icons/apple-touch-icon.png", "image/png", "public, max-age=86400"),
    "/favicon.ico": ("icons/icon-192.png", "image/png", "public, max-age=86400"),
}


# --------------------------------------------------------------------------- #
# 응답 / 쿠키
# --------------------------------------------------------------------------- #

class Response:
    def __init__(self, body: str = "", status: int = 200, ctype: str = "text/html; charset=utf-8"):
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.ctype = ctype
        self.headers: list[tuple[str, str]] = []
        self.cookies: dict[str, tuple[str, int]] = {}
        self.flash_set = False

    def header(self, name: str, value: str) -> "Response":
        self.headers.append((name, value))
        return self

    def set_cookie(self, name: str, value: str, max_age: int = SESSION_MAX_AGE) -> "Response":
        self.cookies[name] = (value, max_age)
        return self

    def clear_cookie(self, name: str) -> "Response":
        self.cookies[name] = ("", 0)
        return self

    def set_flash(self, message: str, error: bool = False) -> "Response":
        payload = ("!" if error else "") + message
        self.cookies[FLASH] = (urllib.parse.quote(payload), 60)
        self.flash_set = True
        return self

    def __call__(self, environ, start_response):
        headers = [
            ("Content-Type", self.ctype),
            ("Content-Length", str(len(self.body))),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "same-origin"),
        ]
        if not any(name.lower() == "cache-control" for name, _ in self.headers):
            headers.append(("Cache-Control", "no-store"))
        headers.extend(self.headers)
        for name, (value, max_age) in self.cookies.items():
            attrs = f"{name}={value}; Path=/; Max-Age={max_age}; SameSite=Lax"
            if name != FLASH:
                attrs += "; HttpOnly"
            if SECURE_COOKIE:
                attrs += "; Secure"
            headers.append(("Set-Cookie", attrs))
        reason = REASONS.get(self.status, "OK")
        start_response(f"{self.status} {reason}", headers)
        return [self.body]


def redirect(location: str, flash: str | None = None, error: bool = False) -> Response:
    resp = Response("", 303).header("Location", location)
    if flash:
        resp.set_flash(flash, error)
    return resp


def static_response(path: str) -> Response | None:
    """매니페스트 · 서비스 워커 · 아이콘을 파일에서 읽어 준다. 없으면 None."""
    spec = STATIC_FILES.get(path)
    if not spec:
        return None
    rel, ctype, cache = spec
    try:
        data = (STATIC_DIR / rel).read_bytes()
    except OSError:
        return None
    resp = Response(data, 200, ctype)
    resp.header("Cache-Control", cache)
    if path == "/sw.js":
        resp.header("Service-Worker-Allowed", "/")
    return resp


def esc(value) -> str:
    return (str(value)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def form_value(form: dict[str, list[str]], key: str, default: str = "") -> str:
    values = form.get(key) or []
    return values[0].strip() if values else default


# --------------------------------------------------------------------------- #
# 세션
# --------------------------------------------------------------------------- #

def _sign(value: str) -> str:
    return hmac.new(store.get_secret().encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def session_cookie(user_id: int) -> str:
    raw = str(user_id)
    return f"{raw}.{_sign(raw)}"


def parse_session(environ) -> int | None:
    header = environ.get("HTTP_COOKIE", "")
    if not header:
        return None
    try:
        morsel = SimpleCookie(header).get(COOKIE)
    except Exception:
        return None
    if not morsel or "." not in morsel.value:
        return None
    raw, signature = morsel.value.split(".", 1)
    if not raw.isdigit() or not hmac.compare_digest(_sign(raw), signature):
        return None
    return int(raw)


def read_flash(environ) -> tuple[str, bool]:
    header = environ.get("HTTP_COOKIE", "")
    if not header:
        return "", False
    try:
        morsel = SimpleCookie(header).get(FLASH)
    except Exception:
        return "", False
    if not morsel or not morsel.value:
        return "", False
    message = urllib.parse.unquote(morsel.value)
    if message.startswith("!"):
        return message[1:], True
    return message, False


def read_form(environ) -> dict[str, list[str]]:
    """application/x-www-form-urlencoded 본문만 읽는다.

    공지 이미지는 JS 가 줄인 data URI 를 hidden input 으로 보내므로 폼은 항상
    urlencoded 다. multipart 로 들어온 요청은 읽지 않고 빈 폼으로 두어, 내용
    없이 저장되어 공지가 지워지는 일이 없게 한다.
    """
    ctype = (environ.get("CONTENT_TYPE") or "").lower()
    if ctype.startswith("multipart/"):
        return {}
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if not length:
        return {}
    raw = environ["wsgi.input"].read(length).decode("utf-8", "replace")
    return urllib.parse.parse_qs(raw, keep_blank_values=True)


# --------------------------------------------------------------------------- #
# 공통 HTML
# --------------------------------------------------------------------------- #

def header(user: dict | None, wide: bool = False) -> str:
    """홈 상단 바. 앱 이름과 사용자·로그아웃만 두고 메뉴 탭은 두지 않는다."""
    who = ""
    if user:
        who = (
            f'<div class="who"><b>{esc(user["nickname"])}</b>님'
            f'<a href="/logout">로그아웃</a></div>'
        )
    cls = "topbar wide" if wide else "topbar"
    return (
        f'<header class="{cls}"><div class="topbar-inner">'
        '<a class="brand" href="/">🏃 온점핑</a>'
        f'{who}'
        "</div></header>"
    )


def app_bar(title: str, wide: bool = False) -> str:
    """서브 화면 상단 앱바. 홈으로 가는 ← 뒤로와 화면 제목만 둔다."""
    cls = "appbar wide" if wide else "appbar"
    return (
        f'<header class="{cls}"><div class="appbar-inner">'
        '<a class="back" href="/">← <span>뒤로</span></a>'
        f'<h1>{esc(title)}</h1>'
        "</div></header>"
    )


def page(content: str, *, title: str = "온점핑 출석", user: dict | None = None,
         flash: str = "", error: bool = False, wide: bool = False,
         appbar_title: str | None = None, script: str = "") -> str:
    flash_html = ""
    if flash:
        flash_html = f'<div class="message{" error" if error else ""}">{esc(flash)}</div>'
    container = 'class="container wide"' if wide else 'class="container"'
    head = app_bar(appbar_title, wide) if appbar_title else header(user, wide)
    script_html = f"<script>{PWA_JS}\n{script}</script>\n"
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ko">\n<head>\n<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        f"{HEAD_TAGS}"
        f"<title>{esc(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
        f"{head}\n"
        f"<main {container}>\n{flash_html}\n{content}\n</main>\n"
        f"{script_html}"
        "</body>\n</html>\n"
    )


def date_label(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.year}년 {d.month}월 {d.day}일 {store.WEEKDAY_KO[store.WEEKDAYS[d.weekday()]]}요일"


def page_note(text: str) -> str:
    return f'<p class="page-note">{esc(text)}</p>'


def notice_image(notice: dict | None, cls: str = "notice-img") -> str:
    """공지 첨부 이미지. 없으면 빈 문자열."""
    data = (notice or {}).get("image_data") or ""
    if not data:
        return ""
    return f'<img class="{cls}" src="{esc(data)}" alt="공지 이미지">'


def preview_text(text: str, limit: int = 70) -> str:
    """블록에 넣을 한두 줄 미리보기."""
    line = " ".join((text or "").split())
    if len(line) <= limit:
        return line
    return line[: limit - 1].rstrip() + "…"


# --------------------------------------------------------------------------- #
# 로그인
# --------------------------------------------------------------------------- #

def render_login(flash: str = "", error: bool = False) -> Response:
    flash_html = f'<div class="message{" error" if error else ""}">{esc(flash)}</div>' if flash else ""
    content = f"""
<div class="login-wrap">
    {flash_html}
    <div class="card">
        <h1>로그인</h1>
        <p>온점핑 출석 관리</p>
        <form method="POST" action="/login">
            <input name="username" placeholder="아이디" autocomplete="username"
                   autocapitalize="none" required>
            <input name="password" type="password" placeholder="비밀번호"
                   autocomplete="current-password" required>
            <button class="wide" style="margin-top:8px">로그인</button>
        </form>
        <p class="small">계정은 관리자에게 문의해 주세요.</p>
    </div>
    <div class="install-row">{install_button()}</div>
</div>
{install_modal()}
"""
    return Response(
        "<!DOCTYPE html>\n<html lang=\"ko\">\n<head>\n<meta charset=\"UTF-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        f"{HEAD_TAGS}<title>로그인 · 온점핑 출석</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
        f"{header(None)}\n<main class=\"container\">{content}</main>\n"
        f"<script>{PWA_JS}</script>\n"
        "</body>\n</html>\n"
    )


# --------------------------------------------------------------------------- #
# 회원: 오늘 출석 /
# --------------------------------------------------------------------------- #

def _people_chips(people: list[dict], me_id: int) -> str:
    if not people:
        return '<p class="empty">아직 출석자가 없습니다.</p>'
    chips = []
    for person in people:
        mine = person["user_id"] == me_id
        label = esc(person["nickname"]) + (" (나)" if mine else "")
        chips.append(f'<span class="chip{" mine" if mine else ""}">{label}</span>')
    return f'<div class="people">{"".join(chips)}</div>'


def _weekday_groups(user: dict) -> list[tuple[str | None, list[str]]]:
    groups: list[tuple[str | None, list[str]]] = []
    for key in store.WEEKDAY_KEYS:
        slot_id = user["weekly"].get(key)
        if groups and groups[-1][0] == slot_id:
            groups[-1][1].append(key)
        else:
            groups.append((slot_id, [key]))
    return groups


def _days_label(days: list[str]) -> str:
    if len(days) == 1:
        return store.WEEKDAY_KO[days[0]]
    if len(days) == 2:
        return store.WEEKDAY_KO[days[0]] + store.WEEKDAY_KO[days[1]]
    return store.WEEKDAY_KO[days[0]] + "~" + store.WEEKDAY_KO[days[-1]]


def weekly_summary(user: dict) -> str:
    chunks = []
    for slot_id, days in _weekday_groups(user):
        time = store.slot_info(slot_id)["time"] if slot_id in store.SLOT_BY_ID else "쉼"
        chunks.append(f"{_days_label(days)} {time}")
    return " · ".join(chunks)


def _block(href: str, icon: str, title: str, sub: str = "", badge_label: str = "",
           badge_class: str = "", extra: str = "") -> str:
    badge = (f' <span class="badge {badge_class}">{esc(badge_label)}</span>'
             if badge_label else "")
    sub_html = f'<div class="block-sub">{esc(sub)}</div>' if sub else ""
    return (
        f'<a class="block{extra}" href="{href}">'
        f'<div class="block-icon">{icon}</div>'
        f'<div class="block-title">{esc(title)}{badge}</div>'
        f"{sub_html}</a>"
    )


def render_index(user: dict, flash: str = "", error: bool = False) -> Response:
    """홈: 안드로이드 앱 홈처럼 큰 블록(타일)로 메뉴를 보여 준다. 네비게이션 바 없음."""
    today = store.kst_today()
    state = store.get_day(today)
    rows = store.list_attendance(today)
    mine_by_slot = {row["slot_id"]: row for row in rows if row["user_id"] == user["id"]}
    notices = store.list_notices()
    latest = notices[0] if notices else None

    parts = [f"""
<div class="dash-head">
    <h1>{esc(user['nickname'])}님, 반가워요 👋</h1>
    <p>{esc(date_label(today))}</p>
</div>"""]

    if state["closed_all"]:
        parts.append('<div class="banner">🚫 오늘은 전체 휴강입니다.</div>')

    # 공지 블록: 최신 공지 요약 (이미지가 있으면 썸네일)
    if latest:
        thumb = notice_image(latest, "thumb") or '<div class="thumb-empty">📢</div>'
        parts.append(f"""
<a class="block notice" href="/notices">
    {thumb}
    <div class="notice-text">
        <div class="block-title">공지사항</div>
        <div class="block-sub">{esc(preview_text(latest['body']))}</div>
        <div class="block-meta">{esc(latest['posted_on'])} · {esc(latest['author'])}</div>
    </div>
</a>""")
    else:
        parts.append("""
<a class="block notice" href="/notices">
    <div class="thumb-empty">📢</div>
    <div class="notice-text">
        <div class="block-title">공지사항</div>
        <div class="block-sub">아직 올라온 공지가 없어요.</div>
    </div>
</a>""")

    if state["closed_all"]:
        badge_label, badge_class = "휴강", "off"
        checkin_sub = "오늘은 전체 휴강이에요"
    elif mine_by_slot:
        first = next(s for s in state["slots"] if s["id"] in mine_by_slot)
        extra = len(mine_by_slot) - 1
        badge_label, badge_class = "출석 완료", "today"
        checkin_sub = f"{first['time']} {first['name']}" + (f" 외 {extra}개" if extra else "")
    else:
        badge_label, badge_class = "출석 전", "off"
        open_count = sum(1 for s in state["slots"] if s["open"])
        checkin_sub = (f"열린 타임 {open_count}개 · 골라서 출석해요" if open_count
                       else "지금은 열린 타임이 없어요")

    parts.append(
        '<div class="blocks">'
        + _block("/checkin", "✅", "출석하기", checkin_sub, badge_label, badge_class)
        + _block("/auto", "🗓️", "자동 출석", "평일 " + weekly_summary(user))
        + "</div>"
    )

    if user["is_admin"]:
        parts.append('<div class="section-label">관리자</div>')
        parts.append(
            '<div class="blocks">'
            + _block("/admin", "🧑‍🏫", "출석·휴강", "오늘 명단과 휴강 관리", extra=" admin")
            + _block("/admin/notices", "📢", "공지 관리", "공지 올리기·수정", extra=" admin")
            + _block("/admin/users", "👥", "유저 관리", "회원 목록·삭제", extra=" admin")
            + _block("/admin/users/new", "➕", "유저 생성", "새 계정 만들기", extra=" admin")
            + "</div>"
        )

    parts.append(f'<div class="install-row">{install_button()}</div>')
    parts.append(install_modal())

    return Response(page("\n".join(parts), title="온점핑", user=user, flash=flash, error=error))


# --------------------------------------------------------------------------- #
# 회원: 출석 /checkin
# --------------------------------------------------------------------------- #

def render_checkin(user: dict, flash: str = "", error: bool = False) -> Response:
    today = store.kst_today()
    state = store.get_day(today)
    rows = store.list_attendance(today)

    mine_by_slot = {row["slot_id"]: row for row in rows if row["user_id"] == user["id"]}
    by_slot: dict[str, list[dict]] = {}
    for row in rows:
        by_slot.setdefault(row["slot_id"], []).append(row)

    parts = [page_note(f"{date_label(today)} · 타임마다 11명 선착순")]

    notices = store.list_notices()
    shown = next((n for n in notices if n["posted_on"] == today), None)
    if shown is None and notices:
        shown = notices[0]
    if shown:
        heading = "📢 오늘의 공지" if shown["posted_on"] == today else "📢 최근 공지"
        badge = ' <span class="badge today">오늘</span>' if shown["posted_on"] == today else ""
        parts.append(f"""
<div class="card">
    <h2>{heading}{badge}</h2>
    <p class="notice-body">{esc(shown['body'])}</p>
    {notice_image(shown)}
    <p class="small">{esc(shown['author'])} · {shown['posted_on']}</p>
</div>""")
    else:
        parts.append("""
<div class="card">
    <h2>📢 오늘의 공지</h2>
    <p class="empty">오늘 올라온 공지가 없습니다.</p>
</div>""")

    if state["closed_all"]:
        parts.append('<div class="banner">🚫 오늘은 전체 휴강입니다.</div>')

    # 시간 선택 출석 카드
    already_done = bool(mine_by_slot)
    done_badge = ' <span class="badge today">오늘 출석 완료</span>' if already_done else ""
    options = []
    for slot in state["slots"]:
        marks = []
        if slot["blocked"]:
            marks.append("휴강")
        if slot["full"]:
            marks.append("마감")
        if slot["id"] in mine_by_slot:
            marks.append("출석함")
        suffix = f" ({' · '.join(marks)})" if marks else ""
        unavailable = bool(marks) or state["closed_all"] or already_done
        options.append(
            f'<option value="{slot["id"]}"{" disabled" if unavailable else ""}>'
            f'{slot["time"]} {slot["name"]}{suffix}</option>'
        )
    open_slots = [] if already_done else [
        s for s in state["slots"] if s["open"] and s["id"] not in mine_by_slot]
    select_disabled = "" if open_slots else " disabled"
    button_disabled = "" if open_slots else " disabled"
    button_label = "출석하기"
    if already_done:
        done_slot = next(s for s in state["slots"] if s["id"] in mine_by_slot)
        button_label = "오늘 출석 완료"
        hint = (f"오늘은 이미 {done_slot['time']} 타임에 출석했어요. "
                "다른 타임으로 바꾸려면 아래에서 출석을 취소한 뒤 다시 선택해 주세요.")
    elif state["closed_all"]:
        hint = "오늘은 전체 휴강이라 출석할 수 없습니다."
    elif not open_slots:
        hint = "지금 선택할 수 있는 타임이 없습니다."
    else:
        hint = "타임을 고르고 출석하기를 누르세요. 타임마다 11명 선착순입니다."

    my_rows = ""
    for slot in state["slots"]:
        record = mine_by_slot.get(slot["id"])
        if not record:
            continue
        my_rows += f"""
        <div class="list-row">
            <div class="list-main"><b>{slot["time"]}</b> <span class="muted">{slot["name"]}</span>
                <span class="badge">출석함</span></div>
            <form method="POST" action="/checkin/cancel/{record["id"]}">
                <button class="small ghost">출석 취소</button>
            </form>
        </div>"""

    parts.append(f"""
<div class="card">
    <h2>✅ 시간 선택해서 출석하기{done_badge}</h2>
    <form method="POST" action="/checkin">
        <select name="slot_id" aria-label="출석 타임"{select_disabled}>
            {''.join(options)}
        </select>
        <button class="wide" style="margin-top:8px"{button_disabled}>{button_label}</button>
    </form>
    <p class="small">{hint}</p>{('<div style="margin-top:12px">' + my_rows + "</div>") if my_rows else ""}
</div>""")

    cards = []
    for slot in state["slots"]:
        people = by_slot.get(slot["id"], [])
        action = ""
        if state["closed_all"]:
            action = '<span class="badge off">전체 휴강</span>'
        elif slot["blocked"]:
            action = '<span class="badge off">휴강</span>'
        elif slot["full"]:
            action = '<span class="badge off">마감</span>'
        cards.append(f"""
<section class="card">
    <div class="slot-head">
        <div class="slot-title"><b>{slot["time"]}</b><span class="name">{slot["name"]}</span></div>
        <span class="badge">{slot["taken"]} / {slot["capacity"]}</span>
        {action}
    </div>
    {_people_chips(people, user["id"])}
</section>""")
    parts.append(f'<div class="slots">{"".join(cards)}</div>')

    return Response(page("\n".join(parts), title="출석하기 · 온점핑 출석", user=user,
                         appbar_title="출석하기", flash=flash, error=error))


# --------------------------------------------------------------------------- #
# 회원: 공지 /notices
# --------------------------------------------------------------------------- #

def render_notices(user: dict, flash: str = "", error: bool = False) -> Response:
    today = store.kst_today()
    notices = store.list_notices()
    today_notice = next((n for n in notices if n["posted_on"] == today), None)
    others = [n for n in notices if n is not today_notice]

    parts = [page_note("오늘의 공지와 지난 공지 목록")]
    if today_notice:
        parts.append(f"""
<div class="card">
    <h2>📢 오늘의 공지 <span class="badge today">오늘</span></h2>
    <p class="notice-body">{esc(today_notice['body'])}</p>
    {notice_image(today_notice)}
    <p class="small">{esc(today_notice['author'])} · {today_notice['posted_on']}</p>
</div>""")
    else:
        parts.append('<div class="card"><h2>📢 오늘의 공지</h2>'
                     '<p class="empty">오늘 올라온 공지가 없습니다.</p></div>')

    if others:
        rows = []
        for notice in others:
            rows.append(f"""
<div class="card notice-card">
    <h3>{notice['posted_on']}</h3>
    <p class="meta">{esc(notice['author'])}</p>
    <p class="notice-body">{esc(notice['body'])}</p>
    {notice_image(notice)}
</div>""")
        parts.append('<h2 style="margin:22px 4px 12px;font-size:17px">지난 공지</h2>' + "".join(rows))

    return Response(page("\n".join(parts), title="공지사항 · 온점핑 출석", user=user,
                         appbar_title="공지사항", flash=flash, error=error))


# --------------------------------------------------------------------------- #
# 회원: 자동 출석 /auto
# --------------------------------------------------------------------------- #

def render_auto(user: dict, flash: str = "", error: bool = False) -> Response:
    summary = " · ".join(
        f"{store.WEEKDAY_KO[d]} {store.slot_info(user['weekly'][d])['time'] if user['weekly'][d] else '쉼'}"
        for d in store.WEEKDAY_KEYS
    )
    day_options = [("", "쉼")] + [(slot["id"], slot["time"]) for slot in store.SLOTS]
    columns = ""
    for key in store.WEEKDAY_KEYS:
        current = user["weekly"].get(key) or ""
        opts = "".join(
            f'<option value="{value}"{" selected" if value == current else ""}>{label}</option>'
            for value, label in day_options
        )
        columns += f"""
        <div>
            <div class="day">{store.WEEKDAY_KO[key]}</div>
            <select name="{key}" aria-label="{store.WEEKDAY_KO[key]}요일 타임">{opts}</select>
        </div>"""

    parts = [
        page_note("평일(월~금)에 밴드 대신 잡아 둘 타임을 정해 두세요"),
        f"""
<div class="card">
    <h2>🗓️ 평일 자동 출석</h2>
    <form method="POST" action="/auto">
        <div class="week-grid">{columns}</div>
        <button class="wide" style="margin-top:14px">저장</button>
    </form>
    <p class="small">내 설정: {esc(summary)}</p>
    <p class="small">주말은 설정하지 않습니다. 비워 두면 그 요일은 자동 출석을 하지 않아요.</p>
</div>""",
        f"""
<div class="card">
    <h2>👤 내 정보</h2>
    <div class="list-row">
        <div class="list-main"><b>{esc(user['nickname'])}</b><span class="meta">@{esc(user['username'])}</span></div>
    </div>
    <form method="POST" action="/profile">
        <input name="nickname" value="{esc(user['nickname'])}" maxlength="20"
               placeholder="닉네임" aria-label="닉네임">
        <button class="wide" style="margin-top:8px">닉네임 변경</button>
    </form>
</div>""",
    ]
    return Response(page("\n".join(parts), title="자동 출석 · 온점핑 출석", user=user,
                         appbar_title="자동 출석", flash=flash, error=error))


# --------------------------------------------------------------------------- #
# 관리자: 출석·휴강 /admin
# --------------------------------------------------------------------------- #

def render_admin(user: dict, flash: str = "", error: bool = False) -> Response:
    today = store.kst_today()
    state = store.get_day(today)
    rows = store.list_attendance(today)
    by_slot: dict[str, list[dict]] = {}
    for row in rows:
        by_slot.setdefault(row["slot_id"], []).append(row)

    slot_rows = ""
    for slot in state["slots"]:
        checked = " checked" if slot["blocked"] else ""
        slot_rows += f"""
    <label class="toggle-row">
        <span><b>{slot["time"]}</b> <span class="muted">{slot["name"]}</span>
        <span class="badge">{slot["taken"]} / {slot["capacity"]}</span></span>
        <input type="checkbox" name="blocked" value="{slot["id"]}"{checked} aria-label="{slot["time"]} 휴강">
    </label>"""

    parts = [page_note(f"{date_label(today)} · 전체 {len(rows)}명 출석")]
    parts.append(f"""
<div class="card">
    <h2>🚫 휴강 제어</h2>
    <form method="POST" action="/admin/day">
        <label class="toggle-row">
            <span><b>오늘 전체 휴강</b></span>
            <input type="checkbox" name="closed_all" value="1"{" checked" if state["closed_all"] else ""}
                   aria-label="전체 휴강">
        </label>{slot_rows}
        <button class="wide" style="margin-top:14px">저장</button>
    </form>
    <p class="small">체크한 타임은 오늘 출석할 수 없습니다. 전체 휴강이면 모든 타임이 닫힙니다.</p>
</div>""")

    cards = []
    for slot in state["slots"]:
        people = by_slot.get(slot["id"], [])
        head_badges = f'<span class="badge">{slot["taken"]} / {slot["capacity"]}</span>'
        if slot["blocked"]:
            head_badges += ' <span class="badge off">휴강</span>'
        elif slot["full"]:
            head_badges += ' <span class="badge off">마감</span>'
        rows_html = ""
        if people:
            for person in people:
                rows_html += f"""
        <div class="list-row">
            <div class="list-main"><b>{esc(person['nickname'])}</b>
                <span class="meta">@{esc(person['username'])}</span></div>
            <form method="POST" action="/checkin/cancel/{person['id']}">
                <button class="small ghost">취소</button>
            </form>
        </div>"""
        else:
            rows_html = '<p class="empty">아직 출석자가 없습니다.</p>'
        cards.append(f"""
<section class="card">
    <div class="slot-head">
        <div class="slot-title"><b>{slot["time"]}</b><span class="name">{slot["name"]}</span></div>
        {head_badges}
    </div>
    {rows_html}
</section>""")
    parts.append(f'<div class="slots">{"".join(cards)}</div>')

    return Response(page("\n".join(parts), title="출석·휴강 · 온점핑 출석",
                         user=user, appbar_title="출석·휴강", flash=flash, error=error, wide=True))


# --------------------------------------------------------------------------- #
# 관리자: 공지 관리 /admin/notices
# --------------------------------------------------------------------------- #

def _image_fields(notice: dict | None) -> str:
    """공지 폼의 이미지 선택 · 미리보기 · 기존 이미지 삭제 UI."""
    image = (notice or {}).get("image_data") or ""
    current = ""
    if image:
        current = f"""
    <div class="image-current">
        <img src="{esc(image)}" alt="첨부된 공지 이미지">
        <label class="check-row"><input type="checkbox" name="remove_image" value="1"> 이 이미지 지우기</label>
    </div>"""
    return f"""
    <div class="image-field">
        <label class="file-pick">🖼️ 이미지 선택
            <input type="file" accept="image/*">
        </label>
        <input type="hidden" name="image_data" value="">
        <p class="small image-status">사진을 고르면 자동으로 줄여서 올려요. (긴 변 1080px · 400KB 이하)</p>
        <div class="image-preview" hidden>
            <img alt="새 이미지 미리보기">
            <button type="button" class="small gray image-clear">빼기</button>
        </div>
    </div>{current}"""


def render_admin_notices(user: dict, flash: str = "", error: bool = False) -> Response:
    today = store.kst_today()
    notices = store.list_notices()
    today_notice = next((n for n in notices if n["posted_on"] == today), None)
    value = esc(today_notice["body"]) if today_notice else ""

    parts = [page_note("오늘의 공지 등록과 지난 공지 수정·삭제 · 이미지 첨부 가능")]
    parts.append(f"""
<div class="card">
    <h2>📢 오늘의 공지</h2>
    <form class="notice-form" method="POST" action="/admin/notices">
        <textarea name="body" placeholder="오늘의 공지를 입력하세요" required>{value}</textarea>
        {_image_fields(today_notice)}
        <button class="wide" style="margin-top:8px">{"공지 수정" if today_notice else "공지 등록"}</button>
    </form>
    <p class="small">하루에 한 건입니다. 오늘 공지를 다시 저장하면 내용만 바뀝니다. 이미지를 새로 고르지 않으면 기존 이미지가 그대로 남아요.</p>
</div>""")

    if notices:
        blocks = []
        for notice in notices:
            badge = ' <span class="badge today">오늘</span>' if notice["posted_on"] == today else ""
            blocks.append(f"""
<div class="card notice-card">
    <h3>{notice['posted_on']}{badge}</h3>
    <p class="meta">{esc(notice['author'])} · 수정 {notice['updated_at'][:16].replace('T', ' ')}</p>
    <form class="notice-form" method="POST" action="/admin/notices/{notice['id']}">
        <textarea name="body" required>{esc(notice['body'])}</textarea>
        {_image_fields(notice)}
        <button class="wide" style="margin-top:8px">저장</button>
    </form>
    <form method="POST" action="/admin/notices/{notice['id']}/delete" style="margin-top:8px">
        <button class="wide ghost">삭제</button>
    </form>
</div>""")
        parts.append('<h2 style="margin:22px 4px 12px;font-size:17px">공지 목록</h2>' + "".join(blocks))
    else:
        parts.append('<div class="card"><p class="empty">등록된 공지가 없습니다.</p></div>')

    return Response(page("\n".join(parts), title="공지 관리 · 온점핑 출석", user=user,
                         appbar_title="공지 관리", flash=flash, error=error, wide=True,
                         script=IMAGE_JS))


# --------------------------------------------------------------------------- #
# 관리자: 회원 관리 /admin/users
# --------------------------------------------------------------------------- #

def render_admin_users(user: dict, flash: str = "", error: bool = False) -> Response:
    users = store.list_users()
    rows_html = ""
    for member in users:
        crown = ' <span class="badge crown">👑 관리자</span>' if member["is_admin"] else \
                ' <span class="badge off">회원</span>'
        weekly = member["weekly"]
        summary = " · ".join(
            f"{store.WEEKDAY_KO[d]} {store.slot_info(weekly[d])['time'] if weekly[d] else '쉼'}"
            for d in store.WEEKDAY_KEYS
        )
        delete = ""
        if not member["is_admin"]:
            delete = f"""
        <form method="POST" action="/admin/users/{member['id']}/delete">
            <button class="small ghost">삭제</button>
        </form>"""
        rows_html += f"""
    <div class="list-row">
        <div class="list-main"><b>{esc(member['nickname'])}</b> <span class="muted">@{esc(member['username'])}</span>{crown}
            <span class="meta">자동 출석 · {esc(summary)}</span></div>{delete}
    </div>"""

    parts = [
        page_note(f"전체 {len(users)}명"),
        """
<div class="card">
    <div class="slot-head">
        <h2 style="margin:0">👥 회원 목록</h2>
        <a class="button small" href="/admin/users/new">➕ 회원 추가하기</a>
    </div>
    <div style="margin-top:8px">%s</div>
</div>""" % rows_html,
    ]
    return Response(page("\n".join(parts), title="회원 관리 · 온점핑 출석", user=user,
                         appbar_title="회원 관리", flash=flash, error=error, wide=True))


def render_admin_user_new(user: dict, flash: str = "", error: bool = False) -> Response:
    parts = [
        page_note("새 회원 계정을 만듭니다. 만든 뒤 회원 목록에서 확인할 수 있어요."),
        """
<div class="card">
    <h2>➕ 회원 만들기</h2>
    <form method="POST" action="/admin/users">
        <input name="username" placeholder="아이디 (영문 소문자·숫자·밑줄 3~20자)"
               autocapitalize="none" required>
        <input name="password" type="password" placeholder="비밀번호 (4자 이상)" minlength="4" required>
        <input name="nickname" placeholder="닉네임 (선택)" maxlength="20">
        <button class="wide" style="margin-top:8px">회원 생성</button>
    </form>
    <p class="small">아이디는 로그인에 쓰는 값이라 나중에 바꿀 수 없습니다.</p>
    <p class="small"><a href="/admin/users">← 회원 목록으로</a></p>
</div>""",
    ]
    return Response(page("\n".join(parts), title="회원 추가 · 온점핑 출석", user=user,
                         appbar_title="회원 추가", flash=flash, error=error, wide=True))


# --------------------------------------------------------------------------- #
# 라우팅
# --------------------------------------------------------------------------- #

def _guard_admin(user: dict | None, flash: str) -> Response | None:
    if not user:
        return redirect("/login")
    if not user["is_admin"]:
        return redirect("/", "관리자만 접근할 수 있어요.", error=True)
    return None


def route(method: str, path: str, form: dict[str, list[str]], user: dict | None, flash: str,
          flash_error: bool) -> Response:
    if path == "/health":
        return Response("OK", 200, "text/plain; charset=utf-8")

    # ---- PWA 정적 파일 (매니페스트 · 서비스 워커 · 아이콘) ----
    asset = static_response(path)
    if asset is not None:
        return asset

    # ---- 로그인 / 로그아웃 ----
    if path == "/login":
        if method == "GET":
            if user:
                return redirect("/")
            return render_login(flash, flash_error)
        if method == "POST":
            found = store.authenticate(form_value(form, "username"), form_value(form, "password"))
            if not found:
                return render_login("아이디 또는 비밀번호가 틀렸습니다.", error=True)
            target = "/admin" if found["is_admin"] else "/"
            resp = redirect(target, f"{found['nickname']}님, 반가워요!")
            return resp.set_cookie(COOKIE, session_cookie(found["id"]))

    if path == "/logout":
        resp = redirect("/login", "로그아웃했습니다.")
        return resp.clear_cookie(COOKIE)

    # ---- 회원 화면 ----
    if path in ("/", "/index.html"):
        if method == "GET":
            if not user:
                return redirect("/login")
            return render_index(user, flash, flash_error)

    if path == "/checkin" and method == "GET":
        if not user:
            return redirect("/login")
        return render_checkin(user, flash, flash_error)

    if path == "/notices" and method == "GET":
        if not user:
            return redirect("/login")
        return render_notices(user, flash, flash_error)

    if path == "/auto":
        if not user:
            return redirect("/login")
        if method == "GET":
            return render_auto(user, flash, flash_error)
        weekly = {key: form_value(form, key) for key in store.WEEKDAY_KEYS}
        store.set_weekly(user["id"], weekly)
        return redirect("/auto", "자동 출석 설정을 저장했어요.")

    if path == "/profile" and method == "POST":
        if not user:
            return redirect("/login")
        nickname = form_value(form, "nickname")
        if not nickname:
            return redirect("/auto", "닉네임을 입력해 주세요.", error=True)
        store.set_nickname(user["id"], nickname)
        return redirect("/auto", "닉네임을 바꿨어요.")

    if path == "/checkin" and method == "POST":
        if not user:
            return redirect("/login")
        slot_id = form_value(form, "slot_id")
        try:
            store.check_in(user["id"], store.kst_today(), slot_id)
        except store.CheckinError as exc:
            return redirect("/checkin", exc.message, error=True)
        return redirect("/checkin", f"{store.slot_info(slot_id)['time']} 출석 완료!")

    match = re.fullmatch(r"/checkin/cancel/(\d+)", path)
    if match and method == "POST":
        if not user:
            return redirect("/login")
        record = store.get_attendance(int(match.group(1)))
        if record is None:
            return redirect("/checkin", "출석 기록을 찾을 수 없습니다.", error=True)
        if record["user_id"] != user["id"] and not user["is_admin"]:
            return redirect("/checkin", "본인 출석만 취소할 수 있어요.", error=True)
        store.cancel_checkin(record["id"])
        back = "/admin" if user["is_admin"] and record["user_id"] != user["id"] else "/checkin"
        return redirect(back, "출석을 취소했어요.")

    # ---- 관리자 화면 ----
    if path == "/admin" or path.startswith("/admin/"):
        blocked = _guard_admin(user, flash)
        if blocked:
            return blocked

        if path == "/admin":
            if method == "POST":
                closed_all = "1" in (form.get("closed_all") or [])
                blocked_slots = form.get("blocked") or []
                store.set_day(store.kst_today(), closed_all=closed_all, blocked=blocked_slots)
                if closed_all:
                    return redirect("/admin", "오늘을 전체 휴강으로 설정했어요.")
                if blocked_slots:
                    times = ", ".join(store.slot_info(s)["time"] for s in blocked_slots
                                      if s in store.SLOT_BY_ID)
                    return redirect("/admin", f"{times} 타임을 휴강으로 막았어요.")
                return redirect("/admin", "휴강 설정을 모두 풀었어요.")
            return render_admin(user, flash, flash_error)

        if path == "/admin/day" and method == "POST":
            closed_all = "1" in (form.get("closed_all") or [])
            blocked_slots = form.get("blocked") or []
            store.set_day(store.kst_today(), closed_all=closed_all, blocked=blocked_slots)
            if closed_all:
                return redirect("/admin", "오늘을 전체 휴강으로 설정했어요.")
            if blocked_slots:
                times = ", ".join(store.slot_info(s)["time"] for s in blocked_slots
                                  if s in store.SLOT_BY_ID)
                return redirect("/admin", f"{times} 타임을 휴강으로 막았어요.")
            return redirect("/admin", "휴강 설정을 모두 풀었어요.")

        if path == "/admin/notices":
            if method == "POST":
                body = form_value(form, "body")
                if not body:
                    return redirect("/admin/notices", "공지 내용을 입력해 주세요.", error=True)
                image, image_error = clean_image_data(form_value(form, "image_data"))
                if image_error:
                    return redirect("/admin/notices", image_error, error=True)
                remove = "1" in (form.get("remove_image") or [])
                new_image = image if image else ("" if remove else None)
                store.set_notice(body, user["nickname"], store.kst_today(), image_data=new_image)
                return redirect("/admin/notices", "오늘의 공지를 저장했어요.")
            return render_admin_notices(user, flash, flash_error)

        match = re.fullmatch(r"/admin/notices/(\d+)", path)
        if match and method == "POST":
            body = form_value(form, "body")
            if not body:
                return redirect("/admin/notices", "공지 내용을 입력해 주세요.", error=True)
            image, image_error = clean_image_data(form_value(form, "image_data"))
            if image_error:
                return redirect("/admin/notices", image_error, error=True)
            remove = "1" in (form.get("remove_image") or [])
            new_image = image if image else ("" if remove else None)
            if not store.update_notice(int(match.group(1)), body, image_data=new_image):
                return redirect("/admin/notices", "공지를 찾을 수 없습니다.", error=True)
            return redirect("/admin/notices", "공지를 수정했어요.")

        match = re.fullmatch(r"/admin/notices/(\d+)/delete", path)
        if match and method == "POST":
            if not store.delete_notice(int(match.group(1))):
                return redirect("/admin/notices", "삭제할 공지가 없습니다.", error=True)
            return redirect("/admin/notices", "공지를 삭제했어요.")

        if path == "/admin/users/new":
            if method == "GET":
                return render_admin_user_new(user, flash, flash_error)

        if path == "/admin/users":
            if method == "POST":
                username = form_value(form, "username").lower()
                password = form_value(form, "password")
                nickname = form_value(form, "nickname")
                if not re.fullmatch(r"[a-z0-9_]{3,20}", username):
                    return redirect("/admin/users", "아이디는 영문 소문자·숫자·밑줄 3~20자로 만들어 주세요.",
                                    error=True)
                if len(password) < 4:
                    return redirect("/admin/users", "비밀번호는 4자 이상이어야 합니다.", error=True)
                if store.username_taken(username):
                    return redirect("/admin/users", "이미 존재하는 아이디입니다.", error=True)
                store.create_user(username, password, nickname or username)
                return redirect("/admin/users", f"{nickname or username} 회원을 만들었어요.")
            return render_admin_users(user, flash, flash_error)

        match = re.fullmatch(r"/admin/users/(\d+)/delete", path)
        if match and method == "POST":
            if not store.delete_user(int(match.group(1))):
                return redirect("/admin/users", "삭제할 수 없는 계정입니다.", error=True)
            return redirect("/admin/users", "회원을 삭제했어요.")

    content = ('<div class="card"><h2>페이지를 찾을 수 없습니다.</h2>'
               '<p class="small">주소를 확인해 주세요.</p>'
               '<p class="small"><a href="/">출석 화면으로 →</a></p></div>')
    return Response(page(content, title="404 · On점핑 출석", user=user, flash=flash), 404)


def _error_page(exc: Exception) -> Response:
    if isinstance(exc, store.StoreBusy):
        message = "저장이 잠시 겹쳤어요. 잠시 뒤 다시 시도해 주세요."
    elif isinstance(exc, store.StoreError):
        message = "저장소에 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요."
    else:
        message = "문제가 발생했습니다. 잠시 뒤 다시 시도해 주세요."
    detail = f"{type(exc).__name__}: {exc}" if not isinstance(exc, store.StoreError) else ""
    content = (f'<div class="card"><h2>😥 {esc(message)}</h2>'
               + (f'<p class="small">{esc(detail)}</p>' if detail else "")
               + '<p class="small"><a href="/">출석 화면으로 →</a></p></div>')
    return Response(page(content, title="오류 · On점핑 출석"), 500)


def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO") or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    store.begin_request()
    try:
        store.init_db()
        user = store.get_user(parse_session(environ))
        flash, flash_error = read_flash(environ)
        form = read_form(environ) if method == "POST" else {}

        try:
            resp = route(method, path, form, user, flash, flash_error)
        except Exception as exc:  # 서버리스에서도 안내 화면을 보여 준다
            resp = _error_page(exc)

        if flash and not resp.flash_set:
            resp.clear_cookie(FLASH)
        return resp(environ, start_response)
    finally:
        store.end_request()
