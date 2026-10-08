// Black-box driver for the Mini App's report page (8.112 follow-up).
//   node report_page.js <page.html> <config.json>
// The page's own script runs in a small DOM shim. It is driven only from
// the outside: element ids from entrypoints.md, input events on the note,
// clicks, and the Telegram WebApp's MainButton and BackButton callbacks.
// No page function is exported or called. fetch is stubbed: the draft and
// send answers come from the config, every request is recorded. The run
// prints one JSON line: {snaps, posts, fetched, createdTags, html, errors}.
const setTimeoutReal = setTimeout;
const fs = require("fs");
const page = fs.readFileSync(process.argv[2], "utf8");
const CFG = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

const created = [];
const errors = [];
class El {
  constructor(tag) {
    this.tagName = (tag || "div").toUpperCase();
    this.children = []; this.listeners = {}; this.attrs = {};
    this._class = ""; this._text = ""; this._html = "";
    this.hidden = false; this.disabled = false; this.readOnly = false;
    this.style = {}; this.value = ""; this.scrollHeight = 0;
    this.classList = {
      add: (c) => { if (!this._class.split(" ").includes(c)) this._class += " " + c; },
      remove: (c) => { this._class = this._class.split(" ").filter(x => x !== c).join(" "); },
      toggle: (c, on) => {
        if (on === undefined) on = !this.classList.contains(c);
        on ? this.classList.add(c) : this.classList.remove(c);
      },
      contains: (c) => this._class.split(" ").includes(c),
    };
  }
  get className() { return this._class; }
  set className(v) { this._class = String(v); }
  get textContent() {
    if (this.children.length) return this.children.map((c) => c.textContent).join("");
    return this._text;
  }
  set textContent(v) { this._text = String(v); this.children = []; this._html = ""; }
  get innerText() { return this.textContent; }
  set innerText(v) { this.textContent = v; }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); this.children = []; this._text = ""; }
  get firstChild() { return this.children[0] || null; }
  appendChild(c) {
    if (c.parent) {
      const i = c.parent.children.indexOf(c);
      if (i >= 0) c.parent.children.splice(i, 1);
    }
    this.children.push(c); c.parent = this; return c;
  }
  append(...cs) { cs.forEach(c => this.appendChild(typeof c === "string" ? textNode(c) : c)); }
  insertBefore(c, ref) {
    this.appendChild(c);
    if (ref) {
      this.children.pop();
      const i = this.children.indexOf(ref);
      this.children.splice(i < 0 ? this.children.length : i, 0, c);
    }
    return c;
  }
  removeChild(c) {
    const i = this.children.indexOf(c);
    if (i >= 0) this.children.splice(i, 1);
    c.parent = null; return c;
  }
  remove() { if (this.parent) this.parent.removeChild(this); }
  replaceChildren(...cs) { this.children = []; this._text = ""; this._html = ""; this.append(...cs); }
  focus() { this.focused = true; }
  blur() { this.focused = false; }
  select() {}
  scrollIntoView() {}
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  removeAttribute(k) { delete this.attrs[k]; }
  hasAttribute(k) { return k in this.attrs; }
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  removeEventListener(ev, fn) {
    this.listeners[ev] = (this.listeners[ev] || []).filter(f => f !== fn);
  }
  fire(ev) {
    const e = { type: ev, target: this, currentTarget: this, preventDefault() {},
                stopPropagation() {} };
    (this.listeners[ev] || []).slice().forEach(f => f.call(this, e));
  }
  click() { if (!this.disabled) this.fire("click"); }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  closest() { return null; }
  contains(o) { for (let p = o; p; p = p.parent) if (p === this) return true; return false; }
  getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0, bottom: 0, right: 0 }; }
  *walk() { yield this; for (const c of this.children) yield* c.walk(); }
}
function textNode(s) { const t = new El("#text"); t._text = String(s); return t; }

const byId = {};
for (const m of page.matchAll(/<([a-zA-Z0-9]+)\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
  const el = new El(m[1]);
  el.hidden = /\shidden(\s|>|=)/.test(m[0]);
  byId[m[2]] = el;
}

global.document = {
  getElementById: (id) => byId[id] || (byId[id] = new El("div")),
  createElement: (t) => { const el = new El(t); created.push(el); return el; },
  createTextNode: (s) => textNode(s),
  createDocumentFragment: () => new El("#fragment"),
  addEventListener: () => {},
  removeEventListener: () => {},
  querySelector: () => null,
  querySelectorAll: () => [],
  visibilityState: "visible",
  documentElement: new El("html"),
  body: new El("body"),
  execCommand: () => false,
};
global.getSelection = () => ({ selectAllChildren() {}, removeAllRanges() {}, addRange() {} });
// node 22 has a read-only global navigator: define it instead of assigning.
const NAV = { userAgent: "node", clipboard: CFG.clipboard ? {
  writeText(t) { global.__copied = t; return Promise.resolve(); } } : undefined };
Object.defineProperty(globalThis, "navigator", { value: NAV, configurable: true, writable: true });

global.__mbParams = {};
const W = {
  initData: "auth_date=1&user=%7B%22id%22%3A1%7D&hash=x",
  ready() {}, expand() {}, close() {},
  colorScheme: "light", themeParams: {},
  isVersionAtLeast: () => true,
  onEvent() {}, offEvent() {}, setHeaderColor() {}, setBackgroundColor() {},
  disableVerticalSwipes() {}, enableVerticalSwipes() {},
  BackButton: { show() { global.__backShown = true; }, hide() { global.__backShown = false; },
                onClick(fn) { global.__back = fn; }, offClick() {} },
  HapticFeedback: { notificationOccurred() {}, impactOccurred() {}, selectionChanged() {} },
  MainButton: {
    setParams(p) { global.__mbParams = Object.assign({}, global.__mbParams, p); },
    setText(t) { global.__mbParams = Object.assign({}, global.__mbParams, { text: t }); },
    show() { global.__mbParams = Object.assign({}, global.__mbParams, { is_visible: true }); },
    hide() { global.__mbParams = Object.assign({}, global.__mbParams, { is_visible: false }); },
    enable() { global.__mbParams = Object.assign({}, global.__mbParams, { is_active: true }); },
    disable() { global.__mbParams = Object.assign({}, global.__mbParams, { is_active: false }); },
    onClick(fn) { global.__main = fn; }, offClick() {},
    showProgress() { global.__mbProgress = true; },
    hideProgress() { global.__mbProgress = false; },
  },
};
if (!CFG.mainbutton) delete W.MainButton;
global.window = { Telegram: { WebApp: W }, addEventListener() {}, location: { hash: "" },
                  navigator: NAV };
global.Telegram = global.window.Telegram;

// ---- the fake server ----------------------------------------------------
const posts = [];
const fetched = [];
const queues = { "/api/report/draft": (CFG.draftAnswers || []).slice(),
                 "/api/report/send": (CFG.sendAnswers || []).slice() };
global.fetch = (url, opts) => {
  const method = (opts && opts.method) || "GET";
  fetched.push(method + " " + url);
  const path = String(url).split("?")[0];
  if (method === "POST") {
    let body = null;
    try { body = JSON.parse(opts.body); } catch (e) { body = opts && opts.body; }
    posts.push({ url: path, body });
  }
  let next = queues[path] && queues[path].shift();
  if (!next) {
    if (path === "/api/report/draft") next = { status: 200, body: CFG.draft };
    else if (path === "/api/report/send") next = { status: 200, body: CFG.sendDefault || {} };
    else if (path === "/api/preferences") next = { status: 200, body: { can_report: true } };
    else next = { status: 200, body: {} };
  }
  if (next === "pending") return new Promise(() => {});
  return Promise.resolve({ ok: next.status < 400, status: next.status,
                           json: () => Promise.resolve(next.body),
                           text: () => Promise.resolve(JSON.stringify(next.body)) });
};
global.setInterval = () => 0;
global.setTimeout = () => 0;
global.clearTimeout = () => {};
global.clearInterval = () => {};
global.requestAnimationFrame = () => 0;
process.on("unhandledRejection", (e) => { errors.push("unhandled: " + (e && e.stack || e)); });

const script = page.match(/<script>([\s\S]*?)<\/script>/g)
  .map(s => s.replace(/<\/?script>/g, "")).join("\n");
try { eval(script); } catch (e) { errors.push("boot: " + (e && e.stack || e)); }

// ---- observation ----------------------------------------------------------
function T(id) { return byId[id] ? byId[id].textContent : null; }
function snap() {
  const st = byId["rp-status"];
  return {
    view: !byId["view-report"].hidden,
    back: !!global.__backShown,
    mb: Object.assign({}, global.__mbParams),
    mbProgress: !!global.__mbProgress,
    exact: T("rp-exact"),
    count: T("rp-count"),
    hint: T("rp-hint"),
    sendDisabled: byId["rp-send"].disabled,
    sendText: T("rp-send"),
    note: byId["rp-note"].value,
    noteReadOnly: !!byId["rp-note"].readOnly,
    status: { text: T("rp-status"), hidden: st.hidden },
    resultHidden: byId["rp-result"].hidden,
    resultTitle: T("rp-result-title"),
    resultLine: T("rp-result-line"),
    ref: T("rp-ref"),
    refRowHidden: byId["rp-ref-row"].hidden,
    moreBtn: T("rp-more-btn") + byId["rp-more-btn"].innerHTML,
    exactBtn: T("rp-exact-btn") + byId["rp-exact-btn"].innerHTML,
    errTitle: T("rp-err-title"),
    reportBlockHidden: byId["report-block"] ? byId["report-block"].hidden : null,
  };
}

// ---- steps ----------------------------------------------------------------
const tick = (ms) => new Promise(r => setTimeoutReal(r, ms || 20));
const snaps = {};
async function step(s) {
  const [op, a, b] = s;
  try {
    if (op === "wait") await tick(a);
    else if (op === "type") {
      const ta = byId["rp-note"]; ta.value = a; ta.fire("input");
    } else if (op === "click") byId[a].click();
    else if (op === "clickChildren") {
      for (let round = 0; round < (b || 1); round++)
        byId[a].children.slice().forEach(c => { if ((c.listeners.click || []).length) c.click(); });
    } else if (op === "main") {
      if (!global.__main) throw new Error("no MainButton handler");
      global.__main();
    } else if (op === "back") {
      if (!global.__back) throw new Error("no BackButton handler");
      global.__back();
    } else if (op === "snap") snaps[a] = snap();
    else throw new Error("unknown step " + op);
  } catch (e) { errors.push(op + ": " + (e && e.stack || e)); }
}

(async () => {
  await tick();
  for (const s of CFG.steps) { await step(s); await tick(5); }
  const all = [...Object.values(byId), ...created];
  const html = [];
  for (const el of all) for (const n of el.walk()) if (n._html) html.push(n._html);
  process.stdout.write(JSON.stringify({
    snaps, posts, fetched, errors,
    createdTags: created.map(e => e.tagName),
    html,
    copied: global.__copied || null,
  }) + "\n");
  process.exit(0);
})();
