/* 온점핑 출석 예약 — 대시보드 (의존성 없음) */

const DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];
const DAY_KO = { sun: "일", mon: "월", tue: "화", wed: "수", thu: "목", fri: "금", sat: "토" };
const STATE_LABEL = {
  voted: "예약 완료", dry: "미리 확인", already: "이미 참여", full: "정원 마감",
  no_post: "글 없음", no_slot: "타임 없음", no_session: "로그인 필요",
  unverified: "확인 필요", closed: "전체 휴강", slot_blocked: "타임 휴강", error: "오류",
};

const state = {
  session: null, config: null, status: null, slots: [], day: null,
  accounts: [], users: [], notices: [], logs: [], me: null, checkins: [],
  shakes: [], shake: null, waitlist: [], weeklySlots: [], tab: "today",
  editingUser: null, deletingUser: null,
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const isAdmin = () => state.session?.role === "admin";
const slotById = (id) => state.slots.find((s) => s.id === id) || {};
const myCheckin = (slotId) => (state.checkins || []).find(
  (c) => c.slot_id === slotId && c.target_date === state.day?.date
);
const myWaiting = (slotId) => (state.waitlist || []).some(
  (w) => w.slot_id === slotId && w.target_date === state.day?.date
);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let data = {};
  try { data = await res.json(); } catch (_) { data = { ok: false, error: "응답을 읽지 못했습니다." }; }
  if (!res.ok) throw Object.assign(new Error(data.error || "요청 실패"), { data, status: res.status });
  return data;
}

/* ---------------- 시간 ---------------- */

function tickClock() {
  const p = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
    weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(new Date()).reduce((acc, x) => ({ ...acc, [x.type]: x.value }), {});
  $("#clock").textContent = `${p.hour}:${p.minute}`;
  $("#date-label").textContent = `${p.year}.${p.month}.${p.day} (${p.weekday})`;
}

function shortDate(iso) {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-");
  const wd = DAYS[new Date(`${iso}T00:00:00`).getDay()];
  return `${m}/${d}(${DAY_KO[wd]})`;
}

function shortDateTime(iso) {
  if (!iso) return "—";
  const dt = new Date(iso.includes("T") ? iso : `${iso}T00:00:00`);
  if (Number.isNaN(dt.getTime())) return iso;
  const p = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).formatToParts(dt).reduce((acc, x) => ({ ...acc, [x.type]: x.value }), {});
  return `${p.month}/${p.day} ${p.hour}:${p.minute}`;
}

function addDays(iso, days) {
  const [y, m, d] = iso.split("-").map(Number);
  const base = new Date(Date.UTC(y, m - 1, d));
  base.setUTCDate(base.getUTCDate() + days);
  return base.toISOString().slice(0, 10);
}

/* ---------------- 이펙트 ---------------- */

function toast(message, kind = "ok") {
  const wrap = $("#toast-wrap");
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  wrap.appendChild(el);
  requestAnimationFrame(() => el.classList.add("in"));
  setTimeout(() => {
    el.classList.remove("in");
    setTimeout(() => el.remove(), 320);
  }, 2600);
}

function confetti() {
  const canvas = document.createElement("canvas");
  canvas.className = "confetti-canvas";
  document.body.appendChild(canvas);
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  canvas.width = innerWidth * dpr;
  canvas.height = innerHeight * dpr;
  ctx.scale(dpr, dpr);
  const colors = ["#166534", "#141413", "#D9480F", "#8FA88F", "#C9C6BD"];
  const cx = innerWidth / 2;
  const cy = innerHeight * 0.42;
  const parts = Array.from({ length: 90 }, () => {
    const angle = Math.random() * Math.PI * 2;
    const speed = 4 + Math.random() * 9;
    return {
      x: cx, y: cy,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed - 3,
      size: 4 + Math.random() * 6,
      rot: Math.random() * Math.PI,
      vr: (Math.random() - 0.5) * 0.3,
      color: colors[Math.floor(Math.random() * colors.length)],
      life: 1,
    };
  });
  let frames = 0;
  function frame() {
    frames += 1;
    ctx.clearRect(0, 0, innerWidth, innerHeight);
    parts.forEach((p) => {
      p.vy += 0.22;
      p.vx *= 0.99;
      p.x += p.vx; p.y += p.vy; p.rot += p.vr;
      p.life -= 0.008;
      ctx.save();
      ctx.globalAlpha = Math.max(p.life, 0);
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      ctx.fillStyle = p.color;
      ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
      ctx.restore();
    });
    if (frames < 110) requestAnimationFrame(frame);
    else canvas.remove();
  }
  requestAnimationFrame(frame);
}

function showStamp(label) {
  const overlay = $("#stamp-overlay");
  $("#stamp-sub").textContent = label || "";
  overlay.hidden = false;
  overlay.classList.remove("play");
  void overlay.offsetWidth;
  overlay.classList.add("play");
  setTimeout(() => { overlay.hidden = true; overlay.classList.remove("play"); }, 1700);
}

/* ---------------- 렌더 ---------------- */

function renderSession() {
  const { session } = state;
  const chip = $("#account-chip");
  const login = $("#login");
  if (!session) {
    chip.hidden = true;
    $("#switch-account").hidden = true;
    $("#tabs").hidden = true;
    login.hidden = false;
    renderAccounts();
    return;
  }
  login.hidden = true;
  $("#tabs").hidden = false;
  chip.hidden = false;
  $("#switch-account").hidden = false;
  $("[data-account-name]").textContent = session.nickname || session.name;
  const role = $("[data-account-role]");
  role.textContent = session.role === "admin" ? "관리자" : "일반 유저";
  role.classList.toggle("member", session.role !== "admin");
  $("#tab-more-label").textContent = isAdmin() ? "운영" : "내 기록";
}

function renderAccounts() {
  const accounts = state.accounts.length ? state.accounts : state.users;
  $("#account-list").innerHTML = accounts.length
    ? accounts.map((u) => `
      <li><button type="button" data-login="${u.id}">
        <span class="a-name">${esc(u.nickname || u.name)}</span>
        <span class="a-role">${u.role === "admin" ? "관리자" : "일반 유저"}</span>
      </button></li>`).join("")
    : `<li><p class="muted" style="padding:12px 0">계정이 없습니다. <span class="mono">python store.py</span></p></li>`;
}

function renderTabs() {
  const admin = isAdmin();
  $("#view-today").hidden = state.tab !== "today";
  $("#view-more").hidden = state.tab !== "more";
  $$(".tab").forEach((t) => t.classList.toggle("is-active", t.dataset.tab === state.tab));
  $("#runline").hidden = !admin;
  $("#side-panel").hidden = !admin;
  $("#member-cards").hidden = admin || !state.session;
  const columns = document.querySelector(".columns");
  if (columns) columns.classList.toggle("solo", !admin);
  $("#admin-slots").hidden = !admin;
  $("#history-section").hidden = admin || !state.session;
  $("#shakes-admin").hidden = !admin;
  $("#users-section").hidden = !admin;
  $("#notices-section").hidden = !admin;
}

function renderSummary() {
  const status = state.status || {};
  const slot = slotById(state.session?.target_slot || state.config?.slot_id);
  const next = status.next_run_at
    ? `${shortDateTime(status.next_run_at)} → ${shortDate(addDays(status.next_run_at.slice(0, 10), Number(state.config?.days_ahead ?? 1)))}`
    : "자동 예약 꺼짐";
  $("#next-run").textContent = next;
  $("#target-slot").textContent = slot.label || "—";
  $("#capacity").textContent = `${slot.capacity || 11}명 · ${state.slots.length}타임`;
  const login = $("#login-state");
  login.textContent = status.login_saved ? "연결됨" : "로그인 필요";
  login.style.color = status.login_saved ? "" : "#B4441C";

  const day = state.day;
  $("#day-label").textContent = day ? `오늘 ${shortDate(day.date)}` : "";
  $("#day-label-member").textContent = day ? shortDate(day.date) : "";
  $("#holiday-toggle").checked = Boolean(day?.closed_all);
  $("#holiday-banner").hidden = !day?.closed_all;
  const mine = state.session?.weekly;
  let myNext = next;
  if (!isAdmin() && mine) {
    for (let i = 1; i <= 8; i += 1) {
      const d = addDays(state.day?.date || state.status.today, i);
      const key = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"][new Date(`${d}T00:00:00Z`).getUTCDay()];
      if (mine[key]) {
        myNext = `${shortDate(d)} ${slotById(mine[key]).time}`;
        break;
      }
    }
  }
  $("#auto-next").textContent = `다음 자동 출석 · ${myNext}`;
  $("#auto-toggle").checked = Boolean(state.session?.auto);
}

function slotState(slot) {
  const day = state.day || {};
  if (day.closed_all) return { key: "closed", label: "휴강" };
  if (slot.blocked) return { key: "blocked", label: "휴강" };
  if (slot.full) return { key: "full", label: "마감" };
  return { key: "open", label: "운영 중" };
}

function renderCards() {
  const slots = state.day?.slots || [];
  $("#slot-cards").innerHTML = slots.map((slot) => {
    const view = slotState(slot);
    const mine = myCheckin(slot.id);
    const waiting = myWaiting(slot.id);
    const remain = Math.max(slot.capacity - slot.taken, 0);
    const pct = Math.min(100, Math.round((slot.taken / slot.capacity) * 100));
    let line = `남은 자리 ${remain}명`;
    let action = "";
    if (view.key === "closed" || view.key === "blocked") {
      line = "오늘은 쉬는 타임";
      action = `<button class="btn btn-lg" type="button" disabled>휴강</button>`;
    } else if (mine) {
      line = "출석 완료";
      action = `<button class="btn btn-lg btn-outline" type="button" data-cancel="${slot.id}">출석 취소</button>`;
    } else if (view.key === "full") {
      line = `만석 · 대기 ${slot.waiting}명`;
      action = waiting
        ? `<button class="btn btn-lg btn-outline" type="button" data-unwait="${slot.id}">대기 취소</button>`
        : `<button class="btn btn-lg" type="button" data-wait="${slot.id}">대기 신청 (현재 ${slot.waiting}명)</button>`;
    } else {
      action = `<button class="btn btn-lg btn-dark" type="button" data-checkin="${slot.id}">출석하기</button>`;
    }
    return `<article class="slot-card ${view.key === "open" ? "is-open" : "is-dim"} ${mine ? "is-mine" : ""}">
      <div class="sc-head">
        <h3>${esc(slot.name)}</h3>
        <span class="sc-time mono">${esc(slot.time)}</span>
      </div>
      <p class="sc-remain">${esc(line)}</p>
      <div class="meter" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100">
        <span style="width:${pct}%"></span>
      </div>
      <p class="sc-sub">${slot.taken}/${slot.capacity}명 신청</p>
      ${action}
    </article>`;
  }).join("");
}

function renderSlots() {
  const slots = state.day?.slots || [];
  $("#slot-body").innerHTML = slots.map((slot) => {
    const view = slotState(slot);
    const pct = Math.min(100, Math.round((slot.taken / slot.capacity) * 100));
    return `<tr class="${view.key === "open" ? "" : "row-dim"}">
      <td class="slot-name">${esc(slot.name)}</td>
      <td class="mono">${esc(slot.time)}</td>
      <td class="num mono">${slot.capacity}</td>
      <td class="num mono">${slot.taken}<span class="muted">/${slot.capacity}</span>
        <div class="meter slim"><span style="width:${pct}%"></span></div></td>
      <td><span class="state st-${view.key}">${esc(view.label)}</span>
        ${slot.waiting ? `<span class="wait-tag">대기 ${slot.waiting}</span>` : ""}</td>
      <td class="actions">
        <button class="btn btn-sm" type="button" data-block-slot="${slot.id}" data-blocked="${slot.blocked ? "1" : "0"}">
          ${slot.blocked ? "차단 해제" : "타임 차단"}</button>
      </td>
    </tr>`;
  }).join("");
}

function renderWeek() {
  const days = state.config?.schedule?.weekdays || [];
  const at = state.config?.schedule?.at || "21:01";
  $("#week-time").textContent = at;
  $("#week-time-2").textContent = at;
  $("#week-days").innerHTML = DAYS.map((d) => `
    <span class="day-chip ${days.includes(d) ? "on" : ""}">
      <span class="dc-day">${DAY_KO[d]}</span>
      <span class="dc-time mono">${days.includes(d) ? at : "휴무"}</span>
    </span>`).join("");
}

function renderNotice() {
  const notice = state.notice;
  if (!notice) {
    $("#notice-band").hidden = true;
    $("#notice-empty").hidden = false;
    return;
  }
  $("#notice-empty").hidden = true;
  $("#notice-band").hidden = false;
  $("#notice-title").textContent = notice.title;
  $("#notice-text").textContent = notice.body || "";
  $("#notice-text").hidden = !notice.body;
  $("#notice-meta").textContent = `${notice.posted_on} · ${notice.author}`;
  $("#notice-actions").hidden = !isAdmin();
}

function renderShake() {
  const mine = state.shake;
  $$("#flavor-picks .flavor").forEach((b) => {
    b.classList.toggle("picked", mine && mine.flavor === b.dataset.flavor);
  });
  $("#shake-status").textContent = mine ? `${mine.flavor} 쉐이크로 찜해 두었습니다.` : "아직 고르지 않았습니다.";
  const orders = state.shakes || [];
  $("#shake-count").textContent = orders.length ? `${orders.length}잔` : "";
  $("#order-list").innerHTML = orders.length
    ? orders.map((o) => `<li><span class="o-name">${esc(o.nickname || o.user_name)}</span>
        <span class="o-flavor">${esc(o.flavor)}</span></li>`).join("")
    : `<li><span class="muted">오늘 주문이 없습니다.</span></li>`;
}

function renderLogs() {
  const logs = state.logs || [];
  $("#log-count").textContent = logs.length ? `${logs.length}건` : "";
  if (!logs.length) {
    $("#log-body").innerHTML = `<tr><td colspan="6" class="empty">기록이 없습니다.</td></tr>`;
    return;
  }
  $("#log-body").innerHTML = logs.map((log) => {
    const people = log.taken == null ? "—" : `${log.taken}/${log.capacity ?? "—"}`;
    const slot = state.slots.find((s) => s.id === log.slot_id || log.slot === s.label);
    return `<tr>
      <td class="mono">${esc(shortDateTime(log.at))}</td>
      <td class="mono">${esc(shortDate(log.target_date))}</td>
      <td>${esc(slot ? `${slot.name} ${slot.time}` : (log.slot || "—"))}</td>
      <td><span class="state s-${esc(log.status)}">${esc(STATE_LABEL[log.status] || log.status)}</span></td>
      <td class="num mono">${esc(people)}</td>
      <td>${esc(log.message || "")}</td>
    </tr>`;
  }).join("");
}

function slotOptions(selected) {
  return state.slots.map((s) =>
    `<option value="${s.id}" ${s.id === selected ? "selected" : ""}>${s.name} · ${s.time}</option>`).join("");
}

const WORDER = ["mon", "tue", "wed", "thu", "fri"];

function weeklyMapFor(user) {
  if (user?.weekly) return { ...user.weekly };
  const map = {};
  const slot = user?.target_slot || state.config?.slot_id;
  const days = user?.days || [];
  WORDER.forEach((d) => { map[d] = days.includes(d) ? slot : null; });
  return map;
}

function weeklyOptions(selected) {
  return `<option value="">쉼</option>` + (state.weeklySlots.length ? state.weeklySlots : state.slots)
    .map((s) => `<option value="${s.id}" ${s.id === selected ? "selected" : ""}>${s.time} ${s.name}</option>`)
    .join("");
}

function renderWeekly(container, weekly) {
  container.innerHTML = WORDER.map((d) => `
    <div class="weekly-row">
      <span class="w-day">${DAY_KO[d]}</span>
      <select data-weekly="${d}">${weeklyOptions(weekly[d] || "")}</select>
    </div>`).join("");
}

function renderSettings() {
  if (!isAdmin()) return;
  const cfg = state.config || {};
  $("#set-time").value = cfg.schedule?.at || "21:01";
  renderWeekly($("#admin-weekly"), state.status?.weekly || {});
  if (state.status?.serverless) {
    $("#run-dry").disabled = true;
    $("#dry-desc").textContent = "서버리스 배포에서는 미리 확인을 실행할 수 없습니다. 로컬 서버나 워커에서 실행하세요.";
  }
}

function renderAutoSlots() {
  if (!state.session || isAdmin()) return;
  renderWeekly($("#member-weekly"), weeklyMapFor(state.session));
}

function renderHistory() {
  const mine = (state.checkins || []).filter((c) => c.user_id === state.session?.id && c.status === "voted");
  const dates = [...new Set(mine.map((c) => c.target_date))].sort();
  let streak = 0;
  let cursor = state.day?.date || new Date().toISOString().slice(0, 10);
  const set = new Set(dates);
  if (!set.has(cursor)) cursor = addDays(cursor, -1);
  while (set.has(cursor)) {
    streak += 1;
    cursor = addDays(cursor, -1);
  }
  $("#streak-count").textContent = streak;
  $("#streak-label").textContent = dates.length ? `최근 출석 ${shortDate(dates[dates.length - 1])}` : "";

  // 이번 달 달력
  const today = state.day?.date || new Date().toISOString().slice(0, 10);
  const [y, m] = today.split("-").map(Number);
  const first = new Date(Date.UTC(y, m - 1, 1));
  const daysInMonth = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const pad = first.getUTCDay();
  const cells = [];
  for (let i = 0; i < pad; i += 1) cells.push(`<span class="cal-cell empty"></span>`);
  for (let d = 1; d <= daysInMonth; d += 1) {
    const iso = `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    const on = set.has(iso);
    cells.push(`<span class="cal-cell ${on ? "on" : ""} ${iso === today ? "today" : ""}">${d}</span>`);
  }
  $("#calendar").innerHTML = `
    <div class="cal-head"><span>${y}년 ${m}월</span><span class="mono">${dates.length}회 출석</span></div>
    <div class="cal-grid cal-labels">${["일", "월", "화", "수", "목", "금", "토"].map((d) => `<span>${d}</span>`).join("")}</div>
    <div class="cal-grid">${cells.join("")}</div>`;

  $("#history-body").innerHTML = mine.length
    ? mine.slice().reverse().slice(0, 12).map((c) => {
        const slot = slotById(c.slot_id);
        return `<tr><td class="mono">${esc(shortDate(c.target_date))}</td>
          <td>${esc(slot.name || c.slot_id)} ${esc(slot.time || "")}</td>
          <td><span class="state s-voted">출석</span></td></tr>`;
      }).join("")
    : `<tr><td colspan="3" class="empty">아직 출석 기록이 없습니다.</td></tr>`;
}

function renderUsers() {
  if (!isAdmin()) return;
  $("#new-user-slot").innerHTML = slotOptions(state.config?.slot_id);
  $("#user-body").innerHTML = state.users.map((u) => {
    if (state.editingUser === u.id) {
      return `<tr class="editing">
        <td>${esc(u.name)}</td>
        <td><input type="text" id="edit-nick-${u.id}" value="${esc(u.nickname)}" maxlength="20" /></td>
        <td><select id="edit-slot-${u.id}">${slotOptions(u.target_slot)}</select></td>
        <td class="mono">${esc(u.days.map((d) => DAY_KO[d]).join("·"))}</td>
        <td class="actions">
          <button class="link-btn" type="button" data-user-save="${u.id}">저장</button>
          <button class="link-btn" type="button" data-user-cancel="1">취소</button>
        </td></tr>`;
    }
    if (state.deletingUser === u.id) {
      return `<tr class="editing">
        <td>${esc(u.name)}</td><td>${esc(u.nickname)}</td><td>${esc(u.target_slot_label)}</td>
        <td class="mono">${esc(u.days.map((d) => DAY_KO[d]).join("·"))}</td>
        <td class="actions"><span class="muted">삭제할까요?</span>
          <button class="link-btn danger" type="button" data-user-delete="${u.id}">삭제</button>
          <button class="link-btn" type="button" data-user-cancel="1">취소</button></td></tr>`;
    }
    const actions = u.role === "admin"
      ? `<span class="muted">기본 계정</span>`
      : `<button class="link-btn" type="button" data-user-edit="${u.id}">수정</button>
         <button class="link-btn danger" type="button" data-user-remove="${u.id}">삭제</button>`;
    return `<tr><td>${esc(u.name)}</td><td>${esc(u.nickname)}</td>
      <td>${esc(u.target_slot_label)}</td>
      <td class="mono">${esc(u.days.map((d) => DAY_KO[d]).join("·"))}</td>
      <td class="actions">${actions}</td></tr>`;
  }).join("") || `<tr><td colspan="5" class="empty">등록된 유저가 없습니다.</td></tr>`;
}

function renderNoticeAdmin() {
  if (!isAdmin()) return;
  $("#notice-list").innerHTML = (state.notices || []).map((n) => `
    <li><span class="n-title">${esc(n.title)}</span>
      <span class="n-date">${esc(n.posted_on)}</span>
      <span class="n-actions">
        <button class="link-btn" type="button" data-notice-edit-id="${n.id}">수정</button>
        <button class="link-btn danger" type="button" data-notice-remove="${n.id}">삭제</button>
      </span></li>`).join("") || `<li><span class="muted">공지가 없습니다.</span></li>`;
}

function renderFoot() {
  $("#foot-state").textContent = state.session
    ? `${state.session.nickname || state.session.name}${state.status?.serverless ? " · Vercel" : ""}`
    : "";
}

function renderAll() {
  renderSession();
  renderTabs();
  renderSummary();
  renderCards();
  renderSlots();
  renderWeek();
  renderNotice();
  renderShake();
  renderLogs();
  renderSettings();
  renderAutoSlots();
  renderHistory();
  renderUsers();
  renderNoticeAdmin();
  renderFoot();
}

/* ---------------- 데이터 ---------------- */

async function boot() {
  const data = await api("/api/bootstrap");
  state.session = data.session;
  state.config = data.config;
  state.status = data.status;
  state.slots = data.slots || [];
  state.day = data.day;
  state.accounts = data.accounts || [];
  state.users = data.users || [];
  state.notice = data.notice;
  state.notices = data.notices || [];
  state.logs = data.logs || [];
  state.me = data.me || null;
  state.checkins = data.checkins || [];
  state.shakes = data.shakes || [];
  state.shake = data.shake || null;
  state.waitlist = data.waitlist || [];
  state.weeklySlots = data.status?.weekly_slots || [];
  renderAll();
  if (state.session) {
    $("#set-nickname").value = state.session.nickname || "";
  }
}

/* ---------------- 이벤트 ---------------- */

function bind() {
  $("#tabs").addEventListener("click", (e) => {
    const tab = e.target.closest(".tab");
    if (!tab) return;
    state.tab = tab.dataset.tab;
    renderTabs();
    document.querySelector("main").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  $("#switch-account").addEventListener("click", () => {
    state.session = null;
    renderSession();
  });

  $("#account-list").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-login]");
    if (!btn) return;
    btn.disabled = true;
    try {
      await api("/api/session", { method: "POST", body: JSON.stringify({ user_id: Number(btn.dataset.login) }) });
      state.tab = "today";
      await boot();
    } catch (err) { toast(err.message, "err"); } finally { btn.disabled = false; }
  });

  // 출석 / 취소 / 대기
  $("#slot-cards").addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    const date = state.day.date;
    try {
      if (btn.dataset.checkin) {
        const id = btn.dataset.checkin;
        const { day } = await api("/api/checkin", {
          method: "POST", body: JSON.stringify({ date, slot_id: id }),
        });
        state.day = day;
        await boot();
        const slot = slotById(id);
        confetti();
        showStamp(`${slot.name} ${slot.time}`);
        toast(`${slot.name} ${slot.time} 출석 완료!`, "ok");
      } else if (btn.dataset.cancel) {
        const { day } = await api("/api/checkin/cancel", {
          method: "POST", body: JSON.stringify({ date, slot_id: btn.dataset.cancel }),
        });
        state.day = day;
        await boot();
        toast("출석을 취소했습니다.", "warn");
      } else if (btn.dataset.wait) {
        const { day } = await api("/api/waitlist", {
          method: "POST", body: JSON.stringify({ date, slot_id: btn.dataset.wait }),
        });
        state.day = day;
        await boot();
        toast("대기 신청을 받았습니다. 자리가 나면 알려 드릴게요.", "ok");
      } else if (btn.dataset.unwait) {
        const { day } = await api("/api/waitlist", {
          method: "DELETE", body: JSON.stringify({ date, slot_id: btn.dataset.unwait }),
        });
        state.day = day;
        await boot();
        toast("대기를 취소했습니다.", "warn");
      }
    } catch (err) { toast(err.message, "err"); }
  });

  // 관리자 타임 차단
  $("#slot-body").addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn || !btn.dataset.blockSlot) return;
    try {
      const { day } = await api("/api/day/slot", {
        method: "POST",
        body: JSON.stringify({
          date: state.day.date,
          slot_id: btn.dataset.blockSlot,
          blocked: btn.dataset.blocked !== "1",
        }),
      });
      state.day = day;
      renderSummary(); renderSlots(); renderCards();
      toast(btn.dataset.blocked === "1" ? "타임 차단을 해제했습니다." : "타임을 휴강 처리했습니다.", "warn");
    } catch (err) { toast(err.message, "err"); }
  });

  $("#holiday-toggle").addEventListener("change", async (e) => {
    if (!isAdmin()) return;
    try {
      const { day } = await api("/api/day", {
        method: "POST", body: JSON.stringify({ date: state.day.date, closed_all: e.target.checked }),
      });
      state.day = day;
      renderSummary(); renderSlots(); renderCards();
      toast(e.target.checked ? "오늘은 전체 휴강입니다." : "휴강을 풀었습니다.", "warn");
    } catch (err) { e.target.checked = !e.target.checked; toast(err.message, "err"); }
  });

  $("#schedule-toggle").addEventListener("change", async (e) => {
    const enabled = e.target.checked;
    try {
      await api("/api/config", { method: "POST", body: JSON.stringify({ schedule: { enabled } }) });
      await boot();
      toast(enabled ? "자동 예약을 켰습니다." : "자동 예약을 껐습니다.", enabled ? "ok" : "warn");
    } catch (err) { e.target.checked = !enabled; toast(err.message, "err"); }
  });

  // 쉐이크 찜
  $("#flavor-picks").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-flavor]");
    if (!btn) return;
    try {
      const { shake } = await api("/api/shake", {
        method: "POST", body: JSON.stringify({ date: state.day.date, flavor: btn.dataset.flavor }),
      });
      state.shake = shake;
      renderShake();
      toast(`${shake.flavor} 쉐이크 찜 완료!`, "ok");
    } catch (err) { toast(err.message, "err"); }
  });

  // 자동 출석 토글 / 타임 선택
  $("#auto-toggle").addEventListener("change", async (e) => {
    try {
      const { session } = await api("/api/profile", {
        method: "POST", body: JSON.stringify({ auto: e.target.checked }),
      });
      state.session = session;
      toast(e.target.checked ? "평일 밤 9시 자동 출석을 켰습니다." : "자동 출석을 껐습니다.",
        e.target.checked ? "ok" : "warn");
    } catch (err) { e.target.checked = !e.target.checked; toast(err.message, "err"); }
  });

  async function saveWeekly(select) {
    const weekly = {};
    $$("#member-weekly select").forEach((s) => { weekly[s.dataset.weekly] = s.value || null; });
    try {
      const { session } = await api("/api/profile", {
        method: "POST", body: JSON.stringify({ weekly }),
      });
      state.session = session;
      renderSummary();
      const picked = session.weekly?.[select.dataset.weekly];
      toast(picked ? `${DAY_KO[select.dataset.weekly]}요일은 ${slotById(picked).time} 타임으로 저장했습니다.`
                   : `${DAY_KO[select.dataset.weekly]}요일은 쉼으로 저장했습니다.`, "ok");
    } catch (err) { toast(err.message, "err"); }
  }

  $("#member-weekly").addEventListener("change", (e) => {
    if (e.target.matches("[data-weekly]")) saveWeekly(e.target);
  });

  $("#admin-weekly").addEventListener("change", async () => {
    const weekly = {};
    $$("#admin-weekly select").forEach((s) => { weekly[s.dataset.weekly] = s.value || null; });
    try {
      await api("/api/config", { method: "POST", body: JSON.stringify({ schedule: { weekly } }) });
      await boot();
      toast("요일별 자동 예약을 저장했습니다.", "ok");
    } catch (err) { toast(err.message, "err"); }
  });

  // 내 설정
  $("#profile-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("#profile-msg");
    try {
      const { session } = await api("/api/profile", {
        method: "POST", body: JSON.stringify({ nickname: $("#set-nickname").value.trim() }),
      });
      state.session = session;
      renderSession();
      msg.textContent = "닉네임을 바꿨습니다.";
      msg.className = "form-msg ok";
      toast("닉네임을 바꿨습니다.", "ok");
    } catch (err) {
      msg.textContent = err.message;
      msg.className = "form-msg err";
    }
  });

  $("#big-font").addEventListener("change", (e) => {
    const on = e.target.checked;
    document.body.classList.toggle("big-font", on);
    localStorage.setItem("onjumping.bigFont", on ? "1" : "0");
  });

  $("#settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("#settings-msg");
    const weekly = {};
    $$("#admin-weekly select").forEach((s) => { weekly[s.dataset.weekly] = s.value || null; });
    if (!Object.values(weekly).some(Boolean)) {
      msg.textContent = "요일을 하나 이상 골라 주세요.";
      msg.className = "form-msg err";
      return;
    }
    try {
      await api("/api/config", {
        method: "POST",
        body: JSON.stringify({ schedule: { at: $("#set-time").value, weekly } }),
      });
      msg.textContent = "저장했습니다.";
      msg.className = "form-msg ok";
      await boot();
      toast("자동 예약 설정을 저장했습니다.", "ok");
    } catch (err) {
      msg.textContent = err.message;
      msg.className = "form-msg err";
    }
  });

  $("#run-dry").addEventListener("click", runDry);

  // 유저 관리
  $("#user-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    const name = form.name.value.trim();
    if (!name) return;
    const days = $$("#new-user-days input:checked").map((i) => i.value);
    try {
      await api("/api/users", {
        method: "POST",
        body: JSON.stringify({
          name,
          nickname: form.nickname.value.trim(),
          target_slot: $("#new-user-slot").value,
          days: days.length ? days : ["mon"],
        }),
      });
      form.reset();
      $$("#new-user-days input").forEach((i) => { i.checked = true; });
      $("#user-msg").textContent = `${name} 추가됨`;
      $("#user-msg").className = "form-msg ok";
      await boot();
      toast(`${name} 님을 추가했습니다.`, "ok");
    } catch (err) {
      $("#user-msg").textContent = err.message;
      $("#user-msg").className = "form-msg err";
    }
  });

  $("#user-body").addEventListener("click", async (e) => {
    const el = e.target.closest("button");
    if (!el) return;
    try {
      if (el.dataset.userEdit) {
        state.editingUser = Number(el.dataset.userEdit);
        state.deletingUser = null;
        renderUsers();
      } else if (el.dataset.userCancel) {
        state.editingUser = null; state.deletingUser = null; renderUsers();
      } else if (el.dataset.userSave) {
        const id = Number(el.dataset.userSave);
        await api(`/api/users/${id}`, {
          method: "PATCH",
          body: JSON.stringify({
            nickname: $(`#edit-nick-${id}`).value.trim(),
            target_slot: $(`#edit-slot-${id}`).value,
          }),
        });
        state.editingUser = null;
        await boot();
        toast("수정했습니다.", "ok");
      } else if (el.dataset.userRemove) {
        state.deletingUser = Number(el.dataset.userRemove);
        state.editingUser = null;
        renderUsers();
      } else if (el.dataset.userDelete) {
        await api(`/api/users/${el.dataset.userDelete}`, { method: "DELETE" });
        state.deletingUser = null;
        await boot();
        toast("삭제했습니다.", "warn");
      }
    } catch (err) { toast(err.message, "err"); }
  });

  // 공지
  $("#notice-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = $("#notice-id").value;
    const payload = {
      title: $("#notice-input-title").value.trim(),
      body: $("#notice-input-body").value.trim(),
      posted_on: $("#notice-input-date").value || undefined,
    };
    if (!payload.title) return;
    try {
      if (id) await api(`/api/notices/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
      else await api("/api/notices", { method: "POST", body: JSON.stringify(payload) });
      resetNoticeForm();
      await boot();
      toast(id ? "공지를 수정했습니다." : "공지를 등록했습니다.", "ok");
    } catch (err) { toast(err.message, "err"); }
  });
  $("#notice-cancel").addEventListener("click", resetNoticeForm);
  $("#notice-list").addEventListener("click", async (e) => {
    const el = e.target.closest("button");
    if (!el) return;
    if (el.dataset.noticeEditId) {
      const n = state.notices.find((x) => x.id === Number(el.dataset.noticeEditId));
      if (!n) return;
      $("#notice-id").value = n.id;
      $("#notice-input-title").value = n.title;
      $("#notice-input-body").value = n.body;
      $("#notice-input-date").value = n.posted_on;
      $("#notice-submit").textContent = "수정 저장";
      $("#notice-cancel").hidden = false;
      $("#notice-input-title").focus();
    } else if (el.dataset.noticeRemove) {
      try {
        await api(`/api/notices/${el.dataset.noticeRemove}`, { method: "DELETE" });
        await boot();
        toast("공지를 삭제했습니다.", "warn");
      } catch (err) { toast(err.message, "err"); }
    }
  });
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-notice-edit]") && state.notice) {
      const n = state.notice;
      $("#notice-id").value = n.id;
      $("#notice-input-title").value = n.title;
      $("#notice-input-body").value = n.body;
      $("#notice-input-date").value = n.posted_on;
      $("#notice-submit").textContent = "수정 저장";
      $("#notice-cancel").hidden = false;
      state.tab = "more";
      renderTabs();
      setTimeout(() => $("#notice-input-title").focus(), 50);
    }
    if (e.target.closest("[data-notice-delete]") && state.notice) {
      if (!confirm("이 공지를 삭제할까요?")) return;
      api(`/api/notices/${state.notice.id}`, { method: "DELETE" })
        .then(boot).then(() => toast("공지를 삭제했습니다.", "warn"))
        .catch((err) => toast(err.message, "err"));
    }
  });
}

function resetNoticeForm() {
  $("#notice-id").value = "";
  $("#notice-input-title").value = "";
  $("#notice-input-body").value = "";
  $("#notice-input-date").value = state.day?.date || new Date().toISOString().slice(0, 10);
  $("#notice-submit").textContent = "공지 등록";
  $("#notice-cancel").hidden = true;
  $("#notice-msg").textContent = "";
}

async function runDry() {
  const btn = $("#run-dry");
  const out = $("#dry-output");
  btn.disabled = true;
  btn.textContent = "확인 중…";
  out.textContent = "$ python checker.py --dry-run";
  try {
    const { job_id } = await api("/api/run-dry", { method: "POST", body: "{}" });
    for (let i = 0; i < 60; i += 1) {
      await new Promise((r) => setTimeout(r, 1200));
      const { job } = await api(`/api/run-dry/${job_id}`);
      if (job.output?.length) out.textContent = job.output.join("\n");
      if (job.state !== "running") break;
    }
    await boot();
  } catch (err) {
    out.textContent = `${out.textContent}\n! ${err.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "지금 확인하기";
  }
}

/* ---------------- 시작 ---------------- */

if (localStorage.getItem("onjumping.bigFont") === "1") {
  document.body.classList.add("big-font");
  $("#big-font").checked = true;
}
tickClock();
setInterval(tickClock, 20000);
bind();
resetNoticeForm();
boot().catch((err) => {
  document.body.insertAdjacentHTML("afterbegin",
    `<p style="padding:12px 36px;border-bottom:1px solid #E4E2DC;color:#8C2F12;font-size:15px">
      서버에 연결하지 못했습니다: ${esc(err.message)} — <span class="mono">python server.py</span></p>`);
});
