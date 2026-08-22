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

A call being SPELLED `session_cb(...)` (or `x.session_cb(...)`) is not
by itself proof of anything — a same-named impostor function, defined
or imported under that name anywhere in the tree, would sail through a
guard that only pattern-matches the identifier (review-1.md rev-iter1-
001; reproduced with a planted `_decoy_probe.py` defining its own
`def session_cb(...)` that returns a raw `f"{sess.name}:{verb}"`). So
this guard also resolves the call against the file's OWN import
bindings (`_import_bindings` below) and accepts it only if that
resolution actually lands on `aipager.bot.session_parity.session_cb` —
a bare name, an aliased name (`import ... as`), or an attribute access
on a name bound to the `session_parity` module all resolve; a locally
defined, differently-imported, or unresolvable `session_cb` does not,
and is a violation regardless of spelling. `test_session_cb_is_defined_
exactly_once_under_aipager` below is a second, independent belt-and-
braces check: even if resolution logic itself had a bug, a second
`def session_cb` appearing anywhere outside `session_parity.py` fails
on its own.
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


# ---- import resolution: verify session_cb() calls actually resolve --
#
# rev-iter1-001: classifying a call as "the legitimate session_cb" by
# bare SPELLING (a Name/Attribute literally named `session_cb`) is not
# enough — a same-named impostor defined or imported elsewhere under
# that name sails through undetected. Everything below resolves a
# callee against the FILE'S OWN import statements and accepts it only
# if it demonstrably targets `aipager.bot.session_parity.session_cb`.
# Nothing is accepted on spelling alone; an unresolvable name is a
# violation, not a pass.

CANONICAL_MODULE = "aipager.bot.session_parity"
CANONICAL_TARGET = f"{CANONICAL_MODULE}.{SESSION_CB_ATTR}"


def _module_dotted_name(path: Path) -> str:
    """The dotted module name a file under AIPAGER_ROOT.parent would be
    imported as — e.g. aipager/bot/session_parity.py ->
    "aipager.bot.session_parity"."""
    rel = path.relative_to(AIPAGER_ROOT.parent)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _relative_import_base(current_dotted: str, level: int) -> str:
    """Resolve the base package for a `from .[...] import ...` relative
    import, given the dotted module name of the file it appears in."""
    package_parts = current_dotted.split(".")[:-1]  # containing package
    if level > 1:
        package_parts = package_parts[: len(package_parts) - (level - 1)]
    return ".".join(package_parts)


def _import_bindings(tree: ast.AST, current_dotted: str) -> dict[str, str]:
    """Map every local name this file's imports bind to the fully
    dotted path it refers to.

    Only ``Import``/``ImportFrom`` nodes are consulted, at ANY nesting
    level (this codebase's own convention is local, in-function
    imports of session_parity — design.md — so module-level-only
    tracking would miss the real pattern). A plain assignment
    (``sp = session_parity``) or a locally defined function is
    deliberately NOT tracked here: either fails resolution below and is
    treated as a violation. That is the fail-closed behaviour this
    guard exists for.
    """
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                base = _relative_import_base(current_dotted, node.level)
                if node.module:
                    base = f"{base}.{node.module}" if base else node.module
            for alias in node.names:
                if alias.name == "*":
                    continue  # wildcard imports are not resolved — conservative
                bound = alias.asname or alias.name
                target = f"{base}.{alias.name}" if base else alias.name
                bindings[bound] = target
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bindings[alias.asname] = alias.name
                else:
                    # `import a.b.c` binds only the top name `a` in the
                    # namespace; `a.b.c` remains reachable off it via
                    # attribute traversal. Self-map so chain resolution
                    # below (base + trailing attrs) reproduces exactly
                    # the dotted path the caller wrote.
                    top = alias.name.split(".")[0]
                    bindings[top] = top
    return bindings


def _callee_dotted(func: ast.AST, bindings: dict[str, str]) -> str | None:
    """The fully-resolved dotted path a Name/Attribute callee refers
    to, per this file's own import bindings — or ``None`` if nothing
    in this file's imports resolves it."""
    trailing: list[str] = []
    cur = func
    while isinstance(cur, ast.Attribute):
        trailing.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    base = bindings.get(cur.id)
    if base is None:
        return None
    trailing.reverse()
    return ".".join([base, *trailing]) if trailing else base


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


def _is_session_cb_call(
    node: ast.AST, *, bindings: dict[str, str], current_dotted: str,
) -> bool:
    """True iff ``node`` is a call that actually RESOLVES to
    ``aipager.bot.session_parity.session_cb`` — spelling alone is never
    enough (rev-iter1-001)."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, (ast.Name, ast.Attribute)):
        return False
    if _callee_dotted(func, bindings) == CANONICAL_TARGET:
        return True
    # Self-reference: session_parity.py itself calls its own
    # module-level session_cb bare, with no import — that's the
    # canonical definition, not an impostor. Nowhere else can this be
    # true, since CANONICAL_MODULE names exactly one file.
    return (
        current_dotted == CANONICAL_MODULE
        and isinstance(func, ast.Name)
        and func.id == SESSION_CB_ATTR
        and func.id not in bindings
    )


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


def _value_violation(
    value: ast.AST, filename: str, lineno: int,
    *, bindings: dict[str, str], current_dotted: str,
) -> str | None:
    """``None`` if ``value`` (the callback_data= keyword's value) is
    provably safe; otherwise a violation message."""
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        if len(value.value.encode()) > 64:
            return (
                f"{filename}:{lineno}: literal callback_data {value.value!r} is "
                f"{len(value.value.encode())} bytes, over the 64-byte cap"
            )
        return None

    if isinstance(value, ast.Call):
        if _is_session_cb_call(value, bindings=bindings, current_dotted=current_dotted):
            return _verb_violation(value, filename, lineno)
        func = value.func
        named_session_cb = (
            (isinstance(func, ast.Attribute) and func.attr == SESSION_CB_ATTR)
            or (isinstance(func, ast.Name) and func.id == SESSION_CB_ATTR)
        )
        if named_session_cb:
            resolved = _callee_dotted(func, bindings) if isinstance(
                func, (ast.Name, ast.Attribute)) else None
            return (
                f"{filename}:{lineno}: callback_data= calls something spelled "
                f"{SESSION_CB_ATTR!r} but it does not resolve (via this file's "
                f"own imports) to {CANONICAL_TARGET} — resolved to {resolved!r}. "
                f"Only the real session_parity.session_cb may embed a session; "
                f"import it explicitly (`from aipager.bot import session_parity` "
                f"+ `session_parity.session_cb(...)`, or `from "
                f"aipager.bot.session_parity import session_cb`) rather than "
                f"defining, aliasing, or re-exporting a same-named function"
            )
        # not session_cb-shaped at all — falls through to the generic
        # "not a literal, not session_cb, not on any allow-list" message.

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
    rel = path.relative_to(AIPAGER_ROOT.parent)
    current_dotted = _module_dotted_name(path)
    bindings = _import_bindings(tree, current_dotted)
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "callback_data":
                continue
            problem = _value_violation(
                kw.value, str(rel), node.lineno,
                bindings=bindings, current_dotted=current_dotted,
            )
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


def test_session_cb_is_defined_exactly_once_under_aipager():
    """rev-iter1-001, second independent layer: regardless of how any
    call resolves, `session_cb` must be DEFINED exactly once anywhere
    under aipager/, in session_parity.py. A second definition — the
    exact `_decoy_probe.py` shape used to verify the import-resolution
    fix above — is itself the violation, whether or not anything ends
    up calling it. If the import-resolution logic above ever regresses,
    this independent grep-style AST check (same pattern as
    `test_make_cb_does_not_exist_anywhere_under_aipager`) still catches
    the decoy."""
    defs = []
    for path in _all_python_files():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                    node.name == SESSION_CB_ATTR:
                rel = path.relative_to(AIPAGER_ROOT.parent)
                defs.append(f"{rel}:{node.lineno}")
    assert len(defs) == 1, (
        f"session_cb must be defined exactly once anywhere under aipager/ "
        f"(in session_parity.py) — found {len(defs)}: {defs}"
    )
    assert defs[0].startswith("aipager/bot/session_parity.py:"), (
        f"session_cb's one definition must live in session_parity.py, "
        f"found at {defs[0]} instead"
    )


# ---- guard-is-load-bearing self-tests ---------------------------------
#
# The 31+ documented "tests passing for reasons unrelated to their name"
# incidents in this codebase make an unverified guard worse than no
# guard — see spec.md's Constraints. These feed synthetic source through
# the same violation-finding functions the real walk above uses, so a
# regression in the guard's own logic (not the production tree) fails
# loudly here instead of silently passing everything.

def _violations_in_source(source: str, *, dotted: str = "<synthetic>") -> list[str]:
    """``dotted`` lets a self-test simulate living inside a specific
    module (e.g. ``CANONICAL_MODULE`` itself, to exercise the
    self-reference case) — defaults to a value that can never equal
    ``CANONICAL_MODULE``, so ordinary self-tests get no special
    treatment."""
    tree = ast.parse(source, filename="<synthetic>")
    bindings = _import_bindings(tree, dotted)
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "callback_data":
                continue
            problem = _value_violation(
                kw.value, "<synthetic>", node.lineno,
                bindings=bindings, current_dotted=dotted,
            )
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
        'from aipager.bot.session_parity import session_cb\n'
        'InlineKeyboardButton("x", callback_data=session_cb('
        f'bot, chat_id, sess, {"a" * 60!r}))'
    )
    violations = _violations_in_source(src)
    assert violations, (
        "guard failed to flag a session_cb(...) verb long enough to overflow "
        "at a plausible index"
    )
    assert "overflow" in violations[0], (
        f"expected an overflow violation (the call resolves fine — the verb "
        f"is the problem), got: {violations[0]!r}"
    )


def test_guard_catches_an_unreviewed_session_cb_verb_fstring():
    src = (
        'from aipager.bot.session_parity import session_cb\n'
        'InlineKeyboardButton("x", callback_data=session_cb(bot, chat_id, sess, f"note-{sess.label}"))'
    )
    violations = _violations_in_source(src)
    assert violations, (
        "guard failed to flag an unreviewed dynamic verb on session_cb(...)"
    )
    assert "allow-list" in violations[0], (
        f"expected an unreviewed-verb violation (the call resolves fine), "
        f"got: {violations[0]!r}"
    )


def test_guard_accepts_a_literal_within_budget():
    src = 'InlineKeyboardButton("x", callback_data="_:clear_gone")'
    assert not _violations_in_source(src)


def test_guard_accepts_a_qualified_session_cb_call():
    src = (
        'from aipager.bot import session_parity\n'
        'InlineKeyboardButton("x", callback_data='
        'session_parity.session_cb(self, chat_id, sess, "allow"))'
    )
    assert not _violations_in_source(src)


def test_guard_accepts_a_bare_session_cb_call():
    src = (
        'from aipager.bot.session_parity import session_cb\n'
        'InlineKeyboardButton("x", callback_data=session_cb(bot, chat_id, sess, "restart"))'
    )
    assert not _violations_in_source(src)


def test_guard_accepts_the_reviewed_opt_verb_fstrings():
    prelude = 'from aipager.bot.session_parity import session_cb\n'
    src_i = prelude + 'InlineKeyboardButton("x", callback_data=session_cb(self, chat_id, sess, f"opt{i}"))'
    src_num = prelude + 'InlineKeyboardButton("x", callback_data=session_cb(self, chat_id, sess, f"opt{num - 1}"))'
    assert not _violations_in_source(src_i)
    assert not _violations_in_source(src_num)


def test_guard_rejects_an_unreviewed_name_not_on_the_allowlist():
    src = 'InlineKeyboardButton("x", callback_data=some_new_unreviewed_var)'
    assert _violations_in_source(src), "guard failed to flag an unreviewed bare name"


# ---- rev-iter1-001: the guard must resolve session_cb, not just match its
# ---- spelling — these are the fix's own load-bearing self-tests ----------

def test_guard_rejects_a_bare_session_cb_call_with_no_import_at_all():
    """No import, no local definition — a plain NameError waiting to
    happen at runtime, but the guard must not need to run the code to
    know it can't prove this call is the real session_cb. This is also
    what `test_guard_accepts_a_bare_session_cb_call` used to look like
    before this fix, when the guard accepted ANY bare name spelled
    session_cb — that was the hole rev-iter1-001 found."""
    src = 'InlineKeyboardButton("x", callback_data=session_cb(bot, chat_id, sess, "restart"))'
    violations = _violations_in_source(src)
    assert violations, "guard failed to flag an unresolved bare session_cb with no import"
    assert "does not resolve" in violations[0]


def test_guard_rejects_a_locally_defined_impostor_session_cb():
    """The exact attack rev-iter1-001 verified against this ship: a
    function spelled `session_cb`, defined in the SAME file (mirroring
    the planted `_decoy_probe.py`), returning the raw unsafe
    `f"{sess.name}:{verb}"` pattern this whole ship exists to remove.
    Called bare, with no import — must not be treated as the real
    thing just because it's spelled the same."""
    src = (
        'def session_cb(bot, chat_id, sess, verb):\n'
        '    return f"{sess.name}:{verb}"\n'
        'InlineKeyboardButton("x", callback_data=session_cb(bot, chat_id, sess, "perms_stop_switch"))\n'
    )
    violations = _violations_in_source(src)
    assert violations, "guard failed to flag a same-named impostor session_cb with no import"
    assert "does not resolve" in violations[0]


def test_guard_rejects_an_impostor_session_cb_imported_from_elsewhere():
    """Same attack, but the impostor is imported under the exact name
    `session_cb` from a module that is NOT session_parity — spelling
    alone still must not be enough to pass."""
    src = (
        'from aipager.bot._decoy_probe import session_cb\n'
        'InlineKeyboardButton("x", callback_data=session_cb(bot, chat_id, sess, "kill"))\n'
    )
    violations = _violations_in_source(src)
    assert violations, "guard failed to flag session_cb imported from a non-canonical module"
    assert "does not resolve" in violations[0]


def test_guard_rejects_an_attribute_call_on_a_non_session_parity_module():
    """Same attack again, this time as `<module>.session_cb(...)` where
    `<module>` is imported from somewhere other than
    aipager.bot.session_parity."""
    src = (
        'from aipager.bot import _decoy_probe\n'
        'InlineKeyboardButton("x", callback_data=_decoy_probe.session_cb(bot, chat_id, sess, "kill"))\n'
    )
    violations = _violations_in_source(src)
    assert violations, (
        "guard failed to flag session_cb accessed off a non-session_parity "
        "module alias"
    )
    assert "does not resolve" in violations[0]


def test_guard_accepts_an_aliased_import_of_the_real_session_cb():
    """`import ... as` must still resolve — aliasing the REAL function
    is not itself suspicious; only aliasing/defining an IMPOSTOR is."""
    src = (
        'from aipager.bot.session_parity import session_cb as scb\n'
        'InlineKeyboardButton("x", callback_data=scb(bot, chat_id, sess, "restart"))\n'
    )
    assert not _violations_in_source(src)


def test_guard_accepts_an_aliased_module_import_of_session_parity():
    src = (
        'from aipager.bot import session_parity as sp\n'
        'InlineKeyboardButton("x", callback_data=sp.session_cb(bot, chat_id, sess, "restart"))\n'
    )
    assert not _violations_in_source(src)


def test_guard_accepts_the_real_session_cb_self_reference_inside_its_own_module():
    """session_parity.py's own body calls its module-level session_cb
    bare, with no import (it can't import itself) — that IS the
    canonical definition, not an impostor, and must resolve."""
    src = 'InlineKeyboardButton("x", callback_data=session_cb(bot, chat_id, sess, "rename"))\n'
    assert not _violations_in_source(src, dotted=CANONICAL_MODULE)


def test_guard_still_rejects_an_impostor_even_inside_a_file_named_like_session_parity():
    """The self-reference carve-out is keyed on the exact dotted module
    path, not on "any file that happens to define session_cb" — a
    same-named impostor sitting in some OTHER file must not benefit
    from it, even if that file were (hypothetically) misidentified."""
    src = (
        'def session_cb(bot, chat_id, sess, verb):\n'
        '    return f"{sess.name}:{verb}"\n'
        'InlineKeyboardButton("x", callback_data=session_cb(bot, chat_id, sess, "kill"))\n'
    )
    assert _violations_in_source(src, dotted="aipager.bot.not_session_parity")
