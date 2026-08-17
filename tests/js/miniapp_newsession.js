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
  fetchCalls.push({
    url, method: (opts && opts.method) || "GET",
    body: opts && opts.body ? JSON.parse(opts.body) : null,
  });
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

// Which shape of scope this run drives. One harness, three worlds: the
// ordinary one, a fresh install with no roots at all, and a scope whose
// roots exist but whose daemon directory is not among them.
const SCENARIO = process.argv[3] || "full";
const SCENARIOS = {
  full:     { directories: ["/home/aly/proj", "/home/aly/daemon-dir"],
              default_directory: "/home/aly/daemon-dir" },
  empty:    { directories: [], default_directory: "" },
  noparent: { directories: ["/home/aly/proj"], default_directory: "" },
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
const FIXTURES = {
  "/api/session-options": Object.assign({
    models: [{ label: "Opus", hint: "Most capable" }],
    schema: SCHEMA,
    scope_defaults: { answer_length: "none" },
    can_create: true,
    can_use_auto: true,
  }, SCENARIOS[SCENARIO] || fatal("unknown scenario: " + SCENARIO)),
  "/api/sessions": { label: "made", session_name: "claude-made__d1" },
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

// extract and run the page script
let script = page.match(/<script>([\s\S]*?)<\/script>/g)
  .map(s => s.replace(/<\/?script>/g, "")).join("\n");
// unwrap the IIFE so the internals are reachable, and export what we drive
// keep the IIFE (it contains top-level `return`s) but export its internals
script = script.replace(/\}\)\(\);\s*$/,
  "\n  global.__api = { openNewSession, renderNewForm, submitNewSession, openGroups };\n})();");
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

const DRIVERS = { full: driveFull, empty: driveEmpty, noparent: driveNoParent };
(DRIVERS[SCENARIO] || (() => fail("unknown scenario: " + SCENARIO)))();
