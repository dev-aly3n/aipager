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
  var lastStatuses = {};        // label -> status, to detect changes for haptics
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

  var STATUS_RANK = { waiting: 0, busy: 1, idle: 2, unknown: 3, gone: 4 };

  function sortSessions(a, b) {
    var ra = STATUS_RANK.hasOwnProperty(a.status) ? STATUS_RANK[a.status] : 5;
    var rb = STATUS_RANK.hasOwnProperty(b.status) ? STATUS_RANK[b.status] : 5;
    if (ra !== rb) { return ra - rb; }
    return String(a.label).localeCompare(String(b.label));
  }

  function renderSessionRow(s) {
    var ctx = (typeof s.context_pct === "number") ? s.context_pct + "% ctx" : "";
    var cost = (typeof s.cost_usd === "number") ? "$" + s.cost_usd.toFixed(2) : "";
    var ago = (s.last_active_seconds_ago === null || s.last_active_seconds_ago === undefined)
      ? "" : s.last_active_seconds_ago + "s ago";
    var meta = [s.project, s.model, ctx, cost, ago].filter(Boolean).join(" · ");
    var statusText = (s.status === "waiting" && s.waiting_kind)
      ? s.status + " (" + s.waiting_kind + ")" : s.status;
    return (
      '<div class="row">' +
        '<span class="label">' + escapeHtml(s.label) + '</span>' +
        '<span class="' + statusClass(s.status) + '">' + escapeHtml(statusText) + '</span>' +
      '</div>' +
      (meta ? '<div class="muted">' + escapeHtml(meta) + '</div>' : '')
    );
  }

  function renderGrid(data) {
    var d = data.daemon || {};
    document.getElementById("daemon-line").textContent =
      "@" + (d.bot_username || "?") + " · v" + (d.version || "?") +
      " · up " + Math.floor((d.uptime_seconds || 0) / 60) + "m";

    var sessions = (data.sessions || []).slice().sort(sortSessions);
    pulseForStatusChanges(sessions);

    var el = document.getElementById("sessions");
    if (sessions.length === 0) {
      el.innerHTML = '<div class="card muted">No sessions.</div>';
      return;
    }
    el.innerHTML = "";
    sessions.forEach(function (s) {
      var card = document.createElement("div");
      card.className = "card";
      card.innerHTML = renderSessionRow(s);
      card.addEventListener("click", function () { openDetail(s.label); });
      el.appendChild(card);
    });
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
    document.getElementById("view-grid").hidden = false;
    if (tg && tg.BackButton) { tg.BackButton.hide(); }
    pollTick();
  }

  if (tg && tg.BackButton) {
    tg.BackButton.onClick(showGrid);
  }
  document.getElementById("tab-timeline").addEventListener("click", function () { setActiveTab("timeline"); });
  document.getElementById("tab-diff").addEventListener("click", function () { setActiveTab("diff"); });

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
