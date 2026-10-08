// Drive the Mini App's problem report page (roadmap 8.112 follow-up) in a
// minimal DOM shim: open it from Settings, type a note, send, and read
// what the page shows and what it posts. Run by tests/test_miniapp_js_smoke.py:
//   node miniapp_report.js <page.html> <scenario>
const setTimeoutReal = setTimeout;
const fs = require("fs");
const page = fs.readFileSync(process.argv[2], "utf8");
const SCENARIO = process.argv[3] || "ready";

const created = [];
class El {
  constructor(tag) {
    this.tagName = (tag || "div").toUpperCase();
    this.children = []; this.listeners = {}; this.attrs = {};
    this._class = ""; this._text = ""; this._html = "";
    this.hidden = false; this.disabled = false; this.readOnly = false;
    this.style = {}; this.value = "";
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
  set innerHTML(v) { this._html = String(v); this.children = []; }
  appendChild(c) {
    if (c.parent) {
      const i = c.parent.children.indexOf(c);
      if (i >= 0) c.parent.children.splice(i, 1);
    }
    this.children.push(c); c.parent = this; return c;
  }
  focus() { this.focused = true; }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
  addEventListener(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  click() { (this.listeners.click || []).forEach(f => f.call(this, {})); }
  querySelectorAll() { return []; }
  *walk() { yield this; for (const c of this.children) yield* c.walk(); }
}

const byId = {};
for (const m of page.matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g)) {
  const el = new El(m[0].slice(1).split(/[\s>]/)[0]);
  el.hidden = /\shidden(\s|>|=)/.test(m[0]);
  byId[m[1]] = el;
}
for (const m of page.matchAll(/getElementById\("([^"]+)"\)/g))
  if (!byId[m[1]]) byId[m[1]] = new El("div");
for (const m of page.matchAll(/rpEl\("([^"]+)"\)/g))
  if (!byId[m[1]]) fail("the page names #" + m[1] + " but the markup has no such id");

global.document = {
  getElementById: (id) => byId[id] || (byId[id] = new El("div")),
  createElement: (t) => { const el = new El(t); created.push(el); return el; },
  addEventListener: () => {},
  querySelectorAll: () => [],
  visibilityState: "visible",
  documentElement: new El("html"),
};
global.__mbParams = {};
global.__notices = [];
const W = {
  initData: "auth_date=1&user=%7B%22id%22%3A1%7D&hash=x",
  ready() {}, expand() {},
  colorScheme: "light",
  isVersionAtLeast: () => true,
  onEvent() {}, setHeaderColor() {}, setBackgroundColor() {},
  disableVerticalSwipes() { global.__swOff = (global.__swOff || 0) + 1; },
  enableVerticalSwipes() { global.__swOn = (global.__swOn || 0) + 1; },
  BackButton: { show() { global.__backShown = true; }, hide() { global.__backShown = false; },
                onClick(fn) { global.__back = fn; } },
  HapticFeedback: { notificationOccurred(k) { global.__haptic = k; }, impactOccurred() {},
                    selectionChanged() {} },
  MainButton: {
    setParams(p) { global.__mbParams = Object.assign({}, global.__mbParams, p); },
    onClick(fn) { global.__main = fn; },
    showProgress() { global.__mbProgress = true; },
    hideProgress() { global.__mbProgress = false; },
  },
};
global.window = { Telegram: { WebApp: W } };
global.Telegram = global.window.Telegram;

function fail(msg) { console.error("FAIL: " + msg); process.exit(1); }

// ---- the fake server ---------------------------------------------------
const ERR = (over) => Object.assign({
  fingerprint: "ap1-0123456789ab", tier: "bug", where: "daemon", trigger: "log_exception",
  logger: "aipager.bot.notify", type: "builtins.KeyError", cause_types: [], errno: null,
  tg_class: null, event: null, tool: null, count: 3, first_day: "2026-10-01",
  last_day: "2026-10-07", versions_seen: ["0.7.19", "0.7.20"],
  frames: [{ file: "aipager/bot/notify.py", line: 120, fn: "deliver" }], external: ["httpx"],
}, over || {});
let REPORT = {
  schema: "aipager-report/1", trigger: "manual", day: "2026-10-08",
  aipager: { version: "0.7.20", install: "pipx", origin: "index", upgradable: false },
  python: { version: "3.12.3", impl: "CPython" },
  os: { system: "linux", arch: "x86_64", distro: "ubuntu", distro_version: "24.04",
        kernel: "6.8", container: "none", service: "systemd-user" },
  deps: { "python-telegram-bot": "22.1", httpx: "0.28.1", aiohttp: null },
  claude_code: { version: "2.1.290", installs: 1, auth: "file" },
  config: { mode: "personal", scopes_dm: 1, scopes_group: 0, custom_roles: 0,
            features: ["miniapp", "tunnel_managed"] },
  runtime: { uptime: "1-24h", sessions_live: 2, sessions_busy: 1, unclean_exits_7d: 0,
             last_exit: "clean" },
  doctor: {},
  flood: { bans_7d: 0, muted_now: false, minimal_now: false, backoff_now: false,
           hour_load: "<25%" },
  counters_24h: { stale_busy: 2 },
  log_digest_24h: [{ site: "aipager/bot/notify.py:120", level: "WARNING", n: 4 }],
  errors: [ERR(), ERR({ type: null, count: 1 }), ERR({ trigger: "crash", type: "<invalid>" }),
           ERR({ type: "telegram.error.BadRequest" })],
};
let AREAS = ["message delivery", "message delivery", "the daemon", "the busy card"];
let EXPECTED_EXACT = null;
let NOTE = null;
if (process.env.AIPAGER_TEST_REPORT) {
  const f = JSON.parse(fs.readFileSync(process.env.AIPAGER_TEST_REPORT, "utf8"));
  REPORT = f.report; AREAS = f.areas; EXPECTED_EXACT = f.expected; NOTE = f.note;
}
if (SCENARIO === "escape") {
  REPORT.errors[0] = ERR({ type: "builtins.<b>bold</b>", last_day: "<i>day</i>" });
  AREAS = ["<img src=x onerror=1>", "a&b", "x", "y"];
  REPORT.aipager.install = "<i>pipx</i>";
}

const GETS = {
  "/api/chats": { chats: [], current: "" },
  "/api/sessions": { daemon: {}, totals: { total: 0, live: 0, gone: 0, waiting: 0, cost_usd: 0 },
                     sessions: [], can_act: true },
};
const posts = [];
const queue = { "/api/report/draft": [], "/api/report/send": [] };
function answer(path, status, body) { queue[path].push({ status, body }); }
const PENDING = { pending: true };
global.fetch = (url, opts) => {
  const method = (opts && opts.method) || "GET";
  if (!url.startsWith("/api/")) fail("fetched outside /api/: " + url);
  if (method === "POST") posts.push({ url, body: JSON.parse(opts.body), headers: opts.headers });
  let next = queue[url] && queue[url].shift();
  if (!next && method === "GET") {
    next = { status: 200, body: GETS[url.split("?")[0]] || {} };
  }
  if (!next) {
    next = url === "/api/report/draft"
      ? { status: 200, body: { draft: "d-1", report: REPORT, areas: AREAS, note_max: 500,
                               expires_in: 86400, preview: "" } }
      : { status: 200, body: {} };
  }
  if (next === PENDING || next.pending) return new Promise(() => {});
  return Promise.resolve({ ok: next.status < 400, status: next.status,
                           json: () => Promise.resolve(next.body) });
};
global.setInterval = () => 0;
global.setTimeout = () => 0;
global.clearTimeout = () => {};
global.clearInterval = () => {};

let script = page.match(/<script>([\s\S]*?)<\/script>/g)
  .map(s => s.replace(/<\/?script>/g, "")).join("\n");
script = script.replace(/\}\)\(\);\s*$/,
  "\n  global.__api = { rp: rp, showNotice: showNotice };\n})();");
eval(script);
const api = global.__api;

// ---- helpers ------------------------------------------------------------
const tick = (ms) => new Promise(r => setTimeoutReal(r, ms || 15));
function type(text) {
  const ta = byId["rp-note"];
  ta.value = text;
  (ta.listeners.input || []).forEach(f => f.call(ta, {}));
}
function view() { return byId["view-report"].hidden ? "other" : "report"; }
function sends() { return posts.filter(p => p.url === "/api/report/send"); }
function drafts() { return posts.filter(p => p.url === "/api/report/draft"); }
async function openPage() {
  await tick();
  byId["report-open"].click();
  if (view() !== "report") fail("Report a problem did not open the report page");
  await tick();
}
function noticeText() { return byId["notice"].textContent; }

// ---- scenarios ----------------------------------------------------------
const S = {};

S.ready = async () => {
  await tick();
  byId["report-open"].click();
  if (view() !== "report") fail("not on the report page");
  if (!global.__backShown) fail("BackButton not shown on the report page");
  if (!byId["tabbar"].hidden) fail("tab bar still shown on a sub-page");
  if (global.__mbParams.text !== "Send report" || !global.__mbParams.is_visible)
    fail("MainButton: " + JSON.stringify(global.__mbParams));
  if (global.__mbParams.is_active) fail("MainButton active before the draft loaded");
  if (!byId["rp-body"].hidden) fail("the report body shows while loading");
  if (!(global.__swOff >= 1)) fail("swipes stay on while typing a report");
  await tick();
  if (!global.__mbParams.is_active) fail("MainButton inactive with a draft loaded");
  if (byId["rp-send"].disabled) fail("in-page Send disabled with a draft loaded");
  if (byId["rp-body"].hidden) fail("the report body did not show");
  const facts = byId["rp-facts"].children.map(c => c.children[0].textContent + "=" + c.children[1].textContent);
  const want = ["aipager=0.7.20 (pipx)", "Claude Code=2.1.290", "System=Ubuntu 24.04",
                "Python=3.12.3",
                "Setup=Personal setup, 1 private chat, running 1 to 24 hours"];
  if (JSON.stringify(facts) !== JSON.stringify(want)) fail("facts: " + JSON.stringify(facts));
  if (byId["rp-err-title"].textContent !== "Errors (4)") fail("err title " + byId["rp-err-title"].textContent);
  const rows = byId["rp-errors"].children;
  if (rows.length !== 4) fail("expected 3 error rows and Show 1 more, got " + rows.length);
  const html = rows.map(r => r.innerHTML);
  if (html[0].indexOf("KeyError in message delivery") < 0 || html[0].indexOf("3 times, last seen 2026-10-07") < 0)
    fail("row 1: " + html[0]);
  if (html[1].indexOf("Logged error in message delivery") < 0 || html[1].indexOf("once") < 0)
    fail("row 2: " + html[1]);
  if (html[2].indexOf("aipager stopped unexpectedly") < 0) fail("row 3: " + html[2]);
  if (html[3].indexOf("Show 1 more") < 0) fail("row 4: " + html[3]);
  rows[0].click();
  const panel = byId["rp-errors"].children[1];
  if (!panel || panel.className !== "panel") fail("an error row did not open its panel");
  if (panel.textContent.indexOf("aipager/bot/notify.py:120  deliver") < 0)
    fail("panel: " + panel.textContent);
  byId["rp-errors"].children[byId["rp-errors"].children.length - 1].click();
  if (byId["rp-errors"].children.filter(c => c.className === "sect-toggle").length !== 4)
    fail("Show 1 more did not show the fourth error");
  if (!byId["rp-more-facts"].hidden) fail("More details open by default");
  byId["rp-more-btn"].click();
  if (byId["rp-more-facts"].hidden) fail("More details did not open");
  if (byId["rp-more-facts"].textContent.indexOf("aiohttp (missing)") < 0)
    fail("libraries: " + byId["rp-more-facts"].textContent);
  if (byId["rp-count"].textContent !== "0 / 500") fail("count " + byId["rp-count"].textContent);
  console.log("ok: report page opens, loads, and shows the summary");
};

S.send_ok = async () => {
  await openPage();
  answer("/api/report/send", 200, { outcome: "sent", reference: "ap1-0123456789ab",
                                    line: "Sent.", retry: false, repeat: false });
  type("  It froze  ");
  global.__main();
  if (!global.__mbProgress) fail("MainButton shows no progress while sending");
  if (!byId["rp-note"].readOnly) fail("the note stays editable while sending");
  await tick();
  const s = sends();
  if (s.length !== 1) fail("sends: " + s.length);
  if (JSON.stringify(Object.keys(s[0].body).sort()) !== '["draft","note"]')
    fail("send body keys: " + JSON.stringify(s[0].body));
  if (s[0].body.draft !== "d-1" || s[0].body.note !== "It froze")
    fail("send body: " + JSON.stringify(s[0].body));
  if (!byId["rp-form"].hidden || byId["rp-result"].hidden) fail("no result panel");
  if (byId["rp-result-title"].textContent !== "Report sent") fail("title " + byId["rp-result-title"].textContent);
  if (byId["rp-ref"].textContent !== "ap1-0123456789ab" || byId["rp-ref-row"].hidden)
    fail("reference not shown");
  if (global.__mbParams.text !== "Done") fail("MainButton after send: " + JSON.stringify(global.__mbParams));
  if (global.__haptic !== "success") fail("no success haptic");
  byId["rp-copy"].click();
  if (byId["rp-copy-hint"].hidden) fail("no clipboard and no long-press hint");
  global.__main();
  if (view() === "report") fail("Done did not leave the report page");
  console.log("ok: send posts {draft, note}, shows the reference, Done leaves");
};

S.note_changed = async () => {
  await openPage();
  answer("/api/report/send", 422, { error: "note_changed", note: "It froze." });
  type("It froze.​");
  byId["rp-send"].click();
  await tick();
  if (byId["rp-note"].value !== "It froze.") fail("the tidied note was not put back");
  if (byId["rp-status"].hidden || byId["rp-status"].textContent.indexOf("tidied up") < 0)
    fail("status: " + byId["rp-status"].textContent);
  if (byId["rp-send"].disabled || !global.__mbParams.is_active) fail("Send not active again");
  if (byId["rp-count"].textContent !== "9 / 500") fail("count " + byId["rp-count"].textContent);
  console.log("ok: a tidied note is shown back before anything is sent");
};

S.stale = async () => {
  await openPage();
  answer("/api/report/send", 410, { error: "draft_gone" });
  answer("/api/report/draft", 200, { draft: "d-2", report: REPORT, areas: AREAS, note_max: 500 });
  type("keep me");
  byId["rp-send"].click();
  await tick(30);
  if (drafts().length !== 2) fail("no fresh draft after 410: " + drafts().length);
  if (byId["rp-note"].value !== "keep me") fail("the note was lost");
  if (byId["rp-status"].textContent.indexOf("out of date") < 0) fail("status " + byId["rp-status"].textContent);
  if (api.rp.draft !== "d-2") fail("draft not replaced");
  console.log("ok: a stale draft loads a fresh one and keeps the note");
};

S.try_later = async () => {
  await openPage();
  const line = "Could not send right now. Nothing was lost; try again later.";
  answer("/api/report/send", 200, { outcome: "rate_limited", reference: null, line,
                                    retry: true, repeat: false });
  byId["rp-send"].click();
  await tick();
  if (byId["rp-form"].hidden) fail("try later left the form");
  if (byId["rp-status"].textContent !== line || !byId["rp-status"].className.includes("is-err"))
    fail("status " + byId["rp-status"].textContent);
  if (!global.__mbParams.is_active || global.__mbParams.text !== "Send report") fail("cannot retry");
  console.log("ok: try later keeps the page and lets Send retry");
};

S.too_old = async () => {
  await openPage();
  answer("/api/report/send", 200, { outcome: "too_old", reference: null, retry: false, repeat: false,
    line: "This aipager version can no longer send reports. Update it (`aipager update`) and try again." });
  byId["rp-send"].click();
  await tick();
  if (byId["rp-result-title"].textContent !== "Update needed") fail("title");
  const line = byId["rp-result-line"];
  if (line.textContent.indexOf("`") >= 0) fail("raw backticks: " + line.textContent);
  const code = line.children.find(c => c.tagName === "CODE");
  if (!code || code.textContent !== "aipager update") fail("no code element for the command");
  if (!byId["rp-ref-row"].hidden) fail("a reference row on a failure");
  if (global.__haptic !== "error") fail("no error haptic");
  console.log("ok: too old shows its line with the command as code");
};

S.escape = async () => {
  await openPage();
  type("<script>x</script>");
  byId["rp-exact-btn"].click();
  byId["rp-errors"].children[0].click();
  byId["rp-more-btn"].click();
  const bad = created.filter(e => ["IMG", "B", "I", "SCRIPT"].includes(e.tagName));
  if (bad.length) fail("an element was made from report data: " + bad.map(e => e.tagName));
  for (const root of Object.values(byId)) {
    for (const n of root.walk()) {
      if (/<img|<b>|<i>|<script/i.test(n._html)) fail("report data reached innerHTML raw: " + n._html);
    }
  }
  const row = byId["rp-errors"].children[0].innerHTML;
  if (row.indexOf("&lt;b&gt;bold&lt;/b&gt; in &lt;img src=x onerror=1&gt;") < 0 ||
      row.indexOf("last seen &lt;i&gt;day&lt;/i&gt;") < 0) fail("row: " + row);
  if (byId["rp-exact"].textContent.indexOf("<script>x</script>") < 0) fail("exact lost the note");
  console.log("ok: report values and the note never reach markup");
};

S.back_while_sending = async () => {
  await openPage();
  answer("/api/report/send", 0, null);
  queue["/api/report/send"][0] = PENDING;
  byId["rp-send"].click();
  if (!api.rp.sending) fail("not sending");
  global.__back();
  if (view() !== "report") fail("Back left the page while sending");
  if (noticeText().indexOf("Sending. One moment.") < 0) fail("notice " + noticeText());
  if (byId["rp-send"].textContent !== "Sending...") fail("button " + byId["rp-send"].textContent);
  console.log("ok: Back while sending stays on the page");
};

S.back_cancels = async () => {
  await openPage();
  type("never mind");
  global.__back();
  if (view() === "report") fail("Back did not leave the page");
  if (sends().length) fail("Back sent something");
  byId["report-open"].click();
  if (byId["rp-note"].value !== "never mind") fail("the note was not kept for the next open");
  console.log("ok: Back sends nothing and keeps the note");
};

S.emoji_count = async () => {
  await openPage();
  type("😀".repeat(300));
  if (byId["rp-count"].textContent !== "300 / 500") fail("count " + byId["rp-count"].textContent);
  if (byId["rp-send"].disabled || !global.__mbParams.is_active) fail("300 emoji refused");
  type("a".repeat(450) + "😀".repeat(51));
  if (byId["rp-count"].textContent !== "501 / 500") fail("count " + byId["rp-count"].textContent);
  if (!byId["rp-send"].disabled || global.__mbParams.is_active) fail("501 code points accepted");
  if (byId["rp-hint"].textContent !== "Too long by 1 character.") fail("hint " + byId["rp-hint"].textContent);
  if (!byId["rp-count"].className.includes("is-over")) fail("counter not red");
  if (byId["rp-note"].attrs.maxlength || /id="rp-note"[^>]*maxlength/.test(page)) fail("maxlength set");
  console.log("ok: the counter counts code points");
};

S.forbidden = async () => {
  answer("/api/report/draft", 403, { error: "forbidden" });
  await openPage();
  if (byId["rp-result-title"].textContent !== "Owner only") fail("title " + byId["rp-result-title"].textContent);
  if (byId["error"].textContent) fail("the app ended: " + byId["error"].textContent);
  if (!byId["report-block"].hidden) fail("the Settings card stays for a non-owner");
  if (global.__mbParams.text !== "Done") fail("MainButton " + JSON.stringify(global.__mbParams));
  console.log("ok: 403 shows Owner only and the app lives on");
};

S.group = async () => {
  answer("/api/report/draft", 403, { error: "private_only" });
  await openPage();
  if (byId["rp-result-title"].textContent !== "Open your private chat") fail("title");
  console.log("ok: a group chat is told to use the private chat");
};

S.build_failed = async () => {
  answer("/api/report/draft", 503, { error: "not_built" });
  await openPage();
  if (byId["rp-result-title"].textContent !== "Couldn't prepare the report") fail("title");
  if (global.__mbParams.text !== "Try again") fail("MainButton " + JSON.stringify(global.__mbParams));
  global.__main();
  await tick();
  if (drafts().length !== 2) fail("Try again did not load a draft");
  if (byId["rp-form"].hidden) fail("the form did not come back");
  console.log("ok: a failed build offers Try again");
};

S.exact = async () => {
  await openPage();
  byId["rp-exact-btn"].click();
  if (byId["rp-exact-wrap"].hidden) fail("the exact report did not open");
  type(NOTE);
  if (byId["rp-exact"].textContent !== EXPECTED_EXACT)
    fail("exact block differs from the server's rendering:\n" + byId["rp-exact"].textContent);
  type("");
  if (byId["rp-exact"].textContent.indexOf('"note"') >= 0) fail("an empty note shows a note key");
  console.log("ok: the exact block equals the bytes sent");
};

(S[SCENARIO] || (() => fail("unknown scenario: " + SCENARIO)))().then(
  () => process.exit(0), (e) => fail(e && e.stack || e));
