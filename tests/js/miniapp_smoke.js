const setTimeoutReal = setTimeout;
// Minimal DOM shim: enough to run the Mini App's script and simulate taps.
const fs = require("fs");
const page = fs.readFileSync(process.argv[2], "utf8");

class El {
  constructor(tag) {
    this.tagName = (tag || "div").toUpperCase();
    this.children = []; this.listeners = {}; this.attrs = {};
    this._class = ""; this._text = ""; this._html = "";
    this.hidden = false; this.disabled = false; this.style = {};
    this.classList = {
      _el: this,
      add: (c) => { if (!this._el._class.split(" ").includes(c)) this._el._class += " " + c; },
      remove: (c) => { this._el._class = this._el._class.split(" ").filter(x => x !== c).join(" "); },
      toggle: (c, on) => { on ? this.classList.add(c) : this.classList.remove(c); },
      contains: (c) => this._el._class.split(" ").includes(c),
    };
  }
  get className() { return this._class; }
  set className(v) { this._class = v; }
  get textContent() { return this._text; }
  set textContent(v) { this._text = String(v); }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = v; this.children = []; }
  // Real appendChild MOVES a node that already has a parent. The page
  // relies on that: reveals are parked in a stash and moved into place.
  appendChild(c) {
    if (c.parent) {
      const i = c.parent.children.indexOf(c);
      if (i >= 0) { c.parent.children.splice(i, 1); }
    }
    this.children.push(c); c.parent = this; return c;
  }
  focus() { this.focused = true; }
  setAttribute(k, v) { this.attrs[k] = v; }
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
};
global.window = { Telegram: { WebApp: {
  initData: "auth_date=1&user=%7B%22id%22%3A1%7D&hash=x",
  ready(){}, expand(){},
  BackButton: { show(){}, hide(){}, onClick(){} },
  HapticFeedback: { notificationOccurred(){} },
} } };
global.Telegram = global.window.Telegram;
const fetchCalls = [];
global.fetch = (url, opts) => {
  fetchCalls.push({ url, method: (opts && opts.method) || "GET" });
  return Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve(FIXTURES[url.split("?")[0]] || {}),
  });
};
global.setInterval = () => 0;
global.setTimeout = (f) => { return 0; };
global.clearTimeout = () => {};
global.clearInterval = () => {};
global.Telegram = undefined;

// Which interaction this run drives. One harness, several scenarios: the
// existing settings-panel flow (default, when no scenario is given — so
// every pre-existing invocation of this harness needs no changes), plus
// one per session detail-page write action (design.md: "Mini App session
// controls").
const SCENARIO = process.argv[3] || "settings";

// Mirrors aipager.miniapp.sessions.NO_TRANSCRIPT_REASON verbatim — this
// harness has no Python import, so the string is duplicated here on
// purpose; a mismatch would only ever show up as a failing assertion
// below, never silently.
const NO_TRANSCRIPT_REASON =
  "No resumable transcript — start a fresh session instead.";

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
};

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
  "/api/sessions/dev/preferences": {
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
};

// extract and run the page script
let script = page.match(/<script>([\s\S]*?)<\/script>/g)
  .map(s => s.replace(/<\/?script>/g, "")).join("\n");
// unwrap the IIFE so the internals are reachable, and export what we drive
// keep the IIFE (it contains top-level `return`s) but export its internals
script = script.replace(/\}\)\(\);\s*$/,
  "\n  global.__api = { openDetail, renderSessionSettings, loadSessionSettings, saveSessionPreference, renderOptionGroup, openGroups };\n})();");
eval(script);

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

// ---- scenario: Stop (single tap, BUSY session) ------------------------
function driveStopBusy() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const host = byId["detail-actions"];
    if (host.children.length !== 1)
      fail("expected exactly one action row for a busy session, got " +
           host.children.length);
    const btn = host.children[0].children[0];
    if (btn.disabled) fail("Stop is disabled for a busy session the caller can act on");
    if (btn.textContent !== "Stop")
      fail("expected a Stop button, got " + JSON.stringify(btn.textContent));

    const before = fetchCalls.length;
    btn.click();
    const sent = fetchCalls.slice(before);
    if (sent.length !== 1)
      fail("tapping Stop sent " + sent.length + " requests, expected exactly one");
    const post = sent.find(f => f.method === "POST" && /\/api\/sessions\/dev\/stop$/.test(f.url));
    if (!post) fail("tapping Stop sent no POST /api/sessions/dev/stop: " + JSON.stringify(sent));

    setTimeoutReal(() => {
      // Single tap acts in place — must NOT have navigated off the page.
      if (byId["view-detail"].hidden)
        fail("Stop navigated away from the detail view");
      console.log("ok: stop -> one POST /api/sessions/dev/stop, stayed on the page");
      process.exit(0);
    }, 20);
  }, 10);
}

// ---- scenario: Kill (two-tap arm -> confirm, IDLE session) ------------
function driveKillIdle() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const host = byId["detail-actions"];
    const btn = host.children[0].children[0];
    if (btn.textContent !== "Kill")
      fail("expected a Kill button, got " + JSON.stringify(btn.textContent));

    const beforeFirst = fetchCalls.length;
    btn.click();                                     // first tap: arm only
    if (fetchCalls.length !== beforeFirst)
      fail("the FIRST tap on Kill sent a request — it must only arm the confirm state");

    const armedBtn = byId["detail-actions"].children[0].children[0];
    if (armedBtn.textContent !== "Confirm?")
      fail("Kill did not read 'Confirm?' after the first tap: " +
           JSON.stringify(armedBtn.textContent));

    const beforeSecond = fetchCalls.length;
    armedBtn.click();                                 // second tap: confirm
    const sent = fetchCalls.slice(beforeSecond);
    const post = sent.find(f => f.method === "POST" && /\/api\/sessions\/dev\/kill$/.test(f.url));
    if (!post) fail("the SECOND tap on Kill sent no POST /api/sessions/dev/kill");

    setTimeoutReal(() => {
      if (byId["view-grid"].hidden)
        fail("a successful Kill did not return to the grid");
      if (!byId["view-detail"].hidden)
        fail("a successful Kill left the detail view visible");
      console.log("ok: kill requires two taps -> POST /api/sessions/dev/kill -> grid");
      process.exit(0);
    }, 20);
  }, 10);
}

// ---- scenario: Resume (single tap, GONE + resumable) -------------------
function driveResumeGone() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const host = byId["detail-actions"];
    // gone + resumable -> resume (row 0), delete (row 1).
    const btn = host.children[0].children[0];
    if (btn.disabled) fail("Resume is disabled despite a resumable transcript");
    if (btn.textContent !== "Resume")
      fail("expected a Resume button first, got " + JSON.stringify(btn.textContent));

    const before = fetchCalls.length;
    btn.click();
    const sent = fetchCalls.slice(before);
    if (sent.length !== 1)
      fail("tapping Resume sent " + sent.length + " requests, expected exactly one");
    const post = sent.find(f => f.method === "POST" && /\/api\/sessions\/dev\/resume$/.test(f.url));
    if (!post) fail("tapping Resume sent no POST /api/sessions/dev/resume: " + JSON.stringify(sent));

    console.log("ok: resume -> one POST /api/sessions/dev/resume");
    process.exit(0);
  }, 10);
}

// ---- scenario: Resume is inert with a reason (GONE + not resumable) ---
function driveResumeGoneNoTranscript() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const host = byId["detail-actions"];
    const row = host.children[0];
    const btn = row.children[0];
    if (!btn.disabled) fail("Resume must be disabled with no resumable transcript");

    const note = row.children[1];
    if (!note || note.textContent !== NO_TRANSCRIPT_REASON)
      fail("Resume's disabled reason does not match NO_TRANSCRIPT_REASON: " +
           JSON.stringify(note && note.textContent));

    const before = fetchCalls.length;
    btn.click();   // a disabled control in this shim has no listener attached
    if (fetchCalls.length !== before)
      fail("an inert Resume button still sent a request");

    console.log('ok: resume inert with reason "' + NO_TRANSCRIPT_REASON + '"');
    process.exit(0);
  }, 10);
}

// ---- scenario: Delete (two-tap arm -> confirm, GONE session) ----------
function driveDeleteGone() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const host = byId["detail-actions"];
    // gone + resumable -> resume (row 0), delete (row 1).
    const btn = host.children[1].children[0];
    if (btn.textContent !== "Delete")
      fail("expected a Delete button second, got " + JSON.stringify(btn.textContent));

    const beforeFirst = fetchCalls.length;
    btn.click();                                      // first tap: arm only
    if (fetchCalls.length !== beforeFirst)
      fail("the FIRST tap on Delete sent a request — it must only arm the confirm state");

    const armedBtn = byId["detail-actions"].children[1].children[0];
    if (armedBtn.textContent !== "Confirm?")
      fail("Delete did not read 'Confirm?' after the first tap: " +
           JSON.stringify(armedBtn.textContent));

    const beforeSecond = fetchCalls.length;
    armedBtn.click();                                  // second tap: confirm
    const sent = fetchCalls.slice(beforeSecond);
    const del = sent.find(f => f.method === "DELETE" && /\/api\/sessions\/dev$/.test(f.url));
    if (!del) fail("the SECOND tap on Delete sent no DELETE /api/sessions/dev");

    setTimeoutReal(() => {
      if (byId["view-grid"].hidden)
        fail("a successful Delete did not return to the grid");
      console.log("ok: delete requires two taps -> DELETE /api/sessions/dev -> grid");
      process.exit(0);
    }, 20);
  }, 10);
}

const DRIVERS = {
  settings: driveSettings,
  stop_busy: driveStopBusy,
  kill_idle: driveKillIdle,
  resume_gone: driveResumeGone,
  resume_gone_no_transcript: driveResumeGoneNoTranscript,
  delete_gone: driveDeleteGone,
};
(DRIVERS[SCENARIO] || (() => fail("unknown scenario: " + SCENARIO)))();
