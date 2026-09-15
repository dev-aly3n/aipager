"""The routing contract as four pure AST predicates (roadmap 8.26 R2).

Importable — ``tests`` is a package — so a black-box test can feed these
synthetic source strings instead of having to write a file into
``aipager/`` to see them fire. ``tests/test_command_replies_respect_mute.py``
runs all four over the real tree.

**The offender predicate changed meaning in 8.26, and that is the crux of
the whole ship.** Until 0.7.12 enforcement was per-site: a mute check
before each of ~66 outbound calls, so ANY call in the send family outside a
handful of exempt files was a leak. It failed three times, most recently
for 9.5 hours, because it scales with the number of call sites and the two
files holding 23 of them (``notify.py``, ``animation.py``) were themselves
exempt — a new leak there was invisible to CI by construction.

Enforcement now lives in ``BudgetRateLimiter.process_request``, which every
Bot API call already passes through. So ``self._app.bot.send_message(...)``
is no longer a leak: that receiver IS the daemon's one limiter-bound
``ExtBot``. What is still a leak is a call on a receiver that is NOT that
bot — a PTB update object (``message.reply_text``, whose 8.17b sentinel
contract 27 tests depend on), a hand-built ``telegram.Bot``, an unknown
local. Hence :data:`BOT_RECEIVERS`.

**The family sweep and the constructor sweep are a PAIR.** Allowing the
bare name ``bot`` as a gated receiver is only safe because
:func:`bot_construction_offenders` forbids building a second ``Bot`` or
``ApplicationBuilder`` anywhere in these packages: there is then no way to
obtain a bot that is not the app's. Weaken the constructor sweep and the
family sweep weakens with it.

**The tolerance sweep is not decoration.** ``NotifyMixin.notify`` is one
1726-line method carrying ~20 direct sends. A bare ``FloodMuted`` from the
new gate propagating out of one of them would abort the rest of the turn —
losing the answer the gate exists to protect, which is the same class of
error as the fallback-into-the-ban trap. Every gated-family call must be
lexically inside a ``try`` that tolerates it.
"""

from __future__ import annotations

import ast

#: Every Bot API method that puts something into a chat. Wider than the
#: 0.7.12 list by ``delete_message`` and ``send_chat_action`` (8.26 D-8):
#: there were nine live ``delete_message`` sites and not one was visible
#: to the old sweep, and a chat action is a request into the chat like any
#: other — exempt from the BUDGET, never from the MUTE.
GATED_FAMILIES: set[str] = {
    "reply_text", "reply_document", "reply_photo",
    "edit_message_text", "edit_message_reply_markup", "edit_message_caption",
    "edit_text",
    "send_message", "send_document", "send_photo",
    "delete_message", "send_chat_action",
}

#: The only files whose direct calls this sweep does not police.
#:
#: SHRUNK from six to three in 8.26 (D-8). ``notify.py``, ``animation.py``
#: and ``rich_message.py`` are no longer exempt — with the gate at the
#: chokepoint they no longer need per-site checks, so there is nothing
#: left to exempt them FOR, and every file removed from this set is a file
#: the sweep now protects. That is the point.
#:
#: * ``transport.py`` — the seam itself. It keeps its own pre-checks
#:   because it must hold for a bot that is not limiter-bound.
#: * ``flood_budget.py`` — the gate. It cannot be gated by itself.
#: * ``observer.py`` — observer bots keep their own tokens, their own
#:   budgets and their own chats (8.21 §11 D12).
EXEMPT_FILES: set[str] = {"transport.py", "flood_budget.py", "observer.py"}

#: Receiver expressions that ARE the daemon's one limiter-bound ``ExtBot``,
#: and are therefore gated by construction. Anything else is an offender.
#: See the module docstring on why the bare name ``bot`` is safe here.
BOT_RECEIVERS: frozenset = frozenset({
    "self._app.bot", "self.bot._app.bot", "bot", "app.bot", "self._bot",
})

#: Files allowed to name ``api.telegram.org`` (8.26 D-8). Every one of
#: them runs OUTSIDE the daemon process or is a diagnostic with no limiter
#: to route through, so there is no gate for them to bypass:
#:
#: * ``observer.py``  — observer bots, own ``telegram.Bot``, own token
#: * ``doctor.py``    — reachability probe run from the CLI
#: * ``daemon.py``    — ``aipager daemon``'s own preflight
#: * ``team_setup.py``/``telegram_api.py`` — the wizard, before a daemon
#:   exists at all
#:
#: ``rich_message.py`` is the one IN-daemon URL builder and is handled by
#: name below. Keep this list minimal: each entry is a path the R1 gate
#: does not cover.
URL_ALLOWLIST: set[str] = {
    "observer.py", "doctor.py", "daemon.py",
    "team_setup.py", "telegram_api.py",
}

#: The in-daemon URL builder, allowed to name the host exactly once.
URL_BUILDER = "rich_message.py"

#: Exception names that tolerate the gate's refusal.
_TOLERANT = {"FloodMuted", "Exception", "BaseException",
             "RichMessageFloodBanned"}


def _receiver(node: ast.Call) -> str | None:
    """``ast.unparse`` of the call's receiver, or ``None`` if it has none."""
    if not isinstance(node.func, ast.Attribute):
        return None
    try:
        return ast.unparse(node.func.value)
    except Exception:  # pragma: no cover - unparse is total in 3.10+
        return None


def _parse(filename: str, source: str) -> ast.Module | None:
    try:
        return ast.parse(source, filename=filename)
    except SyntaxError:
        return None


def _basename(filename: str) -> str:
    return filename.rsplit("/", 1)[-1]


def gated_family_offenders(filename: str, source: str) -> list[str]:
    """Calls in :data:`GATED_FAMILIES` on a receiver that is not gated.

    A call on a PTB update object — ``message.reply_text``,
    ``query.edit_message_text`` — is an offender outside the seam, and
    must stay one: that is the 8.17b sentinel contract the 27 ``MUTED``
    tests depend on, and losing it would silently undo 8.17b.
    """
    if _basename(filename) in EXEMPT_FILES:
        return []
    tree = _parse(filename, source)
    if tree is None:
        return []
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in GATED_FAMILIES):
            continue
        if _receiver(node) in BOT_RECEIVERS:
            continue
        offenders.append(
            f"{_basename(filename)}:{node.lineno} "
            f"{_receiver(node)}.{node.func.attr}(")
    return offenders


def telegram_url_offenders(filename: str, source: str) -> list[str]:
    """``api.telegram.org`` in any string constant or f-string.

    Docstrings are skipped — the first statement of a Module, ClassDef,
    FunctionDef or AsyncFunctionDef — which is what keeps ``errors.py``'s
    explanatory prose from tripping this. A real URL in ``errors.py``
    still fails it.
    """
    base = _basename(filename)
    if base in URL_ALLOWLIST or base == URL_BUILDER:
        return []
    tree = _parse(filename, source)
    if tree is None:
        return []
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                skip.add(id(body[0].value))
    offenders = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in skip
                and "api.telegram.org" in node.value):
            offenders.append(f"{base}:{node.lineno} api.telegram.org")
    return offenders


def bot_construction_offenders(filename: str, source: str) -> list[str]:
    """A second ``Bot`` or ``ApplicationBuilder`` built in these packages.

    This is what makes :data:`BOT_RECEIVERS` safe to write as a short list
    of expressions including the bare name ``bot``: if no second bot can
    be constructed, every ``bot`` in reach is the app's limiter-bound one.
    Weakening this sweep weakens :func:`gated_family_offenders` with it.
    """
    base = _basename(filename)
    if base in {"lifecycle.py", "observer.py"}:
        return []
    tree = _parse(filename, source)
    if tree is None:
        return []
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name in {"Bot", "ApplicationBuilder"}:
            offenders.append(f"{base}:{node.lineno} {name}(")
    return offenders


def _handler_is_tolerant(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:          # bare `except:`
        return True
    names = (handler.type.elts if isinstance(handler.type, ast.Tuple)
             else [handler.type])
    for item in names:
        if isinstance(item, ast.Name) and item.id in _TOLERANT:
            return True
        if isinstance(item, ast.Attribute) and item.attr in _TOLERANT:
            return True
    return False


def untolerated_send_offenders(filename: str, source: str) -> list[str]:
    """Gated-family calls not lexically inside a tolerant ``try``.

    "Tolerant" means at least one handler naming ``FloodMuted``,
    ``RichMessageFloodBanned``, ``Exception`` or ``BaseException`` (or a
    bare ``except:``). Only the ``try``'s BODY counts — a call inside an
    ``except`` or ``finally`` arm of the same statement is not protected
    by it.

    Without this, a bare ``FloodMuted`` from the gate would escape one of
    ``NotifyMixin.notify``'s ~20 direct sends and abort the rest of the
    turn, losing the answer the gate exists to protect.
    """
    if _basename(filename) in EXEMPT_FILES:
        return []
    tree = _parse(filename, source)
    if tree is None:
        return []

    protected: set[int] = set()

    def _mark(body, tolerant: bool) -> None:
        for stmt in body:
            for node in ast.walk(stmt):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in GATED_FAMILIES
                        and tolerant):
                    protected.add(id(node))

    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and any(
                _handler_is_tolerant(h) for h in node.handlers):
            _mark(node.body, True)

    offenders = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in GATED_FAMILIES
                and id(node) not in protected):
            offenders.append(
                f"{_basename(filename)}:{node.lineno} "
                f".{node.func.attr}( not inside a tolerant try")
    return offenders


__all__ = [
    "BOT_RECEIVERS",
    "EXEMPT_FILES",
    "GATED_FAMILIES",
    "URL_ALLOWLIST",
    "bot_construction_offenders",
    "gated_family_offenders",
    "telegram_url_offenders",
    "untolerated_send_offenders",
]
