"""Restricted roles never write in the home folder and never read
credential files (roadmap 8.79, group audit A4, probe p6 part b).

- A ``user`` member's session started in the daemon's folder, which is
  ``$HOME`` under the systemd user unit. Writes were confined to "the
  session's folder", so ``~/.bashrc``, ``~/.ssh/authorized_keys`` and
  ``~/.config/systemd/user/x.service`` were writable: a persistent shell
  from a role with no Bash. Now a session folder that is ``/``, the home
  folder or above it is no write or search root for a confined turn.
- Restricted roles could Read/Grep anything outside aipager's own
  protected paths, the operator's SSH keys and ``~/.git-credentials``
  included. The credential files are now no-access for every role
  without ``bypass_role_denies``.
- The launch side does not offer such a member the home folder (or
  ``/``, or above it) as a session folder; it stays the parent for a new
  folder.

Owners (and admins, which are not confined) see no change. Every test
runs against a fake home under ``tmp_path``; nothing reads the real one.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import policy, preferences, safety
from aipager import policy_snapshot as ps
from aipager.bot import new_flow
from aipager.dtach import enforce, inject
from aipager.miniapp import launch
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry

SESSION = "claude-proj"
CLAUDE_SID = "5b1d7363-aaaa-bbbb-cccc-000000000079"

GROUP = -100
DM = 555
OWNER = 1
ADMIN = 2
USER = 3
READ_ONLY = 4


# ---- isolation -------------------------------------------------------------

@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake home folder, as ``~`` for this test. ``tmp_path`` holds it,
    so ``tmp_path`` is an ancestor of the home folder."""
    h = tmp_path / "home" / "op"
    h.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    assert os.path.expanduser("~") == str(h)
    return h


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path",
                        lambda n: tmp_path / f"{n}.policy.json")
    monkeypatch.delenv("CLAUDE_CODE_TMPDIR", raising=False)
    monkeypatch.delenv("TMPDIR", raising=False)
    launch._created.clear()
    yield
    launch._created.clear()


@pytest.fixture
def project(home):
    p = home / "proj"
    (p / "src").mkdir(parents=True)
    (p / "src" / "app.py").write_text("print('hi')\n")
    return p


def _pol(tmp_path=None, text=None):
    if text is None:
        return policy.load_policy(Path("/nonexistent/p.yaml"),
                                  Path("/nonexistent/p.d"))
    f = tmp_path / "policy.yaml"
    f.write_text(text)
    return policy.load_policy(f, tmp_path / "none.d")


def _role(name):
    return _pol().get_role(name)


def _use(snap):
    ps.write_merged_snapshot(SESSION, snap)


def _use_role(name):
    _use(ps.resolve_snapshot(_role(name), None, None))


def _transcript(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"type": "user", "message": {
        "content": "[via Telegram · @bob · role:user]\ndo it"}}) + "\n")
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


def _write(tmp_path, path, *, cwd):
    return _decide(tmp_path, "Write", {"file_path": str(path), "content": "x"},
                   cwd=cwd)


HOME_FILES = (".bashrc", ".ssh/authorized_keys",
              ".config/systemd/user/x.service")


def _wide_cwds(home):
    # The home folder itself, the filesystem root, and a folder above home.
    return {"home": home, "root": Path("/"), "ancestor": home.parent}


# ===========================================================================
# 1. A confined turn in the home folder (or above it) writes nowhere there
# ===========================================================================

@pytest.mark.parametrize("where", ["home", "root", "ancestor"])
@pytest.mark.parametrize("rel", HOME_FILES)
@pytest.mark.parametrize("role", ["user", "read_only"])
def test_a_restricted_write_in_the_home_folder_is_denied(
        tmp_path, home, where, rel, role):
    _use_role(role)
    cwd = _wide_cwds(home)[where]
    block = _write(tmp_path, home / rel, cwd=cwd)
    assert block is not None
    assert _expected(rel) in block["reason"]


def _expected(rel):
    # ~/.ssh is a credential folder too, and that rule is checked first.
    return ("protected path ~/.ssh/**" if rel.startswith(".ssh/")
            else "runs in the home folder")


@pytest.mark.parametrize("rel", HOME_FILES)
def test_the_floor_is_held_to_the_same_rule(tmp_path, home, rel):
    # No snapshot on disk: the built-in floor (an unattributed turn).
    block = _write(tmp_path, home / rel, cwd=home)
    assert block and _expected(rel) in block["reason"]


def test_a_symlink_to_the_home_folder_is_the_home_folder(tmp_path, home):
    link = tmp_path / "looks-like-a-project"
    link.symlink_to(home)
    _use_role("user")
    block = _write(tmp_path, link / ".bashrc", cwd=link)
    assert block and "runs in the home folder" in block["reason"]


def test_the_deny_reason_says_why_in_plain_words(tmp_path, home):
    _use_role("user")
    block = _write(tmp_path, home / ".bashrc", cwd=home)
    assert block["reason"] == (
        "Write denied - this session runs in the home folder (or a folder "
        "above it), and this role can only write inside a project folder")
    out = json.loads(enforce.deny_decision_json(block["reason"]))
    assert out["hookSpecificOutput"]["permissionDecisionReason"] == (
        "aipager safety policy: " + block["reason"])


@pytest.mark.parametrize("tool,key", [("Edit", "file_path"),
                                      ("NotebookEdit", "notebook_path")])
def test_every_write_tool_is_held(tmp_path, home, tool, key):
    _use_role("user")
    assert _decide(tmp_path, tool, {key: str(home / "notes.ipynb")}, cwd=home)


def test_a_write_in_a_project_folder_under_home_is_allowed(tmp_path, home, project):
    _use_role("user")
    assert _write(tmp_path, project / "src" / "new.py", cwd=project) is None
    # ...and the home folder's own files stay outside it.
    block = _write(tmp_path, home / ".bashrc", cwd=project)
    assert block and "outside the session's folder" in block["reason"]


@pytest.mark.parametrize("where", ["home", "root", "ancestor"])
def test_the_scratchpad_stays_writable(tmp_path, home, where):
    _use_role("user")
    cwd = _wide_cwds(home)[where]
    scratch = enforce._scratch_root(str(cwd), CLAUDE_SID)
    assert _write(tmp_path, f"{scratch}/scratchpad/notes.md", cwd=cwd) is None


@pytest.mark.parametrize("role", ["owner", "admin"])
@pytest.mark.parametrize("rel", HOME_FILES)
def test_owner_and_admin_still_write_in_the_home_folder(tmp_path, home, role, rel):
    _use_role(role)
    assert _write(tmp_path, home / rel, cwd=home) is None


def test_write_roots_drop_only_a_wide_folder(home, project):
    assert str(project) in enforce._write_roots(str(project), CLAUDE_SID)
    for cwd in (home, Path("/"), home.parent):
        roots = enforce._write_roots(str(cwd), CLAUDE_SID)
        assert roots == (enforce._scratch_root(str(cwd), CLAUDE_SID),)


def test_is_wide_folder(home, project, tmp_path):
    assert safety.is_wide_folder(str(home))
    assert safety.is_wide_folder("/")
    assert safety.is_wide_folder(str(home.parent))
    assert safety.is_wide_folder(str(home) + "/")
    assert not safety.is_wide_folder(str(project))
    assert not safety.is_wide_folder(str(tmp_path / "elsewhere"))
    assert safety.is_wide_folder("")
    assert safety.is_wide_folder("a\x00b")


# ===========================================================================
# 2. ...and searches nothing there
# ===========================================================================

@pytest.mark.parametrize("where", ["home", "root", "ancestor"])
@pytest.mark.parametrize("tool,extra", [("Grep", {"pattern": "token"}),
                                        ("Glob", {"pattern": "*.py"})])
def test_a_restricted_search_from_the_home_folder_is_denied(
        tmp_path, home, where, tool, extra):
    _use_role("user")
    cwd = _wide_cwds(home)[where]
    block = _decide(tmp_path, tool, dict(extra), cwd=cwd)
    assert block and "runs in the home folder" in block["reason"]
    assert "can only search inside a project folder" in block["reason"]


def test_a_search_of_a_project_from_the_home_folder_is_denied(tmp_path, home, project):
    _use_role("user")
    block = _decide(tmp_path, "Grep", {"pattern": "x", "path": str(project)},
                    cwd=home)
    assert block and "runs in the home folder" in block["reason"]


@pytest.mark.parametrize("tool,extra", [("Grep", {"pattern": "print"}),
                                        ("Glob", {"pattern": "**/*.py"})])
def test_a_search_inside_a_project_is_fine(tmp_path, project, tool, extra):
    _use_role("user")
    assert _decide(tmp_path, tool, dict(extra), cwd=project) is None
    assert _decide(tmp_path, tool, {**extra, "path": "src"}, cwd=project) is None


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_owner_and_admin_still_search_from_the_home_folder(tmp_path, home, role):
    _use_role(role)
    assert _decide(tmp_path, "Grep", {"pattern": "x"}, cwd=home) is None


# ===========================================================================
# 3. Credential files: no access for roles without bypass_role_denies
# ===========================================================================

CREDENTIAL_FILES = {
    "~/.ssh/**": ".ssh/id_ed25519",
    "~/.gnupg/**": ".gnupg/private-keys-v1.d/a.key",
    "~/.aws/**": ".aws/credentials",
    "~/.config/gh/**": ".config/gh/hosts.yml",
    "~/.git-credentials": ".git-credentials",
    "~/.netrc": ".netrc",
    "~/.docker/config.json": ".docker/config.json",
    "~/.kube/**": ".kube/config",
    "~/.config/gcloud/**": ".config/gcloud/application_default_credentials.json",
    "~/.azure/**": ".azure/msal_token_cache.json",
    "~/.password-store/**": ".password-store/bank.gpg",
    "~/.pgpass": ".pgpass",
    "~/.npmrc": ".npmrc",
    "~/.pypirc": ".pypirc",
}


def test_the_credential_list_is_exactly_the_one_asked_for():
    assert set(safety.CREDENTIAL_PATHS) == set(CREDENTIAL_FILES)


def test_every_credential_rule_is_home_anchored():
    # An unanchored rule would deny every restricted Grep/Glob.
    assert all(p.startswith("~/") for p in safety.CREDENTIAL_PATHS)


def _restricted(kind, tmp_path):
    if kind == "floor":
        return None  # no snapshot on disk
    if kind == "unknown":
        return ps.resolve_snapshot(None, None, None)
    if kind == "custom":
        return ps.resolve_snapshot(
            _pol(tmp_path,
                 "roles:\n  dev:\n    can_prompt: true\n").get_role("dev"),
            None, None)
    return ps.resolve_snapshot(_role(kind), None, None)


@pytest.mark.parametrize("glob,rel", sorted(CREDENTIAL_FILES.items()))
@pytest.mark.parametrize("kind", ["user", "read_only", "floor", "unknown", "custom"])
@pytest.mark.parametrize("spelling", ["tilde", "absolute"])
def test_a_restricted_read_of_a_credential_file_is_denied(
        tmp_path, home, project, glob, rel, kind, spelling):
    snap = _restricted(kind, tmp_path)
    if snap is not None:
        _use(snap)
    target = f"~/{rel}" if spelling == "tilde" else str(home / rel)
    block = _decide(tmp_path, "Read", {"file_path": target}, cwd=project)
    assert block is not None
    assert block["reason"] == f"Read on protected path {glob}"


@pytest.mark.parametrize("rel", sorted(CREDENTIAL_FILES.values()))
@pytest.mark.parametrize("role", ["owner", "admin"])
def test_owner_and_admin_still_read_credential_files(tmp_path, home, project, role, rel):
    _use_role(role)
    assert _decide(tmp_path, "Read", {"file_path": str(home / rel)},
                   cwd=project) is None


def test_a_symlink_in_the_project_to_a_key_is_denied(tmp_path, home, project):
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_ed25519").write_text("not a real key\n")
    (project / "key").symlink_to(home / ".ssh" / "id_ed25519")
    _use_role("user")
    block = _decide(tmp_path, "Read", {"file_path": "key"}, cwd=project)
    assert block and block["reason"] == "Read on protected path ~/.ssh/**"


def test_a_project_file_is_still_readable(tmp_path, project):
    _use_role("user")
    assert _decide(tmp_path, "Read", {"file_path": "src/app.py"}, cwd=project) is None
    # A file elsewhere in the home folder that holds no credentials, too.
    assert _decide(tmp_path, "Read", {"file_path": "~/notes.txt"}, cwd=project) is None


@pytest.mark.parametrize("folder,glob", [(".ssh", "~/.ssh/**"),
                                         (".aws", "~/.aws/**")])
def test_a_session_in_a_credential_folder_can_neither_search_nor_write_it(
        tmp_path, home, folder, glob):
    """The search and write side of the list: a session started inside
    ``~/.ssh`` (a folder this chat once used) has it as its project."""
    cwd = home / folder
    cwd.mkdir()
    _use_role("user")
    block = _decide(tmp_path, "Grep", {"pattern": "x"}, cwd=cwd)
    assert block and block["reason"] == f"Grep on protected path {glob}"
    block = _write(tmp_path, cwd / "authorized_keys", cwd=cwd)
    assert block and block["reason"] == f"Write on protected path {glob}"


def test_a_search_from_a_project_folder_is_not_blocked_by_the_list(tmp_path, project):
    _use_role("user")
    assert _decide(tmp_path, "Grep", {"pattern": "x", "glob": "*.py"},
                   cwd=project) is None


def test_policy_yaml_can_lift_the_list_for_a_role(tmp_path, home, project):
    pol = _pol(tmp_path, "roles:\n  user:\n    deny_paths_no_access: []\n")
    _use(ps.resolve_snapshot(pol.get_role("user"), None, None))
    assert _decide(tmp_path, "Read", {"file_path": str(home / ".netrc")},
                   cwd=project) is None


def test_a_custom_role_with_the_admin_bypass_reads_them(tmp_path, home, project):
    pol = _pol(tmp_path, "roles:\n  lead:\n    bypass_role_denies: true\n")
    _use(ps.resolve_snapshot(pol.get_role("lead"), None, None))
    assert _decide(tmp_path, "Read", {"file_path": str(home / ".netrc")},
                   cwd=project) is None


@pytest.mark.parametrize("name", ["owner", "admin"])
def test_owner_and_admin_snapshots_never_carry_the_list(name):
    snap = ps.resolve_snapshot(_role(name), None, None)
    assert not set(safety.CREDENTIAL_PATHS) & set(snap["deny_paths_no_access"])


def test_the_floor_and_restricted_roles_carry_the_list():
    assert set(safety.CREDENTIAL_PATHS) <= set(ps.FLOOR_SNAPSHOT["deny_paths_no_access"])
    for name in ("user", "read_only"):
        snap = ps.resolve_snapshot(_role(name), None, None)
        assert set(safety.CREDENTIAL_PATHS) <= set(snap["deny_paths_no_access"])


def test_a_carried_admin_turn_does_not_gain_the_list():
    admin = ps.resolve_snapshot(_role("admin"), None, None)
    carried = ps.carried_snapshot(admin)
    assert not set(safety.CREDENTIAL_PATHS) & set(carried["deny_paths_no_access"])
    assert set(safety.DENY_PATHS_NO_ACCESS) <= set(carried["deny_paths_no_access"])


def test_a_lifted_list_comes_back_while_a_turn_is_carried(tmp_path):
    """Documented: a role whose list policy.yaml lifted gets it back when
    its running turn is carried (strictest wins, fail closed)."""
    pol = _pol(tmp_path, "roles:\n  user:\n    deny_paths_no_access: []\n")
    snap = ps.resolve_snapshot(pol.get_role("user"), None, None)
    assert not set(safety.CREDENTIAL_PATHS) & set(snap["deny_paths_no_access"])
    carried = ps.carried_snapshot(snap)
    assert set(safety.CREDENTIAL_PATHS) <= set(carried["deny_paths_no_access"])


def test_a_carried_restricted_turn_gets_the_list_back():
    thin = {**ps.resolve_snapshot(_role("user"), None, None),
            "deny_paths_no_access": []}
    carried = ps.carried_snapshot(thin)
    assert set(safety.CREDENTIAL_PATHS) <= set(carried["deny_paths_no_access"])


# ===========================================================================
# 4. Launch: a confined member is never offered the home folder
# ===========================================================================

def _scopes():
    members = (Member(id=OWNER, label="aly", role="owner"),
               Member(id=ADMIN, label="ada", role="admin"),
               Member(id=USER, label="bob", role="user"),
               Member(id=READ_ONLY, label="cy", role="read_only"))
    return [Scope(chat_id=DM, kind="dm", label="aly",
                  members=(Member(id=OWNER, label="aly", role="owner"),)),
            Scope(chat_id=GROUP, kind="group", label="team", members=members)]


@pytest.fixture
def daemon_in_home(home, monkeypatch):
    """The daemon runs in the home folder (a systemd user unit)."""
    monkeypatch.setattr(inject, "_PROJECT_DIR", str(home))
    return home


def _registry(*cwds, chat=GROUP):
    reg = SessionRegistry()
    for i, cwd in enumerate(cwds):
        s = reg.get_or_create(f"claude-s{i}")
        s.label = f"s{i}"
        s.scope_chat_id = chat
        s.cwd = str(cwd)
    return reg


@pytest.fixture
def gbot(mk_bot):
    def _mk(registry=None, *, scopes=None):
        bot = mk_bot(registry or SessionRegistry(),
                     scopes=_scopes() if scopes is None else scopes)
        bot.policy = _pol()
        bot._app.bot.edit_message_text = AsyncMock()
        return bot
    return _mk


@pytest.mark.parametrize("uid,confined", [(OWNER, False), (ADMIN, False),
                                          (USER, True), (READ_ONLY, True),
                                          (999, True)])
def test_who_is_confined(gbot, uid, confined):
    assert gbot()._is_confined_user(uid, GROUP) is confined


def test_nobody_is_confined_outside_scope_mode(gbot):
    bot = gbot(scopes=None)
    bot.scopes = None
    assert bot._is_confined_user(USER, GROUP) is False


def test_allowed_roots_per_role(daemon_in_home, project, tmp_path):
    home = daemon_in_home
    reg = _registry(project, "/", home.parent)
    full = launch.allowed_roots(reg, GROUP)
    assert str(home) in full and str(project) in full
    assert "/" in full and str(home.parent) in full      # unchanged for owners
    assert launch.allowed_roots(reg, GROUP, confined=True) == [str(project)]


def test_validate_cwd_refuses_a_wide_folder_for_a_confined_caller(daemon_in_home, project):
    home = daemon_in_home
    full = [str(home)]
    assert launch.validate_cwd(str(home), full) == (str(home), "")
    assert launch.validate_cwd(str(project), full) == (str(project), "")
    assert launch.validate_cwd(str(home), full, confined=True) == (
        "", launch.WIDE_FOLDER_REFUSAL)
    # The empty choice is the daemon's folder: home here.
    assert launch.validate_cwd("", full) == ("", "")
    assert launch.validate_cwd("", full, confined=True) == (
        "", launch.WIDE_FOLDER_REFUSAL)


def test_the_empty_choice_is_fine_when_the_daemon_runs_in_a_project(
        monkeypatch, home, project):
    monkeypatch.setattr(inject, "_PROJECT_DIR", str(project))
    assert launch.validate_cwd("", [str(project)], confined=True) == ("", "")


def test_a_confined_member_creates_a_project_folder_in_home(daemon_in_home):
    home = daemon_in_home
    path, existed, err = launch.create_directory(str(home), "myproj", [str(home)],
                                                 confined=True)
    assert (path, existed, err) == (str(home / "myproj"), False, "")


def test_a_confined_member_cannot_reuse_an_existing_home_folder(daemon_in_home):
    home = daemon_in_home
    (home / "Documents").mkdir()
    path, existed, err = launch.create_directory(str(home), "Documents",
                                                 [str(home)], confined=True)
    assert path == "" and "already exists" in err
    # The owner reuses it as before.
    assert launch.create_directory(str(home), "Documents", [str(home)]) == (
        str(home / "Documents"), True, "")


@pytest.mark.parametrize("name", ["node_modules", "bin", "Node_Modules"])
def test_a_confined_member_cannot_create_a_folder_others_load_code_from(
        daemon_in_home, project, name):
    home = daemon_in_home
    path, _e, err = launch.create_directory(str(home), name, [str(home)],
                                            confined=True)
    assert path == "" and "load code from it" in err
    assert not (home / name).exists()
    # Inside a project it is an ordinary folder; the owner may make it anywhere.
    assert launch.create_directory(str(project), name, [str(project)],
                                   confined=True)[2] == ""
    assert launch.create_directory(str(home), name, [str(home)])[2] == ""


def test_a_confined_member_may_reuse_a_folder_inside_a_project(daemon_in_home, project):
    (project / "sub").mkdir()
    assert launch.create_directory(str(project), "sub", [str(project)],
                                   confined=True) == (str(project / "sub"), True, "")


def test_a_created_folder_becomes_a_launch_root_for_the_chat(daemon_in_home):
    home = daemon_in_home
    reg = _registry()
    path, _e, _err = launch.create_directory(str(home), "myproj", [str(home)],
                                             confined=True)
    launch.remember_created(GROUP, path)
    assert launch.allowed_roots(reg, GROUP, confined=True) == [path]
    assert launch.allowed_roots(reg, -999, confined=True) == []


def test_launch_folder_refusal(daemon_in_home, project):
    home = daemon_in_home
    assert launch.launch_folder_refusal(None, True) == launch.WIDE_FOLDER_REFUSAL
    assert launch.launch_folder_refusal(str(home), True) == launch.WIDE_FOLDER_REFUSAL
    assert launch.launch_folder_refusal("/", True) == launch.WIDE_FOLDER_REFUSAL
    assert launch.launch_folder_refusal(str(project), True) == ""
    assert launch.launch_folder_refusal(None, False) == ""
    assert len(launch.WIDE_FOLDER_REFUSAL) <= 200     # fits an alert


# ---- the Name card -----------------------------------------------------------

def _open(bot, run_async, uid, chat=GROUP):
    send = AsyncMock(return_value=MagicMock(message_id=900))
    run_async(new_flow._open_card(bot, chat, uid, send))
    text, kb = send.await_args.args
    return text, kb


def _buttons(kb):
    return [b.text for row in kb.inline_keyboard for b in row]


def _tap(bot, run_async, uid, action, chat=GROUP):
    query = MagicMock()
    query.answer = AsyncMock()
    query.message = MagicMock(message_id=900)
    query.from_user = MagicMock(id=uid)
    update = MagicMock()
    update.callback_query = query
    update.effective_user = MagicMock(id=uid)
    update.effective_chat = MagicMock(id=chat, type="supergroup")
    update.message = None
    run_async(new_flow.handle_callback(bot, update, query, "_", action))
    return query


def _last_edit(bot):
    kw = bot._app.bot.edit_message_text.await_args.kwargs
    return kw["text"], kw.get("reply_markup")


def test_a_user_with_no_project_folder_is_told_what_to_do(gbot, run_async, daemon_in_home):
    bot = gbot()
    text, _kb = _open(bot, run_async, USER)
    assert launch.NO_PROJECT_FOLDER in text
    assert "no project folder yet" in text
    assert str(daemon_in_home) not in text


def test_a_user_with_a_project_folder_is_told_to_pick_it(gbot, run_async, daemon_in_home, project):
    bot = gbot(_registry(project))
    text, _kb = _open(bot, run_async, USER)
    assert "Pick a project folder with 📁 Folder first." in text
    assert launch.NO_PROJECT_FOLDER not in text


def test_a_user_with_nowhere_to_make_a_folder_is_told_to_ask_an_admin(
        gbot, run_async, monkeypatch, home):
    # The daemon runs in `/` (launchd) and the chat has used no folder.
    monkeypatch.setattr(inject, "_PROJECT_DIR", "/")
    text, _kb = _open(gbot(), run_async, USER)
    assert launch.NO_FOLDER_AT_ALL in text
    assert "New folder" not in text.split("📁 no project folder yet")[1]


def test_the_owner_card_is_unchanged(gbot, run_async, daemon_in_home):
    for chat in (DM, GROUP):
        text, _kb = _open(gbot(), run_async, OWNER, chat)
        assert "project folder" not in text
        assert new_flow._short_path(str(daemon_in_home)) in text


def test_the_folder_list_per_role(gbot, run_async, daemon_in_home, project):
    home = daemon_in_home
    reg = _registry(project, home)
    for uid, offers_home in ((OWNER, True), (ADMIN, True), (USER, False)):
        bot = gbot(reg)
        _open(bot, run_async, uid)
        _tap(bot, run_async, uid, "nw:opt:path")
        _text, kb = _last_edit(bot)
        labels = [b.removesuffix(" ✅") for b in _buttons(kb)]
        assert new_flow._short_path(str(project)) in labels
        assert (new_flow._short_path(str(home)) in labels) is offers_home
        assert any(b.startswith("Default folder") for b in labels) is offers_home
        assert "➕ New folder" in labels
        options = bot._new_wizard_pending[(GROUP, uid)]["path_options"]
        assert (str(home) in options) is offers_home


def test_a_user_tap_on_the_default_folder_is_refused(gbot, run_async, daemon_in_home):
    bot = gbot()
    _open(bot, run_async, USER)
    query = _tap(bot, run_async, USER, "nw:path:default")
    assert query.answer.await_count == 1
    assert query.answer.await_args.args[0] == launch.WIDE_FOLDER_REFUSAL
    assert query.answer.await_args.kwargs.get("show_alert") is True


def test_a_user_makes_a_project_folder_under_home_and_starts_there(
        gbot, run_async, mk_update, daemon_in_home):
    home = daemon_in_home
    bot = gbot()
    _open(bot, run_async, USER)
    _tap(bot, run_async, USER, "nw:path:new")
    pending = bot._new_wizard_pending[(GROUP, USER)]
    assert pending["step"] == "opt_path_newfolder"
    assert pending["new_folder_parent"] == str(home)

    update = mk_update("myproj", chat_id=GROUP, user_id=USER)
    run_async(new_flow.maybe_handle_text(bot, update, MagicMock(), "myproj"))
    assert (home / "myproj").is_dir()
    assert pending["cwd"] == str(home / "myproj")
    text, _kb = _last_edit(bot)
    assert "project folder" not in text
    assert str(home / "myproj") in launch.allowed_roots(bot.registry, GROUP,
                                                        confined=True)


def test_a_user_new_folder_on_the_card_is_never_an_existing_home_folder(
        gbot, run_async, mk_update, daemon_in_home):
    home = daemon_in_home
    (home / "Documents").mkdir()
    bot = gbot()
    _open(bot, run_async, USER)
    _tap(bot, run_async, USER, "nw:path:new")
    update = mk_update("Documents", chat_id=GROUP, user_id=USER)
    run_async(new_flow.maybe_handle_text(bot, update, MagicMock(), "Documents"))
    pending = bot._new_wizard_pending[(GROUP, USER)]
    assert pending["cwd"] is None
    assert pending["step"] == "opt_path_newfolder"
    assert "already exists" in _last_edit(bot)[0]


def test_a_stored_default_of_home_is_ignored_for_a_user_only(gbot, daemon_in_home, project):
    home = daemon_in_home
    bot = gbot(_registry(project))
    preferences.set_new_session_default(GROUP, "cwd", str(home))
    assert new_flow.resolve_new_session_settings(bot, GROUP, USER)["cwd"] is None
    assert new_flow.resolve_new_session_settings(bot, GROUP, OWNER)["cwd"] == str(home)
    assert new_flow.resolve_new_session_settings(bot, GROUP, ADMIN)["cwd"] == str(home)
    preferences.set_new_session_default(GROUP, "cwd", str(project))
    assert new_flow.resolve_new_session_settings(bot, GROUP, USER)["cwd"] == str(project)


def test_a_user_defaults_screen_does_not_list_home(gbot, daemon_in_home, project):
    bot = gbot(_registry(project))
    home = new_flow._short_path(str(daemon_in_home))
    proj = new_flow._short_path(str(project))

    def labels(viewer):
        _text, kb = new_flow.render_new_session_defaults(
            bot, GROUP, view="cwd", viewer=viewer)
        return [b.removesuffix(" ✅") for b in _buttons(kb)]
    assert home not in labels(USER) and proj in labels(USER)
    assert home in labels(OWNER) and proj in labels(OWNER)


# ---- the one launch seam ------------------------------------------------------

@pytest.fixture
def no_launch(monkeypatch):
    launched = AsyncMock(return_value=(False, "stub launch"))
    monkeypatch.setattr(inject, "launch_session", launched)
    return launched


@pytest.mark.parametrize("cwd", [None, "home", "/"])
def test_create_session_refuses_a_user_in_a_wide_folder(
        gbot, run_async, daemon_in_home, no_launch, cwd):
    folder = {None: None, "home": str(daemon_in_home), "/": "/"}[cwd]
    bot = gbot()
    name, err = run_async(bot.create_session(
        "x", scope_chat_id=GROUP, cwd=folder, driver_user_id=USER))
    assert (name, err) == ("", launch.WIDE_FOLDER_REFUSAL)
    no_launch.assert_not_awaited()


def test_create_session_lets_a_user_start_in_a_project(
        gbot, run_async, daemon_in_home, project, no_launch):
    bot = gbot()
    run_async(bot.create_session("x", scope_chat_id=GROUP, cwd=str(project),
                                 driver_user_id=USER))
    no_launch.assert_awaited_once()


@pytest.mark.parametrize("uid,chat", [(OWNER, DM), (OWNER, GROUP), (ADMIN, GROUP)])
def test_create_session_is_unchanged_for_owner_and_admin(
        gbot, run_async, daemon_in_home, no_launch, uid, chat):
    bot = gbot()
    run_async(bot.create_session("x", scope_chat_id=chat, cwd=None,
                                 driver_user_id=uid))
    no_launch.assert_awaited_once()


def test_create_session_is_unchanged_in_personal_mode(
        mk_bot, run_async, daemon_in_home, no_launch):
    bot = mk_bot(SessionRegistry())
    run_async(bot.create_session("x", scope_chat_id=DM, cwd=None,
                                 driver_user_id=None))
    no_launch.assert_awaited_once()


# ---- the Mini App ---------------------------------------------------------------

_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


def _init_data(user_id):
    import hashlib
    import hmac
    import time
    from urllib.parse import urlencode
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "T"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", _TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(fields)}


@pytest.fixture
def app(gbot, daemon_in_home, project, monkeypatch):
    """A Mini App for the group, the daemon in the home folder, one
    session already in ``~/proj``. ``create_session`` is the real one,
    with the launch itself faked."""
    from aipager.miniapp.server import MiniAppServer
    monkeypatch.setattr("aipager.config.BOT_TOKEN", _TOKEN)
    launched = AsyncMock(return_value=(False, "stub launch"))
    monkeypatch.setattr(inject, "launch_session", launched)
    reg = _registry(project)
    bot = gbot(reg)
    srv = MiniAppServer(bot, reg, port=8765)
    srv.launched = launched
    return srv


def _call(srv, run_async, method, path, uid, body=None):
    from aiohttp.test_utils import TestClient, TestServer

    async def _run():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            resp = await client.request(method, path, headers=_init_data(uid),
                                        json=body)
            return resp.status, await resp.json()
        finally:
            await client.close()
    return run_async(_run())


@pytest.mark.parametrize("cwd", ["", "home"])
def test_the_mini_app_refuses_a_user_session_in_home(app, run_async, daemon_in_home, cwd):
    folder = str(daemon_in_home) if cwd else ""
    status, body = _call(app, run_async, "POST", "/api/sessions", USER,
                         {"name": "dev", "cwd": folder})
    assert status == 400
    assert body["detail"] == launch.WIDE_FOLDER_REFUSAL
    app.launched.assert_not_awaited()


def test_the_mini_app_lets_a_user_start_in_a_project(app, run_async, project):
    _call(app, run_async, "POST", "/api/sessions", USER,
          {"name": "dev", "cwd": str(project)})
    app.launched.assert_awaited_once()
    assert app.launched.await_args.kwargs["cwd"] == str(project)


@pytest.mark.parametrize("cwd", ["", "home"])
def test_the_mini_app_is_unchanged_for_the_owner(app, run_async, daemon_in_home, cwd):
    folder = str(daemon_in_home) if cwd else ""
    _call(app, run_async, "POST", "/api/sessions", OWNER,
          {"name": "dev", "cwd": folder})
    app.launched.assert_awaited_once()


def test_a_user_creates_a_folder_in_home_from_the_mini_app_and_starts_there(
        app, run_async, daemon_in_home):
    home = daemon_in_home
    status, body = _call(app, run_async, "POST", "/api/directories", USER,
                         {"parent": str(home), "name": "myproj"})
    assert (status, body["path"]) == (200, str(home / "myproj"))
    _call(app, run_async, "POST", "/api/sessions", USER,
          {"name": "dev", "cwd": str(home / "myproj")})
    app.launched.assert_awaited_once()
    assert app.launched.await_args.kwargs["cwd"] == str(home / "myproj")


def test_the_mini_app_will_not_hand_a_user_an_existing_home_folder(
        app, run_async, daemon_in_home):
    home = daemon_in_home
    (home / "bin").mkdir()
    status, _body = _call(app, run_async, "POST", "/api/directories", USER,
                          {"parent": str(home), "name": "bin"})
    assert status == 400
    status, _body = _call(app, run_async, "POST", "/api/sessions", USER,
                          {"name": "dev", "cwd": str(home / "bin")})
    assert status == 400
    app.launched.assert_not_awaited()
    # The owner reuses it, as before.
    status, body = _call(app, run_async, "POST", "/api/directories", OWNER,
                         {"parent": str(home), "name": "bin"})
    assert (status, body["existed"]) == (200, True)


# ---- Replace keeps the old session when the new one may not start ---------------

def test_replace_is_refused_before_the_old_session_is_killed(
        gbot, run_async, daemon_in_home, monkeypatch):
    from aipager.state import Status
    killed = AsyncMock()
    monkeypatch.setattr(inject, "kill_session", killed)
    bot = gbot()
    sess = bot.registry.get_or_create("claude-x__g100")
    sess.label = "x"
    sess.scope_chat_id = GROUP
    sess.status = Status.IDLE
    bot._new_conflict_pending["claude-x__g100"] = {
        "user_id": USER, "msg_id": 0, "prompt": "", "cwd": None,
        "skip_perms": False, "model": None, "was_working": False}
    sess.tap_is_for_this_turn = lambda _m: True
    query = MagicMock()
    query.answer = AsyncMock()
    query.data = "claude-x__g100:new_replace"
    query.from_user = MagicMock(id=USER)
    query.message = MagicMock(message_id=900, chat=MagicMock(id=GROUP))
    update = MagicMock(callback_query=query)
    update.effective_user = MagicMock(id=USER)
    update.effective_chat = MagicMock(id=GROUP, type="supergroup")
    run_async(bot._handle_callback(update, None))
    killed.assert_not_awaited()
    texts = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert launch.WIDE_FOLDER_REFUSAL in texts


def test_a_user_cannot_store_home_as_the_default_through_a_stale_list(
        gbot, run_async, daemon_in_home, project):
    """The defaults screen's list was shown unfiltered (no viewer): a
    confined member's tap on the home folder in it is refused."""
    scopes = [Scope(chat_id=DM, kind="dm", label="bob",
                    members=(Member(id=USER, label="bob", role="user"),))]
    bot = gbot(_registry(project, chat=DM), scopes=scopes)
    new_flow.render_new_session_defaults(bot, DM, view="cwd")
    shown = new_flow._defaults_folders_shown(bot)[DM]
    idx = shown.index(str(daemon_in_home))
    query = _tap(bot, run_async, USER, f"set:ns:cwd:{idx}", chat=DM)
    assert preferences.get_new_session_defaults(DM).cwd == ""
    assert "no longer available" in query.answer.await_args.args[0]
    # The project in the same list is fine.
    _tap(bot, run_async, USER, f"set:ns:cwd:{shown.index(str(project))}", chat=DM)
    assert preferences.get_new_session_defaults(DM).cwd == str(project)
