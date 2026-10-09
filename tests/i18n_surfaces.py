"""Every phase 2 surface of the multilingual design, in one list.

Each entry drives one surface through its public entry point (the
keyboard send, the /settings renderers, the menu registration, the busy
card, a prompt and its taps, the answer, the pinned bar, /status, the
language list...) with fixed fake sessions, and returns every text and
button label it produced. The language comes from what is STORED for the
chat (``preferences.set_ui_language``), never from an explicit ``Tr``, so
a surface that forgets to resolve its chat's language shows English and
fails the per-language tests.

One list, four uses: the English snapshot (``tests/golden/
english_surfaces.json``, recorded before any phase 2 change), the pseudo
sweep (``qps``), the Persian and Russian render tests, and check 12.

Clocks are frozen (``NOW``), so every render is deterministic.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

#: The pinned single chat of the test config (``_pin_single_chat_config``).
CHAT = 256113222
#: The group of ``tests/test_separate_permission_prompt._World``.
GROUP = -1001
#: Bob, a member of that group.
BOB = 2
NOW = 1_000_000.0
WALL = 1_800_000_000.0
LABEL = "x1"
GOLDEN = Path(__file__).resolve().parent / "golden" / "english_surfaces.json"

_FROZEN_MODULES = ("aipager.bot.animation", "aipager.bot.dashboard",
                   "aipager.bot.notify", "aipager.bot.callbacks")


class Env:
    """What every surface needs: the bot factory, a loop runner, the
    monkeypatch, and the language stored for the chats it renders in."""

    def __init__(self, mk_bot, run_async, monkeypatch, code: str = "en"):
        self.mk_bot = mk_bot
        self.run = run_async
        self.mp = monkeypatch
        self.code = code
        self._freeze()

    def _freeze(self) -> None:
        import importlib
        import time as _time
        fake = SimpleNamespace(monotonic=lambda: NOW, time=lambda: WALL,
                               sleep=_time.sleep, strftime=_time.strftime,
                               localtime=_time.localtime, gmtime=_time.gmtime)
        for name in _FROZEN_MODULES:
            self.mp.setattr(importlib.import_module(name), "time", fake)

    def store(self, *chats) -> None:
        """Store this env's language for *chats* (nothing for English)."""
        from aipager import preferences
        for chat in chats:
            preferences.set_ui_language(chat, "" if self.code == "en" else self.code)

    def with_tr(self, fn, *args, chat=CHAT, **kwargs):
        """Call a pure renderer that takes ``tr=`` once migrated: the chat's
        language goes in when the function accepts it (English when it does
        not exist yet, which is the base the snapshot is recorded on)."""
        if "tr" in inspect.signature(fn).parameters:
            from aipager import i18n
            kwargs["tr"] = i18n.for_chat(chat)
        return fn(*args, **kwargs)


SURFACES: list[tuple[str, object]] = []


def surface(name):
    def deco(fn):
        SURFACES.append((name, fn))
        return fn
    return deco


def _inline(kb) -> list[str]:
    if kb is None:
        return []
    rows = getattr(kb, "inline_keyboard", None)
    if rows is None:
        rows = getattr(kb, "keyboard", [])
    return [b.text for row in rows for b in row]


def _reply_kb(kb) -> list[str]:
    return [b.text for row in kb.keyboard for b in row]


def _sess(name="claude-x1", label=LABEL, chat=CHAT, **fields):
    from aipager.state import Status, TrackedSession
    sess = TrackedSession(name=name, label=label, status=fields.pop("status", Status.BUSY))
    sess.scope_chat_id = chat
    sess.scope_kind = "dm" if chat > 0 else "group"
    for key, value in fields.items():
        setattr(sess, key, value)
    return sess


def _registered(bot, *sessions):
    for sess in sessions:
        bot.registry._sessions[sess.name] = sess
    return sessions[0] if len(sessions) == 1 else sessions


# ---- the reply keyboard ------------------------------------------------------

def _keyboard(env, level, chat):
    env.store(chat)
    bot = env.mk_bot()
    bot._miniapp_url = "https://example.invalid/app"
    _registered(bot, _sess(chat=chat))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=5))
    env.run(bot._send_keyboard(level=level, chat_id=chat))
    call = bot._app.bot.send_message.await_args
    return [call.args[1], *_reply_kb(call.kwargs["reply_markup"])]


@surface("keyboard.main")
def _kb_main(env):
    return _keyboard(env, "main", CHAT)


@surface("keyboard.main_group")
def _kb_main_group(env):
    return _keyboard(env, "main", GROUP)


@surface("keyboard.templates")
def _kb_templates(env):
    return _keyboard(env, "templates", CHAT)


@surface("keyboard.commands")
def _kb_commands(env):
    return _keyboard(env, "commands", CHAT)


@surface("keyboard.models")
def _kb_models(env):
    return _keyboard(env, "models", CHAT)


# ---- /settings ---------------------------------------------------------------

@surface("settings.root")
def _settings_root(env):
    from aipager.bot import report_flow, settings_menu
    env.store(CHAT)
    out = []
    for value in ("ask", "off", "auto_off"):
        text, kb = settings_menu.render_settings_root(
            CHAT, problem_reports=report_flow.SETTINGS_ROW_LABELS[value])
        out += [text, *_inline(kb)]
    return out


@surface("settings.sections")
def _settings_sections(env):
    from aipager import preferences
    from aipager.bot import settings_menu
    env.store(CHAT)
    out = []
    for section in settings_menu.SECTIONS:
        text, kb = settings_menu.render_settings_section(CHAT, section)
        out += [text, *_inline(kb)]
    # Every root row value, through a second set of preferences.
    preferences.set_preference(CHAT, "layout", "merged")
    preferences.set_preference(CHAT, "card_age_decay", False)
    preferences.set_preference(CHAT, "simple_formatting", True)
    preferences.set_preference(CHAT, "diff_preview", True)
    preferences.set_preference(CHAT, "answer_length", "short")
    preferences.set_preference(CHAT, "language_level", "simple")
    text, kb = settings_menu.render_settings_root(CHAT)
    out += [text, *_inline(kb)]
    return out


@surface("settings.schema")
def _settings_schema(env):
    from aipager.bot import settings_menu
    out = []
    for entry in env.with_tr(settings_menu.settings_schema):
        out.append(entry["title"])
        for option in entry["options"]:
            out += [option["label"], option["help"]]
    return out


def _ns_bot(env):
    from aipager.bot import new_flow
    from aipager.miniapp import launch
    env.store(CHAT)
    env.mp.setattr(new_flow, "_project_dir", lambda: "/work/app")
    env.mp.setattr(launch, "allowed_roots", lambda *a, **k: ["/work/app", "/work/lib"])
    bot = env.mk_bot()
    return bot


@surface("settings.new_sessions")
def _settings_ns(env):
    from aipager import preferences
    from aipager.bot import new_flow
    bot = _ns_bot(env)
    out = []
    for view in ("", "model", "cwd"):
        text, kb = new_flow.render_new_session_defaults(bot, CHAT, view=view)
        out += [text, *_inline(kb)]
    preferences.set_new_session_default(CHAT, "mode", "ask")
    text, kb = new_flow.render_new_session_defaults(bot, CHAT)
    out += [text, *_inline(kb)]
    return out


@surface("settings.per_session")
def _settings_spref(env):
    from aipager.bot import session_parity
    env.store(CHAT)
    bot = env.mk_bot()
    out = []
    text, kb = session_parity._render_session_pref_picker(bot, CHAT)
    out += [text, *_inline(kb)]
    sess = _registered(bot, _sess())
    text, kb = session_parity._render_session_pref_picker(bot, CHAT)
    out += [text, *_inline(kb)]
    sess.layout_override = "replace"
    text, kb = session_parity.render_session_preferences_root(
        sess, CHAT, cb_prefix="_:spref:0")
    out += [text, *_inline(kb)]
    for section in ("layout", "length"):
        text, kb = session_parity.render_session_preferences_field(
            sess, CHAT, section, cb_prefix="_:spref:0")
        out += [text, *_inline(kb)]
    return out


@surface("settings.problem_reports")
def _settings_reports(env):
    from aipager.bot import report_flow
    from aipager.report import store
    env.store(CHAT)
    out = []
    text, kb = env.with_tr(report_flow.render_settings_section)
    out += [text, *_inline(kb)]
    env.mp.setattr(store, "policy_state", lambda: SimpleNamespace(auto_off=True))
    env.mp.setenv("AIPAGER_REPORT_PROMPTS", "0")
    text, kb = env.with_tr(report_flow.render_settings_section)
    out += [text, *_inline(kb)]
    return out


def _settings_bot(env):
    from pathlib import Path as _P

    from aipager.bot import new_flow
    from aipager.miniapp import launch
    from aipager.policy import load_policy
    from aipager.scope import Member, Scope
    env.store(GROUP, 555)
    env.mp.setattr(new_flow, "_project_dir", lambda: "/work/app")
    env.mp.setattr(launch, "allowed_roots", lambda *a, **k: ["/work/app", "/work/lib"])
    bot = env.mk_bot(scopes=[
        Scope(chat_id=GROUP, kind="group", label="team", members=(
            Member(id=555, label="aly", role="owner"),
            Member(id=777, label="bob", role="user"))),
        Scope(chat_id=555, kind="dm", label="aly DM",
              members=(Member(id=555, label="aly", role="owner"),)),
    ])
    bot.policy = load_policy(_P("/nonexistent/policy.yaml"), _P("/nonexistent/policy.d"))
    bot._app.bot.set_my_commands = AsyncMock()
    bot._app.bot.set_chat_menu_button = AsyncMock()
    bot._send_keyboard = AsyncMock()
    return bot


def _tap(bot, env, data, *, chat, user):
    from tests.test_language_setting import tap
    return tap(bot, env.run, data, chat=chat, user=user)


def _query_texts(query) -> list[str]:
    out = [c.args[0] for c in query.answer.await_args_list if c.args and c.args[0]]
    call = query.edit_message_text.await_args
    if call is not None:
        text = call.args[0] if call.args else call.kwargs.get("text")
        if text:
            out.append(text)
        out += _inline(call.kwargs.get("reply_markup"))
    return out


@surface("settings.taps")
def _settings_taps(env):
    bot = _settings_bot(env)
    out = []
    # An admin changes a group setting (the Changed by line), a member is
    # refused, a bogus section and value.
    for data, user in (("_:set:length:short", 555), ("_:set:length:long", 777),
                       ("_:set:bogus", 555), ("_:set:length:huge", 555),
                       ("_:set:ns:mode:ask", 555), ("_:set:ns:mode:auto", 777),
                       ("_:set:ns:model:99", 555)):
        bot._app.bot.edit_message_text = AsyncMock()
        out += _query_texts(_tap(bot, env, data, chat=GROUP, user=user))
        for call in bot._app.bot.edit_message_text.await_args_list:
            out += [call.kwargs.get("text") or call.args[0],
                    *_inline(call.kwargs.get("reply_markup"))]
    return out


# ---- the / menu and the menu button --------------------------------------------

@surface("menu.commands")
def _menu(env):
    env.store(CHAT)
    bot = env.mk_bot()
    _registered(bot, _sess())
    bot._app.bot.set_my_commands = AsyncMock()
    bot._keyboard_deferred = True
    env.run(bot._update_bot_commands())
    commands = bot._app.bot.set_my_commands.await_args.args[0]
    return [f"/{c.command} {c.description}" for c in commands]


@surface("menu.button")
def _menu_button(env):
    env.store(CHAT)
    bot = env.mk_bot()
    bot._miniapp_url = "https://example.invalid/app"
    bot._app.bot.set_chat_menu_button = AsyncMock()
    env.run(bot._set_menu_button_for(CHAT))
    out = [bot._app.bot.set_chat_menu_button.await_args.kwargs["menu_button"].text]
    bot._app.bot.set_chat_menu_button.reset_mock()

    async def _probe(url):
        return True
    from aipager.miniapp import tunnel
    env.mp.setattr(tunnel, "probe_public_url", _probe)
    bot.release_deferred_keyboard = AsyncMock()
    env.run(bot.publish_miniapp_button("https://example.invalid/app"))
    out += [c.kwargs["menu_button"].text
            for c in bot._app.bot.set_chat_menu_button.await_args_list]
    return out


# ---- the busy card ---------------------------------------------------------------

def card_session(chat=CHAT, label=LABEL):
    """A turn with every kind of row the card draws: an older run that
    folds, prose, a settled agent with its own tools, settled shells of
    every outcome, collapsed prompt answers, a failed tool, a live agent,
    a live shell and a tool still running."""
    from aipager import bg_shells
    sess = _sess(chat=chat, label=label)
    sess.busy_started_at = NOW - 250
    sess.cost_baseline = 0.10
    sess.last_cost_usd = 1.34
    rows = [
        ("Read: /src/app.py", True),
        ("Grep: TODO in src", True),
        ("Edit: /src/app.py", True),
        ("🤖 Explore · 3 tool calls · 1:05", True),
        bg_shells.settled_row("npm test", "completed", 90),
        bg_shells.settled_row("make all", "failed", 5, exit_code=2),
        bg_shells.settled_row("make lint", "failed", 65),
        bg_shells.settled_row("sleep 900", "lost", 900),
        bg_shells.settled_row("tail -f log", "stopped", 30),
        ("🔑 Bash: rm -rf build/ → Allowed", True),
        ("🔑 Bash: git push → Denied", True),
        ("❓ Which database? → Selected option 2", True),
        ("❓ Pick some → Selected: A, B", True),
        ("Bash: npm run build", "failed"),
        ("🤖 general-purpose", False),
        ("Bash: npm run dev", False),
        ("Bash: ls", False),
    ]
    sess.tool_history = list(rows)
    sess.stream_commentary = [(3, "Now the agent.")]
    sess.finished_subagents = [{"history_idx": 3, "tools": [
        "Read: /a", "Grep: b", "Glob: *.py"]}]
    sess.active_subagents = {"a1": {"type": "general-purpose", "activity": "Read: /x",
                                    "started_at": NOW - 65, "history_idx": 14,
                                    "tools": ["Read: /x"], "tool_count": 1}}
    sess.bg_shells = {"t1": {"label": "npm run dev", "summary": "Bash: npm run dev",
                             "started": NOW - 30, "in_job": True, "history_idx": 15}}
    return sess


@surface("card.busy")
def _card_busy(env):
    from aipager.bot import animation
    env.store(CHAT)
    sess = card_session()
    return [animation.build_stream_card(sess, "Working")]


@surface("card.final")
def _card_final(env):
    from aipager.bot import animation
    env.store(CHAT)
    sess = card_session()
    return [animation.build_stream_card(sess, animation.FINAL_VERB, final=True)]


@surface("card.waiting")
def _card_waiting(env):
    from aipager.bot import animation
    env.store(CHAT)
    out = []
    sess = card_session()
    sess.active_subagents["a2"] = {"type": "Explore", "started_at": NOW - 5,
                                   "history_idx": None}
    out.append(animation.build_stream_card(sess, "Working", waiting=True))
    sess.active_subagents = {"a1": {"type": "general-purpose", "started_at": NOW - 5}}
    sess.bg_shells = {}
    out.append(animation.build_stream_card(sess, "Working", waiting=True))
    sess.active_subagents = {}
    sess.bg_shells = {"t1": {"label": "x", "started": NOW, "in_job": True},
                      "t2": {"label": "y", "started": NOW, "in_job": True}}
    out.append(animation.build_stream_card(sess, "Working", waiting=True))
    sess.bg_shells = {}
    out.append(animation.build_stream_card(sess, "Working", waiting=True))
    return out


@surface("card.status_lines")
def _card_status_lines(env):
    """The status line alone: a bare turn, one with a cost, the decayed
    unit, and the Waiting verb."""
    from aipager.bot import animation
    env.store(CHAT)
    out = []
    sess = _sess()
    out.append(animation.build_stream_card(sess, "Working"))
    sess.busy_started_at = NOW - 4000
    sess.card_elapsed_unit = "m"
    out.append(animation.build_stream_card(sess, "Working"))
    out.append(animation.build_stream_card(sess, "Waiting"))
    sess.tool_history = [("Read: /a", True), ("Read: /b", True)]
    out.append(animation.build_stream_card(sess, animation.FINAL_VERB, final=True))
    return out


@surface("card.legacy")
def _card_legacy(env):
    env.store(CHAT)
    bot = env.mk_bot()
    out = []
    sess = card_session()
    sess.subagent_count_this_turn = 2
    sess.tool_history = [(f"Read: /f{i}", True) for i in range(20)] + list(sess.tool_history)
    sess.active_subagents["a1"]["history_idx"] = len(sess.tool_history) - 3
    sess.pending_permission = {"tool_summary": "Bash: rm -rf build/",
                               "tool_info": {"name": "Bash", "detail": "rm -rf build/ && ls"}}
    out.append(bot._build_busy_text(LABEL, "Waiting", sess))
    sess.pending_permission = {"ask_question": True, "question": "Which one?",
                               "options": [{"label": "A", "description": "first"}, {}]}
    out.append(bot._build_busy_text(LABEL, "Waiting", sess))
    sess.pending_permission = {}
    sess.subagent_count_this_turn = 1
    sess.tool_history = [(f"Read: /f{i}", True) for i in range(16)]
    out.append(bot._build_busy_text(LABEL, "Working", sess))
    sess.pending_permission = {"tool_info": {"name": "Bash"}}
    out.append(bot._build_busy_text(LABEL, "Waiting", sess))
    return out


@surface("card.full_log")
def _card_full_log(env):
    from aipager.bot import animation
    env.store(CHAT)
    sess = card_session()
    agents = [{"type": "Explore", "elapsed": 65.0, "tool_count": 3,
               "tools": ["Read: /a", "Grep: b", "Glob: *.py"]},
              {"type": "general-purpose", "elapsed": 1.0, "tool_count": 1, "tools": []}]
    out = [env.with_tr(animation.build_full_log, LABEL, sess.tool_history,
                       sess.stream_commentary, "The answer.", agents=agents)]
    out.append(env.with_tr(animation.build_full_log, LABEL, [("Read: /a", True)], [], ""))
    return out


@surface("card.thinking")
def _card_thinking(env):
    env.store(CHAT)
    bot = env.mk_bot()
    sess = _registered(bot, _sess())
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=7))
    env.run(bot.send_busy(sess))
    call = bot._app.bot.send_message.await_args
    return [call.args[1], *_inline(call.kwargs["reply_markup"])]


class _Limiter:
    """A flood limiter that says the chat is in every regime."""

    def __init__(self, *, warning=1, hourly=True, minimal=True):
        self.warning, self.hourly, self.minimal = warning, hourly, minimal

    def warning_remaining(self, chat):
        return self.warning

    def hourly_usage(self, chat):
        return {"minimal": self.hourly}

    def minimal_mode(self, chat):
        return self.minimal


@surface("card.paused")
def _card_paused(env):
    from aipager.bot import animation
    env.store(CHAT)
    bot = env.mk_bot()
    sess = _registered(bot, _sess())
    sess.busy_msg_id = 11
    env.mp.setattr(animation, "get_rate_limiter", lambda: _Limiter())
    bot._edit_busy_raw = AsyncMock(return_value=True)
    env.run(bot._render_paused_card(sess))
    out = [bot._edit_busy_raw.await_args.args[1]]
    sent = AsyncMock(return_value={"message_id": 12})
    env.mp.setattr(animation, "send_rich_message", sent)
    sess.stream_last_rendered = animation._PAUSED_CARD_TEXT
    env.run(bot._send_card_copy(sess, 9, verb="Working", final=False, waiting=False))
    out.append(sent.await_args.args[1])
    return out


@surface("card.compacting")
def _card_compacting(env):
    from aipager.bot import animation
    env.store(CHAT)
    bot = env.mk_bot()
    sess = _registered(bot, _sess())
    sess.busy_msg_id = 11

    async def _nap(_s):
        return None
    env.mp.setattr(animation, "_compact_sleep", _nap)
    env.mp.setattr(animation, "COMPACT_ANIMATE_MAX_TICKS", 3)
    sess.stack_top_kind = lambda: "compacting"
    bot._edit_busy_raw = AsyncMock(return_value=True)
    env.run(bot._animate_compact(sess))
    return [c.args[1] for c in bot._edit_busy_raw.await_args_list]


# ---- the background agents line ---------------------------------------------------

@surface("agents_line")
def _agents_line(env):
    from aipager.bot import agents_line
    env.store(CHAT)
    out = []
    for markdown in (True, False):
        def run(fn, *a, **k):
            return env.with_tr(fn, *a, markdown=markdown, **k)
        out += [
            run(agents_line.running_line, ["Explore"]),
            run(agents_line.running_line, ["Explore", "general_purpose"]),
            run(agents_line.running_line, ["a", "b", "c", "d", "e"]),
            run(agents_line.running_line, ["a", "npm run dev"], kinds=["agent", "shell"]),
            run(agents_line.running_line, ["sleep 9"], kinds=["shell"]),
            run(agents_line.running_line, ["s1", "s2"], kinds=["shell", "shell"]),
            run(agents_line.done_line, ["Explore"], 372),
            run(agents_line.done_line, ["sleep 90"], 90, kinds=["shell"]),
            run(agents_line.done_line, ["a", "b"], 3700),
            run(agents_line.done_line, ["a", "s"], 61, kinds=["agent", "shell"]),
        ]
    return out


# ---- permission and question prompts, taps and answered lines -------------------------

def _keyboards(env, chat):
    env.store(chat)
    bot = env.mk_bot()
    sess = _registered(bot, _sess(chat=chat))
    sess.pending_permission = {"tool_info": {"always_available": True}}
    out = []
    out += _inline(bot._build_permission_keyboard(sess, always_available=True))
    out += _inline(bot._build_permission_keyboard(sess, always_available=False))
    for kb in (bot._build_stop_keyboard(sess), bot._build_retry_keyboard(sess),
               bot._build_compact_keyboard(sess), bot._build_perms_confirm_keyboard(sess),
               bot._build_perms_busy_keyboard(sess),
               bot._build_resume_mode_keyboard(sess, True, chat_id=chat),
               bot._build_resume_mode_keyboard(sess, False, chat_id=chat),
               bot._separate_permission_keyboard(sess)):
        out += _inline(kb)
    opts = [{"label": "Postgres", "description": "the usual"}, {"description": "x"},
            {"label": "SQLite"}]
    out += _inline(bot._build_inline_ask_keyboard(sess, opts))
    out += _inline(bot._build_inline_ask_keyboard(sess, opts, multi_select=True,
                                                  selected={0}))
    for tool_input in ({"questions": []},
                       {"questions": [{"question": "Which database?", "options": opts}]},
                       {"questions": [{"question": "Free text?", "options": []}]}):
        text, kb = bot._build_ask_keyboard(sess, LABEL, tool_input)
        out += [text, *_inline(kb)]
    for question in ("Continue?", ""):
        text, kb = bot._build_selector_keyboard(sess, LABEL, question,
                                                [(1, "Yes"), (2, "No")])
        out += [text, *_inline(kb)]
    return out


@surface("prompt.keyboards")
def _prompt_keyboards(env):
    return _keyboards(env, CHAT)


@surface("prompt.detail")
def _prompt_detail(env):
    from aipager.bot import transport
    env.store(CHAT)
    out = []
    for summary, detail in (("Bash: list files", "ls -la /tmp && echo done"),
                            ("Bash: x ⟨U+202E⟩", "evil"),
                            ("Bash: ⟨U+202E⟩ ok", ""),
                            ("Bash: ls", "ls")):
        out.append(env.with_tr(transport._format_perm_detail, summary, detail))
    return out


def _world(env):
    from tests.test_separate_permission_prompt import _World
    env.store(GROUP, 555)
    world = _World(env.mk_bot, env.mp)
    return world


@surface("prompt.separate")
def _prompt_separate(env):
    """The prompt sent as its own message, through notify, and each tap on
    it: the toast, the edit and the attributed line."""
    out = []
    for verb, tool_info in (
            ("allow", None),
            ("deny", None),
            ("allow_always", None),
            ("allow_always", {"name": "Bash", "summary": "Bash: ls", "input": {},
                              "standing_rule_suggestion": {
                                  "type": "addRules", "rules": [
                                      {"toolName": "Bash", "ruleContent": "ls"}],
                                  "behavior": "allow", "destination": "localSettings"}}),
            ("opt1", {"name": "AskUserQuestion", "summary": "AskUserQuestion", "input": {
                "questions": [{"question": "Which database?",
                               "options": [{"label": "Postgres"}, {"label": "SQLite"}]}]}}),
    ):
        world = _world(env)
        sess = world.session()
        world.bot._app.bot.send_message.reset_mock()
        env.run(world.bot.notify(sess, "permission_prompt", {
            "tool_info": tool_info or {"name": "Bash", "summary": "Bash: rm -rf build/",
                                       "input": {"command": "rm -rf build/"},
                                       "detail": "rm -rf build/"},
            "hook_reply": {"addr": "/x", "nonce": "n"}}))
        for text, _reply in world.lines(GROUP):
            out.append(text)
        call = world.bot._app.bot.send_message.await_args
        out += _inline(call.kwargs.get("reply_markup"))
        world.bot._app.bot.send_message.reset_mock()
        query = world.tap(env.run, GROUP, BOB, verb)
        # The edit keeps the tapped message's own text (the fixture's) and
        # adds the answer under it: only the addition is ours.
        out += [t.replace("🔐 api · Permission needed\n\n", "")
                for t in _query_texts(query)]
        out += [t for t, _r in world.lines(GROUP)]
    return out


@surface("prompt.inline")
def _prompt_inline(env):
    """The prompt drawn on the busy card, through notify, and a tap."""
    out = []
    for verb in ("allow", "deny", "stop"):
        world = _world(env)
        sess = world.session()
        sess.busy_msg_id = 42
        sess.busy_started_at = NOW - 30
        env.run(world.bot.notify(sess, "permission_prompt", {
            "tool_info": {"name": "Bash", "summary": "Bash: ls", "input": {}},
            "hook_reply": {"addr": "/x", "nonce": "n"}}))
        call = world.bot._edit_busy_raw.await_args
        out += [call.args[1], *_inline(call.kwargs.get("reply_markup"))]
        world.bot._edit_busy_raw.reset_mock()
        query = world.tap(env.run, GROUP, BOB, verb, msg_id=42)
        out += _query_texts(query)
        out += [t for t, _r in world.lines(GROUP)]
        for c in world.bot._edit_busy_raw.await_args_list:
            out += [c.args[1], *_inline(c.kwargs.get("reply_markup"))]
    return out


@surface("prompt.question_inline")
def _prompt_question_inline(env):
    out = []
    for multi, taps in ((False, ["opt1"]), (True, ["opt0", "opt1", "submit"]),
                        (True, ["submit"])):
        world = _world(env)
        sess = world.session()
        sess.busy_msg_id = 42
        env.run(world.bot.notify(sess, "permission_prompt", {
            "tool_info": {"name": "AskUserQuestion", "input": {"questions": [{
                "question": "Which database?", "multiSelect": multi,
                "options": [{"label": "Postgres"}, {"label": "SQLite"}]}]}}}))
        call = world.bot._edit_busy_raw.await_args
        out += [call.args[1], *_inline(call.kwargs.get("reply_markup"))]
        for verb in taps:
            query = world.tap(env.run, GROUP, BOB, verb, msg_id=42)
            out += _query_texts(query)
        out += [t for t, _r in world.lines(GROUP)]
    return out


@surface("prompt.loading")
def _prompt_loading(env):
    world = _world(env)
    sess = world.session()
    sess.busy_msg_id = 42
    env.run(world.bot.notify(sess, "permission_prompt", {
        "tool_info": {"name": "AskUserQuestion", "input": {"questions": []}}}))
    call = world.bot._edit_busy_raw.await_args
    out = [call.args[1], *_inline(call.kwargs.get("reply_markup"))]
    world2 = _world(env)
    sess2 = world2.session()
    env.run(world2.bot.notify(sess2, "permission_prompt", {}))
    out += [t for t, _r in world2.lines(GROUP)]
    return out


@surface("prompt.stop_taps")
def _prompt_stop_taps(env):
    from aipager.bot import callbacks
    env.store(CHAT)
    out = []
    for label, dropped, by in ((LABEL, 0, ""), (LABEL, 1, ""), (LABEL, 3, "@bob")):
        outcome = SimpleNamespace(label=label, dropped=dropped, by=by, ok=True)
        out.append(env.with_tr(callbacks._stopped_line, outcome))
        out.append(env.with_tr(callbacks._stopped_line, outcome, html=False))
    return out


# ---- the answer ---------------------------------------------------------------------

def _rich(env):
    rich = AsyncMock(return_value={"message_id": 77})
    env.mp.setattr("aipager.bot.notify.send_rich_message", rich)
    return rich


@surface("answer.headers")
def _answer_headers(env):
    from aipager.state import Status
    env.store(CHAT)
    out = []
    for elapsed, added, removed in ((250, 10, 5), (250, 0, 0), (0, 3, 0)):
        rich = _rich(env)
        bot = env.mk_bot()
        sess = _registered(bot, _sess(status=Status.IDLE))
        sess.busy_started_at = NOW - elapsed if elapsed else 0.0
        sess.busy_started_wall = 0.0
        sess.last_lines_added, sess.last_lines_removed = added, removed
        bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
        bot._maybe_update_bot_name = AsyncMock()
        env.run(bot.notify(sess, "idle_prompt", {"summary": "done"}))
        out.append(rich.await_args.args[1])
    # The answer under its finished card: the short result line.
    rich = _rich(env)
    bot = env.mk_bot()
    sess = _registered(bot, _sess(status=Status.IDLE))
    sess.busy_started_at = NOW - 9
    sess.busy_msg_id = 31
    sess.tool_history = [("Read: /a", True)]
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    bot._edit_busy_rich = AsyncMock(return_value=True)
    env.run(bot.notify(sess, "idle_prompt", {"summary": "done"}))
    out += [c.args[1] for c in rich.await_args_list]
    # A long answer goes as a file under a header.
    rich = _rich(env)
    bot = env.mk_bot()
    sess = _registered(bot, _sess(status=Status.IDLE))
    sess.busy_started_at = NOW - 9
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.send_document = AsyncMock(return_value=MagicMock(message_id=98))
    bot._maybe_update_bot_name = AsyncMock()
    env.run(bot.notify(sess, "idle_prompt", {"summary": "word " * 8000}))
    out += [c.args[1] for c in bot._app.bot.send_message.await_args_list]
    out += [c.kwargs.get("caption") or "" for c in bot._app.bot.send_document.await_args_list]
    return out


@surface("answer.job_ends")
def _answer_job_ends(env):
    from aipager.state import Status
    env.store(CHAT)
    out = []
    for event, context in (("job_grace_expired", {}), ("job_agents_lost", {}),
                           ("job_agents_lost", {"what": "shell"})):
        for elapsed in (250, 0):
            bot = env.mk_bot()
            sess = _registered(bot, _sess(status=Status.IDLE))
            sess.busy_started_at = NOW - elapsed if elapsed else 0.0
            bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
            bot._maybe_update_bot_name = AsyncMock()
            bot._settle_card_text = AsyncMock(return_value=False)
            bot._drain_after_job = AsyncMock()
            env.run(bot.notify(sess, event, dict(context)))
            out += [c.args[1] for c in bot._app.bot.send_message.await_args_list]
    return out


# ---- the pinned bar ------------------------------------------------------------------

def _pinned(env, sessions, *, limiter=None, url=False, chat=CHAT):
    from aipager.bot import dashboard
    env.store(chat)
    env.mp.setattr(dashboard, "CHAT_ID", str(chat))
    env.mp.setattr(dashboard, "get_rate_limiter", lambda: limiter)
    bot = env.mk_bot()
    if url:
        bot._miniapp_url = "https://example.invalid/app"
    for sess in sessions:
        _registered(bot, sess)
    if len(sessions) > 1:
        bot.registry.set_target(chat, sessions[0]) if hasattr(
            bot.registry, "set_target") else None
    text, kb = bot._render_pinned(chat, list(sessions))
    return [text, *_inline(kb)]


@surface("pinned")
def _pinned_bar(env):
    from aipager.state import Status
    out = []
    one = _sess()
    one.status = Status.IDLE
    out += _pinned(env, [one], url=True)
    busy = _sess()
    busy.bg_agents = {"a": {"label": "Explore"}}
    busy.bg_shells = {"t": {"label": "npm"}, "u": {"label": "x"}}
    out += _pinned(env, [busy])
    busy1 = _sess()
    busy1.bg_agents = {"a": {"label": "Explore"}, "b": {"label": "Plan"}}
    out += _pinned(env, [busy1])
    shell1 = _sess()
    shell1.bg_shells = {"t": {"label": "npm"}}
    out += _pinned(env, [shell1])
    waiting = _sess(status=Status.INTERACTIVE)
    waiting.pending_permission = {"tool_summary": "Bash: rm -rf build/ && make everything again"}
    waiting.bg_agents = {"a": {"label": "Explore"}}
    other = _sess(name="claude-x2", label="x2", status=Status.INTERACTIVE)
    other.pending_permission = {"ask_question": True, "question": "Which?"}
    third = _sess(name="claude-x3", label="x3", status=Status.BUSY)
    out += _pinned(env, [waiting, other, third])
    out += _pinned(env, [waiting])
    a = _sess(name="claude-a", label="a", status=Status.BUSY)
    b = _sess(name="claude-b", label="b", status=Status.IDLE)
    c = _sess(name="claude-c", label="c", status=Status.UNKNOWN)
    d = _sess(name="claude-d", label="d", status=Status.BUSY)
    out += _pinned(env, [a, b, c, d])
    e = _sess(name="claude-e", label="e", status=Status.IDLE)
    f = _sess(name="claude-f", label="f", status=Status.IDLE)
    out += _pinned(env, [e, f])
    out += _pinned(env, [], limiter=_Limiter())
    out += _pinned(env, [], limiter=_Limiter(warning=0, hourly=False, minimal=True))
    return out


@surface("pinned.target")
def _pinned_target(env):
    from aipager.bot import dashboard
    from aipager.state import Status
    env.store(CHAT)
    env.mp.setattr(dashboard, "CHAT_ID", str(CHAT))
    env.mp.setattr(dashboard, "get_rate_limiter", lambda: None)
    bot = env.mk_bot()
    a = _sess(name="claude-a", label="a", status=Status.BUSY)
    b = _sess(name="claude-b", label="b", status=Status.IDLE)
    _registered(bot, a, b)
    env.mp.setattr(bot.registry, "target_for", lambda chat, user=None: a)
    text, kb = bot._render_pinned(CHAT, [a, b])
    return [text, *_inline(kb)]


@surface("pinned.answer_taps")
def _pinned_answer_taps(env):
    from aipager.bot import dashboard
    env.store(CHAT)
    out = []
    for outcome in (dashboard.RESEND_NOT_WAITING, dashboard.RESEND_NOT_RESENDABLE,
                    dashboard.RESEND_BUSY):
        bot = env.mk_bot()
        sess = _registered(bot, _sess())
        bot._resend_pending_prompt = AsyncMock(return_value=outcome)
        bot._safe_answer = AsyncMock()
        query = MagicMock()
        query.message = MagicMock()
        query.message.chat = MagicMock(id=CHAT)
        query.message.chat_id = CHAT
        query.from_user = MagicMock(id=CHAT, language_code=None)
        env.run(bot._pinned_answer(query, sess))
        out.append(bot._safe_answer.await_args.args[1])
    bot = env.mk_bot()
    bot._safe_answer = AsyncMock()
    query = MagicMock()
    query.message = None
    query.from_user = MagicMock(id=CHAT, language_code=None)
    env.run(bot._pinned_answer(query, _sess()))
    out.append(bot._safe_answer.await_args.args[1])
    return out


# ---- /status ------------------------------------------------------------------------------

def _status_bot(env, chat=CHAT):
    env.store(chat)
    env.mp.setattr("aipager.bot.dashboard.queue_depth_parts", lambda sess: (
        getattr(sess, "_q", (0, 0))))
    bot = env.mk_bot()
    bot._read_status_file = lambda name: (
        {"model": "Opus 4.7", "ctx_pct": 45, "cost": 1234.5} if name == "claude-x1" else None)
    return bot


def _status_sessions():
    from aipager.state import Status
    x1 = _sess()
    x1.busy_started_at = NOW - 250
    x1.tool_history = [("Read: /src/app.py", True), ("🤖 Explore · 3 tool calls · 1:05", True)]
    x1.active_subagents = {"a": {"type": "Explore"}}
    x1.bg_shells = {"t": {"label": "npm"}, "u": {"label": "x"}}
    x1._q = (2, 1)
    x2 = _sess(name="claude-x2", label="x2", status=Status.INTERACTIVE)
    x2.pending_permission = {"tool_summary": "Bash: ls"}
    x2._q = (1, 0)
    x3 = _sess(name="claude-x3", label="x3", status=Status.IDLE)
    x3.model_name = "sonnet"
    x3.last_token_pct = 12
    x3.active_subagents = {"a": {"type": "Explore"}, "b": {"type": "Plan"}}
    x3.bg_shells = {"t": {"label": "npm"}}
    x4 = _sess(name="claude-x4", label="x4", status=Status.GONE)
    x4.gone_at = 5.0
    x4.claude_session_id = "abc"
    x5 = _sess(name="claude-x5", label="x5", status=Status.GONE)
    x5.gone_at = 4.0
    return x1, x2, x3, x4, x5


@surface("status.list")
def _status_list(env):
    out = []
    bot = _status_bot(env)
    _registered(bot, *_status_sessions())
    update = MagicMock()
    update.effective_chat = MagicMock(id=CHAT, type="private")
    bot._miniapp_url = "https://example.invalid/app"
    text, kb = bot._render_status_list(CHAT, update)
    out += [text, *_inline(kb)]
    text, kb = bot._render_ended_view(CHAT)
    out += [text, *_inline(kb)]
    many = env.mk_bot()
    for i in range(12):
        s = _sess(name=f"claude-g{i}", label=f"g{i}")
        from aipager.state import Status
        s.status = Status.GONE
        s.gone_at = float(i)
        _registered(many, s)
    text, kb = many._render_ended_view(CHAT)
    out += [text, *_inline(kb)]
    empty = env.mk_bot()
    for render in (empty._render_status_list, empty._render_ended_view):
        text, kb = render(CHAT)
        out += [text, *_inline(kb)]
    only_gone = env.mk_bot()
    from aipager.state import Status
    _registered(only_gone, _sess(name="claude-z", label="z", status=Status.GONE))
    text, kb = only_gone._render_status_list(CHAT)
    out += [text, *_inline(kb)]
    return out


@surface("status.switch")
def _status_switch(env):
    from aipager.state import Status
    out = []
    for chat in (CHAT, GROUP):
        bot = _status_bot(env, chat)
        for status in (Status.IDLE, Status.INTERACTIVE, Status.BUSY):
            sess = _registered(bot, _sess(chat=chat, status=status))
            sess.skip_perms = status == Status.BUSY
            text, kb = bot._render_switch_reply(chat, sess)
            out += [text, *_inline(kb)]
    return out


# ---- the language list -------------------------------------------------------------------

@surface("language.list")
def _language_list(env):
    from aipager import i18n
    from aipager.bot import language_menu
    env.store(CHAT, GROUP)
    out = []
    for chat, private in ((CHAT, True), (GROUP, False)):
        tr = i18n.for_chat(chat)
        text, kb = language_menu.render_language_menu(
            tr, private=private, current=language_menu.current_choice(chat, private=private))
        out += [text, *_inline(kb)]
        out.append(language_menu.confirmation(tr, tr.code))
        out.append(language_menu.row_text(tr, tr.code))
    return out


# ---- running them ---------------------------------------------------------------------------

def render_all(mk_bot, run_async, monkeypatch, code: str = "en",
               names=None) -> dict[str, list[str]]:
    """Every surface (or those in *names*) rendered with *code* stored for
    its chats."""
    out: dict[str, list[str]] = {}
    for name, fn in SURFACES:
        if names is not None and name not in names:
            continue
        env = Env(mk_bot, run_async, monkeypatch, code)
        out[name] = [str(t) for t in fn(env)]
    return out


def dump(renders: dict[str, list[str]]) -> str:
    return json.dumps(renders, ensure_ascii=True, indent=1, sort_keys=True) + "\n"


def load_golden() -> dict[str, list[str]]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


# ---- the pseudo sweep ----------------------------------------------------------------------

#: The surfaces whose every word is now a key: rendered under ``qps``,
#: nothing English may be left unmarked (check 11's pseudo half). A
#: surface joins when its migration lands.
MIGRATED: set[str] = {
    "keyboard.main", "keyboard.main_group", "keyboard.templates", "keyboard.commands",
    "keyboard.models", "language.list",
    "settings.root", "settings.sections", "settings.schema", "settings.new_sessions",
    "settings.per_session", "settings.problem_reports", "settings.taps",
    "menu.commands", "menu.button",
    "card.busy", "card.final", "card.waiting", "card.status_lines", "card.legacy",
    "card.full_log", "card.thinking", "card.paused", "card.compacting", "prompt.detail",
    "agents_line", "prompt.keyboards", "prompt.separate", "prompt.inline",
    "prompt.question_inline", "prompt.loading", "prompt.stop_taps",
    "answer.headers", "answer.job_ends",
    "pinned", "pinned.target", "pinned.answer_taps",
    "status.list", "status.switch",
}

#: Words that are names, not text: session labels, models, tool rows'
#: own words, option labels from Claude, agent types, shell commands.
KNOWN = (
    "x1", "x2", "x3", "x4", "x5", "api", "Sonnet", "Opus", "Haiku", "Fable", "OpusPlan",
    "Postgres", "SQLite", "Explore", "general-purpose", "general_purpose", "general\\_purpose", "Plan",
    "npm", "run", "make", "sleep", "tail", "log", "lint", "all", "build", "dev", "test", "ls",
    "rm", "-rf", "git", "push", "tidy",
    "Yes", "No", "Continue?", "Which database?", "Which one?", "Which?", "Pick some", "Free text?",
    "first", "evil", "ok", "the usual", "English", "Pseudo", "Pseudo RTL", "done",
    "The answer.", "Now the agent.", "example.invalid", "https", "ctx",
)

_MARKED = re.compile(r"⟦.*?⟧", re.S)
_NOT_TEXT = re.compile(
    r"`+[^`]*`+|<code\b[^>]*>.*?</code>|<pre\b[^>]*>.*?</pre>|<[^>]+>|&\w+;"
    r"|(?<!\w)/[\w./*-]+|@\w+|\S+\.(?:py|md)\b|\d+", re.S)


def unmarked_words(text: str, known=KNOWN) -> list[str]:
    """ASCII words outside the pseudo markers, after removing what is
    never translated (code, tags, commands, paths, mentions, numbers,
    names)."""
    from aipager.i18n import core
    rest = _MARKED.sub(" ", text)
    rest = _NOT_TEXT.sub(" ", rest)
    for word in sorted(set(known) | set(core.NEVER_TRANSLATE), key=len, reverse=True):
        rest = re.sub(rf"(?<![\w-]){re.escape(word)}(?![\w-])", " ", rest)
    return re.findall(r"[A-Za-z]{2,}", rest)
