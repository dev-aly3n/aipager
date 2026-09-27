"""The file tools must not reach aipager's /tmp control files.

Roadmap 8.50, found 2026-09-26 by the 8.48/8.49 review and confirmed:
the Write/Edit tools could rewrite ``/tmp/claude-policy-<session>.json``
and set ``bypass_safety: true`` — a restricted Telegram turn granting
itself the owner's bypass. The globs tested here close that for the
file tools only. They are no defence against a shell: the same OS user
owns these files, so a turn with Bash reaches them through a glob no
pattern anticipates (``sed -i … /tmp/*-policy-*.json``). The built-in
restricted roles are contained by having no Bash — see
``tests/test_policy_containment.py``.
"""
import os

import pytest

from aipager import safety

NA, NW = safety.DENY_PATHS_NO_ACCESS, safety.DENY_PATHS_NO_WRITE


@pytest.mark.parametrize("path", [
    "/tmp/claude-policy-victim.json",
    "/tmp/claude-notes-victim/1.json",
    "/tmp/claude-notes-victim",
    "/tmp/claude-status-victim",
    "/tmp/claude-reply-victim",
    "/tmp/claude-dtach-victim",
    "/tmp/../tmp/claude-policy-victim.json",
])
@pytest.mark.parametrize("tool", ["Write", "Edit", "Read", "MultiEdit"])
def test_control_files_are_protected_for_every_file_tool(tool, path):
    assert safety.path_violation(tool, {"file_path": path}, NA, NW)


def test_a_symlink_to_the_policy_file_is_protected(tmp_path):
    target = tmp_path / "claude-policy-real.json"
    target.write_text("{}")
    link = tmp_path / "innocent.json"
    link.symlink_to(target)
    # Point the rule at this tmp_path spelling of the control file.
    glob = str(tmp_path / "claude-policy-*")
    assert safety.path_violation("Write", {"file_path": str(link)}, (glob,), ())


@pytest.mark.parametrize("path", [
    "/tmp/claude-1000/-home-u-proj/abc/scratchpad/out.txt",
    "/tmp/aipager-files/1790367657_photo.jpg",
    "/tmp/other/claude-policy-notes.md",
])
def test_ordinary_tmp_files_stay_allowed(path):
    assert safety.path_violation("Write", {"file_path": path}, NA, NW) is None


def test_every_control_file_prefix_aipager_writes_is_listed():
    root = os.path.join(os.path.dirname(safety.__file__))
    prefixes = set()
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(".py"):
                text = open(os.path.join(dirpath, f), encoding="utf-8").read()
                for part in text.split('"/tmp/claude-')[1:]:
                    word = part.split("-")[0]
                    if word.isalpha():
                        prefixes.add(word)
    listed = {g.split("/tmp/claude-")[1].split("-")[0]
              for g in NA if g.startswith("/tmp/claude-")}
    assert prefixes <= listed, prefixes - listed
