"""Property-based guard: every Telegram callback_data= constructed
anywhere under aipager/ must be provably <=64 bytes for ANY legal
session name (session names are themselves capped at 64 bytes by
inject._VALID_NAME / inject.launch_session — design.md).

Hand-enumeration of "unsafe" sites has undercounted this exact bug
three times in this codebase (13, then a "verified" 23, then 28 found
during design) — twice because the scanner looked for a fixed list of
variable-name spellings (`session_name`, `sess.name`, ...) and missed
sites spelled `target_name` / `existing.name`. This guard does not
enumerate call sites or variable names; it enumerates the SHAPES a
callback_data= value is allowed to take, and fails anything else shut.

Every `callback_data=` keyword value anywhere under aipager/**.py is
exactly one of:

  1. A plain string literal (`ast.Constant`) — checked directly: must
     encode to <= 64 bytes on its own. No session name can be embedded
     in a literal, so no allow-list is needed here.
  2. A call to `session_cb(...)` (bare, or qualified as
     `session_parity.session_cb(...)`) — the ONE legitimate way to
     embed a session after this ship. `session_cb` itself guarantees
     `_:sx:<idx>:<verb>` fits regardless of session name (its own
     docstring + design.md); this guard re-checks the `verb` argument
     specifically, since that is the one part of the format that could
     reintroduce an overflow through an unbounded dynamic component.
  3. Something else (an f-string, a bare variable) that matches one of
     the short, reviewed ALLOWED_* signatures below — each one is a
     NON-session interpolation (a bounded table index, a fixed schema
     key, a `cb_prefix`/`back_action` string itself built from other
     already-safe pieces) with a one-line justification for why it can
     never carry a session name. None of these are new to this ship —
     they're pre-existing, already-short, non-session-scoped callbacks
     (the wizard, /settings, per-session preferences, the /resume
     pager) that design.md's "Out of scope" section explicitly excludes.

Anything that is none of the above — `.format()`, `%`-formatting,
string concatenation, an f-string or bare name not on the allow-list —
FAILS the test with file:line and the source text, forcing a human to
either convert the site to `session_cb(...)` or add a justified
allow-list entry. This is deliberately default-fail: the guard does not
need a complete list of unsafe patterns (that list has failed three
times); it only needs a short list of known-safe ones.
"""

from __future__ import annotations

import ast
from pathlib import Path

AIPAGER_ROOT = Path(__file__).resolve().parents[1] / "aipager"

SESSION_CB_ATTR = "session_cb"

# _:sx:<idx>:<verb> — an implausible 19-digit index is the worst case
# design.md itself measures ("even an implausible 19-digit index totals
# 29 bytes"); reused here so this guard checks the exact bound the
# design commits to, not an arbitrary one of its own.
_WORST_CASE_INDEX = "9" * 19

# ---- allow-list: known-safe NON-session interpolations --------------
#
# Each entry is a normalized signature for a callback_data= value that
# is not a plain literal and not a session_cb(...) call, together with
# why it can never carry a session name. Signatures for f-strings keep
# literal text as-is and replace each `{expr}` hole with
# `{<unparsed expr>}` (see _joinedstr_signature) — so two DIFFERENT
# expressions produce two DIFFERENT signatures; this is not a generic
# "any f-string starting with _:nw: passes" allowance. A new site whose
# signature doesn't already appear here fails the guard, on purpose.

# Bare-Name callback_data= values: the identifier itself is the
# signature. Each of these is a local variable built a couple of lines
# above its one use, from a `*:<idx>` or `*:<prefix>` template — never
# a session name (grep aipager/ for the assignment to confirm on
# review; each is also out of scope for this ship — already short-form
# before it started, per entrypoints.md / design.md's "Out of scope").
ALLOWED_NAME_SIGNATURES = frozenset({
    # new_flow.py's _back_cancel_kb(back_action) — every call site
    # passes a literal "_:nw:..." constant (grep `_back_cancel_kb\(`),
    # never a session-derived string.
    "back_action",
    # session_parity.py's per-session-preferences `cb_prefix` param —
    # always f"_:spref:{idx}" at both call sites (grep `cb_prefix=`),
    # never a session name.
    "cb_prefix",
    # session_parity.py's _render_session_menu: `pref_cb =
    # f"_:spref:{idx}"`, assigned two lines above its one use. Same
    # index scheme as cb_prefix.
    "pref_cb",
})

# f-string (JoinedStr) callback_data= values, keyed by the normalized
# template described above.
ALLOWED_JOINEDSTR_SIGNATURES = frozenset({
    # new_flow.py wizard tokens — out of scope for this ship
    # (design.md: "never embed a session"). `entry['section']` / `idx`
    # / `section` / `token` all come from a fixed schema or a small
    # enumerated choice list, never a session name.
    "_:nw:opt:pref:{entry['section']}",
    "_:nw:model:{idx}",
    "_:nw:path:{idx}",
    "_:nw:pref:{section}:{token}",
    "_:nw:pref:{section}:default",
    # settings_menu.py — chat-level /settings, never session-scoped.
    "_:set:{section}",
    "_:set:{section}:{token}",
    # session_parity.py's per-session-preferences index scheme —
    # already short-form before this ship (entrypoints.md).
    # Interpolates a table INDEX (int) or a `cb_prefix` string built
    # the same way, never a session name.
    "_:spref:{table.index(sess.name)}",
    "{cb_prefix}:{section}",
    "{cb_prefix}:{section}:{token}",
    "{cb_prefix}:{section}:default",
    # dashboard.py's /resume pager — interpolates a page NUMBER, never
    # a session name.
    "_:resume_page:{page - 1}",
    "_:resume_page:{page + 1}",
})

# verb= arguments to session_cb(...) that are f-strings rather than a
# plain literal. `opt<n>` is the only dynamic verb in the codebase, and
# `<n>` is always an options-list position sliced to `[:4]` (design.md's
# "opt<N> composition" note) — bounded to a single digit in practice.
ALLOWED_VERB_JOINEDSTR_SIGNATURES = frozenset({
    "opt{i}",
    "opt{num - 1}",
})


def _joinedstr_signature(node: ast.JoinedStr) -> str:
    parts = []
    for value in node.values:
        if isinstance(value, ast.Constant):
            parts.append(str(value.value))
        elif isinstance(value, ast.FormattedValue):
            parts.append("{" + ast.unparse(value.value) + "}")
        else:  # pragma: no cover - JoinedStr only ever holds these two
            parts.append(ast.unparse(value))
    return "".join(parts)


def _is_session_cb_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr == SESSION_CB_ATTR
    if isinstance(func, ast.Name):
        return func.id == SESSION_CB_ATTR
    return False


def _verb_arg(call: ast.Call) -> ast.AST | None:
    if len(call.args) >= 4:
        return call.args[3]
    for kw in call.keywords:
        if kw.arg == "verb":
            return kw.value
    return None


def _verb_violation(call: ast.Call, filename: str, lineno: int) -> str | None:
    """``None`` if the verb argument to this ``session_cb(...)`` call is
    provably bounded; otherwise a violation message."""
    verb = _verb_arg(call)
    if verb is None:
        return f"{filename}:{lineno}: session_cb(...) call with no verb argument found"

    if isinstance(verb, ast.Constant) and isinstance(verb.value, str):
        worst = f"_:sx:{_WORST_CASE_INDEX}:{verb.value}"
        if len(worst.encode()) > 64:
            return (
                f"{filename}:{lineno}: verb {verb.value!r} would overflow 64 "
                f"bytes at a plausible index ({len(worst.encode())} bytes): {worst!r}"
            )
        return None

    if isinstance(verb, ast.JoinedStr):
        sig = _joinedstr_signature(verb)
        if sig in ALLOWED_VERB_JOINEDSTR_SIGNATURES:
            return None
        return (
            f"{filename}:{lineno}: session_cb(...) verb f-string {sig!r} is not "
            f"on the reviewed allow-list (ALLOWED_VERB_JOINEDSTR_SIGNATURES) — "
            f"add it there with a justification, or make it a literal verb"
        )

    return (
        f"{filename}:{lineno}: session_cb(...) verb argument is "
        f"{ast.unparse(verb)!r} — neither a literal nor a reviewed f-string; "
        f"add a case to test_callback_data_budget.py or simplify the call"
    )


def _value_violation(value: ast.AST, filename: str, lineno: int) -> str | None:
    """``None`` if ``value`` (the callback_data= keyword's value) is
    provably safe; otherwise a violation message."""
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        if len(value.value.encode()) > 64:
            return (
                f"{filename}:{lineno}: literal callback_data {value.value!r} is "
                f"{len(value.value.encode())} bytes, over the 64-byte cap"
            )
        return None

    if _is_session_cb_call(value):
        return _verb_violation(value, filename, lineno)

    if isinstance(value, ast.Name):
        if value.id in ALLOWED_NAME_SIGNATURES:
            return None
        return (
            f"{filename}:{lineno}: callback_data=<bare name {value.id!r}> is not "
            f"on the reviewed allow-list (ALLOWED_NAME_SIGNATURES) — trace where "
            f"it's built and either add it there with a justification or convert "
            f"the site to session_cb(...)"
        )

    if isinstance(value, ast.JoinedStr):
        sig = _joinedstr_signature(value)
        if sig in ALLOWED_JOINEDSTR_SIGNATURES:
            return None
        return (
            f"{filename}:{lineno}: f-string callback_data {sig!r} is not on the "
            f"reviewed allow-list (ALLOWED_JOINEDSTR_SIGNATURES) — this is "
            f"exactly the pattern that caused the original undercount (a session "
            f"name embedded via f-string). Convert to session_cb(...), or add a "
            f"justified allow-list entry if it truly never carries a session name"
        )

    return (
        f"{filename}:{lineno}: callback_data= built via {type(value).__name__} "
        f"({ast.unparse(value)!r}) — not a literal, not session_cb(...), and not "
        f"on any allow-list. Convert to session_cb(...) or add a justified entry"
    )


def _find_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    violations = []
    rel = path.relative_to(AIPAGER_ROOT.parent)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "callback_data":
                continue
            problem = _value_violation(kw.value, str(rel), node.lineno)
            if problem is not None:
                violations.append(problem)
    return violations


def _all_python_files() -> list[Path]:
    return sorted(AIPAGER_ROOT.rglob("*.py"))


def test_no_callback_data_can_overflow_the_64_byte_cap():
    violations = []
    for path in _all_python_files():
        violations.extend(_find_violations(path))
    assert not violations, (
        "callback_data= built in a way this guard cannot prove is <=64 bytes "
        "for every legal session name:\n" + "\n".join(violations)
    )


def test_make_cb_does_not_exist_anywhere_under_aipager():
    """entrypoints.md / design.md: `_make_cb` is deleted, with zero
    remaining callers. A grep-level check independent of the AST walk
    above — belt and braces, since `_make_cb` disappearing is itself
    one of design.md's own success criteria."""
    hits = [str(path) for path in _all_python_files() if "_make_cb" in path.read_text()]
    assert not hits, f"_make_cb still referenced in: {hits}"


# ---- guard-is-load-bearing self-tests ---------------------------------
#
# The 31+ documented "tests passing for reasons unrelated to their name"
# incidents in this codebase make an unverified guard worse than no
# guard — see spec.md's Constraints. These feed synthetic source through
# the same violation-finding functions the real walk above uses, so a
# regression in the guard's own logic (not the production tree) fails
# loudly here instead of silently passing everything.

def _violations_in_source(source: str) -> list[str]:
    tree = ast.parse(source, filename="<synthetic>")
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "callback_data":
                continue
            problem = _value_violation(kw.value, "<synthetic>", node.lineno)
            if problem is not None:
                violations.append(problem)
    return violations


def test_guard_catches_a_raw_fstring_session_name():
    """The exact bug class this ship fixes: embedding a session name
    directly via f-string. Spelled `existing.name` on purpose — the
    SECOND CORRECTION in spec.md records that a name-guessing scanner
    missed exactly this spelling."""
    src = 'InlineKeyboardButton("x", callback_data=f"{existing.name}:kill")'
    assert _violations_in_source(src), "guard failed to flag an f-string session name"


def test_guard_catches_percent_formatting():
    src = 'InlineKeyboardButton("x", callback_data="%s:kill" % session_name)'
    assert _violations_in_source(src), "guard failed to flag %-formatting"


def test_guard_catches_dot_format():
    src = 'InlineKeyboardButton("x", callback_data="{}:kill".format(session_name))'
    assert _violations_in_source(src), "guard failed to flag .format()"


def test_guard_catches_string_concatenation():
    src = 'InlineKeyboardButton("x", callback_data=session_name + ":kill")'
    assert _violations_in_source(src), "guard failed to flag string concatenation"


def test_guard_catches_an_oversized_literal():
    src = f'InlineKeyboardButton("x", callback_data={"x" * 65!r})'
    assert _violations_in_source(src), "guard failed to flag an over-budget literal"


def test_guard_catches_a_session_cb_call_with_an_overflowing_literal_verb():
    src = (
        'InlineKeyboardButton("x", callback_data=session_cb('
        f'bot, chat_id, sess, {"a" * 60!r}))'
    )
    assert _violations_in_source(src), (
        "guard failed to flag a session_cb(...) verb long enough to overflow "
        "at a plausible index"
    )


def test_guard_catches_an_unreviewed_session_cb_verb_fstring():
    src = 'InlineKeyboardButton("x", callback_data=session_cb(bot, chat_id, sess, f"note-{sess.label}"))'
    assert _violations_in_source(src), (
        "guard failed to flag an unreviewed dynamic verb on session_cb(...)"
    )


def test_guard_accepts_a_literal_within_budget():
    src = 'InlineKeyboardButton("x", callback_data="_:clear_gone")'
    assert not _violations_in_source(src)


def test_guard_accepts_a_qualified_session_cb_call():
    src = (
        'InlineKeyboardButton("x", callback_data='
        'session_parity.session_cb(self, chat_id, sess, "allow"))'
    )
    assert not _violations_in_source(src)


def test_guard_accepts_a_bare_session_cb_call():
    src = 'InlineKeyboardButton("x", callback_data=session_cb(bot, chat_id, sess, "restart"))'
    assert not _violations_in_source(src)


def test_guard_accepts_the_reviewed_opt_verb_fstrings():
    src_i = 'InlineKeyboardButton("x", callback_data=session_cb(self, chat_id, sess, f"opt{i}"))'
    src_num = 'InlineKeyboardButton("x", callback_data=session_cb(self, chat_id, sess, f"opt{num - 1}"))'
    assert not _violations_in_source(src_i)
    assert not _violations_in_source(src_num)


def test_guard_rejects_an_unreviewed_name_not_on_the_allowlist():
    src = 'InlineKeyboardButton("x", callback_data=some_new_unreviewed_var)'
    assert _violations_in_source(src), "guard failed to flag an unreviewed bare name"
