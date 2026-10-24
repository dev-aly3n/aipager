// Drive the Mini App page in a small DOM built from its own markup, walk
// every screen (grid, session page, menus and confirms, diff, timeline,
// settings, Updates, the problem report, the new-session form, the
// connection states) and print what a person would see on each one.
//
//   node miniapp_i18n.js <page.html> [scenario]
//
// Unlike tests/js/miniapp_smoke.js (an id-only shim), the markup is parsed
// into a real tree with text nodes, so static text, `data-i18n` attributes,
// `dir`/`lang` and attribute selectors are all observable. Options come
// from the JSON file named by AIPAGER_I18N_OPTS:
//   ui        the `ui` entry the server adds to /api/chats, /api/preferences
//             and PUT /api/app-language (absent: an English viewer)
//   catalogs  code -> the /api/i18n/<code>.json body
//   storage   initial localStorage entries (absent: no localStorage at all)
//   hostile   put markup into every server value
//   noInit    open the page without initData
// The last line printed is "RESULT <json>". Run by
// tests/test_miniapp_page_i18n.py.
const setTimeoutReal = setTimeout;
const fs = require("fs");
const PAGE = fs.readFileSync(process.argv[2], "utf8");
const SCENARIO = process.argv[3] || "screens";
const OPTS = process.env.AIPAGER_I18N_OPTS
  ? JSON.parse(fs.readFileSync(process.env.AIPAGER_I18N_OPTS, "utf8")) : {};

function fail(msg) { console.error("FAIL: " + msg); process.exit(1); }

// ---- a small DOM ------------------------------------------------------------

class TextNode {
  constructor(text) { this.nodeType = 3; this.data = String(text); this.parent = null; }
  get textContent() { return this.data; }
  set textContent(v) { this.data = String(v); }
  get parentNode() { return this.parent; }
  *walk() { yield this; }
}

const VOID = new Set(["input", "meta", "br", "img", "hr", "link", "source", "wbr"]);
const ENTITY = { amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'", "#x27": "'", nbsp: "\u00a0" };
function decode(s) {
  return s.replace(/&(amp|lt|gt|quot|#39|#x27|nbsp|#x[0-9a-fA-F]+|#[0-9]+);/g, (m, e) => {
    if (ENTITY[e] !== undefined) { return ENTITY[e]; }
    return String.fromCodePoint(e[1] === "x" || e[1] === "X"
      ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10));
  });
}

// Every element the page or the markup created, for innerHTML parsing and
// the hostile-markup check.
let writes = 0;

class El {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = String(tag || "div").toUpperCase();
    this.children = []; this.attrs = {}; this.listeners = {};
    this.parent = null; this._html = null;
    this.hidden = false; this.disabled = false; this.readOnly = false;
    this.style = {}; this.value = "";
    this.scrollTop = 0; this.clientHeight = 0; this.scrollHeight = 0;
    const self = this;
    this.classList = {
      add(c) { const l = self._classes(); if (!l.includes(c)) { l.push(c); self._setClasses(l); } },
      remove(c) { self._setClasses(self._classes().filter((x) => x !== c)); },
      toggle(c, on) {
        if (on === undefined) { on = !self._classes().includes(c); }
        if (on) { this.add(c); } else { this.remove(c); }
      },
      contains(c) { return self._classes().includes(c); },
    };
  }
  _classes() { return (this.attrs["class"] || "").split(/\s+/).filter(Boolean); }
  _setClasses(l) { writes++; this.attrs["class"] = l.join(" "); }
  get className() { return this.attrs["class"] || ""; }
  set className(v) { writes++; this.attrs["class"] = String(v); }
  get id() { return this.attrs.id || ""; }
  get parentNode() { return this.parent; }
  get textContent() { return this.children.map((c) => c.textContent).join(""); }
  set textContent(v) {
    writes++;
    this._detachAll();
    this._html = null;
    const s = String(v === null || v === undefined ? "" : v);
    if (s !== "") { const t = new TextNode(s); t.parent = this; this.children.push(t); }
  }
  get innerHTML() { return this._html === null ? serialize(this) : this._html; }
  set innerHTML(v) {
    writes++;
    this._detachAll();
    this._html = String(v);
    parseInto(this, this._html);
  }
  _detachAll() { for (const c of this.children) { c.parent = null; } this.children = []; }
  _adopt(c) {
    if (c.parent) {
      const i = c.parent.children.indexOf(c);
      if (i >= 0) { c.parent.children.splice(i, 1); }
      c.parent._html = null;
    }
    c.parent = this;
    this._html = null;
  }
  appendChild(c) { writes++; this._adopt(c); this.children.push(c); return c; }
  insertBefore(c, ref) {
    if (!ref) { return this.appendChild(c); }
    writes++;
    this._adopt(c);
    const at = this.children.indexOf(ref);
    this.children.splice(at < 0 ? this.children.length : at, 0, c);
    return c;
  }
  removeChild(c) {
    writes++;
    const i = this.children.indexOf(c);
    if (i < 0) { throw new Error("removeChild: not a child"); }
    this.children.splice(i, 1); c.parent = null; this._html = null; return c;
  }
  remove() { if (this.parent) { this.parent.removeChild(this); } }
  setAttribute(k, v) { writes++; this.attrs[k] = String(v); if (k === "hidden") { this.hidden = true; } }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; }
  hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k); }
  removeAttribute(k) { writes++; delete this.attrs[k]; }
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  dispatch(ev, extra) {
    (this.listeners[ev] || []).forEach((f) => f.call(this, Object.assign({
      preventDefault() {}, stopPropagation() {} }, extra || {})));
  }
  click() { if (this.disabled) { return; } this.dispatch("click"); }
  focus() { this.focused = true; }
  select() {}
  scrollIntoView() {}
  querySelectorAll(sel) { return selectAll(this, sel); }
  *walk() { yield this; for (const c of this.children) { yield* c.walk(); } }
}

function serialize(el) {
  return el.children.map((c) => {
    if (c.nodeType === 3) { return c.data.replace(/&/g, "&amp;").replace(/</g, "&lt;"); }
    const attrs = Object.keys(c.attrs).map((k) => " " + k + '="' + c.attrs[k] + '"').join("");
    const tag = c.tagName.toLowerCase();
    return "<" + tag + attrs + ">" + (VOID.has(tag) ? "" : serialize(c) + "</" + tag + ">");
  }).join("");
}

const TOKEN = /<!--[\s\S]*?-->|<\/([A-Za-z][\w-]*)\s*>|<([A-Za-z][\w-]*)((?:\s+[^\s=>\/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>|([^<]+)|(<)/g;
const ATTR = /([^\s=\/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;

function parseInto(root, html) {
  const stack = [root];
  let m;
  TOKEN.lastIndex = 0;
  const re = new RegExp(TOKEN.source, "g");
  while ((m = re.exec(html)) !== null) {
    const top = stack[stack.length - 1];
    if (m[0].indexOf("<!--") === 0) { continue; }
    if (m[1]) {
      const name = m[1].toUpperCase();
      for (let i = stack.length - 1; i > 0; i--) {
        if (stack[i].tagName === name) { stack.length = i; break; }
      }
      continue;
    }
    if (m[2]) {
      const el = new El(m[2]);
      let a;
      const ar = new RegExp(ATTR.source, "g");
      while ((a = ar.exec(m[3] || "")) !== null) {
        const v = a[2] !== undefined ? a[2] : a[3] !== undefined ? a[3] : a[4] !== undefined ? a[4] : "";
        el.attrs[a[1]] = decode(v);
      }
      el.hidden = Object.prototype.hasOwnProperty.call(el.attrs, "hidden");
      el.disabled = Object.prototype.hasOwnProperty.call(el.attrs, "disabled");
      if (el.attrs.value !== undefined) { el.value = el.attrs.value; }
      el.parent = top; top.children.push(el);
      if (!m[4] && !VOID.has(m[2].toLowerCase())) { stack.push(el); }
      continue;
    }
    const text = m[5] !== undefined ? decode(m[5]) : "<";
    const t = new TextNode(text); t.parent = top; top.children.push(t);
  }
}

function matches(el, sel) {
  sel = sel.trim();
  let m = sel.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
  if (m) { return el.hasAttribute(m[1]) && (m[2] === undefined || el.attrs[m[1]] === m[2]); }
  m = sel.match(/^\.([\w-]+)$/);
  if (m) { return el._classes().includes(m[1]); }
  m = sel.match(/^#([\w-]+)$/);
  if (m) { return el.attrs.id === m[1]; }
  if (/^[a-z]+$/.test(sel)) { return el.tagName === sel.toUpperCase(); }
  fail("selector not supported by the harness: " + sel);
  return false;
}

function selectAll(root, sel) {
  const parts = sel.split(",");
  const out = [];
  for (const n of root.walk()) {
    if (n === root || n.nodeType !== 1) { continue; }
    if (parts.some((p) => matches(n, p))) { out.push(n); }
  }
  return out;
}

// ---- the document -------------------------------------------------------------

const htmlEl = new El("html");
const langMatch = PAGE.match(/<html([^>]*)>/);
if (langMatch) {
  let a;
  const ar = new RegExp(ATTR.source, "g");
  while ((a = ar.exec(langMatch[1])) !== null) { htmlEl.attrs[a[1]] = a[2] || ""; }
}
const bodyEl = new El("body");
htmlEl.appendChild(bodyEl);
const bodyStart = PAGE.indexOf("<body>") + "<body>".length;
const bodyEnd = PAGE.indexOf("<script>", bodyStart);
parseInto(bodyEl, PAGE.slice(bodyStart, bodyEnd));

function byId(id) {
  for (const n of bodyEl.walk()) { if (n.nodeType === 1 && n.attrs.id === id) { return n; } }
  return null;
}

global.document = {
  documentElement: htmlEl,
  body: bodyEl,
  getElementById: byId,
  createElement: (t) => new El(t),
  createTextNode: (t) => new TextNode(t),
  addEventListener: () => {},
  querySelectorAll: (sel) => selectAll(htmlEl, sel),
  visibilityState: "visible",
};

// ---- clocks and timers --------------------------------------------------------

let NOW = 1800000000000;
Date.now = () => NOW;
// Recorded, never run on their own: a step runs the ones it needs.
const timers = [];
global.setTimeout = (fn, ms) => { timers.push({ fn, ms: ms || 0, done: false }); return timers.length; };
global.clearTimeout = (id) => { if (timers[id - 1]) { timers[id - 1].done = true; } };
global.setInterval = () => 0;
global.clearInterval = () => {};
function runTimers(pred) {
  timers.forEach((t) => {
    if (!t.done && pred(t.ms)) { t.done = true; t.fn(); }
  });
}

// ---- Telegram ----------------------------------------------------------------

const seen = { mb: [], confirms: [], closed: 0 };
let confirmAnswer = false;
const webApp = {
  initData: OPTS.noInit ? "" : "auth_date=1&user=%7B%22id%22%3A1%7D&hash=x",
  colorScheme: "light",
  ready() {}, expand() {},
  isVersionAtLeast: () => true,
  onEvent() {}, setHeaderColor() {}, setBackgroundColor() {},
  disableVerticalSwipes() {}, enableVerticalSwipes() {},
  close() { seen.closed++; },
  BackButton: { show() {}, hide() {}, onClick(fn) { global.__back = fn; } },
  HapticFeedback: { notificationOccurred() {}, impactOccurred() {}, selectionChanged() {} },
  MainButton: {
    params: {},
    setParams(p) { this.params = Object.assign({}, this.params, p); },
    onClick(fn) { global.__main = fn; },
    showProgress() {}, hideProgress() {},
  },
  showConfirm(message, cb) { seen.confirms.push(message); cb(confirmAnswer); },
};
const STORE = OPTS.storage ? Object.assign({}, OPTS.storage) : null;
global.window = { Telegram: { WebApp: webApp } };
if (STORE) {
  global.window.localStorage = {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(STORE, k) ? STORE[k] : null),
    setItem: (k, v) => { STORE[k] = String(v); },
    removeItem: (k) => { delete STORE[k]; },
  };
}
global.Telegram = undefined;
Object.defineProperty(globalThis, "navigator", {
  value: { clipboard: { writeText: () => Promise.resolve() } }, configurable: true,
});

// ---- the server ----------------------------------------------------------------

const calls = [];
// "METHOD path" -> {status, body} | function(req) -> {status, body} | "reject" | "hold"
const R = {};
const held = [];
// Every string a server answer carried: data, never the page's own text.
const served = new Set();
function collect(v) {
  if (typeof v === "string") { served.add(v); return; }
  if (v && typeof v === "object") { Object.keys(v).forEach((k) => collect(v[k])); }
}
function respond(spec) {
  if (!(spec.body && spec.body.strings && spec.body.code)) { collect(spec.body); }
  return { ok: spec.status < 400, status: spec.status,
           json: () => Promise.resolve(JSON.parse(JSON.stringify(spec.body === undefined ? {} : spec.body))) };
}
global.fetch = (url, opts) => {
  const method = (opts && opts.method) || "GET";
  const path = url.split("?")[0];
  calls.push({ url, method, body: opts && opts.body });
  let m = path.match(/^\/api\/i18n\/([^/]+)\.json$/);
  if (m && method === "GET") {
    const body = (OPTS.catalogs || {})[decodeURIComponent(m[1])];
    if (R["GET " + path] === "reject") { return Promise.reject(new TypeError("Failed to fetch")); }
    const answer = body ? { status: 200, body } : { status: 404, body: { error: "not_found" } };
    if (R["GET " + path] === "hold") {
      return new Promise((res) => { held.push(() => res(respond(answer))); });
    }
    return Promise.resolve(respond(answer));
  }
  let spec = R[method + " " + path];
  if (spec === undefined) { spec = { status: 200, body: {} }; }
  if (typeof spec === "function") { spec = spec({ url, method, body: opts && opts.body }); }
  if (spec === "reject") { return Promise.reject(new TypeError("Failed to fetch")); }
  if (spec && spec.hold) {
    return new Promise((res) => { held.push(() => res(respond(spec))); });
  }
  return Promise.resolve(respond(spec));
};

// The page may only ever talk to its own API.
const realExit = process.exit;
process.exit = (code) => {
  if (!code) {
    const stray = calls.filter((c) => c.url.indexOf("/api/") !== 0);
    if (stray.length) {
      console.error("FAIL: the page fetched outside /api/: " + JSON.stringify(stray.map((c) => c.url)));
      realExit(1);
    }
  }
  realExit(code);
};

// ---- fixtures -------------------------------------------------------------------------

// Every server value, so a check can tell server text from the page's own.
const H = OPTS.hostile ? "<img src=x onerror=1>" : "";
function sv(s) { return H ? s + H : s; }

const SCHEMA = [
  { section: "length", field: "answer_length", title: sv("📏 Answer length"),
    options: [
      { value: "none", label: sv("Don't apply any rule"), help: sv("Claude answers as it likes") },
      { value: "short", label: sv("Short - a few lines"), help: "" },
    ] },
  { section: "layout", field: "layout", title: sv("🖼 Message layout"),
    options: [
      { value: "split", label: sv("Split"), help: "" },
      { value: "merged", label: sv("Merged"), help: "" },
    ] },
];

function row(label, status, extra) {
  return Object.assign({
    label, status, waiting_kind: null, waiting_summary: null, model: "Opus 4.6",
    context_pct: 40, cost_usd: 0.5, last_active_seconds_ago: 30, project: "proj",
  }, extra || {});
}
function grid(rows, extra) {
  const live = rows.filter((r) => r.status !== "gone");
  return Object.assign({
    daemon: { version: "0.7.17", bot_username: "aipager_bot", uptime_seconds: 600 },
    totals: { total: rows.length, live: live.length, gone: rows.length - live.length,
              waiting: rows.filter((r) => r.status === "waiting").length, cost_usd: 4.21 },
    sessions: rows, can_act: true,
  }, extra || {});
}
const MIXED = [
  row("alpha", "waiting", { waiting_kind: "permission", waiting_summary: sv("Bash: npm test") }),
  row("bravo", "waiting", { waiting_kind: "question" }),
  row("charlie", "busy", { context_pct: 64 }),
  row("delta", "busy", { model: "", project: "" }),
  row("echo", "idle", { last_active_seconds_ago: 7200, project: "other" }),
  row("foxtrot", "unknown", { last_active_seconds_ago: null }),
  row("old1", "gone", { last_active_seconds_ago: 90000 }),
  row("old2", "gone", { last_active_seconds_ago: 200 }),
  row("old3", "gone", { model: "", cost_usd: 0, project: "", last_active_seconds_ago: 4000 }),
];

const ALL_ACTIONS = ["bringback", "stop", "clearqueue", "compact", "resume", "rename", "kill",
                     "perms", "restart", "delete"];
function actions(avail, reasons) {
  const out = {};
  ALL_ACTIONS.forEach((k) => {
    if (avail.indexOf(k) >= 0) { out[k] = { available: true, reason: null }; }
    if (reasons && reasons[k]) { out[k] = { available: false, reason: sv(reasons[k]) }; }
  });
  return out;
}
// The server sends a fact's label in the viewer's language (OPTS.factLabels,
// by id); the page decides by the id, never by the label.
function facts(list) {
  const labels = OPTS.factLabels || {};
  return list.map((f) => ({ id: f[0], label: sv(labels[f[0]] || f[1]), value: f[2] }));
}
// The session's three modes as the server sends them (labels in the
// viewer's language); Full access mode needs an admin.
function modes(current, admin) {
  const labels = { ask: "💬 Ask mode", auto: "🤖 Auto mode", full: "🔓 Full access mode" };
  return ["ask", "auto", "full"].map((id) => ({
    id, label: sv(labels[id]), current: id === current,
    available: id !== "full" || !!admin,
    reason: id === "full" && !admin ? sv("Switching to Full access mode requires admin.") : null,
    restarts: id === "full" || current === "full",
  }));
}
function detail(label, status, extra) {
  return Object.assign({
    label, status, waiting_kind: null, waiting_summary: null, model: "", context_pct: 0,
    cost_usd: 0, cwd: "", last_active_seconds_ago: null, busy_elapsed_seconds: null,
    busy_elapsed_unit: "s", mode: "ask", mode_pending: false, modes: modes("ask", false),
    queue_depth: 0, last_message: "",
    timeline: [], facts: [], model_switch: null, answer: null, actions: {},
  }, extra || {});
}
const TIMELINE = [
  { kind: "commentary", text: sv("Reading first.") },
  { kind: "tool", text: sv("Read a.py"), state: "done", elapsed_seconds: null },
  { kind: "tool", text: sv("Bash: pytest"), state: "failed", elapsed_seconds: null },
  { kind: "tool", text: sv("Agent: Explore"), state: "running", elapsed_seconds: 14 },
];
const DETAILS = {
  charlie: detail("charlie", "busy", {
    model: "Opus 4.6", context_pct: 64, cost_usd: 1.5, cwd: "/home/op/proj",
    last_active_seconds_ago: 30, busy_elapsed_seconds: 192, queue_depth: 2,
    last_message: sv("Ran `pytest` and fixed it."), timeline: TIMELINE,
    facts: facts([["model", "Model", "Opus 4.6"], ["context", "Context", "64%"],
                  ["cost", "Cost", "$1.50"], ["working", "Working for", "3:12"],
                  ["last_active", "Last active", "30s ago"], ["cwd", "Directory", "/home/op/proj"]]),
    model_switch: { available: false, reason: sv("Claude is working - switch the model when this turn ends.") },
    actions: actions(["stop", "clearqueue", "rename", "perms", "restart"],
                     { compact: "Nothing to compact - context is already empty." }),
  }),
  alpha: detail("alpha", "waiting", {
    waiting_kind: "permission", waiting_summary: sv("Bash: npm test"), model: "Opus 4.6",
    context_pct: 12, cwd: "/home/op/proj", last_active_seconds_ago: 5, busy_elapsed_seconds: 61,
    answer: { available: true, reason: null },
    model_switch: { available: false, reason: sv("A prompt is open in the terminal - answer it before switching the model.") },
    actions: actions(["stop", "rename", "perms", "restart"],
                     { clearqueue: "Nothing queued to clear." }),
  }),
  bravo: detail("bravo", "waiting", {
    waiting_kind: "question", answer: { available: false, reason: sv("You don't have permission to control this session.") },
    actions: actions([], { stop: "You don't have permission to control this session." }),
  }),
  echo: detail("echo", "idle", {
    model: "Sonnet 5", context_pct: 9, cost_usd: 0.25, cwd: "/home/op/other",
    last_active_seconds_ago: 7200, mode: "full", modes: modes("full", true),
    facts: facts([["model", "Model", "Sonnet 5"], ["context", "Context", "9%"],
                  ["last_active", "Last active", "2h ago"], ["cwd", "Directory", "/home/op/other"]]),
    model_switch: { available: true, reason: null },
    actions: actions(["compact", "rename", "kill", "perms", "restart"]),
  }),
  foxtrot: detail("foxtrot", "unknown"),
  // Its conversation moved into a Claude Code background job.
  golf: detail("golf", "background", {
    model: "Opus 4.6", context_pct: 30, cwd: "/home/op/proj", last_active_seconds_ago: 90,
    parked: true, parked_seconds_ago: 90,
    facts: facts([["model", "Model", "Opus 4.6"], ["context", "Context", "30%"],
                  ["last_active", "Last active", "1m ago"], ["cwd", "Directory", "/home/op/proj"]]),
    actions: actions(["bringback", "rename", "kill", "restart"],
                     { stop: "This session isn't busy right now - nothing to stop." }),
  }),
  old1: detail("old1", "gone", {
    last_active_seconds_ago: 90000, cwd: "/home/op/proj",
    facts: facts([["last_active", "Last active", "1d ago"], ["cwd", "Directory", "/home/op/proj"]]),
    actions: actions(["rename", "delete"],
                     { resume: "No resumable transcript - start a fresh session instead." }),
  }),
};
const OPTIONS = {
  directories: ["/home/op/proj", "/home/op/other"], default_directory: "/home/op/proj",
  models: [{ label: "Opus", hint: sv("Most capable") }, { label: "Sonnet", hint: "" }],
  schema: SCHEMA, scope_defaults: { answer_length: "none", layout: "split" },
  can_create: true, can_use_full: false, default_mode: "auto",
};
const APP_LANGUAGE = {
  title: sv("App language"), hint: sv("Only aipager's own text. Claude's answers don't change."),
  value: "auto", effective: "en",
  options: [{ value: "auto", label: sv("Automatic (your Telegram language)") },
            { value: "en", label: "English" }, { value: "ru", label: "Русский" },
            { value: "fa", label: "فارسی" }],
};
function prefs(extra) {
  return Object.assign({
    schema: SCHEMA, values: { answer_length: "short", layout: "split" }, can_edit: false,
    can_update: true, can_report: true, app_language: JSON.parse(JSON.stringify(APP_LANGUAGE)),
    appearance: { schema: [{ field: "stop", title: sv("🛑 Stop button"), options: [
      { value: "normal", label: sv("Normal"), help: sv("Telegram's own look, like every other button."),
        swatch: [] },
      { value: "red", label: sv("Red"), help: "", swatch: ["red"] }] }],
      values: { stop: "normal" } },
  }, OPTS.ui ? { ui: OPTS.ui } : {}, extra || {});
}
const CHECKS = {
  claude: { lines: [sv("aipager 0.7.13 (up to date)"), sv("Claude Code 2.1.281 → 2.1.290")],
            offer: { kind: "claude", label: sv("Update Claude Code") },
            summary: null, restart: null, notes: [], source: sv("pipx, from PyPI") },
  aipager: { lines: [sv("aipager 0.7.13 → 0.7.14")],
             offer: { kind: "aipager", label: sv("Update aipager") }, summary: null,
             restart: sv("Restart: automatic, once no turn is running."), notes: [sv("A note.")],
             source: sv("pipx, from PyPI") },
  none: { lines: [sv("aipager 0.7.13 (up to date)")], offer: null, summary: null,
          restart: null, notes: [], source: "" },
  said: { lines: [], offer: null, summary: sv("Nothing to do."), restart: null, notes: [],
          source: "" },
};
function job(phase, extra) {
  return Object.assign({ id: 7, kind: "aipager", phase, blockers: [], summary: "",
                         started_at: 1 }, extra || {});
}
const REPORT = {
  aipager: { version: "0.7.17", install: "pipx" },
  claude_code: { version: "2.1.290" },
  os: { system: "linux", distro: "ubuntu", distro_version: "24.04", container: "docker" },
  python: { version: "3.12.3" },
  config: { mode: "team", scopes_dm: 1, scopes_group: 2,
            features: ["miniapp", "voice", "something_new"] },
  runtime: { uptime: "1-24h", last_exit: "crash", unclean_exits_7d: 2 },
  flood: { bans_7d: 1, hour_load: "0.3" },
  deps: { aiohttp: "3.9.5", httpx: null },
  counters_24h: { stale_busy: 2, card_edits: 5 },
  log_digest_24h: [{ n: 3 }, { n: 1 }],
  errors: [
    { trigger: "crash", type: "builtins.RuntimeError", count: 1, last_day: "2026-10-01",
      first_day: "2026-09-30", fingerprint: "ab12", versions_seen: ["0.7.17"],
      frames: [{ file: "aipager/bot/notify.py", line: 10, fn: "deliver" }], external: [] },
    { trigger: "hook_cap", type: null, count: 2, last_day: "2026-10-02", first_day: "2026-10-01",
      fingerprint: "cd34", versions_seen: ["0.7.17"], frames: [], external: [] },
    { type: null, count: 1, last_day: "2026-10-03", first_day: "2026-10-03",
      fingerprint: "ef56", versions_seen: ["0.7.16", "0.7.17"], frames: [], external: ["aiohttp"] },
    { type: "builtins.ValueError", count: 3, last_day: "2026-10-04", first_day: "2026-10-01",
      fingerprint: "0a0b", versions_seen: ["0.7.17"], frames: [], external: [] },
    { type: "<lambda>", count: 1, last_day: "2026-10-05", first_day: "2026-10-05",
      fingerprint: "1c1d", versions_seen: ["0.7.17"], frames: [], external: [] },
  ],
};
function report(extra) {
  return Object.assign(JSON.parse(JSON.stringify(REPORT)), extra || {});
}
const DRAFT = { draft: "d1", report: report(), areas: ["notify", "hook", "monitor", "launch", "x"],
                note_max: 20 };

// What the server sends, before any step changes it.
function baseRoutes() {
  R["GET /api/chats"] = { status: 200, body: Object.assign({
    chats: [{ scope: "1", label: sv("me DM") }, { scope: "-100", label: sv("team") }],
    current: "1" }, OPTS.ui ? { ui: OPTS.ui } : {}) };
  R["GET /api/sessions"] = { status: 200, body: grid(MIXED) };
  Object.keys(DETAILS).forEach((k) => {
    R["GET /api/sessions/" + k] = { status: 200, body: DETAILS[k] };
    R["GET /api/sessions/" + k + "/preferences"] = { status: 200, body: {
      schema: SCHEMA, can_edit: true,
      values: {
        answer_length: { effective: "short", scope_default: "none", override_value: "short",
                         overridden: true },
        layout: { effective: "split", scope_default: "split", override_value: null,
                  overridden: false },
      } } };
  });
  R["GET /api/session-options"] = { status: 200, body: OPTIONS };
  R["GET /api/preferences"] = { status: 200, body: prefs() };
  R["GET /api/update"] = { status: 200, body: { job: null } };
}
baseRoutes();
// The first grid answer waits until a scenario lets it through.
if (OPTS.holdGrid) { R["GET /api/sessions"].hold = true; }

// ---- the page --------------------------------------------------------------------------

let script = PAGE.match(/<script>([\s\S]*?)<\/script>/g)
  .map((s) => s.replace(/<\/?script>/g, "")).join("\n");
script = script.replace(/\}\)\(\);\s*$/,
  "\n  global.__api = { openDetail: openDetail, pollTick: pollTick, showGrid: showGrid," +
  " openNewSession: openNewSession, checkUpdates: checkUpdates };\n})();");
// The first frame: the markup as served, before the page's script ran.
const PRE = { lang: htmlEl.getAttribute("lang"), dir: htmlEl.getAttribute("dir"),
              texts: null, statics: null,
              daemonDir: byId("daemon-line") && byId("daemon-line").getAttribute("dir") };
PRE.texts = visible(bodyEl, []);
PRE.statics = staticTexts();
try {
  eval(script);
} catch (e) {
  fail("the page's script threw: " + (e && e.stack));
}
const api = global.__api || {};
// What the page shows before any answer arrived (the fetches resolve on
// later ticks).
const BOOT = { name: "boot", texts: visible(bodyEl, []) };
const BOOT_STATE = { lang: htmlEl.getAttribute("lang"), dir: htmlEl.getAttribute("dir"),
                     calls: calls.map((c) => c.method + " " + c.url), statics: staticTexts(),
                     daemonDir: byId("daemon-line").getAttribute("dir") };

// ---- what a person sees --------------------------------------------------------------

function visible(node, out) {
  if (node.nodeType === 3) {
    const t = node.data.replace(/\s+/g, " ").trim();
    if (t) { out.push(t); }
    return out;
  }
  if (node.hidden) { return out; }
  // The ring's percent sign used to be CSS `content:`, which the base
  // page's snapshot never saw; the sign itself is checked on its own.
  if (node._classes().includes("lamp-pct")) { return out; }
  ["aria-label", "placeholder", "title"].forEach((a) => {
    if (node.hasAttribute(a) && node.attrs[a] !== "") { out.push("@" + a + "=" + node.attrs[a]); }
  });
  node.children.forEach((c) => visible(c, out));
  return out;
}

const screens = [];
// The most <img> elements ever in the page: server or catalog markup that
// reached innerHTML unescaped would make one (a hostile run checks it).
let maxImgs = 0;
function snap(name) {
  maxImgs = Math.max(maxImgs, selectAll(bodyEl, "img").length);
  const texts = visible(bodyEl, []);
  const mb = webApp.MainButton.params;
  if (mb.is_visible && mb.text) { texts.push("#main=" + mb.text); }
  seen.confirms.forEach((c) => texts.push("#confirm=" + c));
  seen.confirms = [];
  screens.push({ name, texts });
}

function q(sel) { return document.querySelectorAll(sel); }
function el(id) { const n = byId(id); if (!n) { fail("no #" + id); } return n; }
function clearNotice() { el("notice").textContent = ""; el("error").textContent = ""; }
function wait(ms) { return new Promise((r) => setTimeoutReal(r, ms === undefined ? 5 : ms)); }
function findText(root, text) {
  for (const n of root.walk()) {
    if (n.nodeType === 1 && n.textContent === text) { return n; }
  }
  return null;
}
function modeItem(id) {
  const n = q(".mode-" + id)[0];
  if (!n) { fail("no mode item " + id); }
  return n;
}
function menuItem(key) {
  const n = q(".act-" + key)[0];
  if (!n) { fail("no menu item " + key); }
  return n;
}
function groupHeads(host) { return el(host).querySelectorAll(".grp-head"); }
// Open group i of host, unless it is open already (open groups stay open
// across renders of the same form).
function openGroup(host, i) {
  const wrap = el(host).querySelectorAll(".grp")[i];
  if (!wrap) { fail("no group " + i + " in #" + host); }
  if (!wrap.classList.contains("is-open")) { groupHeads(host)[i].click(); }
}
function dropTimers() { timers.forEach((t) => { t.done = true; }); }
function type(id, value) { const n = el(id); n.value = value; n.dispatch("input"); }
function choices(host) { return el(host).querySelectorAll(".choice"); }

// ---- the screens ---------------------------------------------------------------------

const STEPS = [];
function step(name, fn) { STEPS.push([name, fn]); }

step("grid", async () => { await wait(); });
step("grid.shelf", async () => { el("gone-toggle").click(); await wait(); });
step("grid.one_waiting", async () => {
  R["GET /api/sessions"] = { status: 200, body: grid([
    row("alpha", "waiting", { waiting_kind: "permission" }), row("charlie", "idle"),
    row("old1", "gone")]) };
  api.pollTick(); await wait();
});
step("grid.all_quiet", async () => {
  R["GET /api/sessions"] = { status: 200, body: grid([row("old1", "gone")]) };
  api.pollTick(); await wait();
});
step("grid.viewer", async () => {
  R["GET /api/sessions"] = { status: 200, body: grid(MIXED, { can_act: false }) };
  api.pollTick(); await wait();
});
step("grid.empty", async () => {
  R["GET /api/sessions"] = { status: 200, body: grid([]) };
  api.pollTick(); await wait();
});
step("answer.sent", async () => {
  R["GET /api/sessions"] = { status: 200, body: grid(MIXED) };
  api.pollTick(); await wait();
  R["POST /api/sessions/alpha/answer"] = { status: 200, body: { status: "sent", label: "alpha" } };
  clearNotice();
  q(".answer-btn")[0].click(); await wait();
});
step("answer.forbidden", async () => {
  R["POST /api/sessions/alpha/answer"] = { status: 403, body: { error: "forbidden" } };
  clearNotice(); q(".answer-btn")[0].click(); await wait();
});
step("answer.refused_detail", async () => {
  R["POST /api/sessions/alpha/answer"] = { status: 409, body: { error: "not_waiting",
    detail: sv("This session isn't waiting on you any more.") } };
  clearNotice(); q(".answer-btn")[0].click(); await wait();
});
step("answer.refused", async () => {
  R["POST /api/sessions/alpha/answer"] = { status: 502, body: {} };
  clearNotice(); q(".answer-btn")[0].click(); await wait();
});
step("answer.unreachable", async () => {
  R["POST /api/sessions/alpha/answer"] = "reject";
  clearNotice(); q(".answer-btn")[0].click(); await wait();
});

// The session page.
async function open(label) { clearNotice(); api.openDetail(label); await wait(); }
step("detail.busy", async () => { await open("charlie"); });
step("detail.busy.menu", async () => { el("detail-menu-btn").click(); await wait(); });
step("detail.busy.perms", async () => {
  // Full access is refused here; a busy session an admin may switch:
  R["GET /api/sessions/charlie"] = { status: 200, body: Object.assign({}, DETAILS.charlie, {
    actions: actions(["stop", "clearqueue", "compact", "rename", "perms", "restart"]),
    modes: modes("ask", true) }) };
  el("overlay").click();
  await open("charlie");
  el("detail-menu-btn").click(); await wait();
  modeItem("full").click(); await wait();
});
step("detail.busy.restart", async () => {
  el("confirm-cancel").click();
  el("detail-menu-btn").click(); menuItem("restart").click(); await wait();
});
step("detail.busy.stopped", async () => {
  el("confirm-cancel").click();
  R["POST /api/sessions/charlie/stop"] = { status: 200, body: { status: "stopped" } };
  el("detail-menu-btn").click(); menuItem("stop").click(); await wait();
});
step("detail.busy.cleared", async () => {
  R["POST /api/sessions/charlie/clearqueue"] = { status: 200, body: { status: "cleared" } };
  clearNotice(); el("detail-menu-btn").click(); menuItem("clearqueue").click(); await wait();
});
step("detail.busy.compacting", async () => {
  R["POST /api/sessions/charlie/compact"] = { status: 200, body: { status: "queued" } };
  clearNotice(); el("detail-menu-btn").click(); menuItem("compact").click(); await wait();
});
step("detail.busy.refused", async () => {
  R["POST /api/sessions/charlie/compact"] = { status: 409, body: { error: "queue_full",
    detail: sv("Queue is full (50 pending) - clear it or wait for it to drain.") } };
  clearNotice(); el("detail-menu-btn").click(); menuItem("compact").click(); await wait();
});
step("detail.busy.failed", async () => {
  R["POST /api/sessions/charlie/compact"] = { status: 400, body: {} };
  clearNotice(); el("detail-menu-btn").click(); menuItem("compact").click(); await wait();
});
step("detail.busy.unreachable", async () => {
  R["POST /api/sessions/charlie/compact"] = "reject";
  clearNotice(); el("detail-menu-btn").click(); menuItem("compact").click(); await wait();
});
step("detail.busy.perms_done", async () => {
  R["POST /api/sessions/charlie/perms"] = { status: 200, body: { status: "switched" } };
  confirmAnswer = false;
  clearNotice(); el("detail-menu-btn").click(); modeItem("full").click(); await wait();
  el("confirm-ok").click(); await wait();
});
step("detail.busy.mode_sent", async () => {
  R["POST /api/sessions/charlie/perms"] = { status: 200, body: { status: "sent", pending: true } };
  clearNotice(); el("detail-menu-btn").click(); modeItem("auto").click(); await wait();
});
step("detail.busy.restarted", async () => {
  R["POST /api/sessions/charlie/restart"] = { status: 200, body: { status: "restarted" } };
  clearNotice(); el("detail-menu-btn").click(); menuItem("restart").click(); await wait();
  el("confirm-ok").click(); await wait();
});
step("detail.busy.changed", async () => {
  el("detail-menu-btn").click(); await wait();
  R["GET /api/sessions/charlie"] = { status: 200, body: DETAILS.charlie };
  clearNotice(); api.pollTick(); await wait();
});
step("detail.busy.timeline", async () => { el("tab-timeline").click(); await wait(); });
step("detail.busy.diff", async () => {
  R["GET /api/sessions/charlie/diff"] = { status: 200, body: {
    available: true, files_truncated: true, files: [
      { path: "a.py", change_type: "modified", patch: "@@ -1 +1 @@\n-a\n+b", binary: false },
      { path: "img.png", change_type: "added", binary: true },
      { path: "big.txt", change_type: "untracked", patch: "", truncated: true },
      { path: "gone.txt", change_type: "deleted", patch: "" },
      { path: "new.py", change_type: "renamed", patch: "" },
    ] } };
  el("tab-diff").click(); await wait();
});
step("detail.busy.settings_saved", async () => {
  R["PUT /api/sessions/charlie/preferences/layout"] = { status: 200, body: {
    values: { answer_length: { effective: "short", scope_default: "none", override_value: "short", overridden: true },
              layout: { effective: "merged", scope_default: "split", override_value: "merged", overridden: true } } } };
  clearNotice();
  groupHeads("session-settings-groups")[1].click(); await wait();
  choices("session-settings-groups")[1].click(); await wait();
});
step("detail.busy.settings_failed", async () => {
  R["PUT /api/sessions/charlie/preferences/answer_length"] = { status: 500, body: {} };
  clearNotice();
  groupHeads("session-settings-groups")[0].click(); await wait();
  choices("session-settings-groups")[0].click(); await wait();
});
step("detail.busy.reset_confirm", async () => {
  clearNotice(); el("session-settings-reset").click(); await wait();
});
step("detail.busy.reset_done", async () => {
  R["DELETE /api/sessions/charlie/preferences/answer_length"] = { status: 200, body: {
    values: { answer_length: { effective: "none", scope_default: "none", override_value: null, overridden: false },
              layout: { effective: "merged", scope_default: "split", override_value: "merged", overridden: true } } } };
  R["DELETE /api/sessions/charlie/preferences/layout"] = { status: 500, body: {} };
  el("confirm-ok").click(); await wait(20);
});
step("detail.busy.reset_all", async () => {
  R["DELETE /api/sessions/charlie/preferences/layout"] = { status: 200, body: {
    values: { answer_length: { effective: "none", scope_default: "none", override_value: null, overridden: false },
              layout: { effective: "split", scope_default: "split", override_value: null, overridden: false } } } };
  clearNotice(); el("session-settings-reset").click(); el("confirm-ok").click(); await wait(20);
});
step("detail.busy.rename", async () => {
  clearNotice(); el("detail-menu-btn").click(); menuItem("rename").click(); await wait();
});
step("detail.busy.rename_empty", async () => { type("confirm-rename-input", " "); await wait(); });
step("detail.busy.rename_long", async () => { type("confirm-rename-input", "a".repeat(70)); await wait(); });
step("detail.busy.rename_bad", async () => { type("confirm-rename-input", "a b"); await wait(); });
step("detail.busy.rename_reserved", async () => { type("confirm-rename-input", "Status"); await wait(); });
step("detail.busy.renamed", async () => {
  R["POST /api/sessions/charlie/rename"] = { status: 200, body: { status: "renamed", label: "charlie", changed: false } };
  type("confirm-rename-input", "charlie");
  el("confirm-ok").click(); await wait(20);
});
step("detail.busy.rename_failed", async () => {
  R["POST /api/sessions/charlie/rename"] = { status: 409, body: { error: "conflict" } };
  clearNotice(); el("detail-menu-btn").click(); menuItem("rename").click();
  type("confirm-rename-input", "zulu"); el("confirm-ok").click(); await wait(20);
});
step("detail.waiting", async () => { await open("alpha"); });
step("detail.waiting.answer_sent", async () => {
  R["POST /api/sessions/alpha/answer"] = { status: 200, body: { status: "sent" } };
  clearNotice(); el("detail-answer").click(); await wait();
});
step("detail.question", async () => { await open("bravo"); });
step("detail.idle", async () => { await open("echo"); });
step("detail.idle.model", async () => {
  groupHeads("detail-model")[0].click(); await wait();
});
step("detail.idle.model_switching", async () => {
  R["POST /api/sessions/echo/model"] = { hold: true, status: 200, body: { status: "switched", model: "Opus" } };
  clearNotice(); choices("detail-model")[0].click(); await wait();
});
step("detail.idle.model_switched", async () => { held.shift()(); await wait(); });
step("detail.idle.model_unconfirmed", async () => {
  R["POST /api/sessions/echo/model"] = { status: 200, body: { status: "unconfirmed", model: "Sonnet 5" } };
  groupHeads("detail-model")[0].click(); await wait();
  clearNotice(); choices("detail-model")[0].click(); await wait();
});
step("detail.idle.model_refused", async () => {
  R["POST /api/sessions/echo/model"] = { status: 400, body: {} };
  api.openDetail("echo"); await wait();
  groupHeads("detail-model")[0].click(); await wait();
  clearNotice(); choices("detail-model")[0].click(); await wait();
});
step("detail.idle.model_unreachable", async () => {
  R["POST /api/sessions/echo/model"] = "reject";
  groupHeads("detail-model")[0].click(); await wait();
  clearNotice(); choices("detail-model")[0].click(); await wait();
});
step("detail.idle.menu", async () => { clearNotice(); el("detail-menu-btn").click(); await wait(); });
step("detail.idle.perms", async () => { modeItem("ask").click(); await wait(); });
step("detail.idle.kill", async () => {
  el("confirm-cancel").click(); el("detail-menu-btn").click(); menuItem("kill").click(); await wait();
});
step("detail.idle.killed", async () => {
  R["POST /api/sessions/echo/kill"] = { status: 200, body: { status: "killed" } };
  el("confirm-ok").click(); await wait();
});
step("detail.idle.already_gone", async () => {
  R["POST /api/sessions/echo/kill"] = { status: 404, body: { error: "not_found" } };
  await open("echo");
  el("detail-menu-btn").click(); menuItem("kill").click(); el("confirm-ok").click(); await wait();
});
step("detail.idle.diff_empty", async () => {
  R["GET /api/sessions/echo/diff"] = { status: 200, body: { available: true, files: [] } };
  await open("echo"); el("tab-diff").click(); await wait();
});
step("detail.idle.diff_missing", async () => {
  R["GET /api/sessions/echo/diff"] = { status: 404, body: { error: "not_found" } };
  await open("echo"); el("tab-diff").click(); await wait();
});
step("detail.idle.diff_error", async () => {
  R["GET /api/sessions/echo/diff"] = { status: 500, body: {} };
  await open("echo"); el("tab-diff").click(); await wait();
});
["not_a_git_repo", "git_not_installed", "cwd_missing", "no_commits_yet", "git_error",
 "something_else"].forEach((reason) => {
  step("detail.idle.diff_" + reason, async () => {
    R["GET /api/sessions/echo/diff"] = { status: 200, body: { available: false, reason } };
    await open("echo"); el("tab-diff").click(); await wait();
  });
});
step("detail.unknown", async () => { await open("foxtrot"); });
step("detail.unknown.diff_no_repo", async () => {
  R["GET /api/sessions/foxtrot/diff"] = { status: 200, body: { available: true, files: [] } };
  el("tab-diff").click(); el("tab-timeline").click(); await wait();
});
step("detail.gone", async () => { await open("old1"); });
step("detail.gone.menu", async () => { el("detail-menu-btn").click(); await wait(); });
step("detail.gone.delete", async () => { menuItem("delete").click(); await wait(); });
step("detail.gone.deleted", async () => {
  R["DELETE /api/sessions/old1"] = { status: 200, body: { status: "deleted" } };
  el("confirm-ok").click(); await wait();
});
step("detail.gone.resumed", async () => {
  R["GET /api/sessions/old1"] = { status: 200, body: Object.assign({}, DETAILS.old1, {
    actions: actions(["resume", "rename", "delete"]) }) };
  R["POST /api/sessions/old1/resume"] = { status: 200, body: { status: "resumed" } };
  await open("old1"); el("detail-menu-btn").click(); menuItem("resume").click(); await wait();
});
step("detail.gone.resumed_in_ask", async () => {
  R["POST /api/sessions/old1/resume"] = { status: 200, body: { status: "resumed",
    detail: sv("Resumed old1 in Ask: Auto needs an admin.") } };
  clearNotice(); el("detail-menu-btn").click(); menuItem("resume").click(); await wait();
});
step("detail.gone.delete_refused", async () => {
  R["DELETE /api/sessions/old1"] = { status: 409, body: { error: "conflict" } };
  clearNotice(); el("detail-menu-btn").click(); menuItem("delete").click();
  el("confirm-ok").click(); await wait();
});
// A session in Claude Code's background (claude-background-sessions): its
// state, its menu, a busy job's question in the app, Leave it, brought
// back, a failure; then the grid's count, and back where the steps were.
step("detail.background", async () => { await open("golf"); });
step("detail.background.menu", async () => { el("detail-menu-btn").click(); await wait(); });
step("detail.background.busy", async () => {
  el("overlay").click();
  R["POST /api/sessions/golf/bringback"] = { status: 409, body: { error: "bg_busy",
    can_force: true, detail: sv("golf is still working in Claude Code's background: stop " +
                                "that work and bring the conversation back here?") } };
  clearNotice(); el("detail-quick").click(); await wait();
});
step("detail.background.left", async () => { el("confirm-cancel").click(); await wait(); });
step("detail.background.forced", async () => {
  clearNotice(); el("detail-quick").click(); await wait();
  R["POST /api/sessions/golf/bringback"] = { status: 200,
    body: { status: "brought_back", label: "golf" } };
  el("confirm-ok").click(); await wait();
});
step("detail.background.failed", async () => {
  R["POST /api/sessions/golf/bringback"] = { status: 409, body: { error: "bg_stop_failed",
    detail: sv("Could not bring golf back from Claude Code's background: claude stop " +
               "did not answer in time") } };
  clearNotice(); el("detail-quick").click(); await wait();
});
step("grid.background", async () => {
  clearNotice();
  R["GET /api/sessions"] = { status: 200, body: grid([row("golf", "background"),
    row("echo", "idle"), row("old1", "gone")]) };
  api.showGrid(); api.pollTick(); await wait();
});
// Where the steps before were: the mixed grid, old1's page.
step("detail.gone.again", async () => {
  R["GET /api/sessions"] = { status: 200, body: grid(MIXED) };
  api.pollTick(); await wait();
  await open("old1");
});

// Settings and Updates.
step("settings", async () => { clearNotice(); el("maintab-settings").click(); await wait(); });
step("settings.app_language", async () => {
  groupHeads("settings-app-language")[0].click(); await wait();
});
step("settings.app_language_failed", async () => {
  R["PUT /api/app-language"] = { status: 500, body: {} };
  clearNotice(); choices("settings-app-language")[1].click(); await wait();
});
step("settings.saved", async () => {
  R["GET /api/preferences"] = { status: 200, body: prefs({ can_edit: true }) };
  R["PUT /api/preferences/layout"] = { status: 200, body: { values: { answer_length: "short", layout: "merged" } } };
  el("maintab-sessions").click(); el("maintab-settings").click(); await wait();
  clearNotice(); groupHeads("settings-groups")[1].click(); choices("settings-groups")[1].click(); await wait();
});
step("settings.save_failed", async () => {
  R["PUT /api/preferences/layout"] = { status: 500, body: {} };
  clearNotice(); choices("settings-groups")[0].click(); await wait();
});
step("updates.checking", async () => {
  R["POST /api/update/check"] = { hold: true, status: 200, body: { check: CHECKS.claude, job: null } };
  api.checkUpdates(); await wait();
});
step("updates.claude", async () => { held.shift()(); await wait(); });
step("updates.claude_start", async () => {
  R["POST /api/update/start"] = { status: 409, body: { error: "check_expired" } };
  clearNotice(); el("updates-actions").querySelectorAll("button")[0].click(); await wait();
});
step("updates.aipager", async () => {
  R["POST /api/update/check"] = { status: 200, body: { check: CHECKS.aipager, job: null } };
  api.checkUpdates(); await wait();
  confirmAnswer = false;
  el("updates-actions").querySelectorAll("button")[0].click(); await wait();
});
step("updates.none", async () => {
  R["POST /api/update/check"] = { status: 200, body: { check: CHECKS.none, job: null } };
  api.checkUpdates(); await wait();
});
step("updates.said", async () => {
  R["POST /api/update/check"] = { status: 200, body: { check: CHECKS.said, job: null } };
  api.checkUpdates(); await wait();
});
[[409, {}], [429, {}], [503, {}], [500, {}], ["reject"]].forEach((c, i) => {
  step("updates.check_refused_" + i, async () => {
    R["POST /api/update/check"] = c[0] === "reject" ? "reject" : { status: c[0], body: c[1] };
    clearNotice(); api.checkUpdates(); await wait();
  });
});
[[409, { error: "offer_changed" }], [409, { error: "update_in_progress" }], [409, { error: "done" }],
 [503, { error: "shutting_down" }], [429, {}], [500, {}], ["reject"]].forEach((c, i) => {
  step("updates.start_refused_" + i, async () => {
    R["POST /api/update/check"] = { status: 200, body: { check: CHECKS.claude, job: null } };
    api.checkUpdates(); await wait();
    R["POST /api/update/start"] = c[0] === "reject" ? "reject" : { status: c[0], body: c[1] };
    clearNotice(); el("updates-actions").querySelectorAll("button")[0].click(); await wait();
  });
});
[["waiting_for_idle", {}], ["gate_timeout", { blockers: [sv("x1 (working)")] }],
 ["starting", {}], ["claude_updating", {}], ["upgrading", { summary: sv("Upgrading now") }],
 ["some_new_phase", {}], ["done", { summary: sv("aipager is already up to date") }]].forEach((c) => {
  step("updates.job_" + c[0], async () => {
    R["GET /api/update"] = { status: 200, body: { job: job(c[0], c[1]) } };
    el("maintab-sessions").click(); el("maintab-settings").click(); await wait(); await wait();
  });
});
step("updates.job_restart_now", async () => {
  R["GET /api/update"] = { status: 200, body: { job: job("gate_timeout") } };
  el("maintab-sessions").click(); el("maintab-settings").click(); await wait(); await wait();
  confirmAnswer = false;
  el("updates-actions").querySelectorAll("button")[1].click(); await wait();
});
function restartRoutes(after) {
  R["GET /api/update"] = (req) => after.length ? after.shift() : { status: 200, body: { job: null, version: "0.7.18" } };
}
// Each watch is for its own job: the page keeps watching a job it
// already watches.
let restartJobId = 100;
function restartJob(extra) {
  return { status: 200, body: Object.assign({ job: job("restart_scheduled", {
    id: restartJobId, installed: { from: "0.7.17", to: "0.7.18" },
    summary: sv("aipager 0.7.17 → 0.7.18 installed") }),
    version: "0.7.17", restarted: null }, extra || {}) };
}
function restartWatch(name, after, extra, advance) {
  step(name, async () => {
    NOW = 1800000000000;
    restartJobId += 1;
    after = after.map((a) => (typeof a === "function" ? a() : a));
    dropTimers();
    restartRoutes([restartJob(extra)].concat(after));
    el("maintab-sessions").click(); el("maintab-settings").click(); await wait(); await wait();
    for (let i = 0; i < 6; i++) {
      if (advance) { NOW += advance; }
      runTimers((ms) => ms >= 1500 && ms <= 8000);
      await wait();
    }
  });
}
restartWatch("updates.restart_waiting", [restartJob, restartJob, restartJob, restartJob, restartJob, restartJob, restartJob]);
restartWatch("updates.restart_updated", [
  { status: 200, body: { job: null, version: "0.7.18", restarted: { to: "0.7.18", readopted: 3, missing: ["x2", "x3"] } } }]);
restartWatch("updates.restart_updated_one", [
  { status: 200, body: { job: null, version: "0.7.18", restarted: { to: "0.7.18", readopted: 1 } } }]);
restartWatch("updates.restart_other", [{ status: 200, body: { job: null, version: "0.7.99" } }]);
restartWatch("updates.restart_reopen", ["reject", "reject"], { url_changes_on_restart: true });
restartWatch("updates.restart_refused", [{ status: 401, body: {} }]);
restartWatch("updates.restart_slow", ["reject", "reject", "reject", "reject", "reject", "reject"], {}, 60000);

// The problem report.
step("report.open", async () => {
  R["GET /api/update"] = { status: 200, body: { job: null } };
  R["POST /api/report/draft"] = { status: 200, body: DRAFT };
  el("maintab-sessions").click(); el("maintab-settings").click(); await wait();
  el("report-open").click(); await wait(); await wait();
});
step("report.errors_all", async () => {
  el("rp-errors").querySelectorAll("button")[3].click(); await wait();
  el("rp-errors").querySelectorAll("button")[0].click(); await wait();
  el("rp-errors").querySelectorAll("button")[2].click(); await wait();
});
step("report.more", async () => { el("rp-more-btn").click(); el("rp-exact-btn").click(); await wait(); });
step("report.note", async () => { type("rp-note", "It broke"); await wait(); });
step("report.near", async () => { type("rp-note", "It broke when I tapped"); await wait(); });
step("report.too_long", async () => { type("rp-note", "It broke when I tapped Send twice"); await wait(); });
step("report.over_by_one", async () => { type("rp-note", "It broke when I tappe"); await wait(); });
step("report.sent", async () => {
  type("rp-note", "It broke");
  R["POST /api/report/send"] = { status: 200, body: { outcome: "sent", reference: "AP-1234",
    line: sv("Sent.") } };
  el("rp-send").click(); await wait();
});
step("report.copied", async () => { clearNotice(); el("rp-copy").click(); await wait(); });
async function reportOutcome(name, spec, sendAgain) {
  step(name, async () => {
    R["POST /api/report/draft"] = { status: 200, body: DRAFT };
    el("rp-done").click(); await wait();
    el("maintab-settings").click(); await wait();
    el("report-open").click(); await wait(); await wait();
    type("rp-note", "x");
    R["POST /api/report/send"] = spec;
    el("rp-send").click(); await wait(); await wait();
  });
}
reportOutcome("report.too_old", { status: 200, body: { outcome: "too_old", line: sv("Update aipager first.") } });
reportOutcome("report.daily_cap", { status: 200, body: { outcome: "daily_cap", line: sv("Try tomorrow.") } });
reportOutcome("report.not_sent", { status: 200, body: { outcome: "failed", line: sv("It failed, see `log`.") } });
reportOutcome("report.not_sent_no_line", { status: 200, body: { outcome: "invalid" } });
reportOutcome("report.retry", { status: 200, body: { outcome: "failed", retry: true } });
reportOutcome("report.stale", { status: 410, body: {} });
reportOutcome("report.sending", { status: 409, body: {} });
reportOutcome("report.tidied", { status: 422, body: { error: "note_changed", note: "y" } });
reportOutcome("report.bad_note", { status: 422, body: { error: "bad" } });
reportOutcome("report.too_many", { status: 429, body: {} });
reportOutcome("report.other", { status: 500, body: {} });
reportOutcome("report.no_answer", "reject");
reportOutcome("report.private_only", { status: 403, body: { error: "private_only" } });
reportOutcome("report.no_owner", { status: 409, body: { error: "no_owner" } });
step("report.back_while_sending", async () => {
  R["POST /api/report/draft"] = { status: 200, body: DRAFT };
  R["GET /api/preferences"] = { status: 200, body: prefs({ can_edit: true }) };
  el("maintab-sessions").click(); el("maintab-settings").click(); await wait();
  el("report-open").click(); await wait(); await wait();
  type("rp-note", "x");
  R["POST /api/report/send"] = { hold: true, status: 200, body: { outcome: "sent", reference: "AP-1" } };
  el("rp-send").click(); await wait();
  clearNotice(); global.__back(); await wait();
});
step("report.after_send", async () => { held.shift()(); await wait(); });
step("report.build_failed", async () => {
  R["POST /api/report/draft"] = { status: 500, body: {} };
  el("rp-done").click(); await wait();
  el("maintab-settings").click(); await wait();
  el("report-open").click(); await wait(); await wait();
});
step("report.not_owner", async () => {
  R["POST /api/report/draft"] = { status: 403, body: { error: "forbidden" } };
  el("rp-done").click(); await wait(); await wait();
});

// The new-session form.
step("new.open", async () => {
  R["GET /api/preferences"] = { status: 200, body: prefs() };
  R["GET /api/report/draft"] = undefined;
  clearNotice(); el("maintab-sessions").click(); await wait();
  el("new-session-btn").click(); await wait();
  ["new-name", "new-model-name", "new-folder-name"].forEach((id) => { el(id).value = ""; });
});
step("new.groups", async () => {
  el("new-advanced-toggle").click();
  [["new-model", 0], ["new-cwd", 0], ["new-mode", 0], ["new-prefs", 0]].forEach((g) => openGroup(g[0], g[1]));
  await wait();
});
step("new.named", async () => { type("new-name", "zulu"); await wait(); });
step("new.custom_model", async () => {
  choices("new-model")[choices("new-model").length - 1].click(); await wait();
});
step("new.custom_model_bad", async () => { type("new-model-name", "-bad"); await wait(); });
step("new.custom_model_ok", async () => { type("new-model-name", "claude-opus-5"); await wait(); });
step("new.folder", async () => {
  const rows = choices("new-cwd");
  rows[rows.length - 1].click(); await wait();
});
step("new.folder_busy", async () => {
  R["POST /api/directories"] = { hold: true, status: 200, body: { path: "/home/op/proj/zz", existed: false } };
  type("new-folder-name", "zz"); el("new-folder-create").click(); await wait();
});
step("new.folder_created", async () => { clearNotice(); held.shift()(); await wait(); });
[["existed", { status: 200, body: { path: "/home/op/proj/zz", existed: true } }],
 ["forbidden", { status: 403, body: { error: "forbidden" } }],
 ["failed", { status: 400, body: { error: "bad_request" } }],
 ["refused", { status: 400, body: { error: "bad_request", detail: sv("Folder name can't be empty.") } }],
 ["unreachable", "reject"]].forEach((c) => {
  step("new.folder_" + c[0], async () => {
    const rows = choices("new-cwd");
    if (el("new-folder-reveal").parentNode === el("node-stash")) { rows[rows.length - 1].click(); }
    await wait();
    R["POST /api/directories"] = c[1];
    clearNotice(); type("new-folder-name", "zz"); el("new-folder-create").click(); await wait();
  });
});
step("new.full", async () => {
  R["GET /api/session-options"] = { status: 200, body: Object.assign({}, OPTIONS, { can_use_full: true }) };
  api.showGrid(); await wait();
  el("new-session-btn").click(); await wait();
  openGroup("new-mode", 0); await wait();
  choices("new-mode")[2].click(); await wait();
});
step("new.default_dir", async () => {
  R["GET /api/session-options"] = { status: 200, body: Object.assign({}, OPTIONS, {
    directories: [], default_directory: "", models: [] }) };
  api.showGrid(); await wait();
  el("new-session-btn").click(); await wait();
  type("new-name", "");
});
[["conflict", { status: 409, body: { error: "conflict", detail: sv("A session named zulu already exists in this chat.") } }],
 ["forbidden", { status: 403, body: { error: "forbidden" } }],
 ["failed", { status: 400, body: { error: "launch_failed" } }],
 ["unreachable", "reject"]].forEach((c) => {
  step("new.create_" + c[0], async () => {
    R["POST /api/sessions"] = c[1];
    type("new-name", "zulu"); el("new-create").click(); await wait();
  });
});
step("new.created", async () => {
  R["POST /api/sessions"] = { status: 200, body: { label: "charlie" } };
  clearNotice(); type("new-name", "charlie"); el("new-create").click(); await wait(20);
});

// The connection.
step("conn.reconnecting", async () => {
  api.showGrid(); await wait();
  R["GET /api/sessions"] = "reject";
  api.pollTick(); await wait();
});
step("conn.offline", async () => { api.pollTick(); await wait(); api.pollTick(); await wait(); });
step("conn.back", async () => {
  R["GET /api/sessions"] = { status: 200, body: grid(MIXED) };
  api.pollTick(); await wait();
});
step("conn.expired", async () => {
  R["GET /api/sessions"] = { status: 401, body: { error: "unauthorized" } };
  api.pollTick(); await wait();
});

// ---- running --------------------------------------------------------------------------

async function screensRun(only) {
  screens.push(BOOT);
  for (const [name, fn] of STEPS) {
    try {
      await fn();
    } catch (e) {
      fail(name + ": " + (e && e.stack));
    }
    if (!only || only(name)) { snap(name); }
  }
}

// The direction of the places that hold names, paths, code or Claude's words.
function islands() {
  const first = (sel) => { const n = q(sel)[0]; return n ? n.getAttribute("dir") : "missing"; };
  const timeline = el("panel-timeline").innerHTML;
  return {
    tile_label: first(".tile-label"), tile_meta: first(".tile-meta"),
    beacon_label: first(".beacon-label"), beacon_summary: first(".beacon-summary"),
    shelf_label: first(".shelf-label"), shelf_meta: first(".shelf-meta"),
    detail_label: el("detail-label").getAttribute("dir"),
    preview: el("detail-preview").getAttribute("dir"),
    waiting_text: el("detail-waiting-text").getAttribute("dir"),
    fact_cwd: (q(".fact-mono")[0] && q(".fact-mono")[0].children
      .filter((c) => c.tagName === "DD")[0].getAttribute("dir")) || "missing",
    timeline_tool: /timeline-tool[^>]*dir="ltr"/.test(timeline) ? "ltr" : "missing",
    rp_exact: el("rp-exact").getAttribute("dir"), rp_ref: el("rp-ref").getAttribute("dir"),
    daemon_line: el("daemon-line").getAttribute("dir"),
    new_name: el("new-name").getAttribute("dir"),
    new_model_name: el("new-model-name").getAttribute("dir"),
    new_folder_name: el("new-folder-name").getAttribute("dir"),
    rename_input: el("confirm-rename-input").getAttribute("dir"),
    chat_chip: first(".chat-chip"),
    // Not a direction: the ring's percent sign, in the language's sign.
    pct_sign: q(".lamp-pct")[0] ? q(".lamp-pct")[0].textContent : "missing",
  };
}

function result(extra) {
  const out = Object.assign({
    screens,
    lang: htmlEl.getAttribute("lang"),
    dir: htmlEl.getAttribute("dir"),
    calls: calls.map((c) => c.method + " " + c.url),
    imgs: maxImgs,
    islands: islands(),
    storage: STORE,
    served: Array.from(served),
  }, extra || {});
  // Synchronous: process.exit would cut an asynchronous pipe write short.
  fs.writeSync(1, "RESULT " + JSON.stringify(out) + "\n");
  process.exit(0);
}

async function main() {
  if (OPTS.noInit) {
    await wait();
    snap("no_init");
    result();
    return;
  }
  if (SCENARIO === "screens") {
    await screensRun();
    result();
    return;
  }
  if (global.__i18nScenario) { await global.__i18nScenario(); return; }
  fail("unknown scenario: " + SCENARIO);
}

// Scenarios that drive one mechanism rather than every screen.
const SCENARIOS = {};
global.__i18nScenario = null;

// Every element the markup marks, as the page shows it now.
function staticTexts() {
  return selectAll(bodyEl, "[data-i18n],[data-i18n-label],[data-i18n-placeholder],[data-i18n-num]")
    .map((n) => [n.attrs.id || n.attrs["data-i18n"] || n.attrs["data-i18n-label"] ||
                 n.attrs["data-i18n-placeholder"] || n.attrs["data-i18n-num"],
                 n.textContent, n.getAttribute("aria-label"), n.getAttribute("placeholder")]);
}

// The App language row: English, then the language OPTS.switchTo names,
// then English again, all without a reload.
SCENARIOS.live_switch = async () => {
  await wait();
  el("maintab-settings").click(); await wait();
  const english = staticTexts();
  const before = calls.length;
  R["PUT /api/app-language"] = { status: 200, body: { value: OPTS.switchTo.code,
    effective: OPTS.switchTo.code, confirmation: "CONFIRMED", ui: OPTS.switchTo } };
  R["GET /api/preferences"] = { status: 200, body: Object.assign(prefs(), { ui: OPTS.switchTo }) };
  clearNotice();
  openGroup("settings-app-language", 0); await wait();
  choices("settings-app-language")[3].click(); await wait(); await wait(); await wait();
  const switched = { lang: htmlEl.getAttribute("lang"), dir: htmlEl.getAttribute("dir"),
                     calls: calls.slice(before).map((c) => c.method + " " + c.url),
                     notice: el("notice").textContent, texts: staticTexts(),
                     screen: visible(bodyEl, []) };
  const mid = calls.length;
  R["PUT /api/app-language"] = { status: 200, body: { value: "en", effective: "en",
    confirmation: "BACK" } };
  R["GET /api/preferences"] = { status: 200, body: prefs() };
  clearNotice();
  openGroup("settings-app-language", 0); await wait();
  choices("settings-app-language")[1].click(); await wait(); await wait(); await wait();
  const back = { lang: htmlEl.getAttribute("lang"), dir: htmlEl.getAttribute("dir"),
                 calls: calls.slice(mid).map((c) => c.method + " " + c.url),
                 notice: el("notice").textContent, texts: staticTexts(),
                 screen: visible(bodyEl, []) };
  result({ live: { english, switched, back } });
};

// Two switches whose catalogs answer out of order: OPTS.first (its catalog
// held), then OPTS.then (a `ui` entry, or nothing for English); the first
// catalog answers last and must not take the page back.
SCENARIOS.stale_switch = async () => {
  await wait();
  el("maintab-settings").click(); await wait();
  const first = OPTS.first;
  R["GET /api/i18n/" + first.code + ".json"] = "hold";
  R["PUT /api/app-language"] = { status: 200, body: { value: first.code,
    effective: first.code, confirmation: "FIRST", ui: first } };
  R["GET /api/preferences"] = { status: 200, body: Object.assign(prefs(), { ui: first }) };
  openGroup("settings-app-language", 0); await wait();
  choices("settings-app-language")[3].click(); await wait(); await wait();
  const waiting = held.length;
  const then = OPTS.then || null;
  R["PUT /api/app-language"] = { status: 200, body: Object.assign({
    value: then ? then.code : "en", effective: then ? then.code : "en", confirmation: "THEN" },
    then ? { ui: then } : {}) };
  R["GET /api/preferences"] = { status: 200, body: Object.assign(prefs(), then ? { ui: then } : {}) };
  openGroup("settings-app-language", 0); await wait();
  choices("settings-app-language")[then ? 2 : 1].click(); await wait(); await wait(); await wait();
  const before = { lang: htmlEl.getAttribute("lang"), dir: htmlEl.getAttribute("dir"),
                   storage: Object.assign({}, STORE) };
  // Everything the late answer sets off: the language the page puts on
  // <html> (even for a moment) and every request it makes.
  const langs = [];
  const setAttribute = htmlEl.setAttribute;
  htmlEl.setAttribute = function (k, v) {
    if (k === "lang") { langs.push(String(v)); }
    return setAttribute.call(this, k, v);
  };
  const mark = calls.length;
  while (held.length) { held.shift()(); await wait(); }
  await wait(); await wait();
  htmlEl.setAttribute = setAttribute;
  result({ stale: { waiting, before, langs,
                    calls: calls.slice(mark).map((c) => c.method + " " + c.url) } });
};

// The daemon line before the first grid answer (OPTS.holdGrid) and after.
SCENARIOS.loading = async () => {
  await wait(); await wait(); await wait();
  const line = () => ({ dir: el("daemon-line").getAttribute("dir"),
                        text: el("daemon-line").textContent });
  const before = line();
  const waiting = held.length;
  while (held.length) { held.shift()(); await wait(); }
  await wait();
  result({ loading: { before, waiting, after: line() } });
};

// The first frame of a page served for a link's language (?lang=): the
// markup before the script ran, the page right after it ran (no answer
// yet) and once the grid and the server's language arrived.
SCENARIOS.first_paint = async () => {
  await wait(); await wait(); await wait();
  snap("grid");
  result({ first: { pre: PRE, boot: Object.assign({ texts: BOOT.texts }, BOOT_STATE),
                    statics: staticTexts() } });
};

// Counted strings at every count OPTS.counts names.
SCENARIOS.plurals = async () => {
  await wait();
  const out = {};
  for (const n of OPTS.counts) {
    const rows = [];
    for (let i = 0; i < n; i++) { rows.push(row("w" + i, "waiting", { waiting_kind: "question" })); }
    rows.push(row("g1", "gone"));
    R["GET /api/sessions"] = { status: 200, body: grid(rows) };
    api.showGrid(); await wait();
    const need = q(".pulse-need")[0].textContent;
    const tl = [];
    for (let i = 0; i < n; i++) {
      tl.push({ kind: "tool", text: "Read a" + i, state: "done", elapsed_seconds: null });
    }
    R["GET /api/sessions/qd"] = { status: 200, body: detail("qd", "busy",
      { queue_depth: n, timeline: tl, busy_elapsed_seconds: 5 }) };
    api.openDetail("qd"); await wait();
    const queued = q(".act-chip")[0].textContent;
    const timeline = el("tab-timeline").textContent;
    const errs = [];
    for (let i = 0; i < n + 3; i++) {
      errs.push({ type: "builtins.ValueError", count: 2, last_day: "2026-10-04",
                  first_day: "2026-10-01", fingerprint: "f" + i, versions_seen: ["0.7.17"],
                  frames: [], external: [] });
    }
    R["POST /api/report/draft"] = { status: 200, body: { draft: "d" + n,
      report: report({ errors: errs }), areas: [], note_max: 5 } };
    R["GET /api/preferences"] = { status: 200, body: prefs() };
    api.showGrid(); el("maintab-settings").click(); await wait();
    el("report-open").click(); await wait(); await wait();
    const errors = el("rp-err-title").textContent;
    const buttons = el("rp-errors").querySelectorAll("button");
    const more = buttons[buttons.length - 1].textContent;
    type("rp-note", "x".repeat(5 + n)); await wait();
    const tooLong = el("rp-hint").textContent;
    el("maintab-sessions").click(); await wait();
    out[n] = { need, queued, timeline, errors, more, tooLong };
  }
  result({ plurals: out });
};
module.exports = {};
setTimeoutReal(() => {
  if (SCENARIOS[SCENARIO]) { global.__i18nScenario = SCENARIOS[SCENARIO]; }
  main();
}, 0);
