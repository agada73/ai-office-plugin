/* AI 파트너스 사무실 화면 */
"use strict";
const STATUS_COLOR = {"작업 중":"#3b82f6","결재 요청":"#f59e0b","보고 완료":"#10b981","쉬는 중":"#9ca3af","이동 중":"#8b5cf6","대기 중":"#9ca3af","준비 중":"#cbd5e1"};
const TEAM_ORDER = ["기획실","아이디어팀","개발팀","강의팀","영상팀","검토실","출판팀","교육팀"];
const SPEED = 4.5;          // 걷는 속도(칸/초)
const LOUNGE_AFTER = 90;    // 이만큼(초) 놀면 휴게실로
const LOUNGE_MAX = 5;       // 휴게실 한 곳에 들어갈 수 있는 사람 (교수님 결정): 꽉 차면 다른 층 휴게실, 거기도 차면 자리에서 쉼
/* 회의실 (오른쪽 아래 방): 문은 위쪽 벽 가운데, 긴 탁자 위아래 의자 6개, 오른쪽 위 화이트보드 */
const MEETING = {doorX: 26, inY: 12.9, board: [28.6, 13.2],
  spots: [[24.6, 14.55], [26.2, 17.1], [27.7, 14.55], [24.6, 17.1], [27.7, 17.1], [26.2, 14.55], [29.3, 15.6]]};   // 위·아래 번갈아 앉아 이름표가 안 겹치게
let meetingOf = {};          // 직원 이름 → 지금 들어가 있어야 할 회의
let scene = null, T = 32, state = null, lastState = {}, people = {}, order = [];
let layers = {people: {}};
let PFLOOR = {};            // 직원 이름 → 자리가 있는 층 (1층·2층)
let floorShown = 1, AUD = null, STAIRS = [1.45, 9.0], SCENES = {}, PLACES = {}, PLACE_FLOOR = 3, PFLOOR_OF = {};
const STUDY_SHARE = 0.4;     // 오래 쉴 때 이만큼은 2층 도서실에서 자기계발 공부 (나머지는 휴게실)
const MASSAGE_AFTER = 600;   // 10분 넘게 일하고 끝내면 3층 안마실에서 쉼
const CEO = "대표";         // 대표 (3층 대표실)   // 보이는 층, 2층 강당, 계단 위치

const $ = (s, r = document) => r.querySelector(s);
/* '2026-11-07 14:42:03' → '11/7 14:42' */
/* 시간 기록용: 초까지 ('11/7 14:42:03') */
const tms = ts => { if (!ts) return ""; const [d, t] = String(ts).split(" "); const p = d.split("-"); return `${+p[1]}/${+p[2]} ${t || ""}`; };
const tm = ts => { if (!ts) return ""; const [d, t] = String(ts).split(" "); const p = d.split("-"); return `${+p[1]}/${+p[2]} ${(t || "").slice(0, 5)}`; };

/* 화면에서 오류가 나면 눈에 보이게 알려 주고, 기록을 남겨서 고칠 수 있게 함 */
function reportError(msg, stack) {
  msg = String(msg || "알 수 없는 오류");
  if (/ResizeObserver/i.test(msg)) return;
  try { fetch("/api/clientlog", {method: "POST", body: JSON.stringify({msg, stack: String(stack || ""), page: location.pathname})}); } catch (e) {}
  let b = document.getElementById("jsError");
  if (!b) { b = document.createElement("div"); b.id = "jsError"; document.body.appendChild(b); }
  b.innerHTML = `<span>⚠ 화면에 문제가 생겼어요: ${esc(msg.slice(0, 120))} <small>(기록을 남겼어요. 알려 주세요)</small></span><button>닫기</button>`;
  b.querySelector("button").onclick = () => b.remove();
}
window.addEventListener("error", e => reportError(e.message, e.error && e.error.stack));
window.addEventListener("unhandledrejection", e => reportError(e.reason && e.reason.message || e.reason, e.reason && e.reason.stack));
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const api = async (path, body) => {
  const opt = body === undefined ? {} : {method: "POST", body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { const err = new Error(data.error || "문제가 생겼어요"); err.data = data; err.status = r.status; throw err; }
  return data;
};

/* ---------- 사무실 그림 ---------- */
async function initScene() {
  scene = await (await fetch("scene.json")).json();
  const more = [];          // 2층·3층 … 있는 만큼
  for (let f = 2; f <= 5; f++) {
    let sc = null;
    try { const r = await fetch(`scene${f}.json`); if (r.ok) sc = await r.json(); } catch (e) { sc = null; }
    if (!sc) break;
    more.push([f, sc]);
  }
  T = scene.tile;
  STAIRS = scene.stairs || STAIRS;
  AUD = (more.find(([f, sc]) => sc.auditorium) || [0, {}])[1].auditorium || null;
  for (const [f, sc] of more) for (const [k, v] of Object.entries(sc.places || {})) { PLACES[k] = v; PFLOOR_OF[k] = f; if (k === "ceo") PLACE_FLOOR = f; }
  const svg = $("#scene");
  svg.setAttribute("viewBox", scene.viewBox);
  const floors = [[1, scene]].concat(more);
  for (const [f, sc] of floors) SCENES[f] = sc;
  const tile = sc => sc.size ? `<rect class="floorTile" x="0" y="0" width="${sc.size[0] * T}" height="${sc.size[1] * T}" fill="url(#tile)"/>` : "";
  svg.innerHTML = scene.defs + floors.map(([f, sc]) =>
    `<g class="floor" data-floor="${f}"${f === 1 ? "" : ' display="none"'}><g class="bg">${tile(sc)}${sc.bg}<g class="hall">${sc.hall || ""}</g><g class="bgLow">${sc.bgLow || ""}</g></g>` +
    `<g class="people"></g><g class="front">${sc.front}<g class="frontLow">${sc.frontLow || ""}</g></g></g>`).join("");
  const allPeople = {};
  for (const [f, sc] of floors) {
    layers.people[f] = svg.querySelector(`.floor[data-floor="${f}"] .people`);
    for (const [name, P] of Object.entries(sc.people)) { allPeople[name] = P; PFLOOR[name] = f; }
  }
  scene.people = allPeople;      // 직원 정보는 층과 상관없이 한곳에서 찾음
  $("#floors").innerHTML = floors.length > 1 ? floors.map(([f]) => `<button data-floor="${f}" class="${f === 1 ? "on" : ""}">본사 ${f}층 <small></small></button>`).join("") : "<small>· 본사 1층</small>";
  document.querySelectorAll("#floors button").forEach(b => b.onclick = () => showFloor(+b.dataset.floor));
  for (const [name, P] of Object.entries(scene.people)) {
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.setAttribute("class", "emp");
    g.innerHTML =
      `<g class="f-seat">${P.seatFigure}</g><g class="f-idle" display="none">${P.figure}</g><g class="f-coffee" display="none">${P.idleCoffee}</g>` +
      `<g class="f-back" display="none">${P.back || P.seatFigure}</g>` +
      `<g class="f-walk" display="none">${P.walk}</g><g class="f-paper" display="none">${P.walkPaper}</g><g class="f-wcoffee" display="none">${P.walkCoffee}</g>` +
      `<g class="bub"></g>` +
      `<text y="15" font-size="11" text-anchor="middle" font-weight="700" fill="${P.ceo ? "#b8860b" : "#3d3027"}" paint-order="stroke" stroke="#fff" stroke-width="3">${P.ceo ? "👑 대표" : esc(name)}</text>`;
    g.addEventListener("click", () => P.ceo ? openApprovals() : name === "기획실장" ? openNewTask() : openProfile(name));
    const p = {name, el: g, x: P.seat[0], y: P.seat[1], at: "seat", path: [], carry: null, busy: false, timer: null, slot: 0, spot: null,
               home: PFLOOR[name], floor: PFLOOR[name]};
    people[name] = p;
    layers.people[p.floor].appendChild(g);
    place(p);
  }
  floorCounts();
  reorder();
  initCast();
  fitHall();
  if (window.ResizeObserver) { const ro = new ResizeObserver(() => fitHall()); ro.observe($(".boardwrap")); ro.observe($(".scene")); }
  else window.addEventListener("resize", fitHall);
  requestAnimationFrame(tick);
}

/* ---------- 복도 넓이: 사무실 그림 높이를 오른쪽 현황판 높이에 맞춤 ---------- */
let hallExtra = 0;         // 복도를 몇 칸 더 넓혔는지
function fitHall() {
  if (!scene || !scene.size) return;
  const svg = $("#scene"), bw = $(".boardwrap");
  const side = getComputedStyle($(".grid")).display === "grid" && bw.getBoundingClientRect().left > svg.getBoundingClientRect().left + 10;
  let extra = 0;
  if (side) {
    const w = svg.getBoundingClientRect().width;
    const target = Math.max(320, bw.getBoundingClientRect().width * 0.92) - 8;     // 현황판 높이 - 액자 여백
    const vbw = scene.size[0] * T + 16;
    extra = Math.min(8, Math.max(0, (target * vbw / w - 16) / T - scene.size[1]));
  }
  if (Math.abs(extra - hallExtra) < 0.02 && svg.dataset.fit) return;
  hallExtra = extra; svg.dataset.fit = "1";
  svg.setAttribute("viewBox", `-8 -8 ${scene.size[0] * T + 16} ${(scene.size[1] + extra) * T + 16}`);
  svg.querySelectorAll(".floorTile").forEach(e => e.setAttribute("height", (scene.size[1] + extra) * T));
  svg.querySelectorAll(".bgLow, .frontLow").forEach(e => e.setAttribute("transform", `translate(0,${(extra * T).toFixed(1)})`));
  svg.querySelectorAll(".hall").forEach(e => e.setAttribute("transform", `translate(0,${(extra * T / 2).toFixed(1)})`));
  Object.values(people).forEach(place);
}
// 직원의 그림 속 y(칸) → 화면 y: 복도 두 줄 사이는 늘어나고, 아래 줄 방은 넓힌 만큼 내려감
function screenY(y) {
  if (!hallExtra || !scene.stretch) return y;
  const [a, b] = scene.stretch;
  if (y <= a) return y;
  if (y >= b) return y + hallExtra;
  return a + (y - a) * (b - a + hallExtra) / (b - a);
}

/* ---------- 층 (1층·2층): 위쪽 버튼으로 오가요. 직원은 계단으로 층을 오르내려요 ---------- */
function showFloor(f) {
  floorShown = f;
  document.querySelectorAll("#scene .floor").forEach(g => g.setAttribute("display", +g.dataset.floor === f ? "inline" : "none"));
  document.querySelectorAll("#floors button").forEach(b => b.classList.toggle("on", +b.dataset.floor === f));
}
function floorCounts() {
  document.querySelectorAll("#floors button").forEach(b => {
    const n = Object.values(people).filter(p => p.floor === +b.dataset.floor).length;
    $("small", b).textContent = `· ${n}명`;
  });
}
function moveToFloor(p, f) {
  p.floor = f;
  if (layers.people[f]) layers.people[f].appendChild(p.el);
  order = [];
  floorCounts();
}

/* ---------- 직원 소개 보드 (사무실 아래) ---------- */
function portrait(name) {
  return `<svg viewBox="-24 -62 48 70" aria-hidden="true">${scene.people[name].figure}</svg>`;
}
function initCast() {
  const names = Object.keys(scene.people).filter(n => !scene.people[n].ceo);
  $("#cast").innerHTML = `<h3>👥 직원 소개 <small>직원을 누르면 소개와 작업 이력이 나와요</small></h3><div class="cards">` +
    names.map(n => {
      const P = scene.people[n];
      return `<button class="ecard" data-name="${esc(n)}" style="--tc:${P.color}"><span class="face">${portrait(n)}</span>` +
        `<span class="ci"><span><b>${esc(n)}</b><span class="tm">${esc(P.team)}</span></span>` +
        `<span class="role"></span><span class="st"><i class="d"></i><span class="stt">…</span></span></span></button>`;
    }).join("") + `</div>`;
  document.querySelectorAll(".ecard").forEach(c => c.onclick = () => openProfile(c.dataset.name));
}
function updateCast(s) {
  for (const e of s.employees) {
    const c = document.querySelector(`.ecard[data-name="${CSS.escape(e.name)}"]`);
    if (!c) continue;
    const st = (people[e.name]?.at === "lounge") ? "쉬는 중" : e.state;
    c.classList.toggle("off", !e.active);
    $(".role", c).textContent = e.role;
    $(".d", c).style.background = STATUS_COLOR[st] || "#999";
    $(".stt", c).textContent = e.active ? `${st} · ${e.rank} · 일 ${e.done}번` : "준비 중";
  }
}

function place(p) { p.el.setAttribute("transform", `translate(${(p.x * T).toFixed(1)},${(screenY(p.y) * T).toFixed(1)})`); }

function setFigure(p, moving) {
  let which = "f-idle";
  if (moving) which = p.carry === "paper" ? "f-paper" : p.carry === "coffee" ? "f-wcoffee" : "f-walk";
  else if (p.at === "seat") which = "f-seat";
  else if (p.at === "lounge") which = "f-coffee";
  else if (p.at === "training") which = p.name === "교육 담당" ? "f-idle" : "f-back";   // 강당: 강사(앞)를 보고 앉은 뒷모습
  else if (p.at === "meeting" || p.at === "ceo" || p.at === "meeting2") which = "f-idle";
  else if (p.at === "lunch" || p.at === "massage") which = "f-coffee";
  else if (p.at === "study") which = "f-idle";
  for (const c of ["f-seat","f-idle","f-back","f-coffee","f-walk","f-paper","f-wcoffee"]) $("." + c, p.el).setAttribute("display", c === which ? "inline" : "none");
}

function setBubble(p, text, color) {
  const w = text.length * 11 + 16;
  const W = 960, px = p.x * T;                       // 그림 가장자리에서 잘리지 않게 말풍선을 안쪽으로 밀어 줌
  const dx = Math.min(0, W - 4 - (px + w / 2)) + Math.max(0, 4 - (px - w / 2));
  $(".bub", p.el).innerHTML =
    `<rect x="${dx - w/2}" y="-80" width="${w}" height="19" rx="9.5" fill="#fff" stroke="${color}" stroke-width="2"/>` +
    `<text x="${dx}" y="-66.5" font-size="11" text-anchor="middle" fill="${color}" font-weight="700">${esc(text)}</text>`;
}

function reorder() {
  const sorted = Object.values(people).sort((a, b) => a.y - b.y);
  const names = sorted.map(p => p.name).join("|");
  if (names !== order.join("|")) {
    order = sorted.map(p => p.name);
    sorted.forEach(p => (layers.people[p.floor] || layers.people[1]).appendChild(p.el));
  }
}

let lastT = 0;
function tick(t) {
  const dt = Math.min(0.1, (t - lastT) / 1000 || 0); lastT = t;
  let moved = false;
  for (const p of Object.values(people)) {
    if (!p.path.length) continue;
    let step = SPEED * dt;
    while (step > 0 && p.path.length) {
      if (!Array.isArray(p.path[0])) { moveToFloor(p, p.path[0].floor); p.path.shift(); continue; }   // 계단: 다른 층으로
      const [tx, ty] = p.path[0];
      const dx = tx - p.x, dy = ty - p.y, d = Math.hypot(dx, dy);
      if (d <= step) { p.x = tx; p.y = ty; p.path.shift(); step -= d; }
      else { p.x += dx / d * step; p.y += dy / d * step; step = 0; }
    }
    place(p); moved = true;
    if (!p.path.length) arrived(p);
  }
  if (moved) reorder();
  requestAnimationFrame(tick);
}

function arrived(p) {
  p.at = p.goal;
  p.carry = null;
  setFigure(p, false);
  if (p.at === "planner" || (p.at === "ceo" && !p.calledUntil)) {          // 기획실장·대표께 잠깐 보고하고 돌아감
    clearTimeout(p.timer);
    p.timer = setTimeout(() => { p.busy = false; walkTo(p, "seat"); }, p.at === "ceo" ? 3500 : 2500);
  } else if (p.at === "ceo" && p.calledUntil) {      // 대표실로 불려 와서 잠시 이야기
    clearTimeout(p.timer);
    p.timer = setTimeout(() => { p.calledUntil = 0; p.busy = false; walkTo(p, "seat"); }, Math.max(3000, p.calledUntil - Date.now()));
    setBubble(p, "대표님과 이야기 중", STATUS_COLOR["작업 중"]);
  } else {
    p.busy = false;
  }
  applyBubble(p);
}

/* ---------- 걷는 길 ---------- */
function exitPts(name) {
  const e = scene.people[name].exit;       // 자리에서 복도까지의 길(방마다 다름, 그림 도구가 계산)
  return {pts: e.pts.map(p => p.slice()), lane: e.lane, x: e.x};
}
function lanePath(a, bx, b) {
  const L = scene.lanes, ya = a === "bottom" ? L.bottom : L.top, yb = b === "bottom" ? L.bottom : L.top;
  if (ya === yb) return [[bx, yb]];
  return [[L.connectorX, ya], [L.connectorX, yb], [bx, yb]];
}
function loungeCount(f, except) {
  return Object.values(people).filter(q => q !== except && q.loungeFloor === f && (q.at === "lounge" || (q.at === "walking" && q.goal === "lounge"))).length;
}
function loungeCountAt(goal) { return Object.values(people).filter(q => q.pspotOf === goal).length; }
function pickLounge(p) {      // 내 층 휴게실 → 다른 층 휴게실(3층은 카페) → 없으면 0 (자리에서 쉼). 오후 3시~3시 반은 3층 카페부터
  const hm = new Date().getHours() * 60 + new Date().getMinutes();
  let floors = [p.home].concat(Object.keys(layers.people).map(Number).filter(f => f !== p.home));
  if (hm >= 15 * 60 && hm < 15 * 60 + 30 && SCENES[3]) floors = [3].concat(floors.filter(f => f !== 3));
  return floors.find(f => loungeCount(f, p) < LOUNGE_MAX) || 0;
}
function loungeSpot(p) {
  // 그 층 휴게실의 비어 있는 자리에 차례로 앉힘 (두 사람이 같은 자리에 겹치지 않게)
  const spots = (SCENES[p.loungeFloor] || scene).lounge;
  if (p.spot == null) {
    const used = new Set(Object.values(people).filter(q => q !== p && q.spot != null && q.loungeFloor === p.loungeFloor).map(q => q.spot));
    let i = 0;
    while (used.has(i)) i++;
    p.spot = i;
  }
  return spots[Math.min(p.spot, spots.length - 1)].slice();
}
function meetingSpot(p) {
  const m = meetingOf[p.name];
  if (m && m.kind !== "compare" && p.name === "기획실장") return MEETING.board.slice();   // 기획실장은 화이트보드 앞
  if (p.mspot == null) {
    const used = new Set(Object.values(people).filter(q => q !== p && q.mspot != null).map(q => q.mspot));
    let i = 0;
    while (used.has(i)) i++;
    p.mspot = i;
  }
  return MEETING.spots[Math.min(p.mspot, MEETING.spots.length - 1)].slice();
}
function placeSpot(p, goal) {
  const P = PLACES[goal];
  if (goal === "meeting2" && p.name === CEO) return P.head.slice();
  if (p.pspot == null || p.pspotOf !== goal) {
    const used = new Set(Object.values(people).filter(q => q !== p && q.pspotOf === goal && q.pspot != null).map(q => q.pspot));
    let i = 0;
    while (used.has(i)) i++;
    p.pspot = i; p.pspotOf = goal;
  }
  return P.spots[Math.min(p.pspot, P.spots.length - 1)].slice();
}
function trainingSpot(p) {
  if (p.name === "교육 담당") return AUD.podium.slice();
  if (p.mspot == null) {
    const used = new Set(Object.values(people).filter(q => q !== p && q.mspot != null).map(q => q.mspot));
    let i = 0;
    while (used.has(i)) i++;
    p.mspot = i;
  }
  return AUD.spots[Math.min(p.mspot, AUD.spots.length - 1)].slice();
}
function goalFloor(p, goal) {
  if (goal === "seat") return p.home;
  if (PLACES[goal]) return PFLOOR_OF[goal] || PLACE_FLOOR;
  if (goal === "lounge") return p.loungeFloor || p.home;
  if (goal === "training") return AUD ? (Object.keys(SCENES).find(f => SCENES[f].auditorium) | 0) || 2 : 1;
  return 1;      // 기획실장에게 보고·회의실은 1층
}
function walkTo(p, goal, carry) {
  if (goal === "training" && !AUD) goal = "meeting";
  const L = scene.lanes, P = scene.people[p.name], from = p.at;
  let pts = [], lane, x;
  if (p.at === "seat") { const e = exitPts(p.name); pts = e.pts.slice(); lane = e.lane; x = e.x; }
  else if (p.at === "lounge") { const ld = (SCENES[p.floor] || scene).lanes.loungeDoorX; pts = [[ld, L.wallInY], [ld, L.top]]; lane = "top"; x = ld; }
  else if (p.at === "meeting") { pts = [[MEETING.doorX, MEETING.inY], [MEETING.doorX, L.bottom]]; lane = "bottom"; x = MEETING.doorX; }
  else if (PLACES[p.at]) { const P = PLACES[p.at]; pts = [[P.doorX, P.inY], [P.doorX, P.lane === "top" ? L.top : L.bottom]]; lane = P.lane; x = P.doorX; }
  else if (p.at === "training" && AUD) { pts = [[AUD.doorX, AUD.inY], [AUD.doorX, L.bottom]]; lane = "bottom"; x = AUD.doorX; }
  else { const e = exitPts("기획실장"); pts = e.pts.slice(1); lane = e.lane; x = e.x; p.x = e.pts[0][0]; p.y = e.pts[0][1]; }
  let tail = [], dlane, dx;
  if (goal === "seat") { const e = exitPts(p.name); tail = e.pts.slice().reverse().concat([P.seat]); dlane = e.lane; dx = e.x; }
  else if (goal === "lounge") { const ld = (SCENES[p.loungeFloor || p.home] || scene).lanes.loungeDoorX; const s = loungeSpot(p); tail = [[ld, L.wallInY], s]; dlane = "top"; dx = ld; }
  else if (goal === "meeting") { const s = meetingSpot(p); tail = [[MEETING.doorX, MEETING.inY], [s[0], MEETING.inY + .6], s]; dlane = "bottom"; dx = MEETING.doorX; }
  else if (PLACES[goal]) { const P = PLACES[goal], s = placeSpot(p, goal); tail = [[P.doorX, P.inY], [s[0], P.inY + (P.lane === "top" ? -.6 : .6)], s]; dlane = P.lane; dx = P.doorX; }
  else if (goal === "training") { const s = trainingSpot(p); tail = [[AUD.doorX, AUD.inY], [s[0], AUD.inY + .6], s]; dlane = "bottom"; dx = AUD.doorX; }
  else { const e = exitPts("기획실장"); tail = e.pts.slice().reverse().slice(0, 4).concat([]); tail = e.pts.slice().reverse(); dlane = e.lane; dx = e.x; }
  const df = goalFloor(p, goal);
  if (df !== p.floor) {      // 다른 층: 계단까지 걸어가서 → 다른 층 계단에서 나와 → 목적지로
    const [sx, sy] = STAIRS;
    p.path = pts.concat(lanePath(lane, sx, "top"), [[sx, sy], {floor: df}, [sx, L.top]], lanePath("top", dx, dlane), tail);
  } else p.path = pts.concat(lanePath(lane, dx, dlane), tail);
  p.goal = goal; p.carry = carry || (goal === "seat" && p.at === "lounge" ? "coffee" : null);
  if (from === "lounge" && goal !== "lounge") { p.spot = null; p.loungeFloor = 0; }   // 자리를 비워 줌
  if ((from === "meeting" || from === "training") && goal !== from) p.mspot = null;
  if (PLACES[from] && goal !== from) { p.pspot = null; p.pspotOf = null; }
  p.busy = true; p.at = "walking";
  setFigure(p, true);
  setBubble(p, goal === "planner" ? "기획실장에게 보고하러" : goal === "lounge" ? (p.loungeFloor && p.loungeFloor !== p.floor ? `${p.loungeFloor}층 휴게실로` : "휴게실 가는 중") : goal === "meeting" ? (p.floor === 1 ? "회의실 가는 중" : "1층 회의실로") : goal === "training" ? (p.floor === 2 ? "강당 가는 중" : "2층 강당으로") : goal === "lunch" ? "점심 먹으러" : goal === "massage" ? "안마실로 🎁" : goal === "ceo" ? (p.calledUntil ? "대표실로 가요" : "대표님께 보고하러") : goal === "meeting2" ? "대표 회의실로" : goal === "study" ? "도서실로 📚" : "자리로 가는 중", STATUS_COLOR["이동 중"]);
}

/* ---------- 상태 → 그림 ---------- */
function applyBubble(p) {
  const e = (state?.employees || []).find(x => x.name === p.name);
  if (!e || p.at === "walking") return;
  let st = e.state, txt;
  if (p.name === CEO) { $(".bub", p.el).innerHTML = ""; return; }
  if (p.at === "lunch") { setBubble(p, "점심 먹는 중 🍽", STATUS_COLOR["쉬는 중"]); return; }
  if (p.at === "massage") { setBubble(p, "안마 중 💆 수고했어요", STATUS_COLOR["쉬는 중"]); return; }
  if (p.at === "study") { setBubble(p, "📚 자기계발 공부 중", "#7c5cff"); return; }
  if (p.at === "meeting2") { setBubble(p, "대표 회의 중", STATUS_COLOR["작업 중"]); return; }
  if (p.at === "lounge") st = "쉬는 중";
  else if (p.at === "seat" && p.restAtSeat && st === "대기 중") { setBubble(p, "자리에서 쉬는 중", STATUS_COLOR["쉬는 중"]); return; }
  txt = st === "작업 중" ? e.text : st;
  if ((p.at === "meeting" || p.at === "training") && meetingOf[p.name]) {
    const m = meetingOf[p.name];
    if (m.members[0] !== p.name) { $(".bub", p.el).innerHTML = ""; return; }   // 말풍선은 회의를 이끄는 한 사람만 (겹치지 않게)
    txt = m.text + (m.members.length > 1 ? ` (${m.members.length}명)` : ""); st = "작업 중";
  }
  setBubble(p, txt.length > 14 ? txt.slice(0, 13) + "…" : txt, STATUS_COLOR[st] || "#999");
}

function applyState(s) {
  state = s;
  const waitN = (s.counts || {}).approvals || 0;      // 휴대폰 탭·홈 화면에서도 결재 대기가 바로 보이게
  document.title = (waitN ? `(${waitN}) 결재 대기 · ` : "") + "AI 파트너스 사무실";
  const cur = {};
  meetingOf = {};
  for (const m of s.meetings || []) for (const n of m.members) meetingOf[n] = m;
  const now = new Date(), hm = now.getHours() * 60 + now.getMinutes();
  const lunch = hm >= 12 * 60 && hm < 13 * 60, coffee = hm >= 15 * 60 && hm < 15 * 60 + 30;
  const ceoMeet = (s.meetings || []).some(m => m.kind === "ceo");
  const ceo = people[CEO];
  if (ceo && PLACES.meeting2 && !ceo.busy) {      // 대표 회의가 열리면 교수님도 연결문으로 회의실 2 에
    if (ceoMeet && ceo.at !== "meeting2") walkTo(ceo, "meeting2");
    else if (!ceoMeet && ceo.at === "meeting2") walkTo(ceo, "seat");
  }
  for (const e of s.employees) {
    cur[e.name] = e.state;
    const p = people[e.name];
    if (!p) continue;
    const col = e.state === "작업 중" ? STATUS_COLOR["작업 중"] : e.state === "결재 요청" ? STATUS_COLOR["결재 요청"] : e.state === "보고 완료" ? STATUS_COLOR["보고 완료"] : "#475569";
    document.querySelectorAll(`[data-mon="${CSS.escape(e.name)}"]`).forEach(m => m.setAttribute("fill", col));
    p.slot = Object.keys(people).indexOf(e.name);
    if (!p.busy) {
      const wantMeet = !!meetingOf[e.name];
      const wantLounge = !wantMeet && e.active && e.state === "대기 중" && e.name !== "기획실장" && e.idle > LOUNGE_AFTER;
      const mk = wantMeet ? meetingOf[e.name].kind : "";
      const meetGoal = mk === "training" ? "training" : mk === "ceo" && PLACES.meeting2 ? "meeting2" : "meeting";
      // 10분 넘게 일하고 끝냈으면 안마실 🎁
      if (e.state === "작업 중") { if (!p.workSince) p.workSince = Date.now(); }
      else if (p.workSince) {
        const worked = (Date.now() - p.workSince) / 1000; p.workSince = 0;
        if (worked > MASSAGE_AFTER && PLACES.massage && !wantMeet && e.name !== "기획실장") p.massageUntil = Date.now() + 60000;
      }
      const idle = e.active && e.state === "대기 중" && !wantMeet;
      const wantLunch = lunch && idle && PLACES.lunch;
      const wantMassage = idle && p.massageUntil > Date.now();
      if (wantLunch && (p.at === "seat" || p.at === "lounge")) { walkTo(p, "lunch"); applyBubble(p); continue; }
      if (!wantLunch && p.at === "lunch") { walkTo(p, "seat"); applyBubble(p); continue; }
      if (wantMassage && p.at === "seat" && loungeCountAt("massage") < 4) { walkTo(p, "massage"); applyBubble(p); continue; }
      if (!wantMassage && p.at === "massage") { walkTo(p, "seat"); applyBubble(p); continue; }
      // 오래 쉴 때: 일부는 2층 도서실에서 자기계발 공부 (자리가 5개라 차면 휴게실)
      if (wantLounge && p.at === "seat" && PLACES.study) {
        if (p.restPick == null) p.restPick = Math.random() < STUDY_SHARE ? "study" : "lounge";
        if (p.restPick === "study" && loungeCountAt("study") < 5) { walkTo(p, "study"); applyBubble(p); continue; }
      }
      if (!wantLounge && p.at === "study") { p.restPick = null; walkTo(p, "seat"); applyBubble(p); continue; }
      if (!wantLounge && p.at === "seat") p.restPick = null;
      if (e.state === "보고 완료" && e.name === "기획실장" && lastState[e.name] && lastState[e.name] !== "보고 완료" && p.at === "seat" && PLACES.ceo) { walkTo(p, "ceo", "paper"); applyBubble(p); continue; }
      if (wantMeet && p.at !== meetGoal) walkTo(p, meetGoal);
      else if (!wantMeet && (p.at === "meeting" || p.at === "training" || p.at === "meeting2")) {
        const lf = wantLounge ? pickLounge(p) : 0;
        if (lf) p.loungeFloor = lf;
        walkTo(p, lf ? "lounge" : "seat");
      }
      else if (wantLounge && p.at === "seat") {
        const lf = pickLounge(p);
        if (lf) { p.restAtSeat = false; p.loungeFloor = lf; walkTo(p, "lounge"); }
        else p.restAtSeat = true;          // 휴게실 두 곳이 다 차서 자리에서 쉼
      }
      else if (!wantLounge && p.restAtSeat) p.restAtSeat = false;
      else if (!wantLounge && p.at === "lounge") walkTo(p, "seat");
      else if (e.state === "보고 완료" && lastState[e.name] && lastState[e.name] !== "보고 완료" && e.name !== "기획실장" && p.at === "seat") walkTo(p, "planner", "paper");
    }
    applyBubble(p);
  }
  lastState = cur;
  renderBoard(s);
  updateCast(s);
  $("#company").textContent = s.company;
  $("#headMeta").textContent = (s.host ? `💻 ${s.host} · ` : "") + `직원 ${s.employees.length}명 · ` + new Date().toLocaleDateString("ko-KR", {year: "numeric", month: "long", day: "numeric", weekday: "short"});
  $("#fakeBadge").hidden = !s.fake;
  const n = $("#notice");
  n.hidden = !s.notice; n.textContent = s.notice ? "⚠ " + s.notice : "";
}

function renderBoard(s) {
  const teams = {};
  for (const e of s.employees) (teams[e.team] ||= []).push(e);
  let rows = "";
  for (const t of TEAM_ORDER) {
    if (!teams[t]) continue;
    const color = scene.people[teams[t][0].name].color;
    rows += `<div class="team" style="color:${color}">${esc(t)}</div>`;
    for (const e of teams[t]) {
      const st = (people[e.name]?.at === "lounge") ? "쉬는 중" : e.state;
      rows += `<div class="row${e.quiet ? " quiet" : ""}" data-name="${esc(e.name)}"><span class="dot" style="background:${e.quiet ? "#ea580c" : STATUS_COLOR[st]}"></span>` +
        `<span class="rname">${esc(e.name)}${e.active ? `<span class="rk lv${e.level || 0}">${esc(e.rank)}</span>` : ""}</span>` +
        `<span class="rtask">${e.quiet ? `<b class="qtag">⏳ ${e.quiet}분째 소식 없음</b> ` : ""}${esc(e.active ? (e.state === "작업 중" || e.state === "결재 요청" || e.state === "보고 완료" ? e.text : st) : "준비 중")}</span></div>`;
    }
  }
  const c = s.counts;
  $("#board").innerHTML =
    `<h3>📋 현황판</h3><div class="kpis">` +
    `<button class="kpi k1" id="kApprove"><b>${c.approvals}</b><span>결재 대기</span></button>` +
    `<button class="kpi k2" id="kReport"><b>${c.reports}</b><span>보고함</span></button>` +
    `<button class="kpi k3" id="kWork"><b>${c.working}</b><span>작업 중</span></button></div>` +
    (c.quiet ? `<button class="qbar" id="qBar">⏳ 오래 소식이 없는 일이 ${c.quiet}건 있어요 — 눌러서 확인</button>` : "") +
    projStrip(s.projects || []) +
    `<div class="rows">${rows}</div><div class="newrow"><button class="newtask" id="newTask">+ 기획실장 새 일 맡기기</button><button class="newtask idea" id="newIdea">💡 아이디어</button><button class="newtask meet" id="newMeet">🗣 회의 소집</button></div>`;
  $("#kApprove").onclick = openApprovals;
  $("#kReport").onclick = openReports;
  $("#kWork").onclick = openWorking;
  $("#qBar")?.addEventListener("click", openWorking);
  showPromotions(s.promotions || []);
  showFinished(s.tasks || []);
  $("#newTask").onclick = () => openNewTask();
  $("#newIdea").onclick = openIdea;
  $("#newMeet").onclick = openMeeting;
  $("#projOpen").onclick = () => openProjects();
  document.querySelectorAll("[data-pj]").forEach(b => b.onclick = () => openProjects(b.dataset.pj));
  document.querySelectorAll(".row").forEach(r => r.onclick = () => r.dataset.name === "기획실장" ? openTaskList() : openProfile(r.dataset.name));
}

/* ---------- 승진 축하 알림 ---------- */
const seenPromo = new Set((() => { try { return JSON.parse(localStorage.getItem("seenPromo") || "[]"); } catch (e) { return []; } })());
function showPromotions(list) {
  for (const p of list) {
    if (seenPromo.has(p.id)) continue;
    seenPromo.add(p.id);
    try { localStorage.setItem("seenPromo", JSON.stringify([...seenPromo].slice(-50))); } catch (e) {}
    const box = $("#toasts") || document.body.appendChild(Object.assign(document.createElement("div"), {id: "toasts"}));
    const t = document.createElement("button");
    t.className = "toast";
    t.innerHTML = `<span class="tface">${scene && scene.people[p.name] ? portrait(p.name) : "🎉"}</span>` +
      `<span><b>🎉 ${esc(p.name)} 승진!</b><br>${esc(p.from)} → <b>${esc(p.rank)}</b><small>일 ${p.done}번 · 한 번에 통과 ${p.passRate ?? "-"}%</small></span>`;
    t.onclick = () => { t.remove(); openProfile(p.name); };
    box.appendChild(t);
    setTimeout(() => t.classList.add("out"), 30000);
    setTimeout(() => t.remove(), 30600);
  }
}

/* ---------- 일 끝남 알림: 눌러서 결과물 보기 ---------- */
const seenDone = new Set((() => { try { return JSON.parse(localStorage.getItem("seenDone") || "[]"); } catch (e) { return []; } })());
function showFinished(tasks) {
  for (const t of tasks) {
    if (t.status !== "done" || !t.finished || seenDone.has(t.id)) continue;
    seenDone.add(t.id);
    try { localStorage.setItem("seenDone", JSON.stringify([...seenDone].slice(-100))); } catch (e) {}
    if (Date.now() - new Date(t.finished.replace(" ", "T")).getTime() > 3 * 3600e3) continue;   // 오래전에 끝난 일은 조용히
    const box = $("#toasts") || document.body.appendChild(Object.assign(document.createElement("div"), {id: "toasts"}));
    const el = document.createElement("button");
    el.className = "toast done";
    el.innerHTML = `<span class="tface">✅</span><span><b>${esc(t.title)}</b> 끝났어요<small>눌러서 결과물과 저장 폴더 보기</small></span>`;
    el.onclick = () => { el.remove(); openTask(t.id); };
    box.appendChild(el);
    setTimeout(() => el.classList.add("out"), 60000);
    setTimeout(() => el.remove(), 60600);
  }
}

async function refresh() {
  try { applyState(await api("/api/state")); }
  catch (e) { $("#notice").hidden = false; $("#notice").textContent = "⚠ 사무실 서버와 연결이 끊겼어요. 맥에서 사무실이 켜져 있는지 확인해 주세요."; }
}

/* ---------- 창(모달) ---------- */
let modalTimer = null;
function openModal(title, html, onRefresh) {
  $(".modal").classList.remove("wide");
  $("#mtitle").textContent = title;
  $("#mbody").innerHTML = html;
  $("#overlay").hidden = false;
  clearInterval(modalTimer);
  if (onRefresh) modalTimer = setInterval(onRefresh, 3000);
}
function closeModal() { $("#overlay").hidden = true; clearInterval(modalTimer); }
$("#mclose").onclick = closeModal;
$("#overlay").addEventListener("mousedown", e => { if (e.target.id === "overlay") closeModal(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });

function fileLinks(tid, files) {
  if (!files || !files.length) return "";
  return `<div class="files">` + files.map(f => {
    const u = `/api/file?task=${encodeURIComponent(tid)}&path=${encodeURIComponent(f)}`;
    if (/\.(mp4|webm)$/i.test(f)) return `<a href="${u}" target="_blank">▶ ${esc(f)} 재생</a><a href="${u}&dl=1">⬇ 받기</a>`;
    return /\.(md|txt)$/i.test(f) ? `<a href="${u}" target="_blank">📄 ${esc(f)}</a>` : `<a href="${u}&dl=1">⬇ ${esc(f)}</a>`;
  }).join("") + `</div>`;
}

/* 👁 결재함 미리보기: 내려받지 않고 결재함 안에서 바로 봐요 (글·표는 읽기 좋게, 그림·PDF·영상은 그대로) */
function mdToHtml(md) {
  const lines = esc(md).split("\n"), out = [];
  let inTable = false, inList = false;
  const inline = t => t.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
  const close = () => { if (inList) { out.push("</ul>"); inList = false; } if (inTable) { out.push("</table>"); inTable = false; } };
  for (const raw of lines) {
    const l = raw.trimEnd();
    if (/^\|.*\|$/.test(l)) {
      if (/^\|[\s:|-]+\|$/.test(l)) continue;           // 표 줄 구분선
      if (!inTable) { close(); out.push('<table class="pvt">'); inTable = true; }
      out.push("<tr>" + l.slice(1, -1).split("|").map(c => `<td>${inline(c.trim())}</td>`).join("") + "</tr>");
      continue;
    }
    const m = l.match(/^(#{1,4})\s+(.*)/), b = l.match(/^\s*[-*·]\s+(.*)/), n = l.match(/^\s*(\d+)[.)]\s+(.*)/);
    if (m) { close(); out.push(`<h${Math.min(6, m[1].length + 2)}>${inline(m[2])}</h${Math.min(6, m[1].length + 2)}>`); }
    else if (b || n) { if (inTable) { out.push("</table>"); inTable = false; } if (!inList) { out.push("<ul>"); inList = true; } out.push(`<li>${n ? n[1] + ". " : ""}${inline((b || n)[b ? 1 : 2])}</li>`); }
    else if (!l.trim()) { close(); }
    else if (/^&gt;\s?/.test(l)) { close(); out.push(`<blockquote>${inline(l.replace(/^&gt;\s?/, ""))}</blockquote>`); }
    else { close(); out.push(`<p>${inline(l)}</p>`); }
  }
  close();
  return out.join("");
}
function previewLinks(tid, files, key) {
  if (!files || !files.length) return "";
  const set = new Set(files);
  return `<div class="files pvfiles">` + files.map((f, i) => {
    const u = `/api/file?task=${encodeURIComponent(tid)}&path=${encodeURIComponent(f)}`;
    if (/\.docx$/i.test(f) && set.has(f.replace(/\.docx$/i, ".md"))) return "";   // 같은 내용의 .md 쪽에 미리보기 버튼이 있어요
    const isDoc = /\.docx$/i.test(f);                        // 워드는 사무실이 함께 만든 같은 이름의 .md 로 미리보기
    const pf = isDoc ? f.replace(/\.docx$/i, ".md") : f;
    const pu = `/api/file?task=${encodeURIComponent(tid)}&path=${encodeURIComponent(pf)}`;
    const can = /\.(md|txt|png|jpe?g|gif|webp|pdf|mp4|webm|html?)$/i.test(pf);
    const dx = isDoc ? f : (/\.md$/i.test(f) && set.has(f.replace(/\.md$/i, ".docx")) ? f.replace(/\.md$/i, ".docx") : "");
    return `<span class="pvrow">${can ? `<button class="btn sec sm" data-pv="${esc(key)}-${i}" data-u="${esc(pu)}" data-f="${esc(pf)}">👁 ${esc(f)}</button>` : `<span>${esc(f)}</span>`}` +
      `${isDoc ? "" : `<a href="${u}&dl=1" title="내려받기">⬇</a>`}${dx ? `<a href="/api/file?task=${encodeURIComponent(tid)}&path=${encodeURIComponent(dx)}&dl=1" title="워드로 받기">📝</a>` : ""}</span>` +
      `<div class="pv" id="pv-${esc(key)}-${i}" hidden></div>`;
  }).join("") + `</div>`;
}
async function togglePreview(btn) {
  const box = document.getElementById("pv-" + btn.dataset.pv);
  if (!box) return;
  if (!box.hidden) { box.hidden = true; return; }
  box.hidden = false;
  const u = btn.dataset.u, f = btn.dataset.f;
  if (/\.(png|jpe?g|gif|webp)$/i.test(f)) box.innerHTML = `<img src="${u}" alt="">`;
  else if (/\.pdf$/i.test(f)) box.innerHTML = `<iframe src="${u}" title="PDF 미리보기"></iframe>`;
  else if (/\.(mp4|webm)$/i.test(f)) box.innerHTML = `<video src="${u}" controls preload="metadata"></video>`;
  else if (/\.html?$/i.test(f)) box.innerHTML = `<iframe class="pvhtml" src="${u}" sandbox="allow-scripts" title="미리보기"></iframe><p class="muted"><a href="${u}" target="_blank" rel="noopener">새 창에서 크게 보기 ↗</a></p>`;
  else {
    box.innerHTML = `<p class="muted">불러오는 중…</p>`;
    try { const t = await (await fetch(u)).text(); box.innerHTML = `<div class="pvmd">${/\.md$/i.test(f) ? mdToHtml(t) : `<pre>${esc(t)}</pre>`}</div>`; }
    catch (e) { box.innerHTML = `<div class="err">미리보기를 못 불러왔어요: ${esc(e.message)}</div>`; }
  }
}
document.addEventListener("click", e => { const b = e.target.closest("[data-pv]"); if (b) { e.preventDefault(); togglePreview(b); } });

/* 결과물 상자: 저장 폴더 + 꼭 볼 파일 + 나머지 파일 */
function outBox(tid, o) {
  if (!o || !o.folder || !(o.files || []).length) return "";
  const main = o.main || [];
  const here = !state || state.here !== false;   // 사무실 맥에서 직접 연 화면
  const rest = (o.files || []).filter(f => !main.includes(f));
  return `<div class="card outbox"><h4>📦 결과물</h4>` +
    (here ? `<div class="opath"><span>📂</span><code>${esc(o.folder)}</code></div>` : "") +
    `<div class="oact">` +
    (o.final ? `<a class="btn" href="/api/zip?task=${encodeURIComponent(tid)}&which=final">⬇ 최종본 한꺼번에 받기</a>` : "") +
    (main.length ? `<a class="btn sec" href="/api/zip?task=${encodeURIComponent(tid)}&which=main">⬇ 꼭 볼 파일 한꺼번에 받기</a>` : "") +
    (here ? `<button class="btn sec" data-reveal="${esc(tid)}">📂 폴더 열기</button>` +
      `<button class="btn sec" data-copy="${esc(o.folder)}">📋 위치 복사</button>` +
      (o.final ? `<button class="btn sec" data-revealfinal="${esc(tid)}">⭐ 최종본 폴더 열기</button>` : "") : "") + `</div>` +
    (o.final ? (here ? `<div class="opath"><span>⭐</span><code>${esc(o.final)}</code></div>` : "") +
      (o.final_host ? `<p class="muted">💻 ${esc(o.final_host)}에서 만들었어요 · 그 컴퓨터의 최종본 폴더와 구글 드라이브(‘컴퓨터’ 칸)에 함께 있어요</p>` : "") : "") +
    (main.length ? `<div class="omain">${fileLinks(tid, main)}</div>` : "") +
    (rest.length ? `<details><summary>작업용 파일 ${rest.length}개 더 보기</summary>${fileLinks(tid, rest)}</details>` : "") +
    (here ? `<p class="muted">「폴더 열기」는 사무실 맥에서 폴더 창을 열어요. 「한꺼번에 받기」는 zip 하나로 이 기기에 내려받아요.</p>`
          : `<p class="muted">「한꺼번에 받기」는 zip 하나로 이 기기에 내려받아요. 최종본은 구글 드라이브의 ‘컴퓨터 › 맥북 › 최종본_final’에도 있어요.</p>`) + `</div>`;
}
document.addEventListener("click", async e => {
  const r = e.target.closest("[data-reveal]");
  if (r) {
    r.disabled = true;
    try { await api(`/api/task/${encodeURIComponent(r.dataset.reveal)}/reveal`, {}); r.textContent = "✅ 맥에서 열었어요"; }
    catch (err) { alert(err.message); }
    setTimeout(() => { r.disabled = false; r.textContent = "📂 폴더 열기"; }, 2500);
    return;
  }
  const rf = e.target.closest("[data-revealfinal]");
  if (rf) {
    rf.disabled = true;
    try { await api(`/api/task/${encodeURIComponent(rf.dataset.revealfinal)}/reveal_final`, {}); rf.textContent = "✅ 맥에서 열었어요"; }
    catch (err) { alert(err.message); }
    setTimeout(() => { rf.disabled = false; rf.textContent = "⭐ 최종본 폴더 열기"; }, 2500);
    return;
  }
  const c = e.target.closest("[data-copy]");
  if (c) {
    const done = () => { c.textContent = "✅ 복사했어요"; setTimeout(() => c.textContent = "📋 위치 복사", 2000); };
    try { await navigator.clipboard.writeText(c.dataset.copy); done(); }
    catch (err) { prompt("이 위치를 복사해 쓰세요", c.dataset.copy); }
  }
});

/* 한 번 더 확인하는 버튼: 누르면 '정말요? [네][아니요]'로 바뀜 */
let confirming = false;
function confirmButton(host, label, question, onYes, cls = "btn warn") {
  const btn = document.createElement("button");
  btn.className = cls; btn.textContent = label;
  host.appendChild(btn);
  btn.onclick = () => {
    confirming = true;
    const box = document.createElement("span");
    box.className = "askbox";
    box.innerHTML = `<b>${esc(question)}</b> `;
    const yes = document.createElement("button"), no = document.createElement("button");
    yes.className = "btn warn"; yes.textContent = "네";
    no.className = "btn sec"; no.textContent = "아니요";
    box.append(yes, no);
    btn.replaceWith(box);
    const done = () => { confirming = false; box.replaceWith(btn); };
    no.onclick = done;
    yes.onclick = async () => { yes.disabled = no.disabled = true; try { await onYes(); } finally { confirming = false; } };
  };
  return btn;
}
const ctlApi = (tid, what) => api(`/api/task/${encodeURIComponent(tid)}/${what}`, {});

/* 시간 기록: 일을 맡기고, 결재하고, 보고하고, 끝난 순서와 시각 */
const TL_ICON = {start: "📝", talk: "💬", ask: "🟡", decide: "✅", ctl: "⏸", workstart: "🔵", workend: "🟢", done: "🏁"};
function timelineHtml(tl) {
  if (!tl || !tl.length) return "";
  return `<details class="tl" open><summary>🕒 시간 기록 (${tl.length})</summary>` +
    tl.map(e => `<div class="tlr"><span class="tlt">${tms(e.ts)}</span><span>${TL_ICON[e.kind] || "•"} ${esc(e.text)}</span></div>`).join("") + `</details>`;
}

/* 📎 파일 붙이기: 버튼 · 끌어다 놓기 · 붙여넣기(⌘V)로 올리고, 올린 파일 번호를 모아 둠 */
function attacher(textarea, host) {
  const st = {ids: [], busy: 0};
  const wrap = document.createElement("div");
  wrap.className = "attach";
  const canDir = "webkitdirectory" in document.createElement("input") && !/iPhone|iPad|Android/i.test(navigator.userAgent);
  wrap.innerHTML = `<label class="btn sec sm attbtn">📎 파일 붙이기<input type="file" multiple hidden class="attfile"></label>` +
    (canDir ? `<label class="btn sec sm attbtn" title="폴더 안의 파일을 폴더 모양 그대로 복사해서 올려요. 원래 폴더는 그대로 있어요.">📁 폴더 올리기<input type="file" webkitdirectory multiple hidden class="attdir"></label>` : "") +
    `<span class="attlist"></span>`;
  host.appendChild(wrap);
  const list = wrap.querySelector(".attlist");
  const upload = async file => {
    const chip = document.createElement("span");
    chip.className = "attchip up"; chip.textContent = `⏳ ${file.name} 올리는 중…`;
    list.appendChild(chip); st.busy++;
    try {
      const r = await fetch("/api/upload", {method: "POST", headers: {"X-Filename": encodeURIComponent(file.name || "붙여넣은그림.png")}, body: file});
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || "올리지 못했어요");
      st.ids.push(d.id);
      chip.className = "attchip";
      chip.innerHTML = `📎 ${esc(d.name)} <button type="button" title="빼기">✕</button>`;
      chip.querySelector("button").onclick = () => { st.ids = st.ids.filter(x => x !== d.id); chip.remove(); };
    } catch (e) {
      chip.className = "attchip bad"; chip.textContent = `⚠ ${file.name}: ${e.message}`;
      setTimeout(() => chip.remove(), 8000);
    } finally { st.busy--; }
  };
  /* 📁 폴더 하나 = 묶음 하나: 파일마다 폴더 안 위치(relpath)를 같이 보내고, 칩은 하나만 보여 줌 */
  const uploadFolder = async (name, items) => {   // items: [{file, rel}]
    items = items.filter(it => !it.rel.split("/").some(x => x.startsWith(".")));   // .DS_Store 같은 숨은 파일 빼기
    const chip = document.createElement("span");
    chip.className = "attchip up";
    list.appendChild(chip);
    if (!items.length) { chip.className = "attchip bad"; chip.textContent = `⚠ ${name}: 빈 폴더예요`; setTimeout(() => chip.remove(), 8000); return; }
    if (items.length > 2000) { chip.className = "attchip bad"; chip.textContent = `⚠ ${name}: 파일이 너무 많아요 (2000개까지)`; setTimeout(() => chip.remove(), 8000); return; }
    const group = String(Date.now()) + [...crypto.getRandomValues(new Uint8Array(4))].map(b => b.toString(16).padStart(2, "0")).join("");
    let done = 0, bad = 0, gid = "";
    const show = () => { chip.textContent = `⏳ 📁 ${name} 올리는 중… ${done}/${items.length}`; };
    show(); st.busy++;
    const one = async it => {
      try {
        const r = await fetch("/api/upload", {method: "POST", body: it.file, headers: {
          "X-Filename": encodeURIComponent(it.file.name), "X-Group": group, "X-Relpath": encodeURIComponent(it.rel)}});
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.error || "올리지 못했어요");
        gid = d.id;
      } catch (e) { bad++; }
      done++; show();
    };
    try {
      const queue = items.slice();
      await Promise.all([1, 2, 3].map(async () => { while (queue.length) await one(queue.shift()); }));
      if (!gid) throw new Error("하나도 올리지 못했어요");
      st.ids.push(gid);
      chip.className = "attchip";
      chip.innerHTML = `📁 ${esc(name)} (파일 ${items.length - bad}개${bad ? `, ⚠ ${bad}개 못 올림` : ""}) <button type="button" title="빼기">✕</button>`;
      chip.querySelector("button").onclick = () => { st.ids = st.ids.filter(x => x !== gid); chip.remove(); };
    } catch (e) {
      chip.className = "attchip bad"; chip.textContent = `⚠ 📁 ${name}: ${e.message}`;
      setTimeout(() => chip.remove(), 8000);
    } finally { st.busy--; }
  };
  /* 끌어다 놓은 폴더 안을 모두 펼쳐서 파일 목록으로 */
  const readEntry = async (entry, prefix, out) => {
    if (entry.isFile) {
      out.push({file: await new Promise((ok, no) => entry.file(ok, no)), rel: prefix + entry.name});
    } else if (entry.isDirectory) {
      const rd = entry.createReader();
      for (;;) {
        const batch = await new Promise((ok, no) => rd.readEntries(ok, no));
        if (!batch.length) break;
        for (const ch of batch) await readEntry(ch, prefix + entry.name + "/", out);
      }
    }
  };
  const addFiles = fl => [...(fl || [])].forEach(upload);
  wrap.querySelector(".attfile").onchange = e => { addFiles(e.target.files); e.target.value = ""; };
  const dirIn = wrap.querySelector(".attdir");
  if (dirIn) dirIn.onchange = e => {
    const fs = [...e.target.files]; e.target.value = "";
    if (!fs.length) return;
    const top = (fs[0].webkitRelativePath || fs[0].name).split("/")[0];
    uploadFolder(top, fs.map(f => ({file: f, rel: f.webkitRelativePath || f.name})));
  };
  textarea.addEventListener("dragover", e => { e.preventDefault(); textarea.classList.add("drop"); });
  textarea.addEventListener("dragleave", () => textarea.classList.remove("drop"));
  textarea.addEventListener("drop", async e => {
    e.preventDefault(); textarea.classList.remove("drop");
    const entries = [...(e.dataTransfer.items || [])].map(it => it.webkitGetAsEntry && it.webkitGetAsEntry()).filter(Boolean);
    if (!entries.some(en => en.isDirectory)) return addFiles(e.dataTransfer.files);
    for (const en of entries) {
      if (en.isFile) { en.file(f => upload(f)); continue; }
      const out = [];
      try { await readEntry(en, "", out); } catch (err) { /* 읽지 못한 파일은 건너뜀 */ }
      uploadFolder(en.name, out);
    }
  });
  textarea.addEventListener("paste", e => {
    const f = [...((e.clipboardData && e.clipboardData.files) || [])];
    if (f.length) { e.preventDefault(); addFiles(f); }
  });
  st.clear = () => { st.ids = []; list.innerHTML = ""; };
  st.has = () => st.ids.length > 0 || st.busy > 0;
  return st;
}
/* 컴퓨터에서는 Enter 로 바로 보내기 (줄바꿈은 Shift+Enter). 한글 입력 중(조합 중)에는 보내지 않음. 휴대폰은 Enter 가 줄바꿈 */
const ENTER_SENDS = window.matchMedia && matchMedia("(pointer: fine)").matches;
function enterSends(box, fn) {
  if (!box || !ENTER_SENDS) return;
  box.placeholder = (box.placeholder || "") + "  — Enter 보내기 · Shift+Enter 줄바꿈";
  box.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); fn(); }
  });
}
const UPLOADING = "파일을 올리는 중이에요. ⏳ 표시가 📎 로 바뀐 뒤에 보내 주세요.";

/* 새 일 / 일 목록 */
function openNewTask(pid) {
  const ps = (state && state.projects) || [];
  openModal("기획실장에게 새 일 맡기기",
    `<p class="muted">평소 말투로 편하게 적어 주세요. 빠진 정보는 기획실장이 되물어요.</p>
     <label class="muted">프로젝트 <select id="ntProj"><option value="">기획실장이 알아서 정하기</option>` +
     ps.map(p => `<option value="${esc(p.id)}"${p.id === pid ? " selected" : ""}>${esc(p.name)}</option>`).join("") + `</select></label>
     <textarea id="ntText" rows="5" placeholder="예) ○○ 강의 1차시 영상 대본을 만들어 줘  (파일·폴더는 여기로 끌어다 놓거나 ⌘V 로 붙여 넣어도 돼요)"></textarea><div id="ntAtt"></div>
     <label class="cmpchk"><input type="checkbox" id="ntCmp"> ⚖ Claude 와 GPT 에게 같이 맡겨서 비교하기 <span class="muted">— 두 답을 동시에 만들고, 검토 담당이 비교표와 합본을 만들어요. 어느 것을 쓸지는 교수님이 골라요. (GPT 는 Chrome 의 ChatGPT 로)</span></label>
     <button class="btn" id="ntGo">맡기기</button> <button class="btn sec" id="ntList">진행 중인 일 보기</button><div id="ntErr"></div>`);
  $("#ntList").onclick = openTaskList;
  const att = attacher($("#ntText"), $("#ntAtt"));
  const send = async force => {
    const text = $("#ntText").value.trim();
    if (!text) { $("#ntErr").innerHTML = `<div class="err">일 내용을 적어 주세요.</div>`; return; }
    if (att.busy) { $("#ntErr").innerHTML = `<div class="err">${UPLOADING}</div>`; return; }
    $("#ntGo").disabled = true;
    try { const t = await api("/api/task", {text, project: $("#ntProj").value, compare: $("#ntCmp").checked, force, attachments: att.ids}); refresh(); openTask(t.id); }
    catch (e) {
      $("#ntGo").disabled = false;
      if (e.data && e.data.duplicate) return showDuplicate(e.data.duplicate, text, () => send(true), att.ids);
      $("#ntErr").innerHTML = `<div class="err">${esc(e.message)}</div>`;
    }
  };
  $("#ntGo").onclick = () => send(false);
  enterSends($("#ntText"), () => { if (!$("#ntGo").disabled && !$("#ntGo").hidden) send(false); });
  $("#ntText").addEventListener("input", () => { if ($("#ntGo").hidden) { $("#ntGo").hidden = false; $("#ntErr").innerHTML = ""; } });
  setTimeout(() => $("#ntText")?.focus(), 50);
}

/* 🗣 회의 소집: 고른 직원들이 3층 회의실 2 에 모이고 교수님도 연결문으로 들어가요. 직원마다 의견을 내고 기획실장이 회의록·할 일을 정리 */
function openMeeting() {
  const emps = ((state && state.employees) || []).filter(e => e.active && e.name !== "기획실장");
  openModal("🗣 회의 소집",
    `<p class="muted">회의 주제를 적고 부를 직원을 고르세요. 직원들이 3층 회의실 2 에 모이고, 직원마다 자기 일 쪽에서 의견을 낸 뒤 기획실장이 회의록과 할 일을 정리해 보고함에 올려요. 크레딧은 들지 않아요.</p>
     <textarea id="mtText" rows="3" placeholder="예) ○○ 강의 2~6장 영상은 어떤 순서로 만들까?"></textarea>
     <div class="mtpick">${emps.map(e => `<label><input type="checkbox" value="${esc(e.name)}"> ${esc(e.name)}</label>`).join("")}</div>
     <button class="btn" id="mtGo">회의 시작</button><div id="mtErr"></div>`);
  const go = async () => {
    const t = $("#mtText").value.trim();
    const who = [...document.querySelectorAll(".mtpick input:checked")].map(x => x.value);
    if (!t) { $("#mtErr").innerHTML = `<div class="err">회의 주제를 적어 주세요.</div>`; return; }
    if (!who.length) { $("#mtErr").innerHTML = `<div class="err">부를 직원을 한 명 이상 골라 주세요.</div>`; return; }
    $("#mtGo").disabled = true;
    try {
      await api("/api/task", {text: `🗣 대표 회의: ${t}\n참석: ${who.join(", ")}`, force: true});
      refresh(); showFloor(PLACE_FLOOR);
      $("#mbody").innerHTML = `<p>✅ 회의를 열었어요. ${esc(who.join(", "))} 직원이 3층 회의실 2 로 모여요. 회의록은 보고함에 올라와요.</p>`;
    } catch (e) { $("#mtGo").disabled = false; $("#mtErr").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  };
  $("#mtGo").onclick = go;
  enterSends($("#mtText"), go);
  setTimeout(() => $("#mtText")?.focus(), 50);
}

/* 💡 아이디어 맡기기: 휴대폰에서 떠오른 생각을 한 줄로 던지면 아이디어 직원이 정리해서 보고함에 올려요 (크레딧 없음) */
function openIdea() {
  openModal("💡 아이디어 맡기기",
    `<p class="muted">떠오른 생각을 한두 줄로 편하게 적어 주세요. 아이디어 직원이 쓸 만한 방향 몇 가지로 정리해서 보고함에 올려요. 크레딧은 들지 않아요.</p>
     <textarea id="idText" rows="4" placeholder="예) 수업에서 학생들이 직접 그림책을 만들어 보면 어떨까?"></textarea><div id="idAtt"></div>
     <button class="btn" id="idGo">맡기기</button><div id="idErr"></div>`);
  const att = attacher($("#idText"), $("#idAtt"));
  enterSends($("#idText"), () => $("#idGo") && !$("#idGo").disabled && $("#idGo").click());
  $("#idGo").onclick = async () => {
    const t = $("#idText").value.trim();
    if (!t) { $("#idErr").innerHTML = `<div class="err">생각을 적어 주세요.</div>`; return; }
    if (att.busy) { $("#idErr").innerHTML = `<div class="err">${UPLOADING}</div>`; return; }
    $("#idGo").disabled = true;
    try {
      await api("/api/task", {text: "💡 아이디어 메모: " + t, force: true, attachments: att.ids});
      refresh();
      $("#mbody").innerHTML = `<p>✅ 맡겼어요. 아이디어 직원이 정리하면 <b>보고함</b>에 올라와요.</p><button class="btn" id="idMore">하나 더 맡기기</button>`;
      $("#idMore").onclick = openIdea;
    } catch (e) { $("#idGo").disabled = false; $("#idErr").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  };
  setTimeout(() => $("#idText")?.focus(), 50);
}

/* 비슷한 일을 이미 맡겼을 때: 새로 시작하지 않고 먼저 알려 드려요 */
function showDuplicate(d, text, forceNew, attachments) {
  const head = d.active ? "🙋 이미 비슷한 일을 하고 있어요" : "🙋 방금 끝낸 비슷한 일이 있어요";
  $("#ntErr").innerHTML =
    `<div class="dupbox"><b class="duph">${head}</b>` +
    `<div class="dupt"><b>${esc(d.title)}</b> <span class="pill">${esc(TASK_LABEL[d.status] || d.status)}</span></div>` +
    `<div class="muted">맡긴 때 ${tm(d.created)}${d.finished ? ` · 끝난 때 ${tm(d.finished)}` : ""}` +
    `${d.total ? ` · ${d.done} / ${d.total} 단계 끝남` : ""}${d.current ? ` · 지금: ${esc(d.current)}` : ""}</div>` +
    `<div class="dupq">그때 하신 말씀: “${esc(d.request)}”</div>` +
    `<div class="muted">새로 맡기면 같은 일을 처음부터 또 하게 돼요. 어떻게 할까요?</div>` +
    `<div class="dupact"><button class="btn" id="dupOpen">그 일 보기</button>` +
    (d.canAdd ? `<button class="btn ok" id="dupAdd">그 일에 이 말 덧붙이기</button>` : "") +
    `<button class="btn sec" id="dupNew">그래도 새로 맡기기</button></div></div>`;
  $("#dupOpen").onclick = () => openTask(d.id);
  $("#dupNew").onclick = forceNew;
  $("#dupAdd")?.addEventListener("click", async () => {
    $("#dupAdd").disabled = true;
    try { await api(`/api/task/${encodeURIComponent(d.id)}/message`, {text, attachments}); refresh(); openTask(d.id); }
    catch (e) { $("#dupAdd").disabled = false; alert(e.message); }
  });
  $("#ntGo").hidden = true;
}

const TASK_LABEL = {planning: "계획 세우는 중", asking: "교수님 답변 기다림", plan_review: "계획서 결재 대기", running: "직원들이 일하는 중", review: "최종 보고 결재 대기", done: "끝남", error: "멈춤(오류)", paused: "교수님이 멈춤", cancelled: "그만둠"};
async function openTaskList() {
  const s = await api("/api/state");
  const list = s.tasks.slice().reverse();
  openModal("기획실 — 일 목록",
    (list.length ? list.map(t => `<div class="card"><h4>${esc(t.title)}</h4><span class="pill">${TASK_LABEL[t.status] || t.status}</span> <span class="muted">맡긴 때 ${tm(t.created)}</span><br><button class="btn sec" data-t="${esc(t.id)}">열기</button></div>`).join("") : `<p class="muted">아직 맡긴 일이 없어요.</p>`) +
    `<button class="btn" id="nt2">+ 새 일 맡기기</button>`);
  $("#nt2").onclick = () => openNewTask();
  document.querySelectorAll("[data-t]").forEach(b => b.onclick = () => openTask(b.dataset.t));
}

async function openTask(tid) {
  let last = "", first = true;
  const draw = async () => {
    let t;
    try { t = await api("/api/task/" + encodeURIComponent(tid)); } catch (e) { return; }
    if ($("#overlay").hidden || $("#mtitle").textContent !== "일 대화창" || !$("#tkView")) return;
    const canReply = t.status !== "cancelled";
    $("#tkSend").hidden = !canReply;
    $("#tkText").placeholder = !canReply ? "그만둔 일이에요. 지금까지 어떻게 됐는지만 물어볼 수 있어요"
      : t.status === "running" ? "직원들에게 추가로 전할 말이나 파일을 남겨 두세요 (다음 단계부터 읽어요). 상황이 궁금하면 「물어보기」"
      : t.status === "planning" ? "기획실장이 생각하는 중이에요. 지금 적은 말과 파일도 남겨 두면 함께 읽어요"
      : t.status === "paused" ? "멈춰 있어요. 남긴 말과 파일은 「다시 시작」하면 읽어요"
      : t.status === "done" ? "끝난 일이에요. 이어서 시킬 일을 적으면 기획실장이 같은 폴더에서 이어서 해요"
      : "기획실장에게 답하거나 말하기 (파일은 끌어다 놓거나 ⌘V)";
    renderCtl(t);
    renderNow(t);
    // 바뀐 게 없으면 다시 그리지 않음 (입력 중인 글과 커서를 건드리지 않기 위해)
    const sig = JSON.stringify([t.status, t.messages.length, t.error, (t.timeline || []).length, (t.plan ? t.plan.steps.map(s => s.status) : []), t.files]);
    if (sig === last) return;
    last = sig;
    const mb = $("#mbody");
    const stick = first || mb.scrollHeight - mb.scrollTop - mb.clientHeight < 80;   // 맨 아래를 보고 있을 때만 따라 내려감
    first = false;
    const steps = t.plan ? `<div class="card"><h4>계획</h4><div class="detail">${esc(t.plan.summary)}</div>` +
      t.plan.steps.map((s, i) => `<div>${i + 1}. <b>${esc(s.employee)}</b> — ${esc(s.title)} ` +
        (s.engine && s.engine !== "code" ? `<span class="eng ${s.engine === "gpt" ? "e-gpt" : s.engine === "claude_project" ? "e-cp" : "e-cl"}">${esc(ENGINE_NAME[s.engine] || s.engine)}${s.claudeProjectName ? ": " + esc(s.claudeProjectName) : ""}</span> ` : "") + `<span class="pill">${{pending: "대기", running: "작업 중", waiting: "결재 대기", rendering: "영상 만드는 중", composing: "합치는 중", done: "완료", error: "오류"}[s.status] || s.status}</span></div>`).join("") + `</div>` : "";
    const ps = (state && state.projects) || [];
    const curP = ((state && state.tasks) || []).find(x => x.id === tid)?.project || t.project || "";
    $("#tkView").innerHTML =
      `<div><span class="pill">${TASK_LABEL[t.status] || t.status}</span> <label class="muted">프로젝트 <select id="tkProj">` +
      ps.map(p => `<option value="${esc(p.id)}"${p.id === curP ? " selected" : ""}>${esc(p.name)}</option>`).join("") + `</select></label></div>` +
      t.messages.map(m => `<div class="msg ${m.role}${m.kind === "status" ? " st" : ""}">${esc(m.text)}<div class="mt">${m.kind === "status" ? (m.role === "user" ? "상황 질문 · " : "기획실장 (상황 답변) · ") : m.role === "user" ? (m.note ? "추가로 남긴 말 · " : "교수님 · ") : "기획실장 · "}${tm(m.ts)}</div></div>`).join("") +
      (t.status === "planning" ? `<p class="muted">기획실장이 생각하는 중이에요…</p>` : "") +
      (t.error ? `<div class="err">${esc(t.error)}</div><button class="btn warn" id="tkRetry">다시 시도</button>` : "") +
      steps + (t.compare ? `<button class="btn" id="tkCmp">⚖ Claude · GPT 나란히 보기</button>` +
        (t.pick ? ` <span class="pill">교수님 선택: ${esc(t.pick.label)}</span>` : "") : "") + outBox(tid, t) +
      (t.status === "plan_review" || t.status === "review" ? `<p class="muted">결재는 상단 현황판의 「결재 대기」에서 할 수 있어요.</p>` : "") +
      (t.status === "running" ? `<p class="muted">직원들이 일하는 중이에요. 아래에 적은 말은 다음 단계부터 직원들이 읽어요.</p>` : "") +
      timelineHtml(t.timeline);
    if (stick) mb.scrollTop = mb.scrollHeight;
  };
  // 멈추기 · 다시 시작 · 그만두기 버튼 (상태가 바뀔 때만 다시 그림, 확인 중에는 건드리지 않음)
  let ctlSig = "";
  const renderCtl = t => {
    if (confirming) return;
    const sig = t.status + (t.now && t.now.workers.some(w => w.text.startsWith("헤이젠")) ? "R" : "");
    if (sig === ctlSig) return;
    ctlSig = sig;
    const host = $("#tkCtl"); host.innerHTML = "";
    const rendering = t.now && t.now.workers.some(w => w.text.startsWith("헤이젠"));
    const after = async () => { ctlSig = ""; last = ""; refresh(); await draw(); };
    if (["planning", "running"].includes(t.status)) {
      confirmButton(host, "⏸ 멈추기", "하던 작업을 지금 끊을까요?" + (rendering ? " (헤이젠에 맡긴 영상은 멈출 수 없어요)" : ""),
        async () => { await ctlApi(tid, "stop"); await after(); });
    }
    const allDone = t.plan && t.plan.steps.length && t.plan.steps.every(s => s.status === "done");
    if (t.status === "paused" && allDone) {
      const fin = document.createElement("button"); fin.className = "btn ok"; fin.textContent = "✅ 끝난 일로 정리";
      fin.title = "단계는 다 끝났어요. 결과물은 그대로 두고 '끝남'으로 옮겨요";
      fin.onclick = async () => { fin.disabled = true; try { await ctlApi(tid, "finish"); } catch (e) { alert(e.message); } await after(); };
      host.appendChild(fin);
    } else if (t.status === "paused") {
      const go = document.createElement("button"); go.className = "btn ok"; go.textContent = "▶ 다시 시작";
      go.onclick = async () => { go.disabled = true; await ctlApi(tid, "resume"); await after(); };
      host.appendChild(go);
    }
    if (!["done", "cancelled"].includes(t.status)) {
      confirmButton(host, "🛑 그만두기", "이 일을 완전히 그만둘까요? (되돌릴 수 없어요)", async () => {
        const r = await ctlApi(tid, "cancel");
        if (r.note) $("#tkErr").innerHTML = `<div class="err">${esc(r.note)}</div>`;
        await after();
      }, "btn sec");
    }
  };
  // 지금 상황: 일하는 직원, 방금 한 일, 줄 선 일, 최근 파일 (비용 없이 바로 보임)
  const renderNow = t => {
    const n = t.now;
    if (!n || ["done", "cancelled"].includes(t.status)) { $("#tkNow").innerHTML = ""; return; }
    const fmt = sec => sec >= 60 ? `${Math.floor(sec / 60)}분 ${sec % 60}초` : `${sec}초`;
    let h = `<div class="nowbox"><b>🔎 지금 상황</b>`;
    if (n.workers.length) {
      h += n.workers.map(w => `<div class="nw"><b>${esc(w.name)}</b> · ${esc(w.step)} · ${fmt(w.seconds)} 째` +
        (w.recent.length ? `<div class="muted">방금 한 일: ` + w.recent.map(r => `${esc(r.t)} ${esc(r.text)}`).join(" ← ") + `</div>` : "") + `</div>`).join("");
    } else {
      h += `<div class="muted">${t.status === "paused" ? "멈춰 있어요. 「다시 시작」을 누르면 이어서 해요." : "지금 이 일을 하는 직원은 없어요."}</div>`;
    }
    if (n.queued) h += `<div class="muted">차례를 기다리는 작업 ${n.queued}건</div>`;
    if (n.files.length) h += `<div class="muted">최근 만든 파일: ${n.files.map(f => `${esc(f.t)} ${esc(f.name)}`).join(" · ")}</div>`;
    $("#tkNow").innerHTML = h + `</div>`;
  };
  const askStatus = async () => {
    const box = $("#tkText"), q = box.value.trim();
    if (!q) { $("#tkErr").innerHTML = `<div class="err">물어볼 내용을 적어 주세요. 예) 지금 어디까지 됐어?</div>`; return; }
    $("#tkAsk").disabled = true; $("#tkAsk").textContent = "기획실장이 확인하는 중…";
    try { await api(`/api/task/${tid}/ask`, {question: q}); box.value = ""; $("#tkErr").innerHTML = ""; last = ""; await draw(); }
    catch (e) { $("#tkErr").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
    $("#tkAsk").disabled = false; $("#tkAsk").textContent = "❓ 물어보기";
  };
  let att;
  const send = async () => {
    const box = $("#tkText"), text = box.value.trim();
    if (att.busy) { $("#tkErr").innerHTML = `<div class="err">${UPLOADING}</div>`; return; }
    if (!text && !att.ids.length) { $("#tkErr").innerHTML = `<div class="err">보낼 말을 적거나 파일을 붙여 주세요.</div>`; return; }
    $("#tkSend").disabled = true;
    try {
      await api(`/api/task/${tid}/message`, {text, attachments: att.ids});
      box.value = ""; att.clear(); $("#tkErr").innerHTML = ""; last = "";
      refresh(); await draw();
    } catch (e) { $("#tkErr").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
    $("#tkSend").disabled = false;
  };
  // 입력칸은 한 번만 만들고 다시 만들지 않음
  openModal("일 대화창",
    `<div id="tkCtl" class="ctl"></div><div id="tkNow"></div><div id="tkView">불러오는 중…</div>` +
    `<div id="tkInput"><textarea id="tkText" rows="3" placeholder="기획실장에게 답하거나 말하기"></textarea><div id="tkAtt"></div>` +
    `<button class="btn" id="tkSend">보내기</button> <button class="btn sec" id="tkAsk" title="일은 바뀌지 않고, 지금 어디까지 됐는지만 물어봐요">❓ 물어보기</button> ` +
    `<span class="muted">보내기 = 일에 말 남기기 · 물어보기 = 상황만 묻기 · ⌘ + Enter 로 보내기</span><div id="tkErr"></div></div>`, () => draw());
  att = attacher($("#tkText"), $("#tkAtt"));
  $("#tkSend").onclick = send;
  $("#tkAsk").onclick = askStatus;
  $("#tkText").addEventListener("keydown", e => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && !e.isComposing) { e.preventDefault(); send(); }
  });
  enterSends($("#tkText"), () => { if (!$("#tkSend").hidden && !$("#tkSend").disabled) send(); });
  $("#tkView").addEventListener("change", async e => {
    if (e.target.id === "tkProj") {
      try { await api(`/api/task/${tid}/project`, {project: e.target.value}); refresh(); } catch (err) { $("#tkErr").innerHTML = `<div class="err">${esc(err.message)}</div>`; }
    }
  });
  $("#tkView").addEventListener("click", async e => {
    if (e.target.id === "tkCmp") return openCompare(tid);
    if (e.target.id === "tkRetry") {
      try { await api(`/api/task/${tid}/retry`, {}); last = ""; refresh(); draw(); } catch (err) { $("#tkErr").innerHTML = `<div class="err">${esc(err.message)}</div>`; }
    }
  });
  draw();
}

/* ---------- 프로젝트 현황판 ---------- */
function projStrip(ps) {
  const live = ps.filter(p => p.moving).length;
  return `<div class="pstrip"><button class="phd" id="projOpen">🗂 프로젝트 현황판 <span>동시 진행 ${live}개</span></button><div class="pchips">` +
    ps.filter(p => p.active || p.id !== "p-etc").map(p =>
      `<button class="pchip${p.working ? " on" : ""}" data-pj="${esc(p.id)}" style="--pc:${p.color}"><i></i><span class="pn">${esc(p.name)}</span>` +
      `<small>${p.moving ? `진행 ${p.moving}` : p.waiting ? "멈춤·대기" : "쉬는 중"}${p.waiting && p.moving ? ` · 멈춤 ${p.waiting}` : ""}${p.approvals ? ` · 결재 ${p.approvals}` : ""}</small></button>`).join("") + `</div></div>`;
}
const ENGINE_CLS = {Claude: "e-cl", "Claude 채팅": "e-cl", "Claude 프로젝트": "e-cp", GPT: "e-gpt", HeyGen: "e-hg"};
const ENGINE_NAME = {code: "Claude Code", chat: "Claude 채팅", gpt: "ChatGPT", claude_project: "Claude 프로젝트"};
let CP = {projects: [], synced: ""};
async function openProjects(focus) {
  let sig = "";
  const draw = async () => {
    let ps;
    try { ps = await api("/api/projects"); CP = await api("/api/claude_projects"); } catch (e) { return; }
    if ($("#overlay").hidden || !$("#pjView")) return;
    if ($("#pjView").contains(document.activeElement) && document.activeElement.tagName === "SELECT") return;   // 고르는 중엔 다시 그리지 않음
    const ns = JSON.stringify([ps, CP.synced, CP.syncing]);
    if (ns === sig) return;
    sig = ns;
    const live = ps.filter(p => p.moving);
    const card = r => {
      const pct = r.total ? Math.round(r.done / r.total * 100) : 0;
      return `<div class="pjt" data-t="${esc(r.id)}"><div class="pjt1"><b>${esc(r.title)}</b>` + (r.compare ? `<span class="eng e-cmp">비교</span>` : "") +
        r.engines.map(x => `<span class="eng ${ENGINE_CLS[x] || ""}">${esc(x)}</span>`).join("") + `</div>` +
        `<span class="pill">${TASK_LABEL[r.status] || r.status}</span>` + (r.total ? `<span class="muted">${r.done}/${r.total} 단계</span>` : "") +
        (r.total ? `<div class="bar"><i style="width:${pct}%"></i></div>` : "") +
        (r.current ? `<div class="muted">다음: ${esc(r.current)}</div>` : "") +
        r.workers.map(w => `<div class="pjw">🔵 <b>${esc(w.name)}</b> ${esc(w.step)}</div>`).join("") +
        r.approvals.map(a => `<div class="pja">🟡 결재 대기: ${esc(a)}</div>`).join("") + `</div>`;
    };
    $("#pjView").innerHTML =
      `<p class="muted">지금 <b>${live.length}개</b> 프로젝트가 동시에 돌아가고 있어요. 멈춰 있거나 교수님 답을 기다리는 일도 함께 보여요. 카드를 누르면 그 일의 대화창이 열려요.</p><div class="pjgrid">` +
      ps.map(p => `<section class="pj${p.id === focus ? " focus" : ""}" id="pj-${esc(p.id)}" style="--pc:${p.color}">` +
        `<h4>${esc(p.name)}</h4><div class="muted">${esc(p.goal)}</div>` +
        (p.folder ? `<div class="pjf">📁 ${esc(p.folder.replace(/^\/Users\/[^/]+\//, "~/"))}</div>` : "") +
        `<div class="pjs">진행 ${p.moving} · 멈춤·대기 ${p.waiting} · 결재 ${p.approvals} · 끝남 ${p.doneCount}</div>` +
        `<label class="pjcp">💬 claude.ai 프로젝트 <select data-cp="${esc(p.id)}"><option value="">연결 안 함</option>` +
        (CP.projects || []).map(c => `<option value="${esc(c.url)}"${c.url === p.claudeProject ? " selected" : ""}>${esc(c.name)}</option>`).join("") + `</select></label>` +
        `<div class="pjh">진행 중·멈춘 일</div>` + (p.active.length ? p.active.map(card).join("") : `<div class="muted">없어요</div>`) +
        `<div class="pjh">최근 끝난 일</div>` + (p.done.length ? p.done.map(r =>
          `<div class="pjd" data-t="${esc(r.id)}">✅ ${esc(r.title)} <span class="muted">${tm(r.finished || r.created)}</span>` + (r.pick ? `<span class="muted"> · 고른 것: ${esc(r.pick)}</span>` : "") +
          r.engines.map(x => `<span class="eng ${ENGINE_CLS[x] || ""}">${esc(x)}</span>`).join("") + `</div>`).join("") : `<div class="muted">아직 없어요</div>`) +
        `<button class="btn sec sm pjnew" data-np="${esc(p.id)}">+ 이 프로젝트에 새 일</button></section>`).join("") + `</div>` +
      `<div class="pjcpbar muted">claude.ai 프로젝트 ${(CP.projects || []).length}개 불러옴${CP.synced ? " (" + tm(CP.synced) + ")" : ""} ` +
      `<button class="btn sec sm" id="cpSync"${CP.syncing ? " disabled" : ""}>${CP.syncing ? "불러오는 중… (1~2분)" : "↻ 목록 새로 불러오기"}</button> — 연결해 두면 기획실장이 그 프로젝트 안에서 이어서 일해요.</div>` +
      `<details class="pjadd"><summary>+ 새 프로젝트 만들기</summary>` +
      `<input type="text" id="paName" placeholder="이름 (예: 2학기 교양 강의)"><input type="text" id="paGoal" placeholder="목표 (한 줄)">` +
      `<input type="text" id="paFolder" placeholder="결과물 폴더 (없으면 비워 두기)"><input type="text" id="paKw" placeholder="알아볼 낱말, 쉼표로 (예: 교양, 2학기) — 새 일에 이 낱말이 있으면 이 프로젝트로 묶어요">` +
      `<button class="btn" id="paGo">만들기</button><div id="paErr"></div></details>`;
    if (focus) { document.getElementById("pj-" + focus)?.scrollIntoView({block: "nearest"}); focus = null; }
  };
  openModal("프로젝트 현황판", `<div id="pjView">불러오는 중…</div>`, () => draw());
  $("#pjView").addEventListener("change", async e => {
    const sel = e.target.closest("[data-cp]");
    if (!sel) return;
    try { await api(`/api/project/${sel.dataset.cp}/claude`, {url: sel.value}); sig = ""; sel.blur(); draw(); } catch (err) { alert(err.message); }
  });
  $(".modal").classList.add("wide");
  $("#pjView").addEventListener("click", async e => {
    const t = e.target.closest("[data-t]"), np = e.target.closest("[data-np]");
    if (np) return openNewTask(np.dataset.np);
    if (t) return openTask(t.dataset.t);
    if (e.target.id === "cpSync") {
      e.target.disabled = true; e.target.textContent = "불러오는 중… (1~2분)";
      try { await api("/api/claude_projects/sync", {}); } catch (err) { alert(err.message); }
      return;
    }
    if (e.target.id === "paGo") {
      try {
        await api("/api/project", {name: $("#paName").value, goal: $("#paGoal").value, folder: $("#paFolder").value, keywords: $("#paKw").value});
        sig = ""; refresh(); draw();
      } catch (err) { $("#paErr").innerHTML = `<div class="err">${esc(err.message)}</div>`; }
    }
  });
  draw();
}

/* ---------- ⚖ Claude · GPT 나란히 보기 ---------- */
async function openCompare(tid) {
  let sig = "";
  const draw = async () => {
    let c;
    try { c = await api("/api/compare/" + encodeURIComponent(tid)); } catch (e) { if ($("#cmpView")) $("#cmpView").innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
    if ($("#overlay").hidden || !$("#cmpView")) return;
    const ns = JSON.stringify([c.status, c.a.length, c.b.length, c.report.length, c.merged.length, c.approval, c.pick]);
    if (ns === sig) return;
    sig = ns;
    const REC = {A: "Claude 답안", B: "GPT 답안", "합본": "합본"};
    const col = (title, cls, body, wait) => `<section class="cmpcol ${cls}"><h4>${title} <small class="muted">${body ? body.length.toLocaleString() + "자" : ""}</small></h4>` +
      `<div class="cmptxt">${body ? esc(body) : `<span class="muted">${wait}</span>`}</div></section>`;
    const pickBtns = c.approval ? `<div class="cmppick"><b>어느 것을 쓸까요?</b> ` +
      `<button class="btn ok" data-c="A">Claude 답안</button><button class="btn ok" data-c="B">GPT 답안</button><button class="btn ok" data-c="M">합본</button>` +
      ` <span class="muted">고른 것은 '최종_final.md'(워드도 함께)로 저장돼요. 합본을 고치려면 결재함의 「합본 고쳐 줘」</span></div>` : "";
    const st = c.stats || {};
    $("#cmpView").innerHTML =
      `<div class="muted">맡긴 일: ${esc(c.request.slice(0, 200))}${c.request.length > 200 ? "…" : ""}</div>` +
      (c.pick ? `<div class="cmpdone">✅ 교수님 선택: <b>${esc(c.pick.label)}</b> (${tm(c.pick.ts)}) — 최종_final.md 로 저장했어요</div>` : "") +
      (c.recommend ? `<div class="cmprec">검토 담당 추천: <b>${esc(REC[c.recommend] || c.recommend)}</b></div>` : "") +
      pickBtns +
      `<div class="cmpgrid">` + col("🟠 Claude 답안 (A)", "ca", c.a, "Claude 가 쓰는 중이에요…") + col("🟢 GPT 답안 (B)", "cb", c.b, "ChatGPT 에서 받아 오는 중이에요…") + `</div>` +
      `<details class="cmpdet"${c.report && !c.pick ? " open" : ""}><summary>📋 검토 담당 비교 보고서</summary><div class="cmptxt">${c.report ? esc(c.report) : `<span class="muted">두 답이 다 오면 검토 담당이 비교해요.</span>`}</div></details>` +
      `<details class="cmpdet"><summary>🧩 합본 (두 답의 좋은 부분만)</summary><div class="cmptxt">${c.merged ? esc(c.merged) : `<span class="muted">아직 없어요.</span>`}</div></details>` +
      `<div class="muted">지금까지 교수님이 고른 것: Claude ${st.A || 0} · GPT ${st.B || 0} · 합본 ${st.M || 0}</div>`;
  };
  openModal("⚖ Claude · GPT 나란히 보기", `<div id="cmpView">불러오는 중…</div>`, () => draw());
  $(".modal").classList.add("wide");
  $("#cmpView").addEventListener("click", async e => {
    const b = e.target.closest("[data-c]");
    if (!b) return;
    const c = await api("/api/compare/" + encodeURIComponent(tid));
    if (!c.approval) return;
    b.disabled = true;
    try { await api("/api/approval/" + c.approval, {decision: "approve", choice: b.dataset.c}); sig = ""; refresh(); draw(); }
    catch (err) { alert(err.message); b.disabled = false; }
  });
  draw();
}

/* 결재함 */
async function openApprovals() {
  let lastSig = "", busy = false, atts = {};
  // 글을 쓰는 중이거나, 쓴 글·붙인 파일이 남아 있으면 새로 그리지 않음 (한글 입력이 끊기지 않게)
  const typing = () => [...document.querySelectorAll("#mbody textarea, #mbody input")].some(el => el === document.activeElement || el.value.trim())
    || Object.values(atts).some(a => a.has());
  const draw = async (force) => {
    if ($("#overlay").hidden || $("#mtitle").textContent !== "결재함") return;
    if (force !== true && (busy || typing())) return;
    busy = true;
    let list;
    try { list = await api("/api/approvals"); } catch (e) { return; } finally { busy = false; }
    if (!Array.isArray(list)) return;
    // 목록을 받아 오는 사이(다른 노트북에서는 느릴 수 있음)에 글을 쓰기 시작했으면 지우지 않음
    if ($("#overlay").hidden || $("#mtitle").textContent !== "결재함") return;
    if (force !== true && typing()) return;
    const sig = JSON.stringify(list.map(a => [a.id, a.status]));
    if (sig === lastSig && force !== true) return;
    lastSig = sig;
    atts = {};
    const pending = list.filter(a => a.status === "pending"), done = list.filter(a => a.status !== "pending").slice(0, 8);
    const card = a => {
      const open = a.status === "pending";
      const pill = {pending: "결재 대기", approved: "승인", revised: "수정 요청", answered: "답변함"}[a.status] || a.status;
      let btns = "";
      if (open && a.kind === "question") btns = `<textarea id="r-${a.id}" rows="2" placeholder="답을 적어 주세요 (파일은 끌어다 놓거나 ⌘V)"></textarea><div id="ra-${a.id}"></div><button class="btn" data-answer="${a.taskId}" data-id="${a.id}">답하기</button>`;
      else if (open && a.kind === "pick") btns = `<button class="btn" data-cmp="${a.taskId}">⚖ 나란히 보기</button>` +
        `<button class="btn ok" data-pick="${a.id}" data-c="A">Claude 답안 쓰기</button><button class="btn ok" data-pick="${a.id}" data-c="B">GPT 답안 쓰기</button>` +
        `<button class="btn ok" data-pick="${a.id}" data-c="M">합본 쓰기</button><button class="btn warn" data-rev="${a.id}">합본 고쳐 줘</button><div id="rv-${a.id}"></div>`;
      else if (open) btns = `<button class="btn ok" data-ok="${a.id}">승인</button><button class="btn warn" data-rev="${a.id}">수정 요청</button><div id="rv-${a.id}"></div>`;
      return `<div class="card ${a.kind === "credit" ? "credit" : ""}"><span class="pill ${a.status}">${pill}</span>${a.kind === "credit" ? `<span class="pill warn">💳 크레딧이 들어요</span>` : ""}<b>${esc(a.title)}</b>` +
        `<div class="muted">요청 ${tm(a.created)}${a.decided ? ` · ${pill} ${tm(a.decided)}` : ""}</div>` +
        `<div class="detail">${esc(a.detail)}</div>${a.reason ? `<div class="muted">남긴 말: ${esc(a.reason)}</div>` : ""}${open ? previewLinks(a.taskId, a.files, a.id) + (a.files && a.files.length ? `<p class="muted pvhint">👁 를 누르면 내려받지 않고 여기서 바로 볼 수 있어요. 다시 해야 하면 「수정 요청」에 이유를 적어 주세요.</p>` : "") : fileLinks(a.taskId, a.files)}${btns}</div>`;
    };
    $("#mbody").innerHTML = (pending.length ? pending.map(card).join("") : `<p class="muted">지금 결재할 건이 없어요. 👍</p>`) +
      (done.length ? `<h4 class="muted">지난 결재</h4>` + done.map(card).join("") : "");
    document.querySelectorAll("[data-answer]").forEach(b => { atts["q" + b.dataset.id] = attacher($("#r-" + b.dataset.id), $("#ra-" + b.dataset.id)); });
    document.querySelectorAll("[data-ok]").forEach(b => b.onclick = async () => { b.disabled = true; try { await api("/api/approval/" + b.dataset.ok, {decision: "approve"}); refresh(); draw(true); } catch (e) { alert(e.message); } });
    document.querySelectorAll("[data-cmp]").forEach(b => b.onclick = () => openCompare(b.dataset.cmp));
    document.querySelectorAll("[data-pick]").forEach(b => b.onclick = async () => {
      b.disabled = true;
      try { await api("/api/approval/" + b.dataset.pick, {decision: "approve", choice: b.dataset.c}); refresh(); draw(true); } catch (e) { alert(e.message); b.disabled = false; }
    });
    document.querySelectorAll("[data-rev]").forEach(b => b.onclick = () => {
      const id = b.dataset.rev;
      if ($("#rt-" + id)) return $("#rt-" + id).focus();
      $("#rv-" + id).innerHTML = `<textarea id="rt-${id}" rows="2" placeholder="어디를 어떻게 고쳐야 하는지 이유를 적어 주세요 (참고 파일은 끌어다 놓거나 ⌘V)"></textarea><div id="rta-${id}"></div><button class="btn warn" data-send="${id}">수정 요청 보내기</button><div id="rte-${id}"></div>`;
      const att = atts["r" + id] = attacher($("#rt-" + id), $("#rta-" + id));
      $("#rt-" + id).focus();
      $("#rv-" + id).querySelector("[data-send]").onclick = async e => {
        const reason = $("#rt-" + id).value.trim(), say = m => $("#rte-" + id).innerHTML = `<div class="err">${m}</div>`;
        if (att.busy) return say(UPLOADING);
        if (!reason && !att.ids.length) return say("이유를 적어 주세요.");
        e.target.disabled = true;
        try { await api("/api/approval/" + id, {decision: "revise", reason, attachments: att.ids}); atts = {}; refresh(); draw(true); }
        catch (err) { e.target.disabled = false; say(esc(err.message)); }
      };
    });
    document.querySelectorAll("[data-answer]").forEach(b => b.onclick = async () => {
      const text = $("#r-" + b.dataset.id).value.trim(), att = atts["q" + b.dataset.id];
      if (att.busy) return alert(UPLOADING);
      if (!text && !att.ids.length) return $("#r-" + b.dataset.id).focus();
      b.disabled = true;
      try { await api(`/api/task/${b.dataset.answer}/message`, {text, attachments: att.ids}); atts = {}; refresh(); draw(true); }
      catch (e) { b.disabled = false; alert(e.message); }
    });
  };
  openModal("결재함", "불러오는 중…", () => draw());
  draw(true);
}

/* 작업 중: 누가 어떤 일을 하고 있는지, 멈추기까지 */
async function openWorking() {
  const fmt = sec => sec >= 60 ? `${Math.floor(sec / 60)}분 ${sec % 60}초` : `${sec}초`;
  const STEP = {pending: "차례를 기다림", running: "작업 중", waiting: "결재 기다림", rendering: "영상 만드는 중", composing: "합치는 중", done: "끝남", error: "오류"};
  const draw = async force => {
    if ($("#overlay").hidden || $("#mtitle").textContent !== "작업 중" || !$("#wkBody")) return;
    if (confirming && force !== true) return;      // '정말 멈출까요?' 묻는 중에는 다시 그리지 않음
    let s;
    try { s = await api("/api/state"); } catch (e) { return; }
    const workers = s.employees.filter(e => e.state === "작업 중");
    const cards = workers.map(e =>
      `<div class="card work"><span class="face">${portrait(e.name)}</span><div class="wi"><b>${esc(e.name)}</b> <span class="pill">${esc(e.team)}</span>` +
      `<div class="detail">${esc(e.step || e.text)}</div>` +
      (e.task ? `<div class="muted">일: ${esc(e.task)}</div>` : "") +
      `<div class="muted">지금: ${esc(e.text)}${e.workSeconds != null ? ` · ${fmt(e.workSeconds)} 째` : ""}${e.startedAt ? ` · 시작 ${tm(e.startedAt)}` : ""}</div>` +
      (e.quiet ? `<div class="qwarn">⏳ ${e.quiet}분째 아무 소식이 없어요. 아래 '진행 중인 일'에서 「다시 시키기」를 누르면 이 단계를 처음부터 다시 해요.</div>` : "") +
      (e.recent && e.recent.length ? `<div class="muted">방금 한 일: ${e.recent.map(r => `${esc(r.t)} ${esc(r.text)}`).join(" ← ")}</div>` : "") +
      (e.taskId ? `<button class="btn sec" data-open="${esc(e.taskId)}">일 대화창 열기</button>` : "") + `</div></div>`).join("");
    const taskCard = t => {
      const pct = t.total ? Math.round(100 * t.done / t.total) : 0;
      const paused = t.status === "paused";
      return `<div class="card${paused ? " pausedc" : ""}${t.quiet ? " quietc" : ""}"><b>${esc(t.title)}</b> <span class="pill">${paused ? (t.allDone ? "단계는 다 끝남 · 멈춰 둠" : "멈춰 둠") : TASK_LABEL[t.status] || t.status}</span>` +
        (t.quiet ? `<span class="pill qpill">⏳ ${t.quiet}분째 소식 없음</span>` : "") +
        (paused ? `<span class="muted"> 맡긴 때 ${tm(t.created)}${t.pausedAt ? ` · 멈춘 때 ${tm(t.pausedAt)}` : ""}</span>` : "") +
        (t.total ? `<div class="bar"><i style="width:${pct}%"></i></div><div class="muted">${t.done} / ${t.total} 단계 끝남</div>` +
          t.steps.map((st, i) => `<div>${i + 1}. <b>${esc(st.employee)}</b> — ${esc(st.title)} <span class="pill">${STEP[st.status] || st.status}</span></div>`).join("") : "") +
        `<div class="ctlrow" data-ctl="${esc(t.id)}" data-st="${esc(t.status)}" data-alldone="${t.allDone ? 1 : 0}" data-quiet="${t.quiet || 0}"><button class="btn sec" data-open="${esc(t.id)}">일 대화창 열기</button></div></div>`;
    };
    const act = s.activeTasks || [];
    const moving = act.filter(t => t.status !== "paused"), parked = act.filter(t => t.status === "paused");
    const tasks = moving.map(taskCard).join("") +
      (parked.length ? `<h4>⏸ 멈춰 둔 일 ${parked.length}건</h4><p class="muted">교수님이 멈춘 일이에요. 지금 돌고 있지 않아요. 끝난 일은 「끝난 일로 정리」, 필요 없는 일은 「그만두기」를 누르면 여기서 사라져요.</p>` +
        parked.map(taskCard).join("") : "");
    $("#wkBody").innerHTML =
      (workers.length ? cards : `<p class="muted">지금 일하는 직원이 없어요.${s.counts.approvals ? ` 결재가 필요한 일이 ${s.counts.approvals}건 있어요. 「결재 대기」를 눌러 주세요.` : ""}</p>`) +
      (tasks ? `<h4>진행 중인 일</h4>${tasks}` : "");
    document.querySelectorAll("#wkBody [data-open]").forEach(b => b.onclick = () => openTask(b.dataset.open));
    document.querySelectorAll("#wkBody [data-ctl]").forEach(row => {
      const id = row.dataset.ctl, st = row.dataset.st;
      if (["planning", "running"].includes(st)) {
        if (+row.dataset.quiet) confirmButton(row, "🔄 다시 시키기", "지금 하던 단계를 끊고 처음부터 다시 시킬까요? (끝난 단계는 그대로예요)", async () => {
          try { await ctlApi(id, "restart"); } catch (e) { alert(e.message); } refresh(); await draw(true); }, "btn ok");
        confirmButton(row, "⏸ 멈추기", "이 일의 작업을 지금 끊을까요?", async () => { await ctlApi(id, "stop"); refresh(); await draw(true); });
      } else if (st === "paused" && row.dataset.alldone === "1") {
        const fin = document.createElement("button"); fin.className = "btn ok"; fin.textContent = "✅ 끝난 일로 정리";
        fin.onclick = async () => { fin.disabled = true; try { await ctlApi(id, "finish"); } catch (e) { alert(e.message); } refresh(); await draw(true); };
        row.appendChild(fin);
      } else if (st === "paused") {
        const go = document.createElement("button"); go.className = "btn ok"; go.textContent = "▶ 다시 시작";
        go.onclick = async () => { go.disabled = true; await ctlApi(id, "resume"); refresh(); await draw(true); };
        row.appendChild(go);
        confirmButton(row, "🛑 그만두기", "이 일을 완전히 그만둘까요? (만든 파일은 남아요)", async () => { await ctlApi(id, "cancel"); refresh(); await draw(true); }, "btn sec");
      }
    });
  };
  openModal("작업 중", `<div id="wkTop" class="ctl"></div><div id="wkBody">불러오는 중…</div>`, () => draw());
  confirmButton($("#wkTop"), "⏸ 전부 멈추기", "지금 하는 모든 일을 끊을까요? (하던 작업은 버려져요)", async () => {
    const r = await api("/api/stop-all", {}); refresh(); await draw(true);
  });
  draw(true);
}

/* 보고함 */
async function openReports() {
  const list = await api("/api/reports");
  openModal("보고함", list.length ? list.map(r => `<div class="card"><h4>${esc(r.title)}</h4><div class="muted">맡긴 때 ${tm(r.created)}${r.finished ? ` · 끝난 때 ${tm(r.finished)}${r.took ? ` (걸린 시간 ${esc(r.took)})` : ""}` : ""}</div>${outBox(r.id, r)}</div>`).join("") : `<p class="muted">아직 끝난 일이 없어요.</p>`);
}

/* 직원 프로필: 소개와 작업 이력 */
async function openProfile(name) {
  const p = await api("/api/employee/" + encodeURIComponent(name));
  const color = scene.people[name]?.color || "#999";
  const hist = (p.history || []).map(h =>
    `<div class="card"><b>${esc(h.title)}</b> <span class="pill">${esc(h.status)}</span>` +
    (h.revisions ? `<span class="pill revised">수정 ${h.revisions}번</span>` : "") +
    `<div class="muted">${h.started ? `시작 ${tm(h.started)}${h.finished ? ` → 끝 ${tm(h.finished)}${h.took ? ` (${esc(h.took)})` : ""}` : ""}` : `맡은 때 ${tm(h.date)}`} · ${esc(h.taskTitle)}</div>` +
    (h.summary ? `<div class="detail">${esc(h.summary)}</div>` : "") + fileLinks(h.taskId, h.files) + `</div>`).join("");
  openModal(`${p.name} · ${p.team}`,
    `<div class="phead"><span class="face">${scene ? portrait(name) : ""}</span><div><h3>${esc(p.name)} <span class="pill" style="background:${color};color:#fff">${esc(p.team)}</span><span class="pill">${esc(p.rank)}</span></h3>` +
    `<div class="muted">${esc(p.role)}</div></div></div>` +
    `<div class="intro">${esc(p.intro || "")}</div>` +
    (p.strengths && p.strengths.length ? `<div class="chips">${p.strengths.map(x => `<span>${esc(x)}</span>`).join("")}</div>` : "") +
    `<div class="stat"><div><b>${p.done}</b>끝낸 일</div><div><b>${p.passRate == null ? "-" : p.passRate + "%"}</b>한 번에 통과</div><div><b>${(p.history || []).length}</b>맡은 기록</div></div>` +
    (p.active && p.ranks ? rankHtml(p) : "") +
    (p.active ? "" : `<div class="muted">아직 일을 맡길 수 없는 직원이에요. 다음 단계에서 합류해요.</div>`) +
    `<h4>📏 교수님이 정한 규칙 <small class="muted">이 직원이 일할 때마다 읽고 따라요</small></h4><div id="ruleList"></div>` +
    `<textarea id="ruleNew" rows="2" placeholder="${name === "영상 제작" ? "예) 말하는 속도는 0.95배로 한다 / 영상에 자막을 넣는다 / 배경은 흰색으로 한다" : "새 규칙을 적어 주세요"}"></textarea>` +
    `<button class="btn" id="ruleAdd">+ 규칙 추가</button><div id="ruleErr"></div>` +
    `<h4>작업 이력</h4>` + (hist || `<p class="muted">아직 한 일이 없어요.</p>`) +
    `<details><summary>업무 수첩 보기 (교수님이 알려 주신 규칙)</summary><pre class="nb">${esc(p.notebook || "아직 없어요")}</pre></details>` +
    (name === "기획실장" ? `<button class="btn" id="pNew">+ 새 일 맡기기</button><button class="btn sec" id="pList">일 목록 보기</button>` : "") +
    (PLACES.ceo && people[name] ? `<button class="btn sec" id="pCall">📞 대표실로 부르기</button>` : ""));
  $("#pCall")?.addEventListener("click", () => {
    const q = people[name];
    q.calledUntil = Date.now() + 30000; q.busy = false;
    walkTo(q, "ceo"); showFloor(PLACE_FLOOR);
    $("#pCall").textContent = "✅ 대표실로 오고 있어요"; $("#pCall").disabled = true;
  });
  // 규칙 칸: 추가 · 지우기 (저장되면 다음 일부터 바로 적용돼요)
  const renderRules = list => {
    const box = $("#ruleList"); box.innerHTML = "";
    if (!list.length) { box.innerHTML = `<p class="muted">아직 정해 둔 규칙이 없어요.</p>`; return; }
    list.forEach((r, i) => {
      const row = document.createElement("div"); row.className = "rule";
      row.innerHTML = `<span class="rtext">${esc(r)}</span><span class="ract"></span>`;
      confirmButton($(".ract", row), "지우기", "이 규칙을 지울까요?", async () => {
        try { const res = await api(`/api/employee/${encodeURIComponent(name)}/rule/delete`, {index: i}); renderRules(res.rules); }
        catch (e) { $("#ruleErr").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
      }, "btn sec sm");
      box.appendChild(row);
    });
  };
  renderRules(p.rules || []);
  $("#ruleAdd").onclick = async () => {
    const t = $("#ruleNew").value.trim();
    if (!t) { $("#ruleErr").innerHTML = `<div class="err">규칙 내용을 적어 주세요.</div>`; return; }
    $("#ruleAdd").disabled = true;
    try { const res = await api(`/api/employee/${encodeURIComponent(name)}/rule`, {text: t}); $("#ruleNew").value = ""; $("#ruleErr").innerHTML = ""; renderRules(res.rules); }
    catch (e) { $("#ruleErr").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
    $("#ruleAdd").disabled = false;
  };
  $("#pNew")?.addEventListener("click", openNewTask);
  $("#pList")?.addEventListener("click", openTaskList);
}

function rankHtml(p) {
  const ladder = (p.ranks || []).map((r, i) =>
    `<li class="${i < p.level ? "past" : i === p.level ? "now" : ""}"><b>${esc(r.rank)}</b><small>${i ? (r.score != null ? `${r.score}점 · 통과 ${r.rate}%` : `일 ${r.done}번 · 통과 ${r.rate}%`) : "처음"}</small></li>`).join("");
  const next = p.nextRank
    ? `<div class="muted">다음 직급 <b>${esc(p.nextRank)}</b>까지: ${p.rankNeed && p.rankNeed.length ? esc(p.rankNeed.join(", ")) : "조건을 채웠어요"}</div>` +
      `<div class="bar"><i style="width:${p.rankProgress || 0}%"></i></div>`
    : `<div class="muted">가장 높은 직급이에요. 👑</div>`;
  const promo = (p.promotions || []).slice().reverse().map(x => `<span>🎉 ${esc(x.rank)} <small>${tm(x.ts)}</small></span>`).join("");
  const log = (p.scoreLog || []).filter(e => e.delta).map(e =>
    `<div class="slog ${e.delta < 0 ? "minus" : "plus"}${e.undone ? " undone" : ""}"><b>${e.delta > 0 ? "+" : ""}${e.delta}</b><span>${esc(e.why)}</span><small>${tm(e.ts)}</small>` +
    (e.delta < 0 && !e.undone ? `<button class="btn sec sm" data-undo="${esc(e.id)}" data-emp="${esc(p.name)}" title="지적이 아니었으면 되돌려요">취소</button>` : e.undone ? `<small>취소됨</small>` : "") + `</div>`).join("");
  return `<h4>🏅 직급 <span class="rk lv${p.level || 0}">${esc(p.rank)}</span> <span class="score">${p.score || 0}점</span></h4><ol class="ladder">${ladder}</ol>${next}` +
    (promo ? `<div class="chips promo">${promo}</div>` : "") +
    `<p class="muted">${p.unit === "계획" ? "기획실장 전용 직급 — 계획이 승인되면" : "일이 승인되면"} +10점, 한 번에 승인되면 +5점. 수정 요청 −10점, 최종 보고 반려 −15점, 대표 지적 −10점(같은 일에서 또 하면 −15점). 점수와 한 번에 통과 비율을 둘 다 채워야 올라가요. 한 번 오른 직급은 내려가지 않고, 결재와는 상관없어요.</p>` +
    (log ? `<details${(p.scoreLog || []).some(e => e.delta < 0 && !e.undone) ? " open" : ""}><summary>점수 기록 (최근 것부터)</summary><div class="slogs">${log}</div></details>` : "");
}
document.addEventListener("click", async e => {
  const b = e.target.closest("[data-undo]");
  if (!b) return;
  b.disabled = true;
  try { await api(`/api/employee/${encodeURIComponent(b.dataset.emp)}/score/undo`, {id: b.dataset.undo}); openProfile(b.dataset.emp); }
  catch (err) { alert(err.message); b.disabled = false; }
});

/* ---------- 사무실·현황판 크기 조절 (경계선을 끌어서) ---------- */
function initSplitter() {
  const grid = $(".grid"), sp = $("#splitter"), DEFAULT = 0.6, MIN = 0.42, MAX = 0.78;
  let split = DEFAULT;
  try { const v = parseFloat(localStorage.getItem("office.split")); if (v >= MIN && v <= MAX) split = v; } catch (e) {}
  const apply = () => { grid.style.setProperty("--sa", split + "fr"); grid.style.setProperty("--sb", (1 - split) + "fr"); };
  const save = () => { try { localStorage.setItem("office.split", String(split)); } catch (e) {} };
  const clamp = v => Math.min(MAX, Math.max(MIN, v));
  apply();
  sp.addEventListener("pointerdown", e => {
    sp.setPointerCapture(e.pointerId); sp.classList.add("drag"); document.body.classList.add("resizing");
    const move = ev => { const r = grid.getBoundingClientRect(); split = clamp((ev.clientX - r.left) / r.width); apply(); };
    const up = () => { sp.classList.remove("drag"); document.body.classList.remove("resizing"); sp.removeEventListener("pointermove", move); sp.removeEventListener("pointerup", up); sp.removeEventListener("pointercancel", up); save(); };
    sp.addEventListener("pointermove", move); sp.addEventListener("pointerup", up); sp.addEventListener("pointercancel", up);
  });
  sp.addEventListener("dblclick", () => { split = DEFAULT; apply(); save(); });
  sp.addEventListener("keydown", e => {
    if (e.key === "ArrowLeft") { split = clamp(split - .03); apply(); save(); e.preventDefault(); }
    if (e.key === "ArrowRight") { split = clamp(split + .03); apply(); save(); e.preventDefault(); }
    if (e.key === "Home" || e.key === "Enter") { split = DEFAULT; apply(); save(); e.preventDefault(); }
  });
}

/* ---------- 시작 ---------- */
(async function main() {
  initSplitter();
  await initScene();
  await refresh();
  setInterval(refresh, 2500);
})();
