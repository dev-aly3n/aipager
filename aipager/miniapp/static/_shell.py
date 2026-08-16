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

<div id="error" role="alert"></div>

<section id="view-grid">
  <div id="sessions"></div>
</section>

<section id="view-detail" hidden>
  <div id="detail-header">
    <span id="detail-label" class="detail-label"></span>
    <span id="detail-status" class="status"></span>
  </div>
  <div id="detail-meta" class="muted"></div>
  <div class="tabs">
    <button type="button" class="tab-btn" id="tab-timeline">Timeline</button>
    <button type="button" class="tab-btn" id="tab-diff">Diff</button>
  </div>
  <div id="panel-timeline" class="panel"></div>
  <div id="panel-diff" class="panel" hidden></div>
</section>
"""

HTML_TAIL = """\
</body>
</html>
"""

__all__ = ["HTML_BODY", "HTML_HEAD", "HTML_TAIL"]
