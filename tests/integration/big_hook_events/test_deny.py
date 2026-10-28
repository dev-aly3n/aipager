"""Restricted turns' file tools are denied inside the spill folder."""

from __future__ import annotations

import os

import pytest

from aipager import config, instance, safety


def _spill_file():
    base = os.path.dirname(config.SOCKET_PATH)
    return os.path.join(base, f"aipager-spill-{os.getuid()}", "ev-" + "0" * 32 + ".json")


def test_glob_in_deny_list():
    assert set(instance.spill_globs()) <= set(safety.DENY_PATHS_NO_ACCESS)


def test_glob_beside_socket():
    g = instance.spill_globs()[0]
    assert os.path.dirname(g.rstrip("/*")) == os.path.dirname(instance.control_socket_path())


@pytest.mark.parametrize("tool,key", [("Read", "file_path"), ("Write", "file_path"),
                                      ("Edit", "file_path"), ("Grep", "path"),
                                      ("Glob", "path")],
                         ids=["read", "write", "edit", "grep", "glob"])
def test_file_tools_denied(tool, key):
    inp = {key: _spill_file(), "content": "x", "old_string": "a", "new_string": "b",
           "pattern": "x"}
    assert safety.path_violation(tool, inp, safety.DENY_PATHS_NO_ACCESS, (),
                                 cwd="/home/alice")


def test_folder_itself_denied():
    inp = {"path": os.path.dirname(_spill_file()), "pattern": "x"}
    assert safety.path_violation("Grep", inp, safety.DENY_PATHS_NO_ACCESS, (),
                                 cwd="/home/alice")


def test_other_uid_folder_denied():
    p = os.path.join(os.path.dirname(config.SOCKET_PATH), "aipager-spill-99999",
                     "ev-" + "1" * 32 + ".json")
    assert safety.path_violation("Read", {"file_path": p}, safety.DENY_PATHS_NO_ACCESS,
                                 (), cwd="/home/alice")


def test_project_file_allowed():
    assert safety.path_violation("Read", {"file_path": "/home/alice/proj/a.py"},
                                 safety.DENY_PATHS_NO_ACCESS, (),
                                 cwd="/home/alice/proj") is None


def test_instance_glob(monkeypatch, tmp_path):
    monkeypatch.setenv("AIPAGER_INSTANCE_DIR", str(tmp_path / "inst"))
    g = instance.spill_globs()[0]
    assert g.startswith(os.path.dirname(instance.control_socket_path()) + "/aipager-spill-")
