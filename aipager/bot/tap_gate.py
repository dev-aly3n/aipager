"""Which capability a button tap needs (roadmap 8.75): one table.

Every inline-keyboard tap is checked in ``callbacks._dispatch_callback``
against :func:`required_capability` before any handler sees it. In scope
mode the tapper's role in the TAPPED MESSAGE'S chat must grant it:

- ``VIEW``: any member of the chat (re-renders that change nothing).
- ``APPROVE``: ``role.can_approve`` (answering a prompt Claude is waiting on).
- ``PROMPT``: ``role.can_prompt`` (anything that acts on a session or a
  card other members share).
- ``MANAGE``: may use Full access (``_may_use_full_access``: an admin, or
  the member in their own DM), like every other way into Full access.
- ``UPDATE``: ``_is_update_admin`` (installs software, restarts the daemon).

A verb the table does not know needs ``PROMPT`` (fail closed), and
``tests/test_tap_gate.py`` fails when a dispatcher compares a verb literal
the table does not list, so a new button cannot quietly fall to the
default. Sub-handlers keep their own stricter checks (the /new cards'
author binding, admin for a group's /settings writes, Full access on the
mode switch), which the table notes but does not replace.

Pure: no bot state. The two context-dependent cases (a /mode card whose
pending switch is to Full access; a ``_:spref`` tap that writes) are
decided by the caller-supplied ``perms_target_full`` and by the verb's own
shape.
"""

from __future__ import annotations

from enum import Enum

from aipager.i18n import Msg


class Cap(str, Enum):
    VIEW = "view"
    APPROVE = "approve"
    PROMPT = "prompt"
    MANAGE = "manage"
    UPDATE = "update"


VIEW, APPROVE, PROMPT, MANAGE, UPDATE = (
    Cap.VIEW, Cap.APPROVE, Cap.PROMPT, Cap.MANAGE, Cap.UPDATE)

# Sentinel namespaces: the part before the first ``:`` that is not a
# session name.
SENTINEL_NAMESPACES = frozenset({"_", "__voice__"})

# The voice extra's offer (``__voice__:<verb>``).
VOICE_CAPS: dict[str, Cap] = {
    "install": UPDATE,   # pip install into the daemon's venv
    "restart": UPDATE,   # restarts the daemon for everyone
    "cancel": PROMPT,    # closes the offer for whoever else sees it
}

# Exact verbs, in any namespace.
EXACT_CAPS: dict[str, Cap] = {
    # ---- answering a prompt Claude waits on ---------------------------
    "allow": APPROVE,
    "allow_always": APPROVE,     # also writes a standing allow rule
    "deny": APPROVE,
    "continue": APPROVE,
    "submit": APPROVE,           # multi-select AskUserQuestion
    "pin_answer": APPROVE,       # the pinned bar's "Answer <label>"
    # ---- busy card / error card --------------------------------------
    "stop": PROMPT,
    "retry": PROMPT,
    "compact": PROMPT,
    # ---- old /kill picker (answered "out of date") and End's cancel ---
    "kill": PROMPT,
    "kill-confirm": PROMPT,
    "kill-cancel": PROMPT,
    # ---- /status and the session menu (session_parity) ---------------
    "menu": VIEW,
    "menu-close": VIEW,          # closing a menu counts as looking
    "mode_show": VIEW,
    "diff": VIEW,
    "talk": PROMPT,
    "end": PROMPT,
    "restart": PROMPT,
    "restart-confirm": VIEW,     # an old button: answers "out of date"
    "restart-cancel": PROMPT,    # closes someone's confirm, like kill-cancel
    "rename": PROMPT,
    "rename-cancel": PROMPT,     # clears the chat's pending rename
    "delete": PROMPT,
    "delete-confirm": PROMPT,
    "delete-cancel": PROMPT,     # closes someone's confirm, like kill-cancel
    "resume": PROMPT,
    "resume-ask": PROMPT,
    # Ask and Auto are everyone's. A card from before Full access existed
    # carries resume-auto for what was then bypass; it now means Auto (a
    # safety check), which is narrower, never wider.
    "resume-auto": PROMPT,
    "resume-full": MANAGE,       # Full access, like every other way into it
    "resume-cancel": PROMPT,
    # ---- a session in a Claude Code background job (session_parity):
    # each relaunches it, like Restart, and re-checks Restart's rule ---
    "bgback": PROMPT,            # the park notice's "Bring it back now"
    "bgforce": PROMPT,           # the busy question's "Stop and bring back"
    "bgleave": PROMPT,           # the busy question's "Leave it"
    # ---- /mode's card (perms_confirm / perms_stop_switch: see below) --
    "perms_confirm": PROMPT,
    "perms_stop_switch": PROMPT,
    "perms_cancel": PROMPT,
    "perms_wait": PROMPT,
    # ---- legacy /resume mode picker ----------------------------------
    "resume_mode_ask": PROMPT,
    "resume_mode_auto": PROMPT,  # now Auto (a safety check), as resume-auto
    "resume_mode_cancel": PROMPT,
    # ---- /new name-conflict card (author-bound in its handler) -------
    "new_resume": PROMPT,
    "new_replace": PROMPT,
    "new_cancel": PROMPT,
    # ---- sentinel (``_:``) navigation and actions --------------------
    "st:list": VIEW,
    "st:ended": VIEW,
    "pick:cancel": VIEW,
    "resume_noop": VIEW,
    "clear_gone": PROMPT,        # hides the chat's ended sessions
    # /settings: browsing is open; a write is admin-gated in a group by
    # `_dispatch_settings_action` / `new_flow.handle_defaults_callback`.
    "set": VIEW,
    # Per-session preferences picker (a write is a 3-part spref: below).
    "spref": VIEW,
    # Ready card: switch to Full access (before the ``rdy_`` prefix below;
    # the handler re-checks the admin rule too).
    "rdy_full": MANAGE,
}

# Verbs whose suffix is a number (a turn key, an option index).
NUMBERED_CAPS: tuple[tuple[str, Cap], ...] = (
    ("opt", APPROVE),        # opt<N>: an answer option
    ("ststop", PROMPT),      # /status's Stop, keyed by turn
    ("pstop", PROMPT),       # /stop picker's Stop, keyed by turn
    ("endok", PROMPT),       # End confirm, keyed by turn
    ("restartok", PROMPT),   # Restart confirm, keyed by turn
    ("modeask", PROMPT),     # /mode switch to Ask, keyed by turn
    ("modeauto", PROMPT),    # /mode switch to Auto
    ("modefull", MANAGE),    # /mode switch to Full access: the handler
                             # re-checks the admin rule too (may_full)
    ("rdy_m", PROMPT),       # Ready card's model choice
)

# Verb families by prefix (checked after the exact and numbered ones).
PREFIX_CAPS: tuple[tuple[str, Cap], ...] = (
    ("now:", PROMPT),           # "Send now" under a queued message
    ("resume_page:", VIEW),     # /resume picker pages
    ("rdy_", PROMPT),           # Ready card (rdy_full: MANAGE, above)
    ("nw:", PROMPT),            # /new Name card (author-bound inside)
    ("up:", UPDATE),            # /update (re-checked inside)
    ("set:", VIEW),             # /settings browsing; writes gated inside
    ("spref:", VIEW),           # preference browsing; writes: see below
    # Problem reports: owner only, re-checked inside report_flow (no
    # capability says "owner", so a member's tap must reach that check).
    ("rp:", VIEW),
    # A Claude Code background job's buttons, keyed by the job they were
    # shown for (bgback<job>, bgforce<job>; the bare verbs are above).
    ("bgback", PROMPT),
    ("bgforce", PROMPT),
    # The same "Stop and bring back" after a restart-type mode switch,
    # carrying the mode asked for (bgmode<mode><job>): Full access is
    # MANAGE, like every other way into it (the handler re-checks it).
    ("bgmodefull", MANAGE),
    ("bgmodeask", PROMPT),
    ("bgmodeauto", PROMPT),
    ("bgmode", PROMPT),         # no other mode: refused inside (not a verb)
)


def _spref_cap(action: str) -> Cap | None:
    """``spref:<idx>:<section>:<token>`` writes one session's
    preference (PROMPT); every shorter form only browses (VIEW)."""
    if not action.startswith("spref:"):
        return None
    parts = [p for p in action[len("spref"):].split(":") if p != ""]
    return PROMPT if len(parts) >= 3 else VIEW


def required_capability(session_name: str, action: str, *,
                        perms_target_full: bool = False) -> Cap:
    """The capability a tap on ``<session_name>:<action>`` needs.

    ``perms_target_full``: for ``perms_confirm`` / ``perms_stop_switch``,
    whether the /mode card's pending record switches to Full access
    (looked up by the caller at tap time). Unknown verbs need ``PROMPT``.
    """
    if session_name == "__voice__":
        return VOICE_CAPS.get(action, PROMPT)
    if action in ("perms_confirm", "perms_stop_switch") and perms_target_full:
        return MANAGE
    spref = _spref_cap(action)
    if spref is not None:
        return spref
    cap = EXACT_CAPS.get(action)
    if cap is not None:
        return cap
    for prefix, cap in NUMBERED_CAPS:
        if action.startswith(prefix) and action[len(prefix):].isdigit():
            return cap
    for prefix, cap in PREFIX_CAPS:
        if action.startswith(prefix):
            return cap
    return PROMPT


REFUSED_TEXT = Msg("toast.gate_refused", "Your role can't do that here.",
                   note="Pop-up: the tapper's role in this chat may not use this button")
OTHER_CHAT_TEXT = Msg("toast.gate_other_chat", "This button belongs to another chat.",
                      note="Pop-up: a button tapped in a chat that is not its own")


__all__ = [
    "APPROVE",
    "Cap",
    "EXACT_CAPS",
    "MANAGE",
    "NUMBERED_CAPS",
    "OTHER_CHAT_TEXT",
    "PREFIX_CAPS",
    "PROMPT",
    "REFUSED_TEXT",
    "SENTINEL_NAMESPACES",
    "UPDATE",
    "VIEW",
    "VOICE_CAPS",
    "required_capability",
]
