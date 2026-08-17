"""HTML skeleton for the Mini App page — head, body markup, tail.

Split from the CSS (``_styles.py``) and JS (``_app.py``) purely so each
piece stays under a length a human can review in one screen; assembled
back into one page by ``static/__init__.py``. See design.md Decision 4
for why this is a plain ``.py`` split (no packaging change) rather than
real ``.html``/``.css``/``.js`` files.
"""

from __future__ import annotations

HTML_HEAD = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>aipager</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
"""

# Markup only — no inline event handlers (onclick="..." etc): every
# listener is wired in _app.py via addEventListener, which is both the
# established stage-1 style and friendlier to a strict Content-Security-
# Policy (no "unsafe-inline" needed for attribute handlers, just for the
# one <script> block itself, same as stage 1).
HTML_BODY = """\
</head>
<body>
<header>
  <div>
    <h1>aipager</h1>
    <div id="daemon-line" class="muted">Loading…</div>
  </div>
  <span id="conn-badge" class="conn conn-live" hidden></span>
</header>

<nav class="tabbar" id="tabbar">
  <button type="button" class="tabbar-btn is-active" id="maintab-sessions">
    Sessions<span id="waiting-badge" class="badge" hidden></span>
  </button>
  <button type="button" class="tabbar-btn" id="maintab-settings">Settings</button>
</nav>

<div id="error" role="alert"></div>
<div id="notice" role="status"></div>

<section id="view-grid">
  <div id="grid-totals" class="totals muted"></div>
  <div id="sessions" class="grid"></div>
  <div id="empty-state" class="empty" hidden>
    <p><strong>No sessions yet.</strong></p>
    <p class="muted">Tap <em>New session</em> above to start one.</p>
  </div>
  <div id="gone-wrap" hidden>
    <button type="button" class="gone-toggle" id="gone-toggle"></button>
    <div id="sessions-gone" class="grid" hidden></div>
  </div>
</section>

<section id="view-new" hidden>
  <h2 class="sect-title">New session</h2>
  <label class="field-label" for="new-name">Name</label>
  <input id="new-name" class="field-input" type="text" autocomplete="off"
         placeholder="frontend" maxlength="64">
  <div id="new-name-error" class="field-error" hidden></div>

  <label class="field-label">Model</label>
  <div id="new-model"></div>

  <label class="field-label">Working directory</label>
  <div id="new-cwd"></div>

  <button type="button" class="sect-toggle" id="new-advanced-toggle">▸ Advanced</button>
  <div id="new-advanced" hidden>
    <label class="field-label">Permission mode</label>
    <div id="new-mode"></div>
    <div id="new-mode-note" class="muted" hidden>
      Auto mode requires admin.
    </div>
    <div id="new-prefs"></div>
  </div>

  <div id="new-summary" class="new-summary"></div>
  <button type="button" class="primary" id="new-create">Create session</button>
</section>

<!--
  Conditional reveals for the new-session form. They live here, outside any
  group, because the group hosts are rebuilt wholesale on every structural
  render — a reveal built inside one would lose its value and its focus on
  the next tap. renderOptionGroup MOVES the node into place under the row
  that revealed it, and renderNewForm parks it back here before clearing.
  The node identity is what makes the text survive; see GOV.UK's finding
  that a reveal should hold a single input and nothing more.
-->
<div id="node-stash" hidden>
  <div id="new-model-reveal" class="reveal">
    <label class="reveal-label" for="new-model-name">Model name</label>
    <input id="new-model-name" class="field-input" type="text" autocomplete="off"
           autocapitalize="none" spellcheck="false" placeholder="claude-opus-5"
           maxlength="64">
    <div id="new-model-note" class="reveal-note"></div>
  </div>

  <div id="new-folder-reveal" class="reveal">
    <label class="reveal-label" for="new-folder-name">New folder name</label>
    <div class="prefix-field">
      <span id="new-folder-prefix" class="prefix-text"></span>
      <input id="new-folder-name" class="prefix-input" type="text" autocomplete="off"
             autocapitalize="none" spellcheck="false" placeholder="my-project"
             maxlength="64" enterkeyhint="done">
      <button type="button" class="prefix-go" id="new-folder-create"
              aria-label="Create this folder">＋</button>
    </div>
    <div id="new-folder-note" class="reveal-note"></div>
  </div>
</div>

<section id="view-settings" hidden>
  <p class="muted" id="settings-intro">These apply to every session in this chat.</p>
  <div id="settings-groups"></div>
  <div id="settings-readonly" class="muted" hidden>
    Only an admin can change these.
  </div>
</section>

<section id="view-detail" hidden>
  <div id="detail-header">
    <span id="detail-label" class="detail-label"></span>
    <span id="detail-status" class="status"></span>
  </div>
  <div id="detail-waiting" class="waiting-note" hidden></div>
  <dl id="detail-facts" class="facts"></dl>

  <h2 class="sect-title">Last message</h2>
  <div id="detail-preview" class="preview"></div>

  <h2 class="sect-title">Per session settings</h2>
  <div id="session-settings-groups"></div>
  <div id="session-settings-readonly" class="muted" hidden>
    Only someone who can prompt this session can change these.
  </div>
  <button type="button" class="session-settings-reset" id="session-settings-reset" hidden>
    Reset to defaults
  </button>

  <button type="button" class="sect-toggle" id="tab-diff"></button>
  <div id="panel-diff" class="panel" hidden></div>

  <button type="button" class="sect-toggle" id="tab-timeline"></button>
  <div id="timeline-note" class="sect-note" hidden>
    The timeline only covers the current daemon run — it isn't saved across
    restarts, so an older session shows nothing here.
  </div>
  <div id="panel-timeline" class="panel" hidden></div>
</section>
"""

HTML_TAIL = """\
</body>
</html>
"""

__all__ = ["HTML_BODY", "HTML_HEAD", "HTML_TAIL"]
