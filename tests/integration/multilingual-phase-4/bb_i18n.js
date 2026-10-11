// Black-box driver for the served Mini App page in English, Persian and
// Russian (multilingual phase 4, entrypoints.md "Page contract").
//
// Usage: node bb_i18n.js <page.html> <fixture.json> <result.json>
//
// fixture.json: {"fix": {"METHOD /path": body | {status, body, delay?} | {reject}},
//                "storage": {key: value} | null,
//                "steps": [{"do": "boot"|"tab"|"tap_text"|"tap_tile"|"wait",
//                           "id"?, "text"?, "label"?, "name": snapshot name}]}
//
// It finds elements only by documented ids, data-i18n* attributes and
// visible text; it never calls a page-internal function. After each step it
// takes a snapshot (document attributes, every fetch so far, every static
// string element, the text/aria/placeholder/dir of every element, every
// innerHTML written) and writes them to result.json at the end. The Python side
// asserts one property per test.
"use strict";
const fs = require("fs");
const setTimeoutReal = setTimeout;
const page = fs.readFileSync(process.argv[2], "utf8");
const FIXTURE = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

const errors = [];
process.on("uncaughtException", (e) => { errors.push(String(e && e.stack || e)); });
process.on("unhandledRejection", (e) => { errors.push(String(e && e.stack || e)); });

// ---- DOM shim ------------------------------------------------------------
const ALL = [];   // every element ever made, for snapshots
class El {
  constructor(tag) {
    this.tagName = (tag || "div").toUpperCase();
    this.children = []; this.listeners = {}; this.attrs = {};
    this._class = ""; this._text = ""; this._html = ""; this.htmlWrites = [];
    this.hidden = false; this.disabled = false; this.value = "";
    this.dataset = {};
    const styleStore = {};
    this.style = {
      setProperty: (k, v) => { styleStore[k] = v; },
      removeProperty: (k) => { delete styleStore[k]; },
      getPropertyValue: (k) => styleStore[k] || "",
    };
    this.classList = {
      add: (...cs) => { for (const c of cs) if (!this._class.split(" ").includes(c)) this._class = (this._class + " " + c).trim(); },
      remove: (...cs) => { this._class = this._class.split(" ").filter(x => !cs.includes(x)).join(" "); },
      toggle: (c, on) => { const has = this.classList.contains(c); const want = on === undefined ? !has : !!on; want ? this.classList.add(c) : this.classList.remove(c); return want; },
      contains: (c) => this._class.split(" ").includes(c),
    };
    ALL.push(this);
  }
  get className() { return this._class; }
  set className(v) { this._class = String(v); }
  get textContent() {
    if (this.children.length) return this.children.map(c => c.textContent).join("");
    return this._text;
  }
  set textContent(v) {
    for (const c of this.children) c.parent = null;
    this._text = String(v); this._html = ""; this.children = [];
  }
  get innerText() { return this.textContent; }
  set innerText(v) { this.textContent = v; }
  get innerHTML() { return this._html; }
  set innerHTML(v) {
    for (const c of this.children) c.parent = null;
    this._html = String(v); this.htmlWrites.push(this._html);
    this._text = this._html.replace(/<[^>]*>/g, "")
      .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, "\"")
      .replace(/&#39;/g, "'").replace(/&#x27;/g, "'").replace(/&amp;/g, "&");
    this.children = [];
  }
  get parentNode() { return this.parent || null; }
  get parentElement() { return this.parent || null; }
  get firstChild() { return this.children[0] || null; }
  get lastChild() { return this.children[this.children.length - 1] || null; }
  get firstElementChild() { return this.children[0] || null; }
  get childNodes() { return this.children; }
  get nextSibling() {
    if (!this.parent) return null;
    const s = this.parent.children; return s[s.indexOf(this) + 1] || null;
  }
  get previousSibling() {
    if (!this.parent) return null;
    const s = this.parent.children; return s[s.indexOf(this) - 1] || null;
  }
  get id() { return this.attrs.id || ""; }
  set id(v) { this.attrs.id = v; }
  get dir() { return this.attrs.dir || ""; }
  set dir(v) { this.attrs.dir = String(v); }
  get lang() { return this.attrs.lang || ""; }
  set lang(v) { this.attrs.lang = String(v); }
  get placeholder() { return this.attrs.placeholder || ""; }
  set placeholder(v) { this.attrs.placeholder = String(v); }
  get title() { return this.attrs.title || ""; }
  set title(v) { this.attrs.title = String(v); }
  appendChild(c) {
    if (c.parent) { const i = c.parent.children.indexOf(c); if (i >= 0) c.parent.children.splice(i, 1); }
    this.children.push(c); c.parent = this; return c;
  }
  append(...cs) { for (const c of cs) this.appendChild(typeof c === "string" ? textNode(c) : c); }
  prepend(...cs) { for (const c of cs.reverse()) this.insertBefore(typeof c === "string" ? textNode(c) : c, this.children[0] || null); }
  insertBefore(c, ref) {
    if (!ref) return this.appendChild(c);
    if (c.parent) { const i = c.parent.children.indexOf(c); if (i >= 0) c.parent.children.splice(i, 1); }
    const at = this.children.indexOf(ref);
    this.children.splice(at < 0 ? this.children.length : at, 0, c);
    c.parent = this; return c;
  }
  removeChild(c) {
    const i = this.children.indexOf(c);
    if (i < 0) throw new Error("removeChild: not a child");
    this.children.splice(i, 1); c.parent = null; return c;
  }
  replaceChild(n, o) { this.insertBefore(n, o); this.removeChild(o); return o; }
  replaceChildren(...cs) { for (const c of this.children) c.parent = null; this.children = []; this.append(...cs); }
  remove() { if (this.parent) this.parent.removeChild(this); }
  contains(n) { for (const x of this.walk()) if (x === n) return true; return false; }
  focus() { this.focused = true; }
  blur() { this.focused = false; }
  select() {}
  scrollIntoView() {}
  scrollTo() {}
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "hidden") this.hidden = true; }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; }
  removeAttribute(k) { delete this.attrs[k]; if (k === "hidden") this.hidden = false; }
  hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k); }
  toggleAttribute(k, on) { if (on === undefined ? !this.hasAttribute(k) : on) this.setAttribute(k, ""); else this.removeAttribute(k); }
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  removeEventListener(ev, fn) { this.listeners[ev] = (this.listeners[ev] || []).filter(f => f !== fn); }
  dispatch(ev, extra) {
    const e = Object.assign({ type: ev, target: this, currentTarget: this, key: "",
      preventDefault() {}, stopPropagation() {} }, extra || {});
    (this.listeners[ev] || []).forEach(f => f.call(this, e));
  }
  click() { if (this.disabled) return; this.dispatch("click"); }
  querySelectorAll(sel) { return select(Array.from(this.walk()).slice(1), sel); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  matches(sel) { return String(sel).split(",").some(one => matchesSimple(this, one.trim())); }
  closest(sel) {
    let n = this;
    while (n) { if (n.matches && n.matches(sel)) return n; n = n.parent || n.staticParent || null; }
    return null;
  }
  getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 40, right: 100, bottom: 40 }; }
  *walk() { yield this; for (const c of this.children) yield* c.walk(); }
}
function textNode(s) { const t = new El("#text"); t._text = String(s); return t; }

// One compound selector: tag, #id, .class and [attr] parts ("button.grp-head").
function matchesSimple(el, sel) {
  if (!sel || /[\s>+~:]/.test(sel)) return false;
  const parts = sel.match(/(^[a-zA-Z0-9]+)|(#[-\w]+)|(\.[-\w]+)|(\[[-\w]+(?:="[^"]*")?\])/g) || [];
  if (parts.join("") !== sel) return false;
  return parts.every((p) => {
    if (p[0] === "#") return el.attrs.id === p.slice(1);
    if (p[0] === ".") return el._class.split(" ").includes(p.slice(1));
    if (p[0] === "[") {
      const m = /^\[([-\w]+)(?:="([^"]*)")?\]$/.exec(p);
      return m[2] === undefined ? el.hasAttribute(m[1]) : el.getAttribute(m[1]) === m[2];
    }
    return el.tagName === p.toUpperCase();
  });
}

// Attribute selectors only ("[a]", "[a],[b]"), the documented ones.
function select(nodes, sel) {
  const attrs = String(sel).split(",").map(s => s.trim());
  const ok = attrs.every(a => /^\[[a-z0-9-]+\]$/.test(a));
  if (!ok) return [];
  const names = attrs.map(a => a.slice(1, -1));
  return nodes.filter(n => names.some(k => n.hasAttribute(k)));
}

// Every element of the markup that has an id or a data-i18n* attribute.
const byId = {};
const STATIC = [];
const markup = page.slice(page.indexOf("<body"), page.indexOf("<script", page.indexOf("<body")));
const VOID = new Set(["input", "br", "img", "meta", "link", "hr", "use", "source", "path", "circle", "rect"]);
for (const m of markup.matchAll(/<([a-zA-Z0-9]+)((?:[^>"']|"[^"]*"|'[^']*')*)>/g)) {
  const tag = m[1]; const attrText = m[2];
  const hasId = /\bid="/.test(attrText);
  const hasI18n = /\sdata-i18n(-label|-placeholder|-num)?="/.test(attrText);
  if (!hasId && !hasI18n) continue;
  const el = new El(tag);
  for (const a of attrText.matchAll(/([a-zA-Z_:][-a-zA-Z0-9_:.]*)(?:="([^"]*)")?/g)) {
    el.attrs[a[1]] = a[2] === undefined ? "" : a[2].replace(/&amp;/g, "&").replace(/&quot;/g, "\"");
  }
  el.hidden = "hidden" in el.attrs; el.disabled = "disabled" in el.attrs;
  if ("value" in el.attrs) el.value = el.attrs.value;
  if (!VOID.has(tag.toLowerCase())) {
    const rest = markup.slice(m.index + m[0].length);
    const close = rest.indexOf("</" + tag + ">");
    const inner = close >= 0 ? rest.slice(0, close) : "";
    // Leaf text only: an element holding id'd children gets those as nodes.
    if (!/<[a-zA-Z]/.test(inner) || hasI18n) {
      el._text = inner.replace(/<[^>]*>/g, "").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">");
    }
  }
  el.isStatic = true;
  if (hasId) byId[el.attrs.id] = el;
  STATIC.push(el);
}
// Static nesting (the shim keeps static elements flat); used only to judge
// whether a node is on screen.
{
  const stack = [];
  const byStart = new Map();
  for (const e of STATIC) byStart.set(e, true);
  let k = 0;
  for (const m of markup.matchAll(/<(\/?)([a-zA-Z0-9]+)((?:[^>"']|"[^"]*"|'[^']*')*?)(\/?)>/g)) {
    const [, close, tag, attrText, selfClose] = m;
    const t = tag.toLowerCase();
    if (close) {
      const at = stack.map(x => x.tag).lastIndexOf(t);
      if (at >= 0) stack.length = at;
      continue;
    }
    const tracked = /\bid="/.test(attrText) || /\sdata-i18n(-label|-placeholder|-num)?="/.test(attrText);
    let el = null;
    if (tracked) { el = STATIC[k++]; const up = stack.slice().reverse().find(x => x.el); el.staticParent = up ? up.el : null; }
    if (!selfClose && !VOID.has(t)) stack.push({ tag: t, el });
  }
}
const docEl = new El("html");
{
  const h = /<html([^>]*)>/.exec(page);
  if (h) for (const a of h[1].matchAll(/([a-zA-Z-]+)="([^"]*)"/g)) docEl.attrs[a[1]] = a[2];
}
// Every lang/dir write on <html> as [attr, value, clock], where clock is the
// fetch counter (the same one as calls[].sent/answered), so a language shown
// only for a moment (a late catalog before the page heals) is still seen.
const htmlLog = [];
{
  const setAttr = docEl.setAttribute.bind(docEl);
  docEl.setAttribute = (k, v) => { if (k === "lang" || k === "dir") htmlLog.push([k, String(v), order]); setAttr(k, v); };
  for (const k of ["lang", "dir"]) {
    Object.defineProperty(docEl, k, {
      get() { return docEl.attrs[k] || ""; },
      set(v) { htmlLog.push([k, String(v), order]); docEl.attrs[k] = String(v); },
    });
  }
}
const MARKUP_ENGLISH = STATIC.map(e => ({ text: e._text, aria: e.attrs["aria-label"] || null,
                                          placeholder: e.attrs.placeholder || null }));
function connected() {
  const out = [];
  for (const s of STATIC) for (const n of s.walk()) out.push(n);
  return Array.from(new Set(out));
}
global.document = {
  getElementById: (id) => byId[id] || null,
  createElement: (t) => new El(t),
  createElementNS: (_ns, t) => new El(t),
  createTextNode: (s) => textNode(s),
  createDocumentFragment: () => new El("#fragment"),
  addEventListener: () => {},
  removeEventListener: () => {},
  querySelectorAll: (sel) => select(connected(), sel),
  querySelector: (sel) => select(connected(), sel)[0] || null,
  visibilityState: "visible",
  hidden: false,
  documentElement: docEl,
  body: new El("body"),
  activeElement: null,
};

// ---- Telegram WebApp mock ---------------------------------------------------
const tg = { mb: {}, main: null, confirms: [], events: {} };
const webApp = {
  initData: "auth_date=1&user=%7B%22id%22%3A2%7D&hash=x",
  ready() {}, expand() {}, close() {},
  colorScheme: "light", version: "8.0", isVersionAtLeast: () => true,
  onEvent(name, fn) { tg.events[name] = fn; }, offEvent() {},
  setHeaderColor() {}, setBackgroundColor() {},
  disableVerticalSwipes() {}, enableVerticalSwipes() {},
  BackButton: { show() {}, hide() {}, onClick() {}, offClick() {} },
  HapticFeedback: { notificationOccurred() {}, impactOccurred() {}, selectionChanged() {} },
  showConfirm(message, cb) { tg.confirms.push(String(message)); if (cb) cb(false); },
  MainButton: {
    setParams(p) { tg.mb = Object.assign({}, tg.mb, p); },
    setText(t) { tg.mb = Object.assign({}, tg.mb, { text: t }); },
    show() { tg.mb.is_visible = true; }, hide() { tg.mb.is_visible = false; },
    enable() {}, disable() {}, onClick(fn) { tg.main = fn; }, offClick() {},
    showProgress() {}, hideProgress() {},
  },
};
const storage = FIXTURE.storage;
const storageLog = [];
const localStorage = storage === null ? undefined : {
  getItem(k) { return Object.prototype.hasOwnProperty.call(storage, k) ? storage[k] : null; },
  setItem(k, v) { storage[k] = String(v); storageLog.push(["set", k, String(v), order]); },
  removeItem(k) { delete storage[k]; storageLog.push(["remove", k, null, order]); },
};
global.window = { Telegram: { WebApp: webApp }, addEventListener() {}, removeEventListener() {},
                  localStorage, confirm: (m) => { tg.confirms.push(String(m)); return false; },
                  matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }) };
global.localStorage = localStorage;
global.Telegram = undefined;
Object.defineProperty(global, "navigator", { value: { userAgent: "node", clipboard: undefined }, configurable: true, writable: true });
global.location = { pathname: "/", search: "", hash: "" };
global.setInterval = () => 0;
global.setTimeout = () => 0;
global.clearTimeout = () => {};
global.clearInterval = () => {};
global.requestAnimationFrame = undefined;

// ---- fetch -----------------------------------------------------------------
const FIX = FIXTURE.fix;
const calls = [];       // {method, url, path, body, answered}
let order = 0;
global.fetch = (url, opts) => {
  const method = (opts && opts.method) || "GET";
  const path = String(url).split("?")[0];
  const call = { method, url: String(url), path, body: opts && opts.body ? String(opts.body) : null,
                 sent: order++, answered: null };
  calls.push(call);
  let spec = FIX[method + " " + path];
  if (method === "PUT" && path === "/api/app-language" && call.body) {
    const v = JSON.parse(call.body).value;
    spec = FIX["PUT /api/app-language " + v] || spec;
    // From now on the server answers as it does after that PUT.
    if (FIXTURE.after_put && FIXTURE.after_put[v]) Object.assign(FIX, FIXTURE.after_put[v]);
  }
  if (spec && spec.reject) return Promise.reject(new TypeError("Failed to fetch"));
  let status = 200; let body = {};
  if (spec && spec.status !== undefined && spec.body !== undefined) { status = spec.status; body = spec.body; }
  else if (spec) { body = spec; }
  else { status = 404; body = { error: "not_found" }; }
  const text = JSON.stringify(body);
  // {status, body, delay}: answer after *delay* ms (a slow network), so a
  // later request can be answered first.
  const delay = spec && spec.delay ? spec.delay : 1;
  return new Promise((res) => setTimeoutReal(() => {
    call.answered = order++;
    res({ ok: status < 400, status, headers: { get: () => null },
          json: () => Promise.resolve(JSON.parse(text)), text: () => Promise.resolve(text) });
  }, delay));
};

// ---- run the page ------------------------------------------------------------
const script = page.match(/<script>([\s\S]*?)<\/script>/g)
  .map(s => s.replace(/<\/?script>/g, "")).join("\n");
try { (0, eval)(script); } catch (e) { errors.push("eval: " + (e && e.stack || e)); }

// ---- snapshots -------------------------------------------------------------------
const wait = (ms) => new Promise(r => setTimeoutReal(r, ms || 25));
function visibleText(el) {
  // Text a person can see: nothing under a hidden ancestor.
  let n = el; while (n) { if (n.hidden) return null; n = n.parent || n.staticParent || null; }
  return el.textContent;
}
function snapshot() {
  const nodes = connected();
  return {
    html: Object.assign({}, docEl.attrs),
    html_log: htmlLog.slice(),
    calls: calls.map(c => ({ method: c.method, url: c.url, sent: c.sent, answered: c.answered,
                             body: c.body })),
    statics: STATIC.map((e, i) => ({
      key: e.attrs["data-i18n"] || null, label_key: e.attrs["data-i18n-label"] || null,
      placeholder_key: e.attrs["data-i18n-placeholder"] || null,
      num: e.attrs["data-i18n-num"] || null, id: e.attrs.id || null,
      text: e.textContent, aria: e.attrs["aria-label"] || null,
      placeholder: e.attrs.placeholder || null, markup: MARKUP_ENGLISH[i],
    })),
    ids: Object.fromEntries(Object.entries(byId).map(([k, e]) => [k, {
      text: e.textContent, hidden: e.hidden, dir: e.attrs.dir || null,
      aria: e.attrs["aria-label"] || null }])),
    leaves: nodes.filter(n => !n.children.length && (n._text || "").length)
      .map(n => ({ text: n._text, dir: n.attrs.dir || null, cls: n._class,
                   aria: n.attrs["aria-label"] || null, static: !!n.isStatic,
                   visible: visibleText(n) !== null })),
    arias: nodes.map(n => n.attrs["aria-label"]).filter(Boolean),
    dirs: nodes.filter(n => n.attrs.dir).map(n => ({ dir: n.attrs.dir, text: n.textContent })),
    htmls: ALL.flatMap(n => n.htmlWrites),
    mainbutton: Object.assign({}, tg.mb),
    confirms: tg.confirms.slice(),
    storage: storage === null ? null : Object.assign({}, storage),
    storage_log: storageLog.slice(),
  };
}

function findByText(root, text) {
  // The innermost clickable element whose text contains *text*.
  const hits = Array.from(root.walk()).filter(e => e.listeners.click && e.textContent.indexOf(text) !== -1);
  return hits[hits.length - 1] || null;
}

(async () => {
  const out = {};
  for (const step of FIXTURE.steps) {
    await wait(step.ms || 40);
    if (step.do === "tab") byId[step.id] && byId[step.id].click();
    if (step.do === "tap_text") {
      const root = step.id ? byId[step.id] : null;
      const roots = root ? [root] : STATIC;
      let hit = null;
      for (const r of roots) { hit = findByText(r, step.text) || hit; if (hit) break; }
      // step.open: the header that reveals the option, tapped only when the
      // option is not on screen yet (a collapsed group).
      if (!hit && step.open) {
        for (const r of roots) { const h = findByText(r, step.open); if (h) { h.click(); break; } }
        for (let i = 0; i < 4; i++) await wait(30);
        for (const r of roots) { hit = findByText(r, step.text) || hit; if (hit) break; }
      }
      if (!hit) errors.push("tap_text: no element with " + JSON.stringify(step.text));
      else hit.click();
    }
    if (step.do === "tap_tile") {
      const tiles = byId.sessions ? byId.sessions.children : [];
      const t = tiles.find(x => x.textContent.indexOf(step.label) !== -1);
      if (!t) errors.push("tap_tile: no tile " + step.label);
      else { const c = Array.from(t.walk()).find(e => e.listeners.click); (c || t).click(); }
    }
    for (let i = 0; i < 6; i++) await wait(30);
    if (step.name) out[step.name] = snapshot();
    if (process.env.BB_DUMP && step.name) process.stderr.write("== after " + step.name + "\n" +
      process.env.BB_DUMP.split(",").map(id => JSON.stringify(Array.from((byId[id] || new El()).walk()).map(e => [e.tagName, e._class, e._text]))).join("\n") + "\n");
  }
  out.errors = errors;
  if (process.env.BB_DUMP) {
    const dump = (el, d) => { if (!el) return ""; let o = " ".repeat(d * 2) + el.tagName + (el._class ? "." + el._class.replace(/ /g, ".") : "") +
      (el.hidden ? " HIDDEN" : "") + (el.listeners.click ? " CLICK" : "") + " " + JSON.stringify(el.attrs) +
      (!el.children.length && el._text ? " '" + el._text.slice(0, 60) + "'" : "") + "\n";
      for (const c of el.children) o += dump(c, d + 1); return o; };
    for (const id of process.env.BB_DUMP.split(",")) process.stderr.write("## " + id + "\n" + dump(byId[id], 0));
  }
  fs.writeFileSync(process.argv[4], JSON.stringify(out));
  process.exit(0);
})();
