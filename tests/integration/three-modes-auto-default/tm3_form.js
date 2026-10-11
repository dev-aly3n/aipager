// Black-box driver for the Mini App's New session form (three-modes suite).
// Loads the real page, answers GET /api/session-options with the JSON given
// in argv[3] merged over a minimal fixture, opens the form, names the
// session, taps Create without touching the mode, and prints the POST body
// as one line: "POST <json>".
const fs = require("fs");
const page = fs.readFileSync(process.argv[2], "utf8");
const overrides = JSON.parse(process.argv[3] || "{}");
const realSetTimeout = setTimeout;

class El {
  constructor(tag) {
    this.tagName = (tag || "div").toUpperCase();
    this.children = []; this.listeners = {}; this.attrs = {};
    this._class = ""; this._text = ""; this._html = "";
    this.hidden = false; this.disabled = false; this.style = {}; this.value = "";
    this.dataset = {};
    this.classList = {
      add: (c) => { if (!this._class.split(" ").includes(c)) this._class += " " + c; },
      remove: (c) => { this._class = this._class.split(" ").filter(x => x !== c).join(" "); },
      toggle: (c, on) => { on ? this.classList.add(c) : this.classList.remove(c); },
      contains: (c) => this._class.split(" ").includes(c),
    };
  }
  get className() { return this._class; }
  set className(v) { this._class = v; }
  get textContent() {
    if (this.children.length) return this.children.map((c) => c.textContent).join("");
    return this._text;
  }
  set textContent(v) { this._text = String(v); this.children = []; }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = v; this.children = []; }
  appendChild(c) {
    if (c.parent) { const i = c.parent.children.indexOf(c); if (i >= 0) c.parent.children.splice(i, 1); }
    this.children.push(c); c.parent = this; return c;
  }
  insertBefore(c, ref) { this.appendChild(c); return c; }
  removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; }
  remove() { if (this.parent) this.parent.removeChild(this); }
  replaceChildren(...cs) { this.children = []; cs.forEach(c => this.appendChild(c)); }
  append(...cs) { cs.forEach(c => this.appendChild(typeof c === "string" ? Object.assign(new El("span"), {_text: c}) : c)); }
  focus() { this.focused = true; }
  blur() {}
  scrollIntoView() {}
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  removeEventListener() {}
  click() { (this.listeners.click || []).forEach(f => f.call(this, { preventDefault() {}, stopPropagation() {} })); }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  closest() { return null; }
  getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0 }; }
}

const byId = {};
for (const m of page.matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g)) {
  const el = new El("div");
  el.hidden = /\shidden(\s|>|=)/.test(m[0]);
  byId[m[1]] = el;
}
global.document = {
  getElementById: (id) => byId[id] || (byId[id] = new El("div")),
  createElement: (t) => new El(t),
  createTextNode: (t) => Object.assign(new El("span"), { _text: String(t) }),
  addEventListener: () => {},
  querySelector: () => null,
  querySelectorAll: () => [],
  visibilityState: "visible",
  documentElement: new El("html"),
  body: new El("body"),
};
global.window = { Telegram: { WebApp: {
  initData: "auth_date=1&user=%7B%22id%22%3A1%7D&hash=x",
  ready() {}, expand() {},
  BackButton: { show() {}, hide() {}, onClick() {}, offClick() {} },
  HapticFeedback: { notificationOccurred() {}, impactOccurred() {}, selectionChanged() {} },
} }, location: { search: "", hash: "", href: "http://x/" }, addEventListener() {} };
global.location = global.window.location;
global.Telegram = undefined;

const OPTIONS = Object.assign({
  models: [{ label: "Opus", hint: "" }],
  schema: [],
  scope_defaults: {},
  can_create: true,
  directories: ["/home/aly/proj"],
  default_directory: "/home/aly/proj",
}, overrides);
const calls = [];
global.fetch = (url, opts) => {
  const method = (opts && opts.method) || "GET";
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  calls.push({ url, method, body });
  const path = url.split("?")[0];
  let payload = {};
  if (path.endsWith("/api/session-options")) payload = OPTIONS;
  else if (method === "POST" && /\/api\/sessions$/.test(path)) payload = { label: "made", session_name: "claude-made" };
  else if (path.endsWith("/api/sessions")) payload = { daemon: {}, totals: {}, can_act: true, sessions: [] };
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload),
                           text: () => Promise.resolve(JSON.stringify(payload)) });
};
global.setInterval = () => 0;
global.setTimeout = () => 0;
global.clearTimeout = () => {};
global.clearInterval = () => {};

let script = page.match(/<script>([\s\S]*?)<\/script>/g)
  .map(s => s.replace(/<\/?script>/g, "")).join("\n");
script = script.replace(/\}\)\(\);\s*$/,
  "\n  global.__tm3 = { openNewSession };\n})();");
eval(script);

function fireInput(el) { (el.listeners.input || []).forEach(f => f.call(el, {})); }

global.__tm3.openNewSession();
realSetTimeout(() => {
  byId["new-name"].value = "made";
  fireInput(byId["new-name"]);
  byId["new-create"].click();
  realSetTimeout(() => {
    const post = calls.find(c => c.method === "POST" && /\/api\/sessions$/.test(c.url));
    if (!post) { console.error("FAIL: no POST /api/sessions; calls=" + JSON.stringify(calls)); process.exit(1); }
    console.log("POST " + JSON.stringify(post.body));
  }, 30);
}, 30);
