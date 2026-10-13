"""Every phase 3a surface of the multilingual design (conversation and
sessions), registered into the one list of ``tests/i18n_surfaces.py``.

Each entry drives one surface through a public entry point (a command
handler, a tap through the callback dispatcher, ``notify``, or a renderer
called without a ``Tr``) with fixed fake sessions, and returns every text,
pop-up and button label it produced, in the order they went out. The
language comes from what is STORED for the chat, never from an explicit
``Tr``, so a surface that forgets to resolve its chat's language shows
English and fails the per-language tests.

The same function runs on the base the English snapshot was recorded on
(before any phase 3a migration): no surface uses an argument that exists
only after migration. The four uses are the shared module's: the English
snapshot, the pseudo sweep, the Persian and Russian render tests, and
check 12. ``PENDING`` names the surfaces whose migration has not landed;
a surface leaves it with the commit that migrates it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from tests import i18n_surfaces as _shared
from tests.i18n_surfaces import NOW

#: A private chat (its id is its person's id) and a group, as in
#: ``tests/i18n_ml2.py``: aly owns both, bob is a member of the group.
DM = 555
GROUP = -1001
OWNER = 555
BOB = 2
BOT_NAME = "aipager_test_bot"

#: Words a surface shows that are names, not text (role and scope names,
#: what a test chose), by surface: the pseudo sweep skips them there only.
KNOWN_EXTRA: dict[str, tuple[str, ...]] = {
    "whoami.replies": ("team", "owner", "user", "developer", "admin"),
}


# ---- one surface's patches -----------------------------------------------------------------

_MISSING = object()


class _Scoped:
    """The monkeypatch a surface sees: every patch it makes is put back
    when the surface ends (by patching the original value back through the
    test's own monkeypatch, which then restores everything at teardown;
    never ``undo``). So no surface depends on what an earlier one patched,
    and a sweep over a subset renders exactly what the full run does."""

    def __init__(self, mp):
        self._mp = mp
        self._saved: list = []

    def setattr(self, target, name, value=_MISSING, raising=True):
        if value is _MISSING:             # ("module.attr", value)
            import importlib
            path, value = target, name
            module, name = path.rsplit(".", 1)
            target = importlib.import_module(module)
        self._saved.append(("attr", target, name, getattr(target, name, _MISSING)))
        self._mp.setattr(target, name, value, raising=raising)

    def setitem(self, mapping, key, value):
        self._saved.append(("item", mapping, key, mapping.get(key, _MISSING)))
        self._mp.setitem(mapping, key, value)

    def setenv(self, name, value):
        import os
        self._saved.append(("env", None, name, os.environ.get(name, _MISSING)))
        self._mp.setenv(name, value)

    def restore(self) -> None:
        for kind, target, key, old in reversed(self._saved):
            if kind == "attr":
                if old is _MISSING:
                    self._mp.delattr(target, key, raising=False)
                else:
                    self._mp.setattr(target, key, old)
            elif kind == "item":
                if old is _MISSING:
                    self._mp.delitem(target, key, raising=False)
                else:
                    self._mp.setitem(target, key, old)
            elif old is _MISSING:
                self._mp.delenv(key, raising=False)
            else:
                self._mp.setenv(key, old)
        self._saved.clear()


def surface(name):
    """``i18n_surfaces.surface`` with the patches scoped to the surface."""
    def deco(fn):
        def run(env):
            real = env.mp
            env.mp = _Scoped(real)
            try:
                return fn(env)
            finally:
                env.mp.restore()
                env.mp = real
        run.__name__ = fn.__name__
        run.__doc__ = fn.__doc__
        _shared.surface(name)(run)
        return fn
    return deco


# ---- a recording Telegram ---------------------------------------------------------

def _labels(kb) -> list[str]:
    if kb is None:
        return []
    rows = getattr(kb, "inline_keyboard", None)
    if rows is None:
        rows = getattr(kb, "keyboard", None) or []
    return [str(b.text) for row in rows for b in row]


class Tg:
    """Every text a surface sent, edited or popped up, in call order, with
    the labels of the buttons that came with it."""

    def __init__(self):
        self.out: list[str] = []
        self._next = 7000

    def note(self, text, kb=None) -> None:
        if text is not None and text != "":
            self.out.append(str(text))
        self.out += _labels(kb)

    def message(self, chat):
        """A sent message: edits of it are recorded too."""
        self._next += 1
        msg = MagicMock()
        msg.message_id = self._next
        msg.chat_id = chat
        msg.chat = MagicMock(id=chat, type="private" if chat > 0 else "supergroup")

        async def _edit(*args, **kwargs):
            self.note(args[0] if args else kwargs.get("text"), kwargs.get("reply_markup"))
            return msg
        msg.edit_text = AsyncMock(side_effect=_edit)
        return msg

    def replier(self, chat):
        async def _reply(*args, **kwargs):
            self.note(args[0] if args else kwargs.get("text"), kwargs.get("reply_markup"))
            return self.message(chat)
        return AsyncMock(side_effect=_reply)

    def documenter(self, chat):
        async def _doc(*args, **kwargs):
            self.note(kwargs.get("caption"))
            return self.message(chat)
        return AsyncMock(side_effect=_doc)

    def wire(self, bot) -> None:
        """Every call the bot makes through its own Telegram client."""
        tg = bot._app.bot

        async def _send(*args, **kwargs):
            chat = kwargs.get("chat_id", args[0] if args else None)
            text = kwargs.get("text", args[1] if len(args) > 1 else None)
            self.note(text, kwargs.get("reply_markup"))
            return self.message(chat if isinstance(chat, int) else DM)

        async def _edit(*args, **kwargs):
            self.note(kwargs.get("text", args[0] if args else None),
                      kwargs.get("reply_markup"))
            return True

        async def _doc(*args, **kwargs):
            self.note(kwargs.get("caption"))
            return self.message(DM)

        tg.send_message = AsyncMock(side_effect=_send)
        tg.edit_message_text = AsyncMock(side_effect=_edit)
        tg.send_document = AsyncMock(side_effect=_doc)
        tg.delete_message = AsyncMock(return_value=True)
        tg.edit_message_reply_markup = AsyncMock(return_value=True)
        tg.set_message_reaction = AsyncMock(return_value=True)


def world(env, *, scopes: bool = True, chats=(DM, GROUP), username=BOT_NAME):
    """A bot in scope mode (aly's DM and the group) with every Telegram call
    recorded, the language of this env stored for *chats*."""
    from tests import i18n_ml2
    env.store(*chats)
    bot = i18n_ml2.make_bot(env.mk_bot) if scopes else env.mk_bot()
    bot._app.bot.id = 999
    bot._app.bot.username = username
    tg = Tg()
    tg.wire(bot)
    for name in ("_maybe_update_bot_name", "_update_bot_commands", "_send_keyboard",
                 "_react", "_card_for_injected", "_send_busy_and_animate"):
        setattr(bot, name, AsyncMock())
    bot._start_animation = MagicMock()
    bot._stop_animation = MagicMock()
    bot._miniapp_url = ""
    alive(env)
    return bot, tg


def session(bot, label="x1", *, chat=DM, status=None, name=None):
    from aipager.state import Status
    s = bot.registry.get_or_create(name or f"claude-{label}__{'d' if chat > 0 else 'g'}{abs(chat)}")
    s.label = label
    s.scope_chat_id, s.scope_kind = chat, ("dm" if chat > 0 else "group")
    s.status = status if status is not None else Status.IDLE
    return s


def msg(tg, text, *, chat=DM, user=OWNER, reply_to=None, entities=None):
    """An incoming message whose replies (and their edits) are recorded."""
    update = MagicMock()
    m = update.message = MagicMock()
    m.text = text
    m.caption = None
    m.message_id = 900
    m.reply_text = tg.replier(chat)
    m.reply_document = tg.documenter(chat)
    m.reply_to_message = reply_to
    m.quote = None
    m.external_reply = None
    m.forward_origin = None
    m.via_bot = None
    m.media_group_id = None
    m.voice = None
    m.photo = None
    m.document = None
    m.sender_chat = None
    m.is_automatic_forward = False
    m.chat_id = chat
    m.chat = MagicMock(id=chat, type="private" if chat > 0 else "supergroup")
    m.parse_entities = lambda types=None: dict(entities or {})
    m.parse_caption_entities = lambda types=None: {}
    m.from_user = MagicMock(id=user, language_code=None, is_bot=False)
    update.effective_user = m.from_user
    update.effective_user.username = "aly" if user == OWNER else "bob"
    update.effective_chat = m.chat
    update.callback_query = None
    return update


def query(tg, data, *, chat=DM, user=OWNER, message_id=42, text=""):
    """A tap: its pop-up and the edits of the tapped message are recorded."""
    q = MagicMock()
    q.data = data

    async def _answer(*args, **kwargs):
        tg.note(args[0] if args else kwargs.get("text"))
        return True
    q.answer = AsyncMock(side_effect=_answer)

    async def _edit(*args, **kwargs):
        tg.note(args[0] if args else kwargs.get("text"), kwargs.get("reply_markup"))
        return True
    q.edit_message_text = AsyncMock(side_effect=_edit)
    q.edit_message_reply_markup = AsyncMock(return_value=True)
    q.message = tg.message(chat)
    q.message.message_id = message_id
    q.message.text = text
    q.message.reply_text = tg.replier(chat)
    q.message.reply_document = tg.documenter(chat)
    q.message.reply_markup = None
    q.from_user = MagicMock(id=user, language_code=None, is_bot=False)
    q.from_user.username = "aly" if user == OWNER else "bob"
    update = MagicMock()
    update.callback_query = q
    update.message = None
    update.effective_user = q.from_user
    update.effective_chat = q.message.chat
    return update, q


def _reply_to(message_id):
    """The message a reply answers (only its id matters here)."""
    return MagicMock(message_id=message_id, text="", caption=None)


def tap(env, bot, tg, data, **kw):
    update, q = query(tg, data, **kw)
    env.run(bot._handle_callback(update, MagicMock()))
    return q


def cmd(env, bot, tg, handler: str, text: str, **kw):
    """A command message through its real handler."""
    update = msg(tg, text, **kw)
    env.run(getattr(bot, handler)(update, MagicMock()))
    return update


def alive(env, value=True):
    from aipager.dtach import inject
    env.mp.setattr(inject, "is_alive", AsyncMock(return_value=value))
    env.mp.setattr(inject, "send_keys", AsyncMock(return_value=True))
    env.mp.setattr(inject, "list_sessions", AsyncMock(return_value=[]))


def _sess_cb(sess, verb):
    return f"{sess.name}:{verb}"


# ---- safety notices ------------------------------------------------------------------

def safety_reasons(tmp: Path | None = None) -> list[tuple[str, str]]:
    """``(tool, reason)`` for every shape safety.py and enforce write, each
    produced by its real writer (crafted policies, nothing on disk)."""
    from aipager import safety
    out: list[tuple[str, str]] = []

    def add(tool, reason):
        assert reason, tool
        out.append((tool, reason))

    no = ("/srv/secret/**",)
    add("Bash", safety.bash_violation("claude --resume x", (r"\bclaude\b",)))
    add("Bash", safety.bash_violation("grep -r x ~", safety.DENY_BASH_SEARCH_PATTERNS))
    add("WebSearch", safety.tool_violation("WebSearch", (), ("Read",)))
    add("Bash", safety.tool_violation("Bash", ("Bash",), ()))
    add("Read", safety.path_violation("Read", {"file_path": 7}, no, ()))
    add("Read", safety.path_violation("Read", {"file_path": "/srv/secret/a.txt"}, no, ()))
    add("Write", safety.path_violation("Write", {"file_path": "/srv/keep/a.txt"}, (),
                                        ("/srv/keep/**",)))
    add("Write", safety.path_violation("Write", {"file_path": "/w/p/.git/config"}, (), (),
                                        cwd="/w/p", write_roots=("/w/p",)))
    add("Write", safety.path_violation("Write", {"file_path": "/elsewhere/a"}, (), (),
                                        cwd="/w/p", write_roots=("/w/p",)))
    add("Write", safety.path_violation("Write", {"file_path": "/elsewhere/a"}, (), (),
                                        cwd="/w/p", write_roots=("/w/s",), wide_cwd=True))
    add("Grep", safety.path_violation("Grep", {"path": "/elsewhere"}, (), (),
                                       cwd="/w/p", write_roots=("/w/p",)))
    add("Grep", safety.path_violation("Grep", {"path": "/elsewhere"}, (), (),
                                       cwd="/w/p", write_roots=("/w/s",), wide_cwd=True))
    add("Grep", safety.path_violation("Grep", {"path": 3}, (), (),
                                       cwd="/w/p", write_roots=("/w/p",)))
    add("Grep", safety.path_violation("Grep", {"path": "/w/p/sub"}, ("/w/p/sub/**",), (),
                                       cwd="/w/p", write_roots=("/w/p",)))
    add("Grep", safety.path_violation("Grep", {"path": "/w/p"}, ("**/.env",), (),
                                       cwd="/w/p", write_roots=("/w/p",)))
    add("Grep", safety.path_violation("Grep", {"path": "/w/p"}, ("/w/p/sub/**",), (),
                                       cwd="/w/p", write_roots=("/w/p",)))
    for glob in (5, "a\\b", "/abs/*", "../up/*"):
        add("Grep", safety.path_violation("Grep", {"path": "/w/p", "glob": glob}, (), (),
                                           cwd="/w/p", write_roots=("/w/p",)))
    add("Grep", safety.path_violation("Grep", {"path": 4}, (), (), cwd="/w/p"))
    add("Glob", safety.path_violation("Glob", {"path": "/w/p", "pattern": 9}, (), (),
                                       cwd="/w/p"))
    add("Grep", safety.path_violation("Grep", {"path": "/w/p"}, ("**/.env",), (), cwd="/w/p"))
    add("Grep", safety.path_violation("Grep", {"path": "/srv"}, ("/srv/secret/**",), (),
                                       cwd="/w/p"))
    return out


def enforce_reasons(tmp: Path) -> list[tuple[str, str]]:
    """enforce's own two reasons, through ``enforce.decide`` and
    ``enforce.fail_closed`` as tests/test_hook_enforcement.py drives them."""
    import json
    from unittest import mock

    from aipager.dtach import enforce
    snap = {"bypass_safety": False, "deny_tools": [], "allow_tools": [],
            "deny_paths_no_access": ["~/.claude/**"], "deny_paths_no_write": [],
            "deny_bash_patterns": [r"\bclaude\b"]}
    transcript = tmp / "blocked.jsonl"
    lines = [
        {"type": "user", "message": {"content": "[via Telegram · @bob · role:user]\nread it"}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "aipager safety policy: Bash command blocked"}]}},
    ]
    transcript.write_text("".join(json.dumps(x) + "\n" for x in lines))
    out = []
    with mock.patch.object(enforce, "read_snapshot", lambda s: dict(snap)):
        halted = enforce.decide({
            "hook_event_name": "PreToolUse", "session": "claude-x__g100",
            "tool_name": "Bash", "tool_input": {"command": "ls"},
            "transcript_path": str(transcript)})
        out.append(("Bash", halted["reason"]))
    with mock.patch.object(enforce, "read_snapshot", lambda s: None):
        closed = enforce.fail_closed({"session": "claude-x__g100", "tool_name": "Bash"})
        out.append(("Bash", closed["reason"]))
    return out


@surface("safety.notice")
def _safety_notice(env):
    import tempfile

    from aipager import audit as audit_mod
    bot, tg = world(env)
    alive(env)
    env.mp.setattr(audit_mod, "append", lambda **k: None)
    with tempfile.TemporaryDirectory() as tmp:
        reasons = safety_reasons() + enforce_reasons(Path(tmp))[1:]
    from aipager.state import Status
    for tool, reason in reasons:
        sess = session(bot, chat=GROUP, status=Status.BUSY)
        sess.busy_msg_id = None
        env.run(bot.notify(sess, "safety_blocked", {"tool": tool, "reason": reason}))
    return tg.out


# ---- late answers ---------------------------------------------------------------------

def _held_entry(minutes, *, attempts=0):
    return SimpleNamespace(
        session="claude-x1__d555", label="x1", chat_id=DM, key=f"k{minutes}",
        rich_text="The answer.", plain_text="The answer.", reply_to=None,
        attempts=attempts, held_seconds=lambda: minutes * 60.0)


@surface("held.late_marker")
def _held_late_marker(env):
    """A held answer delivered after a mute, held 1, 2, 5, 11 and 21
    minutes: rich, and plain once every rich attempt is spent."""
    from aipager.bot import held as held_mod
    from aipager.bot import notify as notify_mod
    bot, tg = world(env)
    sess = session(bot, chat=DM)
    bot._confirm_held = MagicMock()
    env.mp.setattr(held_mod.HELD, "drop", lambda chat, key: None)
    out: list[str] = []

    async def _rich(chat, text, **kw):
        out.append(text)
        return {"message_id": 1}

    async def _refused(chat, text, **kw):
        raise RuntimeError("bad markdown")
    for minutes in (1, 2, 5, 11, 21):
        env.mp.setattr(notify_mod, "send_rich_message", AsyncMock(side_effect=_rich))
        env.mp.setattr(held_mod.HELD, "pending",
                       lambda chat, m=minutes: [_held_entry(m)])
        env.run(bot.flush_held_answers(sess))
    env.mp.setattr(notify_mod, "send_rich_message", AsyncMock(side_effect=_refused))
    env.mp.setattr(held_mod.HELD, "pending", lambda chat: [
        _held_entry(5, attempts=held_mod.HELD_ANSWER_MAX_ATTEMPTS - 1)])
    env.run(bot.flush_held_answers(sess))
    return out + tg.out


# ---- /help and the home screen ------------------------------------------------------

def _help(env, chat, username):
    from aipager.bot import report_flow
    bot, tg = world(env, username=username)
    env.mp.setattr(report_flow, "is_owner", lambda *a, **k: False)
    cmd(env, bot, tg, "_handle_help_cmd", "/help", chat=chat)
    return tg.out


@surface("help.dm")
def _help_dm(env):
    return _help(env, DM, BOT_NAME)


@surface("help.group")
def _help_group(env):
    return _help(env, GROUP, BOT_NAME)


@surface("help.group_unnamed")
def _help_group_unnamed(env):
    return _help(env, GROUP, "")


@surface("home.dm")
def _home_dm(env):
    """/start in a DM: the target named, then sessions listed but none the
    target, with an ended one to resume."""
    from aipager.state import Status
    bot, tg = world(env)
    bot._read_status_file = lambda name: None
    x1 = session(bot, "x1", chat=DM)
    bot.registry.set_target(x1.name, DM, OWNER)
    cmd(env, bot, tg, "_handle_start_cmd", "/start", chat=DM)
    bot2, tg2 = world(env)
    bot2._read_status_file = lambda name: None
    session(bot2, "x1", chat=DM)
    session(bot2, "x2", chat=DM)
    gone = session(bot2, "x3", chat=DM, status=Status.GONE)
    gone.claude_session_id = "abc"
    gone.gone_at = 5.0
    cmd(env, bot2, tg2, "_handle_start_cmd", "/start", chat=DM)
    return tg.out + tg2.out


@surface("home.group")
def _home_group(env):
    bot, tg = world(env)
    bot._read_status_file = lambda name: None
    x1 = session(bot, "x1", chat=GROUP)
    bot.registry.set_target(x1.name, GROUP, OWNER)
    cmd(env, bot, tg, "_handle_start_cmd", "/start", chat=GROUP)
    return tg.out


@surface("home.empty")
def _home_empty(env):
    bot, tg = world(env)
    bot._read_status_file = lambda name: None
    cmd(env, bot, tg, "_handle_start_cmd", "/start", chat=DM)
    return tg.out


# ---- routing replies --------------------------------------------------------------------

@surface("route.stop")
def _route_stop(env):
    """/stop: a name nobody has, a session that is not working, nothing
    running, and two working sessions (the picker)."""
    from aipager.state import Status
    bot, tg = world(env)
    alive(env)
    session(bot, "x1", chat=DM)
    cmd(env, bot, tg, "_handle_stop_cmd", "/stop zz", chat=DM)
    cmd(env, bot, tg, "_handle_stop_cmd", "/stop x1", chat=DM)
    cmd(env, bot, tg, "_handle_stop_cmd", "/stop", chat=DM)
    session(bot, "x2", chat=DM, status=Status.BUSY)
    session(bot, "x3", chat=DM, status=Status.BUSY)
    cmd(env, bot, tg, "_handle_stop_cmd", "/stop", chat=DM)
    return tg.out


@surface("route.kill")
def _route_kill(env):
    bot, tg = world(env)
    alive(env)
    cmd(env, bot, tg, "_handle_kill_cmd", "/kill", chat=DM)
    cmd(env, bot, tg, "_handle_kill_cmd", "/kill zz", chat=DM)
    session(bot, "x1", chat=DM)
    session(bot, "x2", chat=DM)
    cmd(env, bot, tg, "_handle_kill_cmd", "/kill", chat=DM)
    return tg.out


@surface("route.clearqueue")
def _route_clearqueue(env):
    """/clearqueue with no session, nothing to clear, and 1, 2 and 5
    messages cleared."""
    bot, tg = world(env)
    alive(env)
    cmd(env, bot, tg, "_handle_clearqueue_cmd", "/clearqueue", chat=DM)
    x1 = session(bot, "x1", chat=DM)
    bot.registry.set_target(x1.name, DM, OWNER)
    for ok, n in ((False, 0), (True, 1), (True, 2), (True, 5)):
        bot._clear_queue_core = AsyncMock(return_value=SimpleNamespace(ok=ok, dropped=n))
        cmd(env, bot, tg, "_handle_clearqueue_cmd", "/clearqueue", chat=DM)
    return tg.out


@surface("route.which_session")
def _route_which_session(env):
    """A group member with no session of their own while two run."""
    bot, tg = world(env)
    alive(env)
    session(bot, "x1", chat=GROUP)
    session(bot, "x2", chat=GROUP)
    cmd(env, bot, tg, "_handle_clearqueue_cmd", "/clearqueue", chat=GROUP, user=BOB)
    return tg.out


@surface("route.ended_target")
def _route_ended_target(env):
    """Their own session has ended: which one, with Resume, and the line
    asking to send the message again after it."""
    from aipager.state import Status
    bot, tg = world(env)
    alive(env)
    session(bot, "x2", chat=GROUP)
    x1 = session(bot, "x1", chat=GROUP)
    bot.registry.set_target(x1.name, GROUP, BOB)
    x1.status = Status.GONE
    x1.claude_session_id = "abc"
    cmd(env, bot, tg, "_handle_clearqueue_cmd", "/clearqueue", chat=GROUP, user=BOB)
    update = msg(tg, "hello", chat=GROUP, user=BOB)
    env.run(bot._dispatch_voice_transcript(update, "hello", None))
    return tg.out


@surface("route.no_session")
def _route_no_session(env):
    """Nothing to route a text, a voice transcript or a file to."""
    bot, tg = world(env)
    alive(env)
    update = msg(tg, "hello", chat=DM)
    ctx = MagicMock()
    ctx.bot.id = 999
    env.run(bot._handle_message(update, ctx))
    env.run(bot._dispatch_voice_transcript(msg(tg, "hello", chat=DM), "hello", None))
    env.run(bot._inject_file_prompt(msg(tg, "", chat=DM), ctx, "", [Path("/tmp/f.txt")],
                                    all_photos=False, log_name="f.txt"))
    env.run(bot._send_template(msg(tg, "", chat=DM), "Continue", button=""))
    env.run(bot._send_command(msg(tg, "", chat=DM), "/compact"))
    return tg.out


@surface("route.send_failures")
def _route_send_failures(env):
    """The session's process is gone, or the send did not reach it: for a
    text, a transcript, a file, a template, a command, and a typed /x1."""
    from aipager.state import Status
    bot, tg = world(env)
    ctx = MagicMock()
    ctx.bot.id = 999
    x1 = session(bot, "x1", chat=DM, name="x1")
    bot.registry.set_target(x1.name, DM, OWNER)
    alive(env, False)
    env.run(bot._handle_message(msg(tg, "hello", chat=DM), ctx))
    env.run(bot._dispatch_voice_transcript(msg(tg, "hello", chat=DM), "hello", None))
    env.run(bot._inject_file_prompt(msg(tg, "", chat=DM), ctx, "", [Path("/tmp/f.txt")],
                                    all_photos=False, log_name="f.txt"))
    env.run(bot._send_template(msg(tg, "", chat=DM), "Continue", button=""))
    env.run(bot._send_command(msg(tg, "", chat=DM), "/compact"))
    env.run(bot._handle_message(msg(tg, "/x1 hello", chat=DM), ctx))
    env.run(bot._handle_message(msg(tg, "/zz hello", chat=DM), ctx))
    env.run(bot._inject_file_prompt(msg(tg, "", chat=DM), ctx, "/zz look", [Path("/tmp/f.txt")],
                                    all_photos=False, log_name="f.txt"))
    env.run(bot._inject_file_prompt(msg(tg, "", chat=DM), ctx, "/x1 look", [Path("/tmp/f.txt")],
                                    all_photos=False, log_name="f.txt"))
    alive(env, True)
    bot._inject_prompt = AsyncMock(return_value=False)
    env.run(bot._handle_message(msg(tg, "hello", chat=DM), ctx))
    env.run(bot._dispatch_voice_transcript(msg(tg, "hello", chat=DM), "hello", None))
    env.run(bot._inject_file_prompt(msg(tg, "", chat=DM), ctx, "", [Path("/tmp/f.txt")],
                                    all_photos=False, log_name="f.txt"))
    env.run(bot._send_template(msg(tg, "", chat=DM), "Continue", button=""))
    env.run(bot._send_command(msg(tg, "", chat=DM), "/compact"))
    env.run(bot._handle_message(msg(tg, "/x1 hello", chat=DM), ctx))
    x1.status = Status.BUSY
    env.run(bot._send_command(msg(tg, "", chat=DM), "/clear"))
    return tg.out


@surface("route.sent_to")
def _route_sent_to(env):
    """What a template, a command and a model tap say once sent: the
    keyboard's own label in the chat's language."""
    from aipager import i18n
    from aipager.bot import kbwords
    bot, tg = world(env)
    alive(env)
    x1 = session(bot, "x1", chat=DM)
    bot.registry.set_target(x1.name, DM, OWNER)
    bot._inject_prompt = AsyncMock(return_value=True)
    bot._confirm_model_feedback = AsyncMock()
    label = kbwords.shown_label(i18n.for_chat(DM), "Continue", "Continue")
    from aipager.state import Status
    env.run(bot._send_template(msg(tg, label, chat=DM), "Continue", button=label))
    for command in ("/clear", "/compact", "/model opus"):
        x1.status = Status.IDLE
        x1.model_name = "Opus 4.7"
        env.run(bot._send_command(msg(tg, "", chat=DM), command))
    return tg.out


@surface("route.queue_full")
def _route_queue_full(env):
    """A message held behind an open prompt when the queue is full."""
    from aipager.state import Status
    bot, tg = world(env)
    alive(env)
    x1 = session(bot, "x1", chat=DM, status=Status.INTERACTIVE)
    x1.pending_permission = {"tool_summary": "Bash: ls"}
    x1.queue_prompt = lambda *a, **k: False
    bot.registry.set_target(x1.name, DM, OWNER)
    ctx = MagicMock()
    ctx.bot.id = 999
    env.run(bot._handle_message(msg(tg, "hello", chat=DM), ctx))
    return tg.out


@surface("route.mention_alone")
def _route_mention_alone(env):
    """`@bot /stop` in a group: a command sent with the bot's mention."""
    from telegram import MessageEntity
    bot, tg = world(env)
    alive(env)
    text = f"@{BOT_NAME} /stop"
    mention = MessageEntity(type=MessageEntity.MENTION, offset=0, length=len(BOT_NAME) + 1)
    update = msg(tg, text, chat=GROUP, entities={mention: f"@{BOT_NAME}"})
    ctx = MagicMock()
    ctx.bot.id = 999
    env.run(bot._handle_message(update, ctx))
    return tg.out


@surface("route.model_refusals")
def _route_model_refusals(env):
    """/model refused while a turn runs, a prompt is open, the state is
    not known yet, a switch is pending, or another member's message is
    still waiting."""
    from aipager.bot import handlers
    from aipager.state import Status
    bot, tg = world(env)
    alive(env)
    x1 = session(bot, "x1", chat=DM)
    bot.registry.set_target(x1.name, DM, OWNER)
    for status, extra in ((Status.BUSY, {}),
                          (Status.INTERACTIVE, {"pending_permission": {"tool_summary": "x"}}),
                          (Status.UNKNOWN, {})):
        x1.status = status
        for key, value in extra.items():
            setattr(x1, key, value)
        env.run(bot._send_command(msg(tg, "", chat=DM), "/model opus"))
    x1.status = Status.IDLE
    x1.pending_permission = {}
    x1.model_switch_pending = lambda: True
    env.run(bot._send_command(msg(tg, "", chat=DM), "/model opus"))
    x1.model_switch_pending = lambda: False
    env.mp.setattr(handlers, "mixed_sender_note_outstanding", lambda *a, **k: True)
    env.run(bot._send_command(msg(tg, "", chat=DM), "/model opus"))
    return tg.out


@surface("route.model_feedback")
def _route_model_feedback(env):
    """The /model reply once the statusline confirms the switch, and when
    it does not (under the reply's own first line, as /model sent it)."""
    from aipager.bot import handlers
    bot, tg = world(env)
    x1 = session(bot, "x1", chat=DM)
    bot.registry.set_target(x1.name, DM, OWNER)
    x1.model_name = "Sonnet"
    bot._inject_prompt = AsyncMock(return_value=True)
    bot._confirm_model_feedback = AsyncMock()
    env.run(bot._send_command(msg(tg, "", chat=DM), "/model opus"))
    sent, _sess, _previous, header = bot._confirm_model_feedback.call_args.args
    start = len(tg.out)
    confirm = type(bot)._confirm_model_feedback
    env.mp.setattr(handlers, "await_model_change", AsyncMock(return_value="Opus 4.7"))
    env.run(confirm(bot, sent, x1, "Sonnet", header))
    env.mp.setattr(handlers, "await_model_change", AsyncMock(return_value=None))
    env.run(confirm(bot, sent, x1, "Sonnet", header))
    return tg.out[start:]


# ---- /mode, the mode card, /app and /whoami ---------------------------------------------

@surface("modecard.card")
def _modecard_card(env):
    """The /mode card in a DM (the target marked) in each of the three
    modes, waiting for a switch to be confirmed, in Ask because Auto is not
    available, and in a mode of Claude Code's own; and in a group
    (nobody's target)."""
    from aipager.state import ModeSwitch
    bot, tg = world(env)
    alive(env)
    x1 = session(bot, "x1", chat=DM)
    bot.registry.set_target(x1.name, DM, OWNER)
    for mode in ("ask", "auto", "full"):
        x1.perm_mode = mode
        cmd(env, bot, tg, "_handle_mode_cmd", "/mode", chat=DM)
    x1.perm_mode = "auto"
    x1.mode_switch = ModeSwitch(target="auto", presses=3, sent_at=0.0)
    cmd(env, bot, tg, "_handle_mode_cmd", "/mode", chat=DM)
    x1.reset_mode_tracking()
    x1.reported_mode, x1.auto_unavailable = "default", True
    cmd(env, bot, tg, "_handle_mode_cmd", "/mode", chat=DM)
    x1.reset_mode_tracking()
    x1.reported_mode = "plan"
    cmd(env, bot, tg, "_handle_mode_cmd", "/mode", chat=DM)
    g1 = session(bot, "x2", chat=GROUP)
    g1.perm_mode = "full"
    cmd(env, bot, tg, "_handle_mode_cmd", "/mode x2", chat=GROUP)
    return tg.out


@surface("modecard.cmd_replies")
def _modecard_cmd_replies(env):
    bot, tg = world(env)
    alive(env)
    cmd(env, bot, tg, "_handle_mode_cmd", "/mode", chat=DM)
    cmd(env, bot, tg, "_handle_mode_cmd", "/mode x1 ask extra", chat=DM)
    cmd(env, bot, tg, "_handle_mode_cmd", "/mode zz", chat=DM)
    session(bot, "x1", chat=DM)
    session(bot, "x2", chat=DM)
    for text in ("/mode", "/mode ask", "/mode auto", "/mode full", "/mode x1 ask"):
        cmd(env, bot, tg, "_handle_mode_cmd", text, chat=DM)
    return tg.out


def _perms_world(env):
    """Switches go through the real dispatcher: an Ask <-> Auto one presses
    keys (none written), one that restarts reaches a stand-in restart."""
    from aipager.dtach import inject
    bot, tg = world(env)
    alive(env)
    env.mp.setattr(inject, "press_mode_cycle", AsyncMock(return_value=True))
    bot._perms_switch_core = AsyncMock(return_value=SimpleNamespace(
        ok=True, reason="done", err="", label="x1"))
    # These surfaces predate members' root folders: nobody here is held to
    # a root (member_root and the user-root-folders suite test that rule).
    from aipager import member_root
    env.mp.setattr(member_root, "restricted_root", lambda *a, **k: None)
    return bot, tg


@surface("modecard.perms_flow")
def _modecard_perms_flow(env):
    """/mode x1 typed: Full access refused to a member, a session still
    starting, a busy one (Stop & switch into Full access), Auto from Ask
    without a restart while it works, refused while a prompt is open, the
    Full access confirm, and Ask at once out of Full access, ending in the
    card with who switched it."""
    from aipager.state import Status
    bot, tg = _perms_world(env)
    x1 = session(bot, "x1", chat=GROUP)
    cmd(env, bot, tg, "_handle_mode_cmd", "/mode x1 full", chat=GROUP, user=BOB)
    x1.status = Status.UNKNOWN
    cmd(env, bot, tg, "_handle_mode_cmd", "/mode x1 full", chat=GROUP)
    x1.status = Status.BUSY
    cmd(env, bot, tg, "_handle_mode_cmd", "/mode x1 full", chat=GROUP)
    cmd(env, bot, tg, "_handle_mode_cmd", "/mode x1 auto", chat=GROUP, user=BOB)
    x1.reset_mode_tracking()
    x1.perm_mode = "ask"
    x1.status = Status.INTERACTIVE
    cmd(env, bot, tg, "_handle_mode_cmd", "/mode x1 auto", chat=GROUP)
    x1.status = Status.IDLE
    cmd(env, bot, tg, "_handle_mode_cmd", "/mode x1 full", chat=GROUP)
    x1.perm_mode = "full"
    cmd(env, bot, tg, "_handle_mode_cmd", "/mode x1 ask", chat=GROUP)
    return tg.out


@surface("modecard.switch_taps")
def _modecard_switch_taps(env):
    """The confirm's and the busy card's buttons: cancel, a card out of
    date, a restart running, a session gone, and the switch's outcomes;
    then the card's own switches: Ask <-> Auto in place, refused while a
    prompt is open."""
    from aipager.state import Status
    bot, tg = _perms_world(env)
    x1 = session(bot, "x1", chat=GROUP)

    def pend(target="full", turn=None):
        bot._perms_pending[x1.name] = {"target_mode": target, "msg_id": 42,
                                       "label": "x1", "turn": x1.turn_key
                                       if turn is None else turn}
    pend()
    tap(env, bot, tg, _sess_cb(x1, "perms_cancel"), chat=GROUP)
    tap(env, bot, tg, _sess_cb(x1, "perms_confirm"), chat=GROUP)
    x1.relaunch_in_flight = True
    pend()
    tap(env, bot, tg, _sess_cb(x1, "perms_confirm"), chat=GROUP)
    x1.relaunch_in_flight = False
    pend(turn=x1.turn_key + 5)
    tap(env, bot, tg, _sess_cb(x1, "perms_confirm"), chat=GROUP)
    x1.perm_mode = "full"
    for outcome in (SimpleNamespace(ok=True, reason="done", err="", label="x1"),
                    SimpleNamespace(ok=False, reason="still_stopping", err="", label="x1"),
                    SimpleNamespace(ok=False, reason="launch_failed", err="13", label="x1"),
                    SimpleNamespace(ok=False, reason="already_restarting", err="", label="x1"),
                    SimpleNamespace(ok=False, reason="not_live", err="", label="x1")):
        bot._perms_switch_core = AsyncMock(return_value=outcome)
        pend(target="ask")
        tap(env, bot, tg, _sess_cb(x1, "perms_stop_switch"), chat=GROUP)
    bot._perms_pending["claude-zz"] = {"target_mode": "full", "msg_id": 42,
                                       "label": "zz", "turn": 0}
    tap(env, bot, tg, "claude-zz:perms_confirm", chat=GROUP)
    tap(env, bot, tg, "claude-zz:perms_cancel", chat=GROUP)
    x1.perm_mode = "ask"
    tap(env, bot, tg, _sess_cb(x1, f"modeauto{x1.turn_key}"), chat=GROUP)
    x1.reset_mode_tracking()
    x1.status = Status.INTERACTIVE
    tap(env, bot, tg, _sess_cb(x1, f"modeauto{x1.turn_key}"), chat=GROUP)
    return tg.out


@surface("modecard.switch_results")
def _modecard_switch_results(env):
    """What the next hook makes of an Ask <-> Auto switch, on the card
    that said it was sent: confirmed (in a group, who switched it), Auto
    not available, and a mode it did not reach; with no card left, a
    failure is a message of its own."""
    from aipager.bot import mode_switch
    from aipager.state import ModeSwitch
    bot, tg = _perms_world(env)
    x1 = session(bot, "x1", chat=GROUP)
    cases = (("auto", "auto", "@bob", True, False), ("auto", "default", "", True, True),
             ("ask", "plan", "", True, True), ("ask", "plan", "", False, True))
    for target, reported, by, has_card, retried in cases:
        x1.reset_mode_tracking()
        x1.perm_mode = target
        x1.mode_switch = ModeSwitch(target=target, presses=1, sent_at=0.0, retried=retried,
                                    reply=(GROUP, 42, "mode_card") if has_card else None,
                                    by=by)
        x1.reported_mode = reported
        env.run(_reported(mode_switch, bot, x1))
    return tg.out


async def _reported(module, bot, sess):
    """``on_reported`` and the edits it spawns, in one loop (a task left on
    another loop would never run)."""
    import asyncio
    await module.on_reported(bot, sess, {"dialog": False})
    for _ in range(5):
        if not module._TASKS:
            break
        await asyncio.gather(*list(module._TASKS), return_exceptions=True)


@surface("appcmd.replies")
def _appcmd_replies(env):
    """/app in a group (to someone with a DM of their own, and to someone
    without), not enabled, can't start, still being set up, a stranger in
    personal mode, and the button."""
    from aipager import config
    from aipager.miniapp import server, tunnel
    bot, tg = world(env)
    cmd(env, bot, tg, "_handle_app_cmd", "/app", chat=GROUP)
    cmd(env, bot, tg, "_handle_app_cmd", "/app", chat=GROUP, user=BOB)
    env.mp.setattr(config, "MINIAPP_ENABLED", False)
    cmd(env, bot, tg, "_handle_app_cmd", "/app", chat=DM)
    env.mp.setattr(config, "MINIAPP_ENABLED", True)
    env.mp.setattr(tunnel, "resolve_public_url", AsyncMock(return_value=""))
    for available in (False, True):
        env.mp.setattr(server, "miniapp_extra_available", lambda a=available: a)
        cmd(env, bot, tg, "_handle_app_cmd", "/app", chat=DM)
    env.mp.setattr(tunnel, "resolve_public_url",
                   AsyncMock(return_value="https://example.invalid/app"))
    cmd(env, bot, tg, "_handle_app_cmd", "/app", chat=DM)
    personal, ptg = world(env, scopes=False, chats=(DM,))
    personal._is_personal_mode_operator = lambda uid: False
    cmd(env, personal, ptg, "_handle_app_cmd", "/app", chat=DM, user=4321)
    return tg.out + ptg.out


@surface("whoami.replies")
def _whoami_replies(env):
    """/whoami: an owner and a member in a group's scope, someone not in
    it, a team member and a stranger, and personal mode."""
    from aipager import policy_snapshot
    from aipager.team import Role, Rules, Team, User
    bot, tg = world(env)
    # A fixed snapshot, not the built-in roles' lists (they grow).
    env.mp.setattr(policy_snapshot, "resolve_snapshot", lambda role, scope, member: {
        "deny_tools": ["Bash", "WebFetch"] if member.role == "user" else [],
        "allow_tools": ["Read", "Grep"] if member.role == "user" else [],
        "bypass_safety": member.role == "owner"})
    cmd(env, bot, tg, "_handle_whoami", "/whoami", chat=GROUP)
    cmd(env, bot, tg, "_handle_whoami", "/whoami", chat=GROUP, user=BOB)
    bot._authorize = AsyncMock(return_value=True)
    cmd(env, bot, tg, "_handle_whoami", "/whoami", chat=GROUP, user=4321)
    team, ttg = world(env, scopes=False, chats=(GROUP,))
    team.team = Team(group_id=GROUP, users={
        OWNER: User(id=OWNER, label="aly", role=Role.ADMIN),
        BOB: User(id=BOB, label="bob", role=Role.DEVELOPER)},
        rules=Rules(deny_tools=("Bash",)))
    team._authorize = AsyncMock(return_value=True)
    cmd(env, team, ttg, "_handle_whoami", "/whoami", chat=GROUP, user=BOB)
    team.team = Team(group_id=GROUP, users={
        OWNER: User(id=OWNER, label="aly", role=Role.ADMIN)})
    cmd(env, team, ttg, "_handle_whoami", "/whoami", chat=GROUP)
    cmd(env, team, ttg, "_handle_whoami", "/whoami", chat=GROUP, user=4321)
    personal, ptg = world(env, scopes=False, chats=(DM,))
    cmd(env, personal, ptg, "_handle_whoami", "/whoami", chat=DM)
    return tg.out + ttg.out + ptg.out


# ---- voice notes and files ------------------------------------------------------------------

def _voice_msg(tg, *, size=1000):
    update = msg(tg, None, chat=DM)
    voice = MagicMock()
    voice.file_size = size
    tg_file = MagicMock()
    tg_file.download_to_drive = AsyncMock()
    voice.get_file = AsyncMock(return_value=tg_file)
    update.message.voice = voice
    return update


@surface("media.voice")
def _media_voice(env):
    """A voice note: the extra missing, too big, a failed download, and a
    transcription that is unavailable, fails, hears nothing, and hears."""
    import sys

    from aipager.bot import voice as voice_mod
    bot, tg = world(env)
    bot._dispatch_voice_transcript = AsyncMock()
    env.mp.setattr(voice_mod, "is_available", lambda: False)
    env.run(bot._handle_voice(_voice_msg(tg), MagicMock()))
    env.mp.setattr(voice_mod, "is_available", lambda: True)
    env.run(bot._handle_voice(_voice_msg(tg, size=31 * 1024 * 1024 + 1), MagicMock()))
    failing = _voice_msg(tg)
    failing.message.voice.get_file = AsyncMock(side_effect=RuntimeError("13"))
    env.run(bot._handle_voice(failing, MagicMock()))
    env.mp.setitem(sys.modules, "faster_whisper", None)
    env.mp.setattr(voice_mod, "_model", None)

    async def _not_installed(path, language=None):
        return voice_mod._get_model()

    async def _broken(path, language=None):
        raise RuntimeError("13")
    for transcribe in (_not_installed, _broken, AsyncMock(return_value=""),
                       AsyncMock(return_value="x1")):
        env.mp.setattr(voice_mod, "transcribe", transcribe)
        env.run(bot._handle_voice(_voice_msg(tg), MagicMock()))
    return tg.out


class _Proc:
    """An installer process: times out once (the heartbeat), then ends."""

    def __init__(self, returncode, output=b"x1"):
        self.returncode = returncode
        self._waits = 0
        self.stdout = SimpleNamespace(read=AsyncMock(return_value=output))

    async def wait(self):
        import asyncio
        self._waits += 1
        if self._waits == 1:
            raise asyncio.TimeoutError
        return self.returncode


def _clock(*values):
    times = list(values)

    def _now():
        return times.pop(0) if len(times) > 1 else times[0]
    return _now


@surface("media.voice_install")
def _media_voice_install(env):
    """Installing the voice extra from its button: no recipe, the installer
    won't start, still working, installed (with brew's note), failed."""
    import time as _time

    from aipager import updater
    from aipager.bot import handlers
    bot, tg = world(env)
    out = []
    for installer, cmdline, proc in (
            ("pip", None, None),
            ("pip", ["pip", "install"], OSError("13")),
            ("brew", ["brew", "install"], _Proc(0)),
            ("pip", ["pip", "install"], _Proc(2)),
    ):
        env.mp.setattr(updater, "_detect_installer", lambda i=installer: i)
        env.mp.setattr(updater, "install_extra_cmd", lambda inst, extra, c=cmdline: c)

        async def _spawn(*a, p=proc, **k):
            if isinstance(p, Exception):
                raise p
            return p
        env.mp.setattr(handlers, "_create_subprocess_exec", _spawn)
        env.mp.setattr(handlers, "time", SimpleNamespace(
            monotonic=_clock(0.0, 7.0, 7.0, 7.0), time=_time.time))
        update, q = query(tg, "__voice__:install", chat=DM)
        env.run(bot._install_voice_extra(q))
    return out + tg.out


@surface("media.voice_restart")
def _media_voice_restart(env):
    """The restart button: an update running, the update lock held, the
    service would kill sessions, a failed schedule, systemd's delay,
    launchctl, and the foreground respawn failing three ways."""

    from aipager import self_update
    from aipager import service as service_mod
    bot, tg = world(env)
    session(bot, "x1", chat=DM)

    def run():
        update, q = query(tg, "__voice__:restart", chat=DM)
        env.run(bot._restart_daemon(q))
    bot.updates = SimpleNamespace(busy=True)
    run()
    bot.updates = None

    class _Lock:
        def __init__(self, free):
            self.free = free

        def try_acquire(self):
            return self.free

        def release(self):
            return None
    env.mp.setattr(self_update, "UpdateLock", lambda: _Lock(False))
    run()
    env.mp.setattr(self_update, "UpdateLock", lambda: _Lock(True))
    import platform
    env.mp.setattr(platform, "system", lambda: "Linux")
    for plan, scheduled in (
            (SimpleNamespace(mode="systemd", automatic=False, reason="", at_risk=["x1", "x2"]),
             None),
            (SimpleNamespace(mode="systemd", automatic=False, reason="", at_risk=[]), None),
            (SimpleNamespace(mode="systemd", automatic=True, reason="", at_risk=[]),
             (False, "13")),
            (SimpleNamespace(mode="systemd", automatic=True, reason="", at_risk=[]),
             (True, "")),
    ):
        env.mp.setattr(self_update, "restart_plan", lambda registry, p=plan: p)
        env.mp.setattr(self_update, "schedule_restart", lambda p, s=scheduled: s)
        run()
    env.mp.setattr(platform, "system", lambda: "Darwin")
    env.mp.setattr(service_mod, "MACOS_PLIST_PATH", SimpleNamespace(exists=lambda: True))
    env.mp.setattr(service_mod, "_run", lambda *a, **k: None)
    run()
    env.mp.setattr(platform, "system", lambda: "Plan9")
    # No service unit: the detached respawn (aipager.self_restart) fails to
    # open its log, to spawn, and dies at once.
    from aipager import instance, self_restart
    env.mp.setattr(instance, "self_restart_log_path", lambda: "/run/x1.log")
    for res in (self_restart.WaiterResult(False, "13", "open"),
                self_restart.WaiterResult(False, "13", "spawn"),
                self_restart.WaiterResult(False, "exit 1", "exited")):
        env.mp.setattr(self_restart, "spawn_waiter", lambda t, p, r=res: r)
        run()
    return tg.out


@surface("media.file")
def _media_file(env):
    """A file too big for a bot to download, and a download that failed."""
    from aipager.bot import handlers
    bot, tg = world(env)
    env.mp.setattr(handlers, "_DOWNLOAD_BACKOFF_SECONDS", (0, 0, 0))
    big = msg(tg, None, chat=DM)
    big.message.document = MagicMock(file_size=31 * 1024 * 1024, file_name="x1.py")
    env.run(bot._handle_file(big, MagicMock()))
    failing = msg(tg, None, chat=DM)
    failing.message.document = MagicMock(file_size=10, file_name="x1.py")
    failing.message.document.get_file = AsyncMock(side_effect=RuntimeError("13"))
    env.run(bot._handle_file(failing, MagicMock()))
    return tg.out


@surface("media.album")
def _media_album(env):
    """An album with items that failed every download: one, two, and some
    left out of an album that went."""
    from aipager.bot import handlers
    bot, tg = world(env)
    alive(env)
    x1 = session(bot, "x1", chat=DM)
    bot.registry.set_target(x1.name, DM, OWNER)
    bot._inject_prompt = AsyncMock(return_value=True)
    for paths, failed in (([], ["a.py"]), ([], ["a.py", "b.py"]),
                          ([Path("/tmp/c.py")], ["a.py"]), ([Path("/tmp/c.py")], ["a.py", "b.py"])):
        album = handlers._PendingAlbum(key=(DM, "g1"), update=msg(tg, None, chat=DM),
                                       ctx=MagicMock(), touched=NOW)
        album.paths = list(paths)
        album.failed = list(failed)
        env.run(bot._flush_album(album))
    return tg.out


@surface("taps.voice_cancel")
def _taps_voice_cancel(env):
    bot, tg = world(env)
    tap(env, bot, tg, "__voice__:cancel", chat=DM)
    return tg.out


# ---- /new ----------------------------------------------------------------------------------

def _new_world(env, *, launch_error=""):
    """A bot whose ``create_session`` registers a session without launching
    anything, with fixed folders."""
    from aipager.bot import new_flow
    from aipager.miniapp import launch
    from aipager.state import Status
    bot, tg = world(env)
    alive(env)
    env.mp.setattr(new_flow, "_project_dir", lambda: "/work/app")
    env.mp.setattr(launch, "allowed_roots", lambda *a, **k: ["/work/app", "/work/lib"])
    env.mp.setattr(launch, "default_folder_is_wide", lambda: False)
    # These surfaces predate members' root folders and pin the folders
    # above: nobody here is held to a root (member_root has tests of its own).
    from aipager import member_root
    env.mp.setattr(member_root, "restricted_root", lambda *a, **k: None)

    async def _create(label, *, scope_chat_id, mode="ask", cwd=None,
                      driver_user_id=None, model=None, keep_card=False, reuse_name=None):
        if launch_error:
            return "", launch_error
        sess = session(bot, label, chat=scope_chat_id, name=reuse_name)
        sess.perm_mode = mode
        if cwd:
            sess.cwd = cwd
        if model:
            sess.launch_model = model
        sess.status = Status.IDLE
        return sess.name, ""
    bot.create_session = _create
    return bot, tg


def _open_name_card(env, bot, tg, *, chat=DM, user=OWNER):
    cmd(env, bot, tg, "_handle_new_cmd", "/new", chat=chat, user=user)


@surface("newflow.name_card")
def _newflow_name_card(env):
    """The Name card in a DM and in a group (whose card it is), with an
    empty name, and for a confined member who has to pick a folder."""
    from aipager.bot import new_flow
    from aipager.miniapp import launch
    bot, tg = _new_world(env)
    _open_name_card(env, bot, tg, chat=DM)
    _open_name_card(env, bot, tg, chat=GROUP)
    env.run(new_flow.create_from_text(bot, msg(tg, "/new", chat=DM), "   "))
    env.mp.setattr(launch, "default_folder_is_wide", lambda: True)
    bot._is_confined_user = lambda *a, **k: True
    # The 8.79 hint (nobody is held to a root folder in _new_world).
    _open_name_card(env, bot, tg, chat=GROUP, user=BOB)
    return tg.out


@surface("newflow.model_page")
def _newflow_model_page(env):
    bot, tg = _new_world(env)
    _open_name_card(env, bot, tg)
    start = len(tg.out)
    tap(env, bot, tg, "_:nw:opt:model", chat=DM, message_id=7001)
    return tg.out[start:]


@surface("newflow.folder_page")
def _newflow_folder_page(env):
    bot, tg = _new_world(env)
    _open_name_card(env, bot, tg)
    start = len(tg.out)
    tap(env, bot, tg, "_:nw:opt:path", chat=DM, message_id=7001)
    return tg.out[start:]


@surface("newflow.prompts")
def _newflow_prompts(env):
    """The custom-model and new-folder questions, in a DM and a group."""
    out = []
    for chat in (DM, GROUP):
        bot, tg = _new_world(env)
        _open_name_card(env, bot, tg, chat=chat)
        start = len(tg.out)
        tap(env, bot, tg, "_:nw:model:custom", chat=chat, message_id=7001)
        tap(env, bot, tg, "_:nw:path:new", chat=chat, message_id=7001)
        out += tg.out[start:]
    return out


@surface("newflow.starting")
def _newflow_starting(env):
    """`/new x1`: Starting, then the Ready card; and a launch that failed."""
    out = []
    bot, tg = _new_world(env)
    cmd(env, bot, tg, "_handle_new_cmd", "/new x1", chat=DM)
    out += tg.out
    bot, tg = _new_world(env, launch_error="13")
    cmd(env, bot, tg, "_handle_new_cmd", "/new x1", chat=DM)
    return out + tg.out


@surface("newflow.ready")
def _newflow_ready(env):
    """The Ready card: with a first message, Auto refused to a member (the
    note), in a group, and its talk line."""
    bot, tg = _new_world(env)
    cmd(env, bot, tg, "_handle_new_cmd", "/new x1 fix the tests", chat=DM)
    cmd(env, bot, tg, "_handle_new_cmd", "/new !x2", chat=GROUP, user=BOB)
    cmd(env, bot, tg, "_handle_new_cmd", "/new x3", chat=GROUP)
    return tg.out


def _ready_session(env, chat=GROUP):
    bot, tg = _new_world(env)
    cmd(env, bot, tg, "_handle_new_cmd", "/new x1", chat=chat)
    sess = bot.registry.find_by_label("x1", chat)
    return bot, tg, sess


@surface("newflow.ready_models")
def _newflow_ready_models(env):
    bot, tg, sess = _ready_session(env)
    start = len(tg.out)
    tap(env, bot, tg, _sess_cb(sess, "rdy_model"), chat=GROUP)
    tap(env, bot, tg, _sess_cb(sess, "rdy_back"), chat=GROUP)
    return tg.out[start:]


@surface("newflow.ready_taps")
def _newflow_ready_taps(env):
    """The Ready card's switches: Full access refused, working, switched
    (who), failed; Ask <-> Auto in place (while it works too) and refused
    while a prompt is open; a model switched or refused, an ended
    session."""
    from aipager.bot.session_ops import ModelSwitchOutcome
    from aipager.dtach import inject
    from aipager.miniapp import sessions as mini
    from aipager.state import Status
    bot, tg, sess = _ready_session(env)
    env.mp.setattr(inject, "press_mode_cycle", AsyncMock(return_value=True))
    start = len(tg.out)
    cb = _sess_cb
    sess.perm_mode = "ask"
    tap(env, bot, tg, cb(sess, "rdy_full"), chat=GROUP, user=BOB)
    sess.status = Status.BUSY
    tap(env, bot, tg, cb(sess, "rdy_full"), chat=GROUP)
    sess.status = Status.IDLE
    for outcome in (SimpleNamespace(ok=True, reason="done", err=""),
                    SimpleNamespace(ok=False, reason="launch_failed", err=""),
                    SimpleNamespace(ok=False, reason="launch_failed", err="13")):
        sess.perm_mode = "ask"
        bot._perms_switch_core = AsyncMock(return_value=outcome)
        tap(env, bot, tg, cb(sess, "rdy_full"), chat=GROUP)
    sess.perm_mode = "ask"
    sess.status = Status.BUSY
    tap(env, bot, tg, cb(sess, "rdy_auto"), chat=GROUP, user=BOB)
    sess.reset_mode_tracking()
    sess.status = Status.INTERACTIVE
    tap(env, bot, tg, cb(sess, "rdy_ask"), chat=GROUP)
    sess.status = Status.IDLE
    for outcome in (ModelSwitchOutcome(ok=True, reason="sent", label="x1"),
                    ModelSwitchOutcome(ok=False, reason="send_failed", label="x1"),
                    ModelSwitchOutcome(ok=False, reason="busy", label="x1",
                                       detail=mini.MODEL_SWITCH_BUSY_REASON),
                    ModelSwitchOutcome(ok=False, reason="prompt_open", label="x1",
                                       detail=mini.MODEL_SWITCH_PROMPT_OPEN_REASON),
                    ModelSwitchOutcome(ok=False, reason="unknown", label="x1",
                                       detail=mini.MODEL_SWITCH_UNKNOWN_REASON),
                    ModelSwitchOutcome(ok=False, reason="switch_pending", label="x1",
                                       detail=mini.MODEL_SWITCH_PENDING_REASON),
                    ModelSwitchOutcome(ok=False, reason="not_live", label="x1",
                                       detail=mini.MODEL_SWITCH_NOT_LIVE_REASON)):
        bot._switch_model_core = AsyncMock(return_value=outcome)
        tap(env, bot, tg, cb(sess, "rdy_m1"), chat=GROUP)
    tap(env, bot, tg, cb(sess, "rdy_m99"), chat=GROUP)
    tap(env, bot, tg, cb(sess, "rdy_zz"), chat=GROUP)
    bot._can_prompt_user = lambda *a, **k: False
    tap(env, bot, tg, cb(sess, "rdy_model"), chat=GROUP)
    sess.status = Status.GONE
    tap(env, bot, tg, cb(sess, "rdy_model"), chat=GROUP)
    return tg.out[start:]


@surface("newflow.taps")
def _newflow_taps(env):
    """The Name card's buttons: someone else's card, an expired one, a
    replaced one, Full access refused, a model or folder no longer offered,
    no folder to create one in, cancel; and starting one where you may
    not."""
    bot, tg = _new_world(env)
    _open_name_card(env, bot, tg, chat=GROUP)          # aly's card: 7001
    start = len(tg.out)
    tap(env, bot, tg, "_:nw:cancel", chat=GROUP, user=BOB, message_id=7001)
    _open_name_card(env, bot, tg, chat=GROUP, user=BOB)
    bob_card = bot._new_wizard_pending[(GROUP, BOB)]["msg_id"]
    tap(env, bot, tg, "_:nw:mode:full", chat=GROUP, user=BOB, message_id=bob_card)
    tap(env, bot, tg, "_:nw:model:99", chat=GROUP, user=BOB, message_id=bob_card)
    tap(env, bot, tg, "_:nw:path:99", chat=GROUP, user=BOB, message_id=bob_card)
    tap(env, bot, tg, "_:nw:bogus", chat=GROUP, user=BOB, message_id=bob_card)
    tap(env, bot, tg, "_:nw:mode:ask", chat=GROUP, user=BOB, message_id=12345)
    from aipager.miniapp import launch
    env.mp.setattr(launch, "allowed_roots", lambda *a, **k: [])
    tap(env, bot, tg, "_:nw:path:new", chat=GROUP, user=BOB, message_id=bob_card)
    tap(env, bot, tg, "_:nw:cancel", chat=GROUP, user=BOB, message_id=bob_card)
    tap(env, bot, tg, "_:nw:cancel", chat=GROUP, user=BOB, message_id=bob_card)
    bot._new_wizard_pending[(GROUP, OWNER)]["last_active"] = -1e9
    tap(env, bot, tg, "_:nw:back", chat=GROUP, message_id=7001)
    bot._can_prompt_user = lambda *a, **k: False
    tap(env, bot, tg, "_:nw:open", chat=GROUP, user=BOB)
    return tg.out[start:]


@surface("newflow.closed")
def _newflow_closed(env):
    """A Name card closed because its person did something else, an
    expired one answered, and someone else's card replied to."""
    import asyncio

    from aipager.bot import new_flow
    bot, tg = _new_world(env)
    _open_name_card(env, bot, tg, chat=GROUP)
    start = len(tg.out)

    async def _close():
        new_flow.close_open_card(bot, GROUP, OWNER)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    env.run(_close())
    _open_name_card(env, bot, tg, chat=GROUP)
    card = bot._new_wizard_pending[(GROUP, OWNER)]["msg_id"]
    reply_to = _reply_to(card)
    ctx = MagicMock()
    ctx.bot.id = 999
    env.run(bot._handle_message(msg(tg, "x9", chat=GROUP, user=BOB, reply_to=reply_to), ctx))
    bot._new_wizard_pending[(GROUP, OWNER)]["last_active"] = -1e9
    env.run(bot._handle_message(msg(tg, "x9", chat=GROUP, reply_to=reply_to), ctx))
    return tg.out[start:]


@surface("newflow.conflict_card")
def _newflow_conflict_card(env):
    """`/new x1` for a session that runs, one used before (with its last
    answer), and a second card for the same name."""
    from aipager.state import Status
    bot, tg = _new_world(env)
    session(bot, "x1", chat=DM)
    gone = session(bot, "x2", chat=DM, status=Status.GONE)
    gone.claude_session_id = "abc"
    gone.last_assistant_preview = "x2"
    cmd(env, bot, tg, "_handle_new_cmd", "/new x1", chat=DM)
    cmd(env, bot, tg, "_handle_new_cmd", "/new x2", chat=DM)
    cmd(env, bot, tg, "_handle_new_cmd", "/new x1", chat=DM)
    return tg.out


@surface("newflow.conflict_taps")
def _newflow_conflict_taps(env):
    """The conflict card's buttons: someone else's, not allowed, cancel, a
    switch with the first message queued, expired, working since, replaced
    (fresh, then the Ready card) and a fresh start that failed."""
    from aipager.state import Status
    bot, tg = _new_world(env)
    x1 = session(bot, "x1", chat=GROUP)
    cmd(env, bot, tg, "_handle_new_cmd", "/new x1 fix it", chat=GROUP)
    start = len(tg.out)
    cb = _sess_cb(x1, "new_cancel")
    tap(env, bot, tg, cb, chat=GROUP, user=BOB)
    bot._can_prompt_user = lambda *a, **k: False
    tap(env, bot, tg, _sess_cb(x1, "new_resume"), chat=GROUP)
    del bot._can_prompt_user
    tap(env, bot, tg, "claude-zz:new_resume", chat=GROUP)
    tap(env, bot, tg, _sess_cb(x1, "new_resume"), chat=GROUP)
    tap(env, bot, tg, _sess_cb(x1, "new_replace"), chat=GROUP)
    cmd(env, bot, tg, "_handle_new_cmd", "/new x1", chat=GROUP)
    x1.status = Status.BUSY
    tap(env, bot, tg, _sess_cb(x1, "new_replace"), chat=GROUP)
    x1.status = Status.IDLE
    tap(env, bot, tg, _sess_cb(x1, "new_replace"), chat=GROUP)
    cmd(env, bot, tg, "_handle_new_cmd", "/new x1", chat=GROUP)
    tap(env, bot, tg, _sess_cb(x1, "new_cancel"), chat=GROUP)
    x2 = session(bot, "x2", chat=GROUP)
    cmd(env, bot, tg, "_handle_new_cmd", "/new x2", chat=GROUP)

    async def _fails(*a, **k):
        return "", "13"
    bot.create_session = _fails
    tap(env, bot, tg, _sess_cb(x2, "new_replace"), chat=GROUP)
    return tg.out[start:]


# ---- session commands (⋮ menu, /restart, /rename, /delete, /diff) ------------------------

def _sess_world(env):
    bot, tg = world(env)
    alive(env)
    bot._read_status_file = lambda name: None
    return bot, tg


@surface("sess.menu")
def _sess_menu(env):
    """⋮ for a live session, an ended one that can be resumed, and one with
    nothing to resume from; Close."""
    from aipager.state import Status
    bot, tg = _sess_world(env)
    x1 = session(bot, "x1", chat=DM)
    x2 = session(bot, "x2", chat=DM, status=Status.GONE)
    x2.claude_session_id = "abc"
    x3 = session(bot, "x3", chat=DM, status=Status.GONE)
    for s in (x1, x2, x3):
        tap(env, bot, tg, _sess_cb(s, "menu"), chat=DM)
    tap(env, bot, tg, _sess_cb(x1, "menu-close"), chat=DM)
    return tg.out


@surface("sess.end_confirm")
def _sess_end_confirm(env):
    bot, tg = _sess_world(env)
    x1 = session(bot, "x1", chat=DM)
    cmd(env, bot, tg, "_handle_kill_cmd", "/kill x1", chat=DM)
    tap(env, bot, tg, _sess_cb(x1, "end"), chat=DM)
    return tg.out


@surface("sess.restart")
def _sess_restart(env):
    """/restart: nothing live, one (its confirm), two (the picker), a name
    nobody has; the confirm's outcomes, cancel, an old button."""
    from aipager.bot import session_parity
    bot, tg = _sess_world(env)

    def restart(text, chat=GROUP, user=OWNER):
        env.run(session_parity.handle_restart_cmd(bot, msg(tg, text, chat=chat, user=user),
                                                  MagicMock()))
    restart("/restart", chat=DM)
    x1 = session(bot, "x1", chat=GROUP)
    restart("/restart")
    session(bot, "x2", chat=GROUP)
    restart("/restart")
    restart("/restart zz")
    for outcome in (SimpleNamespace(ok=True, label="x1", reason="done", err=""),
                    SimpleNamespace(ok=False, label="x1", reason="not_live", err=""),
                    SimpleNamespace(ok=False, label="x1", reason="already_restarting", err=""),
                    SimpleNamespace(ok=False, label="x1", reason="still_stopping", err=""),
                    SimpleNamespace(ok=False, label="x1", reason="launch_failed", err="13"),
                    SimpleNamespace(ok=False, label="x1", reason="other", err="")):
        bot._restart_session_core = AsyncMock(return_value=outcome)
        tap(env, bot, tg, _sess_cb(x1, f"restartok{x1.turn_key}"), chat=GROUP)
    bot._restart_session_core = AsyncMock(return_value=SimpleNamespace(
        ok=True, label="x1", reason="done", err=""))
    d1 = session(bot, "x9", chat=DM)
    bot._restart_session_core = AsyncMock(return_value=SimpleNamespace(
        ok=True, label="x9", reason="done", err=""))
    tap(env, bot, tg, _sess_cb(d1, f"restartok{d1.turn_key}"), chat=DM)
    tap(env, bot, tg, _sess_cb(x1, f"restartok{x1.turn_key + 3}"), chat=GROUP)
    tap(env, bot, tg, _sess_cb(x1, "restart"), chat=GROUP)
    tap(env, bot, tg, _sess_cb(x1, "restart-confirm"), chat=GROUP)
    tap(env, bot, tg, _sess_cb(x1, "restart-cancel"), chat=GROUP)
    return tg.out


@surface("sess.rename")
def _sess_rename(env):
    """/rename with nothing to rename, a name nobody has, a taken name, a
    rename, the same name, the question (DM and group), the picker, and
    the question answered, closed, expired."""
    import asyncio

    from aipager.bot import session_parity
    bot, tg = _sess_world(env)

    def rename(text, chat=DM, user=OWNER):
        env.run(session_parity.handle_rename_cmd(bot, msg(tg, text, chat=chat, user=user),
                                                 MagicMock()))
    rename("/rename")
    rename("/rename zz y1")
    x1 = session(bot, "x1", chat=DM)
    session(bot, "x2", chat=DM)
    bot._rename_session_core = AsyncMock(return_value=SimpleNamespace(
        ok=True, previous_label="x1", new_label="y1", changed=True))
    rename("/rename x1 x2")
    rename("/rename x1 y1")
    bot._rename_session_core = AsyncMock(return_value=SimpleNamespace(
        ok=True, previous_label="x1", new_label="x1", changed=False))
    rename("/rename x1 x1")
    rename("/rename")
    tap(env, bot, tg, _sess_cb(x1, "rename"), chat=DM, message_id=7100)
    ctx = MagicMock()
    ctx.bot.id = 999
    env.run(bot._handle_message(msg(tg, "y1", chat=DM, reply_to=_reply_to(7100)),
                                ctx))
    g1 = session(bot, "x1", chat=GROUP)
    tap(env, bot, tg, _sess_cb(g1, "rename"), chat=GROUP, message_id=7200)
    tap(env, bot, tg, _sess_cb(g1, "rename-cancel"), chat=GROUP, user=BOB, message_id=7200)
    tap(env, bot, tg, _sess_cb(g1, "rename-cancel"), chat=GROUP, message_id=7200)
    tap(env, bot, tg, _sess_cb(g1, "rename"), chat=GROUP, message_id=7300)

    async def _close(text_closed=True):
        if text_closed:
            session_parity.close_rename(bot, GROUP, OWNER)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    env.run(_close())
    tap(env, bot, tg, _sess_cb(g1, "rename"), chat=GROUP, message_id=7400)
    session_parity._rename_pending_map(bot)[(GROUP, OWNER)]["last_active"] = -1e9
    env.run(bot._handle_message(msg(tg, "y1", chat=GROUP, reply_to=_reply_to(7400)),
                                ctx))
    env.run(_close(False))
    tap(env, bot, tg, _sess_cb(g1, "rename"), chat=GROUP, message_id=7500)
    bot.registry._sessions.pop(g1.name)
    env.run(bot._handle_message(msg(tg, "y1", chat=GROUP, reply_to=_reply_to(7500)),
                                ctx))
    bot.registry._sessions[g1.name] = g1
    tap(env, bot, tg, _sess_cb(g1, "rename"), chat=GROUP, message_id=7600)
    bot._can_prompt_user = lambda *a, **k: False
    env.run(bot._handle_message(msg(tg, "y1", chat=GROUP, reply_to=_reply_to(7600)),
                                ctx))
    tap(env, bot, tg, _sess_cb(g1, "rename"), chat=GROUP)
    return tg.out


@surface("sess.delete")
def _sess_delete(env):
    """/delete with nothing ended, one ended (its confirm), two (the
    picker), a running one, a name nobody has; the taps."""
    from aipager.bot import session_parity
    from aipager.state import Status
    bot, tg = _sess_world(env)

    def delete(text, chat=DM):
        env.run(session_parity.handle_delete_cmd(bot, msg(tg, text, chat=chat), MagicMock()))
    delete("/delete")
    x1 = session(bot, "x1", chat=DM, status=Status.GONE)
    delete("/delete")
    x2 = session(bot, "x2", chat=DM, status=Status.GONE)
    delete("/delete")
    session(bot, "x3", chat=DM)
    delete("/delete x3")
    delete("/delete zz")
    tap(env, bot, tg, _sess_cb(x1, "delete"), chat=DM)
    tap(env, bot, tg, _sess_cb(x1, "delete-cancel"), chat=DM)
    x2.is_resuming = lambda: True
    tap(env, bot, tg, _sess_cb(x2, "delete"), chat=DM)
    tap(env, bot, tg, _sess_cb(x2, "delete-confirm"), chat=DM)
    x2.is_resuming = lambda: False
    x2.status = Status.IDLE
    tap(env, bot, tg, _sess_cb(x2, "delete"), chat=DM)
    tap(env, bot, tg, _sess_cb(x2, "delete-confirm"), chat=DM)
    g1 = session(bot, "x1", chat=GROUP, status=Status.GONE)
    tap(env, bot, tg, _sess_cb(g1, "delete-confirm"), chat=GROUP)
    tap(env, bot, tg, _sess_cb(x1, "delete-confirm"), chat=DM)
    bot._can_prompt_user = lambda *a, **k: False
    tap(env, bot, tg, _sess_cb(x2, "delete"), chat=DM)
    tap(env, bot, tg, _sess_cb(x2, "delete-confirm"), chat=DM)
    return tg.out


@surface("sess.diff")
def _sess_diff(env):
    """/diff: nothing live, the picker, a name nobody has, and each thing
    git can say: no folder, no git, not a repo, no commits, an error, no
    changes, one file inline, two files as a document."""
    from aipager.bot import session_parity
    from aipager.miniapp import diff as diff_mod
    bot, tg = _sess_world(env)

    def diff(text, chat=DM):
        env.run(session_parity.handle_diff_cmd(bot, msg(tg, text, chat=chat), MagicMock()))
    diff("/diff")
    session(bot, "x1", chat=DM)
    session(bot, "x2", chat=DM)
    diff("/diff")
    diff("/diff zz")
    patch = "--- a/x1.py\n+++ b/x1.py\n@@ -1 +1,2 @@\n-a\n+b\n+c\n"
    for result in ({"available": False, "reason": "cwd_missing"},
                   {"available": False, "reason": "git_not_installed"},
                   {"available": False, "reason": "not_a_git_repo"},
                   {"available": False, "reason": "no_commits_yet"},
                   {"available": False, "reason": "git_error"},
                   {"available": True, "files": []},
                   {"available": True, "files": [{"patch": patch}]},
                   {"available": True, "files": [{"patch": patch}, {"patch": patch,
                                                                    "binary": True}]}):
        env.mp.setattr(diff_mod, "collect_diff", AsyncMock(return_value=result))
        diff("/diff x1")
    return tg.out


@surface("sess.taps")
def _sess_taps(env):
    """A session's buttons refused or out of date: talk, end, its confirm,
    the mode switches, resume; the outcomes of End; an unknown session."""
    from aipager.state import Status
    bot, tg = _sess_world(env)
    x1 = session(bot, "x1", chat=GROUP)
    gone = session(bot, "x2", chat=GROUP, status=Status.GONE)
    tap(env, bot, tg, "claude-zz:menu", chat=GROUP)
    tap(env, bot, tg, _sess_cb(x1, "talk"), chat=GROUP)
    tap(env, bot, tg, _sess_cb(gone, "talk"), chat=GROUP)
    tap(env, bot, tg, _sess_cb(gone, "end"), chat=GROUP)
    gone.is_resuming = lambda: True
    tap(env, bot, tg, _sess_cb(gone, f"endok{gone.turn_key}"), chat=GROUP)
    gone.is_resuming = lambda: False
    tap(env, bot, tg, _sess_cb(gone, f"endok{gone.turn_key}"), chat=GROUP)
    tap(env, bot, tg, _sess_cb(x1, f"endok{x1.turn_key + 3}"), chat=GROUP)
    for result in ("killed", "resuming", "still_running", "not_found"):
        bot._kill_session_core = AsyncMock(return_value=SimpleNamespace(result=result))
        tap(env, bot, tg, _sess_cb(x1, f"endok{x1.turn_key}"), chat=GROUP)
    tap(env, bot, tg, _sess_cb(gone, "mode_show"), chat=GROUP)
    tap(env, bot, tg, _sess_cb(gone, f"modeask{gone.turn_key}"), chat=GROUP)
    x1.perm_mode = "auto"
    tap(env, bot, tg, _sess_cb(x1, f"modeauto{x1.turn_key}"), chat=GROUP)
    tap(env, bot, tg, _sess_cb(x1, f"modefull{x1.turn_key + 3}"), chat=GROUP)
    tap(env, bot, tg, _sess_cb(x1, "resume"), chat=GROUP)
    gone.claude_session_id = "abc"
    tap(env, bot, tg, _sess_cb(gone, "resume"), chat=GROUP)
    tap(env, bot, tg, _sess_cb(gone, "resume-cancel"), chat=GROUP)
    bot._can_prompt_user = lambda *a, **k: False
    for verb in ("talk", "end", f"endok{x1.turn_key}", f"modeask{x1.turn_key}", "resume",
                 "resume-ask", "restart", f"restartok{x1.turn_key}", "rename", "delete"):
        tap(env, bot, tg, _sess_cb(x1, verb), chat=GROUP)
    return tg.out


@surface("sess.talk_held")
def _sess_talk_held(env):
    """"Which session?" with the message it asked about: sent, not sent,
    and too old to send."""
    import time as _time

    from aipager.bot import held_message
    bot, tg = _sess_world(env)
    x1 = session(bot, "x1", chat=GROUP)
    for went in (True, False):
        held_message.hold(bot, GROUP, 7700 + went, BOB, AsyncMock(return_value=went))
        tap(env, bot, tg, _sess_cb(x1, "talk"), chat=GROUP, user=BOB,
            message_id=7700 + went)
    held_message.hold(bot, GROUP, 7800, BOB, AsyncMock(return_value=True))
    held_message._store(bot)[(GROUP, 7800)].at = _time.monotonic() - 10_000
    tap(env, bot, tg, _sess_cb(x1, "talk"), chat=GROUP, user=BOB, message_id=7800)
    return tg.out


@surface("taps.pick_cancel")
def _taps_pick_cancel(env):
    bot, tg = _sess_world(env)
    tap(env, bot, tg, "_:pick:cancel", chat=DM)
    return tg.out


# ---- resume, stop and switch -----------------------------------------------------------------

@surface("sess.resume")
def _sess_resume(env):
    """/resume by name: nobody by that name, running, no transcript, a
    failed launch, resumed with its last answer and without."""
    from aipager.state import Status
    bot, tg = _sess_world(env)
    bot._build_session_dashboard = lambda sess, *a, **k: "📊"
    cmd(env, bot, tg, "_handle_resume_cmd", "/resume zz", chat=DM)
    x1 = session(bot, "x1", chat=DM, status=Status.GONE)
    x1.claude_session_id = "abc"
    for outcome in (SimpleNamespace(ok=False, reason="not_gone", err=""),
                    SimpleNamespace(ok=False, reason="no_transcript", err=""),
                    SimpleNamespace(ok=False, reason="launch_failed", err="13")):
        bot._do_resume_core = AsyncMock(return_value=outcome)
        cmd(env, bot, tg, "_handle_resume_cmd", "/resume x1", chat=DM)
    bot._do_resume_core = AsyncMock(return_value=SimpleNamespace(ok=True, reason="resumed",
                                                                 err=""))
    x1.last_assistant_preview = "x1"
    cmd(env, bot, tg, "_handle_resume_cmd", "/resume x1", chat=DM)
    x1.last_assistant_preview = ""
    x1.transcript_path = ""
    cmd(env, bot, tg, "_handle_resume_cmd", "/resume x1", chat=DM)
    return tg.out


@surface("sess.stop_switch")
def _sess_stop_switch(env):
    """`/x1 stop` on a session not working and on a name nobody has, `/zz`
    for an unknown session, and a Stop tapped on an old card."""
    from aipager.state import Status
    bot, tg = _sess_world(env)
    ctx = MagicMock()
    ctx.bot.id = 999
    x1 = session(bot, "x1", chat=DM)
    env.run(bot._handle_message(msg(tg, "/x1 stop", chat=DM), ctx))
    env.run(bot._handle_message(msg(tg, "/zz stop", chat=DM), ctx))
    alive(env, False)
    env.run(bot._handle_message(msg(tg, "/zz", chat=DM), ctx))
    x1.status = Status.BUSY
    x1.busy_msg_id = 50
    tap(env, bot, tg, _sess_cb(x1, "stop"), chat=DM, message_id=40)
    return tg.out


@surface("taps.resume_mode")
def _taps_resume_mode(env):
    from aipager.state import Status
    bot, tg = _sess_world(env)
    bot._build_session_dashboard = lambda sess, *a, **k: "📊"
    x1 = session(bot, "x1", chat=DM, status=Status.GONE)
    x1.claude_session_id = "abc"
    tap(env, bot, tg, _sess_cb(x1, "resume_mode_cancel"), chat=DM)
    bot._do_resume_core = AsyncMock(return_value=SimpleNamespace(ok=False, reason="not_gone",
                                                                 err=""))
    tap(env, bot, tg, _sess_cb(x1, "resume_mode_ask"), chat=DM)
    return tg.out


@surface("taps.kill_cancel")
def _taps_kill_cancel(env):
    bot, tg = _sess_world(env)
    x1 = session(bot, "x1", chat=DM)
    tap(env, bot, tg, _sess_cb(x1, "kill-cancel"), chat=DM)
    tap(env, bot, tg, _sess_cb(x1, "kill"), chat=DM)
    return tg.out


# ---- the send-now line, card owners, the tap gate, a moved chat ----------------------------

def _queued(bot, chat=DM):
    from aipager.state import Status
    x1 = session(bot, "x1", chat=chat, status=Status.BUSY)
    x1.queued_targets = [{"msg_id": 900, "chat_id": chat, "raw_text": "x1",
                          "driver_user_id": OWNER}]
    return x1


@surface("sendnow.line")
def _sendnow_line(env):
    """The ⏳ Queued line with its ⚡ Send now button, and the same line
    once a slow send could not reach Claude."""
    from aipager.bot import send_now
    bot, tg = world(env)
    x1 = _queued(bot)
    env.mp.setattr(send_now, "held_by_claude", lambda *a, **k: True)
    env.run(bot._send_queued_line_checked(x1, 900))
    x1.queued_lines[900] = (DM, 77)
    env.run(bot._show_line_unreached(x1, 900, DM, 77))
    return tg.out


@surface("sendnow.taps")
def _sendnow_taps(env):
    """⚡ Send now tapped: each outcome, a bad button, an unknown session,
    someone who may not send, and a slow send."""
    from aipager.bot import send_now
    bot, tg = world(env)
    x1 = _queued(bot)
    for result in ("sent", "taken", "interactive", "held", "typing", "failed"):
        bot._send_now_core = AsyncMock(return_value=send_now.SendNowOutcome(result, "x1"))
        tap(env, bot, tg, _sess_cb(x1, "now:900"), chat=DM)
    tap(env, bot, tg, _sess_cb(x1, "now:abc"), chat=DM)
    tap(env, bot, tg, "claude-zz:now:900", chat=DM)
    can = bot._can_prompt_user
    bot._can_prompt_user = lambda *a, **k: False
    tap(env, bot, tg, _sess_cb(x1, "now:900"), chat=DM)
    bot._can_prompt_user = can
    env.mp.setattr(send_now, "SEND_NOW_TOAST_WAIT", 0)

    async def _slow(sess, msg_id):
        import asyncio
        await asyncio.sleep(0.01)
        return send_now.SendNowOutcome("failed", "x1")
    bot._send_now_core = _slow
    x1.queued_lines[900] = (DM, 77)
    tap(env, bot, tg, _sess_cb(x1, "now:900"), chat=DM, message_id=77)
    return tg.out


@surface("sendnow.replies")
def _sendnow_replies(env):
    """/now: each outcome, and no session to send for."""
    from aipager.bot import send_now
    bot, tg = world(env)
    alive(env)
    cmd(env, bot, tg, "_handle_now_cmd", "/now", chat=DM)
    x1 = _queued(bot)
    bot.registry.set_target(x1.name, DM, OWNER)
    for result in ("sent", "nothing", "interactive", "held", "typing", "failed"):
        bot._send_now_core = AsyncMock(return_value=send_now.SendNowOutcome(result, "x1"))
        cmd(env, bot, tg, "_handle_now_cmd", "/now", chat=DM)
    return tg.out


@surface("taps.gate")
def _taps_gate(env):
    """A tap refused before any handler: another chat's button, a role that
    may not, someone else's card, a bad button, an unknown verb."""
    from aipager.bot import tap_gate
    bot, tg = world(env)
    alive(env)
    x1 = session(bot, "x1", chat=GROUP)
    d1 = session(bot, "x2", chat=DM)
    tap(env, bot, tg, _sess_cb(d1, "menu"), chat=GROUP)
    real = tap_gate.required_capability
    env.mp.setattr(tap_gate, "required_capability",
                   lambda *a, **k: tap_gate.MANAGE)
    bot._member_can = lambda *a, **k: False
    tap(env, bot, tg, _sess_cb(x1, "menu"), chat=GROUP, user=BOB)
    del bot._member_can
    env.mp.setattr(tap_gate, "required_capability", real)
    return tg.out + _owner_taps(env)


def _owner_taps(env):
    from aipager.bot import card_owner
    bot, tg = world(env)
    alive(env)
    x1 = session(bot, "x1", chat=GROUP)
    card = tg._next + 1               # the confirm /kill replies with
    cmd(env, bot, tg, "_handle_kill_cmd", "/kill x1", chat=GROUP)
    start = len(tg.out)
    tap(env, bot, tg, _sess_cb(x1, f"endok{x1.turn_key}"), chat=GROUP, user=BOB,
        message_id=card)
    card_owner.claim(bot, GROUP, 4242, OWNER, kind="ask", command="", picker=True,
                     who=MagicMock(id=OWNER, username="aly"))
    tap(env, bot, tg, "_:pick:cancel", chat=GROUP, user=BOB, message_id=4242)
    tap(env, bot, tg, "nocolon", chat=GROUP)
    tap(env, bot, tg, _sess_cb(x1, "zzz"), chat=GROUP)
    return tg.out[start:]


@surface("chatmove.notice")
def _chatmove_notice(env):
    """The line posted in a group Telegram upgraded, in its own language."""
    from aipager.bot import chat_migration
    new_id = -1009999
    bot, tg = world(env, chats=(DM, GROUP, new_id))
    env.run(chat_migration._post_notice(bot, new_id))
    return tg.out


# ---- the session's notices --------------------------------------------------------------------

def _notice_world(env):
    from aipager.state import Status
    bot, tg = world(env)
    alive(env)
    bot._settle_card_text = AsyncMock(return_value=False)
    bot._edit_busy_raw = AsyncMock(side_effect=lambda mid, text, **k: tg.note(text) or True)
    x1 = session(bot, "x1", chat=GROUP, status=Status.BUSY)
    return bot, tg, x1


@surface("notice.memory_cap")
def _notice_memory_cap(env):
    """The hook or the status line helper over its memory limit, during a
    tool or not, with an owner to report it and without."""
    from aipager.bot import report_flow
    bot, tg, x1 = _notice_world(env)
    for owner in (None, 555):
        env.mp.setattr(report_flow, "resolve_owner", lambda b, o=owner: o)
        env.mp.setattr(report_flow, "report_button_markup", lambda *a, **k: None)
        for hook, tool in (("aipager-hook", "Bash"), ("aipager-hook", ""),
                           ("aipager-statusline", "")):
            env.run(bot.notify(x1, "hook_memory_cap_hit", {"hook": hook, "tool": tool}))
    return tg.out


@surface("notice.queue_timeout")
def _notice_queue_timeout(env):
    bot, tg, x1 = _notice_world(env)
    for preview in ("x1", ""):
        env.run(bot.notify(x1, "queue_pickup", {"consumed": [], "expired": [
            {"raw_text": preview}]}))
    return tg.out


@surface("notice.waiting_reminder")
def _notice_waiting_reminder(env):
    bot, tg, x1 = _notice_world(env)
    for kind, summary in (("question", "x1"), ("permission", "Bash: ls"), ("question", "")):
        env.run(bot.notify(x1, "waiting_reminder", {"kind": kind, "summary": summary}))
    return tg.out


@surface("notice.not_taken")
def _notice_not_taken(env):
    bot, tg, x1 = _notice_world(env)
    env.run(bot.notify(x1, "prompt_not_taken", {"grace": 45.0}))
    return tg.out


@surface("notice.compacting")
def _notice_compacting(env):
    """Compacting on the busy card, and as a new message."""
    from aipager.bot import animation
    bot, tg, x1 = _notice_world(env)

    async def _nap(_s):
        return None
    env.mp.setattr(animation, "_compact_sleep", _nap)
    env.mp.setattr(animation, "COMPACT_ANIMATE_MAX_TICKS", 0)
    bot._animate_compact = AsyncMock()
    x1.busy_msg_id = 31
    env.run(bot.notify(x1, "compacting", {}))
    x1.busy_msg_id = None
    env.run(bot.notify(x1, "compacting", {}))
    return tg.out


@surface("notice.context")
def _notice_context(env):
    bot, tg, x1 = _notice_world(env)
    env.run(bot.notify(x1, "context_warning", {"context_pct": 92}))
    return tg.out


@surface("notice.quiet_turn")
def _notice_quiet_turn(env):
    bot, tg, x1 = _notice_world(env)
    for minutes in (1, 2, 5, 21):
        env.run(bot.notify(x1, "stale_busy", {"minutes": minutes}))
    return tg.out


@surface("notice.compacted")
def _notice_compacted(env):
    from aipager.bot import notify as notify_mod
    bot, tg, x1 = _notice_world(env)
    env.mp.setattr(notify_mod, "COMPACT_DONE_PAUSE_SECONDS", 0)
    x1.busy_msg_id = 31
    env.run(bot.notify(x1, "compact_done", {"before_pct": 88, "after_pct": 12}))
    return tg.out


@surface("notice.compact_timeout")
def _notice_compact_timeout(env):
    bot, tg, x1 = _notice_world(env)
    for seconds in (30, 120, 300, 1260):
        x1.busy_msg_id = 31
        env.run(bot.notify(x1, "compact_timeout", {"elapsed_seconds": float(seconds)}))
    return tg.out


@surface("notice.session_end")
def _notice_session_end(env):
    """A session that ended, for every reason Claude Code gives."""
    bot, tg, x1 = _notice_world(env)
    for source in ("clear", "resume", "logout", "prompt_input_exit",
                   "bypass_permissions_disabled", "disappeared", "other", "unknown",
                   "something_new"):
        x1.busy_msg_id = None
        env.run(bot.notify(x1, "session_end", {"source": source}))
    return tg.out


# ---- Retry, Compact and Clear all ------------------------------------------------------------

@surface("taps.retry")
def _taps_retry(env):
    """Retry: nothing to retry, a session not alive, another person's turn,
    an open prompt (each with its note on the card), retrying, failed."""
    from aipager.state import Status
    bot, tg = world(env)
    alive(env)
    x1 = session(bot, "x1", chat=GROUP, name="x1")
    tap(env, bot, tg, _sess_cb(x1, "retry"), chat=GROUP)
    x1.last_prompt = "x1"
    alive(env, False)
    tap(env, bot, tg, _sess_cb(x1, "retry"), chat=GROUP)
    alive(env, True)
    bot._turn_sender_differs = lambda *a, **k: True
    tap(env, bot, tg, _sess_cb(x1, "retry"), chat=GROUP, text="⚠️ x1")
    del bot._turn_sender_differs
    x1.status = Status.INTERACTIVE
    x1.pending_permission = {"tool_summary": "Bash: ls"}
    tap(env, bot, tg, _sess_cb(x1, "retry"), chat=GROUP, text="⚠️ x1")
    x1.status = Status.IDLE
    x1.pending_permission = {}
    for ok in (True, False):
        bot._inject_prompt = AsyncMock(return_value=ok)
        tap(env, bot, tg, _sess_cb(x1, "retry"), chat=GROUP)
        x1.status = Status.IDLE
    return tg.out


@surface("taps.compact")
def _taps_compact(env):
    bot, tg = world(env)
    alive(env)
    x1 = session(bot, "x1", chat=GROUP, name="x1")
    tap(env, bot, tg, "claude-zz:compact", chat=GROUP)
    alive(env, False)
    tap(env, bot, tg, _sess_cb(x1, "compact"), chat=GROUP)
    alive(env, True)
    for ok in (True, False):
        bot._inject_prompt = AsyncMock(return_value=ok)
        tap(env, bot, tg, _sess_cb(x1, "compact"), chat=GROUP)
    return tg.out


@surface("taps.clear_gone")
def _taps_clear_gone(env):
    """/status's Ended view, Clear all: 1, 2 and 5 cleared, and nothing."""
    from aipager.state import Status
    bot, tg = world(env)
    bot._read_status_file = lambda name: None
    for n in (1, 2, 5):
        for i in range(n):
            g = session(bot, f"g{n}{i}", chat=DM, status=Status.GONE)
            g.gone_at = float(i)
        tap(env, bot, tg, "_:clear_gone", chat=DM)
    tap(env, bot, tg, "_:clear_gone", chat=DM)
    return [t for t in tg.out if not t.startswith(("📊", "⚫", "🆕", "✍️", "⋮"))]


# ---- migration state ---------------------------------------------------------------------------

#: Every phase 3a surface.
P3A_SURFACES: frozenset[str] = frozenset(
    name for name, fn in _shared.SURFACES if fn.__module__ == __name__)

#: The phase 3a surfaces whose migration has landed (a migration commit
#: adds its own); every other one is PENDING, English only until then.
P3A_MIGRATED: frozenset[str] = frozenset({
    "safety.notice",
    "held.late_marker",
    "help.dm", "help.group", "help.group_unnamed", "home.dm", "home.group", "home.empty",
    "route.stop", "route.kill", "route.clearqueue", "route.which_session",
    "route.ended_target", "route.no_session", "route.send_failures", "route.sent_to",
    "route.queue_full", "route.mention_alone", "route.model_refusals", "route.model_feedback",
    "modecard.card", "modecard.cmd_replies", "modecard.perms_flow", "modecard.switch_taps",
    "modecard.switch_results",
    "appcmd.replies", "whoami.replies",
    "media.voice", "media.voice_install", "media.voice_restart", "media.file", "media.album",
    "taps.voice_cancel",
    "newflow.name_card", "newflow.model_page", "newflow.folder_page", "newflow.prompts",
    "newflow.starting", "newflow.ready", "newflow.ready_models", "newflow.ready_taps",
    "newflow.taps", "newflow.closed", "newflow.conflict_card", "newflow.conflict_taps",
    "sess.menu", "sess.end_confirm", "sess.restart", "sess.rename", "sess.delete", "sess.diff",
    "sess.taps", "sess.talk_held", "taps.pick_cancel",
    "sess.resume", "sess.stop_switch", "taps.resume_mode", "taps.kill_cancel",
    "sendnow.line", "sendnow.taps", "sendnow.replies", "taps.gate", "chatmove.notice",
    "notice.memory_cap", "notice.queue_timeout", "notice.waiting_reminder", "notice.not_taken",
    "notice.compacting", "notice.context", "notice.quiet_turn", "notice.compacted",
    "notice.compact_timeout", "notice.session_end",
    "taps.retry", "taps.compact", "taps.clear_gone",
})
P3A_PENDING: frozenset[str] = P3A_SURFACES - P3A_MIGRATED

_shared.PENDING.update(P3A_PENDING)
_shared.MIGRATED.update(P3A_SURFACES - P3A_PENDING)
_shared.KNOWN_EXTRA.update(KNOWN_EXTRA)
