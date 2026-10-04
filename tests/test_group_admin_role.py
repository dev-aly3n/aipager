"""Roadmap 8.83 (D-B): the built-in ``admin`` role is an admin.

Group audit A9 / C8 (probe p6 part a): ``_is_admin_user`` returned
``role.bypass_safety``, which only ``owner`` has, so an ``admin`` was told
"Auto mode needs an admin", got Ask for new sessions, and could not change
a group's ``/settings`` or run ``/update``, while ``/whoami`` said admin.

Operator decision 2026-10-04: an admin passes every gate an owner passes in
Telegram and the Mini App, through ``Role.can_manage``. Two things stay the
owner's: changing members and roles (only ``aipager config`` on the machine)
and bypassing the safety floor (``bypass_safety``).

Pinned here, all with the REAL built-in policy (``load_policy``):

- the built-ins: owner and admin manage, user and read_only do not;
  ``bypass_safety`` is still the owner's alone;
- ``policy.yaml`` may set ``can_manage`` per role, and is validated;
- the gate matrix in a group and in the owner's DM: ``_is_admin_user``,
  ``_is_update_admin``, ``/new``'s Auto default, ``/mode auto``,
  ``/update``, a group ``/settings`` write, ``/whoami``;
- an admin's protected-path read and write are still denied by the hook;
- resuming a session saved in Auto needs an admin (typed ``/resume``, the
  picker's buttons, the ``/new`` conflict card, the Mini App); anyone else
  gets Ask and is told so;
- no Telegram or Mini App code can write members or roles;
- the wizard offers owner for group members with a warning, and suggests
  the operator first;
- DM parity: the operator's own install (one DM, the owner) is unchanged.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import policy as policy_mod
from aipager import policy_snapshot as ps
from aipager import safety
from aipager.bot import new_flow, session_parity, update_flow
from aipager.bot.transport import RESUME_AUTO_NEEDS_ADMIN
from aipager.dtach import enforce, inject
from aipager.policy import PolicyError, load_policy
from aipager.scope import Member, Scope
from aipager.state import Status

GROUP = -1001
DM = 555
S = "claude-api__g1001"

ALY, BOB, RO, ADA = 1, 2, 3, 4      # owner, user, read_only, admin
ROLE_OF = {ALY: "owner", BOB: "user", RO: "read_only", ADA: "admin"}
MANAGERS = {ALY, ADA}

ASK_NOTE = f"in Ask: {RESUME_AUTO_NEEDS_ADMIN}"


def _builtin_policy():
    return load_policy(Path("/nonexistent/p.yaml"), Path("/nonexistent/p.d"))


def _group_scope():
    return Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ALY, label="aly", role="owner"),
        Member(id=BOB, label="bob", role="user"),
        Member(id=RO, label="ro", role="read_only"),
        Member(id=ADA, label="ada", role="admin"),
    ))


def _dm_scope():
    return Scope(chat_id=DM, kind="dm", label="aly DM",
                 members=(Member(id=ALY, label="aly", role="owner"),))


@pytest.fixture
def gbot(mk_bot):
    def _mk(scopes="group", policy=None):
        if scopes == "group":
            scopes = [_group_scope(), _dm_scope()]
        elif scopes == "dm":
            scopes = [_dm_scope()]
        bot = mk_bot(scopes=scopes)
        bot.policy = policy or _builtin_policy()
        bot._app.bot.id = 999
        bot._app.bot.delete_message = AsyncMock()
        bot._app.bot.edit_message_text = AsyncMock()
        for name in ("_card_for_injected", "_react", "_maybe_update_bot_name",
                     "_update_bot_commands", "_edit_busy_raw",
                     "_send_busy_and_animate"):
            setattr(bot, name, AsyncMock())
        bot._start_animation = MagicMock()
        bot._stop_animation = MagicMock()
        return bot
    return _mk


def _cmd(text, *, user_id, chat_id):
    u = MagicMock()
    u.effective_chat = MagicMock(id=chat_id,
                                 type="private" if chat_id > 0 else "supergroup")
    u.effective_user = MagicMock(id=user_id, username=f"u{user_id}",
                                 first_name="U", last_name="")
    m = MagicMock()
    m.text = text
    m.caption = None
    m.message_id = 400
    m.reply_to_message = None
    m.quote = None
    m.external_reply = None
    m.forward_origin = None
    m.via_bot = None
    m.media_group_id = None
    m.chat = u.effective_chat
    m.reply_text = AsyncMock(return_value=MagicMock(message_id=901))
    u.message = m
    u.effective_message = m
    u.callback_query = None
    return u


def _replies(u):
    return [c.args[0] if c.args else c.kwargs.get("text", "")
            for c in u.message.reply_text.await_args_list]


def _tap(chat_id, user_id, data, *, msg_id=50):
    q = MagicMock()
    q.data = data
    q.from_user = MagicMock(id=user_id)
    q.message = MagicMock()
    q.message.chat = MagicMock(id=chat_id)
    q.message.chat_id = chat_id
    q.message.message_id = msg_id
    q.message.text = "card"
    q.message.edit_text = AsyncMock()
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    u = MagicMock()
    u.callback_query = q
    u.effective_chat = q.message.chat
    u.effective_user = q.from_user
    u.message = None
    return u, q


def _answers(q):
    return [c.args[0] for c in q.answer.await_args_list if c.args]


def _edited(q):
    return [c.args[0] if c.args else c.kwargs.get("text", "")
            for c in q.edit_message_text.await_args_list]


def _session(bot, name=S, chat=GROUP, kind="group", label="api",
             status=Status.IDLE):
    s = bot.registry.get_or_create(name)
    s.label = label
    s.scope_chat_id, s.scope_kind = chat, kind
    s.status = status
    return s


@pytest.fixture
def launched(monkeypatch):
    seen: list = []

    async def _launch(short, **kw):
        seen.append((short, kw))
        return True, ""

    monkeypatch.setattr(inject, "launch_session", _launch)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr(inject, "list_sessions", AsyncMock(return_value=[]))
    return seen


# ===========================================================================
# The built-in roles
# ===========================================================================

def test_builtin_can_manage():
    pol = _builtin_policy()
    assert {n: r.can_manage for n, r in pol.roles.items()} == {
        "owner": True, "admin": True, "user": False, "read_only": False}


def test_bypass_safety_is_still_the_owners_alone():
    pol = _builtin_policy()
    assert {n: r.bypass_safety for n, r in pol.roles.items()} == {
        "owner": True, "admin": False, "user": False, "read_only": False}
    assert safety.BUILTIN_ROLE_DEFAULTS["admin"]["bypass_safety"] is False


def test_a_role_defaults_to_not_managing():
    assert policy_mod.Role(name="x").can_manage is False


# ===========================================================================
# policy.yaml
# ===========================================================================

def _write_policy(tmp_path, body: str):
    p = tmp_path / "policy.yaml"
    p.write_text(body, encoding="utf-8")
    return p, tmp_path / "policy.d"


def test_policy_yaml_sets_can_manage_per_role(tmp_path):
    p, d = _write_policy(tmp_path, (
        "roles:\n"
        "  lead:\n    can_manage: true\n"
        "  intern:\n    can_manage: false\n"
        "  admin:\n    can_manage: false\n"))
    try:
        pol = load_policy(p, d)
    except PolicyError as e:
        pytest.fail(f"can_manage refused as a role field: {e}")
    assert pol.get_role("lead").can_manage is True
    assert pol.get_role("intern").can_manage is False
    assert pol.get_role("admin").can_manage is False
    # Overriding can_manage never touches the safety floor bypass.
    assert pol.get_role("lead").bypass_safety is False
    assert pol.get_role("owner").can_manage is True


def test_a_role_defined_from_nothing_does_not_manage(tmp_path):
    p, d = _write_policy(tmp_path, "roles:\n  lead:\n    can_prompt: true\n")
    assert load_policy(p, d).get_role("lead").can_manage is False


def test_policy_validate_accepts_can_manage(tmp_path):
    p, d = _write_policy(tmp_path, "roles:\n  lead:\n    can_manage: true\n")
    scopes = [Scope(chat_id=GROUP, kind="group", label="team",
                    members=(Member(id=10, label="lee", role="lead"),))]
    assert policy_mod.validate_policy_files(scopes, p, d) == []
    p.write_text("roles:\n  lead:\n    can_manage: maybe\n")
    assert any("can_manage must be true/false" in x
               for x in policy_mod.validate_policy_files(scopes, p, d))


@pytest.mark.parametrize("val", ['"yes"', "1", "[true]"])
def test_policy_validate_refuses_a_non_bool_can_manage(tmp_path, val):
    p, d = _write_policy(tmp_path, f"roles:\n  lead:\n    can_manage: {val}\n")
    with pytest.raises(PolicyError, match="can_manage must be true/false"):
        load_policy(p, d)


def test_custom_roles_follow_can_manage(gbot, tmp_path):
    p, d = _write_policy(tmp_path, (
        "roles:\n"
        "  lead:\n    can_manage: true\n"
        "  intern:\n    can_manage: false\n"
        "  admin:\n    can_manage: false\n"))
    scope = Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=10, label="lee", role="lead"),
        Member(id=11, label="ian", role="intern"),
        Member(id=ADA, label="ada", role="admin"),
    ))
    bot = gbot(scopes=[scope], policy=load_policy(p, d))
    assert bot._is_admin_user(10, GROUP) is True
    assert bot._is_update_admin(10, GROUP) is True
    assert bot._is_admin_user(11, GROUP) is False
    assert bot._is_update_admin(11, GROUP) is False
    # policy.yaml can take it away from the built-in admin too.
    assert bot._is_admin_user(ADA, GROUP) is False


# ===========================================================================
# The gate matrix (real policy)
# ===========================================================================

@pytest.mark.parametrize("uid", [ALY, ADA, BOB, RO], ids=lambda u: ROLE_OF[u])
def test_admin_rules_in_a_group(gbot, uid):
    bot = gbot()
    assert bot._is_admin_user(uid, GROUP) is (uid in MANAGERS)
    assert bot._is_update_admin(uid, GROUP) is (uid in MANAGERS)


def test_a_non_member_is_never_an_admin(gbot):
    bot = gbot()
    assert bot._is_admin_user(77, GROUP) is False
    assert bot._is_admin_user(None, GROUP) is False
    assert bot._is_update_admin(77, GROUP) is False


def test_the_role_comes_from_the_chat_acted_in(gbot):
    """ada is admin in the group only: in the owner's DM she is no one."""
    bot = gbot()
    assert bot._is_admin_user(ADA, DM) is False


def test_p6a_admin_is_an_admin(gbot):
    """Probe p6 part a, before: ``False False`` and ``can_auto False``."""
    bot = gbot()
    assert bot._is_admin_user(ADA, GROUP) is True
    assert bot._is_update_admin(ADA, GROUP) is True
    st = new_flow.resolve_new_session_settings(bot, GROUP, ADA)
    assert st["can_auto"] is True and st["skip_perms"] is True


@pytest.mark.parametrize("uid", [BOB, RO], ids=lambda u: ROLE_OF[u])
def test_new_defaults_ask_for_others(gbot, uid):
    st = new_flow.resolve_new_session_settings(gbot(), GROUP, uid)
    assert st["can_auto"] is False and st["skip_perms"] is False


@pytest.mark.parametrize("uid", [ALY, ADA, BOB], ids=lambda u: ROLE_OF[u])
def test_mode_auto_typed(gbot, run_async, uid):
    bot = gbot()
    sess = _session(bot)
    u = _cmd("/mode api auto", user_id=uid, chat_id=GROUP)
    run_async(bot._handle_mode_cmd(u, MagicMock()))
    replies = _replies(u)
    assert replies, "no reply"
    if uid in MANAGERS:
        assert not any("needs an admin" in r for r in replies)
        # the confirm step, with its buttons
        assert u.message.reply_text.await_args_list[0].kwargs.get("reply_markup")
    else:
        assert replies == ["Auto mode needs an admin."]
    assert sess.skip_perms is False      # nothing switched without a confirm


@pytest.mark.parametrize("uid", [ALY, ADA, BOB, RO], ids=lambda u: ROLE_OF[u])
def test_update_typed(gbot, run_async, uid):
    bot = gbot()
    u = _cmd("/update", user_id=uid, chat_id=GROUP)
    run_async(update_flow.handle_update_cmd(bot, u, MagicMock()))
    replies = _replies(u)
    if uid == RO:
        # read_only is stopped at the door (no prompting), before the gate
        assert update_flow.CHECK_PROMPT_TEXT not in replies
    elif uid in MANAGERS:
        assert replies == [update_flow.CHECK_PROMPT_TEXT]
    else:
        assert replies == [update_flow.DENIED_TEXT]
        assert update_flow.DENIED_TEXT == "🚫 Only an admin can update aipager."


@pytest.mark.parametrize("uid", [ALY, ADA, BOB], ids=lambda u: ROLE_OF[u])
def test_group_settings_write(gbot, run_async, uid):
    from aipager import preferences
    bot = gbot()
    before = preferences.get_preferences(GROUP).layout
    u, q = _tap(GROUP, uid, "_:set:layout:merged")
    run_async(bot._handle_callback(u, MagicMock()))
    refused = "Only an admin can change settings in this group." in _answers(q)
    assert refused is (uid not in MANAGERS)
    after = preferences.get_preferences(GROUP).layout
    assert (after == "merged") is (uid in MANAGERS)
    assert before != "merged"


@pytest.mark.parametrize("uid", [ALY, ADA, BOB], ids=lambda u: ROLE_OF[u])
def test_whoami_shows_can_manage(gbot, run_async, uid):
    bot = gbot()
    u = _cmd("/whoami", user_id=uid, chat_id=GROUP)
    run_async(bot._handle_whoami(u, MagicMock()))
    text = "\n".join(_replies(u))
    want = "yes" if uid in MANAGERS else "no"
    assert f"can_manage: {want}" in text
    assert f"bypass_safety: {'yes' if uid == ALY else 'no'}" in text


# ---- DM parity: the operator's own install ------------------------------

def test_dm_owner_is_unchanged(gbot):
    bot = gbot(scopes="dm")
    assert bot._is_admin_user(ALY, DM) is True
    assert bot._is_update_admin(ALY, DM) is True
    st = new_flow.resolve_new_session_settings(bot, DM, ALY)
    assert st["can_auto"] is True


def test_personal_mode_is_unchanged(mk_bot, monkeypatch):
    bot = mk_bot(scopes=None)
    assert bot._is_admin_user(12345, 12345) is True


# ===========================================================================
# The safety floor: still owner only
# ===========================================================================

@pytest.fixture
def floor(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "snapshot_path",
                        lambda n: tmp_path / f"{n}.policy.json")
    monkeypatch.delenv("CLAUDE_CODE_TMPDIR", raising=False)
    monkeypatch.delenv("TMPDIR", raising=False)
    project = tmp_path / "work" / "proj"
    project.mkdir(parents=True)
    tp = tmp_path / "t.jsonl"
    tp.write_text(json.dumps({"type": "user", "message": {
        "content": "[via Telegram · @ada · role:admin]\ndo it"}}) + "\n")

    def _decide(role_name, tool, tool_input):
        role = _builtin_policy().get_role(role_name)
        ps.write_merged_snapshot("claude-proj",
                                 ps.resolve_snapshot(role, None, None))
        return enforce.decide({
            "hook_event_name": "PreToolUse", "session": "claude-proj",
            "session_id": "5b1d7363-aaaa-bbbb-cccc-000000000001",
            "cwd": str(project), "tool_name": tool, "tool_input": tool_input,
            "transcript_path": str(tp),
        })
    return _decide


HOME = os.path.expanduser("~")


@pytest.mark.parametrize("tool,inp", [
    ("Read", {"file_path": f"{HOME}/.config/aipager/aipager.yaml"}),
    ("Write", {"file_path": f"{HOME}/.config/aipager/aipager.yaml",
               "content": "x"}),
    ("Read", {"file_path": f"{HOME}/.claude/.credentials.json"}),
])
def test_an_admins_protected_path_access_is_still_denied(floor, tool, inp):
    assert floor("admin", tool, inp)


def test_the_owner_still_bypasses_the_floor(floor):
    assert floor("owner", "Read",
                 {"file_path": f"{HOME}/.config/aipager/aipager.yaml"}) is None


# ===========================================================================
# Resume of a session saved in Auto needs an admin
# ===========================================================================

def _gone_auto(bot, name=S, chat=GROUP, kind="group"):
    s = _session(bot, name, chat, kind, status=Status.GONE)
    s.claude_session_id, s.cwd = "sid-1", "/srv/team"
    s.skip_perms = True
    s.last_assistant_preview = "where we left off"
    return s


@pytest.mark.parametrize("uid", [ALY, ADA, BOB], ids=lambda u: ROLE_OF[u])
def test_typed_resume_of_an_auto_session(gbot, run_async, launched, uid):
    bot = gbot()
    sess = _gone_auto(bot)
    u = _cmd("/resume api", user_id=uid, chat_id=GROUP)
    run_async(bot._handle_resume_cmd(u, MagicMock()))
    assert len(launched) == 1
    auto = uid in MANAGERS
    assert launched[0][1]["skip_perms"] is auto
    assert sess.skip_perms is auto
    text = "\n".join(_replies(u))
    assert "Resumed <b>api</b>" in text
    assert (ASK_NOTE in text) is (not auto)


def test_typed_resume_note_reads_plainly(gbot, run_async, launched):
    bot = gbot()
    _gone_auto(bot)
    u = _cmd("/resume api", user_id=BOB, chat_id=GROUP)
    run_async(bot._handle_resume_cmd(u, MagicMock()))
    first = _replies(u)[0].split("\n", 1)[0]
    assert first == "♻️ Resumed <b>api</b> in Ask: Auto needs an admin."


def test_typed_resume_of_an_ask_session_says_nothing_extra(
        gbot, run_async, launched):
    bot = gbot()
    sess = _gone_auto(bot)
    sess.skip_perms = False
    u = _cmd("/resume api", user_id=BOB, chat_id=GROUP)
    run_async(bot._handle_resume_cmd(u, MagicMock()))
    assert launched[0][1]["skip_perms"] is False
    assert ASK_NOTE not in "\n".join(_replies(u))


def test_dm_owner_typed_resume_is_unchanged(gbot, run_async, launched):
    bot = gbot(scopes="dm")
    sess = _gone_auto(bot, "claude-api__d555", DM, "dm")
    u = _cmd("/resume api", user_id=ALY, chat_id=DM)
    run_async(bot._handle_resume_cmd(u, MagicMock()))
    assert launched[0][1]["skip_perms"] is True and sess.skip_perms is True
    assert ASK_NOTE not in "\n".join(_replies(u))


@pytest.mark.parametrize("uid", [ALY, ADA, BOB], ids=lambda u: ROLE_OF[u])
def test_picker_shows_the_default_this_person_gets(gbot, run_async, uid):
    bot = gbot()
    sess = _gone_auto(bot)
    data = session_parity.session_cb(bot, GROUP, sess, "resume")
    u, q = _tap(GROUP, uid, data, msg_id=60)
    run_async(bot._handle_callback(u, MagicMock()))
    kb = q.edit_message_text.await_args.kwargs["reply_markup"]
    labels = [b.text for row in kb.inline_keyboard for b in row]
    if uid in MANAGERS:
        assert "🤖 Auto (default)" in labels
    else:
        assert "💬 Ask (default)" in labels


@pytest.mark.parametrize("uid", [ALY, ADA, BOB], ids=lambda u: ROLE_OF[u])
def test_picker_ask_resumes_in_ask_for_anyone(gbot, run_async, launched, uid):
    bot = gbot()
    sess = _gone_auto(bot)
    data = session_parity.session_cb(bot, GROUP, sess, "resume-ask")
    u, q = _tap(GROUP, uid, data, msg_id=60)
    run_async(bot._handle_callback(u, MagicMock()))
    assert launched[0][1]["skip_perms"] is False
    assert not any(ASK_NOTE in t for t in _edited(q))


@pytest.mark.parametrize("uid", [ALY, ADA, BOB], ids=lambda u: ROLE_OF[u])
def test_picker_auto(gbot, run_async, launched, uid):
    bot = gbot()
    sess = _gone_auto(bot)
    sess.skip_perms = False
    data = session_parity.session_cb(bot, GROUP, sess, "resume-auto")
    u, q = _tap(GROUP, uid, data, msg_id=60)
    run_async(bot._handle_callback(u, MagicMock()))
    if uid in MANAGERS:
        assert launched[0][1]["skip_perms"] is True
    else:
        assert launched == []           # refused at the tap gate


@pytest.mark.parametrize("uid", [ALY, ADA, BOB], ids=lambda u: ROLE_OF[u])
def test_legacy_resume_mode_auto(gbot, run_async, launched, uid):
    bot = gbot()
    _gone_auto(bot)
    u, q = _tap(GROUP, uid, f"{S}:resume_mode_auto", msg_id=60)
    run_async(bot._handle_callback(u, MagicMock()))
    if uid in MANAGERS:
        assert launched[0][1]["skip_perms"] is True
    else:
        assert launched == []


def test_resume_gate_itself_refuses_auto_past_the_tap_gate(gbot, run_async,
                                                           launched):
    """Defence in depth: a caller that asks ``_do_resume`` for Auto on
    behalf of a non-admin (no tap gate in front) still gets Ask."""
    bot = gbot()
    sess = _gone_auto(bot)
    sess.skip_perms = False
    u = _cmd("/x", user_id=BOB, chat_id=GROUP)
    out: list = []

    async def _reply(text, **kw):
        out.append(text)
    run_async(bot._do_resume(label="api", reply_fn=_reply, update=u,
                             sess=sess, skip_perms_override=True))
    assert launched[0][1]["skip_perms"] is False
    assert ASK_NOTE in out[0]


def test_resume_gate_reads_the_tapper_when_there_is_no_update(
        gbot, run_async, launched):
    bot = gbot()
    sess = _gone_auto(bot)
    q = MagicMock()
    q.from_user = MagicMock(id=ADA)
    q.effective_chat = None         # a CallbackQuery has none
    q.message = MagicMock()
    q.message.chat = MagicMock(id=GROUP)
    q.message.chat_id = GROUP

    async def _reply(text, **kw):
        pass
    run_async(bot._do_resume(label="api", reply_fn=_reply, query=q, sess=sess))
    assert launched[0][1]["skip_perms"] is True


def _conflict(bot, author, skip_perms=False):
    bot._new_conflict_pending[S] = {"user_id": author, "prompt": "",
                                    "skip_perms": skip_perms, "msg_id": 0}


@pytest.mark.parametrize("uid", [ALY, ADA, BOB], ids=lambda u: ROLE_OF[u])
def test_conflict_card_resume_of_an_auto_session(gbot, run_async, launched, uid):
    bot = gbot()
    sess = _gone_auto(bot)
    _conflict(bot, uid)
    u, q = _tap(GROUP, uid, f"{S}:new_resume", msg_id=70)
    run_async(bot._handle_callback(u, MagicMock()))
    auto = uid in MANAGERS
    assert len(launched) == 1
    assert launched[0][1]["skip_perms"] is auto
    assert sess.skip_perms is auto
    assert any(ASK_NOTE in t for t in _edited(q)) is (not auto)


# ---- the Mini App's resume ----------------------------------------------

@pytest.mark.parametrize("uid", [ALY, ADA, BOB], ids=lambda u: ROLE_OF[u])
def test_miniapp_resume_of_an_auto_session(gbot, run_async, launched, uid):
    from aipager.miniapp.server import MiniAppServer
    bot = gbot()
    sess = _gone_auto(bot)
    srv = MiniAppServer(bot, bot.registry, port=8765)
    srv._resolve_own_scope_session = AsyncMock(return_value=(sess, GROUP, uid))
    srv._allow_write = lambda _uid: True
    resp = run_async(srv._handle_session_resume(MagicMock()))
    body = json.loads(resp.body)
    auto = uid in MANAGERS
    assert launched[0][1]["skip_perms"] is auto
    assert body["status"] == "resumed"
    if auto:
        assert "detail" not in body
    else:
        assert body["mode"] == "ask"
        assert body["detail"] == f"Resumed api {ASK_NOTE}"
    mirrored = bot._app.bot.send_message.await_args.kwargs["text"]
    assert (ASK_NOTE in mirrored) is (not auto)


# ===========================================================================
# Nothing in Telegram or the Mini App changes members or roles
# ===========================================================================

RUNTIME_DIRS = ("bot", "miniapp", "dtach")
# The `aipager miniapp` command, run on the machine (not a Mini App route).
MACHINE_SIDE = {"miniapp/cli.py"}
# aipager.yaml (scope, wizard) and the legacy team.yaml (team) writers.
SCOPE_WRITERS = {"dump_scopes", "commit_scope", "replace_scopes",
                 "remove_scope", "_atomic_write_yaml", "dump_team",
                 "archive_team"}


def _runtime_files():
    root = Path(policy_mod.__file__).resolve().parent
    for d in RUNTIME_DIRS:
        for f in sorted((root / d).rglob("*.py")):
            rel = f.relative_to(root).as_posix()
            if rel not in MACHINE_SIDE:
                yield rel, f


def test_the_scan_sees_the_runtime_modules():
    rels = {rel for rel, _ in _runtime_files()}
    assert {"bot/callbacks.py", "bot/handlers.py", "miniapp/server.py",
            "dtach/notify_hook.py"} <= rels


@pytest.mark.parametrize("rel,path", list(_runtime_files()),
                         ids=lambda x: x if isinstance(x, str) else "")
def test_no_telegram_or_miniapp_code_writes_members_or_roles(rel, path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, "module", None) or ""
            names = {a.name for a in node.names}
            assert not mod.startswith("aipager.wizard"), (rel, mod)
            assert not any(n.startswith("aipager.wizard") for n in names), rel
            assert not (names & SCOPE_WRITERS), (rel, names & SCOPE_WRITERS)
        elif isinstance(node, ast.Attribute):
            assert node.attr not in SCOPE_WRITERS, (rel, node.attr)
        elif isinstance(node, ast.Name):
            assert node.id not in SCOPE_WRITERS, (rel, node.id)


def test_the_writer_names_exist():
    """A rename would make the scan pass for nothing."""
    from aipager import scope, team
    from aipager.wizard import scope_io
    for mod, names in ((scope, ("dump_scopes", "_atomic_write_yaml")),
                       (team, ("dump_team", "archive_team")),
                       (scope_io, ("commit_scope", "replace_scopes",
                                   "remove_scope"))):
        for n in names:
            assert callable(getattr(mod, n)), n


def test_the_scan_would_see_a_writer(tmp_path):
    """The check above is not vacuous: the patterns it rejects are the
    ones a writer would use."""
    src = ("from aipager.scope import dump_scopes\n"
           "import aipager.scope as s\ns.dump_scopes([], '', None)\n")
    tree = ast.parse(src)
    hits = [n for n in ast.walk(tree)
            if (isinstance(n, ast.ImportFrom)
                and {a.name for a in n.names} & SCOPE_WRITERS)
            or (isinstance(n, ast.Attribute) and n.attr in SCOPE_WRITERS)]
    assert len(hits) == 2


# ===========================================================================
# The wizard
# ===========================================================================

@pytest.fixture
def wizard_env(tmp_path, monkeypatch):
    from aipager import scope as _scope
    from aipager.wizard import draft as draft_mod
    monkeypatch.setattr(_scope, "CONFIG_PATH", tmp_path / "aipager.yaml")
    monkeypatch.setattr(draft_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(draft_mod, "DRAFT_PATH", tmp_path / ".wizard-draft.json")
    monkeypatch.setattr(policy_mod, "load_policy",
                        lambda *a, **k: _builtin_policy())
    return tmp_path


def _wizard_stubs(monkeypatch, answers, captures):
    from aipager.wizard import first_run, scope_flows, team_setup
    queue = iter(answers)
    asked: list = []

    def _ask(prompt):
        asked.append(prompt)
        try:
            return next(queue)
        except StopIteration:
            raise KeyboardInterrupt("ran out of canned answers")
    monkeypatch.setattr(scope_flows, "_ask", _ask)
    it = iter(captures)
    monkeypatch.setattr(team_setup, "_capture_user_identity",
                        lambda *a, **k: next(it))
    monkeypatch.setattr(first_run, "_step_chat_id", lambda *a, **k: -100)
    monkeypatch.setattr(team_setup, "_collect_deny_tools", lambda: [])
    return asked


def test_wizard_group_role_picker_offers_owner_with_a_warning(
        wizard_env, monkeypatch):
    from aipager.wizard import scope_flows
    warned: list = []
    monkeypatch.setattr(scope_flows, "warn", lambda *lines: warned.extend(lines))
    seen: dict = {}

    def _select(prompt, choices, **kw):
        seen["values"] = [c.value for c in choices]
        return MagicMock()
    monkeypatch.setattr(scope_flows.questionary, "select", _select)
    monkeypatch.setattr(scope_flows, "_ask", lambda prompt: "owner")
    assert scope_flows._pick_role("Role for @x:", warn_owner=True) == "owner"
    assert seen["values"][:4] == ["owner", "admin", "user", "read_only"]
    assert warned == [scope_flows.OWNER_WARNING]
    assert "full control of this machine" in scope_flows.OWNER_WARNING
    assert "—" not in scope_flows.OWNER_WARNING


def test_wizard_no_warning_without_the_ask(wizard_env, monkeypatch):
    from aipager.wizard import scope_flows
    warned: list = []
    monkeypatch.setattr(scope_flows, "warn", lambda *lines: warned.extend(lines))
    monkeypatch.setattr(scope_flows.questionary, "select",
                        lambda *a, **k: MagicMock())
    monkeypatch.setattr(scope_flows, "_ask", lambda prompt: "user")
    scope_flows._pick_role("Role for @x in their DM:")
    assert warned == []


def test_wizard_add_group_member_picker_is_asked_with_owner(
        wizard_env, monkeypatch):
    from aipager.wizard import scope_flows
    calls: list = []

    def _pick(prompt, **kw):
        calls.append(kw)
        return "owner"
    monkeypatch.setattr(scope_flows, "_pick_role", _pick)
    # no owner DM yet: no suggestion; label, then "add another?" False
    _wizard_stubs(monkeypatch, ["dev-team", False], [{"id": 11, "label": "ann"}])
    assert scope_flows.add_group_scope("TOK", "bot") is True
    assert calls[0].get("include_owner", True) is True
    assert calls[0].get("warn_owner") is True


def _seed_owner_dm():
    from aipager import scope as _scope
    _scope.dump_scopes(
        [Scope(chat_id=1, kind="dm", label="aly DM",
               members=(Member(id=1, label="aly", role="owner"),))],
        "TOK", _scope.CONFIG_PATH)


def test_wizard_suggests_the_operator_first_as_owner(wizard_env, monkeypatch):
    from aipager import scope as _scope
    from aipager.wizard import scope_flows
    _seed_owner_dm()
    monkeypatch.setattr(scope_flows, "_pick_role", lambda *a, **k: "user")
    # label, "add yourself?" yes, "add another?" yes, then ann,
    # "add another?" no
    _wizard_stubs(monkeypatch, ["dev-team", True, True, False],
                  [{"id": 11, "label": "ann"}])
    assert scope_flows.add_group_scope("TOK", "bot") is True
    scopes, _ = _scope.load_scopes(_scope.CONFIG_PATH)
    grp = next(s for s in scopes if s.chat_id == -100)
    assert [(m.id, m.label, m.role) for m in grp.members] == [
        (1, "aly", "owner"), (11, "ann", "user")]


def test_wizard_operator_suggestion_is_skippable(wizard_env, monkeypatch):
    from aipager import scope as _scope
    from aipager.wizard import scope_flows
    _seed_owner_dm()
    monkeypatch.setattr(scope_flows, "_pick_role", lambda *a, **k: "user")
    _wizard_stubs(monkeypatch, ["dev-team", False, False],
                  [{"id": 11, "label": "ann"}])
    assert scope_flows.add_group_scope("TOK", "bot") is True
    scopes, _ = _scope.load_scopes(_scope.CONFIG_PATH)
    grp = next(s for s in scopes if s.chat_id == -100)
    assert [m.label for m in grp.members] == ["ann"]


def test_wizard_operator_alone_is_enough(wizard_env, monkeypatch):
    from aipager import scope as _scope
    from aipager.wizard import scope_flows
    _seed_owner_dm()
    _wizard_stubs(monkeypatch, ["dev-team", True, False], [])
    assert scope_flows.add_group_scope("TOK", "bot") is True
    scopes, _ = _scope.load_scopes(_scope.CONFIG_PATH)
    grp = next(s for s in scopes if s.chat_id == -100)
    assert [(m.label, m.role) for m in grp.members] == [("aly", "owner")]


def test_wizard_operator_is_the_owner_dms_member_only(wizard_env, monkeypatch):
    """A DM whose member is not an owner is not the operator's."""
    from aipager import scope as _scope
    from aipager.wizard import scope_flows
    _scope.dump_scopes(
        [Scope(chat_id=9, kind="dm", label="bob DM",
               members=(Member(id=9, label="bob", role="admin"),))],
        "TOK", _scope.CONFIG_PATH)
    assert scope_flows._operator_member() is None
    _seed_owner_dm()
    assert scope_flows._operator_member().id == 1


def test_wizard_resume_does_not_suggest_again(wizard_env, monkeypatch):
    from aipager import scope as _scope
    from aipager.wizard import draft as draft_mod
    from aipager.wizard import scope_flows
    _seed_owner_dm()
    draft_mod.save_draft({"kind": "group", "chat_id": -100, "label": "dev",
                          "members": [{"id": 11, "label": "ann",
                                       "role": "user"}]})
    asked = _wizard_stubs(monkeypatch, ["resume"], [None])
    scope_flows.resume_or_discard_draft("TOK", "bot")
    assert len(asked) == 1
    scopes, _ = _scope.load_scopes(_scope.CONFIG_PATH)
    grp = next(s for s in scopes if s.chat_id == -100)
    assert [m.label for m in grp.members] == ["ann"]


def test_wizard_edit_member_warns_for_a_group(wizard_env, monkeypatch):
    from aipager.wizard import edit_menu
    calls: list = []

    def _pick(prompt, **kw):
        calls.append(kw)
        return "admin"
    monkeypatch.setattr(edit_menu, "_pick_role", _pick)
    monkeypatch.setattr(edit_menu, "commit_scope", lambda *a, **k: None)
    answers = iter([11, "role"])
    monkeypatch.setattr(edit_menu, "_ask", lambda p: next(answers))
    grp = Scope(chat_id=-100, kind="group", label="dev",
                members=(Member(id=11, label="ann", role="user"),))
    assert edit_menu._edit_member(grp, "TOK") is True
    assert calls[0]["warn_owner"] is True
