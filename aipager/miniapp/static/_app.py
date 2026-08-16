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
  var initData = tg ? tg.initData : "";

  // Poll while the tab is visible; visibilitychange below both stops
  // the wasted network/battery cost while backgrounded and forces one
  // immediate poll on return, rather than waiting out the interval.
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

  // ---- connectivity / staleness (spec: never a spinner-forever, never
  // a raw fetch error) --------------------------------------------------

  function setConnState(state) {
    var badge = document.getElementById("conn-badge");
    var errorEl = document.getElementById("error");
    badge.hidden = false;
    badge.className = "conn conn-" + (state === "live" ? "live"
      : state === "reconnecting" ? "reconnecting" : "offline");
    if (state === "live") {
      badge.textContent = "live";
      errorEl.style.display = "none";
    } else if (state === "reconnecting") {
      badge.textContent = "reconnecting…";
      errorEl.style.display = "none";
    } else if (state === "offline") {
      badge.textContent = "offline";
      showFatal("Can't reach the server — check the tunnel.");
    } else if (state === "expired") {
      badge.textContent = "expired";
      showFatal("Session expired — reopen from /app in Telegram.");
    }
  }

  function showFatal(msg) {
    var el = document.getElementById("error");
    el.textContent = msg;
    el.style.display = "block";
  }

  // Transient note that clears itself — used for "not built yet" taps.
  // Deliberately its OWN element, not #error: sharing one would let a
  // 3.5s self-clearing notice wipe a fatal message that must stay put.
  // The concrete case: initData expires after 5 minutes with no refresh
  // path, so any page left open shows the fatal "reopen from /app"; the
  // very next tap on the New-session card would have erased it, leaving
  // the operator with no explanation.
  var noticeTimer = null;
  function showNotice(msg) {
    var el = document.getElementById("notice");
    el.textContent = msg;
    el.style.display = "block";
    if (noticeTimer) { clearTimeout(noticeTimer); }
    noticeTimer = setTimeout(function () {
      el.textContent = "";
      el.style.display = "none";
      noticeTimer = null;
    }, 3500);
  }

  function apiFetch(path) {
    return fetch(path, { headers: { "X-Telegram-Init-Data": initData } })
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

  // A signed-but-now-rejected initData (401) or a caller who's fallen
  // out of scope membership (403) will never succeed by retrying —
  // Telegram's WebApp SDK exposes no way to mint a fresh initData short
  // of a full reload, so both are treated as one terminal state rather
  // than looping a doomed retry. Any other non-2xx or a fetch-level
  // failure (tunnel down, DNS, etc.) is transient and keeps polling.
  function handleFetchError(err) {
    // Once expired, the fatal message is the only thing worth showing.
    // Without this, a later non-auth failure (a tunnel 502, a DNS blip)
    // on a fetch that skipped the authExpired gate — loadDiffIfNeeded is
    // one — would route into setConnState and either hide the fatal
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

  function pulseForStatusChanges(sessions) {
    if (!tg || !tg.HapticFeedback) { return; }
    sessions.forEach(function (s) {
      var prev = lastStatuses[s.label];
      if (prev !== undefined && prev !== s.status) {
        try {
          tg.HapticFeedback.notificationOccurred(s.status === "waiting" ? "warning" : "success");
        } catch (e) {
          // Older Telegram client without this API — no-op.
        }
      }
      lastStatuses[s.label] = s.status;
    });
  }

  // ---- grid ---------------------------------------------------------

  // Row order arrives already sorted by the server (miniapp/sessions.py
  // sort_for_display) so the rule is one pure function pytest can pin,
  // rather than a comparator that only a browser can exercise.

  // "2m ago" style. Kept client-side and re-rendered on a timer so the
  // label ages between polls instead of freezing at whatever the last
  // fetch said.
  function formatAge(seconds) {
    if (seconds === null || seconds === undefined) { return ""; }
    if (seconds < 60) { return Math.max(0, Math.round(seconds)) + "s ago"; }
    if (seconds < 3600) { return Math.floor(seconds / 60) + "m ago"; }
    if (seconds < 86400) { return Math.floor(seconds / 3600) + "h ago"; }
    return Math.floor(seconds / 86400) + "d ago";
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

  function buildCard(s) {
    var card = document.createElement("div");
    card.className = "card" + (s.status === "gone" ? " card-gone" : "");
    var statusText = (s.status === "waiting" && s.waiting_kind)
      ? s.status + " · " + s.waiting_kind : s.status;

    var name = document.createElement("div");
    name.className = "card-name";
    name.textContent = s.label;

    var foot = document.createElement("div");
    foot.className = "card-foot";
    var st = document.createElement("span");
    st.className = statusClass(s.status);
    st.textContent = statusText;
    var age = document.createElement("span");
    age.className = "card-age";
    age.setAttribute("data-age-for", s.label);
    age.textContent = ageFor(s);
    foot.appendChild(st);
    foot.appendChild(age);

    card.appendChild(name);
    card.appendChild(foot);
    card.addEventListener("click", function () { openDetail(s.label); });
    return card;
  }

  function buildNewSessionCard() {
    var card = document.createElement("div");
    card.className = "card card-new";
    var plus = document.createElement("div");
    plus.className = "plus";
    plus.textContent = "+";
    var label = document.createElement("div");
    label.textContent = "New session";
    card.appendChild(plus);
    card.appendChild(label);
    card.addEventListener("click", function () {
      showNotice("Creating sessions from here is coming next — use /new in chat for now.");
    });
    return card;
  }

  var goneCollapsed = true;
  var lastGridData = null;   // so the gone-toggle can re-render without a fetch

  function renderGrid(data) {
    lastGridData = data;
    var d = data.daemon || {};
    document.getElementById("daemon-line").textContent =
      "@" + (d.bot_username || "?") + " · v" + (d.version || "?") +
      " · up " + Math.floor((d.uptime_seconds || 0) / 60) + "m";

    var sessions = data.sessions || [];
    var totals = data.totals || {};
    lastPayloadAt = Date.now();
    lastSessionsByLabel = Object.create(null);
    sessions.forEach(function (s) { lastSessionsByLabel[s.label] = s; });
    pulseForStatusChanges(sessions);

    // Waiting count rides the Sessions tab so the one state that costs
    // the operator time is visible without reading the grid.
    var badge = document.getElementById("waiting-badge");
    if (totals.waiting) {
      badge.textContent = totals.waiting;
      badge.hidden = false;
    } else {
      badge.hidden = true;
    }

    var bits = [];
    if (totals.live) { bits.push(totals.live + " live"); }
    if (totals.gone) { bits.push(totals.gone + " finished"); }
    if (totals.cost_usd) { bits.push("$" + totals.cost_usd.toFixed(2) + " total"); }
    document.getElementById("grid-totals").textContent = bits.join(" · ");

    var live = [];
    var gone = [];
    sessions.forEach(function (s) {
      (s.status === "gone" ? gone : live).push(s);
    });

    // The New-session cell is always the first grid cell and never
    // scrolls out of first position.
    var el = document.getElementById("sessions");
    el.innerHTML = "";
    el.appendChild(buildNewSessionCard());
    live.forEach(function (s) { el.appendChild(buildCard(s)); });

    document.getElementById("empty-state").hidden = sessions.length !== 0;

    var wrap = document.getElementById("gone-wrap");
    var goneEl = document.getElementById("sessions-gone");
    var toggle = document.getElementById("gone-toggle");
    wrap.hidden = gone.length === 0;
    if (gone.length) {
      toggle.textContent = (goneCollapsed ? "Show " : "Hide ") +
        gone.length + " finished session" + (gone.length === 1 ? "" : "s");
      goneEl.hidden = goneCollapsed;
      goneEl.innerHTML = "";
      gone.forEach(function (s) { goneEl.appendChild(buildCard(s)); });
    }
  }

  // Re-stamp only the age labels, so "2m ago" becomes "3m ago" without
  // refetching or rebuilding the cards.
  function tickAges() {
    var nodes = document.querySelectorAll("[data-age-for]");
    for (var i = 0; i < nodes.length; i++) {
      var s = lastSessionsByLabel[nodes[i].getAttribute("data-age-for")];
      if (s) { nodes[i].textContent = ageFor(s); }
    }
  }

  // ---- drill-down: timeline ------------------------------------------

  function renderTimelineRow(row) {
    if (row.kind === "commentary") {
      return '<div class="timeline-row timeline-commentary">' + escapeHtml(row.text) + '</div>';
    }
    var elapsed = (row.state === "running" && typeof row.elapsed_seconds === "number")
      ? " (" + row.elapsed_seconds + "s)" : "";
    return (
      '<div class="timeline-row timeline-tool state-' + escapeHtml(row.state) + '">' +
        escapeHtml(row.text) + escapeHtml(elapsed) +
      '</div>'
    );
  }

  function renderTimeline(rows) {
    var panel = document.getElementById("panel-timeline");
    if (!rows || rows.length === 0) {
      panel.innerHTML = '<div class="muted">No activity yet.</div>';
      return;
    }
    // Only auto-scroll if the reader was already at the bottom — never
    // yank the view out from under someone reading an earlier row.
    var wasAtBottom = (panel.scrollTop + panel.clientHeight >= panel.scrollHeight - 4);
    panel.innerHTML = rows.map(renderTimelineRow).join("");
    if (wasAtBottom) { panel.scrollTop = panel.scrollHeight; }
  }

  function renderDetailData(data) {
    document.getElementById("detail-label").textContent = data.label || "";
    var statusEl = document.getElementById("detail-status");
    statusEl.className = statusClass(data.status);
    statusEl.textContent = (data.status === "waiting" && data.waiting_kind)
      ? data.status + " (" + data.waiting_kind + ")" : (data.status || "");

    var metaParts = [];
    if (data.model) { metaParts.push(data.model); }
    if (typeof data.context_pct === "number") { metaParts.push(data.context_pct + "% ctx"); }
    if (typeof data.cost_usd === "number") { metaParts.push("$" + data.cost_usd.toFixed(2)); }
    if (data.busy_elapsed_seconds !== null && data.busy_elapsed_seconds !== undefined) {
      metaParts.push(data.busy_elapsed_seconds + "s elapsed");
    }
    if (data.waiting_summary) { metaParts.push("waiting: " + data.waiting_summary); }
    document.getElementById("detail-meta").textContent = metaParts.join(" · ");

    renderTimeline(data.timeline);
  }

  // ---- drill-down: diff -----------------------------------------------

  var DIFF_REASONS = {
    not_a_git_repo: "This session's working directory isn't a git repository.",
    git_not_installed: "git isn't available on this machine.",
    cwd_missing: "This session's working directory no longer exists.",
    no_commits_yet: "This repository has no commits yet.",
    git_error: "git couldn't produce a diff for this session."
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
      '<span>' + escapeHtml(f.path) + '</span>' +
      '<span class="muted">' + escapeHtml(f.change_type) + '</span>';

    var body = document.createElement("div");
    body.className = "diff-body";
    if (f.binary) {
      body.innerHTML = '<div class="diff-binary">Binary file — no preview.</div>';
    } else if (!f.patch) {
      body.innerHTML = '<div class="diff-truncated">' +
        (f.truncated ? "Diff too large to show." : "No changes.") + '</div>';
    } else {
      body.innerHTML = f.patch.split("\n").map(renderDiffLine).join("");
    }

    header.addEventListener("click", function () { body.hidden = !body.hidden; });

    wrap.appendChild(header);
    wrap.appendChild(body);
    return wrap;
  }

  function renderDiff(data) {
    var panel = document.getElementById("panel-diff");
    if (!data.available) {
      panel.innerHTML = '<div class="diff-truncated">' +
        escapeHtml(DIFF_REASONS[data.reason] || "Diff unavailable.") + '</div>';
      return;
    }
    var files = data.files || [];
    if (files.length === 0) {
      panel.innerHTML = '<div class="muted">No changes.</div>';
      return;
    }
    panel.innerHTML = "";
    if (data.files_truncated) {
      var note = document.createElement("div");
      note.className = "diff-truncated";
      note.textContent = "Showing a partial set of changed files — the full diff is larger than this viewer renders.";
      panel.appendChild(note);
    }
    files.forEach(function (f) { panel.appendChild(renderDiffFile(f)); });
  }

  function loadDiffIfNeeded() {
    var label = currentView.label;
    if (diffLoadedForLabel === label) { return; }
    var panel = document.getElementById("panel-diff");
    panel.innerHTML = '<div class="muted">Loading diff…</div>';
    apiFetch("/api/sessions/" + encodeURIComponent(label) + "/diff")
      .then(function (data) {
        diffLoadedForLabel = label;
        renderDiff(data);
      })
      .catch(function (err) {
        if (err && err.httpStatus === 404) {
          panel.innerHTML = '<div class="diff-truncated">Session no longer available.</div>';
          return;
        }
        handleFetchError(err);
        panel.innerHTML = '<div class="diff-truncated">Could not load diff.</div>';
      });
  }

  // ---- view switching ---------------------------------------------------

  function setActiveTab(name) {
    currentView.tab = name;
    document.getElementById("tab-timeline").classList.toggle("active", name === "timeline");
    document.getElementById("tab-diff").classList.toggle("active", name === "diff");
    document.getElementById("panel-timeline").hidden = name !== "timeline";
    document.getElementById("panel-diff").hidden = name !== "diff";
    if (name === "diff") { loadDiffIfNeeded(); }
  }

  function openDetail(label) {
    currentView = { type: "detail", label: label, tab: "timeline" };
    diffLoadedForLabel = null;
    document.getElementById("view-grid").hidden = true;
    document.getElementById("view-detail").hidden = false;
    if (tg && tg.BackButton) { tg.BackButton.show(); }
    setActiveTab("timeline");
    pollTick();
  }

  function showGrid() {
    currentView = { type: "grid" };
    document.getElementById("view-detail").hidden = true;
    document.getElementById("view-grid").hidden = mainTab !== "sessions";
    document.getElementById("view-settings").hidden = mainTab !== "settings";
    if (tg && tg.BackButton) { tg.BackButton.hide(); }
    pollTick();
  }

  // ---- top-level tabs -------------------------------------------------

  var mainTab = "sessions";

  function setMainTab(name) {
    mainTab = name;
    // Switching tabs from inside a drill-down returns to the top level —
    // the back button is for going back, the tab bar is for switching.
    currentView = { type: "grid" };
    document.getElementById("view-detail").hidden = true;
    if (tg && tg.BackButton) { tg.BackButton.hide(); }
    document.getElementById("view-grid").hidden = name !== "sessions";
    document.getElementById("view-settings").hidden = name !== "settings";
    document.getElementById("maintab-sessions")
      .classList.toggle("is-active", name === "sessions");
    document.getElementById("maintab-settings")
      .classList.toggle("is-active", name === "settings");
    if (name === "sessions") { pollTick(); }
  }

  if (tg && tg.BackButton) {
    tg.BackButton.onClick(showGrid);
  }
  document.getElementById("maintab-sessions")
    .addEventListener("click", function () { setMainTab("sessions"); });
  document.getElementById("maintab-settings")
    .addEventListener("click", function () { setMainTab("settings"); });
  document.getElementById("gone-toggle").addEventListener("click", function () {
    goneCollapsed = !goneCollapsed;
    if (lastGridData) { renderGrid(lastGridData); }
  });
  document.getElementById("tab-timeline").addEventListener("click", function () { setActiveTab("timeline"); });
  document.getElementById("tab-diff").addEventListener("click", function () { setActiveTab("diff"); });

  // Ages tick on their own so "2m ago" does not sit stale between polls.
  setInterval(function () {
    if (document.visibilityState === "visible") { tickAges(); }
  }, 15000);

  // ---- polling loop -------------------------------------------------

  function pollTick() {
    if (authExpired) { return; }
    if (document.visibilityState !== "visible") { return; }

    if (currentView.type === "grid") {
      apiFetch("/api/sessions")
        .then(function (data) {
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
            // The session left the caller's own scope (cleared, or
            // never existed) — go back to the grid rather than
            // reporting a connectivity problem that isn't one.
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

  if (!initData) {
    document.getElementById("daemon-line").textContent = "";
    showFatal("Open this page from the Telegram app to sign in.");
    return;
  }

  pollTick();
  pollTimer = setInterval(pollTick, POLL_INTERVAL_MS);
})();
"""

__all__ = ["APP_JS"]
