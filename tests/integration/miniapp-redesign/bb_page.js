// Black-box driver for the served Mini App page (roadmap 8.44, "Lanterns").
//
// Usage: node bb_page.js <page.html> <scenario>
//
// Written from entrypoints.md's "Page contract" only: it locates elements
// by the documented ids and by visible text ("Answer in chat"), never by
// the page's internal child structure or class names. It prints one line,
// "RESULT <json>", mapping each named check to {ok, info}; the Python side
// asserts one check per test.
//
// The DOM shim follows tests/js/miniapp_smoke.js, with two deliberate
// differences that mirror a real browser: click() on a disabled element
// does nothing, and dispatching "input" is possible.
"use strict";
const fs = require("fs");
const setTimeoutReal = setTimeout;
const page = fs.readFileSync(process.argv[2], "utf8");
const SCENARIO = process.argv[3] || "grid_mixed";

const checks = {};
const errors = [];
function check(name, ok, info) {
  checks[name] = { ok: !!ok, info: info === undefined ? null : info };
}
process.on("uncaughtException", (e) => { errors.push(String(e && e.stack || e)); });
process.on("unhandledRejection", (e) => { errors.push(String(e && e.stack || e)); });

// ---- DOM shim ------------------------------------------------------------
class El {
  constructor(tag) {
    this.tagName = (tag || "div").toUpperCase();
    this.children = []; this.listeners = {}; this.attrs = {};
    this._class = ""; this._text = ""; this._html = "";
    this.hidden = false; this.disabled = false; this.value = "";
    this.dataset = {};
    const styleStore = {};
    this.style = {
      setProperty: (k, v) => { styleStore[k] = v; },
      removeProperty: (k) => { delete styleStore[k]; },
      getPropertyValue: (k) => styleStore[k] || "",
    };
    this.classList = {
      add: (...cs) => { for (const c of cs) if (!this._class.split(" ").includes(c)) this._class = (this._class + " " + c).trim(); },
      remove: (...cs) => { this._class = this._class.split(" ").filter(x => !cs.includes(x)).join(" "); },
      toggle: (c, on) => { const has = this.classList.contains(c); const want = on === undefined ? !has : !!on; want ? this.classList.add(c) : this.classList.remove(c); return want; },
      contains: (c) => this._class.split(" ").includes(c),
    };
  }
  get className() { return this._class; }
  set className(v) { this._class = String(v); }
  get textContent() {
    if (this.children.length) return this.children.map(c => c.textContent).join("");
    return this._text;
  }
  set textContent(v) {
    for (const c of this.children) c.parent = null;
    this._text = String(v); this._html = ""; this.children = [];
  }
  get innerText() { return this.textContent; }
  set innerText(v) { this.textContent = v; }
  get innerHTML() { return this._html; }
  set innerHTML(v) {
    for (const c of this.children) c.parent = null;
    this._html = String(v); this._text = this._html.replace(/<[^>]*>/g, "")
      .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, "\"")
      .replace(/&#39;/g, "'").replace(/&amp;/g, "&");
    this.children = [];
  }
  get parentNode() { return this.parent || null; }
  get parentElement() { return this.parent || null; }
  get firstChild() { return this.children[0] || null; }
  get lastChild() { return this.children[this.children.length - 1] || null; }
  get childNodes() { return this.children; }
  get nextSibling() {
    if (!this.parent) return null;
    const s = this.parent.children; return s[s.indexOf(this) + 1] || null;
  }
  get id() { return this.attrs.id || ""; }
  set id(v) { this.attrs.id = v; }
  appendChild(c) {
    if (c.parent) { const i = c.parent.children.indexOf(c); if (i >= 0) c.parent.children.splice(i, 1); }
    this.children.push(c); c.parent = this; return c;
  }
  append(...cs) { for (const c of cs) this.appendChild(typeof c === "string" ? textNode(c) : c); }
  insertBefore(c, ref) {
    if (!ref) return this.appendChild(c);
    if (c.parent) { const i = c.parent.children.indexOf(c); if (i >= 0) c.parent.children.splice(i, 1); }
    const at = this.children.indexOf(ref);
    this.children.splice(at < 0 ? this.children.length : at, 0, c);
    c.parent = this; return c;
  }
  removeChild(c) {
    const i = this.children.indexOf(c);
    if (i < 0) throw new Error("removeChild: not a child");
    this.children.splice(i, 1); c.parent = null; return c;
  }
  replaceChildren(...cs) { for (const c of this.children) c.parent = null; this.children = []; this.append(...cs); }
  remove() { if (this.parent) this.parent.removeChild(this); }
  contains(n) { for (const x of this.walk()) if (x === n) return true; return false; }
  focus() { this.focused = true; }
  blur() { this.focused = false; }
  scrollIntoView() {}
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "hidden") this.hidden = true; }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; if (k === "hidden") this.hidden = false; }
  hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k); }
  toggleAttribute(k, on) { if (on === undefined ? !this.hasAttribute(k) : on) this.setAttribute(k, ""); else this.removeAttribute(k); }
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  removeEventListener(ev, fn) { this.listeners[ev] = (this.listeners[ev] || []).filter(f => f !== fn); }
  dispatch(ev, extra) {
    const e = Object.assign({ type: ev, target: this, currentTarget: this,
      preventDefault() {}, stopPropagation() {} }, extra || {});
    (this.listeners[ev] || []).forEach(f => f.call(this, e));
  }
  // A real browser fires no click on a disabled control.
  click() { if (this.disabled) return; this.dispatch("click"); }
  querySelectorAll() { return []; }
  querySelector() { return null; }
  closest() { return null; }
  *walk() { yield this; for (const c of this.children) yield* c.walk(); }
}
function textNode(s) { const t = new El("#text"); t._text = String(s); return t; }

const byId = {};
for (const m of page.matchAll(/<([a-zA-Z0-9]+)[^>]*\bid="([^"]+)"[^>]*>/g)) {
  const el = new El(m[1]);
  el.attrs.id = m[2];
  el.hidden = /\shidden(\s|>|=)/.test(m[0]);
  el.disabled = /\sdisabled(\s|>|=)/.test(m[0]);
  byId[m[2]] = el;
}
const docEl = new El("html");
global.document = {
  getElementById: (id) => byId[id] || null,
  createElement: (t) => new El(t),
  createTextNode: (s) => textNode(s),
  createDocumentFragment: () => new El("#fragment"),
  addEventListener: () => {},
  removeEventListener: () => {},
  querySelectorAll: () => [],
  querySelector: () => null,
  visibilityState: "visible",
  hidden: false,
  documentElement: docEl,
  body: new El("body"),
  activeElement: null,
};

// ---- Telegram WebApp mock ---------------------------------------------------
const tg = {
  haptic: [], closed: 0, mb: {}, mbShown: null, main: null, events: {},
  swOff: 0, swOn: 0, header: [], background: [], back: null,
};
const MINIMAL = /^(grid_|answer_)/.test(SCENARIO) || SCENARIO === "new_form_minimal";
const webApp = {
  initData: "auth_date=1&user=%7B%22id%22%3A1%7D&hash=x",
  ready() {}, expand() {},
  BackButton: { show() {}, hide() {}, onClick(fn) { tg.back = fn; } },
  HapticFeedback: {
    notificationOccurred(t) { tg.haptic.push(["notification", t]); },
    impactOccurred(t) { tg.haptic.push(["impact", t]); },
    selectionChanged() { tg.haptic.push(["selection"]); },
  },
};
if (SCENARIO !== "answer_no_close_api") {
  webApp.close = () => { tg.closed++; };
}
if (!MINIMAL) {
  webApp.colorScheme = "light";
  webApp.version = "8.0";
  webApp.isVersionAtLeast = () => true;
  webApp.onEvent = (name, fn) => { tg.events[name] = fn; };
  webApp.offEvent = () => {};
  webApp.setHeaderColor = (c) => { tg.header.push(c); };
  webApp.setBackgroundColor = (c) => { tg.background.push(c); };
  webApp.disableVerticalSwipes = () => { tg.swOff++; };
  webApp.enableVerticalSwipes = () => { tg.swOn++; };
  webApp.MainButton = {
    setParams(p) { tg.mb = Object.assign({}, tg.mb, p); },
    setText(t) { tg.mb = Object.assign({}, tg.mb, { text: t }); },
    show() { tg.mb = Object.assign({}, tg.mb, { is_visible: true }); },
    hide() { tg.mb = Object.assign({}, tg.mb, { is_visible: false }); },
    enable() { tg.mb = Object.assign({}, tg.mb, { is_active: true }); },
    disable() { tg.mb = Object.assign({}, tg.mb, { is_active: false }); },
    onClick(fn) { tg.main = fn; },
    offClick() {},
    showProgress() {}, hideProgress() {},
  };
}
global.window = { Telegram: { WebApp: webApp }, addEventListener() {}, removeEventListener() {} };
global.Telegram = undefined;
// FLIP needs layout, rAF and matchMedia; only the reorder scenarios have them.
let rafCalls = 0;
if (SCENARIO === "grid_reorder_flip" || SCENARIO === "grid_reorder_reduced") {
  const reduced = SCENARIO === "grid_reorder_reduced";
  global.window.requestAnimationFrame = (fn) => { rafCalls++; fn(0); return rafCalls; };
  global.requestAnimationFrame = global.window.requestAnimationFrame;
  global.window.matchMedia = (q) => ({ matches: reduced && /reduce/.test(String(q)), media: String(q),
    addEventListener() {}, addListener() {} });
  global.matchMedia = global.window.matchMedia;
  El.prototype.getBoundingClientRect = function () {
    const i = this.parent ? this.parent.children.indexOf(this) : 0;
    return { left: (i % 2) * 200, top: Math.floor(i / 2) * 150, width: 190, height: 132,
             right: (i % 2) * 200 + 190, bottom: Math.floor(i / 2) * 150 + 132 };
  };
}
Object.defineProperty(global, "navigator", { value: { userAgent: "node" }, configurable: true, writable: true });
global.location = { pathname: "/", search: "", hash: "" };

// ---- timers: recorded, never run (the page's own schedule is observed) ----
const timers = [];
global.setInterval = (f, ms) => { timers.push(["interval", ms]); return timers.length; };
global.setTimeout = (f, ms) => { timers.push(["timeout", ms]); return timers.length; };
global.clearTimeout = () => {};
global.clearInterval = () => {};

// ---- fixtures ----------------------------------------------------------------
function row(label, status, extra) {
  return Object.assign({
    label, status, waiting_kind: null, waiting_summary: null, model: "Opus 4.6",
    context_pct: 30, cost_usd: 0.42, last_active_seconds_ago: 60, project: "proj",
  }, extra || {});
}
// Same shape as the real GET /api/sessions payload (daemon, totals, rows,
// can_act); the totals are derived from the rows as the server does.
function grid(rows, extra) {
  const n = (st) => rows.filter(r => r.status === st).length;
  const totals = { total: rows.length, live: rows.length - n("gone"), gone: n("gone"),
                   waiting: n("waiting"), cost_usd: rows.reduce((a, r) => a + r.cost_usd, 0) };
  return Object.assign({ daemon: { version: "0.7.17", bot_username: "aipager_test_bot", uptime_seconds: 60 },
                         totals, can_act: true, sessions: rows }, extra || {});
}
function detail(label, status, extra) {
  return Object.assign({
    label, status, waiting_kind: null, waiting_summary: null, model: "Opus 4.6",
    context_pct: 57, cost_usd: 0.42, cwd: "/home/aly/proj",
    last_active_seconds_ago: 300, busy_elapsed_seconds: null, skip_perms: false,
    queue_depth: 0, last_message: "Done.", timeline: [], facts: [],
    actions: {}, model_switch: null, answer: null,
  }, extra || {});
}
const MIXED = [
  row("alpha", "waiting", { waiting_kind: "permission", waiting_summary: "Bash: rm — build" }),
  row("bravo", "waiting", { waiting_kind: "question", waiting_summary: "Which one?" }),
  row("charlie", "busy", { context_pct: 64 }),
  row("delta", "busy"),
  row("echo", "idle"),
  row("old1", "gone"),
  row("old2", "gone"),
];
const WAIT_ALPHA = detail("alpha", "waiting", {
  waiting_kind: "permission", waiting_summary: "Bash: ls",
  actions: { stop: { available: true, reason: null } },
  answer: { available: true, reason: null },
});
const TOOLS = [];
for (let i = 1; i <= 12; i++) {
  const n = (i < 10 ? "0" : "") + i;
  TOOLS.push({ kind: "tool", text: "tool-" + n, state: i === 12 ? "running" : "done",
               elapsed_seconds: i === 12 ? 14 : null });
}
const SCHEMA = [{ section: "length", field: "answer_length", title: "Answer length",
  options: [{ value: "none", label: "Don't apply any rule", help: "" },
            { value: "short", label: "Short", help: "" }] }];

const FIX = {
  "GET /api/sessions": grid(MIXED),
  "GET /api/sessions/alpha": WAIT_ALPHA,
  "GET /api/sessions/alpha/preferences": { schema: SCHEMA, values: {}, can_edit: true },
  "GET /api/sessions/dev/preferences": { schema: SCHEMA, values: {}, can_edit: true },
  "GET /api/sessions/charlie/preferences": { schema: SCHEMA, values: {}, can_edit: true },
  "GET /api/sessions/dev": detail("dev", "busy", {
    busy_elapsed_seconds: 192, queue_depth: 2, timeline: TOOLS,
    actions: { stop: { available: true, reason: null } },
  }),
  "GET /api/sessions/charlie": detail("charlie", "busy"),
  "POST /api/sessions/alpha/answer": { status: 200, body: { status: "sent", label: "alpha" } },
  "POST /api/sessions/dev/stop": { status: 200, body: { status: "stopped", label: "dev", dropped: 0 } },
  "POST /api/sessions/dev/resume": { status: 200, body: { status: "resumed", label: "dev" } },
  "GET /api/session-options": {
    models: [{ label: "Opus", hint: "Most capable" }, { label: "Opus 5.5", hint: "" }],
    schema: SCHEMA, scope_defaults: { answer_length: "none" }, can_create: true,
    can_use_auto: true,
    directories: ["/home/aly/proj", "/home/aly/other"], default_directory: "/home/aly/other",
  },
  "POST /api/sessions": { status: 200, body: { label: "made", session_name: "claude-made" } },
  "GET /api/preferences": { schema: SCHEMA, values: {}, can_edit: true },
};

const S = SCENARIO;
if (S === "grid_none_waiting") FIX["GET /api/sessions"] = grid([row("charlie", "busy"), row("echo", "idle")]);
if (S === "grid_empty") FIX["GET /api/sessions"] = grid([]);
if (S === "answer_single" || S === "answer_no_close_api" || S === "answer_double" ||
    S === "answer_409" || S === "answer_403")
  FIX["GET /api/sessions"] = grid([MIXED[0], MIXED[2]]);
if (S === "answer_viewer") FIX["GET /api/sessions"] = grid(MIXED, { can_act: false });
if (S === "answer_409") FIX["POST /api/sessions/alpha/answer"] = { status: 409,
  body: { error: "not_waiting", detail: "This session isn't waiting — it moved on." } };
if (S === "answer_403") FIX["POST /api/sessions/alpha/answer"] = { status: 403, body: { error: "forbidden" } };
if (S === "detail_waiting_viewer") FIX["GET /api/sessions/alpha"] = Object.assign({}, WAIT_ALPHA,
  { answer: { available: false, reason: "You don't have permission to control this session." } });
if (S === "detail_gone") FIX["GET /api/sessions/dev"] = detail("dev", "gone", {
  actions: { resume: { available: true, reason: null }, delete: { available: true, reason: null } } });
if (S === "detail_idle") FIX["GET /api/sessions/dev"] = detail("dev", "idle", {
  actions: { kill: { available: true, reason: null } } });

const calls = [];
let holdAnswer = null;   // answer_double: keep the POST in flight
global.fetch = (url, opts) => {
  const method = (opts && opts.method) || "GET";
  const path = String(url).split("?")[0];
  calls.push({ url: String(url), method, path, body: opts && opts.body });
  const key = method + " " + path;
  let spec = FIX[key];
  let status = 200; let body = {};
  if (spec && spec.status !== undefined && spec.body !== undefined) { status = spec.status; body = spec.body; }
  else if (spec) { body = spec; }
  const resp = { ok: status < 400, status, json: () => Promise.resolve(JSON.parse(JSON.stringify(body))),
                 text: () => Promise.resolve(JSON.stringify(body)) };
  if (S === "answer_double" && key === "POST /api/sessions/alpha/answer") {
    return new Promise((res) => { holdAnswer = () => res(resp); });
  }
  return Promise.resolve(resp);
};

// ---- run the page ------------------------------------------------------------
let script = page.match(/<script>([\s\S]*?)<\/script>/g)
  .map(s => s.replace(/<\/?script>/g, "")).join("\n");
const HOOKS = ["openDetail", "renderSessionSettings", "loadSessionSettings",
  "saveSessionPreference", "renderOptionGroup", "openGroups", "pollTick", "loadSettings",
  "loadUpdates", "showView", "openNewSession", "renderNewForm", "submitNewSession",
  "renderGrid", "answerPrompt", "showGrid"];
const exportLine = "\n  global.__api = {" + HOOKS.map(h => h + ": (typeof " + h +
  " !== 'undefined' ? " + h + " : undefined)").join(", ") + "};\n})();";
const replaced = script.replace(/\}\)\(\);\s*$/, exportLine);
check("script_ends_with_iife", replaced !== script);
try { eval(replaced); } catch (e) { errors.push("eval: " + (e && e.stack || e)); }
const api = global.__api || {};

// ---- helpers -----------------------------------------------------------------
const wait = (ms) => new Promise(r => setTimeoutReal(r, ms || 15));
function all(root) { return root ? Array.from(root.walk()) : []; }
function text(el) { return el ? el.textContent : ""; }
function buttonsNamed(root, name) {
  return all(root).filter(e => e !== root && (e.tagName === "BUTTON" || e.listeners.click) &&
    text(e).indexOf(name) !== -1 && !e.children.some(c => text(c).indexOf(name) !== -1 && (c.tagName === "BUTTON")));
}
function cardFor(root, label) {
  return (root ? root.children : []).find(c => text(c).indexOf(label) !== -1);
}
function answerButtonIn(root) {
  const bs = buttonsNamed(root, "Answer in chat");
  return bs[bs.length - 1];
}
function posts(path) { return calls.filter(c => c.method === "POST" && (!path || c.path === path)); }
function gets(path) { return calls.filter(c => c.method === "GET" && c.path === path); }
function visible(id) { return byId[id] && !byId[id].hidden; }
function noticeText() { return text(byId["notice"]); }

async function finish() {
  const stray = calls.filter(c => c.path.indexOf("/api/") !== 0);
  check("only_api_requests", stray.length === 0, stray.map(c => c.url));
  check("no_uncaught_errors", errors.length === 0, errors.slice(0, 3));
  console.log("RESULT " + JSON.stringify(checks));
  process.exit(0);
}

// ---- scenarios ---------------------------------------------------------------
const SCEN = {};

SCEN.grid_mixed = async () => {
  await wait(30);
  const tray = byId["needs-you-list"];
  // openGroups is the page's open-state map; every other hook is callable.
  const missing = HOOKS.filter(h => api[h] === undefined ||
    (h !== "openGroups" && typeof api[h] !== "function"));
  check("hooks_exported", missing.length === 0, missing);
  check("boot_fetches_grid", gets("/api/sessions").length >= 1);
  check("tray_visible", visible("needs-you"));
  check("tray_one_card_per_waiting", tray.children.length === 2, tray.children.length);
  check("tray_answer_buttons", buttonsNamed(tray, "Answer in chat").length === 2,
        buttonsNamed(tray, "Answer in chat").length);
  check("tray_labels", text(tray).indexOf("alpha") !== -1 && text(tray).indexOf("bravo") !== -1, text(tray));
  check("tray_kind_permission", text(cardFor(tray, "alpha")).indexOf("Permission") !== -1, text(cardFor(tray, "alpha")));
  check("tray_kind_question", text(cardFor(tray, "bravo")).indexOf("Question") !== -1, text(cardFor(tray, "bravo")));
  check("tray_summary_plain", text(tray).indexOf("Bash: rm - build") !== -1, text(tray));
  check("tray_no_em_dash", text(tray).indexOf("—") === -1, text(tray));
  const tiles = byId["sessions"].children;
  check("grid_tiles_live_only", tiles.length === 3, tiles.map(text));
  check("grid_tiles_are_buttons", tiles.length > 0 && tiles.every(t => t.tagName === "BUTTON"), tiles.map(t => t.tagName));
  check("grid_waiting_not_in_tiles", ["alpha", "bravo"].every(l => text(byId["sessions"]).indexOf(l) === -1));
  check("grid_gone_not_in_tiles", ["old1", "old2"].every(l => text(byId["sessions"]).indexOf(l) === -1));
  check("tile_order", tiles.map(t => ["charlie", "delta", "echo"].find(l => text(t).indexOf(l) !== -1)).join(",") === "charlie,delta,echo",
        tiles.map(text));
  const charlie = tiles.find(t => text(t).indexOf("charlie") !== -1);
  const echo = tiles.find(t => text(t).indexOf("echo") !== -1);
  check("tile_state_working", !!charlie && text(charlie).indexOf("working") !== -1, text(charlie));
  check("tile_state_resting", !!echo && text(echo).indexOf("resting") !== -1, text(echo));
  check("tile_shows_project", !!charlie && text(charlie).indexOf("proj") !== -1, text(charlie));
  check("tile_shows_model", !!charlie && text(charlie).indexOf("Opus 4.6") !== -1, text(charlie));
  check("tile_shows_cost", !!charlie && text(charlie).indexOf("0.42") !== -1, text(charlie));
  const ringLabels = all(charlie).map(e => e.getAttribute("aria-label")).filter(Boolean);
  check("tile_ring_aria_has_pct", ringLabels.some(l => l.indexOf("64") !== -1), ringLabels);
  check("shelf_visible", visible("gone-wrap"));
  check("shelf_toggle_count", /Finished \(2\)/.test(text(byId["gone-toggle"])), text(byId["gone-toggle"]));
  check("shelf_collapsed", byId["sessions-gone"].hidden);
  check("shelf_holds_gone", ["old1", "old2"].every(l => text(byId["sessions-gone"]).indexOf(l) !== -1), text(byId["sessions-gone"]));
  const pulse = text(byId["grid-totals"]);
  check("pulse_need", /2 needs? you/.test(pulse), pulse);
  check("pulse_working", /2 working/.test(pulse), pulse);
  check("pulse_resting", /1 resting/.test(pulse), pulse);
  check("waiting_badge", visible("waiting-badge") && text(byId["waiting-badge"]).trim() === "2", text(byId["waiting-badge"]));
  check("empty_hidden", !visible("empty-state"));
  check("grid_poll_2500", timers.some(t => t[1] === 2500), timers);
  check("no_interval_faster_than_2500", timers.filter(t => t[0] === "interval").every(t => t[1] >= 2500),
        timers.filter(t => t[0] === "interval"));
  // Opening a waiting card's body opens its detail.
  const card = cardFor(byId["needs-you-list"], "alpha");
  const before = gets("/api/sessions/alpha").length;
  const cardTargets = [card].concat(all(card).filter(e => e.listeners.click && text(e).indexOf("Answer in chat") === -1));
  const target = cardTargets.find(e => e.listeners.click);
  if (target) target.click();
  await wait(20);
  check("tray_card_opens_detail", gets("/api/sessions/alpha").length > before && visible("view-detail"));
  // Shelf toggles open.
  api.showGrid && api.showGrid();
  await wait(20);
  byId["gone-toggle"].click();
  await wait(5);
  check("shelf_toggle_opens", !byId["sessions-gone"].hidden);
  await finish();
};

function clone(x) { return JSON.parse(JSON.stringify(x)); }
function tileOf(label) { return byId["sessions"].children.find(t => text(t).indexOf(label) !== -1); }
function tileOrder() {
  return byId["sessions"].children.map(t => ["charlie", "delta", "echo"].find(l => text(t).indexOf(l) !== -1));
}

SCEN.grid_keyed = async () => {
  await wait(30);
  const charlie = tileOf("charlie"); const echo = tileOf("echo");
  const beacon = cardFor(byId["needs-you-list"], "alpha");
  // Identical payload: every node survives.
  if (api.pollTick) api.pollTick();
  await wait(20);
  check("identical_poll_keeps_tile", tileOf("charlie") === charlie);
  check("identical_poll_keeps_beacon", cardFor(byId["needs-you-list"], "alpha") === beacon);
  // A changed context on one tile: nodes are reused, the ring follows.
  const next = clone(FIX["GET /api/sessions"]);
  next.sessions.find(r => r.label === "charlie").context_pct = 81;
  FIX["GET /api/sessions"] = next;
  if (api.pollTick) api.pollTick();
  await wait(20);
  check("changed_poll_reuses_changed_tile", tileOf("charlie") === charlie);
  check("changed_poll_reuses_other_tile", tileOf("echo") === echo);
  const labels = all(tileOf("charlie")).map(e => e.getAttribute("aria-label")).filter(Boolean);
  check("changed_poll_updates_ring", labels.some(l => l.indexOf("81") !== -1), labels);
  // A session that starts waiting moves from the grid into the tray.
  const moved = clone(FIX["GET /api/sessions"]);
  const e = moved.sessions.find(r => r.label === "echo");
  e.status = "waiting"; e.waiting_kind = "permission"; e.waiting_summary = "Bash: ls";
  FIX["GET /api/sessions"] = grid(moved.sessions);
  if (api.pollTick) api.pollTick();
  await wait(20);
  check("waiting_moves_to_tray", !!cardFor(byId["needs-you-list"], "echo") && !tileOf("echo"));
  await finish();
};

async function reorderRun() {
  await wait(30);
  const before = byId["sessions"].children.slice();
  const next = clone(FIX["GET /api/sessions"]);
  const live = next.sessions.filter(r => r.status === "busy" || r.status === "idle").reverse();
  next.sessions = next.sessions.filter(r => !(r.status === "busy" || r.status === "idle"));
  next.sessions.splice(2, 0, ...live);
  FIX["GET /api/sessions"] = next;
  rafCalls = 0;
  if (api.pollTick) api.pollTick();
  await wait(30);
  check("reorder_follows_server", tileOrder().join(",") === "echo,delta,charlie", tileOrder());
  check("reorder_keeps_nodes", byId["sessions"].children.every(t => before.indexOf(t) !== -1));
}

SCEN.grid_reorder = async () => { await reorderRun(); await finish(); };
SCEN.grid_reorder_flip = async () => {
  await reorderRun();
  check("flip_one_raf", rafCalls === 1, rafCalls);
  await finish();
};
SCEN.grid_reorder_reduced = async () => {
  await reorderRun();
  check("reduced_motion_no_raf", rafCalls === 0, rafCalls);
  await finish();
};

SCEN.grid_none_waiting = async () => {
  await wait(30);
  check("tray_hidden", !visible("needs-you"));
  check("badge_hidden", !visible("waiting-badge") || text(byId["waiting-badge"]).trim() === "" || text(byId["waiting-badge"]).trim() === "0",
        text(byId["waiting-badge"]));
  check("tiles_two", byId["sessions"].children.length === 2, byId["sessions"].children.length);
  check("shelf_hidden", !visible("gone-wrap"));
  await finish();
};

SCEN.grid_empty = async () => {
  await wait(30);
  check("empty_visible", visible("empty-state"));
  check("tray_hidden", !visible("needs-you"));
  byId["empty-new"].click();
  await wait(20);
  check("empty_new_opens_form", visible("view-new") && !visible("view-grid"));
  await finish();
};

SCEN.grid_new_button = async () => {
  await wait(30);
  byId["new-session-btn"].click();
  await wait(20);
  check("new_button_opens_form", visible("view-new"));
  check("new_button_fetches_options", gets("/api/session-options").length >= 1);
  await finish();
};

async function tapAnswer(label) {
  await wait(30);
  const card = cardFor(byId["needs-you-list"], label);
  const btn = answerButtonIn(card);
  check("answer_button_found", !!btn);
  if (btn) btn.click();
  return btn;
}

SCEN.answer_single = async () => {
  await tapAnswer("alpha");
  await wait(30);
  check("one_post", posts("/api/sessions/alpha/answer").length === 1, posts().map(c => c.url));
  check("closed", tg.closed === 1, tg.closed);
  check("success_haptic", tg.haptic.some(h => h[0] === "notification" && h[1] === "success"), tg.haptic);
  await finish();
};

SCEN.answer_no_close_api = async () => {
  await tapAnswer("alpha");
  await wait(30);
  check("one_post", posts("/api/sessions/alpha/answer").length === 1);
  await finish();
};

SCEN.answer_multi = async () => {
  await tapAnswer("alpha");
  await wait(30);
  check("one_post", posts("/api/sessions/alpha/answer").length === 1, posts().map(c => c.url));
  check("not_closed", tg.closed === 0, tg.closed);
  check("toast_sent", noticeText().indexOf("Sent to the chat") !== -1, noticeText());
  check("success_haptic", tg.haptic.some(h => h[0] === "notification" && h[1] === "success"), tg.haptic);
  check("stays_on_grid", visible("view-grid"));
  await finish();
};

SCEN.answer_double = async () => {
  const btn = await tapAnswer("alpha");
  check("disabled_in_flight", !!btn && btn.disabled === true);
  if (btn) { btn.click(); btn.click(); }
  check("one_post_in_flight", posts("/api/sessions/alpha/answer").length === 1,
        posts("/api/sessions/alpha/answer").length);
  // A second tap arriving through the hook itself (e.g. the MainButton,
  // which the DOM cannot disable) while the first is still in flight.
  if (api.answerPrompt) api.answerPrompt("alpha");
  check("one_post_hook_in_flight", posts("/api/sessions/alpha/answer").length === 1,
        posts("/api/sessions/alpha/answer").length);
  if (holdAnswer) holdAnswer();
  await wait(30);
  await finish();
};

SCEN.answer_409 = async () => {
  await tapAnswer("alpha");
  await wait(30);
  check("notice_shows_detail_plain", noticeText().indexOf("This session isn't waiting - it moved on.") !== -1, noticeText());
  check("notice_no_em_dash", noticeText().indexOf("—") === -1, noticeText());
  check("not_closed", tg.closed === 0);
  check("button_reenabled", (() => { const b = answerButtonIn(cardFor(byId["needs-you-list"], "alpha")); return !b || !b.disabled; })());
  await finish();
};

SCEN.answer_403 = async () => {
  await tapAnswer("alpha");
  await wait(30);
  check("notice_cant_answer", noticeText().indexOf("You can't answer prompts in this chat.") !== -1, noticeText());
  const expired = /expired/i;
  check("not_expired_badge", !expired.test(text(byId["conn-badge"])), text(byId["conn-badge"]));
  check("not_expired_error", !expired.test(text(byId["error"])), text(byId["error"]));
  check("not_closed", tg.closed === 0);
  // The app keeps working: a fresh poll still goes out and renders.
  const n = gets("/api/sessions").length;
  if (api.pollTick) api.pollTick();
  await wait(20);
  check("still_polls", gets("/api/sessions").length > n);
  await finish();
};

SCEN.answer_viewer = async () => {
  await wait(30);
  const bs = buttonsNamed(byId["needs-you-list"], "Answer in chat");
  check("buttons_present", bs.length === 2, bs.length);
  check("buttons_disabled", bs.length > 0 && bs.every(b => b.disabled), bs.map(b => b.disabled));
  bs.forEach(b => b.click());
  await wait(10);
  check("no_post", posts().length === 0);
  await finish();
};

SCEN.detail_waiting = async () => {
  await wait(30);
  api.openDetail("alpha");
  await wait(30);
  check("mb_text", tg.mb.text === "Answer in chat", tg.mb);
  check("mb_visible", tg.mb.is_visible === true, tg.mb);
  check("waiting_card_visible", visible("detail-waiting"));
  check("detail_answer_visible", visible("detail-answer"));
  check("state_line", text(byId["detail-state"]).trim() === "Needs you (permission)", text(byId["detail-state"]));
  check("quick_stop", visible("detail-quick") && /Stop/.test(text(byId["detail-quick"])), text(byId["detail-quick"]));
  if (tg.main) tg.main();
  await wait(30);
  check("mb_click_posts_answer", posts("/api/sessions/alpha/answer").length === 1, posts().map(c => c.url));
  // Menu overlay hides the MainButton.
  api.openDetail("alpha");
  await wait(30);
  byId["detail-menu-btn"].click();
  await wait(5);
  check("mb_hidden_with_menu_open", tg.mb.is_visible === false, tg.mb);
  check("swipes_off_with_overlay", tg.swOff >= 1, tg.swOff);
  if (tg.back) tg.back();
  await wait(5);
  check("back_closes_menu_first", visible("view-detail") && byId["action-menu"].hidden);
  if (tg.back) tg.back();
  await wait(20);
  check("back_returns_to_grid", visible("view-grid") && !visible("view-detail"));
  check("mb_hidden_on_grid", tg.mb.is_visible === false, tg.mb);
  await finish();
};

SCEN.detail_answer_button = async () => {
  await wait(30);
  api.openDetail("alpha");
  await wait(30);
  byId["detail-answer"].click();
  await wait(30);
  check("detail_answer_posts", posts("/api/sessions/alpha/answer").length === 1, posts().map(c => c.url));
  await finish();
};

SCEN.detail_waiting_viewer = async () => {
  await wait(30);
  api.openDetail("alpha");
  await wait(30);
  check("mb_not_visible", tg.mb.is_visible !== true, tg.mb);
  const b = byId["detail-answer"];
  check("detail_answer_not_actionable", b.hidden || b.disabled, { hidden: b.hidden, disabled: b.disabled });
  b.click();
  await wait(10);
  check("no_post", posts("/api/sessions/alpha/answer").length === 0);
  await finish();
};

SCEN.detail_busy = async () => {
  await wait(30);
  api.openDetail("dev");
  await wait(30);
  check("mb_not_visible", tg.mb.is_visible !== true, tg.mb);
  check("state_working", /^Working for /.test(text(byId["detail-state"]).trim()), text(byId["detail-state"]));
  check("waiting_card_hidden", !visible("detail-waiting"));
  check("quick_stop", visible("detail-quick") && /Stop/.test(text(byId["detail-quick"])), text(byId["detail-quick"]));
  const act = text(byId["detail-activity"]);
  const shown = TOOLS.map(t => t.text).filter(t => act.indexOf(t) !== -1);
  check("activity_visible", visible("detail-activity"));
  check("activity_at_most_8", shown.length <= 8 && shown.length > 0, shown);
  check("activity_has_latest", act.indexOf("tool-12") !== -1, act);
  check("activity_drops_oldest", act.indexOf("tool-01") === -1, act);
  const ringText = text(byId["detail-ring"]) + " " + (byId["detail-ring"].getAttribute("aria-label") || "");
  check("ring_pct", ringText.indexOf("57") !== -1, ringText);
  check("label", text(byId["detail-label"]).indexOf("dev") !== -1, text(byId["detail-label"]));
  const d = gets("/api/sessions/dev").length;
  if (api.pollTick) api.pollTick();
  await wait(20);
  check("detail_poll_refetches_detail", gets("/api/sessions/dev").length === d + 1, gets("/api/sessions/dev").length - d);
  byId["detail-quick"].click();
  await wait(30);
  check("quick_posts_stop_once", posts("/api/sessions/dev/stop").length === 1, posts().map(c => c.url));
  check("quick_no_confirm", byId["overlay"].hidden && byId["confirm-modal"].hidden);
  await finish();
};

SCEN.detail_gone = async () => {
  await wait(30);
  api.openDetail("dev");
  await wait(30);
  check("state_finished", text(byId["detail-state"]).trim() === "Finished", text(byId["detail-state"]));
  check("quick_resume", visible("detail-quick") && /Resume/.test(text(byId["detail-quick"])), text(byId["detail-quick"]));
  byId["detail-quick"].click();
  await wait(30);
  check("quick_posts_resume", posts("/api/sessions/dev/resume").length === 1, posts().map(c => c.url));
  await finish();
};

SCEN.detail_idle = async () => {
  await wait(30);
  api.openDetail("dev");
  await wait(30);
  check("state_resting", /^Resting/.test(text(byId["detail-state"]).trim()), text(byId["detail-state"]));
  check("quick_hidden", !visible("detail-quick"), text(byId["detail-quick"]));
  await finish();
};

SCEN.tile_open = async () => {
  await wait(30);
  const tile = byId["sessions"].children.find(t => text(t).indexOf("charlie") !== -1);
  if (tile) tile.click();
  await wait(30);
  check("tile_opens_detail", gets("/api/sessions/charlie").length >= 1 && visible("view-detail"));
  check("impact_light", tg.haptic.some(h => h[0] === "impact" && h[1] === "light"), tg.haptic);
  check("mb_hidden_non_waiting_detail", tg.mb.is_visible !== true, tg.mb);
  await finish();
};

function setName(v) {
  const el = byId["new-name"];
  el.value = v;
  el.dispatch("input");
  el.dispatch("keyup");
  el.dispatch("change");
}

SCEN.new_form = async () => {
  await wait(30);
  api.openNewSession();
  await wait(30);
  check("form_visible", visible("view-new"));
  check("mb_text", tg.mb.text === "Start session", tg.mb);
  check("mb_visible", tg.mb.is_visible === true, tg.mb);
  check("swipes_disabled", tg.swOff >= 1, tg.swOff);
  const polled = calls.filter(c => c.method === "GET" && c.path.indexOf("/api/sessions") === 0).length;
  if (api.pollTick) api.pollTick();
  await wait(20);
  check("no_session_poll_on_new",
        calls.filter(c => c.method === "GET" && c.path.indexOf("/api/sessions") === 0).length === polled);
  setName("");
  await wait(10);
  check("mb_inactive_when_invalid", tg.mb.is_active === false, tg.mb);
  setName("frontend");
  await wait(10);
  check("mb_active_when_valid", tg.mb.is_active === true, tg.mb);
  check("mb_mirrors_create_button", tg.mb.is_active === !byId["new-create"].disabled,
        { mb: tg.mb.is_active, create_disabled: byId["new-create"].disabled });
  const cwdChips = byId["new-cwd-chips"];
  const modelChips = byId["new-model-chips"];
  check("cwd_chips_visible", !cwdChips.hidden);
  check("cwd_chip_for_recent_project", text(cwdChips).indexOf("proj") !== -1, text(cwdChips));
  check("cwd_chip_skips_unrelated", text(cwdChips).indexOf("other") === -1, text(cwdChips));
  check("model_chip_with_hint", text(modelChips).indexOf("Opus") !== -1, text(modelChips));
  check("model_chip_skips_hintless", text(modelChips).indexOf("Opus 5.5") === -1, text(modelChips));
  const chip = all(cwdChips).find(e => e !== cwdChips && e.listeners.click && text(e).indexOf("proj") !== -1);
  const hapticBefore = tg.haptic.length;
  if (chip) chip.click();
  await wait(10);
  check("chip_selection_haptic", tg.haptic.slice(hapticBefore).some(h => h[0] === "selection"), tg.haptic);
  if (tg.main) tg.main();
  await wait(40);
  const create = posts("/api/sessions")[0];
  let body = null; try { body = create && JSON.parse(create.body); } catch (e) { body = null; }
  check("mb_click_creates", !!create, posts().map(c => c.url));
  check("chip_cwd_posted", !!body && body.cwd === "/home/aly/proj", body);
  await finish();
};

SCEN.new_form_leave = async () => {
  await wait(30);
  api.openNewSession();
  await wait(30);
  const on = tg.swOn;
  if (tg.back) tg.back();
  await wait(20);
  check("left_form", !visible("view-new"));
  check("swipes_reenabled", tg.swOn > on, { before: on, after: tg.swOn });
  check("mb_hidden_after_leave", tg.mb.is_visible === false, tg.mb);
  await finish();
};

SCEN.new_form_minimal = async () => {
  // No MainButton at all: the in-page button still creates.
  await wait(30);
  api.openNewSession();
  await wait(30);
  setName("frontend");
  await wait(10);
  byId["new-create"].click();
  await wait(40);
  check("in_page_create_posts", posts("/api/sessions").length === 1, posts().map(c => c.url));
  await finish();
};

SCEN.settings_tab = async () => {
  await wait(30);
  byId["maintab-settings"].click();
  await wait(30);
  check("settings_visible", visible("view-settings"));
  check("mb_hidden", tg.mb.is_visible !== true, tg.mb);
  const n = calls.filter(c => c.method === "GET" && c.path.indexOf("/api/sessions") === 0).length;
  if (api.pollTick) api.pollTick();
  await wait(20);
  check("no_session_poll_on_settings",
        calls.filter(c => c.method === "GET" && c.path.indexOf("/api/sessions") === 0).length === n);
  await finish();
};

SCEN.theme_changed = async () => {
  await wait(30);
  check("header_color", tg.header.indexOf("secondary_bg_color") !== -1, tg.header);
  check("background_color", tg.background.indexOf("secondary_bg_color") !== -1, tg.background);
  check("handler_registered", typeof tg.events.themeChanged === "function");
  webApp.colorScheme = "dark";
  if (tg.events.themeChanged) tg.events.themeChanged();
  const scheme = docEl.getAttribute("data-scheme") || docEl.dataset.scheme;
  check("scheme_dark", scheme === "dark", scheme);
  webApp.colorScheme = "light";
  if (tg.events.themeChanged) tg.events.themeChanged();
  const back = docEl.getAttribute("data-scheme") || docEl.dataset.scheme;
  check("scheme_light_again", back === "light", back);
  await finish();
};

const run = SCEN[SCENARIO];
if (!run) { console.error("unknown scenario " + SCENARIO); process.exit(2); }
run().catch((e) => { errors.push(String(e && e.stack || e)); finish(); });
