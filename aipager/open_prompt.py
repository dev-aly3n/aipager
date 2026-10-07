"""The open permission prompt as the state file keeps it (roadmap 8.102).

When the daemon restarts while a session waits on a permission prompt (or
a single AskUserQuestion) shown in Telegram, the next daemon brings the
session back as waiting, with the prompt's buttons still answering it
(``LifecycleMixin._restore_open_prompt``). What makes that possible is
this record: a sanitized, JSON-only copy of the prompt, built at save time
from the live ``pending_permission`` / ``pending_prompt_msg`` and checked
field by field when it is read back.

The live dicts are not persisted as they are: they hold values that are
not JSON (``"selected": set()``, an ``InlineKeyboardMarkup``), values that
mean nothing to the next process (``wait_started_at`` is monotonic, the
``prompt_token`` comes from a counter that restarts at 1), and tool input
that can be megabytes of Write content no answer path reads. The record
keeps only what the answer path and the restore need, and the restore
rebuilds the process-local parts.

Pure functions, no I/O and no clock reads: the callers pass the clocks.
No module-level import from the rest of aipager (``aipager.state`` imports
this module).
"""

from __future__ import annotations

import math
import re

#: The record's schema version; any other value is ignored at load.
RECORD_VERSION = 1

#: Length caps (characters). Display strings over a cap are cut when the
#: record is built; a loaded record over a cap is invalid as a whole.
MAX_TOOL_USE_ID = 200
MAX_TOOL_NAME = 200
MAX_SUMMARY = 1000
MAX_DETAIL = 4000
MAX_TEXT = 4096          # a Telegram message's own limit
MAX_HOOK_ADDR = 107      # an AF_UNIX path's limit
MAX_REQUEST_ID = 200
MAX_OPTION_LABEL = 200
MAX_OPTION_DESCRIPTION = 1000
MAX_OPTIONS = 4          # the question keyboard shows the first four

#: The standing-rule suggestion types an "Allow always" may echo. The
#: third copy of this set: ``aipager.dtach.hook_receiver`` and
#: ``aipager.bot.callbacks`` each keep their own
#: ``_STANDING_RULE_SUGGESTION_TYPES`` (the callback re-checks it, so a
#: loaded suggestion of another type could never be echoed anyway).
STANDING_RULE_SUGGESTION_TYPES = frozenset({"addRules", "addDirectories"})

ASK_TOOL = "AskUserQuestion"

#: The optional ``input_digest`` key: the sha256 hex digest of the tool
#: call's whole input that the PermissionRequest stored, so a restored
#: prompt is still closed by its own call's PostToolUse and by no other.
#: Absent when the live prompt had none; then nothing but a tap, its
#: turn's end or the restart's own check closes the restored prompt.
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")

_INLINE_KEYS = frozenset({"v", "shown_wall", "tool_use_id", "kind",
                          "card_msg_id", "perm"})
_SEPARATE_KEYS = frozenset({"v", "shown_wall", "tool_use_id", "kind",
                            "chat_id", "msg_id", "text", "summary", "perm"})
_PERM_KEYS = frozenset({"tool_name", "tool_summary", "detail",
                        "always_available", "standing_rule_suggestion",
                        "hook_reply", "question"})
_HOOK_REPLY_KEYS = frozenset({"addr", "request_id"})
_QUESTION_KEYS = frozenset({"question", "options"})
_OPTION_KEYS = frozenset({"label", "description"})


def _is_int(x) -> bool:
    return type(x) is int


def _is_number(x) -> bool:
    return type(x) in (int, float) and math.isfinite(x)


def _str_within(x, cap: int, *, non_empty: bool = False) -> bool:
    return (isinstance(x, str) and len(x) <= cap
            and (bool(x) or not non_empty))


def _cut(x, cap: int) -> str:
    return x[:cap] if isinstance(x, str) else ""


# ── building the record ─────────────────────────────────────────────────

def _question_record(questions) -> dict | None:
    """The one restorable question of an AskUserQuestion, or None.

    Only a form with exactly ONE question, single choice, 1..4 options:
    the answer to anything else is relative to the TUI's cursor and tab
    position, which a restart cannot see (design.md "AskUserQuestion")."""
    if not isinstance(questions, list) or len(questions) != 1:
        return None
    q = questions[0]
    if not isinstance(q, dict) or q.get("multiSelect"):
        return None
    options = q.get("options")
    if not isinstance(options, list) or not 1 <= len(options) <= MAX_OPTIONS:
        return None
    clean = []
    for opt in options:
        if not isinstance(opt, dict):
            return None
        label = opt.get("label")
        if not isinstance(label, str) or not label or len(label) > MAX_OPTION_LABEL:
            return None
        desc = opt.get("description", "")
        clean.append({"label": label,
                      "description": _cut(desc, MAX_OPTION_DESCRIPTION)})
    text = q.get("question", "?")
    return {"question": _cut(text if isinstance(text, str) else "?", MAX_SUMMARY),
            "options": clean}


def _hook_reply_record(hr) -> dict | None:
    """The parked hook's reply channel as is, or None when it is not the
    exact two-key shape (a malformed channel answers by keystrokes)."""
    if (isinstance(hr, dict) and set(hr) == _HOOK_REPLY_KEYS
            and _str_within(hr["addr"], MAX_HOOK_ADDR, non_empty=True)
            and _str_within(hr["request_id"], MAX_REQUEST_ID, non_empty=True)):
        return {"addr": hr["addr"], "request_id": hr["request_id"]}
    return None


def snapshot(sess, now_mono: float, now_wall: float) -> dict | None:
    """The record of the prompt *sess* is waiting on, or None.

    None unless the session is INTERACTIVE on exactly one surface (inline:
    ``pending_permission`` on a busy card; separate: ``pending_prompt_msg``
    whose message reached the chat), the prompt was shown during this wait
    (its ``wait_started_at`` is not older than ``interactive_entered_at``)
    at a known wall time, its tool is named, it was not asked inside a
    subagent, and, for an AskUserQuestion, it is the restorable kind (one
    question, single choice, untouched).
    *now_mono* is unused today and kept for symmetry with the other
    computed keys of the save."""
    from aipager.state import Status  # local: state imports this module

    del now_mono
    if sess.status is not Status.INTERACTIVE:
        return None
    inline = sess.pending_permission
    record: dict = {"v": RECORD_VERSION}
    if inline:
        card = sess.busy_msg_id
        if not (_is_int(card) and card > 0) or not isinstance(inline, dict):
            return None
        src = inline
        record["kind"] = "inline"
        record["card_msg_id"] = card
    else:
        sent = sess.pending_prompt_msg
        if not isinstance(sent, dict):
            return None
        msg_id = sent.get("msg_id")
        chat_id = sent.get("chat_id")
        text = sent.get("text")
        if not (_is_int(msg_id) and msg_id > 0) or not _is_int(chat_id):
            return None
        if not _str_within(text, MAX_TEXT, non_empty=True):
            return None
        src = sent.get("perm")
        if not isinstance(src, dict):
            return None
        record["kind"] = "separate"
        record["chat_id"] = chat_id
        record["msg_id"] = msg_id
        record["text"] = text
        record["summary"] = _cut(sent.get("summary"), MAX_SUMMARY)

    # Shown during THIS wait: a prompt dict an earlier wait left behind
    # (``pending_prompt_msg`` is never cleared on answer) is not it.
    wait = src.get("wait_started_at")
    if not _is_number(wait) or wait < sess.interactive_entered_at:
        return None
    shown = src.get("shown_wall")
    if not _is_number(shown) or shown <= 0:
        return None
    record["shown_wall"] = float(min(shown, now_wall))

    tool_info = src.get("tool_info")
    if not isinstance(tool_info, dict):
        return None
    name = tool_info.get("name")
    if not _str_within(name, MAX_TOOL_NAME, non_empty=True):
        return None
    # Asked inside a subagent: its answer goes to the subagent's own
    # transcript, which the restart's check does not read, so a prompt
    # answered in the terminal while the daemon was down would look open.
    # Not saved: the restart handles the session as before 8.102.
    if tool_info.get("agent_id"):
        return None
    tuid = tool_info.get("tool_use_id")
    record["tool_use_id"] = (tuid if _str_within(tuid, MAX_TOOL_USE_ID)
                             else "")
    digest = tool_info.get("input_digest")
    if name != ASK_TOOL and _is_digest(digest):
        record["input_digest"] = digest

    if name == ASK_TOOL:
        if not src.get("ask_question"):
            return None  # the degraded "(loading…)" prompt
        if record["kind"] == "inline":
            # Untouched from Telegram: no question advanced, no row moved,
            # nothing ticked. (Multi-select is refused with the question
            # itself, below.)
            if (src.get("current_idx", 0) != 0 or src.get("cursor_pos", 0) != 0
                    or src.get("selected")):
                return None
            questions = src.get("questions")
        else:
            questions = (tool_info.get("input") or {}).get("questions") \
                if isinstance(tool_info.get("input"), dict) else None
        question = _question_record(questions)
        if question is None:
            return None
        summary = tool_info.get("summary") or question["question"]
        record["perm"] = {
            "tool_name": name,
            "tool_summary": _cut(summary, MAX_SUMMARY),
            "detail": "",
            "always_available": None,
            "standing_rule_suggestion": None,
            "hook_reply": None,
            "question": question,
        }
    else:
        if src.get("ask_question"):
            return None
        always = tool_info.get("always_available")
        if type(always) is not bool:
            always = None  # unknown: no Allow always is offered
        rule = tool_info.get("standing_rule_suggestion")
        if not (isinstance(rule, dict) and isinstance(rule.get("type"), str)
                and rule["type"] in STANDING_RULE_SUGGESTION_TYPES):
            rule = None
        summary = src.get("tool_summary")
        if not isinstance(summary, str):
            summary = tool_info.get("summary") or ""
        record["perm"] = {
            "tool_name": name,
            "tool_summary": _cut(summary, MAX_SUMMARY),
            "detail": _cut(tool_info.get("detail"), MAX_DETAIL),
            "always_available": always,
            "standing_rule_suggestion": rule,
            "hook_reply": _hook_reply_record(src.get("hook_reply")),
            "question": None,
        }
    # The record must read back: what would not pass the loader is not
    # written (a suggestion that is not JSON, say).
    return validate(record, busy_msg_id=sess.busy_msg_id, now_wall=now_wall)


# ── reading it back ─────────────────────────────────────────────────────

def _is_digest(x) -> bool:
    return type(x) is str and _DIGEST_RE.fullmatch(x) is not None


def _valid_question(q) -> dict | None:
    if not (isinstance(q, dict) and set(q) == _QUESTION_KEYS):
        return None
    if not _str_within(q["question"], MAX_SUMMARY):
        return None
    options = q["options"]
    if not isinstance(options, list) or not 1 <= len(options) <= MAX_OPTIONS:
        return None
    clean = []
    for opt in options:
        if not (isinstance(opt, dict) and set(opt) == _OPTION_KEYS):
            return None
        if not _str_within(opt["label"], MAX_OPTION_LABEL, non_empty=True):
            return None
        if not _str_within(opt["description"], MAX_OPTION_DESCRIPTION):
            return None
        clean.append({"label": opt["label"], "description": opt["description"]})
    return {"question": q["question"], "options": clean}


def _valid_json(x, depth: int = 0) -> bool:
    """Plain JSON data, finite numbers, not too deep."""
    if depth > 8:
        return False
    if x is None or isinstance(x, (bool, str)):
        return True
    if type(x) in (int, float):
        return math.isfinite(x)
    if isinstance(x, list):
        return all(_valid_json(v, depth + 1) for v in x)
    if isinstance(x, dict):
        return all(isinstance(k, str) and _valid_json(v, depth + 1)
                   for k, v in x.items())
    return False


def _valid_perm(p) -> dict | None:
    if not (isinstance(p, dict) and set(p) == _PERM_KEYS):
        return None
    name = p["tool_name"]
    if not _str_within(name, MAX_TOOL_NAME, non_empty=True):
        return None
    if not _str_within(p["tool_summary"], MAX_SUMMARY):
        return None
    if not _str_within(p["detail"], MAX_DETAIL):
        return None
    always = p["always_available"]
    if not (always is None or type(always) is bool):
        return None
    rule = p["standing_rule_suggestion"]
    if rule is not None and not (
            isinstance(rule, dict)
            and rule.get("type") in STANDING_RULE_SUGGESTION_TYPES
            and _valid_json(rule)):
        return None
    hr = p["hook_reply"]
    if hr is not None and _hook_reply_record(hr) is None:
        return None
    question = p["question"]
    if question is not None:
        question = _valid_question(question)
        if question is None:
            return None
    # A question exactly when the tool is AskUserQuestion, and a question
    # never answers through a hook (nor carries an input digest: see
    # ``validate``).
    if (name == ASK_TOOL) != (question is not None):
        return None
    if question is not None and hr is not None:
        return None
    return {"tool_name": name, "tool_summary": p["tool_summary"],
            "detail": p["detail"], "always_available": always,
            "standing_rule_suggestion": rule,
            "hook_reply": _hook_reply_record(hr) if hr is not None else None,
            "question": question}


def validate(raw, *, busy_msg_id, now_wall: float) -> dict | None:
    """A normalized copy of the saved record *raw*, or None when anything
    about it is off: an unknown version, a wrong type (a bool where an int
    belongs, a NaN or an infinity), a missing or extra key at any level, a
    ``shown_wall`` in the future, an inline prompt whose card is not the
    session's saved busy card (*busy_msg_id*), a standing suggestion of
    another type, a question on a tool other than AskUserQuestion or the
    reverse, an ``input_digest`` (optional) that is not 64 lowercase hex
    characters or that sits on a question. Fail closed: the whole record goes. Never raises."""
    try:
        return _validate(raw, busy_msg_id=busy_msg_id, now_wall=now_wall)
    except Exception:  # noqa: BLE001 - a loader of untrusted data
        return None


def _validate(raw, *, busy_msg_id, now_wall: float) -> dict | None:
    if not isinstance(raw, dict):
        return None
    if not (_is_int(raw.get("v")) and raw["v"] == RECORD_VERSION):
        return None
    kind = raw.get("kind")
    keys = set(raw) - {"input_digest"}
    if kind == "inline":
        if keys != _INLINE_KEYS:
            return None
    elif kind == "separate":
        if keys != _SEPARATE_KEYS:
            return None
    else:
        return None
    has_digest = "input_digest" in raw
    if has_digest and not _is_digest(raw["input_digest"]):
        return None
    shown = raw["shown_wall"]
    if not (_is_number(shown) and 0 < shown <= now_wall):
        return None
    tuid = raw["tool_use_id"]
    if not _str_within(tuid, MAX_TOOL_USE_ID):
        return None
    perm = _valid_perm(raw["perm"])
    if perm is None:
        return None
    if has_digest and perm["question"] is not None:
        return None  # a question is closed by its own id alone
    out: dict = {"v": RECORD_VERSION, "kind": kind, "shown_wall": float(shown),
                 "tool_use_id": tuid, "perm": perm}
    if has_digest:
        out["input_digest"] = raw["input_digest"]
    if kind == "inline":
        card = raw["card_msg_id"]
        if not (_is_int(card) and card > 0 and card == busy_msg_id):
            return None
        out["card_msg_id"] = card
    else:
        chat_id, msg_id = raw["chat_id"], raw["msg_id"]
        if not _is_int(chat_id) or not (_is_int(msg_id) and msg_id > 0):
            return None
        if not _str_within(raw["text"], MAX_TEXT, non_empty=True):
            return None
        if not _str_within(raw["summary"], MAX_SUMMARY):
            return None
        out.update(chat_id=chat_id, msg_id=msg_id, text=raw["text"],
                   summary=raw["summary"])
    return out
