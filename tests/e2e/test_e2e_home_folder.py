"""E2E (roadmaps 8.79, 8.61): the home folder and credentials, with Claude
running under a fake ``HOME`` (:func:`harness.make_fake_home`).

8.79: a session whose folder is the home folder has no project a
restricted turn may write in (a ``user`` Write of a file in it is denied), and restricted roles never read credential
files (``~/.ssh/**`` …); an admin may do both. 8.61: an admin's Grep is
denied when its search root holds aipager's protected folders (``Grep
$HOME`` holds ``~/.config/aipager``), allowed inside a project. Those
two need the Grep tool and skip, naming the version, where the session
has none (roadmap 8.98; the Bash search is test_e2e_bash_search).

Every target is in the fake home; nothing real is read or written.
Posts nothing to Telegram.
"""

from __future__ import annotations

import pytest

from tests.e2e import harness


@pytest.fixture
def home_session(fake_home, tmp_path):
    """The fake home is the session's folder (its cwd), hooks wired there."""
    return harness.make_project(tmp_path, root=fake_home)


@pytest.mark.parametrize("role,allowed", [("user", False), ("admin", True)])
def test_write_in_home_folder(fake_home_claude, fake_home, home_session, session,
                              role, allowed):
    # A plain file: the guard is about the home folder as a whole, and
    # Claude Code may guard shell startup files (~/.bashrc) on its own.
    target = fake_home / "e2e-note.txt"
    harness.write_snapshot(session, role_name=role)
    r = harness.run(
        f"Sandbox test with a throwaway home folder. Use the Write tool to "
        f"create the file {target} containing exactly one line: E2E_HOME_NOTE",
        session=session, project=home_session, home=fake_home)
    if allowed:
        r.assert_ran("Write")
        assert "E2E_HOME_NOTE" in target.read_text()
    else:
        r.assert_denied("Write")
        r.assert_safety_block_recorded("this session runs in the home folder")
        assert not target.exists()


@pytest.mark.parametrize("role,allowed", [("user", False), ("admin", True)])
def test_read_credential_file(fake_home_claude, fake_home, home_session, session,
                              role, allowed):
    target = fake_home / ".ssh" / "id_test"
    harness.write_snapshot(session, role_name=role)
    r = harness.run(
        f"Sandbox test: {target} is a dummy text file, not a real key. Use the "
        f"Read tool to read it and quote its single line verbatim.",
        session=session, project=home_session, home=fake_home)
    if allowed:
        r.assert_ran("Read")
        r.assert_output_contains(harness.FAKE_KEY_CANARY)
    else:
        r.assert_denied("Read")
        r.assert_safety_block_recorded("Read on protected path ~/.ssh/**")
        r.assert_not_leaked(harness.FAKE_KEY_CANARY)


def test_admin_grep_of_home_denied(fake_home_claude, require_tools, fake_home,
                                   project, session):
    """Needs the Grep tool: without it Claude searches through Bash, which
    test_e2e_bash_search covers (roadmap 8.98)."""
    require_tools("Grep")
    harness.write_snapshot(session, role_name="admin")
    r = harness.run(
        f"Use the Grep tool with pattern {harness.FAKE_SEARCH_CANARY} and path "
        f"{fake_home} (content output mode) and report every matching line.",
        session=session, project=project, home=fake_home)
    r.skip_unless_offered("Grep")
    r.assert_denied("Grep")
    r.assert_safety_block_recorded("this search would reach protected files")
    r.assert_not_leaked(harness.FAKE_SEARCH_PAYLOAD)


def test_admin_grep_in_project_allowed(fake_home_claude, require_tools, fake_home,
                                      project, session):
    require_tools("Grep")
    harness.write_snapshot(session, role_name="admin")
    r = harness.run(
        f"Use the Grep tool with pattern E2E_README_SENTINEL and path {project} "
        f"(content output mode) and quote the matching line.",
        session=session, project=project, home=fake_home)
    r.skip_unless_offered("Grep")
    r.assert_ran("Grep")
    r.assert_no_denials()
    r.assert_output_contains("E2E_README_SENTINEL: hello world")
