"""A session's permission mode: Ask, Auto or Full access.

aipager stores and passes around one of three ids:

- ``"ask"``: Claude Code's ``default`` mode (Claude Code shows it as
  Manual): Claude asks before each action. Launched with
  ``--permission-mode default``.
- ``"auto"``: Claude Code's ``auto`` mode: a second model reviews each
  action, runs the safe ones and stops the risky ones for a person to
  decide. Launched with ``--permission-mode auto``. The default for new
  sessions.
- ``"full"``: Claude Code's ``bypassPermissions`` mode: everything runs
  with no review. Launched with ``--dangerously-skip-permissions``. Only
  an admin may choose it.

Every launch passes its flag explicitly, so Claude Code's own starting
mode never decides. The flag that would put bypass into Claude Code's
Shift+Tab cycle (``--allow-`` in front of the bypass flag) is never
passed, so the cycle aipager counts presses in is ``default ->
acceptEdits -> plan -> auto``.

Pure and i18n-free: the hook's import closure reaches this module through
``aipager.state``, and nothing there may load ``aipager.i18n``. The shown
labels live in ``aipager.bot.mode_labels``.
"""

from __future__ import annotations

ASK = "ask"
AUTO = "auto"
FULL = "full"
#: The three ids, in the order every surface lists them.
MODES: tuple[str, str, str] = (ASK, AUTO, FULL)
#: Strictness: a higher number runs more without asking.
_WIDTH = {ASK: 0, AUTO: 1, FULL: 2}

#: The Claude Code flag each mode launches with. ``default`` is the config
#: value Claude Code's hooks report; its CLI also accepts ``manual``.
_FLAGS = {
    ASK: ("--permission-mode", "default"),
    AUTO: ("--permission-mode", "auto"),
    FULL: ("--dangerously-skip-permissions",),
}

#: What Claude Code's ``permission_mode`` (hooks) means for aipager.
#: ``other`` is a mode that is none of the three (shown by its own name).
_FROM_CLAUDE = {
    "default": ASK,
    "manual": ASK,
    "auto": AUTO,
    "bypassPermissions": FULL,
    "acceptEdits": "other",
    "plan": "other",
    "dontAsk": "other",
}
#: The raw Claude Code mode a session in each aipager mode reports.
_TO_CLAUDE = {ASK: "default", AUTO: "auto", FULL: "bypassPermissions"}

#: Claude Code's Shift+Tab cycle without bypass in it (aipager never
#: passes the flag that adds it), with and without Auto
#: (Auto leaves the cycle when it is not available for the session).
_CYCLE = ("default", "acceptEdits", "plan", "auto")
_CYCLE_NO_AUTO = ("default", "acceptEdits", "plan")

#: What ``inject.launch_session`` answers for a mode that is none of the
#: three (a caller still passing the old boolean): English, like the other
#: launch errors.
INVALID_MODE_ERROR = "Invalid permission mode"

#: Seconds between two Shift+Tab presses (one ``dtach -p`` write each).
MODE_PRESS_GAP: float = 0.15
#: A hook arriving sooner than this after aipager's presses was very
#: likely fired before Claude Code read them: it neither confirms nor
#: contradicts the switch.
MODE_SETTLE_SECONDS: float = 1.5


def is_mode(value: object) -> bool:
    """True for exactly ``"ask"``, ``"auto"`` or ``"full"`` (never a bool)."""
    return isinstance(value, str) and value in _WIDTH


def launch_args(mode: object) -> tuple[str, ...]:
    """Claude Code's argv words for *mode*; ValueError (carrying the
    value) for anything that is not one of the three ids (``True`` and
    ``False`` included)."""
    if not is_mode(mode):
        raise ValueError(mode)
    return _FLAGS[mode]  # type: ignore[index]


def launch_flag(mode: object) -> str:
    """The Claude Code flag text for *mode* (``--permission-mode auto``);
    ValueError for anything that is not one of the three ids."""
    return " ".join(launch_args(mode))


def from_claude(raw: object) -> str | None:
    """aipager's id for a ``permission_mode`` Claude Code reported:
    ``ask``, ``auto``, ``full``, ``other`` (acceptEdits, plan, dontAsk),
    or None for anything it does not know (and for a non-string)."""
    if not isinstance(raw, str):
        return None
    return _FROM_CLAUDE.get(raw)


def to_claude(mode: str) -> str:
    """The raw Claude Code mode a session in *mode* reports."""
    return _TO_CLAUDE[mode]


def presses(from_raw: str, to_mode: str, *, auto_in_cycle: bool = True) -> int | None:
    """How many Shift+Tab presses move a session from Claude Code's
    *from_raw* mode to aipager's *to_mode* (``ask`` or ``auto``): 0 when it
    is already there; None when Shift+Tab cannot get there (the start is
    bypass, dontAsk or unknown; the target is Full access; or Auto with
    *auto_in_cycle* False)."""
    cycle = _CYCLE if auto_in_cycle else _CYCLE_NO_AUTO
    if to_mode not in (ASK, AUTO):
        return None
    target = _TO_CLAUDE[to_mode]
    if from_raw == "manual":
        from_raw = "default"
    if from_raw not in cycle or target not in cycle:
        return None
    return (cycle.index(target) - cycle.index(from_raw)) % len(cycle)


def after_presses(from_raw: str, count: int, *, auto_in_cycle: bool = True) -> str:
    """Claude Code's raw mode *count* Shift+Tab presses after *from_raw*,
    along the cycle :func:`presses` counts in; "" when *from_raw* is not
    in it."""
    cycle = _CYCLE if auto_in_cycle else _CYCLE_NO_AUTO
    if from_raw == "manual":
        from_raw = "default"
    if from_raw not in cycle:
        return ""
    return cycle[(cycle.index(from_raw) + count) % len(cycle)]


def mode_from_saved(record: object) -> str:
    """A saved session's mode (the state file): a valid ``perm_mode``
    wins; an older file's ``skip_perms`` True was Auto-that-bypassed, so
    it is Full access; anything else is Ask (what its owner picked)."""
    if not isinstance(record, dict):
        return ASK
    mode = record.get("perm_mode")
    if is_mode(mode):
        return mode  # type: ignore[return-value]
    return FULL if record.get("skip_perms") is True else ASK


def mode_from_new_session_prefs(raw: object) -> str:
    """A chat's New sessions mode (the preferences file): a valid
    ``new_session_perm_mode`` wins; the older ``new_session_mode`` "auto"
    meant bypass, so it is Full access, "ask" is Ask, and anything else
    ("" and missing included) is Auto, the default."""
    if not isinstance(raw, dict):
        return AUTO
    mode = raw.get("new_session_perm_mode")
    if is_mode(mode):
        return mode  # type: ignore[return-value]
    legacy = raw.get("new_session_mode")
    if legacy == "auto":
        return FULL
    if legacy == "ask":
        return ASK
    return AUTO


def narrowest_allowed(mode: str, may_full: bool) -> str:
    """*mode*, or Auto when it is Full access and the person may not use
    it. The one fallback every surface uses."""
    if mode == FULL and not may_full:
        return AUTO
    return mode


def wider(a: str, b: str) -> bool:
    """True when mode *a* runs more without asking than mode *b*."""
    return _WIDTH.get(a, 0) > _WIDTH.get(b, 0)
