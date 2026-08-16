"""The Mini App shell — one self-contained HTML string.

Stage 1 is a single read-only page, so it lives as a plain Python
string constant here rather than split `.html`/`.js`/`.css` files —
`packages = ["aipager"]` (pyproject.toml) ships it automatically with
zero packaging changes. See design.md's "Static assets" section for
why split files are deferred to a later stage.

The page has no secrets and needs none baked in: it fetches
``Telegram.WebApp.initData`` client-side (the Telegram Web App JS SDK
supplies it once the page has loaded inside the Telegram WebView) and
sends it as the ``X-Telegram-Init-Data`` header on every ``/api/status``
call. Unauthenticated by necessity — see design.md's threat model item 2.
"""

from __future__ import annotations

INDEX_HTML = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>aipager</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
  :root { color-scheme: light dark; }
  body {
    margin: 0;
    padding: 16px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--tg-theme-bg-color, #ffffff);
    color: var(--tg-theme-text-color, #000000);
  }
  h1 { font-size: 1.1rem; margin: 0 0 4px; }
  .muted { color: var(--tg-theme-hint-color, #888888); font-size: 0.85rem; }
  .card {
    border: 1px solid var(--tg-theme-hint-color, #e0e0e0);
    border-radius: 10px;
    padding: 12px 14px;
    margin-top: 12px;
  }
  .row { display: flex; justify-content: space-between; padding: 4px 0; }
  .row .label { font-weight: 600; }
  .status { text-transform: capitalize; }
  .status-busy { color: #d97706; }
  .status-idle { color: #16a34a; }
  .status-interactive { color: #2563eb; }
  .status-gone { color: #dc2626; }
  .status-unknown { color: var(--tg-theme-hint-color, #888888); }
  #error { color: #dc2626; margin-top: 12px; display: none; }
</style>
</head>
<body>
<h1>aipager</h1>
<div class="muted" id="daemon-line">Loading…</div>

<div id="sessions"></div>
<div id="error"></div>

<script>
(function () {
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) { tg.ready(); tg.expand(); }
  const initData = tg ? tg.initData : "";

  function statusClass(s) { return "status status-" + s; }

  function renderSession(s) {
    const ctx = (typeof s.context_pct === "number") ? s.context_pct + "% ctx" : "";
    const cost = (typeof s.cost_usd === "number") ? "$" + s.cost_usd.toFixed(2) : "";
    const model = s.model || "";
    const ago = (s.last_active_seconds_ago === null || s.last_active_seconds_ago === undefined)
      ? "" : s.last_active_seconds_ago + "s ago";
    const meta = [model, ctx, cost, ago].filter(Boolean).join(" · ");
    return (
      '<div class="row">' +
        '<span class="label">' + s.label + '</span>' +
        '<span class="' + statusClass(s.status) + '">' + s.status + '</span>' +
      '</div>' +
      (meta ? '<div class="muted">' + meta + '</div>' : '')
    );
  }

  function render(data) {
    const d = data.daemon || {};
    document.getElementById("daemon-line").textContent =
      "@" + (d.bot_username || "?") + " · v" + (d.version || "?") +
      " · up " + Math.floor((d.uptime_seconds || 0) / 60) + "m";

    const sessions = data.sessions || [];
    const el = document.getElementById("sessions");
    if (sessions.length === 0) {
      el.innerHTML = '<div class="card muted">No sessions.</div>';
      return;
    }
    el.innerHTML = sessions.map(function (s) {
      return '<div class="card">' + renderSession(s) + '</div>';
    }).join("");
  }

  function showError(msg) {
    const el = document.getElementById("error");
    el.textContent = msg;
    el.style.display = "block";
  }

  if (!initData) {
    document.getElementById("daemon-line").textContent = "";
    showError("Open this page from the Telegram app to sign in.");
    return;
  }

  fetch("/api/status", { headers: { "X-Telegram-Init-Data": initData } })
    .then(function (res) {
      if (!res.ok) { throw new Error("HTTP " + res.status); }
      return res.json();
    })
    .then(render)
    .catch(function (e) {
      document.getElementById("daemon-line").textContent = "";
      showError("Could not load status (" + e.message + ").");
    });
})();
</script>
</body>
</html>
"""

__all__ = ["INDEX_HTML"]
