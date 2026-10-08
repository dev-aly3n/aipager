"""Roadmap 8.114: `aipager uninstall` says what it keeps.

Uninstall removes the config folder and the session list, and keeps
aipager's data on purpose, so a reinstall picks up where it left off. It
used to name only what it removed. Now the confirmation and the final
message list every kept path that exists, with what it holds, and the
command that removes them by hand.

The first test is the guard that keeps that list true: every
``Path.home() / ...`` path in aipager's code (and every ``expanduser`` of a
``"~/..."`` literal) must be removed by uninstall, named as kept, or be one
of the few paths that are not aipager's data (Claude Code's own files, the
binaries). A new home path fails it until someone decides which. What it
cannot see: a path built from a variable home (``home = Path.home()`` and
then ``home / ...``, pinned by counting those uses per file), from a folder
path kept in a variable and joined later (``_CD = Path.home() / ".claude"``
and then ``_CD / "x"``, or ``Path(str(...))``), from an environment
variable (``HOME``, ``XDG_*``), or from a string never expanded.
"""

from __future__ import annotations

import argparse
import ast
import shlex
from pathlib import Path

import aipager
from aipager import updater

PKG = Path(aipager.__file__).parent

#: Home paths aipager uses that are not its data, so uninstall neither
#: removes nor lists them. Exact paths (relative to the home folder).
NOT_AIPAGERS_DATA = {
    (".claude",): "Claude Code's config folder (read)",
    (".claude", "settings.json"): "Claude Code's settings; uninstall takes out only aipager's entries",
    (".claude.json",): "Claude Code's own state",
    (".claude", ".credentials.json"): "Claude Code's login (read)",
    (".claude", "projects"): "Claude Code's transcripts (read)",
    (".local", "share", "claude", "versions"): "Claude Code's installs (read)",
    (".local", "bin", "claude"): "Claude Code's binary (read)",
    (".local", "bin"): "a folder looked up on PATH",
    (".local", "bin", "aipager"): "aipager's binary, removed by its installer",
    (".config", "systemd", "user", "aipager.service"):
        "the service unit, removed with the service as uninstall's first step",
}
#: Folders whose every entry is not aipager's, whatever its name (a path
#: under one may end in a variable).
NOT_AIPAGERS_DATA_ANY_NAME = {
    (".claude", "projects"): "Claude Code's transcripts, one folder per project",
}
#: Files that use the bare home folder itself (not a path under it), and
#: how many times: one more is a path this test cannot follow.
BARE_HOME_USERS = {
    "install_source.py": (1, "builds PATH entries"),
    "service.py": (2, "fills the home folder into the service unit"),
    "safety.py": (3, "expands ~ in the protected-path patterns"),
}

_LISTS = ("_USER_PATHS_TO_REMOVE", "_MACOS_PATHS_TO_REMOVE", "_USER_PATHS_KEPT")


def _is_home_call(node) -> bool:
    """``Path.home()`` or ``pathlib.Path.home()``."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "home" and not node.args):
        return False
    owner = node.func.value
    return ((isinstance(owner, ast.Name) and owner.id == "Path")
            or (isinstance(owner, ast.Attribute) and owner.attr == "Path"
                and isinstance(owner.value, ast.Name) and owner.value.id == "pathlib"))


def _expanded_literal(node) -> tuple[str, bool] | None:
    """The ``"~..."`` text an ``expanduser`` call expands, if any:
    ``os.path.expanduser("~/x")`` or ``Path("~/x").expanduser()``, with
    whether it was cut short (an f-string: its text up to the first value)."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name != "expanduser":
        return None
    arg = node.args[0] if node.args else None
    if arg is None and isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call):
        arg = func.value.args[0] if func.value.args else None
    cut = False
    if isinstance(arg, ast.JoinedStr):
        head = ""
        for value in arg.values:
            if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                cut = True
                break
            head += value.value
        text = head
    elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        text = arg.value
    else:
        return None
    if not text.startswith("~"):
        return None
    return text, cut


def _home_paths(tree) -> list[tuple[tuple[str, ...] | None, bool, int]]:
    """Every home path in *tree*: ``(parts, cut, line)``. *parts* are the
    literal parts after the home folder, None for the bare home folder;
    *cut* is True when a part that is not a string literal ended them."""
    inner = {id(n.left) for n in ast.walk(tree)
             if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)}
    found, chained = [], set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)) \
                or id(node) in inner:
            continue
        rights, cur = [], node
        while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
            rights.append(cur.right)
            cur = cur.left
        if not _is_home_call(cur):
            continue
        chained.add(id(cur))
        parts: list[str] = []
        cut = False
        for right in reversed(rights):
            if not (isinstance(right, ast.Constant) and isinstance(right.value, str)):
                cut = True
                break
            parts.extend(Path(right.value).parts)
        found.append((tuple(parts), cut, node.lineno))
    for node in ast.walk(tree):
        if _is_home_call(node) and id(node) not in chained:
            found.append((None, False, node.lineno))
        literal = _expanded_literal(node)
        if literal is not None:
            text, cut = literal
            parts = tuple(Path(text[1:].lstrip("/")).parts)
            if cut and not text.endswith("/"):
                parts = parts[:-1]          # the name the value completes
            found.append((parts if parts or cut else None, cut, node.lineno))
    return found


def _uninstall_lists() -> dict[str, list[tuple[str, ...]]]:
    """The paths in updater's removed and kept lists, as written."""
    tree = ast.parse((PKG / "updater.py").read_text())
    lists: dict[str, list[tuple[str, ...]]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in _LISTS:
            lists[node.targets[0].id] = [p for p, _cut, _line in _home_paths(node) if p]
    return lists


def test_every_home_path_is_removed_kept_or_not_aipagers_data():
    lists = _uninstall_lists()
    assert set(lists) == set(_LISTS) and all(lists.values())
    covered = [p for paths in lists.values() for p in paths]

    def _under(parts, roots) -> bool:
        return any(parts[:len(root)] == root for root in roots)

    unaccounted, scanned, bare = [], 0, {}
    for source in sorted(PKG.rglob("*.py")):
        rel = str(source.relative_to(PKG))
        for parts, cut, line in _home_paths(ast.parse(source.read_text())):
            scanned += 1
            if parts is None:
                bare[rel] = bare.get(rel, 0) + 1
            elif _under(parts, covered):
                continue
            elif cut:
                # Ends in a variable: only a folder that is not aipager's
                # whatever the name may hold it.
                if not _under(parts, NOT_AIPAGERS_DATA_ANY_NAME):
                    unaccounted.append(f"{rel}:{line} ~/{'/'.join(parts)}/...")
            elif parts not in NOT_AIPAGERS_DATA:
                unaccounted.append(f"{rel}:{line} ~/{'/'.join(parts)}")
    for rel in set(bare) | set(BARE_HOME_USERS):
        count, allowed = bare.get(rel, 0), BARE_HOME_USERS.get(rel, (0, ""))[0]
        if count != allowed:
            unaccounted.append(f"{rel}: {count} use(s) of the home folder itself, "
                               f"{allowed} allowed")
    assert scanned > 40, "the scan found too few home paths to mean anything"
    assert unaccounted == [], (
        "home paths `aipager uninstall` neither removes nor names as kept "
        "(add them to updater._USER_PATHS_TO_REMOVE or _USER_PATHS_KEPT): "
        + ", ".join(unaccounted))


def test_what_aipager_writes_today_is_kept():
    kept = _uninstall_lists()["_USER_PATHS_KEPT"]
    for path in [(".local", "share", "aipager"), (".local", "state", "aipager"),
                 (".claude", "aipager-flood-state.json"),
                 (".claude", "aipager-audit.jsonl"),
                 (".claude", "aipager-pending-users.json")]:
        assert path in kept


# ---- the messages ---------------------------------------------------------------------

def _uninstall(monkeypatch, capsys, *, force=True):
    monkeypatch.setattr(updater, "_detect_installer", lambda: None)
    monkeypatch.setattr(updater, "_stop_daemon", lambda: None)
    monkeypatch.setattr(updater, "_remove_tmp_sockets", lambda: None)
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    rc = updater.cmd_uninstall(argparse.Namespace(force=force))
    assert rc == 0
    return capsys.readouterr().out


def _make(path: Path) -> Path:
    if path.suffix:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    else:
        (path / "sessions").mkdir(parents=True, exist_ok=True)
    return path


def test_the_kept_data_is_named_and_survives(monkeypatch, capsys):
    kept = {p: what for p, what in updater._USER_PATHS_KEPT}
    data, flood = list(kept)[0], list(kept)[3]
    _make(data)
    _make(flood)
    out = _uninstall(monkeypatch, capsys)
    assert "Kept, so a reinstall picks up where you left off:" in out
    assert f"{data} ({kept[data]})" in out and f"{flood} ({kept[flood]})" in out
    for absent in set(kept) - {data, flood}:
        assert str(absent) not in out
    assert "Your data is kept. To remove it too:" in out
    assert f"rm -rf {shlex.quote(str(data))} {shlex.quote(str(flood))}" in out
    assert data.exists() and flood.exists()       # kept, as it says


def test_the_list_is_shown_before_anything_is_removed(monkeypatch, capsys):
    data = _make(updater._USER_PATHS_KEPT[0][0])
    out = _uninstall(monkeypatch, capsys, force=False)    # answered "n"
    assert str(data) in out and "rm -rf" not in out


def test_nothing_kept_means_no_kept_section(monkeypatch, capsys):
    out = _uninstall(monkeypatch, capsys)
    assert "Kept" not in out and "rm -rf" not in out


def test_the_remove_command_quotes_each_path(monkeypatch, capsys, tmp_path):
    odd = tmp_path / "my home" / "aipager data"
    monkeypatch.setattr(updater, "_USER_PATHS_KEPT", [(odd, "everything")])
    _make(odd)
    out = _uninstall(monkeypatch, capsys)
    assert f"rm -rf '{odd}'" in out


def test_a_path_is_shown_as_it_is_named(monkeypatch, capsys, tmp_path):
    # Brackets in a path are not markup, in the list or in the command.
    odd = tmp_path / "a [b]" / "[bold]x"
    monkeypatch.setattr(updater, "_USER_PATHS_KEPT", [(odd, "everything")])
    _make(odd)
    out = _uninstall(monkeypatch, capsys)
    assert f"{odd} (everything)" in out
    assert f"rm -rf '{odd}'" in out


def test_a_kept_file_written_while_stopping_is_in_the_command(monkeypatch, capsys):
    flood = updater._USER_PATHS_KEPT[3][0]
    monkeypatch.setattr(updater, "_stop_daemon", lambda: _make(flood))
    monkeypatch.setattr(updater, "_detect_installer", lambda: None)
    monkeypatch.setattr(updater, "_remove_tmp_sockets", lambda: None)
    updater.cmd_uninstall(argparse.Namespace(force=True))
    out = capsys.readouterr().out
    assert f"rm -rf {shlex.quote(str(flood))}" in out
