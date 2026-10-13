const setTimeoutReal = setTimeout;
// Minimal DOM shim: enough to run the Mini App's script and simulate taps.
const fs = require("fs");
const page = fs.readFileSync(process.argv[2], "utf8");

class El {
  constructor(tag) {
    this.tagName = (tag || "div").toUpperCase();
    this.children = []; this.listeners = {}; this.attrs = {};
    this._class = ""; this._text = ""; this._html = "";
    this.hidden = false; this.disabled = false; this.style = {}; this.value = "";
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
  documentElement: new El("html"),
};
global.window = { Telegram: { WebApp: {
  initData: "auth_date=1&user=%7B%22id%22%3A1%7D&hash=x",
  ready(){}, expand(){},
  BackButton: { show(){}, hide(){}, onClick(){} },
  HapticFeedback: { notificationOccurred(){} },
} } };

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
if (process.argv[3] === "mainbutton") { withFullTelegram(global.window.Telegram.WebApp); }
global.Telegram = global.window.Telegram;
const fetchCalls = [];
global.fetch = (url, opts) => {
  fetchCalls.push({
    url, method: (opts && opts.method) || "GET",
    body: opts && opts.body ? JSON.parse(opts.body) : null,
  });
  return Promise.resolve({
    ok: true, status: 200,
    json: () => {
      const path = url.split("?")[0];
      const method = (opts && opts.method) || "GET";
      return Promise.resolve(FIXTURES[method + " " + path] || FIXTURES[path] || {});
    },
  });
};
global.setInterval = () => 0;
global.setTimeout = (f) => { return 0; };
global.clearTimeout = () => {};
global.clearInterval = () => {};
global.Telegram = undefined;

// Which shape of scope this run drives. One harness, three worlds: the
// ordinary one, a fresh install with no roots at all, and a scope whose
// roots exist but whose daemon directory is not among them.
const SCENARIO = process.argv[3] || "full";
const SCENARIOS = {
  full:     { directories: ["/home/aly/proj", "/home/aly/daemon-dir"],
              default_directory: "/home/aly/daemon-dir" },
  empty:    { directories: [], default_directory: "" },
  noparent: { directories: ["/home/aly/proj"], default_directory: "" },
  mainbutton: { directories: ["/home/aly/proj"], default_directory: "/home/aly/proj" },
  chips:    { directories: ["/home/aly/proj", "/home/aly/daemon-dir", "/home/aly/other"],
              default_directory: "/home/aly/daemon-dir" },  // Schema from pytest (AIPAGER_TEST_SCHEMA): the real settings_schema().
  schema_plain: { directories: ["/home/aly/proj"], default_directory: "/home/aly/proj" },
  // The permission mode (2026-10-11): three options, Auto by default.
  modes: { directories: ["/home/aly/proj"], default_directory: "/home/aly/proj" },
  modes_member: { directories: ["/home/aly/proj"], default_directory: "/home/aly/proj",
                  can_use_full: false },
  modes_default_ask: { directories: ["/home/aly/proj"], default_directory: "/home/aly/proj",
                       default_mode: "ask" },
  // Create folder (2026-10-12): a text button, one request at a time.
  folder: { directories: ["/home/alice/proj"], default_directory: "/home/alice/proj" },
};
function fatal(msg) { console.error("FAIL: " + msg); process.exit(1); }

const SCHEMA = [
  { section: "length", field: "answer_length", title: "Answer length",
    options: [
      { value: "none", label: "Don't apply any rule", help: "" },
      { value: "short", label: "Short", help: "" },
      { value: "medium", label: "Medium", help: "" },
    ] },
];
const OPTION_SCHEMA = SCENARIO === "schema_plain"
  ? JSON.parse(fs.readFileSync(process.env.AIPAGER_TEST_SCHEMA, "utf8"))
  : SCHEMA;
const FIXTURES = {
  "/api/session-options": Object.assign({
    models: [{ label: "Opus", hint: "Most capable" }, { label: "Opus 5.5", hint: "" }],
    schema: OPTION_SCHEMA,
    scope_defaults: { answer_length: "none" },
    can_create: true,
    can_use_full: true,
    default_mode: "auto",
  }, SCENARIOS[SCENARIO] || fatal("unknown scenario: " + SCENARIO)),
  "/api/sessions": { label: "made", session_name: "claude-made__d1" },
  // GET /api/sessions (the grid behind the form): the recency the
  // "Recent" chips are ordered by. POST answers with the create body.
  "GET /api/sessions": { daemon: {}, totals: {}, can_act: true, sessions: [
    { label: "old", status: "idle", project: "daemon-dir", last_active_seconds_ago: 900 },
    { label: "new", status: "busy", project: "proj", last_active_seconds_ago: 5 },
  ] },
  "/api/directories": { path: "/home/aly/proj/sub", existed: false },
  "/api/sessions/made/preferences/answer_length": {
    values: { answer_length: { effective: "short", scope_default: "none",
                               override_value: "short", overridden: true } },
    changed: true,
  },
  "/api/sessions/made/preferences": {
    schema: SCHEMA,
    values: { answer_length: { effective: "short", scope_default: "none",
                               override_value: "short", overridden: true } },
    can_edit: true,
  },
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
};
if (SCENARIO === "schema_plain") {
  // Each group's first option is the chat default, so every header has a value.
  const d = {};
  OPTION_SCHEMA.forEach(g => { d[g.field] = g.options[0].value; });
  FIXTURES["/api/session-options"].scope_defaults = d;
}

// extract and run the page script
let script = page.match(/<script>([\s\S]*?)<\/script>/g)
  .map(s => s.replace(/<\/?script>/g, "")).join("\n");
// unwrap the IIFE so the internals are reachable, and export what we drive
// keep the IIFE (it contains top-level `return`s) but export its internals
script = script.replace(/\}\)\(\);\s*$/,
  "\n  global.__api = { openNewSession, renderNewForm, submitNewSession, openGroups,\n                   applyStatic };\n})();");
eval(script);

// ---- simulate: fill in the whole form and submit it ----

function fail(msg) { console.error("FAIL: " + msg); process.exit(1); }
function fireInput(el) { (el.listeners.input || []).forEach(f => f.call(el, {})); }
function fireKey(el, key) {
  (el.listeners.keydown || []).forEach(f => f.call(el, { key, preventDefault() {} }));
}

// Expand a collapsed option group and return its body (the list of rows,
// plus any reveal that was moved in beneath one of them).
function bodyOf(hostId) {
  const group = byId[hostId].children[0];
  if (!group) fail(hostId + " rendered no group");
  return group.children[1];
}
// Idempotent on purpose: the header TOGGLES, and picking an option
// re-renders with the group still open. A second unconditional click
// would close it and read as "did not expand".
function expand(hostId) {
  if (!bodyOf(hostId)) { byId[hostId].children[0].children[0].click(); }
  const body = bodyOf(hostId);
  if (!body) fail(hostId + " group did not expand");
  return body;
}
function head(hostId) { return byId[hostId].children[0].children[0]; }
function headValue(hostId) { return head(hostId).children[1].textContent; }
function rows(body) { return body.children.filter(c => c.className.includes("choice")); }
function labelOf(row) { return row.children[0].textContent; }
function isIn(node, host) {
  for (let p = node.parent; p; p = p.parent) { if (p === host) return true; }
  return false;
}

const api = global.__api;

// ---- scenario: the ordinary scope, driven end to end -----------------
function driveFull() {
  api.openNewSession();
  setTimeoutReal(() => {
  for (const id of ["new-model", "new-cwd", "new-mode", "new-prefs"]) {
    if (!byId[id].children.length) fail(id + " rendered no controls");
  }

  // Name it FIRST. Create is also disabled by an empty name, so checking
  // "a bad model disables Create" before this would pass for the wrong
  // reason — which is exactly how a guard gets shipped broken.
  byId["new-name"].value = "made";
  fireInput(byId["new-name"]);
  if (byId["new-create"].disabled)
    fail("Create was disabled on a named, otherwise-default form");

  // ==== model: an editable combobox =====================================
  const modelBody = expand("new-model");
  const modelRows = rows(modelBody);
  const other = modelRows[modelRows.length - 1];
  if (!other.className.includes("choice-new"))
    fail("the type-your-own row does not use the dashed new-thing style");
  other.click();

  // the reveal must sit INSIDE the group that revealed it
  if (!isIn(byId["new-model-reveal"], byId["new-model"]))
    fail("the model input was not moved into the group that revealed it");
  if (!byId["new-model-name"].focused)
    fail("revealing the model input did not focus it");

  // a disabled Create must say why, next to the field that caused it
  if (!byId["new-create"].disabled)
    fail("Create stayed enabled with the model name still empty");
  if (!/Type a model name/.test(byId["new-model-note"].textContent))
    fail("no reason given for the disabled Create: " +
         JSON.stringify(byId["new-model-note"].textContent));

  byId["new-model-name"].value = "opus; rm -rf /";
  fireInput(byId["new-model-name"]);
  if (!byId["new-create"].disabled)
    fail("Create stayed enabled with a malformed model name");
  if (!byId["new-model-note"].className.includes("is-error"))
    fail("a malformed model name showed no error");

  // typing must NOT rebuild the groups — capture identity, then type
  const rowBefore = modelRows[0];
  const dirGroupBefore = byId["new-cwd"].children[0];
  byId["new-model-name"].value = "claude-opus-5";
  fireInput(byId["new-model-name"]);
  if (!isIn(rowBefore, byId["new-model"]))
    fail("typing rebuilt the model group — the option rows were replaced");
  if (byId["new-cwd"].children[0] !== dirGroupBefore)
    fail("typing rebuilt the working-directory group");

  if (headValue("new-model") !== "claude-opus-5")
    fail("the collapsed Model header does not show the typed name: " +
         JSON.stringify(headValue("new-model")));
  if (byId["new-summary"].textContent.indexOf("claude-opus-5") === -1)
    fail("the typed model is not shown back in the summary: " +
         byId["new-summary"].textContent);

  // ==== working directory: picker with an inline new-folder row =========
  const dirBody = expand("new-cwd");
  const dirRows = rows(dirBody);
  if (dirRows.some(r => labelOf(r) === "Default"))
    fail("the daemon's directory is still listed twice (a 'Default' row " +
         "beside its real path)");
  const tagged = dirRows.filter(
    r => r.children.some(c => c.className === "tag"));
  if (tagged.length !== 1 || labelOf(tagged[0]) !== "daemon-dir")
    fail("the daemon's own directory is not the one tagged default");

  // the default directory must already be selected, so New folder has a parent
  if (headValue("new-cwd") !== "daemon-dir")
    fail("no directory was selected by default: " + headValue("new-cwd"));

  dirRows[0].click();                             // pick /home/aly/proj
  const dirBody2 = expand("new-cwd");             // (re-expand: it re-rendered)
  const dirRows2 = rows(dirBody2);
  const newFolder = dirRows2[dirRows2.length - 1];
  if (labelOf(newFolder) !== "New folder")
    fail("the last directory row is not New folder: " + labelOf(newFolder));
  if (!newFolder.className.includes("choice-new"))
    fail("New folder does not use the dashed new-thing style");
  newFolder.click();

  if (!isIn(byId["new-folder-reveal"], byId["new-cwd"]))
    fail("the folder input was not moved into the directory group");
  if (!byId["new-folder-name"].focused)
    fail("revealing the folder input did not focus it");
  // tapping an ACTION must not move the selection off the parent
  if (headValue("new-cwd") !== "proj")
    fail("opening New folder changed the selected directory to " +
         headValue("new-cwd"));
  if (byId["new-folder-prefix"].textContent !== "…/aly/proj/")
    fail("the parent is not shown as the field's prefix: " +
         JSON.stringify(byId["new-folder-prefix"].textContent));

  byId["new-folder-name"].value = "sub";
  fireInput(byId["new-folder-name"]);
  if (byId["new-folder-create"].disabled)
    fail("the create-folder button stayed disabled with a name typed");

  const beforeMkdir = fetchCalls.length;
  fireKey(byId["new-folder-name"], "Enter");      // commit with the keyboard

  setTimeoutReal(() => {
    const mk = fetchCalls.slice(beforeMkdir).find(
      f => f.method === "POST" && /\/api\/directories$/.test(f.url));
    if (!mk) fail("Enter in the folder field sent no POST /api/directories");
    if (!mk.body || mk.body.parent !== "/home/aly/proj" || mk.body.name !== "sub")
      fail("mkdir request carried the wrong parent/name: " + JSON.stringify(mk.body));
    if (headValue("new-cwd") !== "sub")
      fail("the new folder was not selected after being created: " +
           headValue("new-cwd"));
    if (byId["new-summary"].textContent.indexOf("/home/aly/proj/sub") === -1)
      fail("the new folder is not in the summary: " + byId["new-summary"].textContent);

    // The operator has to be TOLD the folder was made, not just shown a
    // changed dropdown. Assert the toast is actually visible and says so,
    // and that it is flagged as a success rather than a neutral note.
    const folderToast = byId["notice"];
    if (!folderToast.className.includes("is-visible"))
      fail("no toast after creating a folder");
    if (folderToast.textContent.indexOf("Folder created") === -1)
      fail("folder toast says: " + folderToast.textContent);
    if (!folderToast.className.includes("toast-ok"))
      fail("folder toast is not marked as a success: " + folderToast.className);

    // choose a reply-style override that differs from the scope default
    const prefsBody = expand("new-prefs");
    rows(prefsBody)[1].click();                   // "Short"

    if (byId["new-create"].disabled) fail("Create stayed disabled on a valid form");
    const before = fetchCalls.length;
    byId["new-create"].click();

    setTimeoutReal(() => {
      const sent = fetchCalls.slice(before);
      const post = sent.find(f => f.method === "POST" && /\/api\/sessions$/.test(f.url));
      if (!post) fail("Create sent no POST /api/sessions");
      if (post.body.model !== "claude-opus-5")
        fail("the typed model did not reach the create request: " +
             JSON.stringify(post.body));
      if (post.body.cwd !== "/home/aly/proj/sub")
        fail("the created folder did not reach the create request: " +
             JSON.stringify(post.body));
      const put = sent.find(f => f.method === "PUT" && /preferences\/answer_length$/.test(f.url));
      if (!put) fail("chosen reply-style setting was NOT applied after creation");

      // Creating a session navigates straight to its page, so without a
      // toast the only confirmation was a haptic tick — nothing at all on
      // a device with haptics off.
      const createToast = byId["notice"];
      if (!createToast.className.includes("is-visible"))
        fail("no toast after creating a session");
      if (createToast.textContent.indexOf("Session created") === -1)
        fail("create toast says: " + createToast.textContent);
      if (!createToast.className.includes("toast-ok"))
        fail("create toast is not marked as a success: " + createToast.className);
      console.log("ok: reveal -> Enter -> POST /api/directories -> POST /api/sessions -> PUT " + put.url);
      process.exit(0);
    }, 20);
  }, 20);
  }, 10);
}

// ---- scenario: a fresh install with no allowed roots at all ----------
//
// The picker must fall back to the lone "Default" option and post
// cwd: "" — exactly today's behaviour for a scope that has never run a
// session. New folder must NOT be offered: there is no parent for it.
function driveEmpty() {
  api.openNewSession();
  setTimeoutReal(() => {
    const dirRows = rows(expand("new-cwd"));
    if (dirRows.length !== 1 || labelOf(dirRows[0]) !== "Default")
      fail("a scope with no directories did not fall back to one Default row: " +
           JSON.stringify(dirRows.map(labelOf)));
    if (dirRows.some(r => r.className.includes("choice-new")))
      fail("New folder was offered with no directory to create it in");

    byId["new-name"].value = "made";
    fireInput(byId["new-name"]);
    if (byId["new-create"].disabled)
      fail("Create is disabled for a scope that has no directories yet");
    const before = fetchCalls.length;
    byId["new-create"].click();
    setTimeoutReal(() => {
      const post = fetchCalls.slice(before).find(
        f => f.method === "POST" && /\/api\/sessions$/.test(f.url));
      if (!post) fail("Create sent no POST /api/sessions");
      if (post.body.cwd !== "")
        fail("a scope with no directories posted cwd " + JSON.stringify(post.body.cwd));
      console.log("ok: no directories -> one Default row -> cwd \"\"");
      process.exit(0);
    }, 20);
  }, 10);
}

// ---- scenario: roots exist, but none of them is the daemon's ---------
//
// "Default" is a selection with no path behind it, so it cannot be a
// parent. Opening New folder on it must say so rather than present an
// empty field and a dead button.
function driveNoParent() {
  api.openNewSession();
  setTimeoutReal(() => {
    const dirRows = rows(expand("new-cwd"));
    if (labelOf(dirRows[0]) !== "Default")
      fail("with no default_directory the Default row should still be offered");
    if (headValue("new-cwd") !== "Default")
      fail("expected to start on Default, got " + headValue("new-cwd"));

    const newFolder = dirRows[dirRows.length - 1];
    if (labelOf(newFolder) !== "New folder")
      fail("New folder row missing: " + JSON.stringify(dirRows.map(labelOf)));
    newFolder.click();

    if (byId["new-folder-create"].disabled !== true)
      fail("the create-folder button is enabled with no parent selected");
    if (!/Pick a working directory/.test(byId["new-folder-note"].textContent))
      fail("no reason given for the dead create-folder button: " +
           JSON.stringify(byId["new-folder-note"].textContent));
    console.log("ok: no parent -> create-folder disabled WITH a stated reason");
    process.exit(0);
  }, 10);
}

// ---- scenario: the quick chips (roadmap 8.44) -------------------------
function chipsIn(id) {
  return byId[id].children.filter(c => c.className.split(" ")[0] === "chip");
}
function chipLabel(c) { return c.children[0].textContent; }
function driveChips() {
  api.openNewSession();
  setTimeoutReal(() => {
    const dirs = chipsIn("new-cwd-chips");
    if (byId["new-cwd-chips"].hidden) fail("no Recent chips");
    if (JSON.stringify(dirs.map(chipLabel)) !== JSON.stringify(["proj", "daemon-dir"]))
      fail("Recent chips should be the grid's projects, newest first: " +
           JSON.stringify(dirs.map(chipLabel)));
    if (!dirs[1].className.includes("is-active")) fail("the selected directory's chip is not active");
    dirs[0].click();
    if (headValue("new-cwd") !== "proj") fail("the chip did not select its directory: " + headValue("new-cwd"));
    const again = chipsIn("new-cwd-chips");
    if (!again[0].className.includes("is-active") || again[1].className.includes("is-active"))
      fail("chip state did not follow the selection");

    const models = chipsIn("new-model-chips");
    if (JSON.stringify(models.map(chipLabel)) !== JSON.stringify(["Opus"]))
      fail("Suggested chips should be the models with a hint: " + JSON.stringify(models.map(chipLabel)));
    models[0].click();
    if (headValue("new-model") !== "Opus") fail("the model chip did not pick Opus: " + headValue("new-model"));

    byId["new-name"].value = "made";
    fireInput(byId["new-name"]);
    const before = fetchCalls.length;
    byId["new-create"].click();
    setTimeoutReal(() => {
      const post = fetchCalls.slice(before).find(f => f.method === "POST" && /\/api\/sessions$/.test(f.url));
      if (!post) fail("no POST /api/sessions");
      if (post.body.cwd !== "/home/aly/proj" || post.body.model !== "Opus")
        fail("the chips did not reach the create request: " + JSON.stringify(post.body));
      console.log("ok: Recent chip -> proj, Suggested chip -> Opus, POST carries both");
      process.exit(0);
    }, 20);
  }, 10);
}

// ---- scenario: MainButton carries Start session (roadmap 8.44) ------
function driveMainButton() {
  api.openNewSession();
  setTimeoutReal(() => {
    const p = global.__mbParams;
    if (p.text !== "Start session" || !p.is_visible) fail("MainButton: " + JSON.stringify(p));
    if (p.is_active) fail("MainButton active with no name");
    if (global.__swOff !== 1) fail("swipes not disabled on the form: " + global.__swOff);
    byId["new-name"].value = "made";
    fireInput(byId["new-name"]);
    if (!global.__mbParams.is_active) fail("MainButton did not follow a valid form");
    const before = fetchCalls.length;
    global.__main();
    setTimeoutReal(() => {
      const posts = fetchCalls.slice(before).filter(f => f.method === "POST" && /\/api\/sessions$/.test(f.url));
      if (posts.length !== 1) fail("MainButton sent " + posts.length + " creates");
      if (global.__swOn !== 1) fail("swipes not re-enabled after leaving the form");
      if (global.__mbParams.is_visible) fail("MainButton stayed up after leaving the form");
      console.log("ok: MainButton Start session -> POST /api/sessions, swipes restored");
      process.exit(0);
    }, 30);
  }, 10);
}

// ---- scenario: the form's reply-style groups show schema text plain ---
function driveSchemaPlain() {
  const schema = OPTION_SCHEMA;
  api.openNewSession();
  setTimeoutReal(() => {
    const host = byId["new-prefs"];
    if (host.children.length !== schema.length)
      fail("new-prefs rendered " + host.children.length + " groups");
    for (let i = 0; i < schema.length; i++) {
      const grp = byId["new-prefs"].children[i];
      if (!grp.children[1]) grp.children[0].click();
    }
    if (byId["new-prefs"].children.some(g => !g.children[1])) fail("a group stayed shut");
    const text = byId["new-prefs"].textContent;
    if (text.indexOf("\u2014") !== -1) fail("the form shows an em dash: " + JSON.stringify(text));
    byId["new-prefs"].children.forEach((g, i) => {
      const title = g.children[0].children[0].textContent;
      if (!/^[A-Za-z0-9]/.test(title)) fail("title keeps its emoji: " + JSON.stringify(title));
      const lead = schema[i].options[0].label.split(/ - | \u2014 /)[0];
      const shown = g.children[0].children[1].textContent;
      if (shown !== lead) fail("header shows " + JSON.stringify(shown) + ", want " +
                               JSON.stringify(lead));
    });
    console.log("ok: form schema text -> no em dash, bare titles, header shows the lead");
    process.exit(0);
  }, 10);
}

// ---- scenario: the permission mode: three options, the default first --
function driveModes() {
  const opts = FIXTURES["/api/session-options"];
  api.openNewSession();
  setTimeoutReal(() => {
    const body = expand("new-mode");
    const labels = rows(body).map(labelOf);
    if (JSON.stringify(labels) !== JSON.stringify(["Ask", "Auto", "Full access"]))
      fail("mode options: " + JSON.stringify(labels));
    const want = { auto: "Auto", ask: "Ask", full: "Full access" }[opts.default_mode];
    if (headValue("new-mode") !== want)
      fail("the form should start on " + want + ", shows " + headValue("new-mode"));
    const note = byId["new-mode-note"];
    if (note.hidden !== opts.can_use_full)
      fail("the admin note should show exactly when Full access is refused");
    rows(expand("new-mode"))[2].click();              // Full access
    const picked = opts.can_use_full ? "Full access" : want;
    if (headValue("new-mode") !== picked)
      fail("picking Full access: " + headValue("new-mode") + ", want " + picked);
    byId["new-name"].value = "made";
    fireInput(byId["new-name"]);
    const summary = byId["new-summary"].textContent;
    if (summary.indexOf(picked === "Full access" ? "Full access mode" : want + " mode") === -1)
      fail("summary does not name the mode: " + JSON.stringify(summary));
    const before = fetchCalls.length;
    byId["new-create"].click();
    setTimeoutReal(() => {
      const post = fetchCalls.slice(before).find(
        f => f.method === "POST" && /\/api\/sessions$/.test(f.url));
      if (!post) fail("Create sent no POST /api/sessions");
      const sent = { "Ask": "ask", "Auto": "auto", "Full access": "full" }[picked];
      if (post.body.mode !== sent || "skip_perms" in post.body)
        fail("create body mode: " + JSON.stringify(post.body));
      console.log("ok: modes " + SCENARIO + " -> " + sent);
      process.exit(0);
    }, 30);
  }, 10);
}

// ---- scenario: Create folder is a text button, one request at a time --
//
// Every POST /api/directories waits until the scenario answers it, so the
// busy state is visible; a second tap or Enter while it waits sends
// nothing; each kind of answer gives the button back with the typed name
// kept and a reason; a name the server would refuse never leaves the page.
// AIPAGER_TEST_FOLDER_NAMES: [[name, valid, reason], ...] from the server's
// own validator (tests/test_miniapp_create_folder.py); reason is the
// server's English for a too-long or badly formed name, else null.
function driveFolder() {
  // applyStatic() walks every [data-i18n] node: give the shim the marked
  // nodes that carry an id, with their keys.
  for (const m of page.matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g)) {
    const k = /\sdata-i18n="([^"]+)"/.exec(m[0]);
    if (k) { byId[m[1]].attrs["data-i18n"] = k[1]; }
  }
  global.document.querySelectorAll = () =>
    Object.values(byId).filter(e => e.attrs["data-i18n"]);
  const OPTIONS = FIXTURES["/api/session-options"];
  const pending = [];
  const baseFetch = global.fetch;
  global.fetch = (url, opts) => {
    if (/\/api\/directories$/.test(url.split("?")[0]) && opts && opts.method === "POST") {
      fetchCalls.push({ url, method: "POST", body: JSON.parse(opts.body) });
      return new Promise((resolve, reject) => pending.push({ resolve, reject }));
    }
    return baseFetch(url, opts);
  };
  function answer(status, body, notJson) {
    const p = pending.shift();
    if (!p) fail("no POST /api/directories is waiting for an answer");
    p.resolve({ ok: status === 200, status,
                json: () => notJson ? Promise.reject(new SyntaxError("not JSON"))
                                    : Promise.resolve(body) });
  }
  function posts() {
    return fetchCalls.filter(f => f.method === "POST" && /\/api\/directories$/.test(f.url)).length;
  }
  const btn = byId["new-folder-create"];
  const name = byId["new-folder-name"];
  const note = byId["new-folder-note"];
  function idleWithName(when) {
    if (btn.disabled) fail(when + ": the button stayed disabled");
    if (name.readOnly) fail(when + ": the name field stayed read only");
    if (btn.textContent !== "Create folder")
      fail(when + ": the button reads " + JSON.stringify(btn.textContent));
    if (name.value !== "my-project") fail(when + ": the typed name was lost");
    if (!note.className.includes("is-error")) fail(when + ": the note is not an error");
  }

  api.openNewSession();
  setTimeoutReal(() => {
    const dirRows = rows(expand("new-cwd"));
    dirRows[dirRows.length - 1].click();                       // New folder
    if (btn.textContent !== "Create folder")
      fail("the create button reads " + JSON.stringify(btn.textContent));
    if (!btn.disabled) fail("Create folder is enabled with no name typed");

    // A caller who may not create folders: the button is disabled and
    // says why, and Enter sends nothing either (the same gate).
    OPTIONS.can_create = false;
    name.value = "my-project";
    fireInput(name);
    if (!btn.disabled) fail("Create folder is enabled for a caller who may not create");
    const FORBIDDEN = "You're not allowed to create folders here.";
    if (note.textContent !== FORBIDDEN)
      fail("no reason for the forbidden button: " + JSON.stringify(note.textContent));
    fireKey(name, "Enter");
    btn.click();
    if (posts() !== 0) fail("Enter sent a folder the caller may not create");
    OPTIONS.can_create = true;

    const table = JSON.parse(process.env.AIPAGER_TEST_FOLDER_NAMES || "[]");
    for (const [value, valid, reason] of table) {
      name.value = value;
      fireInput(name);
      if (btn.disabled === valid)
        fail("name " + JSON.stringify(value) + ": button disabled=" + btn.disabled +
             ", but the server says valid=" + valid);
      if (!valid && value.trim() && !note.className.includes("is-error"))
        fail("no reason shown for the refused name " + JSON.stringify(value));
      if (reason && note.textContent !== reason)
        fail("name " + JSON.stringify(value) + " reads " + JSON.stringify(note.textContent) +
             ", the server says " + JSON.stringify(reason));
      if (!valid) {
        const before = posts();
        fireKey(name, "Enter");
        btn.click();
        if (posts() !== before) fail("a refused name was sent: " + JSON.stringify(value));
      }
    }

    name.value = "my-project";
    fireInput(name);
    if (btn.disabled) fail("a valid name left the button disabled");
    if (note.className.includes("is-error")) fail("a valid name shows an error");
    btn.click();
    if (posts() !== 1) fail("one tap sent " + posts() + " requests");
    if (!btn.disabled) fail("the button stayed enabled while the folder is created");
    if (btn.textContent !== "Creating…")
      fail("busy label: " + JSON.stringify(btn.textContent));
    if (!name.readOnly || name.disabled)
      fail("the name field is not read only (and enabled) while the folder is created");
    // A language switch redraws every marked node: the label stays busy.
    const other = Object.values(byId).find(e => e !== btn && e.attrs["data-i18n"]);
    other.textContent = "stale";
    api.applyStatic();
    if (other.textContent === "stale") fail("applyStatic rewrote nothing (harness)");
    if (btn.textContent !== "Creating…")
      fail("a language switch reset the busy label: " + JSON.stringify(btn.textContent));
    if (btn.attrs["aria-busy"] !== "true") fail("a language switch reset aria-busy");
    btn.click();
    fireKey(name, "Enter");
    if (posts() !== 1) fail("a second tap or Enter while busy sent another request");

    // Each answer that is not a created folder: the button comes back,
    // the typed name stays, and the note says why. The first request is
    // the one already waiting; each later step taps again.
    const FAILED = "Couldn't create that folder.";
    const steps = [
      ["a refusal with a reason", () => answer(400, { error: "bad_request",
        detail: "A folder with that name already exists there. Pick a new name." }),
       "A folder with that name already exists there. Pick a new name."],
      ["a body that is not JSON", () => answer(502, null, true), FAILED],
      ["a rate limit", () => answer(429, { error: "too_many_requests" }),
       "Too many tries. Try again in a minute."],
      ["a refusal of the caller", () => answer(403, { error: "forbidden" }),
       "You're not allowed to create folders here."],
      ["an OK with no path", () => answer(200, { existed: false }), FAILED],
      ["a refusal with no reason", () => answer(400, { error: "bad_request" }), FAILED],
      ["a server error", () => answer(500, { error: "internal" }), FAILED],
      ["no answer at all", () => pending.shift().reject(new TypeError("Failed to fetch")),
       "Couldn't reach the server. No folder was created."],
    ];
    function step(i) {
      if (i === steps.length) { return created(); }
      const [what, respond, says] = steps[i];
      if (i > 0) { btn.click(); }
      if (posts() !== i + 1) fail("before " + what + ": " + posts() + " requests");
      if (btn.attrs["aria-busy"] !== "true") fail("not marked busy before " + what);
      respond();
      setTimeoutReal(() => {
        idleWithName("after " + what);
        if (btn.attrs["aria-busy"] !== "false") fail("still marked busy after " + what);
        if (note.textContent !== says)
          fail(what + " reads " + JSON.stringify(note.textContent));
        step(i + 1);
      }, 10);
    }
    function created() {
      btn.click();
      answer(200, { path: "/home/alice/proj/my-project", existed: false });
      setTimeoutReal(() => {
        if (name.value !== "") fail("success kept the sent name: " + JSON.stringify(name.value));
        if (headValue("new-cwd") !== "my-project")
          fail("the new folder was not selected: " + headValue("new-cwd"));
        if (byId["notice"].textContent.indexOf("Folder created") === -1)
          fail("no Folder created notice: " + byId["notice"].textContent);
        createdAgain();
      }, 10);
    }
    // Once more, inside the new folder: whatever reaches the field while
    // the name is out stays there.
    function createdAgain() {
      const dirRows = rows(expand("new-cwd"));
      dirRows[dirRows.length - 1].click();                     // New folder
      name.value = "sub";
      fireInput(name);
      btn.click();
      if (posts() !== steps.length + 2) fail("the second create sent no request");
      const second = fetchCalls.filter(f => f.method === "POST" &&
                                         /\/api\/directories$/.test(f.url)).pop();
      if (second.body.parent !== "/home/alice/proj/my-project" || second.body.name !== "sub")
        fail("the second create was not inside the new folder: " + JSON.stringify(second.body));
      name.value = "sub-2";
      answer(200, { path: "/home/alice/proj/my-project/sub", existed: false });
      setTimeoutReal(() => {
        if (name.value !== "sub-2")
          fail("success wiped what was typed meanwhile: " + JSON.stringify(name.value));
        if (headValue("new-cwd") !== "sub")
          fail("the second folder was not selected: " + headValue("new-cwd"));
        if (posts() !== steps.length + 2)
          fail("expected " + (steps.length + 2) + " requests in all, sent " + posts());
        console.log("ok: create folder is a text button, one request at a time, " +
                    "every answer handled");
        process.exit(0);
      }, 10);
    }
    step(0);
  }, 10);
}

const DRIVERS = { full: driveFull, empty: driveEmpty, noparent: driveNoParent, chips: driveChips,
                  folder: driveFolder,
                  mainbutton: driveMainButton, schema_plain: driveSchemaPlain,
                  modes: driveModes, modes_member: driveModes, modes_default_ask: driveModes };
(DRIVERS[SCENARIO] || (() => fail("unknown scenario: " + SCENARIO)))();
