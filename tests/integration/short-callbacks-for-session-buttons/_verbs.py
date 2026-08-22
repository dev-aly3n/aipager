"""The verb vocabulary this suite tests against — copied verbatim from
entrypoints.md's "Verb table", not derived from anything in
``aipager/``. Used as TEST INPUT (what strings to probe with), never as
an expected-output threshold, so this does not fall under "a test must
never derive its bounds from the constant it is pinning": the 64-byte
budget assertions in this suite hardcode 64 (Telegram's own documented
API limit), not a value read off any of these lists.
"""

from __future__ import annotations

# Verbs converted onto the short form by this ship (design.md's "28
# sites"), excluding the 4 dynamic `opt<n>` — those are listed
# separately below since they need their own index-composition tests.
SESSION_SCOPED_VERBS = [
    "allow", "allow_always", "deny", "stop",
    "kill", "kill-confirm", "kill-cancel",
    "retry", "compact", "submit",
    "resume",
    "new_resume", "new_replace", "new_cancel",
    "perms_confirm", "perms_cancel", "perms_stop_switch", "perms_wait",
]

OPT_VERBS = ["opt0", "opt1", "opt2", "opt3"]

# Every verb entrypoints.md documents as session-scoped (short-form
# eligible), used for the "stale index -> every verb" sweep (task
# instruction #4) and the byte-budget sweep (#1).
ALL_SESSION_SCOPED_VERBS = SESSION_SCOPED_VERBS + OPT_VERBS

# The ⋮-menu family: entrypoints.md says these were "already short-form
# before this ship" and are "unchanged" by it — out of scope for
# design.md's conversion work, but still part of the documented
# grammar, so the stale-index guarantee ("regardless of verb") is
# tested against a sample of these too.
MENU_FAMILY_SAMPLE_VERBS = ["menu", "menu-close", "diff",
                             "resume-ask", "resume-auto", "resume-cancel"]

# Legacy, long-form-only verbs (entrypoints.md: "accepted for older
# buttons, never emitted now").
LEGACY_LONGFORM_ONLY_VERBS = [
    "resume_mode_ask", "resume_mode_auto", "resume_mode_cancel",
]

TELEGRAM_CALLBACK_DATA_LIMIT = 64

STALE_MESSAGE = "That session is no longer available"
NOT_FOUND_MESSAGE = "Session not found"
