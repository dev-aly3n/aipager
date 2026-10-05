"""An admin's Grep/Glob never reaches aipager's protected folders
(roadmap 8.61).

An admin is unconfined (it keeps every tool, writes anywhere), and its
searches used to be checked on their ``path`` only. ``Grep ~`` (or ``/``,
or no path from a session in the home folder) then searched
``~/.config/aipager`` (the bot token) and ``~/.claude`` (Claude Code's
credentials), and a Glob with an absolute pattern and no path listed
their file names. The operator's decision (8.83, D-B): an admin does
everything an owner does except change members or roles and get past the
safety floor. Now an unconfined search is denied when a search root is a
protected folder, lies inside one, or holds one. Owners are never
checked; ``user`` and ``read_only`` keep their stricter allow-list.

Everything goes through ``enforce.decide`` with snapshots resolved from
real roles. The home folder is a fake one under ``tmp_path``.
"""

from __future__ import annotations

import json
import os

import pytest

from aipager import policy, safety
from aipager import policy_snapshot as ps
from aipager.dtach import enforce

SESSION = "claude-proj"
CLAUDE_SID = "5b1d7363-aaaa-bbbb-cccc-000000000861"

# A custom admin-like role from policy.yaml: bypass_role_denies, no owner
# bypass, so it is unconfined like the built-in admin.
_LEAD_YAML = """\
roles:
  lead:
    bypass_role_denies: true
    can_prompt: true
"""

UNCONFINED = ["admin", "lead"]


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path",
                        lambda n: tmp_path / f"{n}.policy.json")
    monkeypatch.delenv("CLAUDE_CODE_TMPDIR", raising=False)
    monkeypatch.delenv("TMPDIR", raising=False)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake home folder holding fake aipager and Claude Code folders.
    Nothing here reads the real ones."""
    h = tmp_path / "home" / "op"
    (h / ".config" / "aipager").mkdir(parents=True)
    (h / ".config" / "aipager" / "aipager.yaml").write_text("bot_token: FAKE\n")
    (h / ".config" / "other").mkdir()
    (h / ".claude").mkdir()
    monkeypatch.setenv("HOME", str(h))
    assert os.path.expanduser("~") == str(h)
    return h


@pytest.fixture
def project(home):
    p = home / "proj"
    (p / "src").mkdir(parents=True)
    (p / "src" / "a.py").write_text("x = 1\n")
    return p


def _role(tmp_path, name):
    f = tmp_path / "policy.yaml"
    f.write_text(_LEAD_YAML)
    role = policy.load_policy(f, tmp_path / "none.d").get_role(name)
    assert role is not None and role.name == name
    return role


def _use_role(tmp_path, name):
    snap = ps.resolve_snapshot(_role(tmp_path, name), None, None)
    ps.write_merged_snapshot(SESSION, snap)
    return snap


def _transcript(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"type": "user", "message": {
        "content": "[via Telegram · @bob · role:admin]\nfind it"}}) + "\n")
    return str(p)


def _decide(tmp_path, tool, tool_input, *, cwd):
    return enforce.decide({
        "hook_event_name": "PreToolUse",
        "session": SESSION,
        "session_id": CLAUDE_SID,
        "cwd": str(cwd),
        "tool_name": tool,
        "tool_input": tool_input,
        "transcript_path": _transcript(tmp_path),
    })


def _fmt(inp, home, project):
    return {k: v.format(home=home, proj=project, up=home.parent)
            if isinstance(v, str) else v for k, v in inp.items()}


# (tool, input, cwd key): cwd "proj" is the project, "home" the home folder.
REACHING = [
    ("Grep", {"path": "~", "pattern": "TOKEN"}, "proj"),
    ("Grep", {"path": "/", "pattern": "TOKEN"}, "proj"),
    # the folder holding the home folder, like /home
    ("Grep", {"path": "{up}", "pattern": "TOKEN"}, "proj"),
    ("Grep", {"path": "~/.config", "pattern": "TOKEN"}, "proj"),
    ("Grep", {"path": "~/.config/aipager", "pattern": "TOKEN"}, "proj"),
    ("Grep", {"path": "~/.config/aipager/aipager.yaml", "pattern": "x"}, "proj"),
    ("Grep", {"path": "~/.claude", "pattern": "x"}, "proj"),
    ("Grep", {"path": "..", "pattern": "TOKEN"}, "proj"),
    ("Grep", {"path": " ~", "pattern": "TOKEN"}, "proj"),   # JS trim
    ("Grep", {"pattern": "TOKEN"}, "home"),                 # no path, cwd ~
    ("Grep", {"path": "", "pattern": "TOKEN"}, "home"),
    ("Grep", {"path": "/tmp", "pattern": "bypass_safety"}, "proj"),
    ("Grep", {"path": "/tmp/claude-notes-other", "pattern": "x"}, "proj"),
    ("Grep", {"path": "/tmp/claude-policy-x.json", "pattern": "x"}, "proj"),
    ("Glob", {"pattern": "{home}/.config/aipager/*"}, "proj"),
    ("Glob", {"pattern": "~/.config/aipager/*"}, "proj"),
    ("Glob", {"pattern": "~/.claude/**"}, "proj"),
    ("Glob", {"pattern": "/tmp/claude-policy-*"}, "proj"),
    ("Glob", {"pattern": "../.config/aipager/*"}, "proj"),
    ("Glob", {"pattern": "src/../../.claude/*"}, "proj"),
    ("Glob", {"pattern": "{home}/.config/aipage\\r/*"}, "proj"),  # escape
    # No wildcard: Claude Code searches the parent folder for that name at
    # any depth (review 1, Claude Code 2.1.289 + its ripgrep).
    ("Glob", {"pattern": "{home}/.config/aipager.yaml"}, "proj"),
    ("Glob", {"pattern": "{home}/.credentials.json"}, "proj"),
    ("Glob", {"pattern": "{home}/.config/"}, "proj"),
    # The pattern is read whole, spaces included: its pieces alone (../d
    # and e/../.claude/*) reach nothing protected. Claude Code would read
    # this relative pattern as a filter inside the project; denying a
    # pattern whose .. climbs out is the stricter reading.
    ("Glob", {"pattern": "../d e/../.claude/*"}, "proj"),
    ("Glob", {"path": "~", "pattern": "**/*.yaml"}, "proj"),
    ("Glob", {"pattern": "**/*.py"}, "home"),
]

CLEAR = [
    ("Grep", {"path": "~/proj", "pattern": "x"}, "proj"),
    ("Grep", {"pattern": "x"}, "proj"),
    ("Grep", {"path": "src", "glob": "*.py", "pattern": "x"}, "proj"),
    ("Grep", {"path": "~/.config/other", "pattern": "x"}, "proj"),
    ("Grep", {"path": "/tmp/myproj", "pattern": "x"}, "proj"),
    # Claude Code's scratchpad root is no aipager control file
    ("Grep", {"path": "/tmp/claude-1000/p/s/scratchpad", "pattern": "x"}, "proj"),
    ("Grep", {"path": "/etc", "pattern": "x"}, "proj"),
    ("Glob", {"pattern": "**/*.py"}, "proj"),
    ("Glob", {"path": "{proj}", "pattern": "src/**/*.py"}, "proj"),
    ("Glob", {"pattern": "/etc/*.conf"}, "proj"),
    ("Glob", {"pattern": "../proj/src/*"}, "proj"),
    ("Glob", {"pattern": "{proj}/src/a.py"}, "proj"),
    # Braces are not split into folders (review 1: these read as "/").
    ("Glob", {"pattern": "{{src,tests}}/**/*.py"}, "proj"),
    ("Glob", {"pattern": "src/{{a,b}}/*.py"}, "proj"),
    ("Glob", {"pattern": "{{src,lib}}/*"}, "proj"),
    # A relative pattern is a filter inside the search folder to Claude
    # Code; an absolute alternative inside braces matches nothing there.
    ("Glob", {"pattern": "{{src/*,{home}/.claude/*}}"}, "proj"),
    ("Glob", {"pattern": "src/*.py ~/x/*"}, "proj"),
]


def _cwd(key, home, project):
    return home if key == "home" else project


@pytest.mark.parametrize("role", UNCONFINED)
@pytest.mark.parametrize("tool,inp,cwd", REACHING)
def test_an_unconfined_search_that_reaches_protected_files_is_denied(
        tmp_path, home, project, role, tool, inp, cwd):
    _use_role(tmp_path, role)
    block = _decide(tmp_path, tool, _fmt(inp, home, project),
                    cwd=_cwd(cwd, home, project))
    assert block and block["reason"].startswith(
        f"{tool} denied - this search would reach protected files ("), block


@pytest.mark.parametrize("role", UNCONFINED)
@pytest.mark.parametrize("tool,inp,cwd", CLEAR)
def test_an_unconfined_search_elsewhere_stays_allowed(
        tmp_path, home, project, role, tool, inp, cwd):
    _use_role(tmp_path, role)
    assert _decide(tmp_path, tool, _fmt(inp, home, project),
                   cwd=_cwd(cwd, home, project)) is None


@pytest.mark.parametrize("tool,inp,cwd", REACHING + CLEAR)
def test_an_owner_is_never_checked(tmp_path, home, project, tool, inp, cwd):
    _use_role(tmp_path, "owner")
    assert _decide(tmp_path, tool, _fmt(inp, home, project),
                   cwd=_cwd(cwd, home, project)) is None


def test_the_deny_reason_names_the_folder_in_plain_words(tmp_path, home, project):
    _use_role(tmp_path, "admin")
    block = _decide(tmp_path, "Grep", {"path": "~/.config", "pattern": "x"},
                    cwd=project)
    assert block == {"tool": "Grep", "reason": (
        "Grep denied - this search would reach protected files "
        "(~/.config/aipager); search inside a project folder instead")}
    block = _decide(tmp_path, "Grep", {"path": "/tmp/claude-policy-x",
                                       "pattern": "x"}, cwd=project)
    assert block["reason"] == (
        "Grep denied - this search would reach protected files "
        "(/tmp/claude-policy-*); search inside a project folder instead")
    assert "—" not in block["reason"]


def test_a_tmp_folder_holding_no_control_file_is_not_protected(tmp_path, home):
    """``/tmp`` holds aipager's control files, ``/tmp/myproj`` does not,
    and neither does a folder whose name only starts the same way."""
    no_access = ("/tmp/claude-policy-*",)
    check = lambda p: safety.path_violation(  # noqa: E731
        "Grep", {"path": p, "pattern": "x"}, no_access, (), cwd=str(home))
    assert check("/tmp")
    assert check("/tmp/claude-policy-abc")
    assert check("/tmp/claude-policy-abc/sub")
    assert check("/tmp/myproj") is None
    assert check("/tmp/claude-other") is None
    assert check("/tmp/myproj/claude-policy-x") is None


def test_a_literal_protected_file_is_reached_by_its_folder(tmp_path, home):
    no_access = ("~/.netrc",)
    check = lambda p: safety.path_violation(  # noqa: E731
        "Grep", {"path": p, "pattern": "x"}, no_access, (), cwd=str(home))
    assert check("~")
    assert check("~/.netrc")
    assert check("~/.netrc/inner")   # inside a literal protected path
    assert check("~/proj") is None


def test_a_brace_protected_glob_protects_its_whole_folder(tmp_path, home):
    """fnmatch reads no braces, so ``/srv/{a,b}/**`` counts as all of
    ``/srv`` (review 1)."""
    check = lambda p: safety.path_violation(  # noqa: E731
        "Grep", {"path": p, "pattern": "x"}, ("/srv/{a,b}/**",), (),
        cwd=str(home))
    assert check("/srv/a")
    assert check("/srv/a/deep")
    assert check("/srv")
    assert check("/opt") is None


@pytest.mark.parametrize("role", UNCONFINED)
def test_a_symlink_into_a_protected_folder_is_denied(tmp_path, home, project, role):
    _use_role(tmp_path, role)
    (project / "notes").symlink_to(home / ".claude")
    assert _decide(tmp_path, "Grep", {"path": "notes", "pattern": "x"},
                   cwd=project)
    assert _decide(tmp_path, "Glob", {"pattern": "notes/*"}, cwd=project)
    # A wildcard-free pattern whose folder climbs out through a symlink:
    # on disk, lnk/.. is the folder above lnk's target (~/.config).
    (project / "lnk").symlink_to(home / ".config" / "other")
    assert _decide(tmp_path, "Glob",
                   {"pattern": f"{project}/lnk/../aipager.yaml"}, cwd=project)
    # The search folder itself a symlink to the home folder.
    (project / "up").symlink_to(home)
    assert _decide(tmp_path, "Grep", {"path": "up", "pattern": "x"},
                   cwd=project)


@pytest.mark.parametrize("role", UNCONFINED)
@pytest.mark.parametrize("tool,inp", [
    ("Grep", {"path": "~/proj\x00", "pattern": "x"}),
    ("Grep", {"path": 7, "pattern": "x"}),
    ("Grep", {"path": ["~"], "pattern": "x"}),
    ("Glob", {"pattern": "src/\x00*"}),
    ("Glob", {"pattern": 7}),
    ("Glob", {"pattern": ["**"]}),
])
def test_unreadable_input_is_denied(tmp_path, home, project, role, tool, inp):
    _use_role(tmp_path, role)
    block = _decide(tmp_path, tool, inp, cwd=project)
    assert block and "unreadable" in block["reason"], block


def test_no_search_folder_at_all_is_denied(home):
    """No ``path`` and no payload cwd: nothing to check, so deny."""
    reason = safety.path_violation("Grep", {"pattern": "x"},
                                   safety.DENY_PATHS_NO_ACCESS, ())
    assert reason == "Grep with an unreadable search folder"


@pytest.mark.parametrize("role", UNCONFINED)
def test_an_unanchored_protected_glob_denies_every_search(
        tmp_path, home, project, role):
    snap = ps.resolve_snapshot(_role(tmp_path, role), None, None)
    snap["deny_paths_no_access"].append("**/.env")
    ps.write_merged_snapshot(SESSION, snap)
    block = _decide(tmp_path, "Grep", {"path": "src", "pattern": "x"},
                    cwd=project)
    assert block and block["reason"] == (
        "Grep denied - the protected path **/.env can be in any folder, so "
        "this role cannot search with Grep or Glob")
    assert _decide(tmp_path, "Glob", {"pattern": "*.py"}, cwd=project)
    # Reading a file is not a search: unchanged.
    assert _decide(tmp_path, "Read", {"file_path": "src/a.py"},
                   cwd=project) is None


def test_the_admin_snapshot_is_unconfined_and_holds_the_floor(tmp_path, home):
    """What the rows above rely on: the admin and the custom role run
    unconfined, with the floor's protected paths."""
    for name in UNCONFINED:
        snap = ps.resolve_snapshot(_role(tmp_path, name), None, None)
        assert snap["confine_writes"] is False
        assert snap["bypass_safety"] is False
        assert set(safety.DENY_PATHS_NO_ACCESS) <= set(
            snap["deny_paths_no_access"])


# ---- confined roles: unchanged -------------------------------------------

# The confined allow-list's reasons for the same inputs, as they were
# before 8.61 (no row may switch to the unconfined wording).
_OUTSIDE = ("{tool} outside the session's folder - a restricted turn "
            "searches only its project and scratchpad")
_WIDE = ("{tool} denied - this session runs in the home folder (or a folder "
         "above it), and this role can only search inside a project folder")
CONFINED_EXPECTED = [
    ("Grep", {"path": "~", "pattern": "x"}, "proj", _OUTSIDE),
    ("Grep", {"path": "/", "pattern": "x"}, "proj", _OUTSIDE),
    ("Grep", {"path": "~/.config", "pattern": "x"}, "proj", _OUTSIDE),
    ("Grep", {"path": "/tmp", "pattern": "x"}, "proj", _OUTSIDE),
    ("Grep", {"path": "/tmp/myproj", "pattern": "x"}, "proj", _OUTSIDE),
    ("Grep", {"pattern": "x"}, "home", _WIDE),
    ("Glob", {"pattern": "~/.config/aipager/*"}, "proj",
     "{tool} with a glob that is not a plain relative pattern"),
    ("Grep", {"path": "~/proj", "pattern": "x"}, "proj", None),
    ("Grep", {"pattern": "x"}, "proj", None),
    ("Glob", {"pattern": "**/*.py"}, "proj", None),
]


@pytest.mark.parametrize("role", ["user", "read_only"])
@pytest.mark.parametrize("tool,inp,cwd,expected", CONFINED_EXPECTED)
def test_confined_roles_keep_their_results(
        tmp_path, home, project, role, tool, inp, cwd, expected):
    _use_role(tmp_path, role)
    block = _decide(tmp_path, tool, _fmt(inp, home, project),
                    cwd=_cwd(cwd, home, project))
    if expected is None:
        assert block is None
    else:
        assert block == {"tool": tool, "reason": expected.format(tool=tool)}


def test_path_violation_names_the_unconfined_branch(home):
    """``write_roots=None`` is the unconfined case; it used to return
    early for a search with no ``path``."""
    reason = safety.path_violation(
        "Grep", {"pattern": "x"}, safety.DENY_PATHS_NO_ACCESS, (),
        cwd=str(home))
    assert reason and "protected files" in reason


def test_a_case_insensitive_disk_folds_the_comparison(home, monkeypatch):
    """macOS: ``/TMP/CLAUDE-POLICY-x`` opens aipager's policy file and
    ``~/.CONFIG`` holds ``~/.config/aipager``."""
    monkeypatch.setattr(safety, "_case_insensitive_fs", lambda: True)
    check = lambda p: safety.path_violation(  # noqa: E731
        "Grep", {"path": p, "pattern": "x"}, safety.DENY_PATHS_NO_ACCESS, (),
        cwd=str(home))
    assert check("/TMP/CLAUDE-POLICY-x")
    assert check("~/.CONFIG")
    assert check("~/.Config/AIPAGER/aipager.yaml")
    assert check("/TMP/myproj") is None
