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
  set className(v) { this._class = v; }
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
  set textContent(v) { this._text = String(v); this.children = []; }
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
  // Capture the handler so a scenario can press Back for real. The
  // page registers it once at startup; discarding it here made the
  // back-button behaviour untestable.
  BackButton: { show(){}, hide(){}, onClick(fn){ global.__back = fn; } },
  HapticFeedback: { notificationOccurred(){} },
} } };
global.Telegram = global.window.Telegram;
const fetchCalls = [];
let DETAIL_OVERRIDE = null;   // see driveMenuDriftCloses
// A scenario can make ONE specific write route answer something other
// than its default FIXTURES entry — used by driveRenameServerConflict
// to model the server refusing a rename with a 409.
let POST_STATUS_OVERRIDE = null; // { path, method, status, body }
global.fetch = (url, opts) => {
  const method = (opts && opts.method) || "GET";
  fetchCalls.push({ url, method });
  const path = url.split("?")[0];
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
      return Promise.resolve(FIXTURES[path] || {});
    },
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
const PERMS_ADMIN_REQUIRED_REASON = "Switching to Auto mode requires admin.";
const QUEUE_EMPTY_REASON = "Nothing queued to clear.";
const QUEUE_FULL_REASON =
  "Queue is full (50 pending) — clear it or wait for it to drain.";
const QUEUE_CAP = 50;

function fullActionsFor(status, opts) {
  opts = opts || {};
  const isAdmin = !!opts.isAdmin;
  const skipPerms = !!opts.skipPerms;
  const queueDepth = opts.queueDepth || 0;
  const resumable = !!opts.resumable;

  const permsEntry = (!skipPerms && !isAdmin)
    ? { available: false, reason: PERMS_ADMIN_REQUIRED_REASON }
    : { available: true, reason: null };

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
    skip_perms: !!opts.skipPerms, queue_depth: opts.queueDepth || 0,
    last_message: "", timeline: [], facts: [],
    actions: fullActionsFor(status, opts),
  };
}

Object.assign(SESSION_DETAIL_FIXTURES, {
  perms_idle: fullDetailFor("idle", { isAdmin: true, skipPerms: false }),
  perms_busy: fullDetailFor("busy", { isAdmin: true, skipPerms: false }),
  perms_auto_requires_admin: fullDetailFor("idle", { isAdmin: false, skipPerms: false }),
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
  "/api/sessions/dev/perms": { status: "switched", label: "dev", skip_perms: true },
  "/api/sessions/dev/clearqueue": { status: "cleared", label: "dev", dropped: 3 },
  "/api/sessions/dev/compact": { status: "queued", label: "dev" },
  "/api/sessions/dev/restart": { status: "restarted", label: "dev" },
  "/api/sessions/dev/rename": {
    status: "renamed", label: "frontend", previous_label: "dev", changed: true,
  },
};

// extract and run the page script
let script = page.match(/<script>([\s\S]*?)<\/script>/g)
  .map(s => s.replace(/<\/?script>/g, "")).join("\n");
// unwrap the IIFE so the internals are reachable, and export what we drive
// keep the IIFE (it contains top-level `return`s) but export its internals
script = script.replace(/\}\)\(\);\s*$/,
  "\n  global.__api = { openDetail, renderSessionSettings, loadSessionSettings, saveSessionPreference, renderOptionGroup, openGroups, pollTick };\n})();");
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
    if (!note || note.textContent !== NO_TRANSCRIPT_REASON)
      fail("Resume's disabled reason does not match NO_TRANSCRIPT_REASON: " +
           JSON.stringify(note && note.textContent));

    const before = fetchCalls.length;
    item.click();   // a disabled control in this shim has no listener attached
    if (fetchCalls.length !== before)
      fail("an inert Resume still sent a request");

    console.log('ok: resume inert with reason "' + NO_TRANSCRIPT_REASON + '"');
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

// ---- scenario: perms, IDLE, admin targeting Auto -----------------------
function drivePermsIdle() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    const item = itemNamed(menu, "Switch to Auto");
    if (item.disabled) fail("perms must be available for an admin targeting Auto");

    const before = fetchCalls.length;
    item.click();
    if (fetchCalls.length !== before)
      fail("choosing perms from the menu issued a request before confirming");
    if (!modalIsOpen()) fail("choosing perms did not open the confirm modal");
    if (byId["confirm-title"].textContent.indexOf("Auto") === -1)
      fail("confirm title does not name the target mode: " +
           JSON.stringify(byId["confirm-title"].textContent));
    if (byId["confirm-ok"].textContent !== "Switch")
      fail("idle perms confirm label should be 'Switch', got " +
           JSON.stringify(byId["confirm-ok"].textContent));

    byId["confirm-ok"].click();
    const sent = fetchCalls.slice(before);
    if (sent.length !== 1)
      fail("confirming perms sent " + sent.length + " requests, expected one");
    if (!sent.find(f => f.method === "POST" && /\/dev\/perms$/.test(f.url)))
      fail("confirming perms sent no POST /api/sessions/dev/perms");

    console.log("ok: perms idle -> menu -> modal -> POST /api/sessions/dev/perms");
    process.exit(0);
  }, 10);
}

// ---- scenario: perms, BUSY -- Stop task & switch wording ---------------
function drivePermsBusy() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    const item = itemNamed(menu, "Switch to Auto");

    item.click();
    if (!modalIsOpen())
      fail("choosing perms on a busy session did not open the confirm modal");
    if (byId["confirm-title"].textContent.indexOf("Stop the current task") === -1)
      fail("busy perms confirm must use the stop-task wording: " +
           JSON.stringify(byId["confirm-title"].textContent));
    if (byId["confirm-ok"].textContent !== "Stop task & switch")
      fail("busy perms confirm label wrong: " +
           JSON.stringify(byId["confirm-ok"].textContent));
    if (byId["confirm-cancel"].textContent !== "Not now")
      fail("busy perms cancel label wrong: " +
           JSON.stringify(byId["confirm-cancel"].textContent));

    const before = fetchCalls.length;
    byId["confirm-ok"].click();
    const sent = fetchCalls.slice(before);
    if (!sent.find(f => f.method === "POST" && /\/dev\/perms$/.test(f.url)))
      fail("confirming busy perms sent no POST /api/sessions/dev/perms");

    console.log("ok: perms busy -> Stop task & switch wording -> POST /api/sessions/dev/perms");
    process.exit(0);
  }, 10);
}

// ---- scenario: perms, non-admin targeting Auto -- inert with reason ----
function drivePermsAutoRequiresAdmin() {
  api.openDetail("dev");
  setTimeoutReal(() => {
    const menu = openMenu();
    const item = itemNamed(menu, "Switch to Auto");
    if (!item.disabled) fail("a non-admin targeting Auto must see perms disabled");

    const note = menu.children.find(c => c.className === "menu-note");
    if (!note || note.textContent.indexOf("admin") === -1)
      fail("disabled perms must state the admin reason: " +
           JSON.stringify(note && note.textContent));

    const before = fetchCalls.length;
    item.click();   // a disabled control in this shim has no listener attached
    if (fetchCalls.length !== before) fail("an inert perms row still sent a request");

    console.log("ok: perms auto requires admin -> disabled with reason, no fetch");
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

const DRIVERS = {
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
  perms_auto_requires_admin: drivePermsAutoRequiresAdmin,
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
(DRIVERS[SCENARIO] || (() => fail("unknown scenario: " + SCENARIO)))();
