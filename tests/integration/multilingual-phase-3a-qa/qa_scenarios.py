"""Black-box scenario drivers for the phase 3a tester (conversation and
sessions in Persian and Russian).

Each scenario drives the bot only through what entrypoints.md lists as the
public surface (the command handlers, ``_handle_message``, the callback
dispatcher, ``notify``, ``flush_held_answers`` and the exported module
functions) and returns every text the bot showed, in order, plus what
Claude was sent (``inject.send_keys``) and what the audit recorded.

The module is self-contained and only uses names that exist on the base
branch too: re-recording ``golden_base.json`` (``qa_golden.py``) copies it
into an archive of the base tree and runs it there; the English
byte-identity test runs it here and compares with that file.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

#: The commit this branch grew from (its merge-base with the integration
#: branch): what "today" means for English and for safety.py's imports.
#: Its outputs are recorded in golden_base.json (qa_golden.py).
BASE = "5a1f9290dccf4f245fe30f196c7a1d5033e90368"
REPO = Path(__file__).resolve().parents[3]


def clean_env(home, pythonpath):
    """A subprocess environment with a throwaway HOME and none of the
    operator's aipager, XDG or pytest variables."""
    import os
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("XDG_", "AIPAGER_", "CLAUDE_TG_", "PYTEST_"))}
    env.update(HOME=str(home), PYTHONPATH=str(pythonpath), PYTHONDONTWRITEBYTECODE="1")
    return env


DM = 555            # aly's private chat (its id is aly's id)
DM_B = 2            # bob's private chat
GROUP = -1001
OWNER = 555
BOB = 2
BOT_NAME = "aipager_test_bot"


# ---- a recording Telegram ------------------------------------------------------

def _labels(kb):
    if kb is None:
        return []
    rows = getattr(kb, "inline_keyboard", None)
    if rows is None:
        rows = getattr(kb, "keyboard", None) or []
    return ["[btn] " + str(b.text) for row in rows for b in row]


class Tg:
    """Every text sent, edited or popped up, in call order (button labels
    follow their message, prefixed ``[btn]``; pop-ups prefixed ``[toast]``)."""

    def __init__(self):
        self.out: list[str] = []
        self._next = 7000

    def note(self, text, kb=None, prefix=""):
        if text is not None and text != "":
            self.out.append(prefix + str(text))
        self.out += _labels(kb)

    def message(self, chat):
        self._next += 1
        m = MagicMock()
        m.message_id = self._next
        m.chat_id = chat
        m.chat = MagicMock(id=chat, type="private" if chat > 0 else "supergroup")

        async def _edit(*args, **kwargs):
            self.note(args[0] if args else kwargs.get("text"), kwargs.get("reply_markup"))
            return m
        m.edit_text = AsyncMock(side_effect=_edit)
        return m

    def replier(self, chat):
        async def _reply(*args, **kwargs):
            self.note(args[0] if args else kwargs.get("text"), kwargs.get("reply_markup"))
            return self.message(chat)
        return AsyncMock(side_effect=_reply)

    def wire(self, bot):
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
        tg.set_my_commands = AsyncMock()
        tg.delete_my_commands = AsyncMock()
        tg.set_chat_menu_button = AsyncMock()


# ---- the harness -------------------------------------------------------------------

class H:
    """What a scenario gets: the bot factory, a loop runner, monkeypatch."""

    def __init__(self, mk_bot, run, mp, tmp):
        self.mk_bot = mk_bot
        self.run = run
        self.mp = mp
        self.tmp = Path(tmp)
        self.keys: list[str] = []       # what Claude was sent
        self.audit: list[str] = []      # what the audit recorded

    def alive(self, value=True):
        from aipager.dtach import inject

        async def _keys(*args, **kwargs):
            self.keys.append(repr((args[1:], sorted(kwargs.items()))))
            return True
        self.mp.setattr(inject, "is_alive", AsyncMock(return_value=value))
        self.mp.setattr(inject, "send_keys", AsyncMock(side_effect=_keys))
        self.mp.setattr(inject, "send_text_and_enter", AsyncMock(side_effect=_keys))
        self.mp.setattr(inject, "list_sessions", AsyncMock(return_value=[]))

    def record_audit(self):
        from aipager import audit

        def _append(**kw):
            self.audit.append(repr(sorted((k, v) for k, v in kw.items()
                                          if k in ("action", "summary", "reason", "tool"))))
        self.mp.setattr(audit, "append", _append)


def scopes():
    from aipager.scope import Member, Scope
    return [
        Scope(chat_id=GROUP, kind="group", label="team", members=(
            Member(id=OWNER, label="aly", role="owner"),
            Member(id=BOB, label="bob", role="user"))),
        Scope(chat_id=DM, kind="dm", label="aly DM",
              members=(Member(id=OWNER, label="aly", role="owner"),)),
        Scope(chat_id=DM_B, kind="dm", label="bob DM",
              members=(Member(id=BOB, label="bob", role="owner"),)),
    ]


def make_bot(h, *, username=BOT_NAME):
    from aipager.policy import load_policy
    bot = h.mk_bot(scopes=scopes())
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
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
    bot._read_status_file = lambda name: None
    h.alive()
    return bot, tg


def session(bot, label="x1", *, chat=DM, status=None):
    from aipager.state import Status
    kind = "d" if chat > 0 else "g"
    s = bot.registry.get_or_create(f"claude-{label}__{kind}{abs(chat)}")
    s.label = label
    s.scope_chat_id, s.scope_kind = chat, ("dm" if chat > 0 else "group")
    s.status = status if status is not None else Status.IDLE
    return s


def _user(uid):
    u = MagicMock(id=uid, language_code=None, is_bot=False)
    u.username = {OWNER: "aly", BOB: "bob"}.get(uid, "someone")
    u.first_name = u.username
    return u


def msg(tg, text, *, chat=DM, user=OWNER, reply_to=None):
    update = MagicMock()
    m = update.message = MagicMock()
    m.text = text
    m.caption = None
    m.message_id = 900
    m.reply_text = tg.replier(chat)
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
    m.parse_entities = lambda types=None: {}
    m.parse_caption_entities = lambda types=None: {}
    m.from_user = _user(user)
    update.effective_user = m.from_user
    update.effective_chat = m.chat
    update.callback_query = None
    return update


def query(tg, data, *, chat=DM, user=OWNER, message_id=42, text=""):
    q = MagicMock()
    q.data = data

    async def _answer(*args, **kwargs):
        tg.note(args[0] if args else kwargs.get("text"), prefix="[toast] ")
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
    q.message.reply_markup = None
    q.from_user = _user(user)
    update = MagicMock()
    update.callback_query = q
    update.message = None
    update.effective_user = q.from_user
    update.effective_chat = q.message.chat
    return update, q


def tap(h, bot, tg, data, **kw):
    update, q = query(tg, data, **kw)
    h.run(bot._handle_callback(update, MagicMock()))
    return q


def cmd(h, bot, tg, handler, text, **kw):
    update = msg(tg, text, **kw)
    h.run(getattr(bot, handler)(update, MagicMock()))
    return update


def ctx():
    c = MagicMock()
    c.bot.id = 999
    return c


def choose(h, bot, chat, code, *, user=None):
    """The chat's own person (or the group's owner) picks its language the
    way a person does: the ``_:set:lang:<code>`` tap in that chat."""
    if code == "en":
        return
    user = user if user is not None else (chat if chat > 0 else OWNER)
    scratch = Tg()
    update, q = query(scratch, f"_:set:lang:{code}", chat=chat, user=user)
    h.run(bot._handle_callback(update, MagicMock()))


def world(h, lang="en", *, chats=(DM, GROUP), username=BOT_NAME):
    bot, tg = make_bot(h, username=username)
    for chat in chats:
        choose(h, bot, chat, lang)
    return bot, tg


# ---- scenarios ------------------------------------------------------------------------
#
# Each takes (h, lang) and returns a list of strings. The English
# byte-identity test runs every one with lang="en" on the base tree and here.

SCENARIOS: dict = {}


def scenario(fn):
    SCENARIOS[fn.__name__] = fn
    return fn


def _help(h, lang, chat, username):
    bot, tg = world(h, lang, username=username)
    cmd(h, bot, tg, "_handle_help_cmd", "/help", chat=chat)
    return tg.out


@scenario
def help_dm(h, lang):
    return _help(h, lang, DM, BOT_NAME)


@scenario
def help_group(h, lang):
    return _help(h, lang, GROUP, BOT_NAME)


@scenario
def help_group_unnamed(h, lang):
    return _help(h, lang, GROUP, "")


@scenario
def home_screens(h, lang):
    """/start: nothing yet; a target in the DM; in a group; an ended one."""
    from aipager.state import Status
    bot, tg = world(h, lang)
    cmd(h, bot, tg, "_handle_start_cmd", "/start", chat=DM)
    x1 = session(bot, "x1", chat=DM)
    bot.registry.set_target(x1.name, DM, OWNER)
    cmd(h, bot, tg, "_handle_start_cmd", "/start", chat=DM)
    g1 = session(bot, "g1", chat=GROUP)
    bot.registry.set_target(g1.name, GROUP, OWNER)
    cmd(h, bot, tg, "_handle_start_cmd", "/start", chat=GROUP)
    gone = session(bot, "x3", chat=DM, status=Status.GONE)
    gone.claude_session_id = "abc"
    gone.gone_at = 5.0
    cmd(h, bot, tg, "_handle_start_cmd", "/start", chat=DM)
    return tg.out


@scenario
def stop_replies(h, lang):
    from aipager.state import Status
    bot, tg = world(h, lang)
    cmd(h, bot, tg, "_handle_stop_cmd", "/stop", chat=DM)
    session(bot, "x1", chat=DM)
    cmd(h, bot, tg, "_handle_stop_cmd", "/stop zz", chat=DM)
    cmd(h, bot, tg, "_handle_stop_cmd", "/stop x1", chat=DM)
    session(bot, "x2", chat=DM, status=Status.BUSY)
    session(bot, "x3", chat=DM, status=Status.BUSY)
    cmd(h, bot, tg, "_handle_stop_cmd", "/stop", chat=DM)
    return tg.out


@scenario
def kill_replies(h, lang):
    bot, tg = world(h, lang)
    cmd(h, bot, tg, "_handle_kill_cmd", "/kill", chat=DM)
    cmd(h, bot, tg, "_handle_kill_cmd", "/kill zz", chat=DM)
    x1 = session(bot, "x1", chat=DM)
    cmd(h, bot, tg, "_handle_kill_cmd", "/kill x1", chat=DM)
    tap(h, bot, tg, f"{x1.name}:kill-cancel", chat=DM)
    session(bot, "x2", chat=DM)
    cmd(h, bot, tg, "_handle_kill_cmd", "/kill", chat=DM)
    tap(h, bot, tg, "_:pick:cancel", chat=DM)
    return tg.out


@scenario
def session_commands(h, lang):
    """/restart, /rename, /delete and /diff with nothing to act on, a name
    nobody has, one session (its confirm or outcome); the ⋮ menu of a live
    and an ended session; /resume of an ended one with no transcript."""
    from aipager.bot import session_parity
    from aipager.state import Status
    bot, tg = world(h, lang)

    def run(fn, text):
        h.run(fn(bot, msg(tg, text, chat=DM), MagicMock()))
    for fn, verb in ((session_parity.handle_restart_cmd, "/restart"),
                     (session_parity.handle_rename_cmd, "/rename"),
                     (session_parity.handle_delete_cmd, "/delete"),
                     (session_parity.handle_diff_cmd, "/diff")):
        run(fn, verb)
        run(fn, verb + " zz")
    x1 = session(bot, "x1", chat=DM)
    run(session_parity.handle_restart_cmd, "/restart")
    run(session_parity.handle_delete_cmd, "/delete x1")
    x3 = session(bot, "x3", chat=DM, status=Status.GONE)
    run(session_parity.handle_delete_cmd, "/delete")
    tap(h, bot, tg, f"{x1.name}:menu", chat=DM)
    tap(h, bot, tg, f"{x3.name}:menu", chat=DM)
    tap(h, bot, tg, f"{x1.name}:menu-close", chat=DM)
    cmd(h, bot, tg, "_handle_resume_cmd", "/resume x3", chat=DM)
    return tg.out


#: Counts at the plural boundaries, plus a large one (thousands grouping).
COUNTS = (0, 1, 2, 5, 11, 12, 21, 22, 25, 1234)


@scenario
def clearqueue_counts(h, lang):
    bot, tg = world(h, lang)
    cmd(h, bot, tg, "_handle_clearqueue_cmd", "/clearqueue", chat=DM)
    x1 = session(bot, "x1", chat=DM)
    bot.registry.set_target(x1.name, DM, OWNER)
    for n in COUNTS:
        bot._clear_queue_core = AsyncMock(return_value=SimpleNamespace(ok=n > 0, dropped=n))
        cmd(h, bot, tg, "_handle_clearqueue_cmd", "/clearqueue", chat=DM)
    return tg.out


@scenario
def mode_replies(h, lang):
    bot, tg = world(h, lang)
    cmd(h, bot, tg, "_handle_mode_cmd", "/mode", chat=DM)
    cmd(h, bot, tg, "_handle_mode_cmd", "/mode zz", chat=DM)
    x1 = session(bot, "x1", chat=DM)
    bot.registry.set_target(x1.name, DM, OWNER)
    cmd(h, bot, tg, "_handle_mode_cmd", "/mode", chat=DM)
    x1.perm_mode = "full"
    cmd(h, bot, tg, "_handle_mode_cmd", "/mode x1", chat=DM)
    cmd(h, bot, tg, "_handle_mode_cmd", "/mode x1 auto", chat=DM)
    session(bot, "x2", chat=DM)
    cmd(h, bot, tg, "_handle_mode_cmd", "/mode ask", chat=DM)
    cmd(h, bot, tg, "_handle_mode_cmd", "/mode x1 ask extra", chat=DM)
    return tg.out


@scenario
def resume_now_whoami_app(h, lang):
    bot, tg = world(h, lang)
    cmd(h, bot, tg, "_handle_resume_cmd", "/resume zz", chat=DM)
    cmd(h, bot, tg, "_handle_now_cmd", "/now", chat=DM)
    cmd(h, bot, tg, "_handle_app_cmd", "/app", chat=GROUP)
    cmd(h, bot, tg, "_handle_whoami", "/whoami", chat=GROUP)
    cmd(h, bot, tg, "_handle_whoami", "/whoami", chat=GROUP, user=BOB)
    return tg.out


def _new_world(h, lang):
    from aipager.bot import new_flow
    from aipager.miniapp import launch
    from aipager.state import Status
    bot, tg = world(h, lang)
    h.mp.setattr(new_flow, "_project_dir", lambda: "/work/app")
    h.mp.setattr(launch, "allowed_roots", lambda *a, **k: ["/work/app", "/work/lib"])
    h.mp.setattr(launch, "default_folder_is_wide", lambda: False)

    async def _create(label, *, scope_chat_id, mode="ask", cwd=None,
                      driver_user_id=None, model=None, keep_card=False, reuse_name=None):
        sess = session(bot, label, chat=scope_chat_id)
        sess.perm_mode = mode
        sess.status = Status.IDLE
        return sess.name, ""
    bot.create_session = _create
    return bot, tg


@scenario
def new_flow_cards(h, lang):
    """/new: the Name card (DM, group), its model and folder pages, then
    /new x1 (Starting and Ready) and /new x1 again (the conflict card)."""
    bot, tg = _new_world(h, lang)
    cmd(h, bot, tg, "_handle_new_cmd", "/new", chat=DM)
    card = tg._next
    tap(h, bot, tg, "_:nw:opt:model", chat=DM, message_id=card)
    tap(h, bot, tg, "_:nw:opt:path", chat=DM, message_id=card)
    tap(h, bot, tg, "_:nw:cancel", chat=DM, message_id=card)
    cmd(h, bot, tg, "_handle_new_cmd", "/new", chat=GROUP)
    cmd(h, bot, tg, "_handle_new_cmd", "/new x1", chat=DM)
    cmd(h, bot, tg, "_handle_new_cmd", "/new x1", chat=DM)
    return tg.out


def _notice_world(h, lang):
    from aipager.state import Status
    bot, tg = world(h, lang)
    bot._settle_card_text = AsyncMock(return_value=False)
    bot._edit_busy_raw = AsyncMock(side_effect=lambda mid, text, **k: tg.note(text) or True)
    x1 = session(bot, "x1", chat=GROUP, status=Status.BUSY)
    return bot, tg, x1


#: Minutes quiet at the plural boundaries (and an hour, a day).
MINUTES = (1, 2, 5, 11, 12, 21, 22, 25, 60, 1440)


@scenario
def notice_quiet_turn(h, lang):
    bot, tg, x1 = _notice_world(h, lang)
    for minutes in MINUTES:
        h.run(bot.notify(x1, "stale_busy", {"minutes": minutes}))
    return tg.out


@scenario
def notice_context(h, lang):
    bot, tg, x1 = _notice_world(h, lang)
    for pct in (0, 1, 80, 92, 100):
        h.run(bot.notify(x1, "context_warning", {"context_pct": pct}))
    return tg.out


@scenario
def notice_compact(h, lang):
    from aipager.bot import notify as notify_mod
    bot, tg, x1 = _notice_world(h, lang)
    h.mp.setattr(notify_mod, "COMPACT_DONE_PAUSE_SECONDS", 0)
    x1.busy_msg_id = 31
    h.run(bot.notify(x1, "compact_done", {"before_pct": 88, "after_pct": 12}))
    for seconds in (30, 59, 60, 61, 90, 119, 120, 300, 1260):
        x1.busy_msg_id = 31
        h.run(bot.notify(x1, "compact_timeout", {"elapsed_seconds": float(seconds)}))
    return tg.out


SOURCES = ("clear", "resume", "logout", "prompt_input_exit", "bypass_permissions_disabled",
           "disappeared", "other", "unknown", "something_new", "")


@scenario
def notice_session_end(h, lang):
    bot, tg, x1 = _notice_world(h, lang)
    for source in SOURCES:
        x1.busy_msg_id = None
        h.run(bot.notify(x1, "session_end", {"source": source}))
    return tg.out


@scenario
def notice_misc(h, lang):
    """Not taken, a queued message not confirmed, the waiting reminder,
    the memory cap (owner or not, hook or status line)."""
    from aipager.bot import report_flow
    bot, tg, x1 = _notice_world(h, lang)
    for grace in (1.0, 45.0, 120.0):
        h.run(bot.notify(x1, "prompt_not_taken", {"grace": grace}))
    for preview in ("fix x1 & <b>", ""):
        h.run(bot.notify(x1, "queue_pickup", {"consumed": [], "expired": [
            {"raw_text": preview}]}))
    for kind, summary in (("question", "Pick 2?"), ("permission", "Bash: ls"),
                          ("question", "")):
        h.run(bot.notify(x1, "waiting_reminder", {"kind": kind, "summary": summary}))
    for owner in (None, 555):
        h.mp.setattr(report_flow, "resolve_owner", lambda b, o=owner: o)
        h.mp.setattr(report_flow, "report_button_markup", lambda *a, **k: None)
        for hook, tool in (("aipager-hook", "Bash"), ("aipager-hook", ""),
                           ("aipager-statusline", "")):
            h.run(bot.notify(x1, "hook_memory_cap_hit", {"hook": hook, "tool": tool}))
    return tg.out


def safety_reasons():
    """(tool, reason) for safety.py's sentences, each from its real writer."""
    from aipager import safety
    out = []
    no = ("/srv/secret/**",)
    out.append(("Bash", safety.bash_violation("claude --resume x", (r"\bclaude\b",))))
    out.append(("Bash", safety.bash_violation("grep -r x ~", safety.DENY_BASH_SEARCH_PATTERNS)))
    out.append(("WebSearch", safety.tool_violation("WebSearch", (), ("Read",))))
    out.append(("Bash", safety.tool_violation("Bash", ("Bash",), ())))
    out.append(("Read", safety.path_violation("Read", {"file_path": 7}, no, ())))
    out.append(("Read", safety.path_violation("Read", {"file_path": "/srv/secret/a2.txt"},
                                              no, ())))
    out.append(("Write", safety.path_violation("Write", {"file_path": "/srv/keep2/a.txt"}, (),
                                               ("/srv/keep2/**",))))
    out.append(("Edit", safety.path_violation("Edit", {"file_path": "/w/p/.git/config"}, (),
                                              (), cwd="/w/p", write_roots=("/w/p",))))
    out.append(("Write", safety.path_violation("Write", {"file_path": "/elsewhere/a"}, (), (),
                                               cwd="/w/p", write_roots=("/w/p",))))
    out.append(("Write", safety.path_violation("Write", {"file_path": "/elsewhere/a"}, (), (),
                                               cwd="/w/p", write_roots=("/w/s",),
                                               wide_cwd=True)))
    out.append(("Grep", safety.path_violation("Grep", {"path": "/elsewhere"}, (), (),
                                              cwd="/w/p", write_roots=("/w/p",))))
    out.append(("Grep", safety.path_violation("Grep", {"path": "/elsewhere"}, (), (),
                                              cwd="/w/p", write_roots=("/w/s",),
                                              wide_cwd=True)))
    out.append(("Grep", safety.path_violation("Grep", {"path": 3}, (), (),
                                              cwd="/w/p", write_roots=("/w/p",))))
    out.append(("Grep", safety.path_violation("Grep", {"path": "/w/p/sub"}, ("/w/p/sub/**",),
                                              (), cwd="/w/p", write_roots=("/w/p",))))
    out.append(("Grep", safety.path_violation("Grep", {"path": "/w/p"}, ("**/.env",), (),
                                              cwd="/w/p", write_roots=("/w/p",))))
    out.append(("Grep", safety.path_violation("Grep", {"path": "/w/p"}, ("/w/p/sub/**",), (),
                                              cwd="/w/p", write_roots=("/w/p",))))
    for glob in (5, "a\\b", "/abs/*", "../up/*"):
        out.append(("Grep", safety.path_violation("Grep", {"path": "/w/p", "glob": glob}, (),
                                                  (), cwd="/w/p", write_roots=("/w/p",))))
    out.append(("Grep", safety.path_violation("Grep", {"path": 4}, (), (), cwd="/w/p")))
    out.append(("Glob", safety.path_violation("Glob", {"path": "/w/p", "pattern": 9}, (), (),
                                              cwd="/w/p")))
    out.append(("Grep", safety.path_violation("Grep", {"path": "/w/p"}, ("**/.env",), (),
                                              cwd="/w/p")))
    out.append(("Grep", safety.path_violation("Grep", {"path": "/srv"}, ("/srv/secret/**",),
                                              (), cwd="/w/p")))
    return out


@scenario
def safety_reason_strings(h, lang):
    """What Claude reads when a tool is denied: every writer's sentence,
    the two exported constants, and the deny JSON the hook prints."""
    from aipager import safety
    from aipager.dtach import enforce
    reasons = [r for _, r in safety_reasons()] + [safety.BASH_REASON,
                                                  safety.BASH_SEARCH_REASON]
    closed = enforce.fail_closed({"session": "claude-x__g100", "tool_name": "Bash"}) or {}
    reasons.append(str(closed.get("reason", "")))
    return reasons + [enforce.deny_decision_json(r) for r in reasons]


#: Sentences aipager never writes, shown as received (HTML metacharacters
#: included, to see they are escaped exactly as before).
UNKNOWN_REASONS = ("the moon is <b>full</b> & bright",
                   "Read with an unreadable path.",
                   "something else entirely 42")


@scenario
def safety_blocks(h, lang):
    """Every writer's reason, then unknown ones, each on a fresh session,
    then a sticky "session halted" repeat on the last."""
    from aipager.state import Status
    bot, tg = world(h, lang)
    h.record_audit()
    reasons = safety_reasons() + [("Bash", r) for r in UNKNOWN_REASONS]
    x = None
    for i, (tool, reason) in enumerate(reasons):
        x = session(bot, f"s{i}", chat=GROUP, status=Status.BUSY)
        x.busy_msg_id = None
        h.run(bot.notify(x, "safety_blocked", {"tool": tool, "reason": reason}))
    h.run(bot.notify(x, "safety_blocked", {
        "tool": "Bash", "reason": "session halted after a safety block; resume to continue"}))
    return tg.out + ["[audit] " + a for a in h.audit] + ["[keys] " + k for k in h.keys]


#: Seconds an answer was held, around every rounding edge.
HELD_SECONDS = (0, 1, 29, 30, 31, 59, 60, 89, 90, 91, 149, 151, 3600, 7200, -5)


def _held_entry(seconds, *, attempts=0, key="k"):
    return SimpleNamespace(
        session="claude-x1__d555", label="x1", chat_id=DM, key=key,
        rich_text="The answer.", plain_text="The answer.", reply_to=None,
        attempts=attempts, held_seconds=lambda: float(seconds))


@scenario
def held_answers(h, lang):
    from aipager.bot import held as held_mod
    from aipager.bot import notify as notify_mod
    bot, tg = world(h, lang)
    x1 = session(bot, "x1", chat=DM)
    bot._confirm_held = MagicMock()
    h.mp.setattr(held_mod.HELD, "drop", lambda chat, key: None)
    out = []

    async def _rich(chat, text, **kw):
        out.append("[rich] " + text)
        return {"message_id": 1}

    async def _refused(chat, text, **kw):
        raise RuntimeError("bad markdown")
    h.mp.setattr(notify_mod, "send_rich_message", AsyncMock(side_effect=_rich))
    for seconds in HELD_SECONDS:
        h.mp.setattr(held_mod.HELD, "pending", lambda chat, s=seconds: [_held_entry(s)])
        h.run(bot.flush_held_answers(x1))
    h.mp.setattr(notify_mod, "send_rich_message", AsyncMock(side_effect=_refused))
    for seconds in (60, 660):
        h.mp.setattr(held_mod.HELD, "pending", lambda chat, s=seconds: [_held_entry(
            s, attempts=held_mod.HELD_ANSWER_MAX_ATTEMPTS - 1)])
        h.run(bot.flush_held_answers(x1))
    return out + tg.out


@scenario
def gate_and_dispatcher_taps(h, lang):
    """A tap refused before any handler (another chat's button, someone
    else's card, a malformed or unknown button), and the generic ones."""
    from aipager.bot import card_owner
    bot, tg = world(h, lang, chats=(DM, DM_B, GROUP))
    x1 = session(bot, "x1", chat=GROUP)
    d1 = session(bot, "x2", chat=DM)
    tap(h, bot, tg, f"{d1.name}:menu", chat=GROUP, user=BOB)
    card = tg._next + 1
    cmd(h, bot, tg, "_handle_kill_cmd", "/kill x1", chat=GROUP)
    tap(h, bot, tg, f"{x1.name}:endok{x1.turn_key}", chat=GROUP, user=BOB, message_id=card)
    card_owner.claim(bot, GROUP, 4242, OWNER, kind="ask", command="", picker=True,
                     who=MagicMock(id=OWNER, username="aly"))
    tap(h, bot, tg, "_:pick:cancel", chat=GROUP, user=BOB, message_id=4242)
    tap(h, bot, tg, "nocolon", chat=GROUP, user=BOB)
    tap(h, bot, tg, f"{x1.name}:zzz", chat=GROUP, user=BOB)
    tap(h, bot, tg, "claude-zz:compact", chat=GROUP, user=BOB)
    tap(h, bot, tg, f"{x1.name}:retry", chat=GROUP, user=BOB)
    tap(h, bot, tg, "__voice__:cancel", chat=DM)
    return tg.out


@scenario
def clear_gone_counts(h, lang):
    from aipager.state import Status
    bot, tg = world(h, lang)
    for n in (1, 2, 5, 11, 21, 22):
        for i in range(n):
            g = session(bot, f"g{n}x{i}", chat=DM, status=Status.GONE)
            g.gone_at = float(i)
        start = len(tg.out)
        tap(h, bot, tg, "_:clear_gone", chat=DM)
        tg.out[start:] = [t for t in tg.out[start:] if t.startswith("[toast]")]
    tap(h, bot, tg, "_:clear_gone", chat=DM)
    return [t for t in tg.out if t.startswith("[toast]")]


@scenario
def media_files(h, lang):
    """A file too big (its name kept), a failed download, a voice note too
    big, without the voice extra, heard, and heard nothing."""
    from aipager.bot import handlers
    from aipager.bot import voice as voice_mod
    bot, tg = world(h, lang)
    x1 = session(bot, "x1", chat=DM)
    bot.registry.set_target(x1.name, DM, OWNER)
    h.mp.setattr(handlers, "_DOWNLOAD_BACKOFF_SECONDS", (0, 0, 0))
    big = msg(tg, None, chat=DM)
    big.message.document = MagicMock(file_size=31 * 1024 * 1024, file_name="report_2024.py")
    h.run(bot._handle_file(big, ctx()))
    failing = msg(tg, None, chat=DM)
    failing.message.document = MagicMock(file_size=10, file_name="notes 7.txt")
    failing.message.document.get_file = AsyncMock(side_effect=RuntimeError("13"))
    h.run(bot._handle_file(failing, ctx()))

    def voice_msg(size=1000):
        update = msg(tg, None, chat=DM)
        v = MagicMock()
        v.file_size = size
        f = MagicMock()
        f.download_to_drive = AsyncMock()
        v.get_file = AsyncMock(return_value=f)
        update.message.voice = v
        return update
    h.mp.setattr(voice_mod, "is_available", lambda: False)
    h.run(bot._handle_voice(voice_msg(), ctx()))
    h.mp.setattr(voice_mod, "is_available", lambda: True)
    h.run(bot._handle_voice(voice_msg(size=31 * 1024 * 1024 + 1), ctx()))
    for heard in ("", "fix bug 42 in x1 & <tag>"):
        h.mp.setattr(voice_mod, "transcribe", AsyncMock(return_value=heard))
        h.run(bot._handle_voice(voice_msg(), ctx()))
    return tg.out + ["[keys] " + k for k in h.keys]


@scenario
def claude_bytes(h, lang):
    """What Claude is sent (``[keys]``): plain text to the target, a /x1
    message, a voice transcript and a file with its caption; then what the
    chat saw."""
    from aipager.bot import voice as voice_mod
    bot, tg = world(h, lang)
    x1 = session(bot, "x1", chat=DM)
    bot.registry.set_target(x1.name, DM, OWNER)
    h.run(bot._handle_message(msg(tg, "hello 42 & <b>", chat=DM), ctx()))
    x1.status = type(x1.status).IDLE
    h.run(bot._handle_message(msg(tg, "/x1 second 7", chat=DM), ctx()))
    x1.status = type(x1.status).IDLE
    h.mp.setattr(voice_mod, "is_available", lambda: True)
    h.mp.setattr(voice_mod, "transcribe", AsyncMock(return_value="spoken 3"))
    v = msg(tg, None, chat=DM)
    voice = MagicMock(file_size=1000)
    f = MagicMock()
    f.download_to_drive = AsyncMock()
    voice.get_file = AsyncMock(return_value=f)
    v.message.voice = voice
    h.run(bot._handle_voice(v, ctx()))
    x1.status = type(x1.status).IDLE
    doc = msg(tg, None, chat=DM)
    doc.message.caption = "look at 5"
    tg_file = MagicMock()

    async def _download(custom_path=None, **kw):
        Path(custom_path).write_text("x")
    tg_file.download_to_drive = AsyncMock(side_effect=_download)
    doc.message.document = MagicMock(file_size=10, file_name="notes_7.txt",
                                     mime_type="text/plain")
    doc.message.document.get_file = AsyncMock(return_value=tg_file)
    h.run(bot._handle_file(doc, ctx()))
    # The saved file's folder, time stamp and de-duplicating suffix differ
    # per run: the rest of what Claude reads is compared.
    keys = [re.sub(r"\S*/aipager-files/\d+_notes_7(-\d+)?\.txt",
                   "<files>/<ts>_notes_7.txt", k) for k in h.keys]
    return ["[keys] " + k for k in keys] + tg.out


@scenario
def typed_model_command(h, lang):
    """`/model opus` typed while the target works, then while it idles."""
    from aipager.state import Status
    bot, tg = world(h, lang)
    x1 = session(bot, "x1", chat=DM, status=Status.BUSY)
    bot.registry.set_target(x1.name, DM, OWNER)
    h.run(bot._handle_message(msg(tg, "/model opus", chat=DM), ctx()))
    x1.status = Status.IDLE
    h.run(bot._handle_message(msg(tg, "/model opus", chat=DM), ctx()))
    return tg.out + ["[keys] " + k for k in h.keys]


@scenario
def ready_card_model_refusals(h, lang):
    """The Ready card's model buttons refused: a turn running, a prompt
    open, the state unknown, an ended session."""
    from aipager.state import Status
    bot, tg = _new_world(h, lang)
    cmd(h, bot, tg, "_handle_new_cmd", "/new x5", chat=GROUP)
    sess = bot.registry.find_by_label("x5", GROUP)
    start = len(tg.out)
    for status, extra in ((Status.BUSY, {}),
                          (Status.INTERACTIVE, {"pending_permission": {"tool_summary": "x"}}),
                          (Status.UNKNOWN, {})):
        sess.status = status
        for key, value in extra.items():
            setattr(sess, key, value)
        tap(h, bot, tg, f"{sess.name}:rdy_m1", chat=GROUP)
    return [t for t in tg.out[start:] if t.startswith("[toast]")]


@scenario
def exported_renderers(h, lang):
    """The exported renderers called without a Tr (English by contract)."""
    from aipager.bot import card_owner, notify, session_parity
    bot, tg = world(h, lang)
    out = []
    for hook in ("aipager-hook", "aipager-statusline", "weird<hook>"):
        for owner in (True, False):
            out.append(notify.memory_cap_body(hook, owner))
    card_owner.claim(bot, GROUP, 4243, OWNER, kind="ask", command="/kill x1", picker=False,
                     who=MagicMock(id=OWNER, username="aly"))
    out.append(repr(card_owner.refusal(bot, GROUP, 4243, BOB, "claude-x1__g1001", "endok")))
    out.append(repr(card_owner.refusal(bot, GROUP, 4243, OWNER, "claude-x1__g1001", "endok")))
    x1 = session(bot, "x1", chat=DM)
    x2 = session(bot, "x2", chat=DM)
    for made in (session_parity.session_picker(bot, DM, [x1, x2], "Stop"),
                 session_parity.render_end_confirm(bot, DM, x1)):
        if asyncio.iscoroutine(made):
            made = h.run(made)
        # The turn key in callback_data is a clock reading: not compared.
        out.append(re.sub(r"(endok)\d+", r"\1N", repr(made)))
    return out + tg.out


def constants():
    """Exported constants entrypoints.md says equal today's English."""
    from aipager.bot import chat_migration, handlers, held, held_message, send_now, tap_gate
    out = {
        "handlers.HELP_TEXT": handlers.HELP_TEXT,
        "handlers.MODEL_SWITCH_OTHER_SENDER_REASON": handlers.MODEL_SWITCH_OTHER_SENDER_REASON,
        "handlers.APP_IN_GROUP_TEXT": handlers.APP_IN_GROUP_TEXT,
        "handlers.APP_NO_DM_TEXT": handlers.APP_NO_DM_TEXT,
        "tap_gate.REFUSED_TEXT": tap_gate.REFUSED_TEXT,
        "tap_gate.OTHER_CHAT_TEXT": tap_gate.OTHER_CHAT_TEXT,
        "chat_migration.NOTICE": chat_migration.NOTICE,
        "held_message.TOO_OLD": held_message.TOO_OLD,
        "held.LATE_MARKER": held.LATE_MARKER,
    }
    for name in sorted(dir(send_now)):
        if name in ("LINE_TEXT", "BUTTON_TEXT", "LINE_TEXT_UNREACHED") or \
                name.startswith(("TOAST_", "REPLY_")):
            out["send_now." + name] = getattr(send_now, name)
    return out


#: Every scenario's output per language, rendered once per session: the
#: outputs are plain strings and deterministic, and many tests read the
#: same one (the safety scenario alone takes seconds).
_OUT: dict = {}


def output(name: str, lang: str, h_factory) -> list:
    """Scenario *name*'s output in *lang* (cached), with the harness's
    temporary folder written as ``<tmp>``."""
    key = (name, lang)
    if key not in _OUT:
        h = h_factory()
        _OUT[key] = [t.replace(str(h.tmp), "<tmp>") for t in SCENARIOS[name](h, lang)]
    return list(_OUT[key])


def drain(coro):
    """A coroutine and two loop turns after it (fire-and-forget sends)."""
    async def _run():
        result = await coro
        for _ in range(3):
            await asyncio.sleep(0)
        return result
    return _run()
