"""An error in another aipager process, told to the daemon (roadmap 8.112).

The hook (``aipager-hook``) and the command line cannot write the
daemon's report store. On an error they send ONE datagram to the
daemon's control socket, built by :func:`error_datagram` from the
exception's shape only (:mod:`aipager.report.fingerprint`):

``{"type": "report_error", "where": "hook" | "cli", "event": <hook event
name> | null, "tool": <built-in tool> | "mcp" | "other" | null,
"denied": <a PreToolUse refused because it could not be checked>, "env":
<environment counter> | null, "facts": {...}}``; a ``cli`` one has no
event, no tool and no deny.

``facts`` (type, chained types, errno name, aipager frames, outside
packages) is sent only for an error that is not the user's environment;
an environment error sends its counter alone. Never the exception's text,
the hook's payload, the tool's input, a path or a name. No fingerprint
either: the daemon computes it from the checked type and frames
(:func:`fingerprint.fingerprint_of`), so a forged one (twelve hex digits
can spell a chat id) can never be stored.

The daemon reads it with :func:`read_error_datagram`: the socket is local
but any local process can write to it, so every field is checked against
the report schema and a datagram with anything wrong is dropped whole.
Beyond the schema, a relayed error must name real code: each frame's
function is one defined in that shipped file and its line is inside it,
and each exception type is a real class (defined in aipager's own source,
or loaded in the daemon). A forger can then only pick among aipager's
own names, never write text of its own.
Reading one never raises: the receiver runs it in a task, whose error
would be logged and so recorded as a bug a stranger made up.

Standard library only (and :mod:`fingerprint` / :mod:`schema`), imported
by the hook only on its error path; this module opens no socket itself.
"""

from __future__ import annotations

import ast
import functools
import json

from aipager.report import fingerprint as fp
from aipager.report import schema as sc

TYPE = "report_error"
WHERE = ("hook", "cli")
_KEYS = frozenset({"type", "where", "event", "tool", "denied", "env", "facts"})
_FACTS = {key: sc.SCHEMA["errors"][1][key]
          for key in ("type", "cause_types", "errno", "frames", "external")}
_TOOL = sc.SCHEMA["errors"][1]["tool"]
_EVENT = sc.SCHEMA["errors"][1]["event"]


def _shaped(leaf, value) -> bool:
    """*value* passes *leaf* as itself (``Leaf.check`` returns the
    ``"<invalid>"`` sentinel for a bad value, so the sentinel itself must
    never count as shaped)."""
    return value != sc.INVALID and leaf.check(value) == value


def tool_kind(tool) -> str | None:
    """A built-in tool by its name, an MCP tool as ``mcp`` (its server and
    tool names are the user's), anything else ``other``."""
    if not isinstance(tool, str) or not tool:
        return None
    if tool in sc.TOOLS:
        return tool
    return "mcp" if tool.startswith("mcp__") else "other"


def error_datagram(exc: BaseException, *, where: str, event=None, tool=None,
                   denied: bool = False) -> bytes | None:
    """The datagram for *exc* (see the module), or None. Never raises."""
    try:
        env = fp.env_counter(exc)
        message = {
            "type": TYPE, "where": where,
            "event": event if _shaped(_EVENT, event) else None,
            "tool": tool_kind(tool), "denied": denied is True, "env": env,
        }
        if env is None:
            facts = fp.describe_exception(exc)
            message["facts"] = {key: facts[key] for key in _FACTS}
        return json.dumps(message).encode()
    except Exception:  # noqa: BLE001 - an error report must never add one
        return None


def read_error_datagram(message) -> dict | None:
    """The checked content of a ``report_error`` datagram, or None when
    any part of it is not exactly what :func:`error_datagram` sends.
    Never raises."""
    try:
        return _read(message)
    except Exception:  # noqa: BLE001 - a stranger's datagram must not log an error
        return None


def _read(message) -> dict | None:
    if not isinstance(message, dict) or message.get("type") != TYPE:
        return None
    keys = set(message)
    env = message.get("env")
    if keys != (_KEYS - {"facts"} if env is not None else _KEYS):
        return None
    if message["where"] not in WHERE or type(message["denied"]) is not bool:
        return None
    if not _shaped(_EVENT, message["event"]) or not _shaped(_TOOL, message["tool"]):
        return None
    if message["where"] == "cli" and (message["event"] is not None
                                      or message["tool"] is not None or message["denied"]):
        return None
    if env is not None:
        # An unhashable env raises here, and so reads as None (above).
        return dict(message) if env in fp.ENV_COUNTERS else None
    facts, problems = sc.validate_part(_FACTS, message["facts"])
    if problems or not isinstance(facts, dict) or set(facts) != set(_FACTS):
        return None
    # A null type is no real type: _real_type raises on it, which reads as None.
    if not all(_real_type(name) for name in [facts["type"], *facts["cause_types"]]):
        return None
    if not all(_real_frame(frame) for frame in facts["frames"]):
        return None
    return dict(message, facts=facts)


# ---- real code only -----------------------------------------------------------

_ANONYMOUS = frozenset({"<lambda>", "<genexpr>", "<listcomp>", "<dictcomp>", "<setcomp>",
                        "<module>"})


# Read once per daemon: a new install's code is seen after the restart a
# deploy does anyway.
@functools.lru_cache(maxsize=256)
def _source(file: str) -> tuple[frozenset, frozenset, int] | None:
    """The function and class names a shipped file defines (qualified, as
    ``co_qualname`` names them, and bare, as 3.10's ``co_name``), its class
    qualnames, and its line count; None if it cannot be read."""
    source_path = fp.PACKAGE_DIR.parent / file
    try:
        text = source_path.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, SyntaxError, ValueError, RecursionError):
        return None
    names: set[str] = set()
    classes: set[str] = set()

    def visit(node, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = prefix + child.name
                names.update((qualname, child.name))
                visit(child, qualname + ".<locals>.")
            elif isinstance(child, ast.ClassDef):
                qualname = prefix + child.name
                names.update((qualname, child.name))
                classes.add(qualname)
                visit(child, qualname + ".")
            else:
                visit(child, prefix)

    visit(tree, "")
    return frozenset(names), frozenset(classes), text.count("\n") + 1


def _real_frame(frame: dict) -> bool:
    known = _source(frame["file"])
    if known is None or frame["line"] > known[2]:
        return False
    # An anonymous frame names its scope: ``f.<locals>.<lambda>``,
    # ``Cls.<listcomp>`` (a class body), ``f.<locals>.<lambda>.<locals>.<genexpr>``;
    # peel anonymous parts until a real name is left.
    fn = frame["fn"]
    while fn not in known[0] and fn not in _ANONYMOUS:
        head, _, tail = fn.rpartition(".")
        if tail not in _ANONYMOUS or not head:
            return False
        fn = head[: -len(".<locals>")] if head.endswith(".<locals>") else head
    return True


def _loaded_exception_types() -> set[str]:
    """The qualified names of every exception class loaded in this process."""
    seen: set[type] = set()
    stack = [BaseException]
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        try:
            stack.extend(cls.__subclasses__())
        except TypeError:  # a metaclass oddity: no subclasses to add
            pass
    return {fp.qualified_type(cls) for cls in seen}


def _real_type(name: str) -> bool:
    """*name* is ``<other>``, a class defined in aipager's own source, or
    an exception class already loaded in this process."""
    if name == "<other>":
        return True
    parts = name.split(".")
    if parts[0] == "aipager":
        for cut in range(len(parts) - 1, 0, -1):
            stem = "/".join(parts[:cut])
            for file in (stem + ".py", stem + "/__init__.py"):
                if file in fp.shipped_files():
                    known = _source(file)
                    return known is not None and ".".join(parts[cut:]) in known[1]
        return False
    return name in _loaded_exception_types()
