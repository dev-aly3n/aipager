"""When the Claude Code update can only fail (operator, 2026-10-09).

A Claude Code installed with npm into a folder the daemon's user can't
write (``sudo npm install -g`` under ``/usr/lib/node_modules``) makes
``claude update`` stop on "global folder isn't writable". The update
offer must say why instead of offering a button bound to fail. Anything
this cannot tell counts as "can update": the update itself still reports
a failure.

Writability is faked through ``os.access`` (deterministic as root too);
the folders are real ones under ``tmp_path``.
"""

from __future__ import annotations

import os

import pytest

from aipager import self_update
from aipager.self_update import NPM_NOT_WRITABLE_REASON, claude_update_blocker

_REAL_ACCESS = os.access


def _deny_write(monkeypatch, *denied):
    """``os.access`` that says "not writable" for exactly these folders."""
    denied_set = {os.path.normpath(str(d)) for d in denied}

    def _access(path, mode, *a, **k):
        if mode & os.W_OK and os.path.normpath(os.fspath(path)) in denied_set:
            return False
        return _REAL_ACCESS(path, mode, *a, **k)
    monkeypatch.setattr(os, "access", _access)


def _npm_tree(tmp_path, *pkg):
    """``<prefix>/lib/node_modules/<pkg...>/cli.js``; returns (modules, package, cli)."""
    modules = tmp_path / "prefix" / "lib" / "node_modules"
    package = modules.joinpath(*pkg)
    package.mkdir(parents=True)
    cli = package / "cli.js"
    cli.write_text("// claude\n")
    return modules, package, cli


@pytest.fixture
def scoped(tmp_path):
    return _npm_tree(tmp_path, "@anthropic-ai", "claude-code")


def test_npm_package_folder_not_writable_is_blocked(scoped, monkeypatch):
    modules, package, cli = scoped
    _deny_write(monkeypatch, package)
    assert claude_update_blocker(str(cli), "npm") == NPM_NOT_WRITABLE_REASON


def test_npm_node_modules_not_writable_is_blocked(scoped, monkeypatch):
    modules, package, cli = scoped
    _deny_write(monkeypatch, modules)
    assert claude_update_blocker(str(cli), "npm") == NPM_NOT_WRITABLE_REASON


def test_scoped_package_checks_the_package_not_just_the_scope(scoped, monkeypatch):
    # Only `@anthropic-ai/claude-code` is read-only; the `@anthropic-ai`
    # scope folder is writable. Checking the scope folder alone misses it.
    modules, package, cli = scoped
    _deny_write(monkeypatch, package)
    assert os.access(package.parent, os.W_OK)
    assert claude_update_blocker(str(cli), "npm") == NPM_NOT_WRITABLE_REASON


def test_unscoped_package_folder_not_writable_is_blocked(tmp_path, monkeypatch):
    modules, package, cli = _npm_tree(tmp_path, "claude-code")
    _deny_write(monkeypatch, package)
    assert claude_update_blocker(str(cli), "npm") == NPM_NOT_WRITABLE_REASON


def test_the_outer_global_folder_is_the_one_checked(scoped, monkeypatch):
    # Newer npm packages ship the binary in a nested platform package; the
    # global folder npm rewrites is the FIRST node_modules on the path.
    modules, package, _ = scoped
    inner = package / "node_modules" / "@anthropic-ai" / "claude-code-linux-x64"
    inner.mkdir(parents=True)
    binary = inner / "claude"
    binary.write_text("")
    _deny_write(monkeypatch, package)
    assert claude_update_blocker(str(binary), "npm") == NPM_NOT_WRITABLE_REASON


def test_writable_npm_install_is_not_blocked(scoped):
    modules, package, cli = scoped
    assert os.access(package, os.W_OK) and os.access(modules, os.W_OK)
    assert claude_update_blocker(str(cli), "npm") is None


@pytest.mark.parametrize("method", ["native", "unknown"])
def test_only_npm_installs_are_judged(scoped, monkeypatch, method):
    modules, package, cli = scoped
    _deny_write(monkeypatch, modules, package)
    assert claude_update_blocker(str(cli), method) is None


def test_npm_method_with_a_binary_outside_node_modules_is_not_blocked(tmp_path, monkeypatch):
    # `installMethod` says npm but the resolved binary is elsewhere (a stale
    # ~/.claude.json): nothing to judge, so the update is offered.
    other = tmp_path / "bin"
    other.mkdir()
    _deny_write(monkeypatch, other)
    assert claude_update_blocker(str(other / "claude"), "npm") is None


def test_no_resolved_binary_is_not_blocked():
    assert claude_update_blocker(None, "npm") is None
    assert claude_update_blocker("", "npm") is None


def test_missing_folders_are_not_blocked(tmp_path, monkeypatch):
    gone = tmp_path / "gone" / "node_modules" / "@anthropic-ai" / "claude-code"
    _deny_write(monkeypatch, gone, gone.parent.parent)
    assert claude_update_blocker(str(gone / "cli.js"), "npm") is None


def test_a_failing_check_is_not_blocked(scoped, monkeypatch):
    modules, package, cli = scoped

    def _boom(*a, **k):
        raise OSError("no")
    monkeypatch.setattr(os, "access", _boom)
    assert claude_update_blocker(str(cli), "npm") is None


def test_reason_names_the_fix_in_plain_words():
    assert '"claude install"' in NPM_NOT_WRITABLE_REASON
    assert "—" not in NPM_NOT_WRITABLE_REASON
    assert "claude_update_blocker" in self_update.__all__
