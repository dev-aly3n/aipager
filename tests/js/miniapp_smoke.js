const setTimeoutReal = setTimeout;
// Minimal DOM shim: enough to run the Mini App's script and simulate taps.
const fs = require("fs");
const page = fs.readFileSync(process.argv[2], "utf8");
const SCENARIO_ARG = process.argv[3] || "settings";

// Every DOM write the page makes through this shim, so a scenario can
// assert that an unchanged poll touches nothing at all.
global.__writes = 0;

class El {
  constructor(tag) {
    this.tagName = (tag || "div").toUpperCase();
    this.children = []; this.listeners = {}; this.attrs = {};
    this._class = ""; this._text = ""; this._html = "";
    this.hidden = false; this.disabled = false; this.style = {};
    // These are ARROW functions, so `this` is the El instance itself, not
    // the classList object — an earlier version read `this._el._class`,
    // which is undefined here and made add/remove/contains throw. Nothing
    // exercised them until the notice toast started using classList, so
    // the shim had been quietly broken the whole time.
    this.classList = {
      add: (c) => { if (!this._class.split(" ").includes(c)) this._class += " " + c; },
      remove: (c) => { this._class = this._class.split(" ").filter(x => x !== c).join(" "); },
      toggle: (c, on) => { on ? this.classList.add(c) : this.classList.remove(c); },
      contains: (c) => this._class.split(" ").includes(c),
    };
  }
  get className() { return this._class; }
  set className(v) { global.__writes++; this._class = v; }
  // Real textContent is the concatenation of ALL descendant text, and
  // assigning it REPLACES the children. The shim used to return only the
  // node's own text, so any element built from child spans (the notice
  // toast, once it grew an icon) read back as "" and every assertion on
  // its message silently saw an empty string.
  get textContent() {
    if (this.children.length) {
      return this.children.map((c) => c.textContent).join("");
    }
    return this._text;
  }
  set textContent(v) {
    global.__writes++;
    for (const c of this.children) { c.parent = null; }
    this._text = String(v); this.children = [];
  }
  get innerHTML() { return this._html; }
  set innerHTML(v) {
    global.__writes++;
    for (const c of this.children) { c.parent = null; }
    this._html = v; this.children = [];
  }
  get parentNode() { return this.parent || null; }
  // Real appendChild MOVES a node that already has a parent. The page
  // relies on that: reveals are parked in a stash and moved into place.
  appendChild(c) {
    global.__writes++;
    if (c.parent) {
      const i = c.parent.children.indexOf(c);
      if (i >= 0) { c.parent.children.splice(i, 1); }
    }
    this.children.push(c); c.parent = this; return c;
  }
  insertBefore(c, ref) {
    if (!ref) { return this.appendChild(c); }
    global.__writes++;
    if (c.parent) {
      const i = c.parent.children.indexOf(c);
      if (i >= 0) { c.parent.children.splice(i, 1); }
    }
    const at = this.children.indexOf(ref);
    this.children.splice(at < 0 ? this.children.length : at, 0, c);
    c.parent = this; return c;
  }
  removeChild(c) {
    global.__writes++;
    const i = this.children.indexOf(c);
    if (i < 0) { throw new Error("removeChild: not a child"); }
    this.children.splice(i, 1); c.parent = null; return c;
  }
  remove() { if (this.parent) { this.parent.removeChild(this); } }
  focus() { this.focused = true; }
  setAttribute(k, v) { global.__writes++; this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  click() { (this.listeners.click || []).forEach(f => f.call(this, {})); }
  querySelectorAll() { return []; }
  // depth-first walk
  *walk() { yield this; for (const c of this.children) yield* c.walk(); }
}

const byId = {};
// ids the page references. The `hidden` attribute is carried over from the
// markup: an element the shim starts VISIBLE that the page starts hidden
// makes every "did it become visible?" assertion vacuously true.
for (const m of page.matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g)) {
  const el = new El("div");
  el.hidden = /\shidden(\s|>|=)/.test(m[0]);
  byId[m[1]] = el;
}
for (const m of page.matchAll(/getElementById\("([^"]+)"\)/g))
  if (!byId[m[1]]) byId[m[1]] = new El("div");

global.document = {
  getElementById: (id) => byId[id] || (byId[id] = new El("div")),
  createElement: (t) => new El(t),
  addEventListener: () => {},
  querySelectorAll: () => [],
  visibilityState: "visible",
  documentElement: new El("html"),
  body: new El("body"),
};
const webApp = {
  initData: "auth_date=1&user=%7B%22id%22%3A1%7D&hash=x",
  ready(){}, expand(){},
  // Capture the handler so a scenario can press Back for real. The
  // page registers it once at startup; discarding it here made the
  // back-button behaviour untestable.
  BackButton: { show(){}, hide(){}, onClick(fn){ global.__back = fn; } },
  HapticFeedback: { notificationOccurred(){} },
};
// The default mock stays exactly the old, minimal one: every newer
// Telegram API the page uses must be optional. Scenarios that need more
// opt in by name.
if (SCENARIO_ARG.indexOf("answer_") === 0) {
  webApp.close = () => { global.__closed = true; };
  webApp.HapticFeedback.impactOccurred = () => {};
}

// Opt-in: the newer Telegram APIs (MainButton, theme events, swipes).
global.__mbParams = {};
global.__events = {};
global.__swOff = 0;
global.__swOn = 0;
function withFullTelegram(w) {
  w.colorScheme = "light";
  w.isVersionAtLeast = () => true;
  w.onEvent = (name, fn) => { global.__events[name] = fn; };
  w.setHeaderColor = (c) => { global.__header = c; };
  w.setBackgroundColor = (c) => { global.__background = c; };
  w.disableVerticalSwipes = () => { global.__swOff++; };
  w.enableVerticalSwipes = () => { global.__swOn++; };
  w.MainButton = {
    setParams(p) { global.__mbParams = Object.assign({}, global.__mbParams, p); },
    onClick(fn) { global.__main = fn; },
    showProgress() { global.__mbProgress = true; },
    hideProgress() { global.__mbProgress = false; },
  };
}
if (SCENARIO_ARG === "detail_waiting_mainbutton" || SCENARIO_ARG === "theme_changed") {
  withFullTelegram(webApp);
}
global.window = { Telegram: { WebApp: webApp } };
// FLIP needs all three of these; only one scenario provides them.
global.__raf = 0;
if (SCENARIO_ARG === "grid_reorder_flip") {
  global.window.requestAnimationFrame = (fn) => { global.__raf++; fn(); return 1; };
  global.window.matchMedia = () => ({ matches: false });
  El.prototype.getBoundingClientRect = function () {
    const i = this.parent ? this.parent.children.indexOf(this) : 0;
    return { left: (i % 2) * 200, top: Math.floor(i / 2) * 150, width: 190, height: 132 };
  };
}
global.Telegram = global.window.Telegram;
const fetchCalls = [];
let DETAIL_OVERRIDE = null;   // see driveMenuDriftCloses
// A scenario can make ONE specific write route answer something other
// than its default FIXTURES entry — used by driveRenameServerConflict
// to model the server refusing a rename with a 409.
let POST_STATUS_OVERRIDE = null; // { path, method, status, body }
// A scenario can make every fetch of one path fail at the network level
// (the tunnel dropped), the way a real fetch rejects with a TypeError.
let FETCH_REJECT_PATH = null;
// A scenario can hold every PUT under one path prefix until it settles it
// itself (heldPuts[i].settle(status, body)), to answer writes out of order.
let HOLD_PUT_PREFIX = null;
const heldPuts = [];
// A scenario can have every fixture answered as a fresh copy, so a page
// that edits what it was given does not edit the next answer too.
let COPY_FIXTURES = false;
global.fetch = (url, opts) => {
  const method = (opts && opts.method) || "GET";
  fetchCalls.push({ url, method, body: opts && opts.body,
                   headers: (opts && opts.headers) || {} });
  const path = url.split("?")[0];
  if (HOLD_PUT_PREFIX && method === "PUT" && path.indexOf(HOLD_PUT_PREFIX) === 0) {
    return new Promise(resolve => heldPuts.push({ path, body: opts.body,
      settle: (status, body) => resolve({ ok: status < 400, status,
                                          json: () => Promise.resolve(body) }) }));
  }
  if (FETCH_REJECT_PATH && path === FETCH_REJECT_PATH) {
    return Promise.reject(new TypeError("Failed to fetch"));
  }
  if (POST_STATUS_OVERRIDE && path === POST_STATUS_OVERRIDE.path
      && method === POST_STATUS_OVERRIDE.method) {
    const override = POST_STATUS_OVERRIDE;
    return Promise.resolve({
      ok: override.status < 400, status: override.status,
      json: () => Promise.resolve(override.body),
    });
  }
  return Promise.resolve({
    ok: true, status: 200,
    json: () => {
      // A scenario can make the NEXT poll of the session detail return
      // something else, to model the session changing underneath.
      if (DETAIL_OVERRIDE && path === "/api/sessions/dev") {
        return Promise.resolve(DETAIL_OVERRIDE);
      }
      const fixture = FIXTURES[path] || {};
      return Promise.resolve(COPY_FIXTURES ? JSON.parse(JSON.stringify(fixture)) : fixture);
    },
  });
};
global.setInterval = () => 0;
// Recorded (never run): a scenario can assert what the page SCHEDULED,
// e.g. the Updates block's 3 s poll.
const timeoutCalls = [];
global.setTimeout = (f, ms) => { timeoutCalls.push(ms); return 0; };
global.clearTimeout = () => {};
global.clearInterval = () => {};
global.Telegram = undefined;

// Which interaction this run drives. One harness, several scenarios: the
// existing settings-panel flow (default, when no scenario is given — so
// every pre-existing invocation of this harness needs no changes), plus
// one per session detail-page write action (design.md: "Mini App session
// controls").
const SCENARIO = SCENARIO_ARG;

// Mirrors aipager.miniapp.sessions.NO_TRANSCRIPT_REASON — this
// harness has no Python import, so the string is duplicated here on
// purpose; a mismatch would only ever show up as a failing assertion
// below, never silently. It (and the other mirrored reasons below) keeps
// the em dash the server dropped in roadmap 8.54, standing in for
// pass-through text, so the page's plain() stays exercised.
const NO_TRANSCRIPT_REASON =
  "No resumable transcript — start a fresh session instead.";
// The page shows server strings with " - " in place of an em dash
// (roadmap 8.44: no em dash in page text), so every mirrored reason is
// compared through the same normalisation.
function plain(s) {
  return String(s).replace(/ \u2014 /g, " - ").replace(/\u2014/g, "-");
}

function actionsFor(status, resumable) {
  if (status === "busy" || status === "waiting") {
    return { stop: { available: true, reason: null } };
  }
  if (status === "idle") {
    return { kill: { available: true, reason: null } };
  }
  if (status === "gone") {
    return {
      resume: resumable
        ? { available: true, reason: null }
        : { available: false, reason: NO_TRANSCRIPT_REASON },
      delete: { available: true, reason: null },
    };
  }
  return {};
}

function detailFor(status, resumable) {
  return {
    label: "dev", status: status, waiting_kind: null, waiting_summary: null,
    model: "", context_pct: 0, cost_usd: 0, cwd: "",
    last_active_seconds_ago: null, busy_elapsed_seconds: null,
    last_message: "", timeline: [], facts: [],
    actions: actionsFor(status, resumable),
  };
}

// Only the scenario actually being driven needs a session on the page —
// every other key here is dead weight for that run, which is fine: a
// single one is picked below by SCENARIO.
const SESSION_DETAIL_FIXTURES = {
  stop_busy: detailFor("busy", false),
  kill_idle: detailFor("idle", false),
  resume_gone: detailFor("gone", true),
  resume_gone_no_transcript: detailFor("gone", false),
  delete_gone: detailFor("gone", true),
  modal_back_closes: detailFor("gone", true),
  backdrop_cancels: detailFor("gone", true),
  // status the daemon has never characterised -> no actions at all
  no_actions: Object.assign(detailFor("gone", true), { status: "unknown", actions: {} }),
  menu_drift_closes: detailFor("idle", false),
  confirm_isolation: detailFor("idle", false),
};

// ---- Mini App session MENU actions (perms/clearqueue/compact/restart/
//      rename) fixtures -----------------------------------------------
//
// Deliberately a SEPARATE builder from actionsFor()/detailFor() above,
// rather than extending those: the four original scenarios (stop_busy,
// kill_idle, ...) assert an EXACT single-item menu, and giving
// actionsFor() the new keys would silently grow those menus to six
// items and break assertions that were never meant to exercise this
// batch at all.
//
// Mirrors aipager.miniapp.sessions.{PERMS_ADMIN_REQUIRED_REASON,
// QUEUE_EMPTY_REASON,QUEUE_FULL_REASON} verbatim, same rationale as
// NO_TRANSCRIPT_REASON above.
const PERMS_ADMIN_REQUIRED_REASON = "Switching to Full access mode requires admin.";
const QUEUE_EMPTY_REASON = "Nothing queued to clear.";
const QUEUE_FULL_REASON =
  "Queue is full (50 pending) — clear it or wait for it to drain.";
const QUEUE_CAP = 50;

// The perms slot is the mode menu: its own entry is always on offer, and
// which modes it offers is `modes` (Full access mode needs an admin), as
// aipager.miniapp.sessions.mode_options builds it.
const MODE_LABELS = { ask: "💬 Ask mode", auto: "🤖 Auto mode", full: "🔓 Full access mode" };
function modesFor(opts) {
  const current = opts.mode || "ask";
  return ["ask", "auto", "full"].map(function (id) {
    const refused = id === "full" && !opts.isAdmin;
    return { id: id, label: MODE_LABELS[id], current: id === current,
             available: !refused, reason: refused ? PERMS_ADMIN_REQUIRED_REASON : null,
             restarts: id === "full" || current === "full" };
  });
}

function fullActionsFor(status, opts) {
  opts = opts || {};
  const queueDepth = opts.queueDepth || 0;
  const resumable = !!opts.resumable;

  const permsEntry = { available: true, reason: null };

  if (status === "busy" || status === "waiting") {
    const out = {
      stop: { available: true, reason: null },
      clearqueue: queueDepth > 0
        ? { available: true, reason: null }
        : { available: false, reason: QUEUE_EMPTY_REASON },
      rename: { available: true, reason: null },
      perms: permsEntry,
      restart: { available: true, reason: null },
    };
    if (status === "busy") {
      out.compact = queueDepth >= QUEUE_CAP
        ? { available: false, reason: QUEUE_FULL_REASON }
        : { available: true, reason: null };
    }
    return out;
  }
  if (status === "tasks") {
    // Roadmap 8.121: idle, but its job's agents or shells still run
    // (sessions._RELEVANT_ACTIONS["tasks"]).
    return {
      stop: { available: true, reason: null },
      compact: { available: true, reason: null },
      rename: { available: true, reason: null },
      kill: { available: true, reason: null },
      perms: permsEntry,
      restart: { available: true, reason: null },
    };
  }
  if (status === "idle") {
    return {
      compact: { available: true, reason: null },
      rename: { available: true, reason: null },
      kill: { available: true, reason: null },
      perms: permsEntry,
      restart: { available: true, reason: null },
    };
  }
  if (status === "gone") {
    return {
      resume: resumable
        ? { available: true, reason: null }
        : { available: false, reason: NO_TRANSCRIPT_REASON },
      rename: { available: true, reason: null },
      delete: { available: true, reason: null },
    };
  }
  return {};
}

function fullDetailFor(status, opts) {
  opts = opts || {};
  return {
    label: "dev", status: status, waiting_kind: null, waiting_summary: null,
    model: "", context_pct: 0, cost_usd: 0, cwd: "",
    last_active_seconds_ago: null, busy_elapsed_seconds: null,
    mode: opts.mode || "ask", mode_pending: false, modes: modesFor(opts),
    queue_depth: opts.queueDepth || 0,
    last_message: "", timeline: [], facts: [],
    actions: fullActionsFor(status, opts),
  };
}

Object.assign(SESSION_DETAIL_FIXTURES, {
  perms_idle: fullDetailFor("idle", { isAdmin: true, mode: "ask" }),
  perms_busy: fullDetailFor("busy", { isAdmin: true, mode: "ask" }),
  perms_tasks: Object.assign(fullDetailFor("tasks", { isAdmin: true, mode: "ask" }),
                             { job_agents: 2, job_shells: 0 }),
  perms_full_requires_admin: fullDetailFor("idle", { isAdmin: false, mode: "ask" }),
  perms_in_place: fullDetailFor("busy", { isAdmin: false, mode: "ask" }),
  perms_leave_full: fullDetailFor("idle", { isAdmin: false, mode: "full" }),
  restart_idle: fullDetailFor("idle", { isAdmin: true }),
  restart_busy: fullDetailFor("busy", { isAdmin: true }),
  clearqueue_busy: fullDetailFor("busy", { isAdmin: true, queueDepth: 3 }),
  compact_busy_queues: fullDetailFor("busy", { isAdmin: true, queueDepth: 2 }),
  compact_idle_sends: fullDetailFor("idle", { isAdmin: true }),
  compact_queue_full: fullDetailFor("busy", { isAdmin: true, queueDepth: QUEUE_CAP }),
  rename_valid: fullDetailFor("idle", { isAdmin: true }),
  rename_client_side_invalid: fullDetailFor("idle", { isAdmin: true }),
  rename_server_conflict: fullDetailFor("idle", { isAdmin: true }),
  menu_grouping_divider: fullDetailFor("busy", { isAdmin: true, queueDepth: 3 }),
});

// ---- running-session model picker (roadmap 8.35) ---------------------
// Mirrors aipager.miniapp.sessions.MODEL_SWITCH_BUSY_REASON (em dash kept,
// see NO_TRANSCRIPT_REASON).
const MODEL_SWITCH_BUSY_REASON =
  "Claude is working — switch the model when this turn ends.";
function modelDetailFor(status, modelSwitch) {
  return Object.assign(fullDetailFor(status, { isAdmin: true }), {
    model: "Sonnet 5", model_switch: modelSwitch,
  });
}
Object.assign(SESSION_DETAIL_FIXTURES, {
  model_switch: modelDetailFor("idle", { available: true, reason: null }),
  model_unconfirmed: modelDetailFor("idle", { available: true, reason: null }),
  model_busy: modelDetailFor("busy", {
    available: false, reason: MODEL_SWITCH_BUSY_REASON }),
});

// ---- the session page header and activity strip (roadmap 8.44) ------
Object.assign(SESSION_DETAIL_FIXTURES, {
  detail_activity: Object.assign(fullDetailFor("busy", { isAdmin: true, queueDepth: 2 }), {
    context_pct: 57, busy_elapsed_seconds: 192,
    last_message: "Ran `pytest` and <b>fixed</b> it.",
    timeline: [
      { kind: "commentary", text: "Reading first." },
      { kind: "tool", text: "Read a.py", state: "done", elapsed_seconds: null },
      { kind: "tool", text: "Bash: pytest", state: "failed", elapsed_seconds: null },
      { kind: "tool", text: "Edit a.py", state: "done", elapsed_seconds: null },
      { kind: "tool", text: "Bash: pytest -q", state: "running", elapsed_seconds: 14 },
    ],
  }),
  detail_quick_stop: detailFor("busy", false),
  detail_quick_resume: detailFor("gone", true),
  detail_unchanged: fullDetailFor("busy", { isAdmin: true }),
  bg_quick_bringback: backgroundDetail(),
  bg_busy_asks_in_app: backgroundDetail(),
  bg_busy_leave_it: backgroundDetail(),
  bg_restart_busy_asks: backgroundDetail(),
  bg_menu: backgroundDetail(),
  // A session not parked whose conversation a busy background job holds:
  // its mode switch (a restart) is answered bg_busy too.
  bg_perms_busy_asks: fullDetailFor("idle", { isAdmin: true, mode: "ask" }),
});

// A session whose conversation moved into a Claude Code background job
// (sessions.session_actions for "background", nothing running here).
function backgroundDetail() {
  return Object.assign(detailFor("background", false), {
    parked: true, parked_seconds_ago: 90,
    actions: {
      bringback: { available: true, reason: null },
      stop: { available: false, reason: "This session isn't busy right now - nothing to stop." },
      rename: { available: true, reason: null },
      kill: { available: true, reason: null },
      restart: { available: true, reason: null },
    },
  });
}

const SCHEMA = [
  { section: "length", field: "answer_length", title: "Answer length",
    options: [
      { value: "none", label: "Don't apply any rule", help: "" },
      { value: "short", label: "Short", help: "" },
      { value: "medium", label: "Medium", help: "" },
    ] },
];
const FIXTURES = {
  "/api/sessions/dev/preferences/answer_length": {
    values: {
      answer_length: { effective: "short", scope_default: "none",
                       override_value: "short", overridden: true },
    },
    changed: true,
  },
  "/api/sessions/dev/preferences": (SCENARIO === "reset_confirm" ||
                                   SCENARIO === "confirm_isolation")
    ? {
      schema: SCHEMA,
      // Overridden, so "Reset to defaults" is actually offered.
      values: {
        answer_length: { effective: "short", scope_default: "none",
                         override_value: "short", overridden: true },
      },
      can_edit: true,
    }
    : {
      schema: SCHEMA,
      values: {
        answer_length: { effective: "none", scope_default: "none",
                         override_value: null, overridden: false },
      },
      can_edit: true,
    },
  // GET /api/sessions/dev (poll) and DELETE /api/sessions/dev (Delete's
  // confirm request) share this one path — the delete scenario's own
  // assertions only care about the request having been SENT, never about
  // this body, so returning the detail fixture for both is harmless.
  "/api/sessions/dev": SESSION_DETAIL_FIXTURES[SCENARIO],
  "/api/sessions/dev/stop": { status: "stopped", label: "dev", dropped: 0 },
  "/api/sessions/dev/kill": { status: "killed", label: "dev" },
  "/api/sessions/dev/resume": { status: "resumed", label: "dev" },
  "/api/sessions/dev/perms": SCENARIO === "perms_in_place"
    ? { status: "sent", label: "dev", mode: "auto", pending: true }
    : { status: "switched", label: "dev", mode: "full" },
  "/api/sessions/dev/clearqueue": { status: "cleared", label: "dev", dropped: 3 },
  "/api/sessions/dev/compact": { status: "queued", label: "dev" },
  "/api/sessions/dev/restart": { status: "restarted", label: "dev" },
  "/api/session-options": {
    models: [
      { label: "Opus", hint: "Most capable" },
      { label: "Opus 5.5", hint: "" },
    ],
  },
  "/api/sessions/dev/model": SCENARIO === "model_unconfirmed"
    ? { status: "unconfirmed", label: "dev", model: "Sonnet 5",
        requested: "claude-opus-5-5", detail: "not confirmed — check the session." }
    : { status: "switched", label: "dev", model: "Opus 5.5",
        previous_model: "Sonnet 5" },
  "/api/sessions/dev/rename": {
    status: "renamed", label: "frontend", previous_label: "dev", changed: true,
  },
};

// ---- Settings -> Updates (admin self-update) fixtures ------------------
if (SCENARIO.indexOf("updates_") === 0) {
  FIXTURES["/api/preferences"] = {
    schema: [], values: {}, can_edit: true,
    can_update: SCENARIO !== "updates_hidden",
  };
  // Loading Settings asks only for the running job (roadmap 8.43): the
  // versions come from POST /api/update/check, after "Check for updates"
  // or when the Settings tab is opened (updates_auto_*).
  FIXTURES["/api/update"] = { job: null, version: "0.7.13" };
  const CHECKS = {
    claude: { lines: ["aipager 0.7.13 (up to date)", "Claude Code 2.1.281 → 2.1.290"],
              offer: { kind: "claude", label: "Update Claude Code" },
              summary: null, restart: null, notes: [], source: "pipx, from PyPI" },
    aipager: { lines: ["aipager 0.7.13 → 0.7.14", "Claude Code 2.1.281 (up to date)"],
               offer: { kind: "aipager", label: "Update aipager" }, summary: null,
               restart: "Restart: automatic, once no turn is running.", notes: [],
               source: "pipx, from local path /home/op/aipager" },
    none: { lines: ["aipager 0.7.13 (up to date)", "Claude Code 2.1.281 (couldn't check)"],
            offer: null, summary: "Everything is up to date.", restart: null,
            notes: [], source: "pipx, from PyPI" },
    current: { lines: ["aipager 0.7.13 (up to date)", "Claude Code 2.1.281 (up to date)"],
               offer: null, summary: "Everything is up to date.", restart: null,
               notes: [], source: "pipx, from PyPI", up_to_date: true },
  };
  // The update confirm: in place (Restart now follows), systemd (it
  // restarts by itself), neither (a fail-closed plan: steps follow) and
  // a payload from before either field existed.
  const CONFIRM_PLAN = {
    updates_confirm_in_place: { restart_auto: false, restart_in_place: true },
    updates_confirm_systemd: { restart_auto: true, restart_in_place: false },
    updates_confirm_manual_steps: { restart_auto: false, restart_in_place: false },
    updates_confirm_legacy: {},
  }[SCENARIO];
  if (CONFIRM_PLAN) Object.assign(CHECKS.aipager, CONFIRM_PLAN);
  if (SCENARIO === "updates_waiting_check") {
    FIXTURES["/api/update"].version = "0.7.17";
    FIXTURES["/api/update"].waiting = { source: "installed", version: "0.7.18" };
  }
  if (SCENARIO === "updates_restart_now_lost_poll") {
    CHECKS.claude = { lines: ["aipager 0.7.18 installed, 0.7.17 running"], offer: null,
                      summary: "Restart aipager to run 0.7.18.", restart: null, notes: [],
                      source: "pipx, from PyPI", restart_offer: true };
    FIXTURES["/api/update"].version = "0.7.17";
    FIXTURES["/api/update"].url_changes_on_restart = true;
    FIXTURES["/api/update/restart"] = { job: { id: 9, kind: "restart", phase: "verifying",
      blockers: [], summary: "", started_at: 1 } };
    webApp.close = () => { global.__closed = true; };
  }
  const CHECK = (CONFIRM_PLAN ? "aipager" : null) || { updates_check_aipager: "aipager",
                  updates_check_nothing_newer: "none", updates_auto_current: "current",
                  updates_auto_throttle: "current", updates_auto_quiet: "current",
                  updates_auto_offline: "current", updates_auto_conflict: "current" }[SCENARIO]
                  || "claude";
  FIXTURES["/api/update/check"] = { check: CHECKS[CHECK], job: null };
  FIXTURES["/api/update/start"] = { job: { id: 1, kind: CHECKS[CHECK].offer
    ? CHECKS[CHECK].offer.kind : "claude",
    phase: "starting", blockers: [], summary: "", started_at: 1 } };
  const JOB_PHASE = { updates_poll_running: "upgrading",
                      updates_no_poll_terminal: "done",
                      updates_restart_pending: "restart_scheduled",
                      updates_restart_back: "restart_scheduled",
                      updates_restart_give_up: "restart_scheduled",
                      updates_restart_tunnel: "restart_scheduled",
                      updates_restart_wrong_version: "restart_scheduled",
                      updates_restart_watchdog: "restart_scheduled",
                      updates_restart_refused: "restart_scheduled",
                      updates_restart_watchdog_late: "restart_scheduled",
                      updates_restart_hang_tunnel: "restart_scheduled",
                      updates_restart_stuck: "restart_scheduled",
                      updates_restart_stuck_tunnel: "restart_scheduled",
                      updates_verify_dead_tunnel: "verifying",
                      updates_verify_hang: "verifying",
                      updates_verify_same_daemon_done: "verifying",
                      updates_verify_blip: "verifying",
                      updates_upgrading_blip: "upgrading",
                      updates_installed_dead_tunnel: "upgrading",
                      updates_chat_switch_lost_poll: "verifying",
                      updates_done_lost_poll: "done",
                      updates_double_load: "upgrading",
                      updates_verify_blip_then_gone: "verifying",
                      updates_auto_during_job: "upgrading",
                      updates_auto_during_restart: "restart_scheduled" }[SCENARIO];
  if (JOB_PHASE) {
    FIXTURES["/api/update"].job = { id: 7, kind: "aipager", phase: JOB_PHASE,
      blockers: [], summary: JOB_PHASE === "done" ? "aipager is already up to date" : "",
      started_at: 1 };
  }
  // Roadmap 8.45: the job says which version it installed, the old daemon
  // says which it runs, and whether this page's URL survives the restart.
  if (JOB_PHASE === "restart_scheduled") {
    FIXTURES["/api/update"].job.installed = { from: "0.7.17", to: "0.7.18" };
    FIXTURES["/api/update"].job.summary = "\u23f3 aipager 0.7.17 \u2192 0.7.18 installed, restarting\u2026";
    FIXTURES["/api/update"].version = "0.7.17";
    FIXTURES["/api/update"].restarted = null;
    FIXTURES["/api/update"].url_changes_on_restart = SCENARIO === "updates_restart_tunnel"
      || SCENARIO === "updates_restart_hang_tunnel"
      || SCENARIO === "updates_restart_stuck_tunnel";
  }
  if (SCENARIO === "updates_installed_dead_tunnel") {
    FIXTURES["/api/update"].job.installed = { from: "0.7.17", to: "0.7.18" };
    FIXTURES["/api/update"].version = "0.7.17";
    FIXTURES["/api/update"].url_changes_on_restart = true;
  }
  if (JOB_PHASE === "verifying") {
    FIXTURES["/api/update"].version = "0.7.17";
    FIXTURES["/api/update"].url_changes_on_restart = SCENARIO === "updates_verify_dead_tunnel"
      || SCENARIO === "updates_verify_blip_then_gone";
  }
  if (SCENARIO === "updates_restart_tunnel" || SCENARIO === "updates_restart_hang_tunnel"
      || SCENARIO === "updates_verify_dead_tunnel" || SCENARIO === "updates_installed_dead_tunnel") {
    webApp.close = () => { global.__closed = true; };
  }
}

// ---- Settings tab: chat-wide preferences (roadmap 8.40) ---------------
// The scope-wide Settings tab keeps plain values (not the per-session
// {effective, scope_default, ...} shape) and saves via savePreference.
if (SCENARIO.indexOf("scope_save") === 0) {
  FIXTURES["/api/preferences"] = {
    schema: SCHEMA, values: { answer_length: "none" }, can_edit: true,
  };
  FIXTURES["/api/preferences/answer_length"] = {
    values: { answer_length: "short" },
  };
  if (SCENARIO === "scope_save_fails") {
    POST_STATUS_OVERRIDE = { path: "/api/preferences/answer_length",
                             method: "PUT", status: 500,
                             body: { error: "boom" } };
  }
}

// ---- Settings tab: button colours (/settings → 🎨 Appearance) --------
if (SCENARIO.indexOf("appearance_save") === 0) {
  FIXTURES["/api/preferences"] = {
    schema: SCHEMA, values: { answer_length: "none" }, can_edit: true,
    appearance: {
      schema: [{ field: "stop", title: "🛑 Stop button", options: [
        { value: "normal", label: "Normal", help: "Telegram's own look.", swatch: [] },
        { value: "blue", label: "Blue", help: "", swatch: ["blue"] },
        { value: "green", label: "Green", help: "", swatch: ["green"] },
        { value: "red", label: "Red", help: "", swatch: ["red"] }] },
               { field: "prompt", title: "🔐 Allow and Deny buttons", options: [
        { value: "normal", label: "Normal", help: "", swatch: [] },
        { value: "coloured", label: "Coloured", help: "", swatch: ["green", "red"] }] }],
      values: { stop: "normal", prompt: "normal" },
    },
  };
  FIXTURES["/api/appearance/stop"] = { values: { stop: "red", prompt: "normal" } };
  if (SCENARIO === "appearance_save_fails") {
    POST_STATUS_OVERRIDE = { path: "/api/appearance/stop", method: "PUT", status: 500,
                             body: { error: "boom" } };
  }
}
if (SCENARIO.indexOf("appearance_") === 0 && SCENARIO.indexOf("appearance_save") !== 0) {
  FIXTURES["/api/preferences"] = {
    schema: SCHEMA, values: { answer_length: "none" },
    can_edit: SCENARIO !== "appearance_read_only",
    appearance: {
      schema: [{ field: "stop", title: "🛑 Stop button", options: [
        { value: "normal", label: "Normal", help: "", swatch: [] },
        { value: "blue", label: "Blue", help: "", swatch: ["blue"] },
        { value: "green", label: "Green", help: "", swatch: ["green"] },
        { value: "red", label: "Red", help: "", swatch: ["red"] }] }],
      values: { stop: "normal" },
    },
  };
  HOLD_PUT_PREFIX = "/api/appearance/";
  COPY_FIXTURES = true;
}

// ---- Settings tab: the viewer's own App language (multilingual) -------
// Personal, so it renders and saves even where the chat's settings are
// read-only; every word on it comes from the server.
const APP_LANGUAGE = {
  title: "App language",
  hint: "Only aipager's own text. Claude's answers don't change.",
  value: "auto", effective: "en",
  options: [{ value: "auto", label: "Automatic (your Telegram language)" },
            { value: "en", label: "English" }],
};
if (SCENARIO.indexOf("app_language") === 0) {
  FIXTURES["/api/preferences"] = {
    schema: SCHEMA, values: { answer_length: "none" }, can_edit: false,
  };
  if (SCENARIO !== "app_language_absent") {
    FIXTURES["/api/preferences"].app_language = JSON.parse(JSON.stringify(APP_LANGUAGE));
  }
  FIXTURES["/api/app-language"] = {
    value: "en", effective: "en",
    confirmation: "✅ aipager now speaks English here. Your sessions and Claude's answers are unchanged.",
  };
  if (SCENARIO === "app_language_fails") {
    POST_STATUS_OVERRIDE = { path: "/api/app-language", method: "PUT", status: 500,
                             body: { error: "boom" } };
  }
}

// ---- server schema text shown plain (roadmap 8.44) --------------------
// The schema comes from pytest (AIPAGER_TEST_SCHEMA): the real
// settings_schema() output, emoji titles and " - " labels as /settings
// shows them in the chat, plus a probe group that carries an emoji title
// and em dashes.
if (SCENARIO === "schema_plain") {
  const REAL = JSON.parse(fs.readFileSync(process.env.AIPAGER_TEST_SCHEMA, "utf8"));
  const scopeVals = {}, sessVals = {};
  REAL.forEach(g => {
    const v = g.options[0].value;
    scopeVals[g.field] = v;
    sessVals[g.field] = { effective: v, scope_default: v, override_value: null,
                          overridden: false };
  });
  FIXTURES["/api/preferences"] = { schema: REAL, values: scopeVals, can_edit: true };
  FIXTURES["/api/sessions/dev/preferences"] = { schema: REAL, values: sessVals,
                                                can_edit: true };
}

// ---- the lantern grid and Answer in chat (roadmap 8.44) ---------------
function row(label, status, extra) {
  return Object.assign({
    label, status, waiting_kind: null, waiting_summary: null, model: "Opus 4.6",
    context_pct: 40, cost_usd: 0.5, last_active_seconds_ago: 30, project: "proj",
  }, extra || {});
}
function gridOf(rows, extra) {
  const live = rows.filter(r => r.status !== "gone");
  return Object.assign({
    daemon: { version: "0.7.17", bot_username: "aipager_bot", uptime_seconds: 600 },
    totals: { total: rows.length, live: live.length, gone: rows.length - live.length,
              waiting: rows.filter(r => r.status === "waiting").length, cost_usd: 4.21 },
    sessions: rows, can_act: true,
  }, extra || {});
}
function mixedRows() {
  return [
    row("alpha", "waiting", { waiting_kind: "permission", waiting_summary: "Bash: npm test" }),
    row("bravo", "waiting", { waiting_kind: "question",
                              waiting_summary: "Which one — the red or the blue?" }),
    row("charlie", "busy", { context_pct: 64 }),
    row("delta", "busy"),
    row("echo", "idle"),
    row("old1", "gone"), row("old2", "gone"), row("old3", "gone"),
  ];
}
const GRID_FIXTURES = {
  answer_single: () => gridOf([
    row("alpha", "waiting", { waiting_kind: "permission", waiting_summary: "Bash: ls" }),
    row("charlie", "busy"),
  ]),
  answer_viewer: () => gridOf(mixedRows(), { can_act: false }),
  grid_empty: () => gridOf([]),
  // Roadmap 8.121: two sessions waiting for their own background work
  // (one with counts, one finishing up) beside a working and an idle one.
  grid_tasks: () => gridOf([
    row("charlie", "busy"),
    row("tasker", "tasks", { job_agents: 2, job_shells: 1 }),
    row("finisher", "tasks", { job_agents: 0, job_shells: 0 }),
    row("echo", "idle"),
  ]),
  detail_tasks: () => gridOf([row("tasker", "tasks", { job_agents: 2, job_shells: 0 })]),
  detail_tasks_recount: () => gridOf([row("tasker", "tasks", { job_agents: 2, job_shells: 0 })]),
  // Consecutive polls while the agents end one by one (2, 1, none left).
  grid_tasks_recount: () => gridOf([
    row("tasker", "tasks", { job_agents: 2, job_shells: 0 }),
    row("server", "tasks", { job_agents: 0, job_shells: 1 }),
    row("echo", "idle"),
  ]),
  grid_empty_then_expired: () => gridOf([]),
  grid_empty_then_offline: () => gridOf([]),
};
if (SCENARIO === "detail_tasks" || SCENARIO === "detail_tasks_recount") {
  FIXTURES["/api/sessions/tasker"] = Object.assign(fullDetailFor("tasks"), {
    label: "tasker", job_agents: 2, job_shells: 0, context_pct: 30 });
}
if (SCENARIO.indexOf("grid_") === 0 || SCENARIO.indexOf("answer_") === 0 ||
    SCENARIO === "detail_answer" || SCENARIO === "detail_waiting_mainbutton" ||
    SCENARIO === "detail_tasks" || SCENARIO === "detail_tasks_recount") {
  FIXTURES["/api/sessions"] = (GRID_FIXTURES[SCENARIO] || (() => gridOf(mixedRows())))();
  FIXTURES["/api/sessions/alpha/answer"] = { status: "sent", label: "alpha" };
  FIXTURES["/api/sessions/alpha"] = Object.assign(fullDetailFor("waiting", { isAdmin: true }), {
    label: "alpha", waiting_kind: "permission", waiting_summary: "Bash: ls",
    answer: { available: true, reason: null },
  });
}
if (SCENARIO === "answer_409") {
  POST_STATUS_OVERRIDE = { path: "/api/sessions/alpha/answer", method: "POST", status: 409,
    body: { error: "not_waiting", detail: "This session isn't waiting — it moved on." } };
}
if (SCENARIO === "grid_expired") {
  POST_STATUS_OVERRIDE = { path: "/api/sessions", method: "GET", status: 401,
    body: { error: "unauthorized" } };
}
if (SCENARIO === "answer_403") {
  POST_STATUS_OVERRIDE = { path: "/api/sessions/alpha/answer", method: "POST", status: 403,
    body: { error: "forbidden" } };
}

// ===== the chat switcher (roadmap 8.73) =================================
// Two chats (a group listed first, the viewer's DM the server's default)
// or one. Each chat has its own grid, chosen by the X-Aipager-Scope header
// exactly as the server chooses it; no header means the default (the DM).
const CHAT_DM = "111";
const CHAT_GROUP = "-100";
const STORE = {};
global.__held = [];
if (SCENARIO.indexOf("chats_") === 0) {
  if (SCENARIO === "chats_saved") STORE["aipager.chat"] = CHAT_GROUP;
  if (SCENARIO === "chats_saved_stale") STORE["aipager.chat"] = "-999";
  global.window.localStorage = SCENARIO === "chats_storage_throws"
    ? { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } }
    : { getItem: (k) => (k in STORE ? STORE[k] : null),
        setItem: (k, v) => { STORE[k] = String(v); } };
  FIXTURES["/api/chats"] = SCENARIO === "chats_one"
    ? { chats: [{ scope: CHAT_DM, label: "owner DM" }], current: CHAT_DM }
    : { chats: [{ scope: CHAT_GROUP, label: "team" }, { scope: CHAT_DM, label: "owner DM" }],
        current: CHAT_DM };
  // GET answers per chat. The DM's owner may update; in the group they may not.
  const PER_CHAT = {
    "/api/sessions": { [CHAT_DM]: gridOf([row("mine", "idle")]),
                       [CHAT_GROUP]: gridOf([row("grp", "busy")]) },
    "/api/preferences": {
      [CHAT_DM]: { schema: SCHEMA, can_edit: true, can_update: true,
                   values: { answer_length: "none" } },
      [CHAT_GROUP]: { schema: SCHEMA, can_edit: true,
                      can_update: SCENARIO === "chats_late_check_both",
                      values: { answer_length: "short" } },
    },
    "/api/session-options": {
      [CHAT_DM]: { models: [], directories: ["/dm"], default_directory: "/dm" },
      [CHAT_GROUP]: { models: [], directories: ["/grp"], default_directory: "/grp" },
    },
    "/api/update": { [CHAT_DM]: { job: { phase: "done" } },
                     [CHAT_GROUP]: { job: { phase: "done" } } },
    // Writes, keyed "METHOD path".
    "PUT /api/preferences/answer_length": {
      [CHAT_DM]: { values: { answer_length: "medium" }, changed: true },
      [CHAT_GROUP]: { values: { answer_length: "medium" }, changed: true },
    },
    "POST /api/update/check": {
      [CHAT_DM]: { job: null, check: {
        lines: ["aipager 0.7.13 (up to date)", "Claude Code 2.1.281 → 2.1.290"],
        offer: { kind: "claude", label: "Update Claude Code" },
        summary: null, restart: null, notes: [], source: "pipx, from PyPI" } },
    },
  };
  // chats_late*: the default chat's FIRST answer on one path arrives only
  // when the driver releases it, after the viewer has switched away.
  const HOLD = { chats_late: "/api/sessions", chats_late_settings: "/api/preferences",
                 chats_late_options: "/api/session-options", chats_late_updates: "/api/update",
                 chats_late_pref_write: "PUT /api/preferences/answer_length",
                 chats_late_pref_fail: "PUT /api/preferences/answer_length",
                 chats_late_check: "POST /api/update/check",
                 chats_late_check_both: "POST /api/update/check" };
  const stubFetch = global.fetch;
  global.fetch = (url, opts) => {
    const method = (opts && opts.method) || "GET";
    const headers = (opts && opts.headers) || {};
    const path = url.split("?")[0];
    if (url === "/api/chats") {
      // Where the page learned its chats: every request from here on
      // must name one.
      return stubFetch(url, opts).then((r) => Object.assign({}, r, {
        json: () => r.json().then((d) => { global.__chatsAt = fetchCalls.length; return d; }),
      }));
    }
    const key = method === "GET" ? path : method + " " + path;
    if (!PER_CHAT[key]) {
      return stubFetch(url, opts);
    }
    fetchCalls.push({ url, method, headers, body: opts && opts.body });
    const chat = headers["X-Aipager-Scope"] || CHAT_DM;
    const body = PER_CHAT[key][chat];
    // chats_late_pref_fail: the held settings write is refused (500).
    const failed = SCENARIO === "chats_late_pref_fail" && method === "PUT";
    const resp = body && !failed
      ? { ok: true, status: 200, json: () => Promise.resolve(body) }
      : failed ? { ok: false, status: 500, json: () => Promise.resolve({}) }
      : { ok: false, status: 403, json: () => Promise.resolve({ error: "forbidden" }) };
    if (HOLD[SCENARIO] === key && chat === CHAT_DM && !global.__heldOnce) {
      global.__heldOnce = true;
      return new Promise((res) => { global.__held.push(() => res(resp)); });
    }
    return Promise.resolve(resp);
  };
}

// The page may only ever talk to its own API. The Telegram SDK arrives
// through the page's <script src>, never through fetch.
const realExit = process.exit;
process.exit = (code) => {
  if (!code) {
    const stray = fetchCalls.filter(f => f.url.indexOf("/api/") !== 0);
    if (stray.length) {
      console.error("FAIL: the page fetched outside /api/: " +
                    JSON.stringify(stray.map(f => f.url)));
      realExit(1);
    }
  }
  realExit(code);
};

// extract and run the page script
let script = page.match(/<script>([\s\S]*?)<\/script>/g)
  .map(s => s.replace(/<\/?script>/g, "")).join("\n");
// unwrap the IIFE so the internals are reachable, and export what we drive
// keep the IIFE (it contains top-level `return`s) but export its internals
script = script.replace(/\}\)\(\);\s*$/,
  "\n  global.__api = { openDetail, renderSessionSettings, loadSessionSettings, saveSessionPreference, renderOptionGroup, openGroups, pollTick, loadSettings, loadUpdates, showView, renderGrid, answerPrompt, showGrid, switchChat, openNewSession, savePreference, checkUpdates, getSettings: function () { return settingsData; }, getNew: function () { return { options: newOptions, cwd: newState.cwd }; } };\n})();");
// The no-storage scenario asserts the page LOADS without storage, so a
// throw while the script runs is that scenario's failure, not a crash.
try {
  eval(script);
} catch (e) {
  if (SCENARIO !== "chats_storage_throws") { throw e; }
  console.error("FAIL: the page's script threw without storage: " + e);
  process.exit(1);
}

function fail(msg) { console.error("FAIL: " + msg); process.exit(1); }

const api = global.__api;

// ---- scenario: the existing per-session settings flow (default) ------
//
// drive loadSessionSettings by clicking a session card is complex; instead
// invoke the same path the page does when the detail view opens.
// We emulate by calling the fetch-backed loader through a card click.
function driveSettings() {
  api.loadSessionSettings("dev");
  const host = byId["session-settings-groups"];
  setTimeoutReal(() => {
    if (host.children.length !== 1) fail("settings group did not render");
    const grp = host.children[0];
    const head = grp.children[0];
    if (!head.listeners.click) fail("group header has no click handler");

    head.click();                                   // expand
    const body = host.children[0].children[1];
    if (!body) fail("group did not expand when its header was tapped");
    if (body.children.length !== 3) fail("expected 3 choices, got " + body.children.length);

    const choice = body.children[1];
    if (choice.disabled) fail("choice is disabled despite can_edit:true");
    if (!choice.listeners.click) fail("choice has no click handler");

    const before = fetchCalls.length;
    choice.click();                                 // tap an option
    const sent = fetchCalls.slice(before);
    const put = sent.find(f => f.method === "PUT");
    if (!put) fail("tapping an option sent no request at all");
    if (!/\/api\/sessions\/dev\/preferences\/answer_length$/.test(put.url))
      fail("wrong PUT url: " + put.url);

    console.log("ok: expand -> tap -> " + put.method + " " + put.url);
    process.exit(0);
  }, 0);
}

// ---- shared helpers for the kebab -> menu -> modal interaction --------

function openMenu() {
  const kebab = byId["detail-menu-btn"];
  if (kebab.hidden) fail("the kebab is hidden for a session that has actions");
  kebab.click();
  if (byId["overlay"].hidden) fail("tapping the kebab did not open the overlay");
  if (byId["action-menu"].hidden) fail("tapping the kebab did not open the menu");
  return byId["action-menu"];
}
function menuItems(menu) {
  return menu.children.filter(c => c.className.includes("menu-item"));
}
function itemNamed(menu, text) {
  const hit = menuItems(menu).find(i => i.textContent === text);
  if (!hit) fail("no menu item named " + JSON.stringify(text) + ", got " +
                 JSON.stringify(menuItems(menu).map(i => i.textContent)));
  return hit;
}
function modalIsOpen() {
  return !byId["overlay"].hidden && !byId["confirm-modal"].hidden;
}

// ---- scenario: Stop (single tap from the menu, BUSY session) ----------
function driveStopBusy() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    if (menuItems(menu).length !== 1)
      fail("expected exactly one action for a busy session, got " +
           JSON.stringify(menuItems(menu).map(i => i.textContent)));
    const item = itemNamed(menu, "Stop");
    if (item.disabled) fail("Stop is disabled for a busy session the caller can act on");

    const before = fetchCalls.length;
    item.click();
    const sent = fetchCalls.slice(before);
    if (sent.length !== 1)
      fail("tapping Stop sent " + sent.length + " requests, expected exactly one");
    if (!sent.find(f => f.method === "POST" && /\/api\/sessions\/dev\/stop$/.test(f.url)))
      fail("tapping Stop sent no POST /api/sessions/dev/stop: " + JSON.stringify(sent));
    if (modalIsOpen()) fail("Stop is recoverable and must not ask for confirmation");
    if (!byId["overlay"].hidden) fail("the menu stayed open after acting");

    setTimeoutReal(() => {
      if (byId["view-detail"].hidden) fail("Stop navigated away from the detail view");
      console.log("ok: stop -> menu -> one POST /api/sessions/dev/stop, stayed on the page");
      process.exit(0);
    }, 20);
  }, 10);
}

// ---- scenario: Kill (menu -> confirm modal, IDLE session) -------------
function driveKillIdle() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    const item = itemNamed(menu, "Kill");

    const before = fetchCalls.length;
    item.click();
    // THE guard: choosing a destructive action must ask, not act.
    if (fetchCalls.length !== before)
      fail("choosing Kill from the menu issued a request before any confirmation");
    if (!modalIsOpen()) fail("choosing Kill did not open the confirm modal");
    if (!byId["action-menu"].hidden)
      fail("the menu is still open behind the modal — two layers at once");
    if (byId["confirm-title"].textContent.indexOf("dev") === -1)
      fail("the confirm does not name the session: " +
           JSON.stringify(byId["confirm-title"].textContent));

    byId["confirm-ok"].click();
    const sent = fetchCalls.slice(before);
    if (sent.length !== 1)
      fail("confirming Kill sent " + sent.length + " requests, expected one");
    if (!sent.find(f => f.method === "POST" && /\/api\/sessions\/dev\/kill$/.test(f.url)))
      fail("confirming Kill sent no POST /api/sessions/dev/kill");
    if (!byId["overlay"].hidden) fail("the modal stayed open after confirming");

    setTimeoutReal(() => {
      if (!byId["view-detail"].hidden)
        fail("a killed session left the operator on its own dead page");
      if (byId["view-grid"].hidden)
        fail("a killed session did not return to the grid");
      console.log("ok: kill -> menu -> modal -> POST /api/sessions/dev/kill -> grid");
      process.exit(0);
    }, 20);
  }, 10);
}

// ---- scenario: Resume (single tap, GONE + resumable) ------------------
function driveResumeGone() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    const item = itemNamed(menu, "Resume");
    if (item.disabled) fail("Resume is disabled for a session that has a transcript");

    const before = fetchCalls.length;
    item.click();
    const sent = fetchCalls.slice(before);
    if (sent.length !== 1)
      fail("tapping Resume sent " + sent.length + " requests, expected one");
    if (!sent.find(f => f.method === "POST" && /\/api\/sessions\/dev\/resume$/.test(f.url)))
      fail("tapping Resume sent no POST /api/sessions/dev/resume");
    if (modalIsOpen()) fail("Resume is recoverable and must not ask for confirmation");

    console.log("ok: resume -> menu -> one POST /api/sessions/dev/resume");
    process.exit(0);
  }, 10);
}

// ---- scenario: Resume is inert WITH a reason (GONE + not resumable) ---
function driveResumeGoneNoTranscript() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    const item = itemNamed(menu, "Resume");
    if (!item.disabled) fail("Resume must be disabled with no resumable transcript");

    const note = menu.children.find(c => c.className === "menu-note");
    if (!note || note.textContent !== plain(NO_TRANSCRIPT_REASON))
      fail("Resume's disabled reason does not match NO_TRANSCRIPT_REASON: " +
           JSON.stringify(note && note.textContent));

    const before = fetchCalls.length;
    item.click();   // a disabled control in this shim has no listener attached
    if (fetchCalls.length !== before)
      fail("an inert Resume still sent a request");

    console.log('ok: resume inert with reason "' + plain(NO_TRANSCRIPT_REASON) + '"');
    process.exit(0);
  }, 10);
}

// ---- scenario: Delete (menu -> confirm modal, GONE session) -----------
function driveDeleteGone() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    const item = itemNamed(menu, "Delete");

    const before = fetchCalls.length;
    item.click();
    if (fetchCalls.length !== before)
      fail("choosing Delete from the menu issued a request before any confirmation");
    if (!modalIsOpen()) fail("choosing Delete did not open the confirm modal");

    byId["confirm-ok"].click();
    const sent = fetchCalls.slice(before);
    if (sent.length !== 1)
      fail("confirming Delete sent " + sent.length + " requests, expected one");
    if (!sent.find(f => f.method === "DELETE" && /\/api\/sessions\/dev$/.test(f.url)))
      fail("confirming Delete sent no DELETE /api/sessions/dev");

    setTimeoutReal(() => {
      console.log("ok: delete -> menu -> modal -> DELETE /api/sessions/dev -> grid");
      process.exit(0);
    }, 20);
  }, 10);
}

// ---- scenario: a poll that changes the offer closes an open menu -----
//
// The menu is built once from lastDetailData. If the session changes
// underneath (someone kills it from chat), leaving the menu up would
// have it advertising an action the session can no longer take, and
// silently redrawing it would move rows under a finger. Neither: close
// it and say so.
function driveMenuDriftCloses() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    itemNamed(menu, "Kill");                       // idle -> Kill on offer

    // The next poll finds it gone: Resume/Delete instead of Kill.
    DETAIL_OVERRIDE = detailFor("gone", true);
    const before = fetchCalls.length;
    api.pollTick();

    setTimeoutReal(() => {
      if (!byId["overlay"].hidden)
        fail("the menu stayed open while the session's actions changed under it");
      if (byId["notice"].textContent.indexOf("changed") === -1)
        fail("the menu closed silently, with no explanation: " +
             JSON.stringify(byId["notice"].textContent));
      if (fetchCalls.slice(before).some(f => f.method !== "GET"))
        fail("the drift close performed an action");
      // Second, harder case: the SAME actions, one of them newly
      // unavailable (its transcript got cleaned up). A signature built
      // from keys alone would miss this and leave a Resume on offer
      // that the server would now refuse.
      DETAIL_OVERRIDE = detailFor("gone", true);
      api.pollTick();
      setTimeoutReal(() => {
        const menu2 = openMenu();
        itemNamed(menu2, "Resume");
        DETAIL_OVERRIDE = detailFor("gone", false);   // same keys, not resumable
        api.pollTick();
        setTimeoutReal(() => {
          if (!byId["overlay"].hidden)
            fail("the menu stayed open while an action it offers became " +
                 "unavailable — the change detector only watches which " +
                 "actions exist, not whether they can run");
          console.log("ok: a changed session closes its open menu and says so");
          process.exit(0);
        }, 30);
      }, 30);
    }, 30);
  }, 10);
}

// ---- scenario: two different confirms in a row cannot cross ----------
//
// confirmAction (session actions) and confirmRun (Reset) are separate
// fields consumed by one handler. If a stale one survived, confirming
// the dialog on screen would perform the OTHER action.
function driveConfirmIsolation() {
  api.loadSessionSettings("dev");
  api.openDetail("dev");
  setTimeoutReal(() => {
    // Open the Reset confirm, then abandon it.
    byId["session-settings-reset"].click();
    if (byId["confirm-modal"].hidden) fail("Reset did not open its confirm");
    byId["confirm-cancel"].click();

    // Now a session action's confirm. Confirming it must Kill — never
    // run the reset that was cancelled a moment ago.
    itemNamed(openMenu(), "Kill").click();
    if (byId["confirm-modal"].hidden) fail("Kill did not open its confirm");
    const before = fetchCalls.length;
    byId["confirm-ok"].click();

    setTimeoutReal(() => {
      const sent = fetchCalls.slice(before);
      if (!sent.find(f => f.method === "POST" && /\/dev\/kill$/.test(f.url)))
        fail("confirming Kill did not kill: " + JSON.stringify(sent));
      if (sent.find(f => f.method === "DELETE" && /preferences/.test(f.url)))
        fail("confirming Kill ALSO ran the cancelled reset — the pending " +
             "action leaked between dialogs");
      console.log("ok: a cancelled confirm does not leak into the next one");
      process.exit(0);
    }, 30);
  }, 10);
}

// ---- scenario: Reset to defaults asks before discarding --------------
function driveResetConfirm() {
  api.loadSessionSettings("dev");
  setTimeoutReal(() => {
    const reset = byId["session-settings-reset"];
    if (reset.hidden) fail("Reset to defaults is not offered for an overridden session");

    const before = fetchCalls.length;
    reset.click();
    // It discards every override with no undo — it must ask first.
    if (fetchCalls.length !== before)
      fail("Reset to defaults discarded the overrides before confirming");
    if (byId["overlay"].hidden || byId["confirm-modal"].hidden)
      fail("Reset to defaults did not open a confirm dialog");

    // Cancelling must leave the overrides alone.
    byId["confirm-cancel"].click();
    if (fetchCalls.length !== before) fail("cancelling still reset the settings");
    if (!byId["overlay"].hidden) fail("cancel left the dialog open");

    reset.click();
    byId["confirm-ok"].click();
    setTimeoutReal(() => {
      const del = fetchCalls.slice(before).find(
        f => f.method === "DELETE" && /\/preferences\/answer_length$/.test(f.url));
      if (!del) fail("confirming Reset sent no DELETE for the override");
      console.log("ok: reset asks, cancel is safe, confirm clears the override");
      process.exit(0);
    }, 20);
  }, 10);
}

// ---- scenario: no actions -> no kebab at all -------------------------
function driveNoActions() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    if (!byId["detail-menu-btn"].hidden)
      fail("the kebab is offered for a session with no actions — tapping it " +
           "would open an empty menu");
    const before = fetchCalls.length;
    byId["detail-menu-btn"].click();
    if (!byId["overlay"].hidden) fail("an empty menu opened anyway");
    if (fetchCalls.length !== before) fail("it issued a request");
    console.log("ok: no actions -> no kebab, no empty menu");
    process.exit(0);
  }, 10);
}

// ---- scenario: Back closes the modal, it does not leave the page ------
function driveModalBackCloses() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    itemNamed(openMenu(), "Delete").click();
    if (!modalIsOpen()) fail("the confirm modal did not open");

    const before = fetchCalls.length;
    if (typeof global.__back !== "function")
      fail("the page never registered a BackButton handler");
    global.__back();

    if (modalIsOpen()) fail("Back left the confirm modal open");
    if (!byId["overlay"].hidden) fail("Back left the overlay up");
    if (byId["view-detail"].hidden)
      fail("Back closed the modal AND left the session page — a trapdoor, " +
           "not a dismissal");
    if (fetchCalls.length !== before) fail("Back issued a request");

    // …and a second Back, with nothing open, still navigates as before.
    global.__back();
    if (!byId["view-detail"].hidden)
      fail("Back with no layer open no longer leaves the detail page");

    console.log("ok: back closes the modal and stays on the page, then navigates");
    process.exit(0);
  }, 10);
}

// ---- scenario: the backdrop cancels without acting --------------------
function driveBackdropCancels() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    itemNamed(openMenu(), "Delete").click();
    if (!modalIsOpen()) fail("the confirm modal did not open");

    const before = fetchCalls.length;
    byId["overlay"].click();

    if (modalIsOpen()) fail("tapping the backdrop left the modal open");
    if (fetchCalls.length !== before)
      fail("tapping the backdrop performed the action instead of cancelling");
    if (byId["view-detail"].hidden) fail("cancelling navigated away");

    console.log("ok: backdrop cancels, no request issued");
    process.exit(0);
  }, 10);
}

// ===== Mini App session MENU actions (perms/clearqueue/compact/restart/
//       rename) =========================================================

// ---- scenario: perms, IDLE, admin targeting Full access ----------------
function postedMode(sent) {
  const post = sent.find(f => f.method === "POST" && /\/dev\/perms$/.test(f.url));
  if (!post) fail("no POST /api/sessions/dev/perms was sent");
  return JSON.parse(post.body || "{}").mode;
}

function drivePermsIdle() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    // The two modes the session is not in, each its own item.
    const names = menuItems(menu).map(i => i.textContent);
    if (names.indexOf("Switch to Auto mode") === -1 || names.indexOf("Switch to Full access mode") === -1
        || names.indexOf("Switch to Ask mode") !== -1)
      fail("the mode items should be the two other modes: " + JSON.stringify(names));
    const item = itemNamed(menu, "Switch to Full access mode");
    if (item.disabled) fail("perms must be available for an admin targeting Full access");

    const before = fetchCalls.length;
    item.click();
    if (fetchCalls.length !== before)
      fail("choosing Full access from the menu issued a request before confirming");
    if (!modalIsOpen()) fail("choosing Full access did not open the confirm modal");
    if (byId["confirm-title"].textContent.indexOf("Full access") === -1)
      fail("confirm title does not name the target mode: " +
           JSON.stringify(byId["confirm-title"].textContent));
    if (byId["confirm-body"].textContent.indexOf("restarts") === -1)
      fail("the confirm must say the session restarts: " +
           JSON.stringify(byId["confirm-body"].textContent));
    if (byId["confirm-ok"].textContent !== "Switch")
      fail("idle perms confirm label should be 'Switch', got " +
           JSON.stringify(byId["confirm-ok"].textContent));

    byId["confirm-ok"].click();
    const sent = fetchCalls.slice(before);
    if (sent.length !== 1)
      fail("confirming perms sent " + sent.length + " requests, expected one");
    if (postedMode(sent) !== "full")
      fail("the switch did not send the target mode: " + JSON.stringify(sent[0].body));

    console.log("ok: perms idle -> menu -> modal -> POST /api/sessions/dev/perms");
    process.exit(0);
  }, 10);
}

// ---- scenario: perms, BUSY -- Stop task & switch wording ---------------
function drivePermsBusy() {
  permsStopsTheWork("busy");
}

// Roadmap 8.121: a session waiting for its own background agents is still
// working, and a Full access switch restarts it (stopping them), so it is
// asked in the busy wording too.
function drivePermsTasks() {
  permsStopsTheWork("tasks");
}

function permsStopsTheWork(state) {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    const item = itemNamed(menu, "Switch to Full access mode");

    item.click();
    if (!modalIsOpen())
      fail("choosing perms on a " + state + " session did not open the confirm modal");
    if (byId["confirm-title"].textContent.indexOf("Stop the current task") === -1)
      fail(state + " perms confirm must use the stop-task wording: " +
           JSON.stringify(byId["confirm-title"].textContent));
    if (byId["confirm-ok"].textContent !== "Stop task & switch")
      fail(state + " perms confirm label wrong: " +
           JSON.stringify(byId["confirm-ok"].textContent));
    if (byId["confirm-cancel"].textContent !== "Not now")
      fail(state + " perms cancel label wrong: " +
           JSON.stringify(byId["confirm-cancel"].textContent));

    const before = fetchCalls.length;
    byId["confirm-ok"].click();
    const sent = fetchCalls.slice(before);
    if (postedMode(sent) !== "full") fail(state + " switch sent the wrong mode");

    console.log("ok: perms " + state +
                " -> Stop task & switch wording -> POST /api/sessions/dev/perms");
    process.exit(0);
  }, 10);
}

// ---- scenario: perms, non-admin targeting Full access -- inert with reason
function drivePermsFullRequiresAdmin() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    const item = itemNamed(menu, "Switch to Full access mode");
    if (!item.disabled) fail("a non-admin targeting Full access must see it disabled");
    if (itemNamed(menu, "Switch to Auto mode").disabled)
      fail("Auto is everyone's: it must stay on offer to a non-admin");

    const note = menu.children.find(c => c.className === "menu-note");
    if (!note || note.textContent.indexOf("admin") === -1)
      fail("disabled perms must state the admin reason: " +
           JSON.stringify(note && note.textContent));

    const before = fetchCalls.length;
    item.click();   // a disabled control in this shim has no listener attached
    if (fetchCalls.length !== before) fail("an inert perms row still sent a request");

    console.log("ok: perms full access requires admin -> disabled with reason, no fetch");
    process.exit(0);
  }, 10);
}

// ---- scenario: Ask -> Auto, in place: no confirm, one POST, a pending toast
function drivePermsInPlace() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    const before = fetchCalls.length;
    itemNamed(menu, "Switch to Auto mode").click();
    if (modalIsOpen()) fail("an Ask/Auto switch must not ask to confirm (nothing restarts)");
    const sent = fetchCalls.slice(before);
    if (postedMode(sent) !== "auto") fail("the in-place switch sent the wrong mode");
    setTimeoutReal(() => {
      const notice = byId["notice"].textContent;
      if (notice.indexOf("Switch sent") === -1)
        fail("the toast must say the switch was sent, got " + JSON.stringify(notice));
      console.log("ok: perms ask -> auto in place -> one POST, no modal, sent toast");
      process.exit(0);
    }, 20);
  }, 10);
}

// ---- scenario: leaving Full access: confirm (it restarts), no admin -----
function drivePermsLeaveFull() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    const item = itemNamed(menu, "Switch to Ask mode");
    if (item.disabled) fail("leaving Full access needs no admin");
    item.click();
    if (!modalIsOpen()) fail("leaving Full access restarts the session: it must confirm");
    if (byId["confirm-body"].textContent !== "The session restarts; the conversation is kept.")
      fail("leaving Full access body wrong: " +
           JSON.stringify(byId["confirm-body"].textContent));
    const before = fetchCalls.length;
    byId["confirm-ok"].click();
    if (postedMode(fetchCalls.slice(before)) !== "ask") fail("leave-full sent the wrong mode");
    console.log("ok: perms leave full access -> confirm -> POST mode ask");
    process.exit(0);
  }, 10);
}

// ---- scenario: restart (IDLE / BUSY -- same client behaviour either way)
function driveRestart(okMessage) {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    const item = itemNamed(menu, "Restart");
    if (item.disabled) fail("restart must be available");

    const before = fetchCalls.length;
    item.click();
    if (fetchCalls.length !== before)
      fail("choosing Restart issued a request before confirming");
    if (!modalIsOpen()) fail("choosing Restart did not open the confirm modal");
    // Restart's own wording, not Kill/Delete's generic fallback body —
    // the two happen to share a confirmLabel ("Restart"), so only the
    // body text actually distinguishes a dedicated branch from a
    // silently-correct-looking fallback onto the generic one.
    if (byId["confirm-body"].textContent.indexOf("relaunches") === -1)
      fail("restart's confirm body does not describe a relaunch: " +
           JSON.stringify(byId["confirm-body"].textContent));

    byId["confirm-ok"].click();
    const sent = fetchCalls.slice(before);
    if (sent.length !== 1)
      fail("confirming Restart sent " + sent.length + " requests, expected one");
    if (!sent.find(f => f.method === "POST" && /\/dev\/restart$/.test(f.url)))
      fail("confirming Restart sent no POST /api/sessions/dev/restart");

    console.log(okMessage);
    process.exit(0);
  }, 10);
}
function driveRestartIdle() {
  driveRestart("ok: restart idle -> menu -> modal -> POST /api/sessions/dev/restart");
}
function driveRestartBusy() {
  driveRestart("ok: restart busy -> menu -> modal -> POST /api/sessions/dev/restart");
}

// ---- scenario: Clear queue (single tap, BUSY + non-empty queue) --------
function driveClearqueueBusy() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    const item = itemNamed(menu, "Clear queue");
    if (item.disabled) fail("clearqueue must be available when the queue is non-empty");

    const before = fetchCalls.length;
    item.click();
    const sent = fetchCalls.slice(before);
    if (sent.length !== 1)
      fail("tapping Clear queue sent " + sent.length + " requests, expected one");
    if (!sent.find(f => f.method === "POST" && /\/dev\/clearqueue$/.test(f.url)))
      fail("tapping Clear queue sent no POST /api/sessions/dev/clearqueue");
    if (modalIsOpen())
      fail("Clear queue is recoverable and must not ask for confirmation");

    console.log("ok: clearqueue -> menu -> one POST /api/sessions/dev/clearqueue");
    process.exit(0);
  }, 10);
}

// ---- scenario: Compact now (single tap, no confirm) ---------------------
function driveCompact(okMessage) {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    const item = itemNamed(menu, "Compact now");
    if (item.disabled) fail("compact must be available below the queue cap");

    const before = fetchCalls.length;
    item.click();
    const sent = fetchCalls.slice(before);
    if (sent.length !== 1)
      fail("tapping Compact now sent " + sent.length + " requests, expected one");
    if (!sent.find(f => f.method === "POST" && /\/dev\/compact$/.test(f.url)))
      fail("tapping Compact now sent no POST /api/sessions/dev/compact");
    if (modalIsOpen())
      fail("Compact is recoverable and must not ask for confirmation");

    console.log(okMessage);
    process.exit(0);
  }, 10);
}
function driveCompactBusyQueues() {
  driveCompact("ok: compact busy -> menu -> one POST /api/sessions/dev/compact (queues)");
}
function driveCompactIdleSends() {
  driveCompact("ok: compact idle -> menu -> one POST /api/sessions/dev/compact (sends)");
}

// ---- scenario: Compact, queue at cap -- inert with reason ---------------
function driveCompactQueueFull() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    const item = itemNamed(menu, "Compact now");
    if (!item.disabled) fail("compact must be disabled at the queue cap");

    const note = menu.children.find(c => c.className === "menu-note");
    if (!note || note.textContent.indexOf("Queue is full") === -1)
      fail("disabled compact must state the queue-full reason: " +
           JSON.stringify(note && note.textContent));

    const before = fetchCalls.length;
    item.click();
    if (fetchCalls.length !== before) fail("an inert compact row still sent a request");

    console.log("ok: compact queue full -> disabled with reason, no fetch");
    process.exit(0);
  }, 10);
}

// ---- scenario: Rename, a valid new name ----------------------------------
function driveRenameValid() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    const item = itemNamed(menu, "Rename");

    const before = fetchCalls.length;
    item.click();
    if (fetchCalls.length !== before)
      fail("choosing Rename issued a request before confirming");
    if (!modalIsOpen()) fail("choosing Rename did not open the confirm modal");

    const input = byId["confirm-rename-input"];
    if (input.hidden) fail("Rename's text field is not shown");
    if (input.value !== "dev")
      fail("Rename's field is not pre-filled with the current label: " +
           JSON.stringify(input.value));

    input.value = "frontend";
    (input.listeners.input || []).forEach(f => f.call(input, {}));
    if (byId["confirm-ok"].disabled) fail("a valid new name must leave Save enabled");

    byId["confirm-ok"].click();
    const sent = fetchCalls.slice(before);
    if (sent.length !== 1)
      fail("confirming Rename sent " + sent.length + " requests, expected one");
    if (!sent.find(f => f.method === "POST" && /\/dev\/rename$/.test(f.url)))
      fail("confirming Rename sent no POST /api/sessions/dev/rename");

    console.log("ok: rename valid -> menu -> modal -> POST /api/sessions/dev/rename");
    process.exit(0);
  }, 10);
}

// ---- scenario: Rename, a client-side-invalid name -- Save stays disabled
function driveRenameClientSideInvalid() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    itemNamed(menu, "Rename").click();
    if (!modalIsOpen()) fail("choosing Rename did not open the confirm modal");

    const input = byId["confirm-rename-input"];
    input.value = "bad name!";
    (input.listeners.input || []).forEach(f => f.call(input, {}));
    if (!byId["confirm-ok"].disabled)
      fail("an invalid new name must leave Save disabled");
    if (byId["confirm-rename-error"].hidden || !byId["confirm-rename-error"].textContent)
      fail("a disabled Save must say why");

    const before = fetchCalls.length;
    byId["confirm-ok"].click();   // a disabled Save must still refuse to submit
    if (fetchCalls.length !== before)
      fail("tapping a disabled Save still sent a request");

    console.log("ok: rename client-side invalid -> Save disabled, no fetch");
    process.exit(0);
  }, 10);
}

// ---- scenario: Rename, the server refuses (409 conflict) ----------------
function driveRenameServerConflict() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    itemNamed(menu, "Rename").click();

    const input = byId["confirm-rename-input"];
    input.value = "frontend";
    (input.listeners.input || []).forEach(f => f.call(input, {}));

    POST_STATUS_OVERRIDE = {
      path: "/api/sessions/dev/rename", method: "POST", status: 409,
      body: {
        error: "conflict",
        detail: "A session named frontend already exists in this chat.",
      },
    };
    byId["confirm-ok"].click();

    setTimeoutReal(() => {
      if (byId["notice"].textContent.indexOf("already exists in this chat") === -1)
        fail("the notice does not show the server's detail verbatim: " +
             JSON.stringify(byId["notice"].textContent));
      console.log("ok: rename server conflict -> notice shows the server detail verbatim");
      process.exit(0);
    }, 20);
  }, 10);
}

// ---- scenario: the menu divider sits between the two groups -------------
function driveMenuGroupingDivider() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    const classes = menu.children.map(c => c.className);
    const dividerIdx = classes.findIndex(c => c === "menu-divider");
    if (dividerIdx === -1)
      fail("no divider rendered for a menu with both control and destructive items: " +
           JSON.stringify(classes));

    const before = menu.children[dividerIdx - 1];
    const after = menu.children[dividerIdx + 1];
    if (!before || before.className.indexOf("act-rename") === -1)
      fail("divider is not immediately after the last control item (rename); got " +
           JSON.stringify(classes));
    if (!after || after.className.indexOf("act-perms") === -1)
      fail("divider is not immediately before the first destructive item (perms); got " +
           JSON.stringify(classes));

    console.log("ok: divider sits between the last control item and the first destructive one");
    process.exit(0);
  }, 10);
}

// ---- scenario: switch a running session's model ----------------------
function modelHead() {
  const host = byId["detail-model"];
  if (host.hidden) fail("the Model control is hidden on a live session's page");
  if (!host.children.length) fail("the Model control rendered nothing");
  return host.children[0].children[0];
}
function modelRows() {
  const grp = byId["detail-model"].children[0];
  const body = grp.children[1];
  if (!body) fail("the Model group did not expand when its header was tapped");
  return body.children;
}
function driveModelSwitch() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    if (modelHead().textContent.indexOf("Sonnet 5") === -1)
      fail("the Model header does not show the current model: " +
           JSON.stringify(modelHead().textContent));
    modelHead().click();
    const row = modelRows().find(r => r.children[0] &&
                                      r.children[0].textContent === "Opus 5.5");
    if (!row) fail("no Opus 5.5 row from /api/session-options");
    if (row.disabled) fail("the Opus 5.5 row is disabled on an idle session");

    const before = fetchCalls.length;
    row.click();
    const posts = fetchCalls.slice(before).filter(f => f.method === "POST");
    if (posts.length !== 1)
      fail("picking a model sent " + posts.length + " POSTs, expected one");
    if (!/\/api\/sessions\/dev\/model$/.test(posts[0].url))
      fail("wrong model url: " + posts[0].url);
    if (JSON.parse(posts[0].body).model !== "Opus 5.5")
      fail("wrong model body: " + posts[0].body);
    if (modelHead().textContent.indexOf("switching…") === -1)
      fail("no \"switching…\" while the request is open: " +
           JSON.stringify(modelHead().textContent));

    setTimeoutReal(() => {
      const want = SCENARIO === "model_unconfirmed"
        ? "not confirmed (check the session)" : "Opus 5.5";
      if (modelHead().textContent.indexOf(want) === -1)
        fail("after the answer the header should read " + JSON.stringify(want) +
             ", got " + JSON.stringify(modelHead().textContent));
      console.log("ok: model pick -> POST /api/sessions/dev/model -> switching… -> " + want);
      process.exit(0);
    }, 20);
  }, 10);
}
function driveModelBusy() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const note = byId["detail-model-note"];
    if (note.hidden || note.textContent !== plain(MODEL_SWITCH_BUSY_REASON))
      fail("a busy session's Model control does not say why: " +
           JSON.stringify(note.textContent));
    modelHead().click();
    const rows = modelRows();
    if (!rows.length) fail("no model rows rendered");
    const before = fetchCalls.length;
    rows.forEach(r => r.click());
    if (fetchCalls.slice(before).some(f => f.method === "POST"))
      fail("a busy session's Model picker still sent a switch");
    console.log("ok: model picker inert while busy, with the reason");
    process.exit(0);
  }, 10);
}

// ---- scenario: no can_update -> the Updates block never appears ------
function driveUpdatesHidden() {
  api.loadSettings();
  setTimeoutReal(() => {
    if (!byId["updates-block"].hidden) fail("updates block shown without can_update");
    if (fetchCalls.some(f => f.url === "/api/update"))
      fail("asked /api/update although can_update is false");
    console.log("ok: no can_update -> updates block hidden, /api/update never asked");
    process.exit(0);
  }, 10);
}

// ---- scenario: can_update -> ONE "Check for updates" button, no lookup --
function actionLabels() {
  return byId["updates-actions"].children.map(c => c.textContent);
}
// The head's quiet Check for updates (the card's only check control).
function checkBtn() { return byId["updates-check"]; }
function checkLabel() { return byId["updates-check-label"].textContent; }
function checkReady() {
  return !checkBtn().hidden && !checkBtn().disabled && checkLabel() === "Check for updates";
}
function lineTexts() {
  return byId["updates-lines"].children.map(c => c.textContent);
}
function updateRequests() {
  return fetchCalls.filter(f => f.url.indexOf("/api/update") === 0)
    .map(f => f.method + " " + f.url + (f.body && f.body !== "{}" ? " " + f.body : ""));
}

function driveUpdatesRender() {
  api.loadSettings();
  setTimeoutReal(() => {
    if (byId["updates-block"].hidden) fail("updates block hidden despite can_update");
    // At rest: the name, the running version and the quiet head button,
    // no big button.
    if (!checkReady()) fail("no Check for updates in the head: " + JSON.stringify(checkLabel()));
    if (byId["updates-version"].hidden || byId["updates-version"].textContent !== "v0.7.13")
      fail("running version: " + JSON.stringify(byId["updates-version"].textContent));
    if (actionLabels().length) fail("buttons at rest: " + JSON.stringify(actionLabels()));
    if (lineTexts().length) fail("versions shown before any check: " + JSON.stringify(lineTexts()));
    if (JSON.stringify(updateRequests()) !== JSON.stringify(["GET /api/update"]))
      fail("page load looked versions up: " + JSON.stringify(updateRequests()));
    console.log("ok: can_update -> one Check for updates button, no version lookup");
    process.exit(0);
  }, 10);
}

// ---- scenario: tap Check, then the ONE Update button -----------------
function driveUpdatesCheckThenUpdate() {
  api.loadSettings();
  setTimeoutReal(() => {
    checkBtn().click();
    // Synchronously after the tap: the head says Checking… and turns.
    if (checkLabel() !== "Checking…" || !checkBtn().disabled
        || !checkBtn().classList.contains("is-checking"))
      fail("no disabled Checking… state: " + JSON.stringify(checkLabel()));
    if (actionLabels().length) fail("buttons while checking: " + JSON.stringify(actionLabels()));
    setTimeoutReal(() => {
      const want = ["aipager 0.7.13 (up to date)", "Claude Code 2.1.281 → 2.1.290"];
      if (JSON.stringify(lineTexts()) !== JSON.stringify(want))
        fail("check lines: " + JSON.stringify(lineTexts()));
      if (JSON.stringify(actionLabels()) !== JSON.stringify(["Update Claude Code"]))
        fail("want exactly one Update Claude Code button: " + JSON.stringify(actionLabels()));
      if (!byId["updates-restart"].hidden)
        fail("restart note shown without an aipager update");
      if (byId["updates-source"].hidden ||
          byId["updates-source"].textContent !== "pipx, from PyPI")
        fail("install source not shown as muted text");
      byId["updates-actions"].children[0].click();
      setTimeoutReal(() => {
        const want = ["GET /api/update", "POST /api/update/check",
                      'POST /api/update/start {"kind":"claude"}', "GET /api/update"];
        if (JSON.stringify(updateRequests()) !== JSON.stringify(want))
          fail("requests: " + JSON.stringify(updateRequests()));
        console.log("ok: Check -> Checking… -> lines + one button -> POST start {kind: claude}");
        process.exit(0);
      }, 10);
    }, 10);
  }, 10);
}

function driveUpdatesCheckAipager() {
  api.loadSettings();
  setTimeoutReal(() => {
    checkBtn().click();
    setTimeoutReal(() => {
      if (JSON.stringify(actionLabels()) !== JSON.stringify(["Update aipager"]))
        fail("want exactly one Update aipager button: " + JSON.stringify(actionLabels()));
      if (byId["updates-restart"].hidden ||
          byId["updates-restart"].textContent !== "Restart: automatic, once no turn is running.")
        fail("restart note missing with an aipager update");
      console.log("ok: aipager newer -> one Update aipager button + restart note");
      process.exit(0);
    }, 10);
  }, 10);
}

function driveUpdatesCheckNothingNewer() {
  api.loadSettings();
  setTimeoutReal(() => {
    checkBtn().click();
    setTimeoutReal(() => {
      if (lineTexts()[1] !== "Claude Code 2.1.281 (couldn't check)")
        fail("failed lookup line: " + JSON.stringify(lineTexts()));
      if (byId["updates-summary"].hidden ||
          byId["updates-summary"].textContent !== "Everything is up to date.")
        fail("no up-to-date summary");
      // Nothing to do: no button at all; the head checks again.
      if (actionLabels().length)
        fail("nothing newer must offer no button: " + JSON.stringify(actionLabels()));
      if (!checkReady()) fail("the head cannot check again: " + JSON.stringify(checkLabel()));
      checkBtn().click();
      setTimeoutReal(() => {
        const checks = updateRequests().filter(r => r === "POST /api/update/check");
        if (checks.length !== 2) fail("the head did not re-check: " + JSON.stringify(updateRequests()));
        if (updateRequests().some(r => r.indexOf("/api/update/start") !== -1))
          fail("an update started although nothing was newer");
        console.log("ok: nothing newer -> summary, no button, the head checks again");
        process.exit(0);
      }, 10);
    }, 10);
  }, 10);
}

// ---- the Settings tab opened: the card checks by itself ----------------
// Opening the tab (the tab bar, not a reload) checks for updates: an offer
// brings the big button, up to date one quiet line, a failure nothing at
// all. Not during a job or a restart, and not again within ten minutes;
// a tap on the head always checks.
function checkCount() {
  return updateRequests().filter(r => r === "POST /api/update/check").length;
}
function driveUpdatesAuto() {
  let now = 1000000;
  Date.now = () => now;
  if (SCENARIO === "updates_auto_quiet") {
    POST_STATUS_OVERRIDE = { path: "/api/update/check", method: "POST", status: 500,
                             body: { error: "boom" } };
  }
  if (SCENARIO === "updates_auto_offline") FETCH_REJECT_PATH = "/api/update/check";
  if (SCENARIO === "updates_auto_conflict") {
    // An update started elsewhere between the job poll and the check.
    POST_STATUS_OVERRIDE = { path: "/api/update/check", method: "POST", status: 409,
                             body: { error: "update_in_progress" } };
  }
  const quiet = SCENARIO === "updates_auto_quiet" || SCENARIO === "updates_auto_offline"
    || SCENARIO === "updates_auto_conflict";
  const busy = SCENARIO === "updates_auto_during_job" || SCENARIO === "updates_auto_during_restart";
  byId["maintab-settings"].click();
  setTimeoutReal(() => {
    if (checkCount() !== (busy ? 0 : 1))
      fail("checks on opening Settings: " + JSON.stringify(updateRequests()));
    if (busy) {
      console.log("ok: " + SCENARIO + " -> no automatic check");
      process.exit(0);
    }
    if (SCENARIO === "updates_auto_offer") {
      const big = byId["updates-actions"].children.filter(c => c.className === "primary")
        .map(c => c.textContent);
      if (JSON.stringify(big) !== JSON.stringify(["Update Claude Code"]))
        fail("a newer version without its big button: " + JSON.stringify(actionLabels()));
      if (lineTexts().length !== 2) fail("what is new: " + JSON.stringify(lineTexts()));
      console.log("ok: opening Settings -> checked -> newer -> the big button");
      process.exit(0);
    }
    if (SCENARIO === "updates_auto_current") {
      if (actionLabels().length) fail("a button although up to date: " + JSON.stringify(actionLabels()));
      if (lineTexts().length) fail("lines although up to date: " + JSON.stringify(lineTexts()));
      if (!byId["updates-source"].hidden) fail("the install source although up to date");
      if (byId["updates-summary"].hidden ||
          byId["updates-summary"].textContent !== "Everything is up to date.")
        fail("summary: " + JSON.stringify(byId["updates-summary"].textContent));
      if (!checkReady()) fail("the head after the check: " + JSON.stringify(checkLabel()));
      console.log("ok: opening Settings -> checked -> up to date -> one quiet line");
      process.exit(0);
    }
    if (quiet) {
      if (byId["notice"].textContent) fail("a failed automatic check said: " + byId["notice"].textContent);
      if (!checkReady() || actionLabels().length)
        fail("the card after a failed automatic check: " + JSON.stringify([checkLabel(), actionLabels()]));
      checkBtn().click();                     // a tap still checks, and says it failed
      setTimeoutReal(() => {
        if (checkCount() !== 2) fail("the tap did not check: " + JSON.stringify(updateRequests()));
        const said = { updates_auto_offline: "Couldn't reach aipager",
                       updates_auto_conflict: "An update is already running" }[SCENARIO]
          || "Couldn't check for updates";
        if ((byId["notice"].textContent || "").indexOf(said) === -1)
          fail("a failed tapped check said nothing: " + JSON.stringify(byId["notice"].textContent));
        console.log("ok: " + SCENARIO + " -> a failed automatic check stays quiet, a tapped one says so");
        process.exit(0);
      }, 10);
      return;
    }
    // updates_auto_throttle: each step, then the count once the page settled.
    const MIN = 60 * 1000;
    const reopen = () => { byId["maintab-sessions"].click(); byId["maintab-settings"].click(); };
    const steps = [
      [reopen, 1, "reopened within minutes and checked again"],
      // A poll of the job is not opening the tab.
      [() => { now += 10 * MIN + 1; api.loadUpdates(); }, 1, "a poll checked for updates"],
      [reopen, 2, "not checked again after ten minutes"],
      // A tap always checks, and counts as the last check.
      [() => { now += 9 * MIN; checkBtn().click(); }, 3, "the tap within minutes did not check"],
      [() => { now += 2 * MIN; reopen(); }, 3, "checked again two minutes after a tap"],
      [() => { now += 8 * MIN + 1; reopen(); }, 4, "not checked again ten minutes after a tap"],
    ];
    const run = (i) => {
      if (i === steps.length) {
        console.log("ok: one automatic check per ten minutes, a tap always checks");
        process.exit(0);
      }
      const [act, want, why] = steps[i];
      act();
      setTimeoutReal(() => {
        if (checkCount() !== want) fail(why + ": " + checkCount());
        run(i + 1);
      }, 10);
    };
    run(0);
  }, 10);
}

// ---- a check while Restart now is offered: it stays --------------------
// A newer aipager installed and waiting: the banner's Restart now is still
// the right button while a check is asked.
function driveUpdatesWaitingCheck() {
  const realFetch = global.fetch;
  global.fetch = (url, opts) => {
    if (url === "/api/update/check") {
      fetchCalls.push({ url, method: "POST", body: opts && opts.body, headers: {} });
      return new Promise(() => {});             // held: the check never answers
    }
    return realFetch(url, opts);
  };
  api.loadSettings();
  setTimeoutReal(() => {
    if (actionLabels().indexOf("Restart now") === -1)
      fail("no Restart now with the waiting banner: " + JSON.stringify(actionLabels()));
    checkBtn().click();
    if (checkLabel() !== "Checking…") fail("the head while checking: " + JSON.stringify(checkLabel()));
    if (actionLabels().indexOf("Restart now") === -1)
      fail("Restart now went away while checking: " + JSON.stringify(actionLabels()));
    console.log("ok: waiting banner -> Restart now stays while checking");
    process.exit(0);
  }, 10);
}

// ---- scenario: the update confirm promises Restart now only in place ---
function driveUpdatesConfirm() {
  const shown = [];
  webApp.showConfirm = (message, cb) => { shown.push(message); cb(false); };
  api.loadSettings();
  setTimeoutReal(() => {
    checkBtn().click();
    setTimeoutReal(() => {
      byId["updates-actions"].children[0].click();
      setTimeoutReal(() => {
        if (shown.length !== 1) fail("want one confirm: " + JSON.stringify(shown));
        if (updateRequests().some(r => r.indexOf("/api/update/start") !== -1))
          fail("a declined confirm still started the update");
        console.log("ok: confirm -> " + shown[0]);
        process.exit(0);
      }, 10);
    }, 10);
  }, 10);
}

// ---- scenario: a 403 hides the block and does NOT expire the app ------
function driveUpdatesForbidden() {
  POST_STATUS_OVERRIDE = { path: "/api/update", method: "GET", status: 403,
                           body: { error: "forbidden" } };
  api.loadSettings();
  setTimeoutReal(() => {
    if (!byId["updates-block"].hidden) fail("a 403 left the updates block visible");
    if (byId["conn-badge"].textContent === "expired")
      fail("a 403 from /api/update put the whole app into the expired state");
    if ((byId["error"].textContent || "").indexOf("expired") !== -1)
      fail("a 403 from /api/update showed the session-expired message");
    console.log("ok: 403 -> updates block hidden, app not expired");
    process.exit(0);
  }, 10);
}

// ---- the Updates poll: only while a job is still running ----------------
function updatePolls() { return timeoutCalls.filter(ms => ms === 3000).length; }
function startLabels() {
  return byId["updates-actions"].children.map(c => c.textContent)
    .filter(l => l === "Check for updates" || l.indexOf("Update") === 0);
}

function driveUpdatesPollRunning() {
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    if (updatePolls() !== 1) fail("a running job scheduled " + updatePolls() + " polls, want 1");
    if (startLabels().length) fail("check/start buttons offered mid-job: " + JSON.stringify(startLabels()));
    if (!checkBtn().hidden) fail("the head's Check for updates shown mid-job");
    console.log("ok: running job -> polls every 3 s, no check or start buttons");
    process.exit(0);
  }, 10);
}

function driveUpdatesNoPollTerminal() {
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    if (updatePolls() !== 0) fail("a finished job still polls");
    if (startLabels().length) fail("start buttons after a finished job: " + JSON.stringify(startLabels()));
    if (!checkReady()) fail("finished job should offer Check for updates again: " + JSON.stringify(checkLabel()));
    console.log("ok: finished job -> no poll, Check for updates back");
    process.exit(0);
  }, 10);
}

function driveUpdatesRestartPending() {
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    if (updatePolls() !== 0) fail("a pending restart still polls");
    if (byId["updates-actions"].children.length)
      fail("buttons offered while a restart is pending: " +
           JSON.stringify(byId["updates-actions"].children.map(c => c.textContent)));
    if (!byId["updates-job"].textContent.includes("Restarting"))
      fail("pending restart not shown: " + JSON.stringify(byId["updates-job"].textContent));
    if (!checkBtn().hidden) fail("the head's Check for updates shown while restarting");
    console.log("ok: pending restart -> no buttons, no poll");
    process.exit(0);
  }, 10);
}

// ---- the restart after an aipager update (roadmap 8.45) ----------------
// The page's timers are driven by hand here: every setTimeout the page
// makes is queued, and `tick()` runs the oldest after moving the clock.
// Each poll of /api/update answers from `replies` (in order); a reply is
// "down" (fetch rejects), a status number (an error page, not JSON), or a
// JSON body.
// The queue is ordered by due time (a timer set later but due sooner runs
// first, as in a browser), and clearTimeout really removes a timer.
// "hang" is a fetch that never answers until the page aborts it; "stuck"
// is one that ignores the abort too, and is settled only by the scenario
// (`h.settleStuck(reply)`).
function restartHarness(replies) {
  const queue = [];
  let now = 1000000;
  const start = now;
  let nextId = 1;
  Date.now = () => now;
  global.setTimeout = (f, ms) => {
    timeoutCalls.push(ms);
    const id = nextId++;
    queue.push({ f, ms, due: now + ms, id });
    return id;
  };
  global.clearTimeout = (id) => {
    const i = queue.findIndex(t => t.id === id);
    if (i !== -1) queue.splice(i, 1);
  };
  const baseFetch = global.fetch;
  let polls = 0;
  const stuck = [];
  let aborted = 0;
  const reply = (r) => {
    if (r === "down") { return Promise.reject(new TypeError("Failed to fetch")); }
    if (typeof r === "number") {
      return Promise.resolve({ ok: false, status: r,
        json: () => Promise.reject(new SyntaxError("Unexpected token <")) });
    }
    if (r && r.__status) {                 // an error reply WITH a JSON body
      return Promise.resolve({ ok: false, status: r.__status, json: () => Promise.resolve(r.body) });
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(r) });
  };
  global.fetch = (url, opts) => {
    // The first GET is loadUpdates' own (from FIXTURES); the rest answer from
    // `replies`, the job's later polls and the restart watch's alike.
    if (url === "/api/update" && fetchCalls.some(c => c.url === "/api/update")) {
      fetchCalls.push({ url, method: "GET" });
      polls += 1;
      const r = typeof replies === "function" ? replies(now)
        : (replies.length ? replies.shift() : "down");
      if (r === "hang") {
        return new Promise((_res, rej) => {
          const signal = opts && opts.signal;
          if (signal) signal.addEventListener("abort", () => {
            aborted += 1;
            rej(new Error("AbortError"));
          });
        });
      }
      if (r === "stuck") {
        return new Promise((res, rej) => { stuck.push({ res, rej }); });
      }
      return reply(r);
    }
    return baseFetch(url, opts);
  };
  function jobText() { return byId["updates-job"].textContent; }
  function spinning() {
    for (const n of byId["updates-job"].walk()) {
      if (n.className && n.className.indexOf("lamp-spin") !== -1) return true;
    }
    return false;
  }
  function tick(done) {
    if (!queue.length) { fail("no timer queued; job text: " + JSON.stringify(jobText())); }
    let k = 0;
    for (let i = 1; i < queue.length; i++) if (queue[i].due < queue[k].due) k = i;
    const t = queue.splice(k, 1)[0];
    now = Math.max(now, t.due);
    t.f();
    setTimeoutReal(done, 5);
  }
  // Settle the latest "stuck" fetch with a reply (see `reply` above).
  function settleStuck(r) {
    const s = stuck.pop();
    if (!s) fail("no stuck fetch to settle");
    reply(r).then(s.res, s.rej);
  }
  return { queue, tick, jobText, spinning, polls: () => polls, settleStuck,
           stuckCount: () => stuck.length, aborted: () => aborted,
           elapsed: () => now - start, advance: (ms) => { now += ms; } };
}

function noCountdown(text) {
  if (/\d+\s*s\b|in \d|seconds?/.test(text)) fail("a countdown is shown: " + JSON.stringify(text));
}

function driveUpdatesRestartBack() {
  const NEW = { job: null, version: "0.7.18", restarted: null, url_changes_on_restart: false };
  const DONE = { job: null, version: "0.7.18",
                 restarted: { from: "0.7.17", to: "0.7.18", readopted: 2, missing: [] },
                 url_changes_on_restart: false };
  const OLD = JSON.parse(JSON.stringify(FIXTURES["/api/update"]));
  const h = restartHarness([OLD, "down", 502, NEW, DONE]);
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    if (h.jobText() !== "Restarting aipager\u2026") fail("loading state: " + JSON.stringify(h.jobText()));
    if (!h.spinning()) fail("no spinner in the loading state");
    if (byId["updates-actions"].children.length) fail("buttons offered while restarting");
    noCountdown(h.jobText());
    h.tick(() => {                         // old daemon still up
      if (h.jobText() !== "Restarting aipager\u2026") fail("old daemon answer: " + h.jobText());
      h.tick(() => {                       // fetch error: the daemon is down
        if (h.jobText() !== "Restarting aipager\u2026") fail("down: " + h.jobText());
        h.tick(() => {                     // the tunnel's 502 page
          if (h.jobText() !== "Restarting aipager\u2026") fail("502: " + h.jobText());
          h.tick(() => {                   // the new daemon, count not yet known
            if (h.jobText() !== "Updated to 0.7.18.") fail("new version: " + JSON.stringify(h.jobText()));
            if (h.spinning()) fail("still spinning once updated");
            h.tick(() => {                 // ...and its count
              const text = h.jobText();
              if (text !== "Updated to 0.7.18, 2 sessions re-adopted.")
                fail("updated: " + JSON.stringify(text));
              if (byId["updates-version"].textContent !== "v0.7.18")
                fail("the head's version after the restart: " +
                     JSON.stringify(byId["updates-version"].textContent));
              if (h.queue.length) fail("still polling once the count arrived");
              // The poll delays alone (not the poll's own time limit or
              // the give-up watchdog).
              const delays = timeoutCalls.filter(ms => ms !== 6000 && ms !== 140000).slice(-5);
              if (!(delays[1] > delays[0] && delays[2] > delays[1]))
                fail("no backoff: " + JSON.stringify(delays));
              const labels = byId["updates-actions"].children.map(c => c.textContent);
              if (JSON.stringify(labels) !== JSON.stringify(["Check for updates"]))
                fail("after the update: " + JSON.stringify(labels));
              noCountdown(text);
              console.log("ok: restarting -> down/502 tolerated -> Updated to 0.7.18 + count");
              process.exit(0);
            });
          });
        });
      });
    });
  }, 10);
}

function driveUpdatesRestartGiveUp() {
  const h = restartHarness([]);             // every poll: down
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    let rounds = 0;
    const step = () => {
      const text = h.jobText();
      if (text === "Still restarting, reopen the app in a moment.") {
        if (h.queue.length) fail("still polling after giving up");
        const elapsed = h.elapsed();
        // About 2 minutes, but after the old daemon's 125 s watchdog.
        if (elapsed < 130000 || elapsed > 160000) fail("gave up after " + elapsed + " ms");
        if (h.spinning()) fail("still spinning after giving up");
        const labels = byId["updates-actions"].children.map(c => c.textContent);
        if (labels.indexOf("Check for updates") === -1)
          fail("no way out after giving up: " + JSON.stringify(labels));
        console.log("ok: never back -> gives up after ~2 min with reopen hint");
        process.exit(0);
      }
      if (text !== "Restarting aipager\u2026") fail("while down: " + JSON.stringify(text));
      if (++rounds > 60) fail("never gave up");
      h.tick(step);
    };
    step();
  }, 10);
}

function driveUpdatesRestartTunnel() {
  const OLD = JSON.parse(JSON.stringify(FIXTURES["/api/update"]));
  const h = restartHarness(["down", OLD, "down", "down"]);
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    if (h.jobText() !== "Restarting aipager\u2026") fail("loading state: " + h.jobText());
    // One flaky fetch while the old daemon is still up is not the restart.
    h.tick(() => h.tick(() => h.tick(() => {
      if (h.jobText() !== "Restarting aipager\u2026")
        fail("one failed fetch ended the watch: " + JSON.stringify(h.jobText()));
    h.tick(() => {
      const text = h.jobText();
      if (text.indexOf("aipager restarted, reopen the app") !== 0)
        fail("changed tunnel: " + JSON.stringify(text));
      if (text.indexOf("/app") === -1) fail("no reopen hint: " + JSON.stringify(text));
      if (text.indexOf("once the chat says it is updated") === -1)
        fail("no when-to-reopen hint: " + JSON.stringify(text));
      if (h.queue.length) fail("still polling a tunnel that is gone");
      const close = byId["updates-actions"].children.find(c => c.textContent === "Close the app");
      if (!close) fail("no Close the app button");
      close.click();
      if (!global.__closed) fail("Close the app did not close");
      console.log("ok: quick tunnel gone -> reopen the app, no spinning");
      process.exit(0);
    });
    })));
  }, 10);
}

function driveUpdatesRestartWrongVersion() {
  const h = restartHarness(["down", { job: null, version: "0.7.17", restarted: null,
                                      url_changes_on_restart: false }]);
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    h.tick(() => h.tick(() => {
      const text = h.jobText();
      if (text !== "aipager restarted but is running 0.7.17, not 0.7.18.")
        fail("wrong version back: " + JSON.stringify(text));
      if (text.indexOf("Updated") !== -1) fail("claimed an update that did not land");
      if (h.queue.length) fail("still polling");
      console.log("ok: back on the old version -> says so, not Updated");
      process.exit(0);
    }));
  }, 10);
}

function driveUpdatesRestartRefused() {
  // The page's sign-in is no longer accepted (a 401 from the new daemon):
  // polling on cannot help, reopening the app gives it a fresh one.
  const h = restartHarness(["down", 401]);
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    h.tick(() => h.tick(() => {
      const text = h.jobText();
      // initData is only good for 5 min: a 401 says nothing about the restart.
      if (text !== "Reopen the app to see the update's result.")
        fail("401 during the watch: " + JSON.stringify(text));
      if (h.queue.length) fail("still polling after a 401");
      if (byId["updates-block"].hidden) fail("the block was hidden");
      console.log("ok: 401 after restart -> reopen the app");
      process.exit(0);
    }));
  }, 10);
}

// A quick tunnel's old hostname that HANGS instead of failing (a phone's
// WebView, 2026-10-15): each poll gives up after its time limit and counts
// as down, so two of them say "reopen" well before the give-up.
function driveUpdatesRestartHangTunnel() {
  const h = restartHarness(["hang", "hang", "hang"]);
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    let rounds = 0;
    const step = () => {
      const text = h.jobText();
      if (text.indexOf("aipager restarted, reopen the app") === 0) {
        if (h.elapsed() > 30000) fail("reopen only after " + h.elapsed() + " ms");
        if (h.polls() !== 2) fail("polls: " + h.polls());
        if (h.aborted() !== 2) fail("hanging fetches aborted: " + h.aborted());
        if (h.queue.length) fail("timers left after the reopen: " + JSON.stringify(h.queue.map(t => t.ms)));
        const close = byId["updates-actions"].children.find(c => c.textContent === "Close the app");
        if (!close) fail("no Close the app button");
        console.log("ok: hanging polls time out -> reopen the app");
        process.exit(0);
      }
      if (text !== "Restarting aipager\u2026") fail("while hanging: " + JSON.stringify(text));
      if (++rounds > 60) fail("never ended");
      h.tick(step);
    };
    step();
  }, 10);
}

// ---- a restart the page never saw scheduled (2026-10-15) ---------------
// aipager restarts within one 3 s poll of `verifying` (no turn running), so
// the page's next job poll finds the old host gone: it must follow into the
// restart watch, not freeze on "checking the installed version".
function driveUpdatesVerifyDeadTunnel() {
  const h = restartHarness([]);             // every poll after the first: down
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    if (h.jobText().indexOf("checking the installed version") === -1)
      fail("the verifying step first: " + JSON.stringify(h.jobText()));
    let rounds = 0;
    const step = () => {
      const text = h.jobText();
      if (text.indexOf("aipager restarted, reopen the app") === 0) {
        if (h.elapsed() > 30000) fail("reopen only after " + h.elapsed() + " ms");
        if (h.queue.length) fail("timers left after the reopen: " + JSON.stringify(h.queue.map(t => t.ms)));
        const close = byId["updates-actions"].children.find(c => c.textContent === "Close the app");
        if (!close) fail("no Close the app button");
        console.log("ok: verifying -> host gone -> reopen the app");
        process.exit(0);
      }
      if (text.indexOf("checking the installed version") === -1 && text !== "Restarting aipager\u2026")
        fail("between the steps: " + JSON.stringify(text));
      if (++rounds > 60) fail("never left the verifying step");
      h.tick(step);
    };
    step();
  }, 10);
}

// The job poll hangs (no answer, no error): its time limit ends it and the
// restart watch takes over.
function driveUpdatesVerifyHang() {
  const h = restartHarness(["hang"]);
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    let rounds = 0;
    const step = () => {
      if (h.jobText() === "Restarting aipager\u2026") {
        if (h.aborted() !== 1) fail("the hanging job poll was not aborted: " + h.aborted());
        if (h.elapsed() > 3000 + 6000) fail("watch only after " + h.elapsed() + " ms");
        console.log("ok: verifying -> job poll hangs -> times out -> restart watch");
        process.exit(0);
      }
      if (h.jobText().indexOf("checking the installed version") === -1)
        fail("before the watch: " + JSON.stringify(h.jobText()));
      if (++rounds > 10) fail("the hanging poll froze the view");
      h.tick(step);
    };
    step();
  }, 10);
}

// One lost poll while verifying, then the same daemon answers: the job
// finished without a restart. The view shows what the job says and polls
// no more.
function driveUpdatesVerifySameDaemonDone() {
  const DONE = JSON.parse(JSON.stringify(FIXTURES["/api/update"]));
  DONE.job.phase = "done";
  DONE.job.summary = "aipager is already up to date";
  const h = restartHarness(["down", DONE]);
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    let rounds = 0;
    const step = () => {
      const text = h.jobText();
      if (text.indexOf("aipager is already up to date") !== -1) {
        if (h.queue.length) fail("still polling a finished job: " + JSON.stringify(h.queue.map(t => t.ms)));
        if (h.spinning()) fail("still spinning on a finished job");
        console.log("ok: verifying -> one lost poll -> same daemon done -> job view");
        process.exit(0);
      }
      if (++rounds > 10) fail("never showed the job's end: " + JSON.stringify(text));
      h.tick(step);
    };
    step();
  }, 10);
}

// One lost poll while verifying, and the same daemon is still verifying:
// back to the job's step, and the job is followed again.
function driveUpdatesVerifyBlip() {
  const OLD = JSON.parse(JSON.stringify(FIXTURES["/api/update"]));
  const h = restartHarness(["down", OLD]);
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    let rounds = 0, sawWatch = false;
    const step = () => {
      const text = h.jobText();
      if (text === "Restarting aipager\u2026") { sawWatch = true; }
      else if (sawWatch && text.indexOf("checking the installed version") !== -1) {
        if (h.queue.map(t => t.ms).indexOf(3000) === -1)
          fail("the job is not followed again: " + JSON.stringify(h.queue.map(t => t.ms)));
        console.log("ok: verifying -> one lost poll -> same daemon verifying -> polling again");
        process.exit(0);
      }
      if (++rounds > 10) fail("never came back to the job: " + JSON.stringify(text));
      h.tick(step);
    };
    step();
  }, 10);
}

// The Update job past its install (`installed` set) restarts within one
// poll: the page follows into the restart watch, not a retry loop.
function driveUpdatesInstalledDeadTunnel() {
  const h = restartHarness([]);
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    let rounds = 0;
    const step = () => {
      const text = h.jobText();
      if (text.indexOf("aipager restarted, reopen the app") === 0) {
        if (h.elapsed() > 30000) fail("reopen only after " + h.elapsed() + " ms");
        console.log("ok: installed -> host gone -> reopen the app");
        process.exit(0);
      }
      if (++rounds > 20) fail("never left the install step: " + JSON.stringify(text));
      h.tick(step);
    };
    step();
  }, 10);
}

// A job poll from the chat left behind fails after the switch: nothing of
// that chat's job may start a restart watch in the new chat's view.
function driveUpdatesChatSwitchLostPoll() {
  const h = restartHarness(["stuck"]);
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    h.tick(() => {                         // the job poll: in flight for good
      if (h.stuckCount() !== 1) fail("no job poll in flight: " + h.stuckCount());
      api.switchChat("-100777");
      setTimeoutReal(() => {
        h.settleStuck("down");             // the old chat's poll fails now
        setTimeoutReal(() => {
          if (h.jobText() === "Restarting aipager\u2026")
            fail("the old chat's lost poll started a restart watch");
          if (h.queue.some(t => t.ms === 1500))
            fail("a restart poll was scheduled for the old chat's job");
          console.log("ok: chat switched -> the old chat's lost poll does nothing");
          process.exit(0);
        }, 10);
      }, 10);
    });
  }, 10);
}

// A finished job's poll fails: nothing to follow, no retries.
function driveUpdatesDoneLostPoll() {
  const h = restartHarness(["down"]);
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    api.loadUpdates();                     // asked again: this one fails
    setTimeoutReal(() => {
      if (h.polls() !== 1) fail("polls: " + h.polls());
      if (h.queue.some(t => t.ms !== 6000))
        fail("a finished job is polled again: " + JSON.stringify(h.queue.map(t => t.ms)));
      console.log("ok: finished job -> lost poll -> no retry");
      process.exit(0);
    }, 10);
  }, 10);
}

// Update tapped, and the first poll of the job it started is lost: the
// started job is known, so the poll is retried, not dropped.
function driveUpdatesStartLostPoll() {
  const h = restartHarness(["down"]);
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    checkBtn().click();                                    // Check for updates
    setTimeoutReal(() => {
      byId["updates-actions"].children[0].click();        // Update Claude Code
      setTimeoutReal(() => {
        if (h.polls() !== 1) fail("job polls after the start: " + h.polls());
        if (!h.queue.some(t => t.ms === 3000))
          fail("the started job is not polled again: " + JSON.stringify(h.queue.map(t => t.ms)));
        console.log("ok: start -> first job poll lost -> retried");
        process.exit(0);
      }, 10);
    }, 10);
  }, 10);
}

// Two polls of a running job answer close together: one poll loop, not two.
function driveUpdatesDoubleLoad() {
  const OLD = JSON.parse(JSON.stringify(FIXTURES["/api/update"]));
  const h = restartHarness([OLD, OLD]);
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    api.loadUpdates();
    api.loadUpdates();
    setTimeoutReal(() => {
      const loops = h.queue.filter(t => t.ms === 3000).length;
      if (loops !== 1) fail("poll loops: " + loops + " " + JSON.stringify(h.queue.map(t => t.ms)));
      console.log("ok: two answers close together -> one poll loop");
      process.exit(0);
    }, 10);
  }, 10);
}

// A blip while verifying hands back to the job, then aipager restarts
// within one poll: the second watch still knows the address dies with it.
function driveUpdatesVerifyBlipThenGone() {
  const OLD = JSON.parse(JSON.stringify(FIXTURES["/api/update"]));
  const h = restartHarness(["down", OLD]);  // then: down for good
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    let rounds = 0, back = false;
    const step = () => {
      const text = h.jobText();
      if (text.indexOf("checking the installed version") !== -1 && h.polls() >= 2) back = true;
      if (text.indexOf("aipager restarted, reopen the app") === 0) {
        if (!back) fail("reopen before the hand-back");
        if (h.elapsed() > 40000) fail("reopen only after " + h.elapsed() + " ms");
        console.log("ok: verifying -> blip -> back -> host gone -> reopen the app");
        process.exit(0);
      }
      if (++rounds > 60) fail("never said reopen: " + JSON.stringify(text));
      h.tick(step);
    };
    step();
  }, 10);
}

// Restart now from a check result, and its first poll is lost: the watch
// knows this page's address dies with the restart.
function driveUpdatesRestartNowLostPoll() {
  webApp.showConfirm = (message, cb) => { cb(true); };
  const h = restartHarness([]);
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    checkBtn().click();                                    // Check for updates
    setTimeoutReal(() => {
      const restart = byId["updates-actions"].children.find(c => c.textContent === "Restart now");
      if (!restart) fail("no Restart now: " + JSON.stringify(byId["updates-actions"].children.map(c => c.textContent)));
      restart.click();
      setTimeoutReal(() => {
        let rounds = 0;
        const step = () => {
          const text = h.jobText();
          if (text.indexOf("aipager restarted, reopen the app") === 0) {
            if (h.elapsed() > 30000) fail("reopen only after " + h.elapsed() + " ms");
            console.log("ok: Restart now from a check -> lost poll -> reopen the app");
            process.exit(0);
          }
          if (++rounds > 60) fail("never said reopen: " + JSON.stringify(text));
          h.tick(step);
        };
        step();
      }, 10);
    }, 10);
  }, 10);
}

// A lost poll while the package is still installing is a blip, not a
// restart: polling goes on (after a longer gap) and the view keeps the step.
function driveUpdatesUpgradingBlip() {
  const OLD = JSON.parse(JSON.stringify(FIXTURES["/api/update"]));
  const h = restartHarness(["down", { __status: 500, body: { error: "boom" } }, OLD, "down"]);
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    const first = h.jobText();
    if (first === "Restarting aipager\u2026" || first === "") fail("start: " + JSON.stringify(first));
    h.tick(() => {                         // poll 1: down
      if (h.jobText() !== first) fail("after a lost poll: " + JSON.stringify(h.jobText()));
      if (!h.queue.length) fail("polling stopped after one lost poll");
      h.tick(() => {                       // poll 2: an error reply
        if (h.jobText() !== first) fail("after an error reply: " + JSON.stringify(h.jobText()));
        const gaps = h.queue.map(t => t.ms);
        if (gaps.indexOf(6000) === -1) fail("no longer gap after two lost polls: " + JSON.stringify(gaps));
        h.tick(() => {                     // poll 3: the daemon answers
          if (h.jobText() !== first) fail("back: " + JSON.stringify(h.jobText()));
          if (h.queue.map(t => t.ms).indexOf(3000) === -1)
            fail("not back to 3 s polls: " + JSON.stringify(h.queue.map(t => t.ms)));
          h.tick(() => {                   // poll 4: lost again, the gap starts over
            if (h.queue.map(t => t.ms).indexOf(3000) === -1)
              fail("the backoff did not start over: " + JSON.stringify(h.queue.map(t => t.ms)));
            console.log("ok: upgrading -> lost polls retried with backoff -> polling goes on");
            process.exit(0);
          });
        });
      });
    });
  }, 10);
}

// The daemon is down, and the poll in flight at the give-up time is stuck
// for good (it ignores the abort too): the give-up watchdog ends the watch
// on time, on its own clock, and that poll settling afterwards changes
// nothing. (Polls fail at once until 135 s; the one sent at about 139.8 s
// is still in flight at 140 s.)
function driveUpdatesRestartStuck() {
  const NEW = { job: null, version: "0.7.18", restarted: null, url_changes_on_restart: false };
  const h = restartHarness(() => (h.elapsed() >= 135000 ? "stuck" : "down"));
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    let rounds = 0;
    const step = () => {
      const text = h.jobText();
      if (text === "Still restarting, reopen the app in a moment.") {
        if (h.elapsed() !== 140000) fail("gave up after " + h.elapsed() + " ms, want exactly 140000");
        if (h.queue.length) fail("timers left after giving up: " + JSON.stringify(h.queue.map(t => t.ms)));
        if (h.spinning()) fail("still spinning after giving up");
        if (h.stuckCount() !== 1) fail("stuck polls in flight: " + h.stuckCount());
        h.settleStuck(NEW);
        setTimeoutReal(() => {
          if (h.jobText() !== "Still restarting, reopen the app in a moment.")
            fail("a late answer reopened the watch: " + JSON.stringify(h.jobText()));
          if (h.queue.length) fail("polling again after giving up");
          console.log("ok: stuck polls -> gives up on time, late answer ignored");
          process.exit(0);
        }, 10);
        return;
      }
      if (text !== "Restarting aipager\u2026") fail("while stuck: " + JSON.stringify(text));
      if (++rounds > 200) fail("never gave up");
      h.tick(step);
    };
    step();
  }, 10);
}

// A quick tunnel, the old daemon answering "restart scheduled" until
// 135 s and then a poll stuck at the give-up time: the watchdog ends the
// watch with "reopen", since this page's address dies with the restart.
function driveUpdatesRestartStuckTunnel() {
  const OLD = FIXTURES["/api/update"];
  const h = restartHarness(() => (h.elapsed() >= 135000 ? "stuck"
    : JSON.parse(JSON.stringify(OLD))));
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    let rounds = 0;
    const step = () => {
      const text = h.jobText();
      if (text.indexOf("aipager restarted, reopen the app") === 0) {
        if (h.elapsed() !== 140000) fail("reopen after " + h.elapsed() + " ms, want exactly 140000");
        if (h.queue.length) fail("timers left: " + JSON.stringify(h.queue.map(t => t.ms)));
        console.log("ok: stuck poll on a quick tunnel -> reopen at the give-up");
        process.exit(0);
      }
      if (text !== "Restarting aipager\u2026") fail("while waiting: " + JSON.stringify(text));
      if (++rounds > 200) fail("never ended");
      h.tick(step);
    };
    step();
  }, 10);
}

// The restart never happened, and the old daemon's watchdog only says so
// at 125 s (5 s delay + 120 s). The page must still be waiting then.
function driveUpdatesRestartWatchdogLate() {
  const old = FIXTURES["/api/update"];
  const failed = JSON.parse(JSON.stringify(old));
  failed.job.phase = "failed";
  failed.job.summary = "\u26a0\ufe0f The scheduled restart did not happen";
  let t0 = null;
  // The old daemon's watchdog reports the failure at 125 s after
  // scheduling; answering at 125 s into the watch is the later, harder case.
  const h = restartHarness((now) =>
    (now - t0 < 125000 ? JSON.parse(JSON.stringify(old)) : failed));
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    t0 = Date.now();
    let rounds = 0;
    const step = () => {
      const text = h.jobText();
      if (text.indexOf("The scheduled restart did not happen") !== -1) {
        if (h.spinning()) fail("still spinning");
        console.log("ok: watchdog at 125 s -> the job's own outcome, not Still restarting");
        process.exit(0);
      }
      if (text !== "Restarting aipager\u2026")
        fail("gave up before the watchdog: " + JSON.stringify(text));
      if (++rounds > 60) fail("never handed back");
      h.tick(step);
    };
    step();
  }, 10);
}

function driveUpdatesRestartWatchdog() {
  const failed = JSON.parse(JSON.stringify(FIXTURES["/api/update"]));
  failed.job.phase = "failed";
  failed.job.summary = "\u26a0\ufe0f The scheduled restart did not happen";
  const h = restartHarness([failed]);
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    h.tick(() => {
      const text = h.jobText();
      if (text.indexOf("The scheduled restart did not happen") === -1)
        fail("the old daemon's give-up not shown: " + JSON.stringify(text));
      if (h.spinning()) fail("still spinning");
      if (h.queue.filter(t => t.ms !== 3000).length) fail("the restart watch kept polling");
      console.log("ok: restart never happened -> the job's own outcome");
      process.exit(0);
    });
  }, 10);
}

// ---- a start refused because the daemon is stopping (503) -------------
function driveUpdatesShuttingDown() {
  api.loadSettings();
  setTimeoutReal(() => {
    checkBtn().click();                               // Check for updates
  }, 10);
  setTimeoutReal(() => {
    POST_STATUS_OVERRIDE = { path: "/api/update/start", method: "POST", status: 503,
                             body: { error: "shutting_down" } };
    byId["updates-actions"].children[0].click();     // Update Claude Code
    setTimeoutReal(() => {
      const notice = byId["notice"].textContent || "";
      if (notice.indexOf("shutting down") === -1)
        fail("a 503 shutting_down did not say so: " + JSON.stringify(notice));
      console.log("ok: 503 shutting_down -> notice says aipager is shutting down");
      process.exit(0);
    }, 10);
  }, 10);
}

// ---- scenario: Settings tab -> tap a different option -> PUT ----------
// Renders the chat-wide Settings tab from /api/preferences, expands the
// group, taps "Short" and checks the PUT, the optimistic paint, and what
// the screen settles on once the write answers (ok -> Short stays;
// failure -> the previous value comes back).
function scopeGroup() { return byId["settings-groups"].children[0]; }
function scopeHeaderValue() { return scopeGroup().children[0].children[1].textContent; }
function scopeActiveLabels() {
  const body = scopeGroup().children[1];
  return body.children.filter(c => c.classList.contains("is-active"))
    .map(c => c.children[0].textContent);
}
function appGroup() { return byId["settings-app-language"].children[0]; }
function appHeaderValue() { return appGroup().children[0].children[1].textContent; }
function driveAppLanguage() {
  const failing = SCENARIO === "app_language_fails";
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    const host = byId["settings-app-language"];
    const hint = byId["settings-app-language-hint"];
    if (SCENARIO === "app_language_absent") {
      if (host.children.length !== 0) fail("an App language group with no app_language");
      if (!hint.hidden) fail("the hint shows with no app_language");
      if (byId["settings-groups"].children.length !== 1)
        fail("the chat's settings did not render with no app_language");
      console.log("ok: no app_language -> nothing rendered");
      process.exit(0);
    }
    if (host.children.length !== 1) fail("App language group did not render");
    if (hint.hidden || hint.textContent !== APP_LANGUAGE.hint)
      fail("hint: " + JSON.stringify(hint.textContent));
    if (appGroup().children[0].children[0].textContent !== "App language")
      fail("title: " + appGroup().children[0].children[0].textContent);
    if (appHeaderValue() !== "Automatic (your Telegram language)")
      fail("initial value: " + JSON.stringify(appHeaderValue()));
    appGroup().children[0].click();                     // expand
    const body = appGroup().children[1];
    if (!body || body.children.length !== 2) fail("App language did not expand to 2 choices");
    const english = body.children[1];
    if (english.disabled || !english.listeners.click)
      fail("App language is not tappable where the chat's settings are read-only");
    const before = fetchCalls.length;
    try { english.click(); } catch (e) { fail("tapping App language threw: " + e); }
    const put = fetchCalls.slice(before).find(f => f.method === "PUT");
    if (!put) fail("tapping App language sent no PUT");
    if (put.url !== "/api/app-language") fail("wrong PUT url: " + put.url);
    if (JSON.stringify(JSON.parse(put.body)) !== JSON.stringify({ value: "en" }))
      fail("wrong PUT body: " + put.body);
    if (appHeaderValue() !== "English") fail("no optimistic render: " + appHeaderValue());
    setTimeoutReal(() => {
      const notice = byId["notice"].textContent || "";
      if (failing) {
        if (appHeaderValue() !== "Automatic (your Telegram language)")
          fail("failed PUT left the optimistic value: " + appHeaderValue());
        if (notice.indexOf("Couldn't save") === -1) fail("failed PUT said nothing: " + notice);
        console.log("ok: app language tap -> PUT /api/app-language -> restored on failure");
      } else {
        if (appHeaderValue() !== "English") fail("saved value not kept: " + appHeaderValue());
        if (notice.indexOf("aipager now speaks English here.") === -1)
          fail("no confirmation notice: " + notice);
        console.log("ok: app language tap -> PUT /api/app-language -> confirmed");
      }
      process.exit(0);
    }, 10);
  }, 10);
}

// The Appearance section: its own, after the chat's preferences, each
// colour with its dot, a tap saved through /api/appearance/<field>.
function driveAppearanceSave() {
  const failing = SCENARIO === "appearance_save_fails";
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    if (byId["settings-appearance"].hidden) fail("the Appearance section is hidden");
    if (!byId["settings-appearance-readonly"].hidden)
      fail("the read-only note shows where the viewer may change the colours");
    // The chat's preferences hold their own group and nothing else.
    const prefs = byId["settings-groups"].children;
    if (prefs.length !== 1) fail("chat preferences hold " + prefs.length + " rows, want 1 group");
    // Every tap redraws the list: look the groups up again each time.
    const groups = () => byId["settings-appearance-groups"].children;
    const kinds = groups().map(c => (c.className || "").split(" ")[0]);
    if (JSON.stringify(kinds) !== JSON.stringify(["grp", "grp"]))
      fail("want the 2 appearance groups alone, got " + JSON.stringify(kinds));
    groups()[0].children[0].click();                       // expand Stop
    const mains = groups()[0].children[1].children.map(r => r.children[0]);
    const classes = mains.map(m => m.className);
    if (JSON.stringify(classes) !== JSON.stringify([
        "choice-main", "choice-main swatch swatch-blue", "choice-main swatch swatch-green",
        "choice-main swatch swatch-red"])) fail("dots: " + JSON.stringify(classes));
    if (mains[3].textContent !== "Red") fail("label: " + mains[3].textContent);
    groups()[1].children[0].click();                       // expand Allow and Deny
    const pair = groups()[1].children[1].children[1].children[0].className;
    if (pair !== "choice-main swatch swatch-green-red") fail("two-colour dot: " + pair);
    groups()[0].children[1].children[0].click();           // Normal, already in force
    if (fetchCalls.some(f => f.method === "PUT")) fail("re-tapping the colour in force wrote");
    const before = fetchCalls.length;
    groups()[0].children[1].children[3].click();           // Red
    const put = fetchCalls.slice(before).find(f => f.method === "PUT");
    if (!put || put.url !== "/api/appearance/stop") fail("PUT: " + JSON.stringify(put));
    if (JSON.stringify(JSON.parse(put.body)) !== JSON.stringify({ value: "red" }))
      fail("PUT body: " + put.body);
    // Optimistic: Red is shown before the write has answered.
    const stopActive = () => groups()[0].children[1].children
      .filter(c => c.classList.contains("is-active")).map(c => c.children[0].textContent);
    if (JSON.stringify(stopActive()) !== JSON.stringify(["Red"]))
      fail("no optimistic render: " + JSON.stringify(stopActive()));
    setTimeoutReal(() => {
      const notice = byId["notice"].textContent || "";
      if (failing) {
        if (JSON.stringify(stopActive()) !== JSON.stringify(["Normal"]))
          fail("failed PUT left the optimistic colour: " + JSON.stringify(stopActive()));
        if (notice.indexOf("Couldn't save") === -1) fail("failed PUT said nothing: " + notice);
        console.log("ok: appearance tap -> PUT /api/appearance/stop -> restored on failure");
      } else {
        if (JSON.stringify(stopActive()) !== JSON.stringify(["Red"]))
          fail("saved colour not kept: " + JSON.stringify(stopActive()));
        if (notice.indexOf("Saved") === -1) fail("no Saved notice");
        console.log("ok: appearance group -> dots -> PUT /api/appearance/stop -> saved");
      }
      process.exit(0);
    }, 10);
  }, 10);
}

// The Stop group of the appearance scenarios below (their one appearance
// group), and its row labels.
function apStop() { return byId["settings-appearance-groups"].children[0]; }
function apStopActive() {
  return apStop().children[1].children.filter(c => c.classList.contains("is-active"))
    .map(c => c.children[0].textContent);
}

// A chat the viewer may not change: every colour is shown, none is
// tappable, and nothing is written.
function driveAppearanceReadOnly() {
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    if (byId["settings-appearance"].hidden) fail("the Appearance section is hidden");
    if (byId["settings-appearance-readonly"].hidden) fail("no read-only note under the colours");
    apStop().children[0].click();                          // expand
    const rows = apStop().children[1].children;
    if (rows.length !== 4) fail("want 4 colours, got " + rows.length);
    const tappable = rows.filter(r => !r.disabled).map(r => r.children[0].textContent);
    if (tappable.length) fail("tappable in a read-only chat: " + JSON.stringify(tappable));
    if (fetchCalls.some(f => f.method === "PUT")) fail("a read-only chat wrote");
    console.log("ok: appearance read-only -> every colour disabled");
    process.exit(0);
  }, 10);
}

// Writes answered out of order, or after the chat was switched: only the
// last tap in the chat on screen may decide what is shown.
function driveAppearanceRace() {
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    apStop().children[0].click();                          // expand
    apStop().children[1].children[3].click();              // Red
    if (heldPuts.length !== 1) fail("Red sent no PUT: " + heldPuts.length);
    if (SCENARIO.indexOf("appearance_chat_switch") === 0) {
      api.switchChat("-100777");
      // The other chat's Stop is Green. On the Settings tab the page loads
      // its settings (this harness opened Settings without the tab bar,
      // so load them here).
      FIXTURES["/api/preferences"].appearance.values = { stop: "green" };
      api.loadSettings();
      // Until the new chat's settings arrive, no colours at all: the old
      // chat's must not stay on screen.
      if (!byId["settings-appearance"].hidden || byId["settings-appearance-groups"].children.length)
        fail("the old chat's colours stayed on screen while the new chat loaded");
      setTimeoutReal(() => {
        if (!apStop().children[1]) apStop().children[0].click();
        if (JSON.stringify(apStopActive()) !== JSON.stringify(["Green"]))
          fail("the new chat did not load: " + JSON.stringify(apStopActive()));
        // The old chat's answer: a success (Red) or a failure (back to Normal).
        if (SCENARIO === "appearance_chat_switch_fails") heldPuts[0].settle(500, { error: "boom" });
        else heldPuts[0].settle(200, { values: { stop: "red" } });
        setTimeoutReal(() => {
          if (!apStop().children[1]) apStop().children[0].click();
          if (JSON.stringify(apStopActive()) !== JSON.stringify(["Green"]))
            fail("the old chat's save showed here: " + JSON.stringify(apStopActive()));
          if ((byId["notice"].textContent || "").indexOf("Couldn't save") !== -1)
            fail("the old chat's failure was reported here");
          console.log("ok: " + SCENARIO + " -> the old chat's save does nothing");
          process.exit(0);
        }, 10);
      }, 10);
      return;
    }
    apStop().children[1].children[2].click();              // then Green
    if (heldPuts.length !== 2) fail("Green sent no PUT: " + heldPuts.length);
    heldPuts[1].settle(200, { values: { stop: "green" } });
    setTimeoutReal(() => {
      if (SCENARIO === "appearance_superseded_fails") heldPuts[0].settle(500, { error: "boom" });
      else heldPuts[0].settle(200, { values: { stop: "red" } });
      setTimeoutReal(() => {
        if (JSON.stringify(apStopActive()) !== JSON.stringify(["Green"]))
          fail("the earlier tap's late answer won: " + JSON.stringify(apStopActive()));
        if ((byId["notice"].textContent || "").indexOf("Couldn't save") !== -1)
          fail("the earlier tap's failure was reported");
        console.log("ok: " + SCENARIO + " -> the last tap stands");
        process.exit(0);
      }, 10);
    }, 10);
  }, 10);
}

// A server that sends no colours (an empty list, or none at all) leaves no
// empty Appearance section behind.
function driveAppearanceGone() {
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    if (byId["settings-appearance"].hidden) fail("the Appearance section did not show");
    if (SCENARIO === "appearance_gone_empty") FIXTURES["/api/preferences"].appearance.schema = [];
    else delete FIXTURES["/api/preferences"].appearance;
    api.loadSettings();
    setTimeoutReal(() => {
      if (!byId["settings-appearance"].hidden) fail("an Appearance section with no colours");
      if (byId["settings-appearance-groups"].children.length) fail("colours left on screen");
      if (byId["settings-groups"].children.length !== 1) fail("the chat's preferences did not render");
      console.log("ok: " + SCENARIO + " -> no Appearance section");
      process.exit(0);
    }, 10);
  }, 10);
}

function driveScopeSave() {
  const failing = SCENARIO === "scope_save_fails";
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    const host = byId["settings-groups"];
    if (host.children.length !== 1) fail("settings group did not render: " + host.children.length);
    if (!byId["settings-appearance"].hidden) fail("an Appearance section with no appearance");
    if (scopeHeaderValue() !== "Don't apply any rule")
      fail("initial value not rendered: " + JSON.stringify(scopeHeaderValue()));
    scopeGroup().children[0].click();                     // expand
    const body = scopeGroup().children[1];
    if (!body || body.children.length !== 3) fail("group did not expand to 3 choices");
    const choice = body.children[1];
    if (choice.disabled || !choice.listeners.click) fail("choice not tappable despite can_edit");

    const before = fetchCalls.length;
    try { choice.click(); } catch (e) { fail("tapping a Settings option threw: " + e); }
    const put = fetchCalls.slice(before).find(f => f.method === "PUT");
    if (!put) fail("tapping a Settings option sent no PUT");
    if (put.url !== "/api/preferences/answer_length") fail("wrong PUT url: " + put.url);
    if (JSON.stringify(JSON.parse(put.body)) !== JSON.stringify({ value: "short" }))
      fail("wrong PUT body: " + put.body);
    // optimistic: painted before the write has answered
    if (scopeHeaderValue() !== "Short") fail("no optimistic render: " + scopeHeaderValue());
    if (JSON.stringify(scopeActiveLabels()) !== JSON.stringify(["Short"]))
      fail("optimistic active row wrong: " + JSON.stringify(scopeActiveLabels()));

    setTimeoutReal(() => {
      const notice = byId["notice"].textContent || "";
      if (failing) {
        if (scopeHeaderValue() !== "Don't apply any rule")
          fail("failed PUT left the optimistic value: " + scopeHeaderValue());
        if (JSON.stringify(scopeActiveLabels()) !== JSON.stringify(["Don't apply any rule"]))
          fail("failed PUT: active row not restored: " + JSON.stringify(scopeActiveLabels()));
        if (notice.indexOf("Couldn't save") === -1) fail("failed PUT said nothing: " + notice);
        console.log("ok: scope tap -> PUT /api/preferences/answer_length -> optimistic -> restored on failure");
      } else {
        if (scopeHeaderValue() !== "Short") fail("saved value not kept: " + scopeHeaderValue());
        if (notice.indexOf("Saved") === -1) fail("no Saved notice: " + notice);
        console.log("ok: scope tap -> PUT /api/preferences/answer_length -> optimistic -> saved");
      }
      process.exit(0);
    }, 10);
  }, 10);
}

// ---- scenario: schema text is shown plain on both settings surfaces ---
function openEveryGroup(hostId, n) {
  const host = byId[hostId];
  if (host.children.length !== n) fail(hostId + " rendered " + host.children.length + " groups");
  for (let i = 0; i < n; i++) {
    const grp = byId[hostId].children[i];
    if (!grp.children[1]) grp.children[0].click();     // the header toggles
  }
  if (byId[hostId].children.some(g => !g.children[1])) fail(hostId + ": a group stayed shut");
  return byId[hostId];
}
function checkSchemaPlain(where, host, schema) {
  const text = host.textContent;
  if (text.indexOf("\u2014") !== -1) fail(where + " shows an em dash: " + JSON.stringify(text));
  host.children.forEach((g, i) => {
    const title = g.children[0].children[0].textContent;
    if (!/^[A-Za-z0-9]/.test(title)) fail(where + " title keeps its emoji: " + JSON.stringify(title));
    const lead = schema[i].options[0].label.split(/ - | \u2014 /)[0];
    const shown = g.children[0].children[1].textContent;
    if (shown !== lead) fail(where + " header shows " + JSON.stringify(shown) +
                             ", want the label's lead " + JSON.stringify(lead));
  });
}
function driveSchemaPlain() {
  const schema = FIXTURES["/api/preferences"].schema;
  api.showView("settings");
  api.loadSettings();
  setTimeoutReal(() => {
    checkSchemaPlain("Settings tab", openEveryGroup("settings-groups", schema.length), schema);
    api.loadSessionSettings("dev");
    setTimeoutReal(() => {
      checkSchemaPlain("session settings",
                       openEveryGroup("session-settings-groups", schema.length), schema);
      console.log("ok: schema text -> no em dash, bare titles, header shows the lead");
      process.exit(0);
    }, 10);
  }, 10);
}

const DRIVERS = {
  scope_save: driveScopeSave,
  appearance_save: driveAppearanceSave,
  appearance_save_fails: driveAppearanceSave,
  appearance_read_only: driveAppearanceReadOnly,
  appearance_superseded_fails: driveAppearanceRace,
  appearance_superseded_ok: driveAppearanceRace,
  appearance_chat_switch: driveAppearanceRace,
  appearance_chat_switch_fails: driveAppearanceRace,
  appearance_gone_empty: driveAppearanceGone,
  appearance_gone_absent: driveAppearanceGone,
  app_language: driveAppLanguage,
  app_language_fails: driveAppLanguage,
  app_language_absent: driveAppLanguage,
  schema_plain: driveSchemaPlain,
  scope_save_fails: driveScopeSave,
  updates_shutting_down: driveUpdatesShuttingDown,
  updates_auto_offer: driveUpdatesAuto,
  updates_auto_current: driveUpdatesAuto,
  updates_auto_quiet: driveUpdatesAuto,
  updates_auto_offline: driveUpdatesAuto,
  updates_auto_conflict: driveUpdatesAuto,
  updates_waiting_check: driveUpdatesWaitingCheck,
  updates_auto_throttle: driveUpdatesAuto,
  updates_auto_during_job: driveUpdatesAuto,
  updates_auto_during_restart: driveUpdatesAuto,
  updates_poll_running: driveUpdatesPollRunning,
  updates_no_poll_terminal: driveUpdatesNoPollTerminal,
  updates_restart_pending: driveUpdatesRestartPending,
  updates_restart_back: driveUpdatesRestartBack,
  updates_restart_give_up: driveUpdatesRestartGiveUp,
  updates_restart_tunnel: driveUpdatesRestartTunnel,
  updates_restart_wrong_version: driveUpdatesRestartWrongVersion,
  updates_restart_watchdog: driveUpdatesRestartWatchdog,
  updates_restart_refused: driveUpdatesRestartRefused,
  updates_restart_watchdog_late: driveUpdatesRestartWatchdogLate,
  updates_restart_hang_tunnel: driveUpdatesRestartHangTunnel,
  updates_restart_stuck: driveUpdatesRestartStuck,
  updates_restart_stuck_tunnel: driveUpdatesRestartStuckTunnel,
  updates_verify_dead_tunnel: driveUpdatesVerifyDeadTunnel,
  updates_verify_hang: driveUpdatesVerifyHang,
  updates_verify_same_daemon_done: driveUpdatesVerifySameDaemonDone,
  updates_verify_blip: driveUpdatesVerifyBlip,
  updates_installed_dead_tunnel: driveUpdatesInstalledDeadTunnel,
  updates_chat_switch_lost_poll: driveUpdatesChatSwitchLostPoll,
  updates_done_lost_poll: driveUpdatesDoneLostPoll,
  updates_start_lost_poll: driveUpdatesStartLostPoll,
  updates_double_load: driveUpdatesDoubleLoad,
  updates_verify_blip_then_gone: driveUpdatesVerifyBlipThenGone,
  updates_restart_now_lost_poll: driveUpdatesRestartNowLostPoll,
  updates_upgrading_blip: driveUpdatesUpgradingBlip,
  updates_hidden: driveUpdatesHidden,
  updates_render: driveUpdatesRender,
  updates_check_then_update: driveUpdatesCheckThenUpdate,
  updates_check_aipager: driveUpdatesCheckAipager,
  updates_check_nothing_newer: driveUpdatesCheckNothingNewer,
  updates_confirm_in_place: driveUpdatesConfirm,
  updates_confirm_systemd: driveUpdatesConfirm,
  updates_confirm_manual_steps: driveUpdatesConfirm,
  updates_confirm_legacy: driveUpdatesConfirm,
  updates_forbidden: driveUpdatesForbidden,
  model_switch: driveModelSwitch,
  model_unconfirmed: driveModelSwitch,
  model_busy: driveModelBusy,
  settings: driveSettings,
  stop_busy: driveStopBusy,
  kill_idle: driveKillIdle,
  resume_gone: driveResumeGone,
  resume_gone_no_transcript: driveResumeGoneNoTranscript,
  delete_gone: driveDeleteGone,
  modal_back_closes: driveModalBackCloses,
  backdrop_cancels: driveBackdropCancels,
  no_actions: driveNoActions,
  reset_confirm: driveResetConfirm,
  menu_drift_closes: driveMenuDriftCloses,
  confirm_isolation: driveConfirmIsolation,
  perms_idle: drivePermsIdle,
  perms_busy: drivePermsBusy,
  perms_tasks: drivePermsTasks,
  perms_full_requires_admin: drivePermsFullRequiresAdmin,
  perms_in_place: drivePermsInPlace,
  perms_leave_full: drivePermsLeaveFull,
  restart_idle: driveRestartIdle,
  restart_busy: driveRestartBusy,
  clearqueue_busy: driveClearqueueBusy,
  compact_busy_queues: driveCompactBusyQueues,
  compact_idle_sends: driveCompactIdleSends,
  compact_queue_full: driveCompactQueueFull,
  rename_valid: driveRenameValid,
  rename_client_side_invalid: driveRenameClientSideInvalid,
  rename_server_conflict: driveRenameServerConflict,
  menu_grouping_divider: driveMenuGroupingDivider,
};

// ===== the lantern grid (roadmap 8.44) =================================

function trayCards() { return byId["needs-you-list"].children; }
function answerBtn(card) { return card.children[1].children[0]; }
function tiles() { return byId["sessions"].children; }
function tileLabel(t) { return t.children[1].textContent; }
function tileLamp(t) { return t.children[0].children[0]; }
function pulseText() {
  const main = byId["grid-totals"].children[0];
  return main ? main.textContent : "";
}
function clone(x) { return JSON.parse(JSON.stringify(x)); }
function posts() { return fetchCalls.filter(f => f.method === "POST"); }

function driveGridRender() {
  setTimeoutReal(() => {
    if (byId["needs-you"].hidden) fail("the Needs you tray is hidden with two sessions waiting");
    if (trayCards().length !== 2) fail("tray has " + trayCards().length + " cards, want 2");
    if (tiles().length !== 3) fail("grid has " + tiles().length + " tiles, want 3 live");
    if (tiles().some(t => /skel/.test(t.className))) fail("a skeleton tile survived the first render");
    if (JSON.stringify(tiles().map(tileLabel)) !== JSON.stringify(["charlie", "delta", "echo"]))
      fail("tiles out of server order: " + JSON.stringify(tiles().map(tileLabel)));
    if (byId["sessions-gone"].children.length !== 3) fail("shelf does not hold the 3 finished");
    if (!byId["sessions-gone"].hidden) fail("the shelf starts open");
    if (byId["gone-wrap"].hidden) fail("the shelf is hidden");
    if (pulseText() !== "2 need you, 2 working, 1 resting")
      fail("pulse sentence: " + JSON.stringify(pulseText()));
    if (byId["waiting-badge"].hidden || byId["waiting-badge"].textContent !== "2")
      fail("waiting badge does not read 2");
    const labels = trayCards().map(c => answerBtn(c).textContent);
    if (labels.some(l => l !== "Answer in chat")) fail("answer buttons: " + JSON.stringify(labels));
    const summary = trayCards()[1].children[0].children[1].children[1].textContent;
    if (summary !== "Which one - the red or the blue?")
      fail("the tray summary is not shown plain: " + JSON.stringify(summary));
    if (tileLamp(tiles()[0]).attrs.style !== "--pct:64") fail("ring does not carry the context");
    console.log("ok: grid -> 2 beacons, 3 lanterns, 3 on the shelf, pulse sentence");
    process.exit(0);
  }, 10);
}

function tileWord(t) { return t.children[0].children[1].children[0].textContent; }

// Roadmap 8.121: a session waiting for its own agents or shells names
// them on its tile, lights the work lamp, and counts with working.
function driveGridTasks() {
  setTimeoutReal(() => {
    const byLabel = {};
    tiles().forEach(t => { byLabel[tileLabel(t)] = t; });
    const tasker = byLabel.tasker;
    const finisher = byLabel.finisher;
    if (!tasker || !finisher) fail("the waiting sessions have no tiles");
    if (tileWord(tasker) !== "waiting for 2 agents and 1 shell")
      fail("tile word: " + JSON.stringify(tileWord(tasker)));
    if (tileWord(finisher) !== "finishing up")
      fail("finishing tile word: " + JSON.stringify(tileWord(finisher)));
    if (tileLamp(tasker).className !== "lamp lamp-work")
      fail("tile lamp: " + tileLamp(tasker).className);
    if (!/^tile tile-tasks\b/.test(tasker.className)) fail("tile class: " + tasker.className);
    if (tileLamp(byLabel.echo).className !== "lamp lamp-rest") fail("the idle lamp is lit");
    if (pulseText() !== "3 working, 1 resting")
      fail("pulse sentence: " + JSON.stringify(pulseText()));
    console.log("ok: grid -> waiting for its tasks reads its counts, lit, counted working");
    process.exit(0);
  }, 10);
}

function driveDetailTasks() {
  setTimeoutReal(() => {
    api.openDetail("tasker");
    setTimeoutReal(() => {
      if (byId["detail-state"].textContent !== "Waiting for 2 agents")
        fail("state line: " + JSON.stringify(byId["detail-state"].textContent));
      if (byId["detail-status"].textContent !== "working")
        fail("status chip: " + byId["detail-status"].textContent);
      if (byId["detail-status"].className !== "status status-tasks")
        fail("status chip class: " + byId["detail-status"].className);
      if (byId["detail-ring"].className !== "lamp lamp-lg lamp-work")
        fail("header ring: " + byId["detail-ring"].className);
      console.log("ok: session page -> Waiting for 2 agents, working, lit ring");
      process.exit(0);
    }, 10);
  }, 10);
}

// The counts move between two polls with nothing else changing: the tile
// must follow them (the grid's and the tile's change checks both carry
// the counts), on the same tile, still lit and counted working.
function driveGridTasksRecount() {
  setTimeoutReal(() => {
    const tile = tiles()[0];
    if (tileLabel(tile) !== "tasker") fail("first tile: " + tileLabel(tile));
    if (tileWord(tile) !== "waiting for 2 agents")
      fail("first poll tile word: " + JSON.stringify(tileWord(tile)));
    if (tileWord(tiles()[1]) !== "waiting for 1 shell")
      fail("shells-only tile word: " + JSON.stringify(tileWord(tiles()[1])));
    if (pulseText() !== "2 working, 1 resting")
      fail("first poll pulse: " + JSON.stringify(pulseText()));
    const steps = [[1, "waiting for 1 agent"], [0, "finishing up"]];
    let data = clone(FIXTURES["/api/sessions"]);
    steps.forEach(([agents, want]) => {
      data = clone(data);
      data.sessions[0].job_agents = agents;
      api.renderGrid(data);
      if (tiles()[0] !== tile) fail("a recount rebuilt the tile instead of updating it");
      if (tileWord(tile) !== want)
        fail(agents + " agents left, tile word: " + JSON.stringify(tileWord(tile)));
      if (tileLamp(tile).className !== "lamp lamp-work")
        fail(agents + " agents left, lamp: " + tileLamp(tile).className);
      if (pulseText() !== "2 working, 1 resting")
        fail(agents + " agents left, pulse: " + JSON.stringify(pulseText()));
    });
    console.log("ok: grid -> a recount moves the tile word 2 agents, 1 agent, finishing up");
    process.exit(0);
  }, 10);
}

// The session page's state line follows the counts poll by poll: agents,
// both, shells, then finishing up.
function driveDetailTasksRecount() {
  const steps = [
    [1, 1, "Waiting for 1 agent and 1 shell"],
    [0, 2, "Waiting for 2 shells"],
    [0, 0, "Finishing up"],
  ];
  function detail(agents, shells) {
    return Object.assign(fullDetailFor("tasks"), {
      label: "tasker", job_agents: agents, job_shells: shells, context_pct: 30 });
  }
  function next(i) {
    if (i === steps.length) {
      console.log("ok: session page -> the state line follows the counts to Finishing up");
      process.exit(0);
    }
    const [agents, shells, want] = steps[i];
    FIXTURES["/api/sessions/tasker"] = detail(agents, shells);
    api.pollTick();
    setTimeoutReal(() => {
      if (byId["detail-state"].textContent !== want)
        fail(agents + " agents, " + shells + " shells, state line: " +
             JSON.stringify(byId["detail-state"].textContent));
      if (byId["detail-ring"].className !== "lamp lamp-lg lamp-work")
        fail(agents + " agents, " + shells + " shells, ring: " +
             byId["detail-ring"].className);
      next(i + 1);
    }, 10);
  }
  api.openDetail("tasker");
  setTimeoutReal(() => {
    if (byId["detail-state"].textContent !== "Waiting for 2 agents")
      fail("first state line: " + JSON.stringify(byId["detail-state"].textContent));
    next(0);
  }, 10);
}

// Empty: one "New session" (the empty state's own), not two.
function driveGridEmpty() {
  setTimeoutReal(() => {
    if (byId["empty-state"].hidden) fail("the empty state is hidden with no sessions");
    if (!byId["new-session-btn"].hidden) fail("the + stays beside the empty state's button");
    console.log("ok: empty grid -> empty state with one New session button");
    process.exit(0);
  }, 10);
}

// Expired: the form cannot load, so no button may lead to it.
function driveGridExpired() {
  setTimeoutReal(() => {
    if (byId["conn-badge"].textContent !== "expired") fail("a 401 did not expire the app");
    if (!byId["new-session-btn"].hidden) fail("the + is offered while expired");
    if (!byId["empty-state"].hidden) fail("the empty state is offered while expired");
    console.log("ok: expired -> no New session button");
    process.exit(0);
  }, 10);
}

// An empty grid that loaded, then lost the server: the empty state's
// "New session" leads to a form that cannot load, so it goes too. The
// first check proves the empty state really was on screen before.
function emptyThenUnreachable(breakPoll, polls, badge, ok) {
  setTimeoutReal(() => {
    if (byId["empty-state"].hidden) fail("the empty grid did not show its empty state");
    breakPoll();
    for (let i = 0; i < polls; i++) { api.pollTick(); }
    setTimeoutReal(() => {
      if (byId["conn-badge"].textContent !== badge)
        fail("the failed poll did not reach " + badge + ": " + byId["conn-badge"].textContent);
      if (!byId["empty-state"].hidden)
        fail("the empty state and its New session button stay while " + badge);
      if (!byId["new-session-btn"].hidden) fail("the + is offered while " + badge);
      console.log(ok);
      process.exit(0);
    }, 10);
  }, 10);
}
function driveGridEmptyThenExpired() {
  emptyThenUnreachable(() => {
    POST_STATUS_OVERRIDE = { path: "/api/sessions", method: "GET", status: 401,
      body: { error: "unauthorized" } };
  }, 1, "expired", "ok: empty grid then 401 -> no New session button");
}
function driveGridEmptyThenOffline() {
  // Three straight failures after a success is the page's offline line.
  emptyThenUnreachable(() => { FETCH_REJECT_PATH = "/api/sessions"; }, 3, "offline",
    "ok: empty grid then offline -> no New session button");
}

function driveGridKeyed() {
  setTimeoutReal(() => {
    const before = tiles()[0];
    if (tileLamp(before).attrs.style !== "--pct:64") fail("first ring wrong");
    const next = clone(FIXTURES["/api/sessions"]);
    next.sessions[2].context_pct = 81;
    api.renderGrid(next);
    if (tiles()[0] !== before) fail("a changed poll rebuilt the tile instead of updating it");
    if (tileLamp(before).attrs.style !== "--pct:81")
      fail("the ring did not follow the context: " + tileLamp(before).attrs.style);
    console.log("ok: a changed poll reuses the tile and moves its ring");
    process.exit(0);
  }, 10);
}

function driveGridUnchanged() {
  setTimeoutReal(() => {
    global.__writes = 0;
    const same = clone(FIXTURES["/api/sessions"]);
    same.sessions.forEach(r => { r.last_active_seconds_ago += 7; });   // ages only
    api.renderGrid(same);
    if (global.__writes !== 0) fail("an unchanged poll made " + global.__writes + " DOM writes");
    console.log("ok: an unchanged poll touches nothing");
    process.exit(0);
  }, 10);
}

function reorderedGrid() {
  const next = clone(FIXTURES["/api/sessions"]);
  const live = next.sessions.filter(r => r.status === "busy" || r.status === "idle").reverse();
  next.sessions = next.sessions.filter(r => r.status === "waiting")
    .concat(live, next.sessions.filter(r => r.status === "gone"));
  return next;
}

function driveGridReorder() {
  setTimeoutReal(() => {
    const byLabel = {};
    tiles().forEach(t => { byLabel[tileLabel(t)] = t; });
    try { api.renderGrid(reorderedGrid()); } catch (e) { fail("reorder threw: " + e); }
    const order = tiles().map(tileLabel);
    if (JSON.stringify(order) !== JSON.stringify(["echo", "delta", "charlie"]))
      fail("reorder: " + JSON.stringify(order));
    if (tiles().some(t => byLabel[tileLabel(t)] !== t)) fail("reorder rebuilt tiles");
    if (global.__raf !== 0) fail("FLIP ran without its APIs");
    console.log("ok: reorder moves the same tiles, no FLIP without its APIs");
    process.exit(0);
  }, 10);
}

function driveGridReorderFlip() {
  setTimeoutReal(() => {
    api.renderGrid(reorderedGrid());
    if (global.__raf !== 1) fail("FLIP used " + global.__raf + " animation frames, want exactly 1");
    const stuck = tiles().filter(t => t.style.transform);
    if (stuck.length) fail("FLIP left a transform behind");
    console.log("ok: reorder animates with one requestAnimationFrame");
    process.exit(0);
  }, 10);
}

function answerCallsFor(label) {
  return posts().filter(f => f.url === "/api/sessions/" + label + "/answer");
}

function driveAnswerSingle() {
  setTimeoutReal(() => {
    answerBtn(trayCards()[0]).click();
    if (answerCallsFor("alpha").length !== 1) fail("tap did not POST the answer route once");
    setTimeoutReal(() => {
      if (global.__closed !== true) fail("one session waiting: the app should close onto the chat");
      console.log("ok: answer -> POST /api/sessions/alpha/answer -> close");
      process.exit(0);
    }, 10);
  }, 10);
}

function driveAnswerMulti() {
  setTimeoutReal(() => {
    answerBtn(trayCards()[0]).click();
    setTimeoutReal(() => {
      if (global.__closed) fail("closed with another session still waiting");
      if (byId["notice"].textContent.indexOf("Sent to the chat") === -1)
        fail("no confirmation: " + JSON.stringify(byId["notice"].textContent));
      console.log("ok: answer with two waiting -> stays and says Sent to the chat");
      process.exit(0);
    }, 10);
  }, 10);
}

function driveAnswerDouble() {
  setTimeoutReal(() => {
    const btn = answerBtn(trayCards()[0]);
    btn.click();
    btn.click();
    if (answerCallsFor("alpha").length !== 1)
      fail("a double tap sent " + answerCallsFor("alpha").length + " requests");
    if (!btn.disabled) fail("the button is not disabled while its request runs");
    console.log("ok: a double tap sends one request");
    process.exit(0);
  }, 10);
}

function driveAnswer409() {
  setTimeoutReal(() => {
    answerBtn(trayCards()[0]).click();
    setTimeoutReal(() => {
      const n = byId["notice"].textContent;
      if (n.indexOf("This session isn't waiting - it moved on.") === -1)
        fail("the server detail is not shown plain: " + JSON.stringify(n));
      console.log("ok: a refused answer shows the server's reason, plain");
      process.exit(0);
    }, 10);
  }, 10);
}

function driveAnswer403() {
  setTimeoutReal(() => {
    answerBtn(trayCards()[0]).click();
    setTimeoutReal(() => {
      if (byId["notice"].textContent.indexOf("can't answer") === -1)
        fail("403 did not say why: " + JSON.stringify(byId["notice"].textContent));
      if (byId["conn-badge"].textContent === "expired") fail("a 403 expired the whole app");
      if ((byId["error"].textContent || "").indexOf("expired") !== -1) fail("expired message shown");
      console.log("ok: 403 -> can't answer here, app not expired");
      process.exit(0);
    }, 10);
  }, 10);
}

function driveAnswerViewer() {
  setTimeoutReal(() => {
    const card = trayCards()[0];
    if (!answerBtn(card).disabled) fail("a viewer's Answer in chat is enabled");
    const note = card.children[2];
    if (note.hidden || note.textContent.indexOf("can't answer") === -1)
      fail("a disabled Answer in chat gives no reason");
    answerBtn(card).click();
    console.log("ok: viewer -> Answer in chat disabled with a reason");
    process.exit(0);
  }, 10);
}

function driveDetailAnswer() {
  setTimeoutReal(() => {
    api.openDetail("alpha");
    setTimeoutReal(() => {
      const btn = byId["detail-answer"];
      if (byId["detail-waiting"].hidden) fail("the waiting card is hidden");
      if (btn.hidden || btn.disabled) fail("Answer in chat is not offered on the session page");
      btn.click();
      if (answerCallsFor("alpha").length !== 1) fail("the detail button did not POST once");
      console.log("ok: session page -> Answer in chat -> POST /api/sessions/alpha/answer");
      process.exit(0);
    }, 10);
  }, 10);
}

Object.assign(DRIVERS, {
  grid_render: driveGridRender,
  grid_tasks: driveGridTasks,
  detail_tasks: driveDetailTasks,
  grid_tasks_recount: driveGridTasksRecount,
  detail_tasks_recount: driveDetailTasksRecount,
  grid_empty: driveGridEmpty,
  grid_expired: driveGridExpired,
  grid_empty_then_expired: driveGridEmptyThenExpired,
  grid_empty_then_offline: driveGridEmptyThenOffline,
  grid_keyed: driveGridKeyed,
  grid_unchanged: driveGridUnchanged,
  grid_reorder: driveGridReorder,
  grid_reorder_flip: driveGridReorderFlip,
  answer_single: driveAnswerSingle,
  answer_multi: driveAnswerMulti,
  answer_double: driveAnswerDouble,
  answer_409: driveAnswer409,
  answer_403: driveAnswer403,
  answer_viewer: driveAnswerViewer,
  detail_answer: driveDetailAnswer,
});

// ===== the session page (roadmap 8.44) =================================

function driveDetailActivity() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    if (byId["detail-state"].textContent !== "Working for 3:12")
      fail("state line: " + JSON.stringify(byId["detail-state"].textContent));
    if (byId["detail-status"].textContent !== "working") fail("status chip: " + byId["detail-status"].textContent);
    if (byId["detail-ring"].attrs.style !== "--pct:57") fail("header ring: " + byId["detail-ring"].attrs.style);
    const strip = byId["detail-activity"];
    if (strip.hidden) fail("the activity strip is hidden on a working session");
    const beads = strip.children.find(c => c.className === "beads");
    if (!beads || beads.children.length !== 4) fail("want 4 beads (tool rows only)");
    const classes = beads.children.map(c => c.className);
    if (classes.join(",") !== "bead bead-done,bead bead-failed,bead bead-done,bead bead-running")
      fail("bead states: " + JSON.stringify(classes));
    if (strip.textContent.indexOf("2 queued") === -1) fail("no queue chip: " + strip.textContent);
    if (strip.textContent.indexOf("Bash: pytest -q (running 0:14)") === -1)
      fail("no latest-tool line: " + strip.textContent);
    const html = byId["detail-preview"].innerHTML;
    if (html.indexOf("<code>pytest</code>") === -1 || html.indexOf("<b>") !== -1)
      fail("the reply is not escaped-then-coded: " + html);
    strip.click();
    if (byId["panel-timeline"].hidden) fail("tapping the strip did not open the timeline");
    console.log("ok: session page -> ring, state line, 4 beads, running last");
    process.exit(0);
  }, 10);
}

function driveDetailQuick(key, title) {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const pill = byId["detail-quick"];
    if (pill.hidden) fail("no " + title + " pill");
    if (pill.textContent !== title) fail("pill reads " + JSON.stringify(pill.textContent));
    const before = fetchCalls.length;
    pill.click();
    const sent = fetchCalls.slice(before);
    if (sent.length !== 1 || sent[0].method !== "POST" || sent[0].url !== "/api/sessions/dev/" + key)
      fail("the pill sent " + JSON.stringify(sent));
    if (!byId["overlay"].hidden) fail("the pill asked for confirmation");
    console.log("ok: quick " + key + " -> one POST /api/sessions/dev/" + key);
    process.exit(0);
  }, 10);
}

function driveDetailUnchanged() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    global.__writes = 0;
    api.pollTick();
    setTimeoutReal(() => {
      if (global.__writes !== 0) fail("an unchanged detail poll made " + global.__writes + " writes");
      console.log("ok: an unchanged detail poll touches nothing");
      process.exit(0);
    }, 10);
  }, 10);
}

// A busy Claude Code background job: the page asks in the app (the
// question as its title, "Stop and bring back" / "Leave it"), never a
// chat card; the confirm re-sends the same action with {"force": true}.
const BG_BUSY_DETAIL = "dev is still working in Claude Code's background: stop that work " +
  "and bring the conversation back here?";

function bgBusyOverride(path) {
  POST_STATUS_OVERRIDE = { path: path, method: "POST", status: 409,
    body: { error: "bg_busy", detail: BG_BUSY_DETAIL, can_force: true } };
}

function bgQuestionShown() {
  if (byId["confirm-modal"].hidden) fail("no in-app question for a busy job");
  if (byId["confirm-title"].textContent !== BG_BUSY_DETAIL)
    fail("question reads " + JSON.stringify(byId["confirm-title"].textContent));
  if (byId["confirm-ok"].textContent !== "Stop and bring back")
    fail("confirm reads " + JSON.stringify(byId["confirm-ok"].textContent));
  if (byId["confirm-cancel"].textContent !== "Leave it")
    fail("cancel reads " + JSON.stringify(byId["confirm-cancel"].textContent));
  if (!byId["confirm-body"].hidden) fail("the question has an empty body showing");
}

function postsTo(path, from) {
  return fetchCalls.slice(from).filter(f => f.method === "POST" && f.url === path);
}

function driveBgBusyAsks(leave) {
  const path = "/api/sessions/dev/bringback";
  bgBusyOverride(path);
  api.openDetail("dev");
  setTimeoutReal(() => {
    const before = fetchCalls.length;
    byId["detail-quick"].click();
    setTimeoutReal(() => {
      const first = postsTo(path, before);
      if (first.length !== 1 || first[0].body) fail("first ask sent " + JSON.stringify(first));
      bgQuestionShown();
      POST_STATUS_OVERRIDE = null;
      const mid = fetchCalls.length;
      if (leave) {
        byId["confirm-cancel"].click();
        setTimeoutReal(() => {
          if (postsTo(path, mid).length) fail("Leave it sent " + JSON.stringify(postsTo(path, mid)));
          if (!byId["confirm-modal"].hidden || !byId["overlay"].hidden) fail("the question stayed open");
          console.log("ok: Leave it closes the question and sends nothing");
          process.exit(0);
        }, 10);
        return;
      }
      byId["confirm-ok"].click();
      setTimeoutReal(() => {
        const again = postsTo(path, mid);
        if (again.length !== 1 || JSON.parse(again[0].body || "{}").force !== true)
          fail("Stop and bring back sent " + JSON.stringify(again));
        console.log("ok: a busy job asks in the app, then one POST with force");
        process.exit(0);
      }, 10);
    }, 10);
  }, 10);
}

function driveBgRestartBusyAsks() {
  const path = "/api/sessions/dev/restart";
  bgBusyOverride(path);
  api.openDetail("dev");
  setTimeoutReal(() => {
    byId["detail-menu-btn"].click();
    const item = menuItems(byId["action-menu"]).find(i => i.className.indexOf("act-restart") !== -1);
    if (!item) fail("no Restart in the background menu");
    item.click();
    const before = fetchCalls.length;
    byId["confirm-ok"].click();     // the restart confirm
    setTimeoutReal(() => {
      if (postsTo(path, before).length !== 1) fail("restart not sent");
      bgQuestionShown();
      POST_STATUS_OVERRIDE = null;
      const mid = fetchCalls.length;
      byId["confirm-ok"].click();
      setTimeoutReal(() => {
        const again = postsTo(path, mid);
        if (again.length !== 1 || JSON.parse(again[0].body || "{}").force !== true)
          fail("restart again sent " + JSON.stringify(again));
        console.log("ok: a busy job's restart asks in the app, then restart with force");
        process.exit(0);
      }, 10);
    }, 10);
  }, 10);
}

function driveBgPermsBusyAsks() {
  const path = "/api/sessions/dev/perms";
  bgBusyOverride(path);
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    const before = fetchCalls.length;
    itemNamed(menu, "Switch to Full access mode").click();
    byId["confirm-ok"].click();     // the mode switch's confirm
    setTimeoutReal(() => {
      const first = postsTo(path, before);
      if (first.length !== 1 || JSON.parse(first[0].body || "{}").force !== undefined)
        fail("first switch sent " + JSON.stringify(first));
      bgQuestionShown();
      POST_STATUS_OVERRIDE = null;
      const mid = fetchCalls.length;
      byId["confirm-ok"].click();
      setTimeoutReal(() => {
        const again = postsTo(path, mid);
        const body = again.length === 1 ? JSON.parse(again[0].body || "{}") : {};
        if (body.force !== true || body.mode !== "full")
          fail("Stop and bring back sent " + JSON.stringify(again));
        console.log("ok: a busy job's mode switch asks in the app, then the same mode with force");
        process.exit(0);
      }, 10);
    }, 10);
  }, 10);
}

function driveBgMenu() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    byId["detail-menu-btn"].click();
    const items = menuItems(byId["action-menu"]).map(n => n.className.split(" ")
      .filter(c => c.indexOf("act-") === 0)[0]);
    if (JSON.stringify(items) !== JSON.stringify(
        ["act-bringback", "act-stop", "act-rename", "act-kill", "act-restart"]))
      fail("background menu: " + JSON.stringify(items));
    console.log("ok: background menu -> bringback, stop, rename, kill, restart");
    process.exit(0);
  }, 10);
}

Object.assign(DRIVERS, {
  bg_quick_bringback: () => driveDetailQuick("bringback", "Bring it back"),
  bg_busy_asks_in_app: () => driveBgBusyAsks(false),
  bg_busy_leave_it: () => driveBgBusyAsks(true),
  bg_restart_busy_asks: driveBgRestartBusyAsks,
  bg_perms_busy_asks: driveBgPermsBusyAsks,
  bg_menu: driveBgMenu,
});

Object.assign(DRIVERS, {
  detail_activity: driveDetailActivity,
  detail_quick_stop: () => driveDetailQuick("stop", "Stop"),
  detail_quick_resume: () => driveDetailQuick("resume", "Resume"),
  detail_unchanged: driveDetailUnchanged,
});

// ===== the Telegram layer (roadmap 8.44) ================================

function driveDetailWaitingMainButton() {
  setTimeoutReal(() => {
    if (!document.documentElement.classList.contains("has-mainbutton"))
      fail("no has-mainbutton class with a MainButton present");
    if (global.__mbParams.is_visible) fail("MainButton shown on the grid");
    api.openDetail("alpha");
    setTimeoutReal(() => {
      const p = global.__mbParams;
      if (p.text !== "Answer in chat" || !p.is_visible || !p.is_active)
        fail("MainButton on a waiting session: " + JSON.stringify(p));
      global.__main();
      if (answerCallsFor("alpha").length !== 1) fail("MainButton did not POST the answer once");
      setTimeoutReal(() => {
        byId["detail-menu-btn"].click();
        if (byId["overlay"].hidden) fail("menu did not open");
        if (global.__mbParams.is_visible) fail("MainButton stayed up under the open menu");
        if (global.__swOff !== 1) fail("swipes not disabled while the menu is open");
        global.__back();
        if (!global.__mbParams.is_visible) fail("MainButton did not come back after the menu");
        if (global.__swOn !== 1) fail("swipes not re-enabled after the menu");
        global.__back();
        if (global.__mbParams.is_visible) fail("MainButton stayed up on the grid");
        console.log("ok: waiting session -> MainButton Answer in chat, hidden under the menu");
        process.exit(0);
      }, 10);
    }, 10);
  }, 10);
}

function driveThemeChanged() {
  setTimeoutReal(() => {
    const root = document.documentElement;
    if (root.getAttribute("data-scheme") !== "light") fail("no data-scheme at start");
    if (global.__header !== "secondary_bg_color") fail("header colour not set");
    const handler = global.__events.themeChanged;
    if (typeof handler !== "function") fail("no themeChanged handler registered");
    window.Telegram.WebApp.colorScheme = "dark";
    handler();
    if (root.getAttribute("data-scheme") !== "dark") fail("themeChanged did not switch data-scheme");
    console.log("ok: themeChanged -> data-scheme dark");
    process.exit(0);
  }, 10);
}

Object.assign(DRIVERS, {
  detail_waiting_mainbutton: driveDetailWaitingMainButton,
  theme_changed: driveThemeChanged,
});

// ===== the chat switcher (roadmap 8.73) ================================

function chips() { return byId["chat-switch"].children; }
function chipNamed(text) {
  const hit = chips().find(c => c.textContent === text);
  if (!hit) fail("no chip named " + text + ": " + JSON.stringify(chips().map(c => c.textContent)));
  return hit;
}
function gridCalls() { return fetchCalls.filter(f => f.url === "/api/sessions" && f.method === "GET"); }
function scopeOf(call) { return call.headers["X-Aipager-Scope"] || ""; }
// Every request sent after /api/chats answered names the chat on screen.
function callsAfterChats() {
  if (global.__chatsAt === undefined) fail("the page never learned its chats");
  return fetchCalls.slice(global.__chatsAt);
}
function liveLabels() { return tiles().map(tileLabel); }

function driveChatsOne() {
  setTimeoutReal(() => {
    if (!byId["chat-switch"].hidden) fail("the switcher shows with one chat");
    if (chips().length) fail("the switcher drew chips for one chat");
    if (JSON.stringify(liveLabels()) !== '["mine"]') fail("grid: " + JSON.stringify(liveLabels()));
    api.pollTick();
    setTimeoutReal(() => {
      const later = callsAfterChats();
      if (!later.length) fail("no request after /api/chats");
      if (later.some(f => scopeOf(f) !== CHAT_DM))
        fail("a request did not name the one chat: " + JSON.stringify(later.map(scopeOf)));
      if (Object.keys(STORE).length) fail("a one-chat page stored a choice");
      console.log("ok: one chat -> no switcher, every call names it");
      process.exit(0);
    }, 10);
  }, 10);
}

function driveChatsTwo() {
  setTimeoutReal(() => {
    if (byId["chat-switch"].hidden) fail("the switcher is hidden with two chats");
    if (JSON.stringify(chips().map(c => c.textContent)) !== '["team","owner DM"]')
      fail("chips: " + JSON.stringify(chips().map(c => c.textContent)));
    if (!chipNamed("owner DM").className.includes("is-active")) fail("the default chat is not active");
    if (JSON.stringify(liveLabels()) !== '["mine"]') fail("grid before: " + JSON.stringify(liveLabels()));
    chipNamed("team").click();
    setTimeoutReal(() => {
      if (JSON.stringify(liveLabels()) !== '["grp"]') fail("grid after switch: " + JSON.stringify(liveLabels()));
      const last = gridCalls()[gridCalls().length - 1];
      if (scopeOf(last) !== CHAT_GROUP) fail("the switch did not poll the group: " + scopeOf(last));
      if (STORE["aipager.chat"] !== CHAT_GROUP) fail("the choice was not remembered");
      if (!chipNamed("team").className.includes("is-active")) fail("the chosen chip is not active");
      // Sub-pages hide it, the top level brings it back.
      api.openDetail("grp");
      if (!byId["chat-switch"].hidden) fail("the switcher shows on a session page");
      api.showGrid();
      if (byId["chat-switch"].hidden) fail("the switcher did not come back on the grid");
      // Settings follow the chosen chat, and switching there stays there.
      byId["maintab-settings"].click();
      const prefs = fetchCalls.filter(f => f.url === "/api/preferences");
      if (!prefs.length || scopeOf(prefs[prefs.length - 1]) !== CHAT_GROUP)
        fail("settings did not load for the group");
      chipNamed("owner DM").click();
      setTimeoutReal(() => {
        const again = fetchCalls.filter(f => f.url === "/api/preferences");
        if (scopeOf(again[again.length - 1]) !== CHAT_DM) fail("settings did not reload for the DM");
        if (byId["view-settings"].hidden) fail("switching left the Settings tab");
        const unnamed = callsAfterChats().filter(f => !scopeOf(f));
        if (unnamed.length) fail("requests without a chat: " + JSON.stringify(unnamed.map(f => f.url)));
        console.log("ok: two chats -> switcher, switch polls and remembers, settings follow");
        process.exit(0);
      }, 10);
    }, 10);
  }, 10);
}

function driveChatsSaved() {
  setTimeoutReal(() => {
    const calls = gridCalls();
    if (!calls.length) fail("no grid poll");
    if (calls.some(f => scopeOf(f) !== CHAT_GROUP))
      fail("a poll went out for another chat: " + JSON.stringify(calls.map(scopeOf)));
    if (JSON.stringify(liveLabels()) !== '["grp"]') fail("grid: " + JSON.stringify(liveLabels()));
    if (!chipNamed("team").className.includes("is-active")) fail("the saved chat is not active");
    console.log("ok: a saved chat -> the first poll is already for it");
    process.exit(0);
  }, 10);
}

function driveChatsSavedStale() {
  setTimeoutReal(() => {
    if (STORE["aipager.chat"] !== "") fail("the stale choice was not forgotten: " + STORE["aipager.chat"]);
    if (fetchCalls.some(f => scopeOf(f) === "-999")) fail("the page sent a chat it is not in");
    if (JSON.stringify(liveLabels()) !== '["mine"]') fail("grid: " + JSON.stringify(liveLabels()));
    if (!chipNamed("owner DM").className.includes("is-active")) fail("the default chat is not active");
    console.log("ok: a stale saved chat -> the default chat");
    process.exit(0);
  }, 10);
}

function driveChatsStorageThrows() {
  setTimeoutReal(() => {
    if (JSON.stringify(liveLabels()) !== '["mine"]') fail("grid: " + JSON.stringify(liveLabels()));
    try { chipNamed("team").click(); } catch (e) { fail("switching threw without storage: " + e); }
    setTimeoutReal(() => {
      if (JSON.stringify(liveLabels()) !== '["grp"]') fail("grid after switch: " + JSON.stringify(liveLabels()));
      console.log("ok: no storage -> the switcher still works");
      process.exit(0);
    }, 10);
  }, 10);
}

function driveChatsLate() {
  setTimeoutReal(() => {
    if (global.__held.length !== 1) fail("the default chat's first answer was not held");
    chipNamed("team").click();
    setTimeoutReal(() => {
      if (JSON.stringify(liveLabels()) !== '["grp"]') fail("grid after switch: " + JSON.stringify(liveLabels()));
      global.__held.shift()();          // the old chat's answer lands now
      setTimeoutReal(() => {
        if (JSON.stringify(liveLabels()) !== '["grp"]')
          fail("a late answer for the old chat replaced the grid: " + JSON.stringify(liveLabels()));
        console.log("ok: a late answer for the chat left behind is dropped");
        process.exit(0);
      }, 10);
    }, 10);
  }, 10);
}

function driveChatsLateSettings() {
  setTimeoutReal(() => {
    byId["maintab-settings"].click();       // the DM's settings: held
    if (global.__held.length !== 1) fail("the default chat's settings were not held");
    chipNamed("team").click();
    setTimeoutReal(() => {
      const got = api.getSettings();
      if (!got || got.values.answer_length !== "short") fail("the group's settings did not load");
      global.__held.shift()();          // the DM's settings land now
      setTimeoutReal(() => {
        if (api.getSettings().values.answer_length !== "short")
          fail("late settings for the old chat replaced the group's");
        console.log("ok: late settings for the chat left behind are dropped");
        process.exit(0);
      }, 10);
    }, 10);
  }, 10);
}

function driveChatsLateOptions() {
  // The form's text fields (this shim's elements carry no value by default).
  ["new-name", "new-model-name", "new-folder-name"].forEach((id) => { byId[id].value = ""; });
  setTimeoutReal(() => {
    api.openNewSession();                    // the DM's options: held
    if (global.__held.length !== 1) fail("the default chat's options were not held");
    api.showGrid();
    chipNamed("team").click();
    setTimeoutReal(() => {
      global.__held.shift()();          // the DM's options land now
      setTimeoutReal(() => {
        const got = api.getNew();
        if (got.options !== null || got.cwd !== "")
          fail("late options for the old chat were kept: " + JSON.stringify(got));
        api.openNewSession();
        setTimeoutReal(() => {
          if (api.getNew().cwd !== "/grp") fail("the form did not take the group's folder: " + api.getNew().cwd);
          console.log("ok: late new-session options for the chat left behind are dropped");
          process.exit(0);
        }, 10);
      }, 10);
    }, 10);
  }, 10);
}

function driveChatsLateUpdates() {
  setTimeoutReal(() => {
    byId["maintab-settings"].click();       // DM settings, then its /api/update: held
    setTimeoutReal(() => {
      if (global.__held.length !== 1) fail("the default chat's update status was not held");
      chipNamed("team").click();
      setTimeoutReal(() => {
        if (!byId["updates-block"].hidden) fail("the group shows an Updates block");
        global.__held.shift()();        // the DM's update status lands now
        setTimeoutReal(() => {
          if (!byId["updates-block"].hidden)
            fail("a late update status for the old chat showed the Updates block");
          console.log("ok: a late update status for the chat left behind is dropped");
          process.exit(0);
        }, 10);
      }, 10);
    }, 10);
  }, 10);
}

function driveChatsLatePrefWrite(failing) {
  setTimeoutReal(() => {
    byId["maintab-settings"].click();       // the DM's settings
    setTimeoutReal(() => {
      api.savePreference("answer_length", "medium");   // its PUT: held
      if (global.__held.length !== 1) fail("the DM's settings write was not held");
      chipNamed("team").click();
      setTimeoutReal(() => {
        if (api.getSettings().values.answer_length !== "short") fail("the group's settings did not load");
        global.__held.shift()();        // the DM's write answers now
        setTimeoutReal(() => {
          if (api.getSettings().values.answer_length !== "short")
            fail("the DM's saved values replaced the group's: " +
                 JSON.stringify(api.getSettings().values));
          if (byId["notice"].textContent.indexOf("Saved") >= 0)
            fail("said Saved. for a write in the chat left behind");
          if (byId["notice"].textContent.indexOf("Couldn't save") >= 0)
            fail("reported a failed write in the chat left behind");
          console.log(failing
            ? "ok: a settings write refused after a switch leaves the new chat alone"
            : "ok: a settings write answered after a switch leaves the new chat alone");
          process.exit(0);
        }, 10);
      }, 10);
    }, 10);
  }, 10);
}

// Both chats may update: the new chat's block must not stay on
// "Checking…" once the old chat's check answers.
function driveChatsLateCheckBoth() {
  setTimeoutReal(() => {
    byId["maintab-settings"].click();
    setTimeoutReal(() => {
      api.checkUpdates();               // the DM's check: held
      if (global.__held.length !== 1) fail("the DM's update check was not held");
      chipNamed("team").click();
      setTimeoutReal(() => {
        global.__held.shift()();        // the DM's check answers now
        setTimeoutReal(() => {
          if (byId["updates-block"].hidden) fail("the group's Updates block is hidden");
          if (actionLabels().length || !checkReady())
            fail("the group's Updates block after the old check: " +
                 JSON.stringify([actionLabels(), checkLabel()]));
          if (lineTexts().length) fail("the old chat's check was shown: " + JSON.stringify(lineTexts()));
          console.log("ok: an update check answered after a switch leaves the new chat's block ready");
          process.exit(0);
        }, 10);
      }, 10);
    }, 10);
  }, 10);
}

function driveChatsLateCheck() {
  setTimeoutReal(() => {
    byId["maintab-settings"].click();       // the DM's settings: it may update
    setTimeoutReal(() => {
      api.checkUpdates();               // its check: held
      if (global.__held.length !== 1) fail("the DM's update check was not held");
      chipNamed("team").click();
      setTimeoutReal(() => {
        if (!byId["updates-block"].hidden) fail("the group shows an Updates block");
        global.__held.shift()();        // the DM's check answers now
        setTimeoutReal(() => {
          if (!byId["updates-block"].hidden)
            fail("an update check from the chat left behind showed the Updates block");
          console.log("ok: an update check answered after a switch stays hidden");
          process.exit(0);
        }, 10);
      }, 10);
    }, 10);
  }, 10);
}

Object.assign(DRIVERS, {
  chats_late_pref_write: () => driveChatsLatePrefWrite(false),
  chats_late_pref_fail: () => driveChatsLatePrefWrite(true),
  chats_late_check: driveChatsLateCheck,
  chats_late_check_both: driveChatsLateCheckBoth,
  chats_late_options: driveChatsLateOptions,
  chats_late_updates: driveChatsLateUpdates,
  chats_late_settings: driveChatsLateSettings,
  chats_one: driveChatsOne,
  chats_two: driveChatsTwo,
  chats_saved: driveChatsSaved,
  chats_saved_stale: driveChatsSavedStale,
  chats_storage_throws: driveChatsStorageThrows,
  chats_late: driveChatsLate,
});
(DRIVERS[SCENARIO] || (() => fail("unknown scenario: " + SCENARIO)))();
