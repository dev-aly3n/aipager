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
  appendChild(c) { this.children.push(c); c.parent = this; return c; }
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

const SCHEMA = [
  { section: "length", field: "answer_length", title: "Answer length",
    options: [
      { value: "none", label: "Don't apply any rule", help: "" },
      { value: "short", label: "Short", help: "" },
      { value: "medium", label: "Medium", help: "" },
    ] },
];
const FIXTURES = {
  "/api/session-options": {
    directories: ["/home/aly/proj"],
    models: [{ label: "Opus", hint: "Most capable" }],
    schema: SCHEMA,
    scope_defaults: { answer_length: "none" },
    can_create: true,
    can_use_auto: true,
  },
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

// Expand a collapsed option group and return its list of choices.
function expand(hostId, index) {
  const group = byId[hostId].children[index || 0];
  if (!group) fail(hostId + " rendered no group to expand");
  group.children[0].click();                      // the header toggles it
  const list = byId[hostId].children[index || 0].children[1];
  if (!list) fail(hostId + " group did not expand");
  return list;
}

const api = global.__api;
api.openNewSession();

setTimeoutReal(() => {
  // model + directory + mode + the four reply-style groups must all render
  const groups = ["new-model", "new-cwd", "new-mode", "new-prefs"];
  for (const id of groups) {
    if (!byId[id].children.length) fail(id + " rendered no controls");
  }

  // Name it FIRST. Create is also disabled by an empty name, so checking
  // "a bad model disables Create" before this would pass for the wrong
  // reason — which is exactly how a guard gets shipped broken.
  byId["new-name"].value = "made";
  fireInput(byId["new-name"]);
  if (byId["new-create"].disabled)
    fail("Create was disabled on a named, otherwise-default form");

  // ---- a full model name, typed ----------------------------------------
  const modelChoices = expand("new-model");
  const custom = modelChoices.children[modelChoices.children.length - 1];
  custom.click();
  if (byId["new-model-custom"].hidden)
    fail("picking the type-a-name option did not reveal the model input");
  if (!byId["new-create"].disabled)
    fail("Create stayed enabled with the model name still empty");

  // a malformed name must block Create rather than reach the server
  byId["new-model-name"].value = "opus; rm -rf /";
  fireInput(byId["new-model-name"]);
  if (!byId["new-create"].disabled)
    fail("Create stayed enabled with a malformed model name");
  if (byId["new-model-error"].hidden)
    fail("a malformed model name showed no error");

  byId["new-model-name"].value = "claude-opus-5";
  fireInput(byId["new-model-name"]);
  if (byId["new-summary"].textContent.indexOf("claude-opus-5") === -1)
    fail("the typed model is not shown back in the summary: " +
         byId["new-summary"].textContent);

  // ---- create a working directory --------------------------------------
  const dirChoices = expand("new-cwd");
  dirChoices.children[1].click();                 // the one served directory
  byId["new-folder-toggle"].click();
  if (byId["new-folder"].hidden) fail("the New folder panel did not open");
  byId["new-folder-name"].value = "sub";
  const beforeMkdir = fetchCalls.length;
  byId["new-folder-create"].click();

  setTimeoutReal(() => {
    const mk = fetchCalls.slice(beforeMkdir).find(
      f => f.method === "POST" && /\/api\/directories$/.test(f.url));
    if (!mk) fail("Create folder sent no POST /api/directories");
    if (!mk.body || mk.body.parent !== "/home/aly/proj" || mk.body.name !== "sub")
      fail("mkdir request carried the wrong parent/name: " + JSON.stringify(mk.body));
    if (byId["new-summary"].textContent.indexOf("/home/aly/proj/sub") === -1)
      fail("the new folder was not selected after being created: " +
           byId["new-summary"].textContent);

    // choose a reply-style override that differs from the scope default
    const list = expand("new-prefs");
    list.children[1].click();                     // "Short"

    // submit (the name was set at the top)
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
      if (!/\/api\/sessions\/made\/preferences\/answer_length$/.test(put.url))
        fail("preference PUT went to the wrong url: " + put.url);
      console.log("ok: form -> POST /api/directories -> POST /api/sessions -> PUT " + put.url);
      process.exit(0);
    }, 20);
  }, 20);
}, 10);
