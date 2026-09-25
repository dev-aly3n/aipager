"""HTML skeleton for the Mini App page — head, body markup, tail.

Split from the CSS (``_styles.py``) and JS (``_app.py``) purely so each
piece stays under a length a human can review in one screen; assembled
back into one page by ``static/__init__.py``. See design.md Decision 4
for why this is a plain ``.py`` split (no packaging change) rather than
real ``.html``/``.css``/``.js`` files.
"""

from __future__ import annotations

# Where the page loads Telegram's SDK from. The daemon serves it at
# SDK_SRC_SELF when it has a copy (fetched once, cached under the data
# dir — see miniapp/webapp_sdk.py), which is the good case: the page then
# needs exactly ONE reachable host, the one that just delivered it.
# When the daemon has no copy, the page falls back to SDK_SRC_TELEGRAM,
# which is precisely what it did before roadmap 8.18 — so the worst case
# is the old behaviour, never worse. aipager does not redistribute
# Telegram's script, so there is no third option; server.py's
# _handle_index picks between these two per request.
SDK_SRC_SELF = "/telegram-web-app.js"
SDK_SRC_TELEGRAM = "https://telegram.org/js/telegram-web-app.js"

# The only piece of the page that varies. ``.format()`` is safe here and
# nowhere else in this package: this string has no other braces, while
# the CSS and JS below are full of them.
HTML_HEAD_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>aipager</title>
<script src="{sdk_src}"></script>
"""


def html_head(sdk_src: str = SDK_SRC_SELF) -> str:
    """The page head with the SDK ``<script>`` pointed at ``sdk_src``."""
    return HTML_HEAD_TEMPLATE.format(sdk_src=sdk_src)


# The default (self-served) head, kept as a module constant because that
# is the import shape the rest of the package and its tests already use.
HTML_HEAD = html_head()

# Markup only - no inline event handlers (onclick="..." etc): every
# listener is wired in _app.py via addEventListener, which keeps the page
# friendly to a strict Content-Security-Policy.
#
# The icons are one inline SVG sprite (line icons on a 24px grid, stroked
# in currentColor), used as <svg><use href="#i-..."></use></svg>. No
# xmlns: inline SVG in HTML does not need one, and the page carries no
# http(s) URL at all. Symbol ids named after a session action (i-stop,
# i-kill, ...) are the action menu's icons. A NON-raw Python string, so it
# must never contain a backslash.
HTML_BODY = """\
<meta name="color-scheme" content="light dark">
</head>
<body>
<svg class="sprite" aria-hidden="true" width="0" height="0" focusable="false">
  <symbol id="i-lantern" viewBox="0 0 24 24"><path d="M10 4.2a2 2 0 0 1 4 0"/><path d="M7.5 7.2h9M8 20.5h8"/><path d="M8.5 7.2l-.8 2.3v8.2c0 1.5 1 2.8 2.5 2.8h3.6c1.5 0 2.5-1.3 2.5-2.8V9.5l-.8-2.3"/><path d="M12 11.2c1.3 1.3 1.9 2.3 1.9 3.3a1.9 1.9 0 0 1-3.8 0c0-1 .6-2 1.9-3.3z"/></symbol>
  <symbol id="i-plus" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></symbol>
  <symbol id="i-chevron" viewBox="0 0 24 24"><path d="M9 6l6 6-6 6"/></symbol>
  <symbol id="i-dots" viewBox="0 0 24 24"><circle cx="12" cy="5.5" r="1.7" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.7" fill="currentColor" stroke="none"/><circle cx="12" cy="18.5" r="1.7" fill="currentColor" stroke="none"/></symbol>
  <symbol id="i-chat" viewBox="0 0 24 24"><path d="M20.5 11.5a8.5 8.5 0 0 1-12.4 7.6L3.5 20.5l1.4-4.4A8.5 8.5 0 1 1 20.5 11.5z"/><path d="M8.5 10.5h7M8.5 13.5h4.5"/></symbol>
  <symbol id="i-open" viewBox="0 0 24 24"><path d="M7 17L17 7M9 7h8v8"/></symbol>
  <symbol id="i-lock" viewBox="0 0 24 24"><rect x="5" y="10.5" width="14" height="10" rx="2.5"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5M12 14.5v2"/></symbol>
  <symbol id="i-question" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M9.6 9.4a2.5 2.5 0 0 1 4.8 1c0 1.7-2.4 2.1-2.4 3.6"/><circle cx="12" cy="17.2" r="1" fill="currentColor" stroke="none"/></symbol>
  <symbol id="i-stop" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2.5"/></symbol>
  <symbol id="i-clearqueue" viewBox="0 0 24 24"><path d="M4 6.5h11M4 11.5h8M4 16.5h6"/><path d="M15 14l5 5M20 14l-5 5"/></symbol>
  <symbol id="i-compact" viewBox="0 0 24 24"><path d="M4 14h6v6M20 10h-6V4M10 14l-6 6M14 10l6-6"/></symbol>
  <symbol id="i-resume" viewBox="0 0 24 24"><path d="M8 5.8v12.4a1 1 0 0 0 1.5.9l9.6-6.2a1 1 0 0 0 0-1.7L9.5 4.9A1 1 0 0 0 8 5.8z"/></symbol>
  <symbol id="i-rename" viewBox="0 0 24 24"><path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16v4zM13.5 6.5l4 4"/></symbol>
  <symbol id="i-kill" viewBox="0 0 24 24"><path d="M12 3.5v8M6.3 7a8 8 0 1 0 11.4 0"/></symbol>
  <symbol id="i-perms" viewBox="0 0 24 24"><path d="M12 3l7 3v5.5c0 4.4-3 8-7 9.5-4-1.5-7-5.1-7-9.5V6z"/><path d="M9 12l2 2 4-4"/></symbol>
  <symbol id="i-restart" viewBox="0 0 24 24"><path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3L19.5 9"/><path d="M19.5 4v5h-5"/></symbol>
  <symbol id="i-delete" viewBox="0 0 24 24"><path d="M4 7h16M10 11v6M14 11v6"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4.5A1.5 1.5 0 0 1 10.5 3h3A1.5 1.5 0 0 1 15 4.5V7"/></symbol>
  <symbol id="i-folder" viewBox="0 0 24 24"><path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/></symbol>
  <symbol id="i-spark" viewBox="0 0 24 24"><path d="M11 3.5l1.8 4.9 4.9 1.8-4.9 1.8L11 16.9l-1.8-4.9-4.9-1.8 4.9-1.8z"/><path d="M18 14.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/></symbol>
  <symbol id="i-refresh" viewBox="0 0 24 24"><path d="M4.5 12a7.5 7.5 0 0 1 13-5.1L19.5 9M19.5 4.5V9H15M19.5 12a7.5 7.5 0 0 1-13 5.1L4.5 15M4.5 19.5V15H9"/></symbol>
  <symbol id="i-check" viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></symbol>
</svg>

<header class="app-head">
  <div class="brand">
    <svg class="ic brand-mark" aria-hidden="true"><use href="#i-lantern"></use></svg>
    <div class="brand-text">
      <h1>aipager</h1>
      <div id="daemon-line" class="daemon-line">Loading…</div>
    </div>
  </div>
  <span id="conn-badge" class="conn conn-live" hidden></span>
</header>

<nav class="tabbar" id="tabbar" aria-label="Sections">
  <button type="button" class="tabbar-btn is-active" id="maintab-sessions">Sessions<span id="waiting-badge" class="badge" hidden></span></button>
  <button type="button" class="tabbar-btn" id="maintab-settings">Settings</button>
</nav>

<div id="error" role="alert"></div>
<div id="notice" role="status"></div>

<section id="view-grid">
  <div class="pulse-row">
    <div id="grid-totals" class="pulse" aria-live="polite"></div>
    <button type="button" id="new-session-btn" class="fab" aria-label="New session"><svg class="ic" aria-hidden="true"><use href="#i-plus"></use></svg></button>
  </div>
  <div id="needs-you" class="needs-you" hidden>
    <h2 class="sect-title sect-need"><span class="beacon-dot" aria-hidden="true"></span>Needs you</h2>
    <div id="needs-you-list" class="beacon-list"></div>
  </div>
  <div id="sessions" class="lanterns"></div>
  <div id="empty-state" class="empty" hidden>
    <div class="empty-lamp" aria-hidden="true"><svg class="ic"><use href="#i-lantern"></use></svg></div>
    <p class="empty-title">No sessions yet.</p>
    <p class="empty-text">Start one here or send /new in the chat.</p>
    <button type="button" id="empty-new" class="primary"><svg class="ic" aria-hidden="true"><use href="#i-plus"></use></svg>New session</button>
  </div>
  <div id="gone-wrap" class="shelf" hidden>
    <button type="button" class="gone-toggle" id="gone-toggle" aria-expanded="false"></button>
    <div id="sessions-gone" class="shelf-list" hidden></div>
  </div>
</section>

<section id="view-new" hidden>
  <div class="view-hero">
    <div class="hero-lamp" aria-hidden="true"><svg class="ic"><use href="#i-lantern"></use></svg></div>
    <div>
      <h2 class="view-title">New session</h2>
      <p class="view-sub">Name it, pick a folder, and Claude starts there.</p>
    </div>
  </div>
  <ol class="steps">
    <li class="step">
      <span class="step-num" aria-hidden="true">1</span>
      <div class="step-head"><label class="step-title" for="new-name">Name</label></div>
      <input id="new-name" class="field-input" type="text" autocomplete="off"
             autocapitalize="none" spellcheck="false" placeholder="frontend" maxlength="64">
      <div id="new-name-error" class="field-error" hidden></div>
    </li>
    <li class="step">
      <span class="step-num" aria-hidden="true">2</span>
      <div class="step-head"><span class="step-title">Where</span></div>
      <div id="new-cwd-chips" class="chips" hidden></div>
      <div id="new-cwd" class="groups"></div>
    </li>
    <li class="step">
      <span class="step-num" aria-hidden="true">3</span>
      <div class="step-head"><span class="step-title">Model</span></div>
      <div id="new-model-chips" class="chips" hidden></div>
      <div id="new-model" class="groups"></div>
    </li>
    <li class="step step-more">
      <span class="step-num" aria-hidden="true">+</span>
      <button type="button" class="disclosure" id="new-advanced-toggle" aria-expanded="false"><span>More options</span><svg class="ic chev" aria-hidden="true"><use href="#i-chevron"></use></svg></button>
      <div id="new-advanced" hidden>
        <div id="new-mode" class="groups"></div>
        <div id="new-mode-note" class="note" hidden>Auto mode requires admin.</div>
        <div id="new-prefs" class="groups"></div>
      </div>
    </li>
  </ol>
  <div id="new-summary" class="new-summary"></div>
  <button type="button" class="primary block" id="new-create">Start session</button>
</section>

<!--
  Scrim and confirm dialog. The action MENU is not here: it hangs off the
  kebab in the session header so it stays anchored to the button. Only
  one layer is ever open, so "what does Back close?" has one answer.
-->
<div id="overlay" class="overlay" hidden>
  <div id="confirm-modal" class="modal" role="dialog" aria-modal="true"
       aria-labelledby="confirm-title" hidden>
    <h2 id="confirm-title" class="modal-title"></h2>
    <p id="confirm-body" class="modal-body"></p>
    <input id="confirm-rename-input" class="field-input" type="text"
           autocomplete="off" autocapitalize="none" spellcheck="false"
           maxlength="64" aria-label="New name" hidden>
    <div id="confirm-rename-error" class="field-error" hidden></div>
    <div class="modal-actions">
      <button type="button" id="confirm-cancel" class="modal-btn">Cancel</button>
      <button type="button" id="confirm-ok" class="modal-btn is-danger"></button>
    </div>
  </div>
</div>

<!--
  Conditional reveals for the new-session form. They live here, outside
  any group, because group hosts are rebuilt on every structural render;
  renderOptionGroup MOVES a reveal under the row that revealed it and
  renderNewForm parks it back here, so typed text and focus survive.
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
              aria-label="Create this folder"><svg class="ic" aria-hidden="true"><use href="#i-plus"></use></svg></button>
    </div>
    <div id="new-folder-note" class="reveal-note"></div>
  </div>
</div>

<section id="view-settings" hidden>
  <h2 class="sect-title">Chat preferences</h2>
  <p class="sect-note" id="settings-intro">These apply to every session in this chat.</p>
  <div id="settings-groups" class="groups"></div>
  <div id="settings-readonly" class="note" hidden>Only an admin can change these.</div>
  <div id="updates-block" class="card updates" hidden>
    <div class="card-head">
      <span class="card-icon" aria-hidden="true"><svg class="ic"><use href="#i-refresh"></use></svg></span>
      <h3 class="card-title">Updates</h3>
    </div>
    <div id="updates-lines" class="updates-lines"></div>
    <div id="updates-source" class="updates-small" hidden></div>
    <div id="updates-restart" class="updates-small" hidden></div>
    <div id="updates-summary" class="updates-row" hidden></div>
    <div id="updates-job" class="updates-job" hidden></div>
    <div id="updates-actions" class="updates-actions"></div>
  </div>
</section>

<section id="view-detail" hidden>
  <div id="detail-header" class="d-head">
    <div id="detail-ring" class="lamp lamp-lg" aria-hidden="true"></div>
    <div class="d-title">
      <div class="d-title-row">
        <span id="detail-label" class="detail-label"></span>
        <span id="detail-status" class="status"></span>
      </div>
      <div id="detail-state" class="d-state"></div>
    </div>
    <button type="button" id="detail-quick" class="pill" hidden></button>
    <span class="kebab-wrap">
      <button type="button" id="detail-menu-btn" class="kebab"
              aria-haspopup="menu" aria-expanded="false"
              aria-label="Session actions" hidden><svg class="ic" aria-hidden="true"><use href="#i-dots"></use></svg></button>
      <div id="action-menu" class="menu" role="menu" hidden></div>
    </span>
  </div>
  <div id="detail-waiting" class="beacon beacon-solo" hidden>
    <div class="beacon-head">
      <span id="detail-waiting-lamp" class="lamp lamp-need lamp-sm" aria-hidden="true"></span>
      <div class="beacon-text">
        <span id="detail-waiting-kind" class="beacon-kicker"></span>
        <p id="detail-waiting-text" class="beacon-summary"></p>
      </div>
    </div>
    <div class="beacon-actions">
      <button type="button" id="detail-answer" class="primary btn-need" hidden><svg class="ic" aria-hidden="true"><use href="#i-chat"></use></svg><span>Answer in chat</span></button>
    </div>
    <div id="detail-answer-note" class="note" hidden></div>
  </div>
  <button type="button" id="detail-activity" class="activity" hidden></button>

  <h2 class="sect-title">Latest reply</h2>
  <div id="detail-preview" class="preview"></div>
  <dl id="detail-facts" class="facts"></dl>
  <!-- Switch the RUNNING session's model (roadmap 8.35). Drawn only for a
       live session; the note says why the picker is greyed out. -->
  <div id="detail-model" class="groups" hidden></div>
  <div id="detail-model-note" class="note" hidden></div>

  <h2 class="sect-title">Session settings</h2>
  <div id="session-settings-groups" class="groups"></div>
  <div id="session-settings-readonly" class="note" hidden>Only someone who can prompt this session can change these.</div>
  <button type="button" class="link-btn" id="session-settings-reset" hidden>Reset to defaults</button>

  <div class="groups disclosures">
    <button type="button" class="sect-toggle" id="tab-diff"></button>
    <div id="panel-diff" class="panel" hidden></div>
    <button type="button" class="sect-toggle" id="tab-timeline"></button>
    <div id="timeline-note" class="sect-note" hidden>The timeline only covers the current daemon run. It isn't saved across restarts, so an older session shows nothing here.</div>
    <div id="panel-timeline" class="panel" hidden></div>
  </div>
</section>
"""

HTML_TAIL = """\
</body>
</html>
"""

__all__ = ["HTML_BODY", "HTML_HEAD", "HTML_TAIL"]
