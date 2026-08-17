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
// ids the page references
for (const m of page.matchAll(/id="([^"]+)"/g)) byId[m[1]] = new El("div");
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

// ---- simulate: open a session, expand the group, tap an option ----

// drive loadSessionSettings by clicking a session card is complex; instead
// invoke the same path the page does when the detail view opens.
// We emulate by calling the fetch-backed loader through a card click.
function fail(msg) { console.error("FAIL: " + msg); process.exit(1); }

const api = global.__api;
api.openNewSession();

setTimeoutReal(() => {
  // model + directory + mode + the four reply-style groups must all render
  const groups = ["new-model", "new-cwd", "new-mode", "new-prefs"];
  for (const id of groups) {
    if (!byId[id].children.length) fail(id + " rendered no controls");
  }

  // choose a reply-style override that differs from the scope default
  const prefs = byId["new-prefs"];
  const head = prefs.children[0].children[0];
  head.click();                                  // expand the group
  const list = byId["new-prefs"].children[0].children[1];
  if (!list) fail("reply-style group did not expand");
  const shortChoice = list.children[1];          // "Short"
  shortChoice.click();

  // name it and submit
  byId["new-name"].value = "made";
  const before = fetchCalls.length;
  byId["new-create"].click();

  setTimeoutReal(() => {
    const sent = fetchCalls.slice(before);
    const post = sent.find(f => f.method === "POST" && /\/api\/sessions$/.test(f.url));
    if (!post) fail("Create sent no POST /api/sessions");
    const put = sent.find(f => f.method === "PUT" && /preferences\/answer_length$/.test(f.url));
    if (!put) fail("chosen reply-style setting was NOT applied after creation");
    if (!/\/api\/sessions\/made\/preferences\/answer_length$/.test(put.url))
      fail("preference PUT went to the wrong url: " + put.url);
    console.log("ok: form -> POST /api/sessions -> PUT " + put.url);
    process.exit(0);
  }, 20);
}, 10);
