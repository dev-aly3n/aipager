"""No shared runtime path or Telegram URL is spelled out outside its resolver.

``aipager/instance.py`` owns every ``/tmp`` runtime path and
``aipager/telegram_endpoint.py`` owns the Bot API base. A new literal
``/tmp/claude-...`` or ``https://api.telegram.org`` anywhere else would
silently escape AIPAGER_INSTANCE_DIR / AIPAGER_TELEGRAM_API_BASE: a test
instance would then share that file with the operator's daemon, or send
its token to the real Telegram. This scans every string constant in the
package (f-string pieces included, docstrings excluded).
"""

from __future__ import annotations

import ast
import pathlib

PKG = pathlib.Path(__file__).resolve().parent.parent / "aipager"

FORBIDDEN = ("/tmp/claude-", "/tmp/aipager", "https://api.telegram.org")

OWNERS = {"instance.py", "telegram_endpoint.py"}

# (relative path, exact constant) pairs that may stay, each for a reason.
ALLOWED = {
    # The built-in safety globs for a normal install (the instance's own
    # globs come from instance.protected_globs()).
    ("safety.py", "/tmp/claude-policy-*"),
    ("safety.py", "/tmp/claude-notes-*"),
    ("safety.py", "/tmp/claude-status-*"),
    ("safety.py", "/tmp/claude-reply-*"),
    ("safety.py", "/tmp/claude-dtach-*"),
    # Claude Code's own scratchpad root, not an aipager file.
    ("safety.py", "(?<![\\w.~/-])/tmp/claude-\\d+(?=/|[\\s;&|()<>'\\\"`]|$)"),
    # Uninstall removes a pre-XDG leftover socket (normal install only).
    ("updater.py", "/tmp/aipager.sock"),
    # User-facing uninstall summary text (describes a normal install).
    ("updater.py", "  • the daemon control socket, /tmp/claude-dtach-*.sock, "
                   "/tmp/claude-status-*.json"),
    # The two hook binaries inline the control-socket rule (stdlib-only,
    # <5 ms); tests/test_hook_socket_path_precedence.py pins agreement.
    ("dtach/notify_hook.py", "/tmp/aipager.sock"),
    ("dtach/statusline_notify.py", "/tmp/aipager.sock"),
    # The test-only guard names a normal install's live control socket, to
    # refuse a test's datagram to it (problem reports, roadmap 8.112).
    ("_test_guard.py", "/tmp/aipager.sock"),
}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and \
                    isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _offenders() -> list[tuple[str, int, str]]:
    found = []
    for path in sorted(PKG.rglob("*.py")):
        rel = path.relative_to(PKG).as_posix()
        if rel in OWNERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docs = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in docs:
                continue
            if any(f in node.value for f in FORBIDDEN) and (rel, node.value) not in ALLOWED:
                found.append((rel, node.lineno, node.value))
    return found


def test_no_runtime_path_or_api_literal_outside_the_resolvers():
    offenders = _offenders()
    assert not offenders, (
        "Spell these through aipager.instance / aipager.telegram_endpoint "
        f"(or allowlist with a reason): {offenders}"
    )


def test_every_allowlisted_literal_still_exists():
    """A stale allowlist entry would quietly permit a future literal."""
    present = set()
    for rel, value in ALLOWED:
        tree = ast.parse((PKG / rel).read_text(encoding="utf-8"))
        if any(isinstance(n, ast.Constant) and n.value == value for n in ast.walk(tree)):
            present.add((rel, value))
    assert present == ALLOWED, ALLOWED - present
