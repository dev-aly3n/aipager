"""Client-side JS for the Mini App page.

A raw Python string (``r\"\"\"...\"\"\"``) — the JS below uses literal
``\\n``/regex escapes freely, which would otherwise get mangled by
Python's own backslash-escape parsing of a normal string literal.

No framework, no bundler, no external asset beyond Telegram's own
``telegram-web-app.js`` (already loaded in ``_shell.py``) — see
design.md Decision 4. Everything server-controlled that lands in
``innerHTML`` (labels, models, tool text, diff patch content — the last
of which can contain arbitrary file content from the session's working
tree) goes through ``escapeHtml`` first; nothing here trusts session
data to be HTML-safe just because it came from this daemon's own state.
"""

from __future__ import annotations

APP_JS = r"""
(function () {
  var tg = window.Telegram && window.Telegram.WebApp;
  if (tg) { tg.ready(); tg.expand(); }

  // Colours come from Telegram's theme variables except the amber pair,
  // picked by <html data-scheme>. Telegram's header and background are
  // painted in the canvas colour. Feature-checked: older clients lack these.
  function tgAtLeast(version) {
    return !(tg && typeof tg.isVersionAtLeast === "function") || tg.isVersionAtLeast(version);
  }
  function applyScheme() {
    var root = document.documentElement;
    if (root && root.setAttribute) {
      root.setAttribute("data-scheme", tg && tg.colorScheme === "dark" ? "dark" : "light");
    }
    if (!tg || !tgAtLeast("6.1")) { return; }
    try {
      if (typeof tg.setHeaderColor === "function") { tg.setHeaderColor("secondary_bg_color"); }
      if (typeof tg.setBackgroundColor === "function") { tg.setBackgroundColor("secondary_bg_color"); }
    } catch (e) { /* older client */ }
  }
  applyScheme();
  if (tg && typeof tg.onEvent === "function") {
    try { tg.onEvent("themeChanged", applyScheme); } catch (e) { /* older client */ }
  }
  var initData = tg ? tg.initData : "";

  // ---- which chat (roadmap 8.73) ---------------------------------------
  // A person in several chats sees one at a time. chosenScope names it to
  // the server on EVERY call (authHeaders is the only place the page
  // builds request headers); "" until /api/chats has answered, which the
  // server reads as its default chat.
  var chosenScope = "";
  var SCOPE_KEY = "aipager.chat";

  function authHeaders(json) {
    var h = { "X-Telegram-Init-Data": initData };
    if (chosenScope) { h["X-Aipager-Scope"] = chosenScope; }
    if (json) { h["Content-Type"] = "application/json"; }
    return h;
  }

  // The remembered choice is a per-viewer convenience: storage can be
  // missing or throw (private mode, blocked site data), and then the
  // page simply starts on the server's default chat.
  function readSavedScope() {
    try { return window.localStorage.getItem(SCOPE_KEY) || ""; } catch (e) { return ""; }
  }
  function saveScope(scope) {
    try { window.localStorage.setItem(SCOPE_KEY, scope); } catch (e) { /* no storage */ }
  }

  // Poll only while visible; returning forces one immediate poll.
  var POLL_INTERVAL_MS = 2500;

  var authExpired = false;      // 401/403 seen -> stop polling for good;
                                 // retrying a signature that will never
                                 // verify again just wastes battery.
  var consecutiveFailures = 0;
  var lastSuccessAt = null;
  var pollTimer = null;
  var currentView = { type: "grid" };
  // Object.create(null): a session may legitimately be labelled "__proto__"
  // (/new has no character allowlist), and plain-object bracket assignment
  // would hit the prototype setter instead of creating an own property.
  var lastStatuses = Object.create(null);   // label -> status, for haptics
  var lastSessionsByLabel = Object.create(null); // label -> last row, so tickAges re-stamps
                                // age labels between polls without a refetch
  var diffLoadedForLabel = null;
  var lastDetailData = null;   // last /api/sessions/{label} payload
  var lastDiffData = null;     // last /diff payload, for the section header

  function escapeHtml(value) {
    var s = (value === null || value === undefined) ? "" : String(value);
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function statusClass(s) { return "status status-" + s; }

  // ---- the page's language (multilingual phase 4) ----------------------
  // The page holds keys only. AIPAGER_UI_EN (inlined by static/__init__.py)
  // is every string's English; another language's strings arrive from
  // /api/i18n/<code>.json once the server names one in a `ui` entry. A
  // string that language lacks falls back to its English, never a key.
  var UI_EN = (typeof AIPAGER_UI_EN === "object" && AIPAGER_UI_EN) || {};
  var LANG_KEY = "aipager.lang";
  // Wrapped around a string inserted into a right-to-left sentence, so a
  // name or a path keeps its own direction (written as escapes: the page's
  // source holds no raw direction control).
  var ISOLATE_OPEN = "\u2068";
  var ISOLATE_CLOSE = "\u2069";
  function englishLang() {
    return { code: "en", v: "", rtl: false, digits: null, decimal: ".", group: ",",
             percent: "%", list: ", ", strings: null };
  }
  // A language's catalog (the /api/i18n/<code>.json body) as the page
  // holds it.
  function langFrom(cat, version) {
    var digits = typeof cat.digits === "string" && cat.digits.length === 10 &&
      cat.digits !== "0123456789" ? cat.digits : null;
    return { code: cat.code, v: version, rtl: !!cat.rtl, digits: digits,
             decimal: String(cat.decimal || "."), group: String(cat.group || ","),
             percent: String(cat.percent || "%"),
             list: typeof cat.list === "string" ? cat.list : ", ",
             strings: cat.strings && typeof cat.strings === "object" ? cat.strings : {} };
  }
  var L = englishLang();
  // The link's language (?lang=): the server inlined its catalog as
  // AIPAGER_UI_FIRST and wrote the markup in it, so the first frame is
  // already in it, with no request. Display only: the API still names the
  // viewer's language once the page talks to it, and the page follows.
  var UI_FIRST = (typeof AIPAGER_UI_FIRST === "object" && AIPAGER_UI_FIRST) || null;
  if (UI_FIRST && UI_FIRST.cat && typeof UI_FIRST.cat.code === "string" &&
      UI_FIRST.cat.code !== "en") {
    L = langFrom(UI_FIRST.cat, String(UI_FIRST.v || ""));
  }

  // A number in the page's digits ("1.5" -> "۱٫۵", "45%" -> "۴۵٪" in
  // Persian), in the order aipager/i18n/core.py's Tr._digits uses. Never
  // for a name, a version, a path or code.
  function num(x) {
    var s = String(x);
    if (!L.digits) { return s; }
    var digits = L.digits;
    return s.replace(/\./g, L.decimal).replace(/,/g, L.group).replace(/%/g, L.percent)
      .replace(/[0-9]/g, function (d) { return digits.charAt(Number(d)); });
  }

  // {name} placeholders ({{ and }} are braces). A number goes in through
  // num(), a string as it is (isolated in a right-to-left page); a missing
  // value is "".
  function fill(pattern, params) {
    params = params || {};
    return String(pattern).replace(/\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}/g,
      function (m, name) {
        if (m === "{{") { return "{"; }
        if (m === "}}") { return "}"; }
        var v = params[name];
        if (v === null || v === undefined) { return ""; }
        if (typeof v === "number") { return num(v); }
        return L.rtl ? ISOLATE_OPEN + String(v) + ISOLATE_CLOSE : String(v);
      });
  }

  // The page's own text: the language's string, else its English, else "".
  function t(key, params) {
    var own = L.strings ? L.strings[key] : undefined;
    var pattern = typeof own === "string" ? own : UI_EN[key];
    return typeof pattern === "string" ? fill(pattern, params) : "";
  }

  // A counted string: the language's form for n (its CLDR category), else
  // the English form for n. {n} is the count.
  function tn(key, n, params) {
    var own = L.strings ? L.strings[key] : undefined;
    var form = own && typeof own === "object" ? own[pluralCategory(L.code, n)] : undefined;
    if (typeof form !== "string") {
      var en = UI_EN[key];
      form = en && typeof en === "object"
        ? (pluralCategory("en", n) === "one" ? en.one : en.other) : undefined;
    }
    if (typeof form !== "string") { return ""; }
    var all = { n: n };
    for (var k in params) {
      if (Object.prototype.hasOwnProperty.call(params, k)) { all[k] = params[k]; }
    }
    return fill(form, all);
  }

  // A list in the language's own separator (", " in English).
  function joinList(items) { return items.join(L.list); }

  // Server strings (reasons, details, hints, summaries) can still carry
  // an em dash from text aipager passes through (Claude's summaries, tool
  // text, error details); the page shows a plain hyphen.
  function plain(s) {
    if (s === null || s === undefined) { return ""; }
    return String(s).replace(/ \u2014 /g, " - ").replace(/\u2014/g, "-");
  }

  // One line icon from the page's inline sprite (see _shell.py). Page-owned
  // names only; never server data.
  function icon(name, cls) {
    return '<svg class="ic' + (cls ? " " + cls : "") + '" aria-hidden="true">' +
      '<use href="#i-' + name + '"></use></svg>';
  }

  // ---- connectivity (never a spinner-forever, never a raw fetch error) --

  // Both "New session" buttons open a form that needs the server, so
  // while offline or expired they are hidden (a dead end otherwise), and
  // the "+" steps aside while the empty state offers its own button.
  var unreachable = false;
  var gridEmpty = false;
  function syncNewButtons() {
    document.getElementById("empty-state").hidden = !gridEmpty || unreachable;
    document.getElementById("new-session-btn").hidden = unreachable || gridEmpty;
  }

  var connState = "";   // what the badge says, so a language switch can redraw it
  function setConnState(state) {
    connState = state;
    unreachable = state === "offline" || state === "expired";
    syncNewButtons();
    var badge = document.getElementById("conn-badge");
    var errorEl = document.getElementById("error");
    // Only a problem is worth a badge: while live there is none.
    badge.hidden = state === "live";
    setClass(badge, "conn conn-" + (state === "live" ? "live"
      : state === "reconnecting" ? "reconnecting" : "offline"));
    if (state === "live") {
      setText(badge, t("ui.conn.live"));
      errorEl.style.display = "none";
    } else if (state === "reconnecting") {
      setText(badge, t("ui.conn.reconnecting"));
      errorEl.style.display = "none";
    } else if (state === "offline") {
      clearGridSkeleton();
      badge.textContent = t("ui.conn.offline");
      showFatal(t("ui.conn.unreachable"));
    } else if (state === "expired") {
      clearGridSkeleton();
      badge.textContent = t("ui.conn.expired");
      showFatal(t("ui.conn.session_expired", { command: "/app" }));
    }
  }

  function showFatal(msg) {
    var el = document.getElementById("error");
    el.textContent = msg;
    el.style.display = "block";
  }

  // Transient note that clears itself - used for "not built yet" taps.
  // Deliberately its OWN element, not #error: sharing one would let a
  // 3.5s self-clearing notice wipe a fatal message that must stay put.
  // The concrete case: initData expires after 5 minutes with no refresh
  // path, so any page left open shows the fatal "reopen from /app"; the
  // very next tap on the New-session card would have erased it, leaving
  // the operator with no explanation.
  var noticeTimer = null;
  // kind: "ok", "err", or neutral. The icon is a DOM node, not CSS
  // `content:` (a CSS emoji was once mangled by Python's escapes), and
  // the message goes through textContent so server text is never markup.
  function showNotice(msg, kind) {
    var el = document.getElementById("notice");
    kind = kind === "ok" || kind === "err" ? kind : "info";
    el.textContent = "";
    var icon = document.createElement("span");
    icon.className = "toast-icon";
    icon.textContent = kind === "ok" ? "✓" : kind === "err" ? "!" : "i";
    var body = document.createElement("span");
    body.className = "toast-text";
    body.textContent = msg;
    el.appendChild(icon);
    el.appendChild(body);
    // Reset the kind classes without touching is-visible, so a second
    // notice arriving mid-fade cannot strand the toast in the wrong colour.
    el.classList.remove("toast-ok");
    el.classList.remove("toast-err");
    el.classList.remove("toast-info");
    el.classList.add("toast-" + kind);
    // A class, not inline display: toggling display would kill the fade
    // and bring back the layout shift.
    el.classList.add("is-visible");
    if (noticeTimer) { clearTimeout(noticeTimer); }
    noticeTimer = setTimeout(function () {
      el.classList.remove("is-visible");
      noticeTimer = null;
      // Text clears only after the fade-out.
      setTimeout(function () {
        if (!el.classList.contains("is-visible")) { el.textContent = ""; }
      }, 200);
    }, 3500);
  }

  function apiFetch(path) {
    return fetch(path, { headers: authHeaders() })
      .then(function (res) {
        if (res.status === 401 || res.status === 403) {
          var authErr = new Error("auth");
          authErr.authFailed = true;
          throw authErr;
        }
        if (!res.ok) {
          var httpErr = new Error("HTTP " + res.status);
          httpErr.httpStatus = res.status;
          throw httpErr;
        }
        return res.json();
      });
  }

  // 401/403 cannot succeed on retry (the SDK cannot mint fresh
  // initData), so both are one terminal state. Anything else is
  // transient and keeps polling.
  function handleFetchError(err) {
    // Once expired, the fatal message is the only thing worth showing.
    // Without this, a later non-auth failure (a tunnel 502, a DNS blip)
    // on a fetch that skipped the authExpired gate - loadDiffIfNeeded is
    // one - would route into setConnState and either hide the fatal
    // message ("reconnecting" clears #error) or overwrite it with the
    // wrong explanation ("offline"). Same class as the notice/fatal
    // clobbering fixed above, reached by a narrower path.
    if (authExpired) { return; }
    if (err && err.authFailed) {
      authExpired = true;
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      setConnState("expired");
      return;
    }
    consecutiveFailures += 1;
    if (lastSuccessAt === null) {
      setConnState("offline");
    } else {
      setConnState(consecutiveFailures >= 3 ? "offline" : "reconnecting");
    }
  }

  // ---- haptics ----------------------------------------------------------

  // Every call feature-checked: older Telegram clients lack some of these.
  function haptic(kind, arg) {
    var h = tg && tg.HapticFeedback;
    if (!h) { return; }
    try {
      if (kind === "impact" && typeof h.impactOccurred === "function") {
        h.impactOccurred(arg);
      } else if (kind === "select" && typeof h.selectionChanged === "function") {
        h.selectionChanged();
      } else if (kind === "notify" && typeof h.notificationOccurred === "function") {
        h.notificationOccurred(arg);
      }
    } catch (e) { /* older client */ }
  }

  function pulseForStatusChanges(sessions) {
    if (!tg || !tg.HapticFeedback) { return; }
    sessions.forEach(function (s) {
      var prev = lastStatuses[s.label];
      if (prev !== undefined && prev !== s.status) {
        try {
          tg.HapticFeedback.notificationOccurred(s.status === "waiting" ? "warning" : "success");
        } catch (e) {
          // Older Telegram client without this API - no-op.
        }
      }
      lastStatuses[s.label] = s.status;
    });
  }

  // ---- grid ---------------------------------------------------------

  // Rows arrive sorted by the server (sessions.sort_for_display).

  // "2m ago", re-rendered on a timer so it ages between polls.
  function formatAge(seconds) {
    if (seconds === null || seconds === undefined) { return ""; }
    if (seconds < 60) { return t("ui.age.seconds", { n: Math.max(0, Math.round(seconds)) }); }
    if (seconds < 3600) { return t("ui.age.minutes", { n: Math.floor(seconds / 60) }); }
    if (seconds < 86400) { return t("ui.age.hours", { n: Math.floor(seconds / 3600) }); }
    return t("ui.age.days", { n: Math.floor(seconds / 86400) });
  }

  // Wall-clock reading for when the last payload arrived, so ticking can
  // add elapsed time to the server's own seconds-ago figure.
  var lastPayloadAt = 0;

  function ageFor(s) {
    if (s.last_active_seconds_ago === null || s.last_active_seconds_ago === undefined) {
      return "";
    }
    var drift = lastPayloadAt ? (Date.now() - lastPayloadAt) / 1000 : 0;
    return formatAge(s.last_active_seconds_ago + drift);
  }

  // ---- the lanterns: keyed rendering ----------------------------------
  // Three label -> node maps: beacons (waiting), tiles (live) and the
  // shelf (finished). A poll writes only what changed, so an unchanged
  // poll touches nothing and a tile never flickers or jumps.

  // Keyed by the status id: the word is shown, the id decides.
  var STATE_WORDS = {
    busy: "ui.state.busy", idle: "ui.state.idle", waiting: "ui.state.waiting",
    gone: "ui.state.gone", unknown: "ui.state.unknown"
  };

  function stateWord(status) {
    return STATE_WORDS[status] ? t(STATE_WORDS[status]) : String(status || "");
  }

  function money(v) { return num("$" + (Number(v) || 0).toFixed(2)); }

  function pctOf(s) {
    var p = Math.round(Number(s && s.context_pct) || 0);
    return Math.max(0, Math.min(100, p));
  }

  // Each writes only when the value differs.
  function setText(node, text) { if (node.textContent !== text) { node.textContent = text; } }
  function setClass(node, cls) { if (node.className !== cls) { node.className = cls; } }
  function setAttr(node, key, value) {
    if (node.getAttribute(key) !== value) { node.setAttribute(key, value); }
  }
  function setHtml(node, html) { if (node.innerHTML !== html) { node.innerHTML = html; } }

  function make(tag, cls) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (tag === "button") { node.type = "button"; }
    return node;
  }

  // The enter animation plays once, when the node is new. It is a class so
  // a later re-append (a reorder) does not replay it.
  function markNew(node) {
    node.classList.add("is-new");
    node.addEventListener("animationend", function () { node.classList.remove("is-new"); });
  }

  function openFromGrid(label) {
    haptic("impact", "light");
    openDetail(label);
  }

  function buildTile(s) {
    var tile = { el: make("button", "tile"), sig: "" };
    var top = make("span", "tile-top");
    tile.lamp = make("span", "lamp");
    // The ring's number and its percent sign (in the language's sign).
    tile.num = make("span", "lamp-num");
    tile.numText = make("span");
    tile.pct = make("span", "lamp-pct");
    tile.num.appendChild(tile.numText);
    tile.num.appendChild(tile.pct);
    tile.lamp.appendChild(tile.num);
    var side = make("span", "tile-side");
    tile.state = make("span", "tile-state");
    tile.age = make("span", "tile-age");
    tile.age.setAttribute("data-age-for", s.label);
    side.appendChild(tile.state);
    side.appendChild(tile.age);
    top.appendChild(tile.lamp);
    top.appendChild(side);
    // Names, model ids and folders read left to right in any language.
    tile.label = make("span", "tile-label");
    tile.label.setAttribute("dir", "ltr");
    tile.project = make("span", "tile-project");
    tile.project.setAttribute("dir", "auto");
    tile.meta = make("span", "tile-meta");
    tile.meta.setAttribute("dir", "ltr");
    tile.el.appendChild(top);
    tile.el.appendChild(tile.label);
    tile.el.appendChild(tile.project);
    tile.el.appendChild(tile.meta);
    var label = s.label;
    tile.el.addEventListener("click", function () { openFromGrid(label); });
    updateTile(tile, s);
    markNew(tile.el);
    return tile;
  }

  function updateTile(tile, s) {
    var pct = pctOf(s);
    var sig = [s.label, s.status, pct, s.model, s.cost_usd, s.project].join("|");
    if (tile.sig === sig) { return; }
    tile.sig = sig;
    var word = stateWord(s.status);
    setClass(tile.el, "tile tile-" + s.status);
    setClass(tile.lamp, "lamp " + (s.status === "busy" ? "lamp-work" : "lamp-rest"));
    setAttr(tile.lamp, "style", "--pct:" + pct);
    setText(tile.numText, num(pct));
    setText(tile.pct, L.percent);
    setText(tile.state, word);
    setText(tile.label, s.label);
    setText(tile.project, s.project || t("ui.tile.no_folder"));
    setText(tile.meta, (s.model ? s.model + " · " : "") + money(s.cost_usd));
    setAttr(tile.el, "aria-label", s.model
      ? t("ui.tile.aria", { session: s.label, state: word, pct: pct, model: s.model,
                            cost: money(s.cost_usd) })
      : t("ui.tile.aria_no_model", { session: s.label, state: word, pct: pct,
                                     cost: money(s.cost_usd) }));
  }

  function buildBeacon(s) {
    var b = { el: make("div", "beacon"), sig: "" };
    var body = make("button", "beacon-body");
    b.lamp = make("span", "lamp lamp-need");
    var text = make("span", "beacon-text");
    var top = make("span", "beacon-top");
    b.label = make("span", "beacon-label");
    b.label.setAttribute("dir", "ltr");
    b.kind = make("span", "kind-chip");
    b.age = make("span", "beacon-age");
    b.age.setAttribute("data-age-for", s.label);
    top.appendChild(b.label);
    top.appendChild(b.kind);
    top.appendChild(b.age);
    b.summary = make("span", "beacon-summary");
    b.summary.setAttribute("dir", "auto");   // Claude's or a tool's own words
    text.appendChild(top);
    text.appendChild(b.summary);
    body.appendChild(b.lamp);
    body.appendChild(text);
    var actions = make("div", "beacon-actions");
    b.answer = make("button", "primary btn-need answer-btn");
    b.answer.innerHTML = icon("chat");
    var answerText = make("span");
    answerText.textContent = t("ui.btn.answer_in_chat");
    b.answer.appendChild(answerText);
    b.open = make("button", "icon-btn");
    b.open.innerHTML = icon("open");
    b.open.setAttribute("aria-label", t("ui.beacon.open_aria", { session: s.label }));
    actions.appendChild(b.answer);
    actions.appendChild(b.open);
    b.note = make("div", "note");
    b.note.hidden = true;
    b.el.appendChild(body);
    b.el.appendChild(actions);
    b.el.appendChild(b.note);
    var label = s.label;
    body.addEventListener("click", function () { openFromGrid(label); });
    b.open.addEventListener("click", function () { openFromGrid(label); });
    b.answer.addEventListener("click", function () { answerPrompt(label); });
    updateBeacon(b, s);
    markNew(b.el);
    return b;
  }

  function updateBeacon(b, s) {
    var sig = [s.label, s.waiting_kind, s.waiting_summary, gridCanAct ? 1 : 0].join("|");
    if (b.sig === sig) { return; }
    b.sig = sig;
    var question = s.waiting_kind === "question";
    setHtml(b.lamp, icon(question ? "question" : "lock"));
    setText(b.label, s.label);
    setText(b.kind, t(question ? "ui.kind.question" : "ui.kind.permission"));
    setText(b.summary, plain(s.waiting_summary) ||
      t(question ? "ui.beacon.asking" : "ui.beacon.wants_tool"));
    b.answer.disabled = !gridCanAct || !!answering[s.label];
    setText(b.note, gridCanAct ? "" : t(NO_ANSWER_TEXT));
    b.note.hidden = gridCanAct;
  }

  function buildShelfRow(s) {
    var r = { el: make("button", "shelf-row"), sig: "" };
    r.lamp = make("span", "lamp lamp-out lamp-sm");
    r.lamp.innerHTML = icon("lantern");
    var text = make("span", "shelf-text");
    r.label = make("span", "shelf-label");
    r.label.setAttribute("dir", "ltr");
    r.meta = make("span", "shelf-meta");
    r.meta.setAttribute("dir", "auto");   // folder and model, or a state word
    text.appendChild(r.label);
    text.appendChild(r.meta);
    r.age = make("span", "shelf-age");
    r.age.setAttribute("data-age-for", s.label);
    r.el.appendChild(r.lamp);
    r.el.appendChild(text);
    r.el.appendChild(r.age);
    var label = s.label;
    r.el.addEventListener("click", function () { openFromGrid(label); });
    updateShelfRow(r, s);
    return r;
  }

  function updateShelfRow(r, s) {
    var sig = [s.label, s.project, s.model, s.cost_usd].join("|");
    if (r.sig === sig) { return; }
    r.sig = sig;
    setText(r.label, s.label);
    setText(r.meta, [s.project, s.model, s.cost_usd ? money(s.cost_usd) : ""]
      .filter(function (x) { return !!x; }).join(" · ") || stateWord("gone"));
  }

  // FLIP only when every API exists and reduced motion is off; one
  // requestAnimationFrame per reorder.
  function canFlip(sample) {
    var w = window;
    if (!sample || typeof sample.getBoundingClientRect !== "function") { return false; }
    if (typeof w.requestAnimationFrame !== "function" || typeof w.matchMedia !== "function") {
      return false;
    }
    try {
      return !w.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (e) {
      return false;
    }
  }

  function sameOrder(host, nodes) {
    var kids = host.children;
    if (!kids || kids.length !== nodes.length) { return false; }
    for (var i = 0; i < nodes.length; i++) {
      if (kids[i] !== nodes[i]) { return false; }
    }
    return true;
  }

  function placeChildren(host, nodes, animate) {
    if (sameOrder(host, nodes)) { return; }
    var flip = animate && canFlip(nodes[0]);
    var before = [];
    if (flip) {
      nodes.forEach(function (n) {
        before.push(n.parentNode === host ? n.getBoundingClientRect() : null);
      });
    }
    nodes.forEach(function (n) { host.appendChild(n); });
    if (!flip) { return; }
    var moved = [];
    nodes.forEach(function (n, i) {
      var a = before[i];
      if (!a) { return; }
      var b = n.getBoundingClientRect();
      var dx = a.left - b.left;
      var dy = a.top - b.top;
      if (!dx && !dy) { return; }
      n.style.transition = "none";
      n.style.transform = "translate(" + dx + "px, " + dy + "px)";
      moved.push(n);
    });
    if (!moved.length) { return; }
    moved[0].getBoundingClientRect();     // commit the inverted positions
    window.requestAnimationFrame(function () {
      moved.forEach(function (n) {
        n.style.transition = "";
        n.style.transform = "";
      });
    });
  }

  // Update what exists, build what is new, drop what left, then fix the
  // order (only if it actually changed).
  function syncBucket(host, map, rows, build, update, animate) {
    var keep = Object.create(null);
    var nodes = [];
    rows.forEach(function (s) {
      keep[s.label] = true;
      var item = map[s.label];
      if (item) { update(item, s); } else { item = map[s.label] = build(s); }
      setText(item.age, ageFor(s));
      nodes.push(item.el);
    });
    Object.keys(map).forEach(function (label) {
      if (keep[label]) { return; }
      var node = map[label].el;
      if (node.parentNode) { node.parentNode.removeChild(node); }
      delete map[label];
    });
    placeChildren(host, nodes, animate);
  }

  // "1 needs you, 2 working" over "$4.21 spent · 2 finished".
  function renderPulse(sessions, totals) {
    var host = document.getElementById("grid-totals");
    var count = { waiting: 0, busy: 0, idle: 0, other: 0 };
    sessions.forEach(function (s) {
      if (s.status === "gone") { return; }
      if (count[s.status] !== undefined && s.status !== "other") { count[s.status] += 1; } else { count.other += 1; }
    });
    var clauses = [];
    if (count.waiting) { clauses.push(["pulse-need", tn("ui.pulse.need", count.waiting)]); }
    if (count.busy) { clauses.push(["pulse-work", tn("ui.pulse.working", count.busy)]); }
    if (count.idle) { clauses.push(["pulse-rest", tn("ui.pulse.resting", count.idle)]); }
    if (count.other) { clauses.push(["pulse-rest", tn("ui.pulse.unknown", count.other)]); }
    if (!clauses.length) {
      clauses.push(["pulse-rest", sessions.length ? t("ui.pulse.all_quiet") : ""]);
    }
    var sub = [t("ui.pulse.spent", { cost: money(totals.cost_usd) })];
    if (totals.gone) { sub.push(tn("ui.pulse.finished", totals.gone)); }
    var key = JSON.stringify([clauses, sub, sessions.length]);
    if (host.getAttribute("data-key") === key) { return; }
    host.setAttribute("data-key", key);
    host.innerHTML = "";
    if (!sessions.length) { return; }
    var main = make("span", "pulse-main");
    clauses.forEach(function (c, i) {
      if (i) {
        var sep = make("span");
        sep.textContent = L.list;
        main.appendChild(sep);
      }
      var part = make("span", c[0]);
      part.textContent = c[1];
      main.appendChild(part);
    });
    var line = make("span", "pulse-sub");
    line.textContent = sub.join(" · ");
    host.appendChild(main);
    host.appendChild(line);
  }

  var goneCollapsed = true;
  var lastGridData = null;   // so the gone-toggle can re-render without a fetch
  var gridSig = null;        // what the grid on screen was built from
  var gridCanAct = true;     // may this caller answer prompts (GET /api/sessions can_act)
  var gridSkeleton = false;  // the first-load placeholders are up
  var tileMap = Object.create(null);
  var beaconMap = Object.create(null);
  var shelfMap = Object.create(null);

  // Everything the grid shows except the ages, which tickAges re-stamps,
  // and the uptime, which only the daemon line shows.
  function gridSignature(data) {
    var rows = (data.sessions || []).map(function (s) {
      return [s.label, s.status, s.waiting_kind, s.waiting_summary, s.model,
              pctOf(s), s.cost_usd, s.project].join("|");
    });
    var t = data.totals || {};
    return JSON.stringify([rows, t.cost_usd, data.can_act === false, goneCollapsed]);
  }

  function showGridSkeleton() {
    var host = document.getElementById("sessions");
    host.innerHTML = "";
    // Shaped like a tile: a ring, a name, a caption.
    for (var i = 0; i < 4; i++) {
      var tile = make("div", "skel skel-tile");
      tile.appendChild(make("span", "skel-ring"));
      tile.appendChild(make("span", "skel-bar w70"));
      tile.appendChild(make("span", "skel-bar w40"));
      host.appendChild(tile);
    }
    skeleton(document.getElementById("grid-totals"), 1);
    gridSkeleton = true;
  }

  // A first load that fails leaves nothing to wait for: drop the shapes.
  function clearGridSkeleton() {
    if (!gridSkeleton) { return; }
    document.getElementById("sessions").innerHTML = "";
    document.getElementById("grid-totals").innerHTML = "";
    document.getElementById("daemon-line").textContent = "";
    gridSkeleton = false;
  }

  function renderGrid(data) {
    lastGridData = data;
    var d = data.daemon || {};
    var daemonLine = document.getElementById("daemon-line");
    setAttr(daemonLine, "dir", "ltr");   // a bot name and a version
    setText(daemonLine,
      "@" + (d.bot_username || "?") + " · v" + (d.version || "?") + " · " +
      t("ui.daemon.uptime", { n: Math.floor((d.uptime_seconds || 0) / 60) }));

    var sessions = data.sessions || [];
    var totals = data.totals || {};
    lastPayloadAt = Date.now();
    lastSessionsByLabel = Object.create(null);
    sessions.forEach(function (s) { lastSessionsByLabel[s.label] = s; });
    pulseForStatusChanges(sessions);

    var sig = gridSignature(data);
    if (sig === gridSig) { return; }     // nothing on screen would change
    gridSig = sig;
    gridCanAct = data.can_act !== false;

    if (gridSkeleton) {
      document.getElementById("sessions").innerHTML = "";
      gridSkeleton = false;
    }

    // Waiting count rides the Sessions tab so the one state that costs
    // the operator time is visible without reading the grid.
    var badge = document.getElementById("waiting-badge");
    if (totals.waiting) {
      setText(badge, num(totals.waiting));
      badge.hidden = false;
    } else {
      badge.hidden = true;
    }
    renderPulse(sessions, totals);

    var waiting = [];
    var live = [];
    var gone = [];
    sessions.forEach(function (s) {
      if (s.status === "waiting") { waiting.push(s); } else if (s.status === "gone") { gone.push(s); } else { live.push(s); }
    });

    syncBucket(document.getElementById("needs-you-list"), beaconMap, waiting,
               buildBeacon, updateBeacon, false);
    document.getElementById("needs-you").hidden = waiting.length === 0;
    syncBucket(document.getElementById("sessions"), tileMap, live,
               buildTile, updateTile, true);
    gridEmpty = sessions.length === 0;
    syncNewButtons();

    var wrap = document.getElementById("gone-wrap");
    var goneEl = document.getElementById("sessions-gone");
    var toggle = document.getElementById("gone-toggle");
    wrap.hidden = gone.length === 0;
    syncBucket(goneEl, shelfMap, gone, buildShelfRow, updateShelfRow, false);
    if (gone.length) {
      setDisclosure(toggle, tn("ui.grid.finished_n", gone.length), !goneCollapsed);
      goneEl.hidden = goneCollapsed;
    }
  }

  // Re-stamp only the age labels.
  function tickAges() {
    var nodes = document.querySelectorAll("[data-age-for]");
    for (var i = 0; i < nodes.length; i++) {
      var s = lastSessionsByLabel[nodes[i].getAttribute("data-age-for")];
      if (s) { setText(nodes[i], ageFor(s)); }
    }
  }

  // ---- Answer in chat (8.31 parity) ------------------------------------
  // Re-sends the pending prompt into the chat through the pinned bar's
  // path. postSessionAction, never apiFetch: a 403 here means "you
  // can't answer prompts", not "your session expired".
  var NO_ANSWER_TEXT = "ui.answer.cannot";
  var answering = Object.create(null);   // label -> a request is in flight

  function waitingCount() {
    var rows = (lastGridData && lastGridData.sessions) || [];
    return rows.filter(function (s) { return s.status === "waiting"; }).length;
  }

  function setAnswerBusy(label, busy) {
    syncMainButton();
    var b = beaconMap[label];
    if (b) { b.answer.disabled = busy || !gridCanAct; }
    var detailBtn = document.getElementById("detail-answer");
    if (currentView.label === label) {
      detailBtn.disabled = busy || !(lastDetailData && lastDetailData.answer &&
                                     lastDetailData.answer.available);
    }
  }

  function answerPrompt(label) {
    if (answering[label]) { return; }     // one request per tap, never two
    answering[label] = true;
    setAnswerBusy(label, true);
    var others = waitingCount();
    postSessionAction(label, "answer", "POST").then(function (r) {
      answering[label] = false;
      setAnswerBusy(label, false);
      if (r.status === 200) {
        haptic("notify", "success");
        // The Mini App is a sheet over the chat: closing it shows the
        // prompt just posted, one tap from its buttons. With other
        // sessions still waiting, closing would strand them, so stay.
        if (others <= 1 && tg && typeof tg.close === "function") {
          try { tg.close(); return; } catch (e) { /* stay open */ }
        }
        showNotice(t("ui.answer.sent"), "ok");
        pollTick();
        return;
      }
      if (r.status === 403) {
        showNotice(t(NO_ANSWER_TEXT), "err");
        return;
      }
      showNotice(plain((r.data && r.data.detail) || t("ui.answer.send_failed")), "err");
      pollTick();
    }).catch(function () {
      answering[label] = false;
      setAnswerBusy(label, false);
      showNotice(t("ui.answer.unreachable"), "err");
    });
  }

  // ---- drill-down: timeline ------------------------------------------

  // The clock unit of the session whose timeline is shown (its busy card's).
  var timelineUnit = "s";

  function renderTimelineRow(row) {
    if (row.kind === "commentary") {
      return '<div class="timeline-row timeline-commentary" dir="auto">' +
        escapeHtml(row.text) + '</div>';
    }
    var elapsed = (row.state === "running" && typeof row.elapsed_seconds === "number")
      ? " (" + clockOf(row.elapsed_seconds, timelineUnit) + ")" : "";
    return (
      '<div class="timeline-row timeline-tool state-' + escapeHtml(row.state) + '" dir="ltr">' +
        escapeHtml(row.text) + escapeHtml(elapsed) +
      '</div>'
    );
  }

  function renderTimeline(rows, unit) {
    timelineUnit = unit || "s";
    var panel = document.getElementById("panel-timeline");
    if (!rows || rows.length === 0) {
      panel.innerHTML = '<div class="muted">' + escapeHtml(t("ui.timeline.empty")) + '</div>';
      return;
    }
    // Only auto-scroll if the reader was already at the bottom - never
    // yank the view out from under someone reading an earlier row.
    var wasAtBottom = (panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 4);
    panel.innerHTML = rows.map(renderTimelineRow).join("");
    if (wasAtBottom) { panel.scrollTop = panel.scrollHeight; }
  }

  // ---- session page: each section re-renders only when its slice changed
  var detailSig = Object.create(null);

  function detailChanged(key, value) {
    var sig = JSON.stringify(value === undefined ? null : value);
    if (detailSig[key] === sig) { return false; }
    detailSig[key] = sig;
    return true;
  }

  // clock: port of aipager/clockfmt.py
  // "0:45", "4:10", "1:23:00": m:ss under an hour, h:mm:ss after. At the
  // busy card's unit "m" or "h" (a turn past 10 min) it moves in 30 s
  // steps, as the card does. Line for line the Python one; a test runs
  // both over the same inputs.
  var CLOCK_STEP_SECONDS = 30;
  function clockText(seconds, unit) {
    var value = Number(seconds);
    if (!isFinite(value) || value < 0) { value = 0; }
    var total = Math.floor(value);
    if (unit === "m" || unit === "h") { total -= total % CLOCK_STEP_SECONDS; }
    var hours = Math.floor(total / 3600);
    var rest = total % 3600;
    var minutes = Math.floor(rest / 60);
    var secs = rest % 60;
    var ss = (secs < 10 ? "0" : "") + secs;
    if (hours) { return hours + ":" + (minutes < 10 ? "0" : "") + minutes + ":" + ss; }
    return minutes + ":" + ss;
  }
  // end clock

  // The clock in the page's digits (the port above stays Latin, line for
  // line the Python one).
  function clockOf(seconds, unit) { return num(clockText(seconds, unit)); }

  // plurals: port of aipager/i18n/plurals.py
  // The CLDR plural category of a whole number, line for line the Python
  // rules (a test runs both over the same counts). A negative count is
  // measured by its size; an unknown code follows English.
  function pluralCategory(code, n) {
    n = Math.abs(Math.trunc(Number(n) || 0));
    var mod10 = n % 10;
    var mod100 = n % 100;
    var millions = n !== 0 && n % 1000000 === 0;
    switch (code) {
      case "fa":
        return (n === 0 || n === 1) ? "one" : "other";
      case "es": case "it":
        return n === 1 ? "one" : (millions ? "many" : "other");
      case "fr": case "pt-BR":
        return (n === 0 || n === 1) ? "one" : (millions ? "many" : "other");
      case "ru": case "uk":
        if (mod10 === 1 && mod100 !== 11) { return "one"; }
        if (mod10 >= 2 && mod10 <= 4 && !(mod100 >= 12 && mod100 <= 14)) { return "few"; }
        return "many";
      case "ar":
        if (n === 0) { return "zero"; }
        if (n === 1) { return "one"; }
        if (n === 2) { return "two"; }
        if (mod100 >= 3 && mod100 <= 10) { return "few"; }
        if (mod100 >= 11 && mod100 <= 99) { return "many"; }
        return "other";
      case "zh-Hans": case "ko": case "id": case "vi":
        return "other";
      default:
        return n === 1 ? "one" : "other";
    }
  }
  // end plurals

  // What a session waits on, by its id; an id this page has no word for
  // shows as it is.
  var KIND_WORDS = { permission: "ui.kind_word.permission", question: "ui.kind_word.question" };

  function stateLine(d) {
    if (d.status === "busy") {
      return typeof d.busy_elapsed_seconds === "number"
        ? t("ui.state_line.working_for",
            { clock: clockOf(d.busy_elapsed_seconds, d.busy_elapsed_unit) })
        : t("ui.state_line.working");
    }
    if (d.status === "waiting") {
      return d.waiting_kind
        ? t("ui.state_line.needs_you_kind", {
          kind: KIND_WORDS[d.waiting_kind] ? t(KIND_WORDS[d.waiting_kind]) : d.waiting_kind })
        : t("ui.state_line.needs_you");
    }
    if (d.status === "idle") {
      var age = ageFor(d);
      return age ? t("ui.state_line.resting_since", { age: age }) : t("ui.state_line.resting");
    }
    if (d.status === "gone") { return t("ui.state_line.finished"); }
    return t("ui.state_line.unknown");
  }

  // The header ring: context as fuel, unlit once finished.
  function paintRing(status, pct) {
    var ring = document.getElementById("detail-ring");
    var lamp = status === "busy" ? "lamp-work" : status === "waiting" ? "lamp-need"
      : status === "gone" ? "lamp-out" : "lamp-rest";
    setClass(ring, "lamp lamp-lg " + lamp);
    if (status === "gone") {
      setAttr(ring, "style", "--pct:0");
      setHtml(ring, icon("lantern"));
    } else {
      setAttr(ring, "style", "--pct:" + pct);
      // The number and its percent sign, both in the page's language.
      setHtml(ring, '<span class="lamp-num">' + escapeHtml(num(pct)) +
        '<span class="lamp-pct">' + escapeHtml(L.percent) + "</span></span>");
    }
  }

  function paintHeader(d) {
    setText(document.getElementById("detail-label"), d.label || "");
    var statusEl = document.getElementById("detail-status");
    setClass(statusEl, statusClass(d.status));
    setText(statusEl, stateWord(d.status));
    setText(document.getElementById("detail-state"), stateLine(d));
    paintRing(d.status, pctOf(d));
  }

  // Stop while Claude works or waits, Resume once finished: the menu's
  // own request, no confirmation.
  function renderQuick(data) {
    var btn = document.getElementById("detail-quick");
    var a = data.actions || {};
    var key = (a.stop && a.stop.available) ? "stop"
      : (a.resume && a.resume.available) ? "resume" : "";
    btn.hidden = !key;
    if (!key) { return; }
    setAttr(btn, "data-action", key);
    setClass(btn, "pill" + (key === "resume" ? " pill-resume" : ""));
    btn.innerHTML = icon(key);
    var text = make("span");
    text.textContent = t(ACTION_TITLES[key]);
    btn.appendChild(text);
  }

  // The last tool calls as beads: done, failed or running.
  function renderActivity(data) {
    var btn = document.getElementById("detail-activity");
    var tools = (data.timeline || []).filter(function (r) { return r.kind === "tool"; });
    var recent = tools.slice(-8);
    var chips = [];
    if (data.queue_depth) { chips.push(tn("ui.activity.queued", data.queue_depth)); }
    if (data.status === "busy" && typeof data.busy_elapsed_seconds === "number") {
      chips.push(clockOf(data.busy_elapsed_seconds, data.busy_elapsed_unit));
    }
    if (!recent.length && !chips.length) { btn.hidden = true; btn.innerHTML = ""; return; }
    btn.hidden = false;
    btn.innerHTML = "";
    var head = make("span", "act-head");     // spans: a <button> takes phrasing content only
    var title = make("span", "act-title");
    title.textContent = t(recent.length ? "ui.activity.recent" : "ui.activity.title");
    head.appendChild(title);
    var chipRow = make("span", "act-chips");
    chips.forEach(function (c) {
      var chip = make("span", "act-chip");
      chip.textContent = c;
      chipRow.appendChild(chip);
    });
    head.appendChild(chipRow);
    btn.appendChild(head);
    if (!recent.length) { return; }
    var beads = make("span", "beads");
    recent.forEach(function (r) {
      var state = r.state === "failed" ? "failed" : r.state === "running" ? "running" : "done";
      var bead = make("span", "bead bead-" + state);
      bead.setAttribute("title", r.text || "");
      beads.appendChild(bead);
    });
    btn.appendChild(beads);
    var last = recent[recent.length - 1];
    var line = make("span", "act-last");
    var tool = last.text || "";
    line.textContent = last.state === "running"
      ? (typeof last.elapsed_seconds === "number"
        ? t("ui.activity.running_for",
            { tool: tool, clock: clockOf(last.elapsed_seconds, data.busy_elapsed_unit) })
        : t("ui.activity.running", { tool: tool }))
      : last.state === "failed" ? t("ui.activity.failed", { tool: tool }) : tool;
    btn.appendChild(line);
    btn.setAttribute("aria-label", tn("ui.activity.aria", recent.length));
  }

  // The reply with `code` spans; escaped first, so nothing becomes markup.
  function renderPreview(data) {
    var prev = document.getElementById("detail-preview");
    if (data.last_message) {
      setClass(prev, "preview");
      prev.innerHTML = escapeHtml(data.last_message)
        .replace(/`([^`\n]+)`/g, "<code>$1</code>");
    } else {
      setClass(prev, "preview is-empty");
      prev.textContent = t(data.status === "gone"
        ? "ui.preview.nothing_gone" : "ui.preview.nothing_yet");
    }
  }

  function renderDetailData(data) {
    lastDetailData = data;
    paintHeader(data);
    if (detailChanged("quick", data.actions)) { renderQuick(data); }

    // What it is blocked on: the reason the operator opened the page.
    var waitEl = document.getElementById("detail-waiting");
    if (data.status === "waiting") {
      var isQuestion = data.waiting_kind === "question";
      document.getElementById("detail-waiting-lamp").innerHTML =
        icon(isQuestion ? "question" : "lock");
      document.getElementById("detail-waiting-kind").textContent =
        t(isQuestion ? "ui.waiting.asking" : "ui.waiting.permission");
      document.getElementById("detail-waiting-text").textContent = data.waiting_summary
        ? plain(data.waiting_summary)
        : t(isQuestion ? "ui.waiting.question_open" : "ui.waiting.permission_open");
      var answer = data.answer;
      var answerBtn = document.getElementById("detail-answer");
      var answerNote = document.getElementById("detail-answer-note");
      answerBtn.hidden = !answer;
      answerBtn.disabled = !answer || !answer.available || !!answering[data.label];
      var why = answer && !answer.available ? plain(answer.reason || t(NO_ANSWER_TEXT)) : "";
      answerNote.textContent = why;
      answerNote.hidden = !why;
      waitEl.hidden = false;
    } else {
      waitEl.hidden = true;
    }

    // `facts` is built server-side (sessions.display_facts) and already
    // omits what would be noise - a finished session has no model, cost
    // or context to report, and "0% ctx · $0.00" reads like a fault.
    if (detailChanged("facts", data.facts)) { renderFacts(data.facts || []); }
    if (detailChanged("preview", [data.last_message, data.status === "gone"])) {
      renderPreview(data);
    }
    if (detailChanged("activity", [data.timeline, data.queue_depth, data.status,
                                   data.busy_elapsed_seconds])) {
      renderActivity(data);
    }

    renderDetailActions(data);     // every poll: it guards an open menu
    if (detailChanged("model", [data.label, data.model, data.model_switch, modelSwitch,
                                !!openGroups["session-model"],
                                modelChoices ? modelChoices.length : -1])) {
      renderModelControl(data);
    }
    if (detailChanged("timeline", data.timeline)) { renderTimeline(data.timeline, data.busy_elapsed_unit); }
    updateSectionHeaders(data);
    syncMainButton();
  }

  // Facts grid: the directory takes a whole row; an odd one out widens.
  // Decided by the fact's id, never by its (translated) label.
  function renderFacts(facts) {
    var dl = document.getElementById("detail-facts");
    dl.innerHTML = "";
    var narrow = facts.filter(function (f) { return f.id !== "cwd"; }).length;
    var seen = 0;
    facts.forEach(function (fact) {
      var isDir = fact.id === "cwd";
      if (!isDir) { seen += 1; }
      var wide = isDir || (narrow % 2 === 1 && seen === narrow);
      var cell = document.createElement("div");
      cell.className = "fact" + (wide ? " fact-wide" : "") + (isDir ? " fact-mono" : "");
      var dt = document.createElement("dt");
      dt.textContent = fact.label;
      var dd = document.createElement("dd");
      dd.textContent = fact.value;
      // A model id and a folder are names: left to right in any language.
      if (fact.id === "cwd" || fact.id === "model") { dd.setAttribute("dir", "ltr"); }
      cell.appendChild(dt);
      cell.appendChild(dd);
      dl.appendChild(cell);
    });
  }

  // ---- running-session model picker (roadmap 8.35) ---------------------
  // Rows come from /api/session-options (the list the chat uses). The
  // server holds the POST open until the statusline reports the new
  // model or gives up: exactly how long "switching…" shows.
  var modelChoices = null;        // [{label, hint}] once fetched
  var modelChoicesLoading = false;
  // {label, previous, text, pending} for the switch in flight, keyed by
  // label so it never paints on another session's page.
  var modelSwitch = null;
  // Keys: the text is looked up when shown, so it follows a language switch.
  var MODEL_SWITCHING_TEXT = "ui.model.switching";
  var MODEL_UNCONFIRMED_TEXT = "ui.model.unconfirmed";

  function loadModelChoices() {
    if (modelChoices || modelChoicesLoading) { return; }
    modelChoicesLoading = true;
    apiFetch("/api/session-options").then(function (data) {
      modelChoices = (data && data.models) || [];
      modelChoicesLoading = false;
      if (lastDetailData) { renderModelControl(lastDetailData); }
    }).catch(function () {
      modelChoicesLoading = false;
    });
  }

  function renderModelControl(data) {
    var host = document.getElementById("detail-model");
    var note = document.getElementById("detail-model-note");
    host.innerHTML = "";
    var state = data && data.model_switch;
    if (!state) {
      host.hidden = true;
      note.hidden = true;
      return;
    }
    host.hidden = false;
    loadModelChoices();
    var sw = (modelSwitch && modelSwitch.label === data.label) ? modelSwitch : null;
    // An unconfirmed switch settles as soon as a later poll shows the
    // model really changed (the operator answered Claude Code's own
    // confirmation in the terminal, say).
    if (sw && !sw.pending && data.model && data.model !== sw.previous) {
      modelSwitch = null;
      sw = null;
    }
    var options = (modelChoices || []).map(function (m) {
      return { value: m.label, label: m.label, help: plain(m.hint || "") };
    });
    renderOptionGroup(host, {
      key: "session-model",
      title: t("ui.model.title"),
      options: options,
      current: null,
      ltr: true,
      valueText: sw ? t(sw.text) : (data.model || "-"),
      disabled: !state.available || !!(sw && sw.pending),
      onPick: function (value) {
        switchSessionModel(data.label, value, data.model || "");
      },
      rerender: function () { renderModelControl(lastDetailData || data); }
    });
    var reason = state.available ? "" : plain(state.reason || "");
    note.textContent = reason;
    note.hidden = !reason;
  }

  function switchSessionModel(label, model, previous) {
    openGroups["session-model"] = false;
    modelSwitch = { label: label, previous: previous,
                    text: MODEL_SWITCHING_TEXT, pending: true };
    if (lastDetailData) { renderModelControl(lastDetailData); }
    fetch("/api/sessions/" + encodeURIComponent(label) + "/model", {
      method: "POST",
      headers: authHeaders(true),
      body: JSON.stringify({ model: model })
    }).then(function (res) {
      return res.json().then(function (d) { return { status: res.status, data: d }; });
    }).then(function (r) {
      if (!modelSwitch || modelSwitch.label !== label) { return; }
      if (r.status === 200 && r.data && r.data.status === "switched") {
        modelSwitch = null;
        if (lastDetailData && lastDetailData.label === label) {
          lastDetailData.model = r.data.model;
        }
        showNotice(t("ui.model.now", { model: r.data.model }), "ok");
      } else if (r.status === 200) {
        modelSwitch = { label: label, previous: previous,
                        text: MODEL_UNCONFIRMED_TEXT, pending: false };
        showNotice(plain((r.data && r.data.detail) || t(MODEL_UNCONFIRMED_TEXT)), "err");
      } else {
        modelSwitch = null;
        showNotice(plain((r.data && r.data.detail) || t("ui.model.failed")), "err");
      }
      if (lastDetailData && lastDetailData.label === label) {
        renderModelControl(lastDetailData);
      }
    }).catch(function () {
      if (modelSwitch && modelSwitch.label === label) { modelSwitch = null; }
      showNotice(t("ui.model.unreachable"), "err");
      if (lastDetailData && lastDetailData.label === label) {
        renderModelControl(lastDetailData);
      }
    });
  }

  // Section headers are the toggle and say what is inside.
  function updateSectionHeaders(data) {
    var tl = document.getElementById("tab-timeline");
    var rows = (data && data.timeline) ? data.timeline.length : 0;
    setDisclosure(tl, rows ? tn("ui.timeline.title_n", rows) : t("ui.timeline.title_empty"),
                  timelineOpen);

    var note = document.getElementById("timeline-note");
    if (note) {
      // The honest explanation: tool_history/stream_commentary are not
      // persisted, so a restart empties this for every older session.
      // Only while the section is open - collapsed sections must not
      // push the last message off the screen with an explanation.
      note.hidden = !(timelineOpen && rows === 0);
    }
    updateDiffHeader();
  }

  // A disclosure row, with an optional second line; both escaped.
  function setDisclosure(btn, title, open, sub) {
    var html = '<span class="dis-title">' + escapeHtml(title) +
      (sub ? '<span class="rp-sub">' + escapeHtml(sub) + "</span>" : "") + "</span>" +
      icon("chevron", "chev" + (open ? " is-open" : ""));
    if (btn.innerHTML !== html) { btn.innerHTML = html; }
    setAttr(btn, "aria-expanded", open ? "true" : "false");
  }

  function updateDiffHeader() {
    var btn = document.getElementById("tab-diff");
    var title = t("ui.diff.title");
    if (lastDiffData && !lastDiffData.available) {
      title = t("ui.diff.title_none");
    } else if (lastDiffData) {
      var n = (lastDiffData.files || []).length;
      title = n ? tn("ui.diff.title_n", n) : t("ui.diff.title_none");
    }
    setDisclosure(btn, title, diffOpen);
  }

  // ---- per-session settings (design §4 default-vs-override mechanic) --

  // {schema, values, can_edit}, or null before the first fetch.
  var sessionSettingsData = null;
  // Which session sessionSettingsData is *for* - every callback below
  // checks this before touching shared state, so a slow response for a
  // session the operator has already navigated away from can never paint
  // (or, worse, silently roll back) the session on screen now.
  var sessionSettingsLabel = null;
  // Per-field write counter, so a slow PUT that resolves after a newer one
  // cannot roll the field back. Declared here rather than beside its only
  // reader: it was previously declared inside the block that rendered the
  // settings, and a rewrite of that block deleted it - every tap then threw
  // `sessionSaveSeq is not defined` on the first line of the save handler,
  // which aborts the click silently. Nothing repainted, nothing was sent,
  // and the option simply did not respond.
  var sessionSaveSeq = Object.create(null);

  function renderSessionSettings() {
    var root = document.getElementById("session-settings-groups");
    root.innerHTML = "";
    if (!sessionSettingsData) { return; }
    var canEdit = !!sessionSettingsData.can_edit;
    document.getElementById("session-settings-readonly").hidden = canEdit;

    // The label the LOADED DATA belongs to, not whatever view is current -
    // those can diverge mid-navigation, and a write must target the session
    // whose values are on screen.
    var label = sessionSettingsLabel;
    var anyOverridden = false;
    sessionSettingsData.schema.forEach(function (group) {
      var v = sessionSettingsData.values[group.field] || {};
      if (v.overridden) { anyOverridden = true; }
      renderOptionGroup(root, {
        key: "sess:" + group.field,
        title: group.title,
        options: group.options,
        // Fill = what this session uses; tag = the chat default.
        current: v.effective,
        defaultValue: v.scope_default,
        disabled: !canEdit,
        rerender: renderSessionSettings,
        onPick: function (value) {
          saveSessionPreference(label, group.field, value);
        }
      });
    });

    var reset = document.getElementById("session-settings-reset");
    reset.hidden = !(canEdit && anyOverridden);
  }

  function saveSessionPreference(label, field, value) {
    if (!sessionSettingsData) { return; }
    var current = sessionSettingsData.values[field];
    if (current && current.overridden && current.override_value === value) { return; }
    var previous = current;
    var seq = (sessionSaveSeq[field] || 0) + 1;
    sessionSaveSeq[field] = seq;

    // Optimistic, with `previous` kept so a failed write restores the truth.
    sessionSettingsData.values[field] = {
      effective: value,
      scope_default: previous ? previous.scope_default : value,
      override_value: value,
      overridden: true
    };
    renderSessionSettings();

    fetch("/api/sessions/" + encodeURIComponent(label) + "/preferences/" + encodeURIComponent(field), {
      method: "PUT",
      headers: authHeaders(true),
      body: JSON.stringify({ value: value })
    }).then(function (res) {
      if (res.status === 401 || res.status === 403) {
        var authErr = new Error("auth");
        authErr.authFailed = true;
        throw authErr;
      }
      if (!res.ok) { throw new Error("HTTP " + res.status); }
      return res.json();
    }).then(function (data) {
      if (sessionSaveSeq[field] !== seq || sessionSettingsLabel !== label) { return; }
      // Only adopt a well-formed body. Assigning an absent `values` would
      // leave the client with no values object at all, and the next render
      // or tap would throw on it.
      if (!data || !data.values) { return; }
      sessionSettingsData.values = data.values;
      renderSessionSettings();
      showNotice(t("ui.settings.saved"), "ok");
      if (tg && tg.HapticFeedback) {
        try { tg.HapticFeedback.notificationOccurred("success"); } catch (e) { /* old client */ }
      }
    }).catch(function (err) {
      if (err && err.authFailed) {
        handleFetchError(err);
        return;
      }
      if (sessionSaveSeq[field] !== seq || sessionSettingsLabel !== label) { return; }
      sessionSettingsData.values[field] = previous;
      renderSessionSettings();
      showNotice(t("ui.settings.save_failed"), "err");
    });
  }

  // "Reset to default": one DELETE per overridden field. Each is
  // idempotent, so a mid-sequence failure self-heals on the next tap.
  function resetSessionSettings() {
    if (!sessionSettingsData) { return; }
    var label = sessionSettingsLabel;
    var fields = Object.keys(sessionSettingsData.values).filter(function (field) {
      return sessionSettingsData.values[field].overridden;
    });
    if (fields.length === 0) { return; }

    var resetBtn = document.getElementById("session-settings-reset");
    resetBtn.disabled = true;
    var remaining = fields.length;
    var hadError = false;

    fields.forEach(function (field) {
      fetch("/api/sessions/" + encodeURIComponent(label) + "/preferences/" + encodeURIComponent(field), {
        method: "DELETE",
        headers: authHeaders()
      }).then(function (res) {
        if (res.status === 401 || res.status === 403) {
          var authErr = new Error("auth");
          authErr.authFailed = true;
          throw authErr;
        }
        if (!res.ok) { throw new Error("HTTP " + res.status); }
        return res.json();
      }).then(function (data) {
        if (sessionSettingsLabel === label) { sessionSettingsData.values = data.values; }
      }).catch(function (err) {
        hadError = true;
        if (err && err.authFailed) { handleFetchError(err); }
      }).then(function () {
        remaining -= 1;
        if (remaining > 0) { return; }
        if (sessionSettingsLabel !== label) { return; }
        resetBtn.disabled = false;
        renderSessionSettings();
        showNotice(t(hadError ? "ui.settings.reset_failed" : "ui.settings.reset_done"),
                   hadError ? "err" : "ok");
      });
    });
  }

  function loadSessionSettings(label) {
    sessionSettingsLabel = label;
    sessionSettingsData = null;
    renderSessionSettings();
    apiFetch("/api/sessions/" + encodeURIComponent(label) + "/preferences")
      .then(function (data) {
        if (sessionSettingsLabel !== label) { return; }
        sessionSettingsData = data;
        renderSessionSettings();
      })
      .catch(function (err) {
        if (sessionSettingsLabel !== label) { return; }
        handleFetchError(err);
      });
  }

  // ---- drill-down: diff -----------------------------------------------

  // Keyed by the server's reason id.
  var DIFF_REASONS = {
    not_a_git_repo: "ui.diff.reason.not_a_git_repo",
    git_not_installed: "ui.diff.reason.git_not_installed",
    cwd_missing: "ui.diff.reason.cwd_missing",
    no_commits_yet: "ui.diff.reason.no_commits_yet",
    git_error: "ui.diff.reason.git_error"
  };
  // A file's change, keyed by the server's id; an unknown id shows as it is.
  var CHANGE_WORDS = {
    untracked: "ui.diff.change.untracked", deleted: "ui.diff.change.deleted",
    added: "ui.diff.change.added", renamed: "ui.diff.change.renamed",
    modified: "ui.diff.change.modified"
  };

  function renderDiffLine(line) {
    var cls = "diff-context";
    if (line.indexOf("@@") === 0) {
      cls = "diff-hunk";
    } else if (line.indexOf("+") === 0 && line.indexOf("+++") !== 0) {
      cls = "diff-add";
    } else if (line.indexOf("-") === 0 && line.indexOf("---") !== 0) {
      cls = "diff-del";
    }
    return '<div class="diff-line ' + cls + '">' + escapeHtml(line) + '</div>';
  }

  function renderDiffFile(f) {
    var wrap = document.createElement("div");
    wrap.className = "diff-file";

    var header = document.createElement("div");
    header.className = "diff-file-header";
    header.innerHTML =
      '<span dir="ltr">' + escapeHtml(f.path) + '</span>' +
      '<span class="muted">' + escapeHtml(CHANGE_WORDS[f.change_type]
        ? t(CHANGE_WORDS[f.change_type]) : f.change_type) + '</span>';

    var body = document.createElement("div");
    body.className = "diff-body";
    if (f.binary) {
      body.innerHTML = '<div class="diff-binary">' + escapeHtml(t("ui.diff.binary")) + '</div>';
    } else if (!f.patch) {
      body.innerHTML = '<div class="diff-truncated">' +
        escapeHtml(t(f.truncated ? "ui.diff.too_large" : "ui.diff.no_changes")) + '</div>';
    } else {
      body.setAttribute("dir", "ltr");     // a patch reads left to right in any language
      body.innerHTML = f.patch.split("\n").map(renderDiffLine).join("");
    }

    header.addEventListener("click", function () { body.hidden = !body.hidden; });

    wrap.appendChild(header);
    wrap.appendChild(body);
    return wrap;
  }

  function renderDiff(data) {
    lastDiffData = data;
    var panel = document.getElementById("panel-diff");
    if (!data.available) {
      panel.innerHTML = '<div class="diff-truncated">' +
        escapeHtml(t(DIFF_REASONS[data.reason] || "ui.diff.unavailable")) + '</div>';
      return;
    }
    var files = data.files || [];
    if (files.length === 0) {
      // Name the repo. "No changes." alone reads like the viewer failed;
      // "no uncommitted changes in aipager" is a statement about the repo.
      // Belt and braces: only trust the cached detail payload when it is
      // demonstrably for the session on screen.
      var forThisSession = lastDetailData &&
        lastDetailData.label === currentView.label;
      var repo = forThisSession && lastDetailData.cwd
        ? lastDetailData.cwd.split("/").filter(Boolean).pop()
        : "";
      var msg = repo
        ? t("ui.diff.clean_in", { repo: repo })
        : t("ui.diff.clean");
      panel.innerHTML = '<div class="muted">' + escapeHtml(msg) + "</div>";
      return;
    }
    panel.innerHTML = "";
    if (data.files_truncated) {
      var note = document.createElement("div");
      note.className = "diff-truncated";
      note.textContent = t("ui.diff.partial");
      panel.appendChild(note);
    }
    files.forEach(function (f) { panel.appendChild(renderDiffFile(f)); });
  }

  function loadDiffIfNeeded() {
    var label = currentView.label;
    if (diffLoadedForLabel === label) { return; }
    var panel = document.getElementById("panel-diff");
    panel.innerHTML = '<div class="muted">' + escapeHtml(t("ui.diff.loading")) + '</div>';
    apiFetch("/api/sessions/" + encodeURIComponent(label) + "/diff")
      .then(function (data) {
        diffLoadedForLabel = label;
        renderDiff(data);
      })
      .catch(function (err) {
        if (err && err.httpStatus === 404) {
          panel.innerHTML = '<div class="diff-truncated">' + escapeHtml(t("ui.diff.gone")) +
            '</div>';
          return;
        }
        handleFetchError(err);
        panel.innerHTML = '<div class="diff-truncated">' + escapeHtml(t("ui.diff.failed")) +
          '</div>';
      });
  }

  // ---- detail-page write actions -----------------------------------------
  // `data.actions` comes from sessions.session_actions: render exactly
  // the keys given, disable an unavailable one and show its reason.
  // Stop/Resume/Clear-queue/Compact are one tap (recoverable);
  // Kill/Delete/Perms/Restart go through the centred confirm modal, out
  // of double-tap reach. Rename uses the same modal with a text field.
  var overlayCloser = null;   // set while the menu OR the modal is open
  var confirmAction = null;   // {label, action} awaiting confirmation
  var confirmRun = null;      // or a plain callback (Reset to defaults)
  var menuSignature = "";     // what the open menu was built from

  // Keys, keyed by the action id (the text follows the page's language).
  var ACTION_TITLES = {
    stop: "ui.btn.action.stop", kill: "ui.btn.action.kill", resume: "ui.btn.action.resume",
    delete: "ui.btn.action.delete", clearqueue: "ui.btn.action.clearqueue",
    compact: "ui.btn.action.compact", restart: "ui.btn.action.restart",
    rename: "ui.btn.action.rename"
    // perms: one item per mode the session is not in (data.modes).
  };
  // Path segment per action; Delete is the DELETE verb on the resource.
  var ACTION_PATHS = {
    stop: "stop", kill: "kill", resume: "resume", delete: "",
    clearqueue: "clearqueue", compact: "compact", perms: "perms",
    restart: "restart", rename: "rename"
  };
  var ACTION_METHODS = {
    stop: "POST", kill: "POST", resume: "POST", delete: "DELETE",
    clearqueue: "POST", compact: "POST", perms: "POST", restart: "POST",
    rename: "POST"
  };
  var CONFIRM_ACTIONS = { kill: true, delete: true, perms: true, restart: true };
  // Mirrors sessions._CANONICAL_ACTION_ORDER: five control items, then
  // four confirm-modal ones; the divider falls between the groups.
  var ACTION_ORDER = [
    "stop", "clearqueue", "compact", "resume", "rename",
    "kill", "perms", "restart", "delete"
  ];

  // The perms slot's items: every mode the session is not in, in the
  // server's order (Ask, Auto, Full access), each with its own rule.
  var MODE_SWITCH_TITLES = {
    ask: "ui.btn.switch_to_ask", auto: "ui.btn.switch_to_auto",
    full: "ui.btn.switch_to_full"
  };
  // The confirm's title for a switch that restarts, by its target.
  var PERMS_TITLES = {
    ask: "ui.confirm.perms_ask", auto: "ui.confirm.perms_auto",
    full: "ui.confirm.perms_full"
  };
  var PERMS_BUSY_TITLES = {
    ask: "ui.confirm.perms_busy_ask", auto: "ui.confirm.perms_busy_auto",
    full: "ui.confirm.perms_busy_full"
  };
  function otherModes(data) {
    return ((data && data.modes) || []).filter(function (m) {
      return !m.current && MODE_SWITCH_TITLES[m.id];
    });
  }

  // One close for both layers: only one is ever open.
  function closeOverlay() {
    overlayCloser = null;
    // Reset both pending fields, so no future caller inherits a stale one.
    confirmAction = null;
    confirmRun = null;
    menuSignature = "";
    document.getElementById("overlay").hidden = true;
    document.getElementById("action-menu").hidden = true;
    document.getElementById("confirm-modal").hidden = true;
    // Reset rename's field too, so it never leaks into another dialog.
    document.getElementById("confirm-rename-input").hidden = true;
    document.getElementById("confirm-rename-error").hidden = true;
    syncChrome();
    var kebab = document.getElementById("detail-menu-btn");
    kebab.setAttribute("aria-expanded", "false");
    if (kebab.focus) { try { kebab.focus(); } catch (e) { /* older webview */ } }
  }

  // What the menu was built from: a poll that changes the offer closes
  // the menu instead of redrawing it under a finger.
  function actionsSignature(data) {
    if (!data || !data.actions) { return ""; }
    return ACTION_ORDER.filter(function (k) { return data.actions[k]; })
      .map(function (k) {
        return k + ":" + (data.actions[k].available ? "1" : "0");
      }).join(",") + "|" + otherModes(data).map(function (m) {
        return m.id + ":" + (m.available ? "1" : "0");
      }).join(",") + "|" + (data.label || "");
  }

  function postSessionAction(label, action, method, body) {
    var path = "/api/sessions/" + encodeURIComponent(label) +
      (action ? "/" + action : "");
    var headers = authHeaders();
    if (method === "POST") { headers["Content-Type"] = "application/json"; }
    var opts = { method: method, headers: headers };
    if (body) { opts.body = JSON.stringify(body); }
    return fetch(path, opts)
      .then(function (res) {
        return res.json().then(function (data) {
          return { status: res.status, data: data };
        });
      });
  }

  // Success toast per action, then pollTick (the session stays). Kill,
  // Delete and Rename navigate, so they are handled separately.
  var ACTION_SUCCESS_NOTICE = {
    stop: "ui.done.stop", resume: "ui.done.resume", clearqueue: "ui.done.clearqueue",
    compact: "ui.done.compact", perms: "ui.done.perms", restart: "ui.done.restart"
  };

  function runDetailAction(label, action, mode) {
    var method = ACTION_METHODS[action];
    var body = action === "perms" ? { mode: mode } : null;
    postSessionAction(label, ACTION_PATHS[action], method, body).then(function (r) {
      if (action === "kill" || action === "delete") {
        if (r.status === 200 || (r.status === 404 && action === "kill")) {
          // Kill's race (socket already gone) reads as gone either way.
          showNotice(t(r.status === 404 ? "ui.done.already_gone"
            : (action === "kill" ? "ui.done.kill" : "ui.done.delete")), "ok");
          showGrid();
          return;
        }
        showNotice(plain((r.data && r.data.detail) || t("ui.action.failed")), "err");
        return;
      }
      // stop / resume / clearqueue / compact / perms / restart
      if (r.status === 200) {
        // A resume that came back in Auto (Full access needs an admin)
        // says so; an Ask/Auto switch is sent, not yet confirmed.
        var done = (action === "perms" && r.data && r.data.status === "sent")
          ? "ui.done.mode_sent" : (ACTION_SUCCESS_NOTICE[action] || "ui.done.generic");
        showNotice((action === "resume" && r.data && r.data.detail)
          ? plain(r.data.detail) : t(done), "ok");
        pollTick();
        return;
      }
      showNotice(plain((r.data && r.data.detail) || t("ui.action.failed")), "err");
    }).catch(function () {
      showNotice(t("ui.action.unreachable"), "err");
    });
  }

  function openActionMenu() {
    var data = lastDetailData;
    if (!data || !data.actions) { return; }
    var menu = document.getElementById("action-menu");
    menu.innerHTML = "";

    var rendered = 0;
    var controlRendered = 0;
    var dividerInserted = false;
    ACTION_ORDER.forEach(function (key) {
      var spec = data.actions[key];
      if (!spec) { return; }
      var modes = key === "perms" ? otherModes(data) : null;
      if (modes && !modes.length) { return; }
      rendered++;

      // One hairline before the first destructive item, only when a
      // control item precedes it.
      if (CONFIRM_ACTIONS[key] && !dividerInserted && controlRendered > 0) {
        var divider = document.createElement("div");
        divider.className = "menu-divider";
        menu.appendChild(divider);
        dividerInserted = true;
      }
      if (!CONFIRM_ACTIONS[key]) { controlRendered++; }

      var entries = modes ? modes.map(function (m) {
        return {
          title: t(MODE_SWITCH_TITLES[m.id]), mode: m.id, danger: !!m.restarts,
          available: spec.available && m.available,
          reason: spec.available ? m.reason : spec.reason
        };
      }) : [{
        title: t(ACTION_TITLES[key]), mode: null, danger: !!CONFIRM_ACTIONS[key],
        available: spec.available, reason: spec.reason
      }];
      entries.forEach(function (entry) {
        var item = document.createElement("button");
        item.type = "button";
        item.className = "menu-item act-" + key +
          (entry.mode ? " mode-" + entry.mode : "") + (entry.danger ? " is-danger" : "");
        item.setAttribute("role", "menuitem");
        // Icon node plus label node; textContent stays the action name.
        var glyph = document.createElement("span");
        glyph.className = "menu-icon";
        glyph.innerHTML = icon(key);
        var text = document.createElement("span");
        text.className = "menu-label";
        text.textContent = entry.title;
        item.appendChild(glyph);
        item.appendChild(text);
        if (!entry.available) {
          item.disabled = true;
        } else {
          item.addEventListener("click", function () {
            onMenuItemTap(data.label, key, entry.mode);
          });
        }
        menu.appendChild(item);

        // An action that cannot run says why, under itself.
        if (!entry.available && entry.reason) {
          var note = document.createElement("div");
          note.className = "menu-note";
          note.textContent = plain(entry.reason);
          menu.appendChild(note);
        }
      });
    });
    if (!rendered) { return; }

    menuSignature = actionsSignature(data);
    document.getElementById("overlay").hidden = false;
    menu.hidden = false;
    document.getElementById("confirm-modal").hidden = true;
    document.getElementById("detail-menu-btn").setAttribute("aria-expanded", "true");
    overlayCloser = closeOverlay;
    syncChrome();
  }

  function onMenuItemTap(label, action, mode) {
    if (action === "rename") {
      openRenameModal(label);
      return;
    }
    if (!CONFIRM_ACTIONS[action]) {
      closeOverlay();
      runDetailAction(label, action);
      return;
    }

    var data = lastDetailData || {};
    if (action === "perms") {
      var entry = ((data.modes || []).filter(function (m) { return m.id === mode; }))[0];
      if (!entry || !entry.restarts) {
        // Ask <-> Auto: no restart, nothing to confirm.
        closeOverlay();
        runDetailAction(label, action, mode);
        return;
      }
      // Into or out of Full access: the session restarts.
      var busy = data.status === "busy";
      var session = { session: label };
      openConfirm({
        label: label,
        action: action,
        mode: mode,
        title: t((busy ? PERMS_BUSY_TITLES : PERMS_TITLES)[mode], session),
        body: t(mode === "full" ? "ui.confirm.perms_full_body"
          : "ui.confirm.perms_leave_full_body"),
        confirmLabel: t(busy ? "ui.btn.stop_and_switch" : "ui.btn.switch"),
        cancelLabel: t(busy ? "ui.btn.not_now" : "ui.btn.cancel")
      });
      return;
    }
    if (action === "restart") {
      openConfirm({
        label: label,
        action: action,
        title: t("ui.confirm.restart", { session: label }),
        body: t("ui.confirm.restart_body"),
        confirmLabel: t(ACTION_TITLES.restart)
      });
      return;
    }

    openConfirm({
      label: label,
      action: action,
      title: t(action === "kill" ? "ui.confirm.kill" : "ui.confirm.delete", { session: label }),
      body: t(action === "kill" ? "ui.confirm.kill_body" : "ui.confirm.delete_body"),
      confirmLabel: t(ACTION_TITLES[action])
    });
  }

  // The one confirm dialog: destructive session actions and "Reset to
  // defaults". Always hides the rename field (openRenameModal shows it).
  function openConfirm(opts) {
    confirmAction = opts.label
      ? { label: opts.label, action: opts.action, mode: opts.mode || null } : null;
    confirmRun = opts.onConfirm || null;
    document.getElementById("confirm-title").textContent = opts.title;
    document.getElementById("confirm-body").textContent = opts.body;
    var ok = document.getElementById("confirm-ok");
    ok.textContent = opts.confirmLabel;
    ok.className = "modal-btn is-danger";
    ok.disabled = false;
    var cancel = document.getElementById("confirm-cancel");
    cancel.textContent = opts.cancelLabel || t("ui.btn.cancel");
    document.getElementById("confirm-rename-input").hidden = true;
    document.getElementById("confirm-rename-error").hidden = true;
    document.getElementById("action-menu").hidden = true;
    document.getElementById("overlay").hidden = false;
    document.getElementById("confirm-modal").hidden = false;
    document.getElementById("detail-menu-btn").setAttribute("aria-expanded", "false");
    overlayCloser = closeOverlay;
    syncChrome();
    // Focus lands on Cancel, not the destructive button.
    if (cancel.focus) { try { cancel.focus(); } catch (e) { /* older webview */ } }
  }

  // ---- Rename: the confirm modal plus a text field ----------------------
  // Client-side checks mirror launch.validate_session_name as a
  // courtesy; the server is the gate.
  var RENAME_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;
  // Mirrors aipager.dtach.inject._RESERVED. Kept in sync by
  // tests/test_reserved_name_reconciliation.py, which fails if the two
  // drift - this list went stale once already, missing every command
  // added after it was written.
  var RENAME_RESERVED = {
    "app": true, "clearqueue": true, "delete": true, "diff": true,
    "help": true, "kill": true, "language": true, "list": true, "ls": true,
    "mode": true, "new": true,
    "now": true, "perms": true, "rename": true, "restart": true, "resume": true,
    "settings": true, "start": true, "status": true, "stop": true,
    "update": true, "whoami": true
  };

  function renameValidationError(value) {
    var trimmed = (value || "").trim();
    if (!trimmed) { return t("ui.rename.empty"); }
    if (trimmed.length > 64) {
      return t("ui.rename.too_long", { max: 64 });
    }
    if (!RENAME_NAME_RE.test(trimmed)) {
      return t("ui.rename.pattern");
    }
    if (RENAME_RESERVED[trimmed.toLowerCase()]) {
      return t("ui.rename.reserved", { name: trimmed });
    }
    return "";
  }

  function refreshRenameValidity() {
    var input = document.getElementById("confirm-rename-input");
    var ok = document.getElementById("confirm-ok");
    var errEl = document.getElementById("confirm-rename-error");
    var err = renameValidationError(input.value);
    ok.disabled = !!err;
    errEl.textContent = err;
    errEl.hidden = !err;
  }

  function openRenameModal(label) {
    confirmAction = { label: label, action: "rename" };
    confirmRun = null;
    document.getElementById("confirm-title").textContent =
      t("ui.confirm.rename", { session: label });
    document.getElementById("confirm-body").textContent = t("ui.confirm.rename_body");
    var ok = document.getElementById("confirm-ok");
    ok.textContent = t("ui.btn.save");
    ok.className = "modal-btn";   // rename isn't destructive - no red.
    document.getElementById("confirm-cancel").textContent = t("ui.btn.cancel");

    var input = document.getElementById("confirm-rename-input");
    input.hidden = false;
    input.value = label;

    document.getElementById("action-menu").hidden = true;
    document.getElementById("overlay").hidden = false;
    document.getElementById("confirm-modal").hidden = false;
    document.getElementById("detail-menu-btn").setAttribute("aria-expanded", "false");
    overlayCloser = closeOverlay;
    syncChrome();

    refreshRenameValidity();
    // Focus lands in the input: rename is "start typing".
    if (input.focus) {
      try { input.focus(); input.select(); } catch (e) { /* older webview */ }
    }
  }

  function submitRename(oldLabel, newLabel) {
    fetch("/api/sessions/" + encodeURIComponent(oldLabel) + "/rename", {
      method: "POST",
      headers: authHeaders(true),
      body: JSON.stringify({ label: newLabel })
    }).then(function (res) {
      return res.json().then(function (data) {
        return { status: res.status, data: data };
      });
    }).then(function (r) {
      if (r.status === 200) {
        var body = r.data || {};
        showNotice(t(body.changed === false ? "ui.done.rename_same" : "ui.done.rename"), "ok");
        // The old label would 404: go to the new one.
        openDetail(body.label || newLabel);
        return;
      }
      showNotice(plain((r.data && r.data.detail) || t("ui.rename.failed")), "err");
    }).catch(function () {
      showNotice(t("ui.action.unreachable"), "err");
    });
  }

  function onConfirmTap() {
    // Refuse a disabled Confirm even if a click is dispatched at it.
    if (document.getElementById("confirm-ok").disabled) { return; }
    var pending = confirmAction;
    var run = confirmRun;
    if (pending && pending.action === "rename") {
      var newLabel = document.getElementById("confirm-rename-input").value.trim();
      closeOverlay();
      submitRename(pending.label, newLabel);
      return;
    }
    closeOverlay();
    if (run) { run(); return; }
    if (pending) { runDetailAction(pending.label, pending.action, pending.mode); }
  }

  // The kebab shows only when there are actions.
  function renderDetailActions(data) {
    var kebab = document.getElementById("detail-menu-btn");
    var count = 0;
    if (data && data.actions) {
      ACTION_ORDER.forEach(function (k) { if (data.actions[k]) { count++; } });
    }
    kebab.hidden = count === 0;

    // An offer change must not redraw an open menu.
    if (overlayCloser && menuSignature &&
        actionsSignature(data) !== menuSignature) {
      closeOverlay();
      showNotice(t("ui.menu.changed"));
    }
  }

  // ---- view switching ---------------------------------------------------

  // Independent collapsible sections, collapsed by default, below the reply.
  var diffOpen = false;
  var timelineOpen = false;

  function toggleDiff() {
    diffOpen = !diffOpen;
    document.getElementById("panel-diff").hidden = !diffOpen;
    if (diffOpen) { loadDiffIfNeeded(); }
    updateDiffHeader();
  }

  function toggleTimeline() {
    timelineOpen = !timelineOpen;
    document.getElementById("panel-timeline").hidden = !timelineOpen;
    updateSectionHeaders(lastDetailData);
  }

  function openDetail(label) {
    diffOpen = false;
    timelineOpen = false;
    lastDiffData = null;
    // Navigating to a (possibly different) session must never carry an
    // open menu or a half-answered confirm along with it.
    closeOverlay();
    // Reset the detail payload too: renderDiff names the repo from
    // lastDetailData.cwd, and expanding "Changed files" on this session
    // before its own detail poll lands would otherwise name the PREVIOUS
    // session's directory next to this one's diff.
    lastDetailData = null;
    document.getElementById("panel-diff").hidden = true;
    document.getElementById("panel-timeline").hidden = true;
    diffLoadedForLabel = null;
    showView("detail", { label: label });

    // Paint from the grid's row so the page is never blank; the poll
    // overwrites it.
    var known = lastSessionsByLabel[label];
    detailSig = Object.create(null);
    if (known) {
      paintHeader(known);
    } else {
      paintHeader({ label: label, status: "unknown", context_pct: 0 });
      setText(document.getElementById("detail-status"), "");
      setText(document.getElementById("detail-state"), "");
    }
    document.getElementById("detail-quick").hidden = true;
    document.getElementById("detail-activity").hidden = true;
    document.getElementById("detail-waiting").hidden = true;
    // The previous session's Model picker must not linger.
    document.getElementById("detail-model").hidden = true;
    document.getElementById("detail-model-note").hidden = true;
    var facts = document.getElementById("detail-facts");
    facts.innerHTML = "";
    for (var i = 0; i < 4; i++) {
      var cell = make("div", "fact");
      skeleton(cell, 2);
      facts.appendChild(cell);
    }
    skeleton(document.getElementById("detail-preview"), 2);
    skeleton(document.getElementById("session-settings-groups"), 4, "skel-row");

    loadSessionSettings(label);
    pollTick();
  }

  function showGrid() {
    // Leaving the detail page - the same rule as openDetail.
    closeOverlay();
    // Back from a sub-page returns to whichever top-level tab was active.
    showView(mainTab === "settings" ? "settings" : "grid");
    pollTick();
  }



  // ---- settings group rendering (shared by all three surfaces) --------
  //
  // Collapsed by default: the heading and the value in force. All options
  // at once was what the operator called "lost in there".
  var openGroups = Object.create(null);

  // Schema text is shared with /settings in the chat, where titles lead
  // with an emoji and labels carry a separator ("Off - busy card only").
  // The page has its own line icons, so titles lose the leading emoji and
  // the collapsed header shows only the lead of a label ("Off"); the row
  // keeps the whole label.
  // Strips the leading emoji token (and a right-to-left mark before it),
  // never a word in any script.
  function groupTitle(s) { return plain(s).replace(/^[^\p{L}\p{N}\s]+\s+/u, ""); }
  function labelLead(s) {
    var t = String(s === null || s === undefined ? "" : s);
    var cut = t.indexOf(" - ");
    if (cut < 0) { cut = t.indexOf(" \u2014 "); }
    return plain(cut > 0 ? t.slice(0, cut) : t);
  }

  function renderOptionGroup(host, opts) {
    // opts: {key, title, options[], current, defaultValue, disabled, onPick,
    //        rerender, valueText, reveal}. valueText (new form only)
    // overrides the header for a typed value. reveal = {after, node}: a
    // persistent node moved under that row, so typing survives a render.
    // An option may carry create: true (dashed row) and active: <bool>
    // (selected state for an action row, which cannot use `current`).
    var isOpen = !!openGroups[opts.key];
    var wrap = document.createElement("div");
    wrap.className = "grp" + (isOpen ? " is-open" : "");

    var head = document.createElement("button");
    head.type = "button";
    head.className = "grp-head";
    head.setAttribute("aria-expanded", isOpen ? "true" : "false");

    var title = document.createElement("span");
    title.className = "grp-title";
    title.textContent = groupTitle(opts.title);

    var value = document.createElement("span");
    value.className = "grp-value";
    // Names (models, folders) and words share one list: each reads in its
    // own direction.
    if (opts.ltr) { value.setAttribute("dir", "auto"); }
    var currentOpt = null;
    opts.options.forEach(function (o) {
      if (o.value === opts.current) { currentOpt = o; }
    });
    value.textContent = opts.valueText !== undefined
      ? plain(opts.valueText)
      : (currentOpt ? labelLead(currentOpt.label) : "-");

    var caret = document.createElement("span");
    caret.className = "grp-caret";      // the chevron is drawn in CSS
    caret.setAttribute("aria-hidden", "true");

    head.appendChild(title);
    head.appendChild(value);
    head.appendChild(caret);
    head.addEventListener("click", function () {
      openGroups[opts.key] = !openGroups[opts.key];
      opts.rerender();
    });
    wrap.appendChild(head);

    if (isOpen) {
      var list = document.createElement("div");
      list.className = "grp-body";
      opts.options.forEach(function (o) {
        var row = document.createElement("button");
        row.type = "button";
        var isActive = o.active !== undefined
          ? !!o.active
          : o.value === opts.current;
        row.className = "choice" + (o.create ? " choice-new" : "") +
          (isActive ? " is-active" : "");
        if (opts.disabled) { row.disabled = true; }

        var main = document.createElement("span");
        main.className = "choice-main";
        main.textContent = plain(o.label);
        if (opts.ltr) { main.setAttribute("dir", "auto"); }
        row.appendChild(main);

        // The tag marks the SCOPE's value, never the selection.
        if (opts.defaultValue !== undefined && o.value === opts.defaultValue) {
          var tag = document.createElement("span");
          tag.className = "tag";
          tag.textContent = t("ui.settings.default_tag");
          row.appendChild(tag);
        }
        if (o.help) {
          var help = document.createElement("span");
          help.className = "choice-help";
          help.textContent = plain(o.help);
          if (opts.ltr) { help.setAttribute("dir", "auto"); }
          row.appendChild(help);
        }
        if (!opts.disabled) {
          row.addEventListener("click", function () {
            haptic("select");
            opts.onPick(o.value);
          });
        }
        list.appendChild(row);
        // Right after the row that revealed it.
        if (opts.reveal && opts.reveal.after === o.value) {
          list.appendChild(opts.reveal.node);
        }
      });
      wrap.appendChild(list);
    }
    host.appendChild(wrap);
    // Handed back so the new form can update the header per keystroke.
    return { wrap: wrap, value: value };
  }


  // A shaped placeholder while a fetch is in flight: an empty panel
  // that fills in later read as "comes in with a delay".
  function skeleton(host, rows, cls) {
    host.innerHTML = "";
    for (var i = 0; i < rows; i++) {
      var d = document.createElement("div");
      d.className = "skel " + (cls || "skel-line") +
        (cls ? "" : [" w90", " w70", " w40"][i % 3]);
      host.appendChild(d);
    }
  }

  // ---- views ----------------------------------------------------------
  //
  // Every view is declared here once. Before this, visibility was a set of
  // per-function `hidden` assignments and pollTick branched on "grid or
  // else", so adding the new-session view in batch 5 silently made
  // pollTick treat it as a session detail: it fetched
  // /api/sessions/undefined, got a 404, and the 404 handler sent the
  // operator back to the grid ~2.5s after opening the form. A table means
  // a new view cannot be half-added - it either has an entry or it does
  // not render at all.
  //
  //   section  - the DOM section this view shows
  //   topLevel - true if the Sessions|Settings tab bar belongs on screen
  //              (it is top-level navigation; on a sub-page it competes
  //              with Telegram's BackButton and makes the page feel lost)
  //   polls    - "grid" | "detail" | null (no polling at all)
  var VIEWS = {
    grid:     { section: "view-grid",     topLevel: true,  polls: "grid" },
    settings: { section: "view-settings", topLevel: true,  polls: null },
    detail:   { section: "view-detail",   topLevel: false, polls: "detail" },
    "new":    { section: "view-new",      topLevel: false, polls: null },
    report:   { section: "view-report",   topLevel: false, polls: null }
  };

  // ---- Telegram chrome: MainButton and vertical swipes ------------------
  // MainButton mirrors the in-page primary action ("Start session",
  // "Answer in chat") and hides elsewhere and under a menu or dialog.
  var mainAction = null;       // what a MainButton tap does right now
  var mainKey = "";            // the params last sent, to skip repeats
  var mainWired = false;
  var swipesOff = false;
  var submitting = false;      // the new-session POST is in flight

  function mainButton() {
    var mb = tg && tg.MainButton;
    return mb && typeof mb.setParams === "function" ? mb : null;
  }

  function syncMainButton() {
    var mb = mainButton();
    if (!mb) { return; }
    var root = document.documentElement;
    if (root && root.classList) { root.classList.add("has-mainbutton"); }
    if (!mainWired && typeof mb.onClick === "function") {
      mb.onClick(function () { if (mainAction) { mainAction(); } });
      mainWired = true;
    }
    var params = { is_visible: false };
    var action = null;
    var label = currentView.label;
    if (!overlayCloser && currentView.type === "new") {
      params = { text: t("ui.btn.start_session"), is_visible: true,
                 is_active: !document.getElementById("new-create").disabled };
      action = function () {
        if (!document.getElementById("new-create").disabled) { submitNewSession(); }
      };
    } else if (!overlayCloser && currentView.type === "detail" && lastDetailData &&
               lastDetailData.label === label && lastDetailData.answer &&
               lastDetailData.answer.available) {
      params = { text: t("ui.btn.answer_in_chat"), is_visible: true,
                 is_active: !answering[label] };
      action = function () { answerPrompt(label); };
    } else if (!overlayCloser && currentView.type === "report") {
      params = rp.result
        ? { text: t(rp.result.retry ? "ui.btn.try_again" : "ui.btn.done"), is_visible: true,
            is_active: true }
        : { text: t("ui.btn.send_report"), is_visible: true, is_active: rpCanSend() };
      action = rp.result ? rpDone : rpSend;
    }
    mainAction = action;
    var busy = submitting || (currentView.type === "detail" && !!answering[label]) ||
      (currentView.type === "report" && rp.sending);
    var key = JSON.stringify([params, busy && params.is_visible]);
    if (key === mainKey) { return; }
    mainKey = key;
    try {
      mb.setParams(params);
      if (busy && params.is_visible && typeof mb.showProgress === "function") {
        mb.showProgress(false);
      } else if (typeof mb.hideProgress === "function") {
        mb.hideProgress();
      }
    } catch (e) { /* older client */ }
  }

  // No swipe-to-dismiss while typing (a new session, a report) or in a dialog.
  function syncSwipes() {
    var off = currentView.type === "new" || currentView.type === "report" || !!overlayCloser;
    if (off === swipesOff || !tg || !tgAtLeast("7.7")) { return; }
    var fn = off ? tg.disableVerticalSwipes : tg.enableVerticalSwipes;
    if (typeof fn !== "function") { return; }
    swipesOff = off;
    try { fn.call(tg); } catch (e) { /* older client */ }
  }

  function syncChrome() {
    syncMainButton();
    syncSwipes();
  }

  function showView(name, extra) {
    var spec = VIEWS[name];
    if (!spec) { return; }            // unknown view: change nothing
    currentView = { type: name };
    if (extra) {
      for (var k in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, k)) {
          currentView[k] = extra[k];
        }
      }
    }
    for (var key in VIEWS) {
      if (Object.prototype.hasOwnProperty.call(VIEWS, key)) {
        document.getElementById(VIEWS[key].section).hidden = key !== name;
      }
    }
    document.getElementById("tabbar").hidden = !spec.topLevel;
    // The chat switcher is top-level navigation too.
    document.getElementById("chat-switch").hidden = !spec.topLevel || !chatSwitcherShown();
    if (tg && tg.BackButton) {
      if (spec.topLevel) { tg.BackButton.hide(); } else { tg.BackButton.show(); }
    }
    syncChrome();
  }

  // ---- settings -------------------------------------------------------

  var settingsData = null;   // {schema, values, can_edit} from the server

  // The viewer's own App language: above the chat's preferences, never
  // disabled (it is personal, not an admin's), every word from the server.
  function renderAppLanguage() {
    var host = document.getElementById("settings-app-language");
    var hint = document.getElementById("settings-app-language-hint");
    if (!host) { return; }
    host.innerHTML = "";
    var al = settingsData && settingsData.app_language;
    if (!al) {
      if (hint) { hint.hidden = true; hint.textContent = ""; }
      return;
    }
    renderOptionGroup(host, {
      key: "app:language",
      title: al.title,
      options: al.options || [],
      current: al.value,
      disabled: false,
      rerender: renderSettings,
      onPick: function (value) { saveAppLanguage(value); }
    });
    if (hint) {
      hint.textContent = al.hint || "";
      hint.hidden = !al.hint;
    }
  }

  function renderSettings() {
    var root = document.getElementById("settings-groups");
    root.innerHTML = "";
    if (!settingsData) { return; }
    renderAppLanguage();
    document.getElementById("settings-readonly").hidden = !!settingsData.can_edit;

    settingsData.schema.forEach(function (group) {
      renderOptionGroup(root, {
        key: "scope:" + group.field,
        title: group.title,
        options: group.options,
        current: settingsData.values[group.field],
        disabled: !settingsData.can_edit,
        rerender: renderSettings,
        onPick: function (value) {
          savePreference(group.field, value);
        }
      });
    });
  }

  var saveSeq = Object.create(null);
  function savePreference(field, value) {
    if (!settingsData || settingsData.values[field] === value) { return; }
    // Optimistic, with the previous value kept for a failed write.
    var previous = settingsData.values[field];
    var seq = (saveSeq[field] || 0) + 1;
    saveSeq[field] = seq;
    settingsData.values[field] = value;
    renderSettings();
    var epoch = chatEpoch;

    fetch("/api/preferences/" + encodeURIComponent(field), {
      method: "PUT",
      headers: authHeaders(true),
      body: JSON.stringify({ value: value })
    }).then(function (res) {
      if (res.status === 401 || res.status === 403) {
        var authErr = new Error("auth");
        authErr.authFailed = true;
        throw authErr;
      }
      if (!res.ok) { throw new Error("HTTP " + res.status); }
      return res.json();
    }).then(function (data) {
      if (epoch !== chatEpoch) { return; }     // saved in the chat left behind
      if (saveSeq[field] !== seq) { return; }   // superseded - ignore
      settingsData.values = data.values;
      renderSettings();
      showNotice(t("ui.settings.saved"), "ok");
      if (tg && tg.HapticFeedback) {
        try { tg.HapticFeedback.notificationOccurred("success"); } catch (e) { /* old client */ }
      }
    }).catch(function (err) {
      if (err && err.authFailed) {
        // The same terminal state every other request uses.
        handleFetchError(err);
        return;
      }
      if (epoch !== chatEpoch) { return; }     // failed in the chat left behind
      if (saveSeq[field] !== seq) { return; }   // superseded - ignore
      settingsData.values[field] = previous;
      renderSettings();
      showNotice(t("ui.settings.save_failed"), "err");
    });
  }

  var appLanguageSeq = 0;
  function saveAppLanguage(value) {
    var al = settingsData && settingsData.app_language;
    if (!al || al.value === value) { return; }
    // Optimistic, with the previous choice kept for a failed write.
    var previous = al.value;
    var seq = ++appLanguageSeq;
    al.value = value;
    renderSettings();
    fetch("/api/app-language", {
      method: "PUT",
      headers: authHeaders(true),
      body: JSON.stringify({ value: value })
    }).then(function (res) {
      if (res.status === 401 || res.status === 403) {
        var authErr = new Error("auth");
        authErr.authFailed = true;
        throw authErr;
      }
      if (!res.ok) { throw new Error("HTTP " + res.status); }
      return res.json();
    }).then(function (data) {
      if (seq !== appLanguageSeq) { return; }   // superseded - ignore
      var now = settingsData && settingsData.app_language;
      if (now) {
        now.value = data.value;
        now.effective = data.effective;
      }
      renderSettings();
      showNotice(data.confirmation, "ok");
      // The page changes language at once, no reload: Settings comes back
      // from the server in the new language.
      setUi(data.ui);
    }).catch(function (err) {
      if (err && err.authFailed) {
        handleFetchError(err);
        return;
      }
      if (seq !== appLanguageSeq) { return; }   // superseded - ignore
      var now = settingsData && settingsData.app_language;
      if (now) { now.value = previous; }
      renderSettings();
      showNotice(t("ui.settings.save_failed"), "err");
    });
  }

  function loadSettings() {
    // Re-entry shows held values; only a first visit gets the skeleton.
    if (settingsData) { renderSettings(); } else {
      skeleton(document.getElementById("settings-groups"), 4, "skel-row");
    }
    var epoch = chatEpoch;
    apiFetch("/api/preferences")
      .then(function (data) {
        if (epoch !== chatEpoch) { return; }     // for the chat left behind
        settingsData = data;
        renderSettings();
        loadUpdates();
        renderReport();
        // A language chosen in the chat (/language) reaches an open page here.
        if ((data && data.ui) || L.code !== "en") { setUi(data && data.ui); }
      })
      .catch(handleFetchError);
  }

  // ---- problem reports (owner only, private chat) ---------------------
  // The page posts only the draft id and the note; the server sends its
  // kept draft and hands back a note not in its final form. Report values
  // reach the DOM only through textContent or escapeHtml.

  // RP_LATER only when aipager never answered (timeout, network error).
  // RP_TRY is wording.TRY_LATER (ui_text.REPORT_TRY_LATER; a test keeps the
  // two equal). Keys: the page shows them in its language.
  var RP_LATER = "ui.report.later";
  var RP_TRY = "ui.report.try_later";
  var RP_PANELS = {
    noowner: ["ui.report.panel.noowner_title", "ui.report.panel.noowner"],
    owner: ["ui.report.panel.owner_title", "ui.report.panel.owner"],
    group: ["ui.report.panel.group_title", "ui.report.panel.group"],
    build: ["ui.report.panel.build_title", "ui.report.panel.build"]
  };
  // The panels' one parameter: a terminal command, shown as code.
  var RP_PANEL_PARAMS = { noowner: { command: "`aipager report`" } };
  var RP_TITLES = { sent: "ui.report.sent_title", too_old: "ui.report.too_old_title",
                    daily_cap: "ui.report.daily_cap_title" };
  var RP_FEATURES = { miniapp: "ui.report.feature.miniapp",
    tunnel_managed: "ui.report.feature.tunnel_managed",
    tunnel_override: "ui.report.feature.tunnel_override",
    voice: "ui.report.feature.voice",
    diff_preview_any: "ui.report.feature.diff_preview_any",
    rich_summaries: "ui.report.feature.rich_summaries" };
  var RP_UPTIME = { "<10m": "ui.report.uptime.under_10m", "10m-1h": "ui.report.uptime.10m_1h",
    "1-24h": "ui.report.uptime.1h_24h", "1-7d": "ui.report.uptime.1d_7d",
    ">7d": "ui.report.uptime.over_week" };
  var RP_MODES = { personal: "ui.report.setup.personal", team: "ui.report.setup.team",
                   scope: "ui.report.setup.scope" };
  var RP_EXITS = { clean: "ui.report.exit.clean", crash: "ui.report.exit.crash",
                   reboot: "ui.report.exit.reboot" };
  var RP_NAMES = { macos: "macOS", darwin: "macOS", nixos: "NixOS", freebsd: "FreeBSD" };

  var rp = { max: 500, note: "", noteEpoch: -1, seq: 0, open: {} };

  function rpEl(id) { return document.getElementById(id); }

  function hideReport() {
    rpEl("report-block").hidden = true;
    rp.seq += 1;
    rp.draft = rp.report = rp.result = null;
    rp.note = "";
    rp.loading = rp.sending = false;
  }

  function renderReport() {
    if (!settingsData || !settingsData.can_report) { hideReport(); return; }
    rpEl("report-block").hidden = false;
  }

  // NOT apiFetch (a 403 must not end the app). No answer in 30 s rejects.
  function rpPost(path, body) {
    return new Promise(function (resolve, reject) {
      var done = false;
      function end(fn, v) { if (!done) { done = true; fn(v); } }
      setTimeout(function () { end(reject, new Error("timeout")); }, 30000);
      fetch(path, { method: "POST", headers: authHeaders(true), body: JSON.stringify(body) })
        .then(function (res) {
          return res.json().then(null, function () { return {}; }).then(function (data) {
            return { status: res.status, data: data || {} };
          });
        })
        .then(function (r) { end(resolve, r); }, function (e) { end(reject, e); });
    });
  }

  function rpNote() { return rpEl("rp-note").value.trim(); }
  // Code points, as the server counts them (an emoji is one, not two).
  function rpCount(s) { return s.replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g, "x").length; }
  function rpCanSend() {
    return !!rp.draft && !rp.sending && !rp.result && rpCount(rpNote()) <= rp.max;
  }

  function rpSync() {
    var btn = rpEl("rp-send");
    btn.disabled = !rpCanSend();
    setText(btn, t(rp.sending ? "ui.btn.sending" : "ui.btn.send_report"));
    rpEl("rp-note").readOnly = !!rp.sending;
    syncMainButton();
  }

  // The line sits under the note; Send is often tapped from the bottom of
  // the page, so a message is brought into view and buzzes.
  function rpStatus(text, err) {
    var el = rpEl("rp-status");
    el.textContent = text || "";
    el.className = "rp-status" + (err ? " is-err" : "");
    el.hidden = !text;
    if (!text) { return; }
    haptic("notify", err ? "error" : "warning");
    if (el.scrollIntoView) {
      try { el.scrollIntoView({ behavior: "smooth", block: "center" }); } catch (e) { /* old webview */ }
    }
  }

  function rpFact(dl, label, value, wide) {
    var cell = make("div", "fact" + (wide ? " fact-wide" : ""));
    cell.appendChild(make("dt")).textContent = label;
    cell.appendChild(make("dd")).textContent = value;
    dl.appendChild(cell);
  }

  function rpName(s) {
    s = String(s || "");
    return RP_NAMES[s] || s.charAt(0).toUpperCase() + s.slice(1);
  }
  function rpTimes(n) { return n > 1 ? tn("ui.report.times", n) : t("ui.report.once"); }
  function rpObj(v) { return v && typeof v === "object" ? v : {}; }
  function rpJoin(o, fn) { return joinList(Object.keys(o).map(function (k) { return fn(k, o[k]); })); }

  function rpFacts() {
    var r = rp.report, a = rpObj(r.aipager), os = rpObj(r.os), c = rpObj(r.config);
    var rt = rpObj(r.runtime), fl = rpObj(r.flood), dl = rpEl("rp-facts"), more = rpEl("rp-more-facts");
    var sys = os.distro && os.distro !== "other"
      ? rpName(os.distro) + (os.distro_version ? " " + os.distro_version : "") : rpName(os.system);
    var mode = RP_MODES[c.mode] ? t(RP_MODES[c.mode]) : "";
    var digest = r.log_digest_24h || [], times = 0;
    digest.forEach(function (d) { times += d.n || 0; });
    dl.innerHTML = more.innerHTML = "";
    rpFact(dl, "aipager", a.version + " (" + a.install + ")");
    rpFact(dl, "Claude Code", rpObj(r.claude_code).version || t("ui.report.not_found"));
    rpFact(dl, t("ui.report.label.system"), os.container && os.container !== "none"
      ? t("ui.report.system_in", { system: sys, container: os.container }) : sys);
    rpFact(dl, "Python", rpObj(r.python).version);
    var setup = {
      mode: mode, dms: tn("ui.report.n_private_chats", c.scopes_dm || 0),
      groups: tn("ui.report.n_groups", c.scopes_group || 0),
      uptime: t(RP_UPTIME[rt.uptime] || "ui.report.uptime.unknown")
    };
    rpFact(dl, t("ui.report.label.setup"), t(mode
      ? (c.scopes_group ? "ui.report.setup_line" : "ui.report.setup_line_no_groups")
      : (c.scopes_group ? "ui.report.setup_line_no_mode" : "ui.report.setup_line_bare"),
      setup), 1);
    rpFact(more, t("ui.report.label.features"), joinList((c.features || []).map(function (f) {
      return RP_FEATURES[f] ? t(RP_FEATURES[f]) : f;
    })) || t("ui.report.none"), 1);
    rpFact(more, t("ui.report.label.libraries"), rpJoin(rpObj(r.deps), function (k, v) {
      return k + " " + (v === null ? t("ui.report.missing") : v);
    }) || t("ui.report.none"), 1);
    rpFact(more, t("ui.report.label.stops"), t("ui.report.stops", {
      last: t(RP_EXITS[rt.last_exit] || "ui.report.exit.unknown"),
      n: rt.unclean_exits_7d || 0 }), 1);
    rpFact(more, t("ui.report.label.limits"),
      tn("ui.report.limits", fl.bans_7d || 0, { load: num(fl.hour_load) }), 1);
    rpFact(more, t("ui.report.label.counts"), rpJoin(rpObj(r.counters_24h), function (k, v) {
      return k.replace(/_/g, " ") + " " + num(v);
    }) || t("ui.report.none"), 1);
    rpFact(more, t("ui.report.label.log"), digest.length
      ? tn("ui.report.log_line", digest.length, { times: rpTimes(times) })
      : t("ui.report.nothing_logged"), 1);
  }

  function rpErrTitle(e, area) {
    var type = String(e.type);
    return e.trigger === "crash" ? t("ui.report.err.crash")
      : e.trigger === "hook_cap" ? t("ui.report.err.hook_cap")
      : e.type === null || e.type === undefined ? t("ui.report.err.logged_in", { area: area })
      : t("ui.report.err.type_in", { area: area, type: type.charAt(0) === "<"
        ? t("ui.report.err.error") : type.slice(type.lastIndexOf(".") + 1) });
  }

  function rpRow(host, title, open, sub, onTap) {
    var btn = host.appendChild(make("button", "sect-toggle"));
    setDisclosure(btn, title, open, sub);
    btn.addEventListener("click", onTap);
  }

  function rpErrors() {
    var errs = rp.report.errors || [], host = rpEl("rp-errors");
    host.innerHTML = "";
    setText(rpEl("rp-err-title"), errs.length ? tn("ui.report.errors_n", errs.length)
      : t("ui.report.errors"));
    host.hidden = !errs.length;
    rpEl("rp-noerr").hidden = !!errs.length;
    errs.forEach(function (e, i) {
      if (i > 2 && !rp.all) { return; }
      var open = !!rp.open[i];
      rpRow(host, rpErrTitle(e, rp.areas[i] || "aipager"), open,
            t("ui.report.err_sub", { times: rpTimes(e.count), day: e.last_day }),
            function () { rp.open[i] = !open; rpErrors(); });
      if (!open) { return; }
      var panel = host.appendChild(make("div", "panel"));
      panel.appendChild(make("p", "sect-note")).textContent = t("ui.report.err_detail", {
        fingerprint: e.fingerprint, day: e.first_day,
        versions: joinList(e.versions_seen || []) });
      var body = panel.appendChild(make("div", "diff-body"));
      body.setAttribute("dir", "ltr");     // file:line function, code
      (e.frames || []).forEach(function (f) {
        body.appendChild(make("div", "diff-line")).textContent = f.file + ":" + f.line + "  " + f.fn;
      });
      if ((e.external || []).length) {
        panel.appendChild(make("p", "sect-note")).textContent =
          t("ui.report.also_through", { names: joinList(e.external) });
      }
    });
    if (errs.length > 3 && !rp.all) {
      rpRow(host, tn("ui.report.show_more", errs.length - 3), false, "",
            function () { rp.all = true; rpErrors(); });
    }
  }

  function rpToggles() {
    setDisclosure(rpEl("rp-more-btn"), t("ui.report.more"), !!rp.more);
    rpEl("rp-more-facts").hidden = !rp.more;
    setDisclosure(rpEl("rp-exact-btn"), t("ui.report.exact"), !!rp.exact);
    rpEl("rp-exact-wrap").hidden = !rp.exact;
    rpExact();
  }

  // The exact text sent: the draft in its own key order, the note last.
  function rpExact() {
    if (!rp.exact || !rp.report) { return; }
    var copy = {}, note = rpNote();
    for (var k in rp.report) {
      if (Object.prototype.hasOwnProperty.call(rp.report, k)) { copy[k] = rp.report[k]; }
    }
    if (note) { copy.note = note; }
    rpEl("rp-exact").textContent = JSON.stringify(copy, null, 2);
  }

  function rpOnInput() {
    var ta = rpEl("rp-note"), count = rpEl("rp-count"), hint = rpEl("rp-hint");
    rp.note = ta.value;
    rp.noteEpoch = chatEpoch;
    if (ta.scrollHeight) {
      ta.style.height = "auto";
      ta.style.height = ta.scrollHeight + 2 + "px";
    }
    var n = rpCount(rpNote()), over = n - rp.max;
    setText(count, num(n) + " / " + num(rp.max));
    setClass(count, "rp-count" + (over > 0 ? " is-over" : over > -51 ? " is-near" : ""));
    setText(hint, over > 0 ? tn("ui.report.too_long", over) : t("ui.report.as_written"));
    setClass(hint, over > 0 ? "is-over" : "");
    rpExact();
    rpSync();
  }

  // The result panel. A `code` part of the line becomes a <code>.
  function rpResult(title, line, ok, ref, retry) {
    var el = rpEl("rp-result-line");
    rp.result = { retry: retry };
    rpEl("rp-form").hidden = rpEl("rp-hero").hidden = true;
    rpEl("rp-result").hidden = false;
    rpEl("rp-badge").className = "rp-badge" + (ok ? " is-ok" : "");
    rpEl("rp-badge").innerHTML = icon(ok ? "check" : "question");
    rpEl("rp-result-title").textContent = title;
    el.textContent = "";
    String(line).split("`").forEach(function (part, i) {
      el.appendChild(make(i % 2 ? "code" : "span", i % 2 ? "rp-code" : "")).textContent = part;
    });
    rpEl("rp-ref").textContent = ref || "";
    rpEl("rp-ref-row").hidden = !ref;
    rpEl("rp-copy").textContent = t("ui.btn.copy");
    rpEl("rp-copy-hint").hidden = true;
    rpEl("rp-done").textContent = t(retry ? "ui.btn.try_again" : "ui.btn.done");
    rpSync();
  }

  function rpPanel(key) {
    var p = RP_PANELS[key];
    rpResult(t(p[0]), t(p[1], RP_PANEL_PARAMS[key]), false, null, key === "build");
  }

  function rpRefused(status, d) {
    if (status === 401) {
      handleFetchError({ authFailed: true });
    } else if (status === 403 && d.error === "private_only") {
      rpPanel("group");
    } else if (status === 403) {
      if (settingsData) { settingsData.can_report = false; }
      renderReport();
      rpPanel("owner");
    } else if (status === 409 && d.error === "no_owner") {
      rpPanel("noowner");
    } else {
      return false;
    }
    return true;
  }

  function rpLoad(after) {
    var seq = ++rp.seq, epoch = chatEpoch;
    rp.draft = rp.report = rp.result = null;
    rp.open = {};
    rp.all = false;
    rpEl("rp-form").hidden = rpEl("rp-hero").hidden = false;
    rpEl("rp-result").hidden = true;
    rpEl("rp-body").hidden = true;
    rpStatus("");
    skeleton(rpEl("rp-facts"), 3, "skel-row");
    rpSync();
    function stale() { return seq !== rp.seq || epoch !== chatEpoch; }
    rpPost("/api/report/draft", {}).then(function (r) {
      if (stale()) { return; }
      var d = r.data;
      if (r.status === 200 && d.draft && d.report) {
        rp.draft = d.draft;
        rp.report = d.report;
        rp.areas = d.areas || [];
        rp.max = d.note_max || 500;
        rpFacts();
        rpErrors();
        rpToggles();
        rpEl("rp-body").hidden = false;
        rpOnInput();
        rpStatus(after);
      } else if (!rpRefused(r.status, d)) {
        rpPanel("build");
      }
    }, function () { if (!stale()) { rpPanel("build"); } });
  }

  function openReport() {
    if (rp.noteEpoch !== chatEpoch) { rp.note = ""; }
    rpEl("rp-note").value = rp.note;
    rp.sending = false;
    rp.result = null;
    showView("report");
    rpLoad();
    rpOnInput();
  }

  function rpOutcome(d) {
    var line = d.line ? String(d.line) : t(RP_TRY);
    if (d.retry) { rpStatus(line, 1); return; }
    haptic("notify", d.outcome === "sent" ? "success" : "error");
    rp.draft = null;
    rp.note = "";
    if (d.outcome === "sent") {
      rpResult(t(RP_TITLES.sent), t("ui.report.thanks"), true, d.reference);
    } else {
      rpResult(t(RP_TITLES[d.outcome] || "ui.report.not_sent"), line);
    }
  }

  function rpSend() {
    if (!rpCanSend()) { return; }
    var epoch = chatEpoch;
    var body = { draft: rp.draft, note: rpNote() };
    rp.sending = true;
    rpStatus("");
    rpSync();
    rpPost("/api/report/send", body).then(function (r) {
      if (epoch !== chatEpoch) { return; }
      var d = r.data, s = r.status;
      rp.sending = false;
      rpSync();
      if (s === 200) {
        rpOutcome(d);
      } else if (rpRefused(s, d)) {
        return;
      } else if (s === 410) {
        rpLoad(t("ui.report.stale"));
      } else if (s === 409) {
        rpStatus(t("ui.report.still_sending"));
      } else if (s === 422 && d.error === "note_changed") {
        rpEl("rp-note").value = String(d.note || "");
        rpOnInput();
        rpStatus(t("ui.report.tidied"));
      } else if (s === 422) {
        rpStatus(t("ui.report.bad_note"), 1);
      } else if (s === 429) {
        rpStatus(t("ui.report.too_many"), 1);
      } else {
        rpStatus(t(RP_TRY));
      }
    }, function () {
      if (epoch !== chatEpoch) { return; }
      rp.sending = false;
      rpSync();
      rpStatus(t(RP_LATER));
    });
  }

  function rpDone() {
    if (rp.result && rp.result.retry) { rpLoad(); } else { showGrid(); }
  }

  function rpCopy() {
    var ref = rpEl("rp-ref"), cb = typeof navigator !== "undefined" && navigator.clipboard;
    function fallback() {
      try { window.getSelection().selectAllChildren(ref); } catch (e) { /* no selection API */ }
      rpEl("rp-copy-hint").hidden = false;
    }
    if (!cb || typeof cb.writeText !== "function") { fallback(); return; }
    cb.writeText(ref.textContent).then(function () {
      rpEl("rp-copy").textContent = t("ui.btn.copied");
      haptic("notify", "success");
      showNotice(t("ui.report.copied"), "ok");
    }, fallback);
  }

  rpEl("report-open").addEventListener("click", openReport);
  rpEl("rp-note").addEventListener("input", rpOnInput);
  rpEl("rp-send").addEventListener("click", rpSend);
  rpEl("rp-done").addEventListener("click", rpDone);
  rpEl("rp-copy").addEventListener("click", rpCopy);
  rpEl("rp-more-btn").addEventListener("click", function () { rp.more = !rp.more; rpToggles(); });
  rpEl("rp-exact-btn").addEventListener("click", function () { rp.exact = !rp.exact; rpToggles(); });

  // ---- updates (admin only) -------------------------------------------
  // As /update in chat (8.43): Check, then ONE Update button. The offer
  // comes from the server's update_offer(); nothing is looked up until
  // Check is tapped. Shown only with `can_update`; the server enforces it.

  var updatesData = null;     // GET /api/update payload: { job }
  var updatesCheck = null;    // POST /api/update/check result, or null
  var updatesChecking = false;
  var updatesTimer = null;
  var UPDATE_TERMINAL = { done: 1, failed: 1, cancelled: 1, restart_scheduled: 1 };
  // A running job's step in words, by its phase id (an unknown one shows its id).
  var UPDATE_PHASES = { starting: "ui.update.job.starting",
                        claude_updating: "ui.update.job.claude_updating",
                        upgrading: "ui.update.job.upgrading",
                        verifying: "ui.update.job.verifying" };

  // A dedicated fetch, NOT apiFetch: a 401/403 here means "you are not the
  // admin" and must hide this block - apiFetch would turn it into the
  // terminal "session expired" state for the whole app.
  function updatesFetch(path, method, body) {
    var headers = authHeaders();
    var opts = { method: method || "GET", headers: headers };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (res) {
      if (res.status === 401 || res.status === 403) {
        var err = new Error("forbidden");
        err.forbidden = true;
        throw err;
      }
      return res.json().then(function (data) {
        return { ok: res.ok, status: res.status, data: data };
      });
    });
  }

  function stopUpdatesPoll() {
    if (updatesTimer) { clearTimeout(updatesTimer); updatesTimer = null; }
  }

  function hideUpdates() {
    updatesData = null;
    updatesCheck = null;
    stopUpdatesPoll();
    document.getElementById("updates-block").hidden = true;
  }

  function loadUpdates() {
    stopUpdatesPoll();
    if (!settingsData || !settingsData.can_update) { hideUpdates(); return; }
    var epoch = chatEpoch;
    updatesFetch("/api/update")
      .then(function (r) {
        if (epoch !== chatEpoch) { return; }     // asked in the chat left behind
        if (!r.ok) { return; }
        updatesData = r.data;
        renderUpdates();
        var job = updatesData.job;
        if (job && !UPDATE_TERMINAL[job.phase] && currentView.type === "settings"
            && document.visibilityState !== "hidden") {
          updatesTimer = setTimeout(loadUpdates, 3000);
        }
      })
      .catch(function (err) {
        if (err && err.forbidden) { hideUpdates(); }
      });
  }

  function updateButton(label, onTap, className) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = className || "primary";
    b.textContent = label;
    b.addEventListener("click", onTap);
    return b;
  }

  function confirmThen(message, fn) {
    if (tg && typeof tg.showConfirm === "function") {
      try { tg.showConfirm(message, function (ok) { if (ok) { fn(); } }); return; }
      catch (e) { /* older client: fall through */ }
    }
    if (typeof window.confirm === "function") {
      if (window.confirm(message)) { fn(); }
      return;
    }
    fn();
  }

  function checkUpdates() {
    if (updatesChecking) { return; }
    updatesChecking = true;
    updatesCheck = null;
    renderUpdates();
    var epoch = chatEpoch;
    updatesFetch("/api/update/check", "POST", {})
      .then(function (r) {
        updatesChecking = false;
        // Checked in the chat left behind: drop the answer, but take the
        // "Checking…" state off whatever the new chat's block shows.
        if (epoch !== chatEpoch) { renderUpdates(); return; }
        if (r.ok && r.data && r.data.check) {
          updatesCheck = r.data.check;
          // The check speaks for the installed version now; the banner's
          // hooks notice (no lookup) stays.
          var kept = updatesData && updatesData.waiting;
          updatesData = { job: r.data.job || null,
                          waiting: kept && kept.source === "hooks" ? kept : null };
        } else if (r.status === 409) {
          showNotice(t("ui.update.running"), "err");
          loadUpdates();
        } else if (r.status === 429) {
          showNotice(t("ui.update.too_many"), "err");
        } else if (r.status === 503) {
          showNotice(t("ui.update.shutting_down"), "err");
        } else {
          showNotice(t("ui.update.check_failed"), "err");
        }
        renderUpdates();
      })
      .catch(function (err) {
        updatesChecking = false;
        if (err && err.forbidden) { hideUpdates(); return; }
        showNotice(t("ui.update.unreachable"), "err");
        renderUpdates();
      });
  }

  function postUpdate(action, body) {
    updatesFetch("/api/update/" + encodeURIComponent(action), "POST",
                 body === undefined ? {} : body)
      .then(function (r) {
        if (r.status === 409) {
          var why = (r.data && r.data.error) || "conflict";
          if (why === "check_expired" || why === "offer_changed") {
            showNotice(t("ui.update.stale"), "err");
          } else {
            showNotice(t(why === "update_in_progress"
              ? "ui.update.running" : "ui.update.finished"), "err");
          }
        } else if (r.status === 503 && r.data && r.data.error === "shutting_down") {
          showNotice(t("ui.update.shutting_down"), "err");
        } else if (r.status === 429) {
          showNotice(t("ui.update.too_many"), "err");
        } else if (!r.ok) {
          showNotice(t("ui.update.start_failed"), "err");
        }
        // A started (or refused) update voids the check it came from.
        if (action === "start" || action === "restart") { updatesCheck = null; }
        loadUpdates();
      })
      .catch(function (err) {
        if (err && err.forbidden) { hideUpdates(); return; }
        showNotice(t("ui.update.unreachable"), "err");
      });
  }

  function setLine(id, text) {
    var el = document.getElementById(id);
    el.textContent = text || "";
    el.hidden = !text;
  }

  // ---- the restart after an aipager update (roadmap 8.45) --------------
  // No countdown: a loading state until the NEW daemon answers with the
  // version the job installed. The old daemon goes away mid-watch, so a
  // failed fetch or an error page (the tunnel's 502) means "not back yet".
  // A managed quick tunnel gets a new hostname every run: once the old
  // one is gone this page can never reach the new daemon, so it says so
  // instead of spinning.
  // About 2 minutes, but never before the old daemon's own watchdog (5 s
  // delay + 120 s, update_flow.RESTART_WATCHDOG_SECONDS) can have said the
  // restart did not happen, plus one full poll interval.
  var RESTART_GIVE_UP_MS = 140000;
  var RESTART_POLL_FIRST_MS = 1500;
  var RESTART_POLL_MAX_MS = 8000;
  // Polls, after the new version answered, to wait for its re-adopted count.
  var RESTART_COUNT_POLLS = 3;
  var restartWatch = null;

  function beginRestartWatch(job, data) {
    if (restartWatch && restartWatch.jobId === job.id) { return; }
    stopRestartWatch();
    var inst = job.installed || {};
    restartWatch = {
      jobId: job.id, to: String(inst.to || ""),
      from: String(inst.from || (data && data.version) || ""),
      urlChanges: !!(data && data.url_changes_on_restart),
      startedAt: Date.now(), delay: RESTART_POLL_FIRST_MS, timer: null,
      state: "restarting", version: "", restarted: null, countPolls: 0, downs: 0
    };
    scheduleRestartPoll(restartWatch);
  }

  function stopRestartWatch() {
    if (restartWatch && restartWatch.timer) { clearTimeout(restartWatch.timer); }
    restartWatch = null;
  }

  function scheduleRestartPoll(w) {
    w.timer = setTimeout(function () { restartPoll(w); }, w.delay);
    w.delay = Math.min(Math.round(w.delay * 1.5), RESTART_POLL_MAX_MS);
  }

  function endRestartWatch(w, state) {
    w.state = state;
    w.timer = null;
    renderUpdates();
  }

  function restartPoll(w) {
    if (restartWatch !== w) { return; }
    w.timer = null;
    fetch("/api/update", { method: "GET", cache: "no-store",
                           headers: authHeaders() })
      .then(function (res) {
        if (res.status === 401 || res.status === 403) { return { kind: "refused" }; }
        if (!res.ok) { return { kind: "down" }; }
        return res.json().then(function (d) { return { kind: "up", data: d }; },
                               function () { return { kind: "down" }; });
      }, function () { return { kind: "down" }; })
      .then(function (r) {
        if (restartWatch !== w) { return; }
        // A refused sign-in: the page's initData is too old (5 min).
        if (r.kind === "refused") { endRestartWatch(w, "refused"); return; }
        if (r.kind === "down") {
          // Twice in a row, so one flaky fetch is not taken for the restart.
          w.downs += 1;
          if (w.urlChanges && w.downs >= 2) { endRestartWatch(w, "reopen"); return; }
        } else {
          w.downs = 0;
          if (restartAnswered(w, r.data)) { return; }
        }
        if (Date.now() - w.startedAt >= RESTART_GIVE_UP_MS) {
          endRestartWatch(w, w.state === "updated" ? "updated" : "slow");
          return;
        }
        scheduleRestartPoll(w);
      });
  }

  // True when the watch is over (or handed back to the job view).
  function restartAnswered(w, d) {
    var job = (d && d.job) || null;
    if (job && job.id === w.jobId) {
      if (job.phase === "restart_scheduled") { return false; }   // the old daemon, still up
      // The old daemon gave up on the restart: show what the job says.
      stopRestartWatch();
      updatesData = { job: job };
      renderUpdates();
      return true;
    }
    // A daemon that does not know this job: the new one.
    updatesData = { job: job };
    w.version = String((d && d.version) || "");
    var done = d && d.restarted;
    if (done && (!w.to || done.to === w.to)) { w.restarted = done; }
    var arrived = w.to ? w.version === w.to : (w.version !== "" && w.version !== w.from);
    if (!arrived) { endRestartWatch(w, "other"); return true; }
    if (w.restarted || w.countPolls >= RESTART_COUNT_POLLS) {
      endRestartWatch(w, "updated");
      return true;
    }
    // Back on the new version; its re-adopted count follows a moment later.
    w.countPolls += 1;
    if (w.state !== "updated") { w.state = "updated"; renderUpdates(); }
    return false;
  }

  function restartText(w) {
    if (w.state === "restarting") { return t("ui.update.restarting"); }
    if (w.state === "updated") {
      var version = w.version || w.to;
      var done = w.restarted;
      var text = done && typeof done.readopted === "number"
        ? tn("ui.update.updated_readopted", done.readopted, { version: version })
        : t("ui.update.updated", { version: version });
      if (done && done.missing && done.missing.length) {
        text += "\n" + t("ui.update.not_back", { names: joinList(done.missing) });
      }
      return text;
    }
    if (w.state === "other") {
      var running = w.version || t("ui.update.unknown_version");
      return w.to ? t("ui.update.other_not", { version: running, expected: w.to })
        : t("ui.update.other", { version: running });
    }
    if (w.state === "reopen") { return t("ui.update.reopen", { command: "/app" }); }
    if (w.state === "refused") { return t("ui.update.refused"); }
    return t("ui.update.slow");
  }

  function renderRestartWatch(jobEl, actions) {
    var w = restartWatch;
    jobEl.hidden = false;
    jobEl.innerHTML = "";
    var row = make("div", "restart-row");
    if (w.state === "restarting") {
      row.appendChild(make("span", "lamp lamp-sm lamp-work lamp-spin"));
    }
    var text = make("span", "restart-text");
    text.textContent = plain(restartText(w));
    row.appendChild(text);
    jobEl.appendChild(row);
    var ended = w.state === "reopen" || w.state === "slow" || w.state === "refused";
    if (ended && tg && typeof tg.close === "function") {
      actions.appendChild(updateButton(t("ui.btn.close_app"), function () {
        try { tg.close(); } catch (e) { /* stay open */ }
      }, "updates-again"));
    }
    if (w.state !== "restarting") {
      actions.appendChild(updateButton(t("ui.btn.check_updates"), function () {
        stopRestartWatch();
        checkUpdates();
      }, "updates-again"));
    }
  }

  function renderUpdates() {
    var block = document.getElementById("updates-block");
    if (!updatesData && !updatesChecking && !updatesCheck && !restartWatch) {
      block.hidden = true;
      return;
    }
    block.hidden = false;
    var job = (updatesData && updatesData.job) || null;
    var running = job && !UPDATE_TERMINAL[job.phase];

    var jobEl = document.getElementById("updates-job");
    var actions = document.getElementById("updates-actions");
    var linesEl = document.getElementById("updates-lines");
    actions.innerHTML = "";
    linesEl.innerHTML = "";
    setLine("updates-source", "");
    setLine("updates-restart", "");
    setLine("updates-summary", "");
    setLine("updates-banner", "");
    if (job && job.phase === "restart_scheduled") { beginRestartWatch(job, updatesData); }
    if (restartWatch) { renderRestartWatch(jobEl, actions); return; }
    // Installed and waiting for a restart, or hooks already on a newer
    // aipager: one line, while no job runs.
    var waiting = (updatesData && updatesData.waiting) || null;
    var banner = "";
    if (waiting && !running) {
      banner = waiting.source === "hooks"
        ? t("ui.update.hooks_banner", { version: waiting.version, running: waiting.running })
        : t("ui.update.waiting_banner", { version: waiting.version });
    }
    setLine("updates-banner", banner);
    if (job) {
      jobEl.hidden = false;
      var lines = [];
      if (job.phase === "waiting_for_idle") {
        lines.push(t("ui.update.job.waiting_idle"));
      } else if (job.phase === "gate_timeout") {
        lines.push(t("ui.update.job.gate_timeout"));
      } else if (running) {
        lines.push(UPDATE_PHASES[job.phase] ? t(UPDATE_PHASES[job.phase])
          : t("ui.update.job.working_phase", { phase: job.phase.replace(/_/g, " ") }));
      }
      if (job.summary) { lines.push(plain(job.summary)); }
      (job.blockers || []).forEach(function (b) { lines.push("• " + plain(b)); });
      jobEl.textContent = lines.join("\n");
    } else {
      jobEl.hidden = true;
      jobEl.textContent = "";
    }

    if (running) {
      var id = job.id;
      if (job.phase === "gate_timeout") {
        actions.appendChild(updateButton(t("ui.btn.wait_more"), function () {
          postUpdate("wait-more", { job_id: id });
        }));
      }
      if (job.phase === "waiting_for_idle" || job.phase === "gate_timeout") {
        actions.appendChild(updateButton(t("ui.btn.restart_now"), function () {
          confirmThen(t("ui.update.restart_now_confirm"),
                      function () { postUpdate("restart-now", { job_id: id }); });
        }));
        actions.appendChild(updateButton(t("ui.btn.cancel"), function () {
          postUpdate("cancel", { job_id: id });
        }));
      }
      return;
    }

    if (updatesChecking) {
      var busy = updateButton(t("ui.btn.checking"), function () {});
      busy.disabled = true;
      actions.appendChild(busy);
      return;
    }
    // Restart now, once: from the banner, a check that found the update
    // installed and waiting, or a finished update that offers it.
    var restartOffer = !!banner || !!(updatesCheck && updatesCheck.restart_offer)
      || !!(job && job.phase === "done" && job.can_restart);
    if (restartOffer) {
      actions.appendChild(updateButton(t("ui.btn.restart_now"), function () {
        confirmThen(t("ui.update.restart_confirm"), function () { postUpdate("restart"); });
      }));
    }
    if (!updatesCheck) {
      actions.appendChild(updateButton(t("ui.btn.check_updates"), checkUpdates,
                                       restartOffer ? "updates-again" : undefined));
      return;
    }
    var check = updatesCheck;
    (check.lines || []).forEach(function (line) {
      var row = document.createElement("div");
      row.className = "updates-row";
      row.textContent = plain(line);
      linesEl.appendChild(row);
    });
    setLine("updates-source", plain([check.source].concat(check.notes || [])
      .filter(function (t) { return !!t; }).join("\n")));
    // The restart note comes only with an offered aipager update; the
    // steps to restart by hand only with an installed one waiting.
    setLine("updates-restart", plain(check.restart || check.steps || ""));
    var offer = check.offer;
    if (offer && offer.kind) {
      var kind = offer.kind;
      actions.appendChild(updateButton(offer.label, function () {
        var go = function () { postUpdate("start", { kind: kind }); };
        if (kind === "claude") { go(); return; }
        // What the job will end with: Restart now (in place), the steps
        // to restart by hand (restart_auto false), or its own restart. A
        // payload without restart_auto keeps the automatic text.
        var confirmKey = check.restart_in_place === true ? "ui.update.offer_confirm_manual"
          : check.restart_auto === false ? "ui.update.offer_confirm_steps"
          : "ui.update.offer_confirm";
        confirmThen(t(confirmKey, { offer: offer.label }), go);
      }));
      return;
    }
    setLine("updates-summary", plain(check.summary || t("ui.update.up_to_date")));
    if (!check.restart_offer) {
      actions.appendChild(updateButton(t("ui.btn.check_again"), checkUpdates, "updates-again"));
    }
  }


  // ---- new session ----------------------------------------------------

  var newOptions = null;      // /api/session-options payload
  // prefs: only fields the operator diverged on. folderOpen is NOT `cwd`:
  // the folder is created inside the selection, which must stay a real
  // directory. folderError/folderBusy keep the reveal's note a pure
  // function of state.
  var newState = {
    model: "", cwd: "", mode: "auto", modePicked: false, prefs: {},
    folderOpen: false, folderError: "", folderBusy: false
  };
  // Sentinel for a typed model; chosenModel() resolves it.
  var MODEL_CUSTOM = "custom:free-text";
  // The server's rule (launch._VALID_MODEL), as a courtesy.
  var MODEL_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]*$/;
  var CWD_NEW_FOLDER = "cwd:new-folder";
  // Set when a reveal opens, consumed by the next render: focusing on
  // every render would steal the keyboard.
  var pendingFocus = null;
  // The Model header's value node, updated per keystroke.
  var modelValueNode = null;

  function typedModel() {
    return document.getElementById("new-model-name").value.trim();
  }

  function chosenModel() {
    return newState.model === MODEL_CUSTOM ? typedModel() : newState.model;
  }

  // The Model header shows what was typed, not "Other model".
  function modelValueText() {
    if (newState.model === MODEL_CUSTOM) { return typedModel() || t("ui.new.not_set"); }
    var found = null;
    modelOptions().forEach(function (o) {
      if (o.value === newState.model) { found = o; }
    });
    return found ? found.label : "-";
  }

  function basename(path) {
    return path.split("/").filter(Boolean).pop() || path;
  }

  // The tail of a path identifies it; shown as a field prefix.
  function shortPath(path) {
    var parts = path.split("/").filter(Boolean);
    if (parts.length <= 2) { return path; }
    return "…/" + parts.slice(-2).join("/");
  }

  // Park a reveal before its host is cleared, keeping text and caret.
  function stashNode(id) {
    document.getElementById("node-stash")
      .appendChild(document.getElementById(id));
  }

  function modelOptions() {
    // "" = Claude Code's own default. Aliases carry a hint, not a version:
    // an alias is always the latest of its family.
    var opts = [{ value: "", label: t("ui.new.default"), help: t("ui.new.default_model_help") }];
    ((newOptions && newOptions.models) || []).forEach(function (m) {
      opts.push({ value: m.label, label: m.label, help: plain(m.hint || "") });
    });
    // The CLI accepts an alias or a full name, so the form does too.
    opts.push({
      value: MODEL_CUSTOM, label: t("ui.new.other_model"),
      help: t("ui.new.other_model_help", { example: "claude-opus-5" }), create: true
    });
    return opts;
  }

  function directoryOptions() {
    var dirs = (newOptions && newOptions.directories) || [];
    var defaultDir = (newOptions && newOptions.default_directory) || "";
    var opts = [];
    // The daemon's directory is in `dirs` under its real path; "Default"
    // stays only when there is nothing else.
    if (!dirs.length || !defaultDir) {
      opts.push({ value: "", label: t("ui.new.default"), help: t("ui.new.default_dir_help") });
    }
    dirs.forEach(function (d) {
      opts.push({ value: d, label: basename(d), help: d });
    });
    // Creating needs somewhere to create in, and permission to do it.
    if (dirs.length && newOptions && newOptions.can_create) {
      opts.push({
        value: CWD_NEW_FOLDER, label: t("ui.new.new_folder"),
        help: t("ui.new.new_folder_help"), create: true,
        active: newState.folderOpen
      });
    }
    return opts;
  }

  // Picking a directory or a model: one path for the group rows and the
  // quick chips, so a chip is exactly the same choice as its row.
  function pickCwd(value) {
    newState.cwd = value;
    newState.folderOpen = false;
    renderNewForm();
  }

  function pickModel(value) {
    newState.model = value;
    if (value === MODEL_CUSTOM) { pendingFocus = "new-model-name"; }
    renderNewForm();
  }

  // Directories matching a grid session, most recent first, at most four.
  function recentDirectories() {
    var dirs = (newOptions && newOptions.directories) || [];
    var rows = (lastGridData && lastGridData.sessions) || [];
    var seen = Object.create(null);
    var ranked = [];
    rows.slice().sort(function (a, b) {
      var x = a.last_active_seconds_ago;
      var y = b.last_active_seconds_ago;
      return (x === null || x === undefined ? Infinity : x) -
             (y === null || y === undefined ? Infinity : y);
    }).forEach(function (row) {
      dirs.forEach(function (d) {
        if (!seen[d] && row.project && basename(d) === row.project) {
          seen[d] = true;
          ranked.push(d);
        }
      });
    });
    return ranked.slice(0, 4);
  }

  function buildChips(host, caption, items) {
    host.innerHTML = "";
    host.hidden = !items.length;
    if (!items.length) { return; }
    var cap = make("span", "chips-cap");
    cap.textContent = caption;
    host.appendChild(cap);
    items.forEach(function (it) {
      var chip = make("button", "chip" + (it.active ? " is-active" : ""));
      chip.setAttribute("aria-pressed", it.active ? "true" : "false");
      if (it.hint) {
        chip.setAttribute("aria-label", it.label + ", " + it.hint);
        chip.setAttribute("title", it.hint);
      }
      chip.innerHTML = icon(it.icon);
      var main = make("span");
      main.textContent = it.label;
      main.setAttribute("dir", "ltr");     // a folder's or a model's name
      chip.appendChild(main);
      if (it.sub) {
        var sub = make("span", "chip-sub");
        sub.textContent = it.sub;
        chip.appendChild(sub);
      }
      chip.addEventListener("click", function () {
        haptic("select");
        it.pick();
      });
      host.appendChild(chip);
    });
  }

  // Quick chips for where and which model, from data already here.
  function renderChips() {
    buildChips(document.getElementById("new-cwd-chips"), t("ui.new.chips_recent"),
      recentDirectories().map(function (d) {
        return { icon: "folder", label: basename(d), active: newState.cwd === d,
                 pick: function () { pickCwd(d); } };
      }));
    buildChips(document.getElementById("new-model-chips"), t("ui.new.chips_suggested"),
      ((newOptions && newOptions.models) || []).filter(function (m) { return !!m.hint; })
        .map(function (m) {
          return { icon: "spark", label: m.label, hint: plain(m.hint),
                   active: newState.model === m.label,
                   pick: function () { pickModel(m.label); } };
        }));
  }

  // ---- structural render: rebuilds the option groups ------------------
  //
  // Split from refreshNewForm: typing once rebuilt every group.
  function renderNewForm() {
    var modelOpts = modelOptions();
    var isCustomModel = newState.model === MODEL_CUSTOM;
    var models = document.getElementById("new-model");
    stashNode("new-model-reveal");
    models.innerHTML = "";
    modelValueNode = renderOptionGroup(models, {
      key: "new:model",
      title: t("ui.model.title"),
      options: modelOpts,
      ltr: true,
      current: newState.model,
      // What was typed, not the label of the row that let them type it.
      valueText: modelValueText(),
      reveal: isCustomModel
        ? { after: MODEL_CUSTOM, node: document.getElementById("new-model-reveal") }
        : null,
      rerender: renderNewForm,
      onPick: pickModel
    }).value;

    // The server's allow-list, the one validate_cwd checks.
    var dirOpts = directoryOptions();
    var dirs = document.getElementById("new-cwd");
    stashNode("new-folder-reveal");
    dirs.innerHTML = "";
    var currentDir = null;
    dirOpts.forEach(function (o) {
      if (o.value === newState.cwd) { currentDir = o; }
    });
    renderOptionGroup(dirs, {
      key: "new:cwd",
      title: t("ui.new.cwd_title"),
      options: dirOpts,
      ltr: true,
      current: newState.cwd,
      // The tag marks the daemon's own directory.
      defaultValue: (newOptions && newOptions.default_directory) || undefined,
      valueText: currentDir ? currentDir.label : "-",
      reveal: newState.folderOpen
        ? { after: CWD_NEW_FOLDER, node: document.getElementById("new-folder-reveal") }
        : null,
      rerender: renderNewForm,
      onPick: function (value) {
        if (value === CWD_NEW_FOLDER) {
          // An action: the selection stays, as the parent for the new folder.
          newState.folderOpen = !newState.folderOpen;
          if (newState.folderOpen) { pendingFocus = "new-folder-name"; }
          renderNewForm();
          return;
        }
        pickCwd(value);
      }
    });

    // Ask and Auto are everyone's; Full access is for admins.
    var canFull = !!(newOptions && newOptions.can_use_full);
    var modes = document.getElementById("new-mode");
    modes.innerHTML = "";
    renderOptionGroup(modes, {
      key: "new:mode",
      title: t("ui.new.mode_title"),
      options: [
        { value: "ask", label: t("ui.new.mode_ask"), help: t("ui.new.mode_ask_help") },
        { value: "auto", label: t("ui.new.mode_auto"), help: t("ui.new.mode_auto_help") },
        { value: "full", label: t("ui.new.mode_full"),
          help: t(canFull ? "ui.new.mode_full_help" : "ui.new.requires_admin") }
      ],
      current: newState.mode,
      rerender: renderNewForm,
      onPick: function (value) {
        if (value === "full" && !canFull) { return; }
        newState.mode = value;
        newState.modePicked = true;
        renderNewForm();
      }
    });
    document.getElementById("new-mode-note").hidden = canFull;

    // Reply-style settings, each tagged with the chat default.
    var prefsEl = document.getElementById("new-prefs");
    prefsEl.innerHTML = "";
    var schema = (newOptions && newOptions.schema) || [];
    var scopeDefaults = (newOptions && newOptions.scope_defaults) || {};
    schema.forEach(function (group) {
      var chosen = Object.prototype.hasOwnProperty.call(newState.prefs, group.field)
        ? newState.prefs[group.field]
        : scopeDefaults[group.field];
      renderOptionGroup(prefsEl, {
        key: "new:" + group.field,
        title: group.title,
        options: group.options,
        current: chosen,
        defaultValue: scopeDefaults[group.field],
        rerender: renderNewForm,
        onPick: function (value) {
          if (value === scopeDefaults[group.field]) {
            delete newState.prefs[group.field];    // back to inheriting
          } else {
            newState.prefs[group.field] = value;
          }
          renderNewForm();
        }
      });
    });

    renderChips();
    refreshNewForm();

    if (pendingFocus) {
      var focusEl = document.getElementById(pendingFocus);
      pendingFocus = null;
      if (focusEl && focusEl.focus) {
        try { focusEl.focus(); } catch (e) { /* older webview */ }
      }
    }
  }

  // ---- targeted refresh: no DOM rebuilt, safe to run per keystroke ----
  function refreshNewForm() {
    var canCreate = !!(newOptions && newOptions.can_create);
    var isCustomModel = newState.model === MODEL_CUSTOM;
    var typed = typedModel();
    var modelBad = isCustomModel && !!typed && !MODEL_RE.test(typed);

    // A disabled Create always shows a reason beside the field.
    if (modelValueNode) { modelValueNode.textContent = modelValueText(); }

    var modelNote = document.getElementById("new-model-note");
    if (modelBad) {
      modelNote.textContent = t("ui.new.model_pattern");
      modelNote.className = "reveal-note is-error";
    } else if (isCustomModel && !typed) {
      modelNote.textContent = t("ui.new.model_needed");
      modelNote.className = "reveal-note";
    } else {
      modelNote.textContent = "";
      modelNote.className = "reveal-note";
    }

    // The parent shown inside the field IS the current selection, so the
    // two cannot disagree.
    document.getElementById("new-folder-prefix").textContent =
      newState.cwd ? shortPath(newState.cwd) + "/" : "";
    var folderRaw = document.getElementById("new-folder-name").value;
    var folderInvalid = folderNameError(folderRaw);
    var folderBtn = document.getElementById("new-folder-create");
    folderBtn.disabled = !folderNameTrim(folderRaw) || !!folderInvalid || !newState.cwd ||
      !canCreate || newState.folderBusy;
    paintFolderBusy();
    // Read only, not disabled (keeps focus), while busy.
    document.getElementById("new-folder-name").readOnly = newState.folderBusy;

    // "Default" has no path, so it cannot be a parent: say so rather
    // than open a reveal onto a dead button.
    var folderNote = document.getElementById("new-folder-note");
    if (!newState.cwd) {
      folderNote.textContent = t("ui.new.pick_dir_first");
      folderNote.className = "reveal-note is-live";
    } else if (!canCreate) {
      folderNote.textContent = t("ui.new.folder_forbidden");
      folderNote.className = "reveal-note is-live";
    } else if (newState.folderError) {
      folderNote.textContent = newState.folderError;
      folderNote.className = "reveal-note is-live is-error";
    } else if (folderInvalid) {
      folderNote.textContent = folderInvalid;
      folderNote.className = "reveal-note is-live is-error";
    } else {
      folderNote.textContent = "";
      folderNote.className = "reveal-note is-live";
    }

    var name = document.getElementById("new-name").value.trim();
    var where = newState.cwd ? newState.cwd : t("ui.new.daemon_dir");
    // The summary must show a typed model: the one setting a keystroke
    // can get wrong unseen. One whole sentence per name and mode.
    var model = chosenModel();
    var summaries = name
      ? { ask: "ui.new.summary_named_ask", auto: "ui.new.summary_named_auto",
          full: "ui.new.summary_named_full" }
      : { ask: "ui.new.summary_ask", auto: "ui.new.summary_auto",
          full: "ui.new.summary_full" };
    document.getElementById("new-summary").textContent = t(
      summaries[newState.mode] || summaries.auto,
      { name: name, where: where, model: model || t("ui.new.cli_model") });

    var create = document.getElementById("new-create");
    create.disabled = submitting || !name || !canCreate ||
      (isCustomModel && (!typed || modelBad));
    syncMainButton();
  }

  // "Creating…" until the answer; applyStatic() repaints it too.
  function paintFolderBusy() {
    var folderBtn = document.getElementById("new-folder-create");
    folderBtn.textContent = t(newState.folderBusy ? "ui.new.creating" : "ui.new.create_folder");
    folderBtn.setAttribute("aria-busy", newState.folderBusy ? "true" : "false");
  }

  function openNewSession() {
    showView("new");
    document.getElementById("new-name-error").hidden = true;
    if (!newOptions) {
      skeleton(document.getElementById("new-model"), 1, "skel-row");
    }
    renderNewForm();
    var epoch = chatEpoch;
    apiFetch("/api/session-options")
      .then(function (data) {
        if (epoch !== chatEpoch) { return; }     // for the chat left behind
        newOptions = data;
        // Start on the mode /new would give this person (the chat's New
        // sessions setting, Auto unless it says otherwise), until they
        // pick one themselves.
        if (!newState.modePicked) {
          newState.mode = data.default_mode || "auto";
        }
        if (newState.mode === "full" && !data.can_use_full) { newState.mode = "auto"; }
        // Select the directory a session would use anyway, so New folder
        // always has a parent.
        if (!newState.cwd && data.default_directory) {
          newState.cwd = data.default_directory;
        }
        renderNewForm();
      })
      .catch(handleFetchError);
  }

  // Client-side checks mirror launch.validate_new_dir_name as a courtesy
  // (one path segment of Latin letters, digits, dots, hyphens and
  // underscores, starting with a letter or digit, after trimming); the
  // server is the gate. tests/test_miniapp_create_folder.py fails if the
  // two drift.
  var FOLDER_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
  var FOLDER_NAME_MAX = 64;
  // What Python's str.strip() removes (str.isspace), which differs from
  // JavaScript's trim(): Python strips \x1c-\x1f and \x85 and keeps \ufeff.
  var PY_SPACE = "\t\n\x0b\x0c\r\x1c-\x1f \x85\xa0\u1680\u2000-\u200a\u2028\u2029" +
    "\u202f\u205f\u3000";
  var PY_STRIP_RE = new RegExp("^[" + PY_SPACE + "]+|[" + PY_SPACE + "]+$", "g");

  function folderNameTrim(value) { return (value || "").replace(PY_STRIP_RE, ""); }

  // The reason a typed folder name would be refused, or "" (an empty name
  // has no reason: the button just waits for one).
  function folderNameError(value) {
    var trimmed = folderNameTrim(value);
    if (!trimmed) { return ""; }
    // Code points, as Python's len().
    if (Array.from(trimmed).length > FOLDER_NAME_MAX) {
      return t("ui.new.folder_too_long", { max: FOLDER_NAME_MAX });
    }
    if (!FOLDER_NAME_RE.test(trimmed)) { return t("ui.new.folder_pattern"); }
    return "";
  }

  // One request at a time: the button, Enter and a quick second tap all
  // come here, and nothing goes out while one is running. Every answer,
  // including a body that is not JSON and no answer at all, ends the busy
  // state, keeps the typed name and says why.
  function createFolder() {
    var nameEl = document.getElementById("new-folder-name");
    var folder = folderNameTrim(nameEl.value);
    // The button's gate.
    if (newState.folderBusy || !folder || !newState.cwd || folderNameError(folder) ||
        !(newOptions && newOptions.can_create)) {
      return;
    }
    newState.folderError = "";
    newState.folderBusy = true;
    refreshNewForm();
    fetch("/api/directories", {
      method: "POST",
      headers: authHeaders(true),
      body: JSON.stringify({ parent: newState.cwd, name: folder })
    }).then(function (res) {
      return res.json().then(function (data) { return data; }, function () { return null; })
        .then(function (data) { return { status: res.status, data: data }; });
    }).then(function (r) {
      newState.folderBusy = false;
      if (r.status !== 200 || !r.data || !r.data.path) {
        newState.folderError =
          (r.status === 403 && t("ui.new.folder_forbidden")) ||
          (r.status === 429 && t("ui.new.folder_too_many")) ||
          plain((r.data && r.data.detail) || "") ||
          t("ui.new.folder_failed");
        refreshNewForm();
        return;
      }
      // The server has just sanctioned this path inside an allowed root, so
      // Create will accept it; adding it locally shows it selected.
      var path = r.data.path;
      if (!newOptions) { newOptions = {}; }
      if (!newOptions.directories) { newOptions.directories = []; }
      if (newOptions.directories.indexOf(path) === -1) {
        newOptions.directories.push(path);
      }
      newState.cwd = path;
      newState.folderOpen = false;
      newState.folderError = "";
      // Only the sent name.
      if (folderNameTrim(nameEl.value) === folder) { nameEl.value = ""; }
      showNotice(t(r.data.existed ? "ui.new.folder_existed" : "ui.new.folder_created"), "ok");
      renderNewForm();
    }).catch(function () {
      newState.folderBusy = false;
      newState.folderError = t("ui.new.folder_unreachable");
      refreshNewForm();
    });
  }

  function submitNewSession() {
    var nameEl = document.getElementById("new-name");
    var errEl = document.getElementById("new-name-error");
    var create = document.getElementById("new-create");
    var name = nameEl.value.trim();
    if (!name) { return; }
    errEl.hidden = true;
    create.disabled = true;
    submitting = true;
    syncMainButton();

    fetch("/api/sessions", {
      method: "POST",
      headers: authHeaders(true),
      body: JSON.stringify({
        name: name,
        model: chosenModel(),
        cwd: newState.cwd,
        mode: newState.mode
      })
    }).then(function (res) {
      return res.json().then(function (data) { return { status: res.status, data: data }; });
    }).then(function (r) {
      if (r.status === 200) {
        nameEl.value = "";
        if (tg && tg.HapticFeedback) {
          try { tg.HapticFeedback.notificationOccurred("success"); } catch (e) { /* old client */ }
        }
        // Say so in words, not just a haptic (nothing on a device with
        // haptics off). The toast is fixed, so it survives the navigation.
        showNotice(t("ui.new.created"), "ok");
        // Chosen settings become per-session overrides via the existing route.
        var label = r.data.label;
        // Await the overrides first: the session page loads settings once, so
        // racing them would show scope defaults for what was just chosen.
        var writes = Object.keys(newState.prefs).map(function (field) {
          return fetch("/api/sessions/" + encodeURIComponent(label) +
                "/preferences/" + encodeURIComponent(field), {
            method: "PUT",
            headers: authHeaders(true),
            body: JSON.stringify({ value: newState.prefs[field] })
          }).catch(function () { /* session exists; page will show truth */ });
        });
        newState.prefs = {};
        Promise.all(writes).then(function () {
          submitting = false;
          openDetail(label);
        });
        return;
      }
      // A name collision or rejected directory is answered beside its field.
      errEl.textContent = plain((r.data && r.data.detail) || "") ||
        t(r.status === 403 ? "ui.new.create_forbidden" : "ui.new.create_failed");
      errEl.hidden = false;
      submitting = false;
      create.disabled = false;
      syncMainButton();
    }).catch(function () {
      errEl.textContent = t("ui.new.create_unreachable");
      errEl.hidden = false;
      submitting = false;
      create.disabled = false;
      syncMainButton();
    });
  }

  // Typing runs the targeted refresh only, never renderNewForm.
  document.getElementById("new-name").addEventListener("input", refreshNewForm);
  document.getElementById("new-model-name").addEventListener("input", refreshNewForm);
  document.getElementById("new-folder-name").addEventListener("input", function () {
    newState.folderError = "";       // typing IS the retry
    refreshNewForm();
  });
  document.getElementById("new-folder-name").addEventListener("keydown", function (e) {
    // Enter is the commit for a one-field reveal; the button is for thumbs.
    if (e && (e.key === "Enter" || e.keyCode === 13)) {
      if (e.preventDefault) { e.preventDefault(); }
      createFolder();
    }
  });
  document.getElementById("new-folder-create").addEventListener("click", createFolder);
  document.getElementById("new-create").addEventListener("click", submitNewSession);
  document.getElementById("new-advanced-toggle").addEventListener("click", function () {
    var adv = document.getElementById("new-advanced");
    var btn = document.getElementById("new-advanced-toggle");
    adv.hidden = !adv.hidden;
    btn.setAttribute("aria-expanded", adv.hidden ? "false" : "true");
    btn.classList.toggle("is-open", !adv.hidden);
  });
  document.getElementById("new-session-btn").addEventListener("click", openNewSession);
  document.getElementById("detail-quick").addEventListener("click", function () {
    var key = this.getAttribute("data-action");
    if (key && currentView.label) { runDetailAction(currentView.label, key); }
  });
  document.getElementById("detail-activity").addEventListener("click", function () {
    if (!timelineOpen) { toggleTimeline(); }
    var tl = document.getElementById("tab-timeline");
    if (tl.scrollIntoView) {
      try { tl.scrollIntoView({ behavior: "smooth", block: "start" }); } catch (e) { /* old webview */ }
    }
  });
  document.getElementById("detail-answer").addEventListener("click", function () {
    if (currentView.label) { answerPrompt(currentView.label); }
  });
  document.getElementById("empty-new").addEventListener("click", openNewSession);

  // ---- top-level tabs -------------------------------------------------

  var mainTab = "sessions";

  function setMainTab(name) {
    mainTab = name;
    // Switching tabs from inside a sub-page returns to the top level -
    // the back button is for going back, the tab bar is for switching.
    showView(name === "settings" ? "settings" : "grid");
    document.getElementById("maintab-sessions")
      .classList.toggle("is-active", name === "sessions");
    document.getElementById("maintab-settings")
      .classList.toggle("is-active", name === "settings");
    if (name === "sessions") { pollTick(); } else { loadSettings(); }
  }

  if (tg && tg.BackButton) {
    // Back closes an open layer before navigating: backing out of a
    // dialog must not drop the operator on the grid.
    tg.BackButton.onClick(function () {
      // Leaving mid-send would lose the outcome (the reference).
      if (currentView.type === "report" && rp.sending) {
        showNotice(t("ui.report.sending_wait"), "info");
        return;
      }
      if (overlayCloser) { overlayCloser(); return; }
      showGrid();
    });
  }

  document.getElementById("detail-menu-btn").addEventListener("click", function () {
    if (overlayCloser) { closeOverlay(); return; }   // tapping ⋮ again closes
    openActionMenu();
  });
  // The backdrop dismisses; taps inside a layer stop propagation.
  document.getElementById("overlay").addEventListener("click", closeOverlay);
  document.getElementById("action-menu").addEventListener("click", function (e) {
    if (e && e.stopPropagation) { e.stopPropagation(); }
  });
  document.getElementById("confirm-modal").addEventListener("click", function (e) {
    if (e && e.stopPropagation) { e.stopPropagation(); }
  });
  document.getElementById("confirm-cancel").addEventListener("click", closeOverlay);
  document.getElementById("confirm-ok").addEventListener("click", onConfirmTap);
  document.getElementById("confirm-rename-input")
    .addEventListener("input", refreshRenameValidity);
  document.getElementById("confirm-rename-input").addEventListener("keydown", function (e) {
    // Enter is the commit for a one-field dialog, same precedent as the
    // new-folder-name field.
    if (e && (e.key === "Enter" || e.keyCode === 13)) {
      if (e.preventDefault) { e.preventDefault(); }
      if (!document.getElementById("confirm-ok").disabled) { onConfirmTap(); }
    }
  });
  document.addEventListener("keydown", function (e) {
    if (overlayCloser && e && e.key === "Escape") { closeOverlay(); }
  });
  document.getElementById("maintab-sessions")
    .addEventListener("click", function () { setMainTab("sessions"); });
  document.getElementById("maintab-settings")
    .addEventListener("click", function () { setMainTab("settings"); });
  document.getElementById("gone-toggle").addEventListener("click", function () {
    goneCollapsed = !goneCollapsed;
    if (lastGridData) { renderGrid(lastGridData); }
  });
  document.getElementById("tab-timeline").addEventListener("click", toggleTimeline);
  document.getElementById("tab-diff").addEventListener("click", toggleDiff);
  document.getElementById("session-settings-reset").addEventListener("click", function () {
    // No undo, so it gets the destructive actions' confirmation.
    openConfirm({
      title: t("ui.confirm.reset"),
      body: t("ui.confirm.reset_body"),
      confirmLabel: t("ui.btn.reset"),
      onConfirm: resetSessionSettings
    });
  });

  // Ages tick on their own so "2m ago" does not sit stale between polls.
  setInterval(function () {
    if (document.visibilityState === "visible") { tickAges(); }
  }, 15000);

  // ---- chat switcher (roadmap 8.73) ----------------------------------
  // Shown only to someone in more than one chat; with one chat the page
  // looks exactly as before. Names only (the chat's label), never ids.
  var chatList = null;      // [{scope, label}] once /api/chats answered
  var chatsLoading = false;
  var chatEpoch = 0;        // bumped on a switch: answers for the old chat are dropped
  // Someone who picked a chat before: the first poll waits for /api/chats
  // so the default chat never flashes up first. Nobody else waits (a
  // one-chat install never stores a choice), so their first poll goes
  // out at once, as it always did.
  var waitForChats = !!readSavedScope() || !!readLang();

  function loadChats() {
    if (chatsLoading) { return; }
    chatsLoading = true;
    apiFetch("/api/chats").then(function (data) {
      chatsLoading = false;
      var list = data && Array.isArray(data.chats) ? data.chats : [];
      var current = data && typeof data.current === "string" ? data.current : "";
      var saved = readSavedScope();
      var pick = current;
      list.forEach(function (c) { if (c && c.scope === saved) { pick = saved; } });
      // A remembered chat they have left: forget it, so later loads stop
      // waiting for the chat list before their first poll.
      if (saved && pick !== saved) { saveScope(""); }
      var waited = waitForChats;
      chatList = list;
      chosenScope = pick;
      waitForChats = false;
      renderChatSwitcher();
      var go = function () {
        // A remembered chat that is not the server's default: anything
        // fetched before this answer was for the default chat.
        if (pick !== current) {
          resetChatState();
          refreshChatView();
        } else if (waited) {
          pollTick();
        }
      };
      // The viewer's language (multilingual phase 4): none for English, so
      // an English viewer's page goes on exactly as before (a language this
      // phone remembers is forgotten). A remembered language waits for its
      // catalog, so the grid never flashes English.
      var ui = data && data.ui;
      if (ui || L.code !== "en" || readLang()) {
        var switched = setUi(ui);
        if (waited) { switched.then(go, go); return; }
      }
      go();
    }).catch(function (err) {
      chatsLoading = false;
      handleFetchError(err);
    });
  }

  function chatSwitcherShown() {
    return !!(chatList && chatList.length > 1);
  }

  function renderChatSwitcher() {
    var host = document.getElementById("chat-switch");
    if (!chatSwitcherShown()) { host.hidden = true; return; }
    setAttr(host, "aria-label", t("ui.shell.chat_aria"));
    host.innerHTML = "";
    chatList.forEach(function (c) {
      var btn = make("button", "chat-chip" + (c.scope === chosenScope ? " is-active" : ""));
      btn.type = "button";
      btn.setAttribute("dir", "auto");   // a chat's name, in its own script
      btn.setAttribute("aria-pressed", c.scope === chosenScope ? "true" : "false");
      btn.textContent = plain(c.label);
      btn.addEventListener("click", function () { switchChat(c.scope); });
      host.appendChild(btn);
    });
    var spec = VIEWS[currentView.type];
    host.hidden = !(spec && spec.topLevel);
  }

  function switchChat(scope) {
    if (!scope || scope === chosenScope) { return; }
    chosenScope = scope;
    saveScope(scope);
    haptic("select");
    resetChatState();
    renderChatSwitcher();
    refreshChatView();
  }

  // Everything the page holds about the chat on screen.
  function resetChatState() {
    chatEpoch += 1;
    lastStatuses = Object.create(null);
    lastSessionsByLabel = Object.create(null);
    lastGridData = null;
    gridSig = null;
    tileMap = Object.create(null);
    beaconMap = Object.create(null);
    shelfMap = Object.create(null);
    document.getElementById("needs-you-list").innerHTML = "";
    document.getElementById("sessions-gone").innerHTML = "";
    document.getElementById("needs-you").hidden = true;
    document.getElementById("gone-wrap").hidden = true;
    document.getElementById("waiting-badge").hidden = true;
    lastDetailData = null;
    settingsData = null;
    newOptions = null;
    newState.cwd = "";
    newState.mode = "auto";
    newState.modePicked = false;
    hideUpdates();
    hideReport();
  }

  function refreshChatView() {
    closeOverlay();
    if (mainTab === "settings") {
      showView("settings");
      loadSettings();
    } else {
      showView("grid");
      showGridSkeleton();
      pollTick();
    }
  }

  // ---- the page's language: static text, switching, remembering --------

  // Static strings that hold a command (the command never translated).
  var STATIC_PARAMS = { "ui.empty.text": { command: "/new" } };

  // Every element the markup marks: its text, its aria-label, its
  // placeholder, or (data-i18n-num) a number in the page's digits.
  function applyStatic() {
    var nodes = document.querySelectorAll(
      "[data-i18n],[data-i18n-label],[data-i18n-placeholder],[data-i18n-num]");
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      var key = node.getAttribute("data-i18n-num");
      if (key) { setText(node, num(key)); }
      key = node.getAttribute("data-i18n");
      if (key) { setText(node, t(key, STATIC_PARAMS[key])); }
      key = node.getAttribute("data-i18n-label");
      if (key) { setAttr(node, "aria-label", t(key, STATIC_PARAMS[key])); }
      key = node.getAttribute("data-i18n-placeholder");
      if (key) { setAttr(node, "placeholder", t(key, STATIC_PARAMS[key])); }
    }
    // "Loading..." in the page's language until the grid writes the
    // version line (and its dir) there.
    setAttr(document.getElementById("daemon-line"), "dir", "auto");
    paintFolderBusy();
  }

  // The last language other than English, kept on this phone so a later
  // open waits for it instead of flashing English. Storage can be missing
  // or throw; then the page just starts in English.
  function readLang() {
    try { return window.localStorage.getItem(LANG_KEY) || ""; } catch (e) { return ""; }
  }
  function rememberLang(code) {
    try {
      if (code) {
        window.localStorage.setItem(LANG_KEY, code);
      } else if (readLang()) {
        window.localStorage.removeItem(LANG_KEY);
      }
    } catch (e) { /* no storage */ }
  }

  var langSeq = 0;           // the latest switch wins
  var langLoading = "";      // "code|version" whose catalog is on its way
  var langPromise = null;

  // ui: the server's {code, rtl, v}, or nothing for English. Resolves once
  // the page shows that language (or kept its own, when the catalog could
  // not be had: English is a complete fallback).
  function setUi(ui) {
    if (!ui || !ui.code || ui.code === "en") {
      langSeq += 1;
      langLoading = "";
      rememberLang("");
      if (L.code !== "en") {
        L = englishLang();
        applyLang();
      }
      return Promise.resolve();
    }
    var version = String(ui.v || "");
    if (ui.code === L.code && version === L.v) { return Promise.resolve(); }
    var want = ui.code + "|" + version;
    if (langLoading === want && langPromise) { return langPromise; }
    var seq = ++langSeq;
    langLoading = want;
    langPromise = apiFetch("/api/i18n/" + encodeURIComponent(ui.code) + ".json?v=" +
                           encodeURIComponent(version))
      .then(function (cat) {
        if (seq !== langSeq) { return; }
        langLoading = "";
        if (!cat || cat.code !== ui.code) { return; }
        L = langFrom(cat, version);
        rememberLang(cat.code);
        applyLang();
      }, function () {
        if (seq === langSeq) { langLoading = ""; }
      });
    return langPromise;
  }

  function applyLang() {
    paintLang();
    rerender();
  }

  // <html lang dir> and the marked strings in the page's language.
  function paintLang() {
    var root = document.documentElement;
    if (root && root.setAttribute) {
      root.setAttribute("lang", L.code);
      root.setAttribute("dir", L.rtl ? "rtl" : "ltr");
    }
    applyStatic();
  }

  // What is on screen, again in the page's language. Only Settings asks
  // the server again (its schema and words are the server's); everything
  // else redraws from what the page holds.
  function rerender() {
    if (overlayCloser) { closeOverlay(); }
    if (connState) { setConnState(connState); }
    // The grid's nodes carry their words: build them again.
    gridSig = null;
    tileMap = Object.create(null);
    beaconMap = Object.create(null);
    shelfMap = Object.create(null);
    if (!gridSkeleton) {
      document.getElementById("sessions").innerHTML = "";
      document.getElementById("grid-totals").setAttribute("data-key", "");
    }
    document.getElementById("needs-you-list").innerHTML = "";
    document.getElementById("sessions-gone").innerHTML = "";
    if (lastGridData) { renderGrid(lastGridData); }
    detailSig = Object.create(null);
    if (currentView.type === "detail" && lastDetailData) {
      renderDetailData(lastDetailData);
      if (diffOpen && lastDiffData) { renderDiff(lastDiffData); }
    }
    if (sessionSettingsData) { renderSessionSettings(); }
    if (currentView.type === "new") { renderNewForm(); }
    if (settingsData) { renderSettings(); }
    if (currentView.type === "settings") { loadSettings(); }
    renderUpdates();
    if (rp.report) { rpFacts(); rpErrors(); rpToggles(); rpOnInput(); }
    if (rp.result) {
      rpEl("rp-copy").textContent = t("ui.btn.copy");
      rpEl("rp-done").textContent = t(rp.result.retry ? "ui.btn.try_again" : "ui.btn.done");
    }
    rpSync();
    renderChatSwitcher();
    mainKey = "";
    syncMainButton();
  }

  // ---- polling loop -------------------------------------------------

  function pollTick() {
    if (authExpired) { return; }
    if (document.visibilityState !== "visible") { return; }
    if (!chatList) {
      loadChats();                 // asked again after a failed first try
      if (waitForChats) { return; }
    }
    // What a view polls is declared in VIEWS; polls:null (Settings, the
    // new form) polls nothing.
    var spec = VIEWS[currentView.type];
    var mode = spec ? spec.polls : null;
    if (mode === null || mode === undefined) { return; }
    if (mode === "detail" && !currentView.label) { return; }

    if (mode === "grid") {
      var epoch = chatEpoch;
      apiFetch("/api/sessions")
        .then(function (data) {
          if (epoch !== chatEpoch) { return; }   // for the chat left behind
          lastSuccessAt = Date.now();
          consecutiveFailures = 0;
          setConnState("live");
          renderGrid(data);
        })
        .catch(handleFetchError);
    } else {
      apiFetch("/api/sessions/" + encodeURIComponent(currentView.label))
        .then(function (data) {
          lastSuccessAt = Date.now();
          consecutiveFailures = 0;
          setConnState("live");
          renderDetailData(data);
        })
        .catch(function (err) {
          if (err && err.httpStatus === 404) {
            // Left the caller's scope: back to the grid, not a connectivity error.
            showGrid();
            return;
          }
          handleFetchError(err);
        });
    }
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") { pollTick(); }
  });

  // The link's language: the markup is already in it; this keeps the
  // page's own state the same as after any switch, and this phone
  // remembers it too.
  if (L.code !== "en") {
    paintLang();
    rememberLang(L.code);
  }

  if (!initData) {
    document.getElementById("daemon-line").textContent = "";
    showFatal(t("ui.conn.no_init"));
    return;
  }

  showGridSkeleton();
  syncChrome();
  pollTick();
  pollTimer = setInterval(pollTick, POLL_INTERVAL_MS);
})();
"""

__all__ = ["APP_JS"]
