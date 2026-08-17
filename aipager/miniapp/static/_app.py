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
    card.addEventListener("click", openNewSession);
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
    lastDetailData = data;
    document.getElementById("detail-label").textContent = data.label || "";
    var statusEl = document.getElementById("detail-status");
    statusEl.className = statusClass(data.status);
    statusEl.textContent = (data.status === "waiting" && data.waiting_kind)
      ? data.status + " (" + data.waiting_kind + ")" : (data.status || "");

    // What it is blocked on, prominently — this is the reason the
    // operator opened the page at all. The API has returned these two
    // fields since stage 2; the old tab-strip page ignored them.
    var waitEl = document.getElementById("detail-waiting");
    if (data.status === "waiting") {
      var what = data.waiting_summary
        ? data.waiting_summary
        : (data.waiting_kind === "question" ? "a question" : "a permission prompt");
      waitEl.textContent = "Waiting on you: " + what;
      waitEl.hidden = false;
    } else {
      waitEl.hidden = true;
    }

    // `facts` is built server-side (sessions.display_facts) and already
    // omits what would be noise — a finished session has no model, cost
    // or context to report, and "0% ctx · $0.00" reads like a fault.
    var dl = document.getElementById("detail-facts");
    dl.innerHTML = "";
    (data.facts || []).forEach(function (fact) {
      var dt = document.createElement("dt");
      dt.textContent = fact.label;
      var dd = document.createElement("dd");
      dd.textContent = fact.value;
      dl.appendChild(dt);
      dl.appendChild(dd);
    });

    var prev = document.getElementById("detail-preview");
    if (data.last_message) {
      prev.className = "preview";
      prev.textContent = data.last_message;
    } else {
      prev.className = "preview is-empty";
      prev.textContent = data.status === "gone"
        ? "Nothing was captured before this session ended."
        : "Nothing captured yet — it arrives once Claude replies.";
    }

    renderTimeline(data.timeline);
    updateSectionHeaders(data);
  }

  // Section headers double as the toggle, and say what is inside before
  // you open it — a count, or why there is nothing to see.
  function updateSectionHeaders(data) {
    var tl = document.getElementById("tab-timeline");
    var rows = (data && data.timeline) ? data.timeline.length : 0;
    tl.textContent = (timelineOpen ? "▾ " : "▸ ") +
      (rows ? "Timeline (" + rows + ")" : "Timeline — empty");

    var note = document.getElementById("timeline-note");
    if (note) {
      // The honest explanation: tool_history/stream_commentary are not
      // persisted, so a restart empties this for every older session.
      // Only while the section is open — collapsed sections must not
      // push the last message off the screen with an explanation.
      note.hidden = !(timelineOpen && rows === 0);
    }
    updateDiffHeader();
  }

  function updateDiffHeader() {
    var btn = document.getElementById("tab-diff");
    var caret = diffOpen ? "▾ " : "▸ ";
    if (!lastDiffData) {
      btn.textContent = caret + "Changed files";
      return;
    }
    if (!lastDiffData.available) {
      btn.textContent = caret + "Changed files — none";
      return;
    }
    var n = (lastDiffData.files || []).length;
    btn.textContent = caret + (n ? "Changed files (" + n + ")" : "Changed files — none");
  }

  // ---- per-session settings (design §4 default-vs-override mechanic) --

  // {schema, values, can_edit} from GET /api/sessions/{label}/preferences,
  // or null before the first fetch resolves / while switching sessions.
  var sessionSettingsData = null;
  // Which session sessionSettingsData is *for* — every callback below
  // checks this before touching shared state, so a slow response for a
  // session the operator has already navigated away from can never paint
  // (or, worse, silently roll back) the session on screen now.
  var sessionSettingsLabel = null;
  // Per-field write counter, so a slow PUT that resolves after a newer one
  // cannot roll the field back. Declared here rather than beside its only
  // reader: it was previously declared inside the block that rendered the
  // settings, and a rewrite of that block deleted it — every tap then threw
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

    // The label the LOADED DATA belongs to, not whatever view is current —
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
        // Fill = what this session will actually use. Tag = what the chat
        // default is. Two independent values; the tag never follows the
        // selection.
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

    // Optimistic: paint this session as now overriding `field` to
    // `value` immediately; keep `previous` so a failed write can put the
    // truth back instead of leaving a button that lies about what is
    // stored — same pattern as the scope-wide savePreference.
    sessionSettingsData.values[field] = {
      effective: value,
      scope_default: previous ? previous.scope_default : value,
      override_value: value,
      overridden: true
    };
    renderSessionSettings();

    fetch("/api/sessions/" + encodeURIComponent(label) + "/preferences/" + encodeURIComponent(field), {
      method: "PUT",
      headers: {
        "X-Telegram-Init-Data": initData,
        "Content-Type": "application/json"
      },
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
      showNotice("Saved.");
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
      showNotice("Couldn't save — the value on screen is what's stored.");
    });
  }

  // "Reset to default" (design §4): one DELETE per overridden field. A
  // mid-sequence failure leaves some fields reset and some not — each
  // DELETE is independently idempotent, so the next tap (or the next
  // page load) self-heals; there is no invalid intermediate state.
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
        headers: { "X-Telegram-Init-Data": initData }
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
        showNotice(hadError
          ? "Some settings could not be reset — reopen to retry."
          : "Reset to defaults.");
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
    lastDiffData = data;
    var panel = document.getElementById("panel-diff");
    if (!data.available) {
      panel.innerHTML = '<div class="diff-truncated">' +
        escapeHtml(DIFF_REASONS[data.reason] || "Diff unavailable.") + '</div>';
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
        ? "No uncommitted changes in " + repo + "."
        : "No uncommitted changes.";
      panel.innerHTML = '<div class="muted">' + escapeHtml(msg) + "</div>";
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

  // Independent collapsible sections, not a tab strip: the page leads
  // with the last message, and both of these are secondary. Collapsed by
  // default so nothing below the fold competes with it.
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
    // Reset the detail payload too: renderDiff names the repo from
    // lastDetailData.cwd, and expanding "Changed files" on this session
    // before its own detail poll lands would otherwise name the PREVIOUS
    // session's directory next to this one's diff.
    lastDetailData = null;
    document.getElementById("panel-diff").hidden = true;
    document.getElementById("panel-timeline").hidden = true;
    diffLoadedForLabel = null;
    showView("detail", { label: label });

    // Paint from the row the grid already has, so the page is never blank
    // while the detail request is in flight. The poll overwrites this with
    // the authoritative payload a moment later.
    var known = lastSessionsByLabel[label];
    document.getElementById("detail-label").textContent = label;
    var st = document.getElementById("detail-status");
    if (known) {
      st.className = statusClass(known.status);
      st.textContent = known.status;
    } else {
      st.className = "status";
      st.textContent = "";
    }
    document.getElementById("detail-waiting").hidden = true;
    skeleton(document.getElementById("detail-facts"), 3);
    skeleton(document.getElementById("detail-preview"), 2);
    skeleton(document.getElementById("session-settings-groups"), 4, "skel-row");

    loadSessionSettings(label);
    pollTick();
  }

  function showGrid() {
    // Back from a sub-page returns to whichever top-level tab was active.
    showView(mainTab === "settings" ? "settings" : "grid");
    pollTick();
  }



  // ---- settings group rendering (shared by all three surfaces) --------
  //
  // Collapsed by default, showing only the heading and the value in
  // force. Every alternative on screen at once — four groups x up to five
  // options — was what the operator meant by "make user lost in there".
  var openGroups = Object.create(null);

  function renderOptionGroup(host, opts) {
    // opts: {key, title, options[], current, defaultValue, disabled, onPick}
    var wrap = document.createElement("div");
    wrap.className = "grp";

    var head = document.createElement("button");
    head.type = "button";
    head.className = "grp-head";
    var isOpen = !!openGroups[opts.key];

    var title = document.createElement("span");
    title.className = "grp-title";
    title.textContent = opts.title;

    var value = document.createElement("span");
    value.className = "grp-value";
    var currentOpt = null;
    opts.options.forEach(function (o) {
      if (o.value === opts.current) { currentOpt = o; }
    });
    value.textContent = currentOpt ? currentOpt.label : "—";

    var caret = document.createElement("span");
    caret.className = "grp-caret";
    caret.textContent = isOpen ? "▾" : "▸";

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
        row.className = "choice" + (o.value === opts.current ? " is-active" : "");
        if (opts.disabled) { row.disabled = true; }

        var main = document.createElement("span");
        main.className = "choice-main";
        main.textContent = o.label;
        row.appendChild(main);

        // The tag marks the SCOPE's value and never follows the
        // selection — see the per-session settings mechanic.
        if (opts.defaultValue !== undefined && o.value === opts.defaultValue) {
          var tag = document.createElement("span");
          tag.className = "tag";
          tag.textContent = "default";
          row.appendChild(tag);
        }
        if (o.help) {
          var help = document.createElement("span");
          help.className = "choice-help";
          help.textContent = o.help;
          row.appendChild(help);
        }
        if (!opts.disabled) {
          row.addEventListener("click", function () { opts.onPick(o.value); });
        }
        list.appendChild(row);
      });
      wrap.appendChild(list);
    }
    host.appendChild(wrap);
  }


  // A shaped placeholder while a fetch is in flight. An empty panel that
  // fills in half a second later reads as broken — which is what the
  // operator reported as "comes in with a delay".
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
  // a new view cannot be half-added — it either has an entry or it does
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
    "new":    { section: "view-new",      topLevel: false, polls: null }
  };

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
    if (tg && tg.BackButton) {
      if (spec.topLevel) { tg.BackButton.hide(); } else { tg.BackButton.show(); }
    }
  }

  // ---- settings -------------------------------------------------------

  var settingsData = null;   // {schema, values, can_edit} from the server

  function renderSettings() {
    var root = document.getElementById("settings-groups");
    root.innerHTML = "";
    if (!settingsData) { return; }
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

  function savePreference(field, value) {
    if (!settingsData || settingsData.values[field] === value) { return; }
    // Optimistic: paint the choice immediately, but keep the previous
    // value so a failed write can put the truth back rather than leaving
    // a button that lies about what is stored.
    var previous = settingsData.values[field];
    var seq = (saveSeq[field] || 0) + 1;
    saveSeq[field] = seq;
    settingsData.values[field] = value;
    renderSettings();

    fetch("/api/preferences/" + encodeURIComponent(field), {
      method: "PUT",
      headers: {
        "X-Telegram-Init-Data": initData,
        "Content-Type": "application/json"
      },
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
      if (saveSeq[field] !== seq) { return; }   // superseded — ignore
      settingsData.values = data.values;
      renderSettings();
      showNotice("Saved.");
      if (tg && tg.HapticFeedback) {
        try { tg.HapticFeedback.notificationOccurred("success"); } catch (e) { /* old client */ }
      }
    }).catch(function (err) {
      if (err && err.authFailed) {
        // Same terminal state every other request uses, rather than
        // reporting a permanent auth failure as a transient save error.
        handleFetchError(err);
        return;
      }
      if (saveSeq[field] !== seq) { return; }   // superseded — ignore
      settingsData.values[field] = previous;
      renderSettings();
      showNotice("Couldn't save — the value on screen is what's stored.");
    });
  }

  function loadSettings() {
    // Re-entering the tab shows the values already held while the refresh
    // runs; only a first visit gets the skeleton.
    if (settingsData) { renderSettings(); } else {
      skeleton(document.getElementById("settings-groups"), 4, "skel-row");
    }
    apiFetch("/api/preferences")
      .then(function (data) {
        settingsData = data;
        renderSettings();
      })
      .catch(handleFetchError);
  }


  // ---- new session ----------------------------------------------------

  var newOptions = null;      // /api/session-options payload
  // prefs: field -> chosen value, only for fields the operator diverged on.
  var newState = { model: "", cwd: "", skip_perms: false, prefs: {} };

  function renderNewForm() {
    var models = document.getElementById("new-model");
    models.innerHTML = "";
    // "" = leave the session on Claude Code's own default. Aliases carry a
    // hint rather than a version: an alias always resolves to the latest of
    // its family, so a baked-in "Opus 5" would be wrong on the next release.
    // The real version shows on the session once it reports one.
    var modelOpts = [{ value: "", label: "Default", help: "Whatever the CLI is configured to use" }];
    ((newOptions && newOptions.models) || []).forEach(function (m) {
      modelOpts.push({ value: m.label, label: m.label, help: m.hint || "" });
    });
    renderOptionGroup(models, {
      key: "new:model",
      title: "Model",
      options: modelOpts,
      current: newState.model,
      rerender: renderNewForm,
      onPick: function (value) { newState.model = value; renderNewForm(); }
    });

    // Directories come from the server's allow-list — the same list
    // validate_cwd checks against, so the picker can never offer a path
    // the server would refuse.
    var dirs = document.getElementById("new-cwd");
    dirs.innerHTML = "";
    var dirOpts = [{ value: "", label: "Default", help: "The daemon's own directory" }];
    ((newOptions && newOptions.directories) || []).forEach(function (d) {
      dirOpts.push({ value: d, label: d.split("/").filter(Boolean).pop(), help: d });
    });
    renderOptionGroup(dirs, {
      key: "new:cwd",
      title: "Working directory",
      options: dirOpts,
      current: newState.cwd,
      rerender: renderNewForm,
      onPick: function (value) { newState.cwd = value; renderNewForm(); }
    });

    var canAuto = !!(newOptions && newOptions.can_use_auto);
    var modes = document.getElementById("new-mode");
    modes.innerHTML = "";
    renderOptionGroup(modes, {
      key: "new:mode",
      title: "Permission mode",
      options: [
        { value: false, label: "Ask", help: "Claude asks before running tools" },
        { value: true, label: "Auto",
          help: canAuto ? "Runs tools without asking" : "Requires admin" }
      ],
      current: newState.skip_perms,
      rerender: renderNewForm,
      onPick: function (value) {
        if (value === true && !canAuto) { return; }
        newState.skip_perms = value;
        renderNewForm();
      }
    });
    document.getElementById("new-mode-note").hidden = canAuto;

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

    var name = document.getElementById("new-name").value.trim();
    var where = newState.cwd
      ? newState.cwd
      : "the daemon's own directory";
    document.getElementById("new-summary").textContent =
      "Starts Claude" + (name ? " as " + name : "") + " in " + where +
      ", in " + (newState.skip_perms ? "Auto" : "Ask") + " mode.";

    var create = document.getElementById("new-create");
    create.disabled = !name || !(newOptions && newOptions.can_create);
  }

  function openNewSession() {
    showView("new");
    document.getElementById("new-name-error").hidden = true;
    if (!newOptions) {
      skeleton(document.getElementById("new-model"), 1, "skel-row");
    }
    renderNewForm();
    apiFetch("/api/session-options")
      .then(function (data) { newOptions = data; renderNewForm(); })
      .catch(handleFetchError);
  }

  function submitNewSession() {
    var nameEl = document.getElementById("new-name");
    var errEl = document.getElementById("new-name-error");
    var create = document.getElementById("new-create");
    var name = nameEl.value.trim();
    if (!name) { return; }
    errEl.hidden = true;
    create.disabled = true;

    fetch("/api/sessions", {
      method: "POST",
      headers: {
        "X-Telegram-Init-Data": initData,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        name: name,
        model: newState.model,
        cwd: newState.cwd,
        skip_perms: newState.skip_perms
      })
    }).then(function (res) {
      return res.json().then(function (data) { return { status: res.status, data: data }; });
    }).then(function (r) {
      if (r.status === 200) {
        nameEl.value = "";
        if (tg && tg.HapticFeedback) {
          try { tg.HapticFeedback.notificationOccurred("success"); } catch (e) { /* old client */ }
        }
        // Chosen reply-style settings become per-session overrides through
        // batch 4's existing route — no second write path for the same data.
        var label = r.data.label;
        // Await the overrides before navigating. The session page loads its
        // settings once, with no re-poll, so racing these against that GET
        // would land the operator on a page showing scope defaults for
        // settings they just chose — a silent lie with no self-correction.
        var writes = Object.keys(newState.prefs).map(function (field) {
          return fetch("/api/sessions/" + encodeURIComponent(label) +
                "/preferences/" + encodeURIComponent(field), {
            method: "PUT",
            headers: {
              "X-Telegram-Init-Data": initData,
              "Content-Type": "application/json"
            },
            body: JSON.stringify({ value: newState.prefs[field] })
          }).catch(function () { /* session exists; page will show truth */ });
        });
        newState.prefs = {};
        Promise.all(writes).then(function () { openDetail(label); });
        return;
      }
      // A name collision or a rejected directory is answered inline, next
      // to the field that caused it — not as a generic failure toast.
      errEl.textContent = (r.data && r.data.detail) ||
        (r.status === 403 ? "You're not allowed to create sessions here."
                          : "Couldn't create that session.");
      errEl.hidden = false;
      create.disabled = false;
    }).catch(function () {
      errEl.textContent = "Couldn't reach the server — the session was not created.";
      errEl.hidden = false;
      create.disabled = false;
    });
  }

  document.getElementById("new-name").addEventListener("input", renderNewForm);
  document.getElementById("new-create").addEventListener("click", submitNewSession);
  document.getElementById("new-advanced-toggle").addEventListener("click", function () {
    var adv = document.getElementById("new-advanced");
    adv.hidden = !adv.hidden;
    document.getElementById("new-advanced-toggle").textContent =
      (adv.hidden ? "▸ " : "▾ ") + "Advanced";
  });

  // ---- top-level tabs -------------------------------------------------

  var mainTab = "sessions";

  function setMainTab(name) {
    mainTab = name;
    // Switching tabs from inside a sub-page returns to the top level —
    // the back button is for going back, the tab bar is for switching.
    showView(name === "settings" ? "settings" : "grid");
    document.getElementById("maintab-sessions")
      .classList.toggle("is-active", name === "sessions");
    document.getElementById("maintab-settings")
      .classList.toggle("is-active", name === "settings");
    if (name === "sessions") { pollTick(); } else { loadSettings(); }
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
  document.getElementById("tab-timeline").addEventListener("click", toggleTimeline);
  document.getElementById("tab-diff").addEventListener("click", toggleDiff);
  document.getElementById("session-settings-reset").addEventListener("click", resetSessionSettings);

  // Ages tick on their own so "2m ago" does not sit stale between polls.
  setInterval(function () {
    if (document.visibilityState === "visible") { tickAges(); }
  }, 15000);

  // ---- polling loop -------------------------------------------------

  function pollTick() {
    if (authExpired) { return; }
    if (document.visibilityState !== "visible") { return; }
    // The Settings tab is a real view now — polling the grid behind it is
    // pure battery and tunnel traffic for something nobody is looking at.
    // What (if anything) this view polls is declared in VIEWS, not
    // inferred. A view with polls:null — the settings tab, the new-session
    // form — is left alone entirely.
    var spec = VIEWS[currentView.type];
    var mode = spec ? spec.polls : null;
    if (mode === null || mode === undefined) { return; }
    if (mode === "detail" && !currentView.label) { return; }

    if (mode === "grid") {
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
