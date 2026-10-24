"""Every migrated surface of the multilingual design (phases 2 and 3b),
in one list.

Each entry drives one surface through its public entry point (the
keyboard send, the /settings renderers, the menu registration, the busy
card, a prompt and its taps, the answer, the pinned bar, /status, the
language list...) with fixed fake sessions, and returns every text and
button label it produced. The language comes from what is STORED for the
chat (``preferences.set_ui_language``), never from an explicit ``Tr``, so
a surface that forgets to resolve its chat's language shows English and
fails the per-language tests.

One list, four uses: the English snapshot (``tests/golden/
english_surfaces.json``, each surface recorded before its phase changed
anything), the pseudo
sweep (``qps``), the Persian and Russian render tests, and check 12.

Clocks are frozen (``NOW``), so every render is deterministic.
"""

from __future__ import annotations

import inspect
import json
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aipager.i18n.locales import LOCALES
from tests.footer_text import unfooter

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
#: Chats and people no surface renders for (check 12's decoys).
DECOY_CHATS = (77, -77)
DECOY_PEOPLE = (77, 78)

#: The English surfaces that show a permission mode's label. Since
#: 2026-10-10 (operator) the label names the mode as a mode ("💬 Ask" ->
#: "💬 Ask mode"), so these differ from goldens recorded on older bases,
#: and only by that: see :func:`without_mode_labels`.
MODE_LABEL_SURFACES = frozenset({
    "miniapp.mirrors", "modecard.card", "modecard.cmd_replies", "modecard.perms_flow",
    "modecard.switch_taps", "newflow.closed", "newflow.conflict_taps", "newflow.name_card",
    "newflow.ready", "newflow.ready_models", "newflow.ready_taps", "newflow.starting",
    "newflow.taps", "prompt.keyboards", "sess.taps", "settings.new_sessions",
    "settings.taps", "status.switch",
})

#: The English surfaces the three permission modes (2026-10-11) changed on
#: purpose: Auto is the default, the admin gate moved to Full access, a
#: third mode on every card and button, every mode named as a mode, the
#: in-place switch's texts, and /help's /mode line naming all three.
#: Recorded in english_surfaces.json; a surface outside this set changing
#: is a defect.
THREE_MODES_SURFACES = frozenset({
    "help.dm", "help.group", "help.group_unnamed", "menu.commands", "miniapp.details", "miniapp.mirrors", "miniapp.payload_texts",
    "modecard.card", "modecard.cmd_replies", "modecard.perms_flow", "modecard.switch_taps",
    "newflow.closed", "newflow.conflict_taps", "newflow.name_card", "newflow.ready",
    "newflow.ready_models", "newflow.ready_taps", "newflow.starting", "newflow.taps",
    "prompt.keyboards", "sess.taps", "settings.new_sessions", "settings.taps",
    "transport.texts",
})
#: The surface the three modes added (what the confirming hook does to a
#: switch's card).
THREE_MODES_NEW_SURFACES = frozenset({"modecard.switch_results"})
#: The English surface the reply keyboard's App button left (2026-10-12:
#: a Mini App opened from a keyboard button gets no init data). It
#: differs from goldens recorded on older bases by that button only: see
#: :func:`with_keyboard_app_removed`.
NO_KEYBOARD_APP_SURFACES = frozenset({"keyboard.main"})
#: The surface Restart now without a service unit added (2026-10-12): the
#: installed-and-waiting check, the manual steps, the restart job, the
#: hooks notice. New, so goldens recorded on older bases do not have it.
RESTART_NOW_NEW_SURFACES = frozenset({"update.restart"})
#: The busy card's surfaces the agents tree (2026-10-14) changed: a
#: divider above the status line, and nothing else (see
#: :func:`without_card_divider`).
AGENTS_TREE_CARD_SURFACES = frozenset({"card.busy", "card.final", "card.status_lines",
                                       "card.waiting"})
#: Every English surface the agents tree changed: those, and the block
#: under an answer (a 🤖 on each row, six rows, the tree).
AGENTS_TREE_SURFACES = AGENTS_TREE_CARD_SURFACES | frozenset({"agents_line"})
#: The surface the agents tree added (a helper's rows on the card).
AGENTS_TREE_NEW_SURFACES = frozenset({"card.helpers"})
#: The settings menus /settings → 🎨 Appearance (2026-10-15) changed: one
#: "🎨 Appearance" row, and nothing else (see :func:`without_appearance_row`).
APPEARANCE_SURFACES = frozenset({"settings.root", "settings.sections"})
#: The surface it added (the Appearance page and each choice's page).
APPEARANCE_NEW_SURFACES = frozenset({"settings.appearance"})


def without_card_divider(value):
    """*value* (a card surface's list of texts) as goldens recorded on older
    bases have it: without the divider above each card's status line, and
    (2026-10-15) with that status line in Markdown again rather than drawn
    as a footer block (small text), and nothing else changed."""
    return [unfooter(v).replace("\n\n---\n\n", "\n\n") for v in value]


#: The card surfaces the "never end with a fold" fix (2026-10-24) changed:
#: an agent's newest tool call stays plain after its fold, and a fold
#: needs three calls besides it, so the sample Explore agent's three calls
#: show plain where they were folded (see :func:`with_agent_calls_folded`).
CARD_FOLD_TAIL_SURFACES = frozenset({"card.busy", "card.final", "card.waiting"})
_EXPLORE_CALLS_PLAIN = "\u2705 `Read: /a`\n\n\u2705 `Grep: b`\n\n\u2705 `Glob: *.py`"
_EXPLORE_CALLS_FOLDED = ("<details><summary>\u25b8 3 tool calls</summary>\n\n"
                         f"{_EXPLORE_CALLS_PLAIN}\n\n</details>")


def with_agent_calls_folded(value):
    """*value* (a card surface's list of texts) as goldens recorded before
    2026-10-24 have it: the sample Explore agent's three tool calls behind
    a "3 tool calls" fold again, and nothing else changed."""
    return [v.replace(_EXPLORE_CALLS_PLAIN, _EXPLORE_CALLS_FOLDED) for v in value]


def without_appearance_row(value):
    """*value* (a settings menu's list of texts) without the "🎨 Appearance"
    row goldens recorded on older bases do not have."""
    return [v for v in value if v != "🎨 Appearance"]


def with_keyboard_app_removed(value):
    """*value* (a keyboard surface's list of texts) without the "📱 App"
    button older goldens recorded on the main keyboard."""
    return [v for v in value if v != "📱 App"]


#: The Mini App page screens (tests/golden/miniapp_page_english.json) the
#: quiet Updates card (2026-10-15) changed: the card moved first on the
#: Settings tab (every Settings screen's texts in a new order), its head
#: offers Check for updates whenever no job runs (in place of Check again),
#: and it names the running version. See :func:`updates_card_change_only`.
#: The Mini App page screens Claude Code background jobs (2026-10-24) added:
#: a session in the background, its menu, a busy job's question in the
#: app, Leave it, Stop and bring back, a failure, the grid's count, and
#: old1's page again (where the screens after them start). New screens
#: only: no screen recorded before changed.
BG_JOBS_PAGE_SCREENS = frozenset({
    "detail.background", "detail.background.menu", "detail.background.busy",
    "detail.background.left", "detail.background.forced", "detail.background.failed",
    "grid.background", "detail.gone.again",
})
UPDATES_CARD_PAGE_SCREENS = frozenset({
    "settings", "settings.app_language", "settings.app_language_failed", "settings.saved",
    "settings.save_failed", "updates.checking", "updates.claude", "updates.claude_start",
    "updates.aipager", "updates.none", "updates.said", "updates.check_refused_0",
    "updates.check_refused_1", "updates.check_refused_2", "updates.check_refused_3",
    "updates.check_refused_4", "updates.start_refused_0", "updates.start_refused_1",
    "updates.start_refused_2", "updates.start_refused_3", "updates.start_refused_4",
    "updates.start_refused_5", "updates.start_refused_6", "updates.job_waiting_for_idle",
    "updates.job_gate_timeout", "updates.job_starting", "updates.job_claude_updating",
    "updates.job_upgrading", "updates.job_some_new_phase", "updates.job_done",
    "updates.job_restart_now", "updates.restart_waiting", "updates.restart_updated",
    "updates.restart_updated_one", "updates.restart_other", "updates.restart_reopen",
    "updates.restart_refused", "updates.restart_slow",
})
_RUNNING_VERSION = re.compile(r"v\d+(?:\.\d+)+")


def updates_card_change_only(before: list, after: list) -> bool:
    """Whether *after* (a page screen's texts) differs from *before* only
    as the quiet Updates card changes it: any order, "Check again" gone,
    "Check for updates" and the running version ("v1.2.3") added."""
    gone = Counter(before) - Counter(after)
    added = Counter(after) - Counter(before)
    return set(gone) <= {"Check again"} and all(
        text == "Check for updates" or _RUNNING_VERSION.fullmatch(text) for text in added)


#: The Appearance section (2026-10-15) every Settings screen shows after
#: Chat preferences, as the page fixture draws it: its heading, its intro
#: and its one group, then the read-only note where the chat is read-only.
#: See :func:`without_appearance_section`.
APPEARANCE_SECTION_TEXTS = ("Appearance", "How aipager's buttons look in this chat.",
                            "Stop button", "Normal")
_READ_ONLY_NOTE = "Only an admin can change these."


def without_appearance_section(texts: list) -> list:
    """*texts* (a page screen's) without the Appearance section, which
    must be exactly :data:`APPEARANCE_SECTION_TEXTS` (and the note)."""
    if APPEARANCE_SECTION_TEXTS[0] not in texts:
        return list(texts)
    i = texts.index(APPEARANCE_SECTION_TEXTS[0])
    j = i + len(APPEARANCE_SECTION_TEXTS)
    assert tuple(texts[i:j]) == APPEARANCE_SECTION_TEXTS, texts[i:j]
    if texts[j:j + 1] == [_READ_ONLY_NOTE]:
        j += 1
    return texts[:i] + texts[j:]


#: The Mini App page screens (tests/golden/miniapp_page_english.json) the
#: three modes changed or added.
THREE_MODES_PAGE_SCREENS = frozenset({
    "detail.busy.menu", "detail.busy.mode_sent", "detail.busy.perms", "detail.idle.menu",
    "detail.idle.perms", "new.auto", "new.create_conflict", "new.create_failed",
    "new.create_forbidden", "new.create_unreachable", "new.custom_model",
    "new.custom_model_bad", "new.custom_model_ok", "new.default_dir", "new.folder",
    "new.folder_busy", "new.folder_created", "new.folder_existed", "new.folder_failed",
    "new.folder_forbidden", "new.folder_refused", "new.folder_unreachable", "new.full",
    "new.groups", "new.named", "new.open",
})

_MODE_LABEL_UNDO = (
    (re.compile(r"(🤖 Auto|💬 Ask) mode"), r"\1"),
    # The sentences around the label that said "mode" themselves or read
    # wrong with it: "x1 is in 🤖 Auto mode.", "Starting x1 in 🤖 Auto
    # mode…", "Permissions: 🤖 Auto mode".
    (re.compile(r"\bis in (🤖 Auto|💬 Ask)"), r"is \1"),
    (re.compile(r"(Starting <b>[^<]*</b>) in (🤖 Auto|💬 Ask)"), r"\1 \2"),
    (re.compile(r"\bPermissions: (🤖 Auto|💬 Ask)"), r"Mode: \1"),
)


def without_mode_labels(value):
    """*value* (a string, or lists and dicts of them) with the English mode
    labels and the sentences around them read as before 2026-10-10, on
    both sides of a comparison with an older golden: what is left equal
    proves nothing else in the English changed."""
    if isinstance(value, str):
        for pattern, repl in _MODE_LABEL_UNDO:
            value = pattern.sub(repl, value)
        return value
    if isinstance(value, list):
        return [without_mode_labels(v) for v in value]
    if isinstance(value, dict):
        return {k: without_mode_labels(v) for k, v in value.items()}
    return value


#: The golden surfaces (design 8.5): the three render tests' golden
#: tuples, the language list and the phase 5 launch surfaces, one list for
#: the render test of every language (tests/test_i18n_render_golden.py).
GOLDEN_SURFACES = (
    "card.busy", "card.final", "card.thinking", "prompt.separate", "prompt.inline",
    "answer.headers", "settings.root", "language.list", "status.list", "auth.refusals",
    "transport.texts", "group.texts", "keyboard.main", "menu.commands",
    "route.no_session", "route.send_failures", "route.stop", "taps.gate",
    "sess.end_confirm", "notice.not_taken", "modecard.cmd_replies",
    "language.offer", "language.first_contact", "bot.descriptions",
)

_FROZEN_MODULES = ("aipager.bot.animation", "aipager.bot.dashboard",
                   "aipager.bot.notify", "aipager.bot.callbacks")


class Env:
    """What every surface needs: the bot factory, a loop runner, the
    monkeypatch, and the language stored for the chats it renders in."""

    def __init__(self, mk_bot, run_async, monkeypatch, code: str = "en",
                 decoy: str | None = None):
        self.mk_bot = mk_bot
        self.run = run_async
        self.mp = monkeypatch
        self.code = code
        self._freeze()
        if decoy is not None:
            self.decoys(decoy)

    def decoys(self, code: str) -> None:
        """Store *code* for chats no surface renders in (a private chat and
        a group), and remember it as their person's Telegram language, so
        a surface that took another chat's language would show it (check
        12)."""
        from aipager import preferences
        from aipager.i18n import persist
        for chat in DECOY_CHATS:
            preferences.set_ui_language(chat, code)
        for person in DECOY_PEOPLE:
            persist.set_seen(person, code)

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

    def msg(self, value, chat=CHAT, **params):
        """A module constant as *chat* reads it: rendered through the
        chat's language once it is a key (``Msg``), as it is before."""
        from aipager import i18n
        from aipager.i18n import Msg
        if isinstance(value, Msg):
            return i18n.for_chat(chat).text(value, **params)
        return value.format(**params) if params else value


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


@surface("settings.appearance")
def _settings_appearance(env):
    """/settings → 🎨 Appearance: the page, each choice's options, and
    the page again with every choice changed (the current one highlighted)."""
    from aipager import preferences
    from aipager.bot import appearance
    env.store(CHAT)
    out = []
    text, kb = appearance.render_page(CHAT)
    out += [text, *_inline(kb)]
    for field in preferences.APPEARANCE_FIELDS:
        text, kb = appearance.render_field(CHAT, field)
        out += [text, *_inline(kb)]
    for field, value in (("stop", "red"), ("prompt", "coloured"), ("app", "blue"),
                         ("settings", "highlight")):
        preferences.set_appearance(CHAT, field, value)
    text, kb = appearance.render_page(CHAT)
    out += [text, *_inline(kb)]
    for entry in env.with_tr(appearance.appearance_schema):
        out.append(entry["title"])
        for option in entry["options"]:
            out += [option["label"], option["help"]]
    return out


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


@surface("card.helpers")
def _card_helpers(env):
    """A helper agent's rows (2026-10-14): named after the agent at the top
    of its chain ("pipeline-runner › ship-tester"), running and settled,
    next to the session's own agent, on the rich and the legacy card."""
    from aipager.bot import animation
    env.store(CHAT)
    bot = env.mk_bot()
    sess = _sess()
    sess.busy_started_at = NOW - 120
    sess.tool_history = [
        ("🤖 pipeline-runner", False),
        ("🤖 pipeline-runner › ship-tester · 2 tool calls · 0:15", True),
        ("🤖 pipeline-runner › Explore", False),
    ]
    # Claude's words: the card keeps the chat's direction, as card.busy does.
    sess.stream_commentary = [(0, "Now the agent.")]
    sess.finished_subagents = [{"history_idx": 1, "tools": ["Read: /a", "Bash: ls"]}]
    sess.active_subagents = {
        "r1": {"type": "pipeline-runner", "activity": "Write: /final.md",
               "started_at": NOW - 100, "history_idx": 0, "tools": ["Write: /final.md"],
               "tool_count": 1},
        "e1": {"type": "Explore", "card_label": "pipeline-runner › Explore",
               "activity": "Grep: agent_parents", "started_at": NOW - 12,
               "history_idx": 2, "tools": ["Grep: agent_parents"], "tool_count": 1},
    }
    return [animation.build_stream_card(sess, "Working"),
            animation.build_stream_card(sess, animation.FINAL_VERB, final=True),
            bot._build_busy_text(LABEL, "Working", sess)]


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
    """The status block under an answer, in every state the spec names
    (markdown: the only form an answer carries)."""
    from aipager.bot import agents_line as al
    env.store(CHAT)
    A, V = al.AgentRow, al.BlockView
    views = [
        # Two agents and a shell, working: prose, then a tool phrase.
        V(agents=(A("pipeline-runner", False, prose="Writing final.md for the pipeline"),
                  A("Explore", False, tool="Read", tool_arg="state.py")),
          shells_running=1),
        # One done, the other still going (its Agent call's description).
        V(agents=(A("Explore", True, 42),
                  A("pipeline-runner", False, description="Running the full suite")),
          shells_running=1),
        # Agents done, a shell still up; then that shell finished.
        V(agents=(A("Explore", True, 42), A("pipeline-runner", True, 300)),
          agents_done_seconds=372, shells_running=1),
        V(agents=(A("Explore", True, 42), A("pipeline-runner", True, 300)),
          agents_done_seconds=372, shells_finished=1, shells_finished_seconds=7985),
        # Only shells: running, finished, stopped, both.
        V(shells_running=1),
        V(shells_running=2),
        V(shells_finished=1, shells_finished_seconds=90),
        V(shells_stopped=2, shells_stopped_seconds=61),
        V(shells_finished=1, shells_finished_seconds=90, shells_stopped=1,
          shells_stopped_seconds=30),
        # One agent done alone; as many rows as the cap; a bare row.
        V(agents=(A("Explore", True, 42),), agents_done_seconds=42),
        V(agents=tuple(A(f"general_purpose{i}", False) for i in range(6))),
        V(agents=(A("Explore", False),)),
    ]
    tools = (("Read", "state.py"), ("Write", "final.md"), ("Edit", "notify.py"),
             ("MultiEdit", "a.py"), ("NotebookEdit", "n.ipynb"), ("Bash", ""),
             ("Grep", "agents_line"), ("Glob", "*.py"), ("WebSearch", "telegram bot api"),
             ("WebFetch", "docs.python.org"), ("Agent", ""), ("Task", ""),
             ("TodoWrite", ""), ("Read", ""))
    views += [V(agents=(A("Explore", False, tool=t, tool_arg=arg),)) for t, arg in tools]
    views += [
        # The tree (2026-10-14): two agents of the session's own, each with
        # its own agents under it, one of them deeper (named after the
        # agent that started it), one waiting for its own, one done.
        V(agents=(A("pipeline-runner", False, prose="Writing final.md for the pipeline"),
                  A("ship-tester", False, description="Write and run criteria tests",
                    parent=0),
                  A("ship-developer", False, tool="Edit", tool_arg="state.py", parent=0),
                  A("r2-runner", False, waiting=True, tool="Bash"),
                  A("ship-reviewer", False, description="Collect line numbers",
                    parent=2),
                  A("Explore", True, 42, parent=3))),
        # More agents than rows: the ones working first, each under its own.
        V(agents=(A("pipeline-runner", False, waiting=True),
                  A("ship-tester", True, 15, parent=0),
                  A("ship-developer", False, parent=0),
                  A("ship-reviewer", False, parent=2),
                  A("r2-runner", False, waiting=True),
                  A("Explore", False, parent=4),
                  A("Plan", False, parent=4),
                  A("general_purpose", False, parent=5))),
    ]
    return [env.with_tr(al.render_block, view) for view in views]


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
               bot._build_resume_mode_keyboard(sess, "full", chat_id=chat, may_full=True),
               bot._build_resume_mode_keyboard(sess, "full", chat_id=chat, may_full=False),
               bot._build_resume_mode_keyboard(sess, "ask", chat_id=chat, may_full=True),
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
            sess.perm_mode = "auto" if status == Status.BUSY else "ask"
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


# ---- phase 5: the launch -----------------------------------------------------------------

@surface("language.offer")
def _language_offer(env):
    """The one-time offer card (7.4), in the language it offers (the
    person's Telegram language; Persian stands in for English, which is
    never offered)."""
    from aipager import i18n
    from aipager.bot import language_offer
    code = "fa" if env.code == "en" else env.code
    text, kb = language_offer.render_offer(i18n.get(env.code), code)
    return [text, *_inline(kb)]


@surface("language.first_contact")
def _language_first_contact(env):
    """The one-time first-contact line (7.1), in the chat's language."""
    from aipager import i18n
    from aipager.bot import language_menu
    env.store(CHAT)
    return [language_menu.first_contact_text(i18n.for_chat(CHAT))]


@surface("bot.descriptions")
def _bot_descriptions(env):
    """The bot's description and short description (5.8), as
    ``sync_descriptions`` sets them for a language."""
    from aipager import i18n
    from aipager.bot import bot_descriptions
    tr = i18n.get(env.code)
    return [tr.text(bot_descriptions.DESCRIPTION), tr.text(bot_descriptions.SHORT)]


# ---- phase 3b: /update ---------------------------------------------------------------------

#: A stranger's and a read-only member's ids (phase 3b's auth surfaces).
STRANGER, LURKER, VIEWER = 9, 8, 3


def _sent_texts(mock) -> list[str]:
    out = []
    for call in mock.await_args_list:
        text = call.kwargs.get("text")
        if text is None and len(call.args) > 1:
            text = call.args[1]
        if text is not None:
            out.append(text)
        out += _inline(call.kwargs.get("reply_markup"))
    return out


def _reply_texts(mock) -> list[str]:
    out = []
    for call in mock.await_args_list:
        text = call.args[0] if call.args else call.kwargs.get("text")
        out.append(text)
        out += _inline(call.kwargs.get("reply_markup"))
    return out


def update_status(*, running="1.0.1", latest="1.0.2", ap_newer=True, upgradable=True,
                  reason=None, installed=None, cc="2.1.300", cc_latest="2.1.310",
                  cc_newer=True, blocked=None, automatic=True, restart_reason=None):
    """A check's raw result, as ``UpdateManager._collect_status`` builds it."""
    return {
        "aipager": {"running": running, "installed": installed or running,
                    "latest": latest, "update_available": ap_newer,
                    "source": {"kind": "pipx", "origin": "index", "detail": None,
                               "upgradable": upgradable, "reason": reason,
                               "describe": "pipx (PyPI)", "describe_short": "pipx"}},
        "claude": {"current": cc, "latest": cc_latest, "update_available": cc_newer,
                   "method": "native", "channel": "latest", "blocked": blocked},
        "restart": {"mode": "systemd", "automatic": automatic, "reason": restart_reason,
                    "manual_command": None},
    }


UPDATE_STATUSES = (
    update_status(),
    update_status(cc_newer=False, cc_latest="2.1.300", upgradable=False,
                  reason="editable install"),
    update_status(ap_newer=False, latest="1.0.1", blocked="npm prefix not writable"),
    update_status(ap_newer=False, latest="1.0.1", cc_newer=False, cc_latest="2.1.300"),
    update_status(latest=None, cc_newer=False, cc_latest="2.1.300"),
    update_status(ap_newer=False, latest="1.0.1", cc=None, installed="1.0.2"),
    update_status(cc_latest=None, automatic=False, restart_reason="no service unit"),
    update_status(cc_newer=False, cc_latest="2.1.300", automatic=False),
)


def _update_bot(env, chat=CHAT):
    env.store(chat)
    return env.mk_bot()


def _job(kind="aipager", chat=CHAT, phase="starting"):
    from aipager.bot import update_flow
    return update_flow._Job(id=7, kind=kind, chat_id=chat, user_id=CHAT, origin="chat",
                            started_at=WALL, phase=phase)


def _cmd_update(user, chat=CHAT):
    update = MagicMock()
    update.effective_user = MagicMock(id=user, language_code=None)
    update.effective_chat = MagicMock(id=chat, type="private" if chat > 0 else "supergroup")
    update.message = MagicMock()
    update.message.chat_id = chat
    update.message.chat = update.effective_chat
    update.message.reply_text = AsyncMock(return_value=MagicMock(message_id=5))
    update.effective_message = update.message
    return update


@surface("update.prompt")
def _update_prompt(env):
    from aipager.bot import update_flow
    bot = _update_bot(env)
    out = []
    for user, phase in ((CHAT, None), (7, None), (CHAT, "waiting_for_idle"),
                        (CHAT, "restart_scheduled")):
        if phase:
            bot.updates._job = _job(phase=phase)
        update = _cmd_update(user)
        env.run(update_flow.handle_update_cmd(bot, update, MagicMock()))
        out += _reply_texts(update.message.reply_text)
    return out


@surface("update.check")
def _update_check(env):
    from aipager.bot import update_flow
    env.store(CHAT, GROUP)
    out = []
    for status in UPDATE_STATUSES:
        text, kb = env.with_tr(update_flow.render_check, status, show_paths=True)
        out += [text, *_inline(kb)]
        payload = env.with_tr(update_flow.check_payload, status, show_paths=False)
        out += payload["lines"] + payload["notes"]
        out += [t for t in (payload["summary"], payload["restart"]) if t]
        if payload["offer"]:
            out.append(payload["offer"]["label"])
    text, kb = env.with_tr(update_flow.render_check, UPDATE_STATUSES[0], show_paths=False,
                           chat=GROUP)
    out += [text, *_inline(kb)]
    out += _inline(env.with_tr(update_flow.check_prompt_markup))
    return out


@surface("update.job")
def _update_job(env):
    from aipager import self_update
    from aipager.bot import update_flow
    from aipager.state import Status
    bot = _update_bot(env)
    mgr = bot.updates
    out = []
    job = _job()
    out.append(env.with_tr(update_flow._job_text, job))
    for phase in ("waiting_for_idle", "gate_timeout"):
        job.phase = phase
        job.blockers = ["x1: running a turn", "x2: waiting on a question"]
        out.append(env.with_tr(update_flow._job_text, job))
        out += _inline(env.with_tr(update_flow._job_markup, job))
        out.append(mgr._gate_live(job))
    job.blockers.append("held answer not yet delivered")
    out.append(mgr._gate_live(job))
    # Sessions: two in this chat, one elsewhere; then none at all.
    _registered(bot, _sess(status=Status.IDLE),
                _sess(name="claude-x2", label="x2", status=Status.BUSY),
                _sess(name="claude-x3", label="x3", chat=GROUP, status=Status.IDLE))
    out.append(mgr._session_note(job, "2.1.310"))
    out.append(mgr._session_note(_job(chat=GROUP), None))
    empty = env.mk_bot()
    out.append(empty.updates._session_note(job, "2.1.310"))
    for plan in (self_update.RestartPlan("foreground", False, "no service unit",
                                         "aipager start", ["x1", "x2"]),
                 self_update.RestartPlan("foreground", False, None, None, [])):
        out.append(env.with_tr(mgr._manual_restart_text, plan))
    env.mp.setattr(self_update, "HEARTBEAT_GRANULARITY_SECONDS", 30)
    for seconds in (0, 30, 60, 150):
        out.append(env.with_tr(update_flow._elapsed_label, seconds))
    out.append(env.with_tr(update_flow._pre_tail, "pip output", GROUP))
    # The watchdog: still alive after the scheduled restart.
    env.mp.setattr(self_update, "RESTART_DELAY_SECONDS", 0)
    env.mp.setattr(update_flow, "RESTART_WATCHDOG_SECONDS", 0)
    env.mp.setattr(self_update, "cancel_scheduled_restart", lambda *a, **k: None)
    env.mp.setattr(self_update, "clear_marker", lambda: None)
    watched = _job(phase="restart_scheduled")
    mgr._job, mgr._lock_owner = watched, watched.id
    mgr._release_lock = lambda j: None
    env.run(mgr._restart_watchdog(watched))
    out.append(watched.sections[-1])
    return out


def _steps_mgr(env, chat=CHAT):
    from aipager.state import Status
    bot = _update_bot(env, chat)
    _registered(bot, _sess(status=Status.IDLE),
                _sess(name="claude-x3", label="x3", chat=GROUP, status=Status.IDLE))
    mgr = bot.updates

    async def _report(job, *, ornament=False):
        return None
    mgr._report = _report
    return mgr


def _beating(mgr, result, labels):
    async def _beat(job, label, fn, *args):
        labels.append(str(label))
        return result
    mgr._await_with_heartbeat = _beat


@surface("update.steps")
def _update_steps(env):
    """Every section the Claude Code and aipager steps write, through
    the steps themselves (the installer, the network and systemd faked)."""
    from aipager import install_source, self_update
    out: list[str] = []
    labels: list[str] = []
    CU = self_update.ClaudeUpdateResult
    for chat, result, shutting in (
            (CHAT, CU(None, None, None, False, "", error="claude not found"), False),
            (CHAT, CU("2.1.300", None, None, True, "downloading"), False),
            (CHAT, CU("2.1.300", "2.1.300", 1, False, "npm failed"), False),
            (GROUP, CU("2.1.300", "2.1.300", None, False, "npm failed", error="boom"), False),
            (CHAT, CU("2.1.300", "2.1.310", 0, False, ""), False),
            (CHAT, CU("2.1.300", None, 0, False, ""), True),
            (CHAT, CU("2.1.310", "2.1.310", 0, False, ""), False)):
        mgr = _steps_mgr(env, chat)
        mgr._shutting_down = shutting
        _beating(mgr, result, labels)
        job = _job(kind="claude", chat=chat)
        env.run(mgr._claude_step(job))
        out.append(job.sections[-1])

    src = install_source.InstallSource(kind="pipx", prefix="/opt/pipx",
                                       python="/opt/pipx/bin/python", origin="index",
                                       upgradable=True)
    state = {"source": src, "argv": ["pipx", "upgrade", "aipager"], "latest": "1.0.2",
             "probe": ("1.0.2", True, None), "plan": None, "marker_error": None,
             "schedule": (True, "aipager-restart"), "gates": []}
    auto = self_update.RestartPlan("systemd", True, None, "systemctl --user restart aipager")
    # systemd's manual restart (KillMode): "Restart needed" as it always
    # read. A daemon without a unit offers Restart now or the steps for its
    # setup instead (surface "update.restart").
    manual = self_update.RestartPlan("systemd", False, "no service unit",
                                     "aipager start", ["x1"], steps="systemd")
    mp = env.mp
    mp.setattr(install_source, "detect_install_source", lambda **k: state["source"])
    mp.setattr(install_source, "upgrade_argv", lambda s: state["argv"])
    mp.setattr(install_source, "upgrade_env", lambda s: None)
    mp.setattr(install_source, "augmented_path", lambda: "/usr/bin")
    mp.setattr(install_source, "reinstall_hint", lambda kind: "pipx install --force aipager")
    mp.setattr(self_update, "running_version", lambda: "1.0.1")
    mp.setattr(self_update, "installed_version", lambda: "1.0.2")
    mp.setattr(self_update, "latest_aipager_version", lambda: state["latest"])
    mp.setattr(self_update, "restart_plan", lambda registry: state["plan"])
    mp.setattr(self_update, "probe_installed_version", lambda python: state["probe"])

    def _write_marker(marker):
        if state["marker_error"]:
            raise OSError(state["marker_error"])
    mp.setattr(self_update, "write_marker", _write_marker)
    mp.setattr(self_update, "schedule_restart", lambda plan: state["schedule"])
    mp.setattr(self_update, "clear_marker", lambda: None)
    mp.setattr(self_update, "cancel_scheduled_restart", lambda *a, **k: None)

    CR = self_update.CommandResult
    ok = CR(0, "Successfully installed aipager", False, None)

    def _src(**fields):
        base = dict(kind="pipx", prefix="/opt/pipx", python="/opt/pipx/bin/python",
                    origin="index", upgradable=True)
        base.update(fields)
        return install_source.InstallSource(**base)

    cases = (
        (CHAT, dict(source=_src(upgradable=False, reason="editable install")), ok),
        (CHAT, dict(argv=None), ok),
        (CHAT, dict(latest="1.0.1", plan=manual), ok),
        (CHAT, dict(plan=auto, gates=["cancel"]), ok),
        (CHAT, dict(plan=manual), CR(None, "still resolving", True, "timed out")),
        (CHAT, dict(plan=manual), CR(2, "resolver error", False, None)),
        (CHAT, dict(plan=manual, probe=(None, False, "ImportError")), ok),
        (CHAT, dict(plan=manual, probe=("1.0.1", True, None),
                    source=_src(origin="local", origin_detail="/src/aipager")), ok),
        (GROUP, dict(plan=manual, probe=("1.0.1", True, None),
                     source=_src(origin="local", origin_detail="/src/aipager")), ok),
        (CHAT, dict(plan=manual, probe=("1.0.1", True, None), source=_src(origin="vcs")), ok),
        (CHAT, dict(plan=manual, probe=("1.0.1", True, None)), ok),
        (CHAT, dict(plan=manual, probe=("1.0.1", True, None),
                    source=_src(origin="unknown")), ok),
        (CHAT, dict(plan=manual), ok),
        (CHAT, dict(plan=auto, gates=["open", "cancel"]), ok),
        (CHAT, dict(plan=auto, gates=["open", "open"], marker_error="disk full"), ok),
        (CHAT, dict(plan=auto, gates=["open", "open"], schedule=(False, "no systemd-run")),
         ok),
        (CHAT, dict(plan=auto, gates=["open", "open"]), ok),
    )
    for chat, changes, result in cases:
        state.update(source=src, argv=["pipx", "upgrade", "aipager"], latest="1.0.2",
                     probe=("1.0.2", True, None), plan=auto, marker_error=None,
                     schedule=(True, "aipager-restart"), gates=[])
        state.update(changes)
        mgr = _steps_mgr(env, chat)
        _beating(mgr, result, labels)
        seen: list[str] = []

        async def _gate(job, _state=state, _seen=seen):
            if job.sections:
                _seen.append(job.sections[-1])
            return _state["gates"].pop(0) if _state["gates"] else "open"
        mgr._wait_gate = _gate
        job = _job(chat=chat)
        env.run(mgr._aipager_step(job))
        out += seen + ["\n\n".join(job.sections)]
    # A shutdown during or after the install.
    mgr = _steps_mgr(env)
    for installed in ("1.0.1", "1.0.2"):
        mp.setattr(self_update, "installed_version", lambda v=installed: v)
        job = _job()
        mgr._installed_unprobed_at_shutdown(job, "1.0.1")
        out.append(job.sections[-1])
    for phase, interrupted in (("claude_updating", True), ("upgrading", True),
                               ("upgrading", False)):
        job = _job(phase=phase)
        job.interrupted = interrupted
        out.append(mgr._shutdown_section(job))
    out += sorted(set(labels))
    return out


def _marker_bot(env, marker, *, running="1.0.2"):
    from aipager import install_source, self_update
    from aipager.state import SessionRegistry, Status
    env.store(CHAT)
    registry = SessionRegistry()
    bot = env.mk_bot(registry)
    _registered(bot, _sess(status=Status.IDLE),
                _sess(name="claude-x2", label="x2", status=Status.BUSY))
    env.mp.setattr(self_update, "MARKER_SETTLE_SECONDS", 0)
    env.mp.setattr(self_update, "read_and_clear_marker", lambda: dict(marker))
    env.mp.setattr(self_update, "running_version", lambda: running)
    env.mp.setattr(install_source, "reinstall_hint", lambda kind: "pipx install --force aipager")
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock(message_id=5))
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=6))
    return bot


@surface("update.marker")
def _update_marker(env):
    import time as _time

    from aipager.bot import update_flow
    out = []
    one = [{"name": "claude-x1", "label": "x1"}, {"name": "claude-x9", "label": "x9"}]
    two = [{"name": "claude-x1", "label": "x1"}, {"name": "claude-x2", "label": "x2"}]
    for marker, running in (
            ({"from": "1.0.1", "to": "1.0.2", "sessions": one}, "1.0.2"),
            ({"from": "1.0.1", "to": "1.0.2", "sessions": two}, "1.0.2"),
            ({"from": "1.0.1", "to": "1.0.2", "sessions": []}, "1.0.1"),
            ({"interrupted": "claude_updating", "from": "1.0.1"}, "1.0.1"),
            ({"interrupted": "upgrading", "from": "1.0.1", "source_kind": "pipx"}, "1.0.1")):
        for edit in (False, True):
            marker = dict(marker, chat_id=CHAT, scheduled_at=_time.time())
            if edit:
                marker["message"] = {"chat_id": CHAT, "message_id": 5, "prefix": ""}
            bot = _marker_bot(env, marker, running=running)
            env.run(update_flow.deliver_update_marker(bot, bot.registry))
            out += [c.kwargs["text"] for c in bot._app.bot.edit_message_text.await_args_list]
            out += _sent_texts(bot._app.bot.send_message)
    return out


def _cb_query(chat=CHAT, user=CHAT, message_id=42):
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = MagicMock(message_id=message_id, chat_id=chat)
    query.message.chat = MagicMock(id=chat, type="private" if chat > 0 else "supergroup")
    query.message.edit_text = AsyncMock()
    query.from_user = MagicMock(id=user, language_code=None)
    update = MagicMock()
    update.callback_query = query
    update.message = None
    update.effective_user = query.from_user
    update.effective_chat = query.message.chat
    return update, query


def _tapped(query) -> list[str]:
    out = [c.args[0] for c in query.answer.await_args_list if c.args and c.args[0]]
    for call in query.edit_message_text.await_args_list:
        text = call.args[0] if call.args else call.kwargs.get("text")
        out.append(text)
        out += _inline(call.kwargs.get("reply_markup"))
    return out


@surface("update.taps")
def _update_taps(env):
    from aipager import install_source
    from aipager.bot import update_flow
    bot = _update_bot(env)
    mgr = bot.updates
    mgr._spawn = lambda coro: coro.close()
    env.mp.setattr(install_source, "detect_install_source", lambda **k: install_source.InstallSource(
        kind="pipx", prefix="/opt/pipx", python="/opt/pipx/bin/python",
        upgradable=False, reason="editable install"))
    out = []

    def tap(action, user=CHAT):
        update, query = _cb_query(user=user)
        env.run(update_flow.handle_callback(bot, update, query, "_", action))
        return _tapped(query)

    out += tap("up:x")
    out += tap("up:x", user=7)
    out += tap("up:cc")
    out += tap("up:chk")
    out += tap("up:now:abc")
    out += tap("up:now:123")
    out += tap("up:zzz")
    for error, job in (("check_expired", None), ("not_upgradable", None),
                       ("shutting_down", None), ("update_in_progress", {"phase": "upgrading"})):
        async def _start(kind, _e=error, _j=job, **k):
            return update_flow.StartResult(False, _e, _j)
        mgr.start_offer = _start
        out += tap("up:go:ap")
    mgr._shutting_down = True
    out += tap("up:chk")
    mgr._shutting_down = False
    mgr._job = _job(phase="upgrading")
    out += tap("up:chk")
    mgr._job = None

    async def _broken():
        raise RuntimeError("offline")
    mgr.check = _broken
    message = MagicMock(chat_id=CHAT)
    message.chat = MagicMock(id=CHAT)
    message.edit_text = AsyncMock()
    env.run(update_flow._show_check(bot, message, CHAT))
    out += _reply_texts(message.edit_text)
    return out


def _waiting_status(*, latest="1.1.0", installed_ok=True, mode="foreground", automatic=False,
                    in_place=True, steps="terminal", container=None, ap_newer=False,
                    manual_command=None):
    status = update_status(latest=latest, ap_newer=ap_newer, cc_newer=False,
                           cc_latest="2.1.300", installed="1.1.0", automatic=automatic)
    status["aipager"]["installed_ok"] = installed_ok
    status["restart"].update(mode=mode, in_place=in_place, steps=steps, container=container,
                             manual_command=manual_command,
                             reason=None if automatic else "no service unit")
    return status


@surface("update.restart")
def _update_restart(env):
    """Restart now (update-restart-without-service): the installed-and-
    waiting check, the steps for each setup, the restart job's sections,
    the owner's hooks notice and the Restart now refusals."""
    from aipager import self_restart, self_update
    from aipager.bot import report_flow, report_offer, update_flow
    env.store(CHAT, GROUP)
    out: list[str] = []
    for status in (
            _waiting_status(),
            _waiting_status(automatic=True, mode="systemd", in_place=False, steps="systemd"),
            _waiting_status(in_place=False),
            _waiting_status(in_place=False, mode="docker", steps="docker",
                            container="0123456789ab"),
            _waiting_status(in_place=False, mode="docker", steps="docker"),
            _waiting_status(in_place=False, mode="launchd", steps="launchd",
                            manual_command="launchctl kickstart -k gui/501/com.aipager.daemon"),
            _waiting_status(in_place=False, mode="systemd", steps="systemd",
                            manual_command="systemctl --user restart aipager.service"),
            _waiting_status(installed_ok=False, latest="1.0.2"),
            _waiting_status(latest="1.0.2", ap_newer=True, installed_ok=None)):
        text, kb = env.with_tr(update_flow.render_check, status, show_paths=True)
        out += [text, *_inline(kb)]
        payload = env.with_tr(update_flow.check_payload, status, show_paths=False)
        out += payload["lines"] + payload["notes"]
        out += [t for t in (payload["summary"], payload["restart"], payload["steps"]) if t]
    # The restart job, through its step (the probe, the plan and the gate
    # faked).
    state = {"probe": ("1.1.0", True, None), "gate": "open", "request": True,
             "plan": self_update.RestartPlan("foreground", False, "no service unit",
                                             in_place=True)}
    mp = env.mp
    mp.setattr(self_update, "running_version", lambda: "1.0.1")
    mp.setattr(self_update, "probe_restart_target", lambda target: state["probe"])
    mp.setattr(self_update, "restart_plan", lambda registry: state["plan"])
    mp.setattr(self_update, "write_marker", lambda marker: None)
    mp.setattr(self_update, "clear_marker", lambda: None)
    mp.setattr(self_restart, "request", lambda *a, **k: state["request"])
    from aipager import install_source
    mp.setattr(install_source, "reinstall_hint", lambda kind: "pipx install --force aipager")
    plans = {
        "terminal": self_update.RestartPlan("foreground", False, "no service unit"),
        "docker": self_update.RestartPlan("docker", False, "no service unit", steps="docker",
                                          container="0123456789ab"),
    }
    for changes in (dict(probe=("1.0.1", True, None)),
                    dict(probe=(None, False, "ImportError")),
                    dict(gate="cancel"),
                    dict(plan=plans["terminal"]),
                    dict(plan=plans["docker"]),
                    dict(),
                    dict(request=False)):
        state.update(probe=("1.1.0", True, None), gate="open", request=True,
                     plan=self_update.RestartPlan("foreground", False, "no service unit",
                                                  in_place=True))
        state.update(changes)
        mgr = _steps_mgr(env)

        async def _gate(job, _state=state):
            return _state["gate"]
        mgr._wait_gate = _gate
        job = _job(kind="restart")
        outcome = env.run(mgr._restart_step(job))
        out.append(job.live or "")
        if isinstance(outcome, update_flow._InPlace):
            env.run(mgr._request_in_place(job, outcome))
        out.append("\n\n".join(job.sections))
    # A foreground update that ends with Restart now.
    mgr = _steps_mgr(env)
    job = _job()
    out.append(mgr._after_install_text(job, self_update.RestartPlan(
        "foreground", False, "no service unit", in_place=True), _tr(), "1.1.0"))
    job.phase = "done"
    out += _inline(env.with_tr(update_flow._job_markup, job))
    # The owner's one notice: hooks already on a newer aipager.
    bot = _update_bot(env)
    mgr = bot.updates
    mp.setattr(update_flow, "_daemon_code_version", lambda: "1.0.1")
    mp.setattr(report_flow, "resolve_owner", lambda b: CHAT)
    mp.setattr(report_offer, "dm_clear", lambda owner: True)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=9))
    mgr._hook_seen["1.1.0"] = {"delivered": False, "attempts": 0, "last_try_mono": None,
                               "last_try": None, "task": None, "deferred": None}
    env.run(mgr._hook_notice("1.1.0", CHAT))
    for call in bot._app.bot.send_message.await_args_list:
        out.append(call.args[1] if len(call.args) > 1 else call.kwargs.get("text"))
        out += _inline(call.kwargs.get("reply_markup"))
    # ...and that notice once aipager runs 1.1.0: edited into "finished".
    mp.setattr(self_update, "MARKER_SETTLE_SECONDS", 0)
    mp.setattr(update_flow, "_daemon_code_version", lambda: "1.1.0")
    bot._app.bot.edit_message_text = AsyncMock(return_value=MagicMock(message_id=9))
    env.run(update_flow.finish_hook_notices(bot))
    for call in bot._app.bot.edit_message_text.await_args_list:
        out.append(call.kwargs.get("text"))
        out += _inline(call.kwargs.get("reply_markup"))
    # The Restart now refusals (pop-ups).
    for error, detail, job in (("shutting_down", None, None),
                               ("update_in_progress", "lock_held", None),
                               ("update_in_progress", None, {"phase": "verifying"})):
        async def _start(_e=error, _d=detail, _j=job, **k):
            return update_flow.StartResult(False, _e, _j, detail=_d)
        mgr.start_restart = _start
        update, query = _cb_query()
        env.run(update_flow.handle_callback(bot, update, query, "_", "up:rs"))
        out += _tapped(query)
    return out


# ---- phase 3b: problem reports ---------------------------------------------------------------

def _tr(chat=CHAT):
    from aipager import i18n
    return i18n.for_chat(chat)


def _kept(mode="inline", chat=CHAT, msg_id=42, state="open"):
    from aipager.bot import report_flow
    return report_flow.KeptReport(report={"v": 1}, preview=b'{\n "errors": []\n}',
                                  chat_id=chat, msg_id=msg_id, mode=mode, state=state,
                                  created=report_flow._mono())


@surface("report.card")
def _report_card(env):
    from aipager.bot import report_flow as rf
    env.store(CHAT)
    out = []
    for mode in ("inline", "document"):
        out.append(env.with_tr(rf.card_text, _kept(mode)))
    for status in (rf.SENDING_TEXT, rf.NOTE_ADDED, rf.NOTE_CUT, rf.NOTE_EMPTY,
                   rf.NOTE_REFUSED, rf.NOTE_CLOSED, rf.SEND_FAILED_TEXT):
        out.append(env.with_tr(rf.card_text, _kept(), rf.shown(_tr(), status)))
    out.append(env.with_tr(rf._capture_text))
    for line in (rf.CANCELLED_TEXT, rf.LOST_TEXT, rf.DOC_FAILED_TEXT):
        out.append(env.with_tr(rf._final_text, env.msg(line)))
    out += _inline(env.with_tr(rf.preview_keyboard))
    out += _inline(env.with_tr(rf._capture_keyboard))
    out += _inline(env.with_tr(rf.report_button_markup))
    out += _inline(env.with_tr(rf.report_button_markup, rf.REPORT_THIS_BUTTON))
    return out


@surface("report.taps")
def _report_taps(env):
    from aipager.bot import report_flow as rf
    from aipager.report import policy, store
    env.store(CHAT, GROUP)
    bot = env.mk_bot()
    out = []

    def tap(action, *, user=CHAT, chat=CHAT, msg_id=42):
        update, query = _cb_query(chat=chat, user=user, message_id=msg_id)
        env.run(rf.handle_callback(bot, update, query, "_", action))
        return _tapped(query)

    out += tap("rp:send", user=7)
    out += tap("rp:send", chat=GROUP)
    for result in (rf.OpenResult.OPENED, rf.OpenResult.NO_OWNER, rf.OpenResult.NOT_SENT):
        async def _open(bot, *, trigger, errors=None, _r=result):
            return _r
        env.mp.setattr(rf, "open_preview", _open)
        out += tap("rp:open", chat=GROUP)
    out += tap("rp:zzz")
    rf._cards(bot)[(CHAT, 42)] = _kept(state="sending")
    out += tap("rp:send")
    out += tap("rp:cancel")
    rf._cards(bot)[(CHAT, 42)] = _kept(state="done")
    out += tap("rp:send")
    rf._cards(bot).clear()
    out += tap("rp:send")
    out += tap("rp:cancel")
    env.mp.setattr("aipager.config.CHAT_ID", "-5")
    out += tap("rp:send")
    env.mp.setattr("aipager.config.CHAT_ID", str(CHAT))
    # The offer's answers.
    pending = SimpleNamespace(pending=["fp1"], last_offer_ts=123, auto_off=False)
    env.mp.setattr(store, "policy_state", lambda: pending)
    env.mp.setattr(store, "save_policy", lambda state: None)
    env.mp.setattr(store, "records", lambda: [])
    env.mp.setattr(policy, "settle", lambda state, now: state)
    out += tap("rp:op:999")
    for verb, result, auto_off in (("op", rf.OpenResult.OPENED, False),
                                   ("op", rf.OpenResult.NOT_SENT, False),
                                   ("on", None, False), ("od", None, True)):
        async def _open(bot, *, trigger, errors=None, _r=result):
            return _r
        env.mp.setattr(rf, "open_preview", _open)
        env.mp.setattr(policy, "note_answer",
                       lambda state, answer, _a=auto_off: SimpleNamespace(auto_off=_a))
        out += tap(f"rp:{verb}:123")
    # A note typed after its card was lost.
    bot._report_note_pending = {"chat_id": CHAT, "user_id": CHAT, "msg_id": 42,
                                "last_active": rf._mono()}
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=7))
    update = _cmd_update(CHAT)
    update.callback_query = None
    update.message.text = "it broke"
    update.message.reply_to_message = None
    update.message.forward_origin = None
    update.message.via_bot = None
    update.message.external_reply = None
    env.run(rf.maybe_handle_text(bot, update, MagicMock(), "it broke"))
    out += _sent_texts(bot._app.bot.send_message)
    return out


def _offer_entry(trigger, count=1, file="aipager/bot/notify.py"):
    return {"trigger": trigger, "count": count, "type": "builtins.KeyError",
            "frames": [{"file": file}]}


@surface("report.offer")
def _report_offer(env):
    from aipager.bot import report_offer
    env.store(CHAT)
    out = []
    for entries in ([_offer_entry("crash")], [_offer_entry("crash", 3)],
                    [_offer_entry("hook_cap", 2)],
                    [_offer_entry("error"), _offer_entry("crash")],
                    [_offer_entry("error", 3, "aipager/bot/zzz.py"), _offer_entry("crash"),
                     _offer_entry("crash"), _offer_entry("crash")]):
        out.append(env.with_tr(report_offer.notice_text, entries))
    out += _inline(env.with_tr(report_offer.notice_keyboard, 123))
    return out


@surface("report.outcomes")
def _report_outcomes(env):
    from aipager.bot import report_drafts, report_flow
    from aipager.report import send
    env.store(CHAT)
    out = []
    for outcome in (*sorted(send.OUTCOMES), "bogus"):
        out.append(env.with_tr(report_flow.outcome_line, outcome, "ap1-x"))
    out.append(env.with_tr(report_drafts.reply_for, report_drafts.FAILED, None).line)
    out.append(env.with_tr(report_drafts.reply_for, "sent", "ap1-x").line)
    report = {"errors": [{"frames": [{"file": f}]} for f in (
        "aipager/bot/notify.py", "aipager/bot/animation.py", "aipager/dtach/notify_hook.py",
        "aipager/bot/zzz.py")] + [{}]}
    out += env.with_tr(report_drafts.areas, report)
    return out


# ---- phase 3b: refusals and notices ----------------------------------------------------------

def _person_update(user, chat, *, language_code=None, username="who"):
    update = _cmd_update(user, chat)
    update.effective_user.language_code = language_code
    update.effective_user.username = username
    update.effective_user.first_name = "Who"
    update.effective_user.last_name = None
    update.effective_user.is_bot = False
    update.message.sender_chat = None
    update.message.from_user = update.effective_user
    return update


@surface("auth.refusals")
def _auth_refusals(env):
    from pathlib import Path as _P

    from aipager import team as team_mod
    from aipager.policy import load_policy
    from aipager.scope import Member, Scope
    team_mod.reset_unauthorized_seen()
    env.store(CHAT, GROUP, STRANGER, LURKER)
    out = []
    # Legacy team mode: a stranger, a read-only member, a stranger's tap.
    team = team_mod.Team(group_id=CHAT, users={
        CHAT: team_mod.User(CHAT, "aly", team_mod.Role.ADMIN),
        VIEWER: team_mod.User(VIEWER, "rob", team_mod.Role.READ_ONLY)})
    bot = env.mk_bot(team=team)
    for user in (STRANGER, VIEWER):
        update = _person_update(user, CHAT)
        env.run(bot._authorize(update))
        out += _reply_texts(update.message.reply_text)
    _update, query = _cb_query(user=STRANGER)
    env.run(bot._authorize_callback(query))
    out += _tapped(query)
    team_mod.reset_unauthorized_seen()
    # Scope mode: an unknown private chat, a non-member, a read-only member.
    bot = env.mk_bot(scopes=[Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=555, label="aly", role="owner"),
        Member(id=VIEWER, label="rob", role="read_only")))])
    bot.policy = load_policy(_P("/nonexistent/policy.yaml"), _P("/nonexistent/policy.d"))
    for user, chat in ((STRANGER, STRANGER), (LURKER, GROUP), (VIEWER, GROUP)):
        update = _person_update(user, chat)
        env.run(bot._authorize(update))
        out += _reply_texts(update.message.reply_text)
    _update, query = _cb_query(chat=GROUP, user=LURKER)
    env.run(bot._authorize_callback(query))
    out += _tapped(query)
    team_mod.reset_unauthorized_seen()
    return out


@surface("auth.auto_deny")
def _auth_auto_deny(env):
    from aipager import team as team_mod
    from aipager.dtach import inject

    async def _nap(_s):
        return None
    env.store(CHAT)
    env.mp.setattr(inject, "send_keys", AsyncMock(return_value=True))
    env.mp.setattr("aipager.bot.auth._sleep", _nap)
    out = []
    for triggerer in (None, SimpleNamespace(id=2, label="bob", role=None),
                      team_mod.User(2, "bob", team_mod.Role.DEVELOPER)):
        bot = env.mk_bot()
        sess = _registered(bot, _sess())
        bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=8))
        env.run(bot._auto_deny(sess, {"name": "Bash", "summary": "Bash: rm -rf build/"},
                               triggerer))
        out += _sent_texts(bot._app.bot.send_message)
    return out


# ---- phase 3b: groups --------------------------------------------------------------------------

@surface("group.texts")
def _group_texts(env):
    from aipager.bot import group_intake
    from aipager.scope import Member, Scope
    env.store(GROUP)
    out = []
    for username in ("aipager_test_bot", None):
        bot = env.mk_bot()
        bot._app.bot.username = username
        out.append(env.with_tr(group_intake.group_help_talk_line, bot, chat=GROUP))
        out.append(env.with_tr(group_intake.talk_hint, bot, GROUP, "x1", dm="",
                               raw_label="x1", chat=GROUP))
    bot = env.mk_bot(scopes=[Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=555, label="aly", role="owner"),))])
    msg = MagicMock(chat_id=GROUP)
    msg.chat = MagicMock(id=GROUP)
    msg.reply_text = AsyncMock(return_value=MagicMock(message_id=3))
    env.run(group_intake._answer_anonymous_sender(bot, msg))
    out += _reply_texts(msg.reply_text)
    return out


@surface("group.dropped")
def _group_dropped(env):
    from aipager.bot import live_reload
    from aipager.scope import Member, Scope
    env.store(GROUP)
    env.mp.setattr(live_reload, "can_prompt", lambda *a, **k: False)
    scopes = [Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=2, label="bob", role="user"),))]
    out = []
    for uid in (2, 77):
        for n in (1, 3):
            bot = env.mk_bot()
            bot._mark_not_delivered = AsyncMock()
            bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=9))
            sess = _registered(bot, _sess(chat=GROUP))
            sess.pending_queue = [(f"hello {i}", 100 + i, None, None, uid) for i in range(n)]
            env.run(live_reload._drop_held(bot, sess, GROUP, scopes, scopes, None))
            out += _sent_texts(bot._app.bot.send_message)
    return out


# ---- phase 3b: shared transport texts ------------------------------------------------------------

API_ERRORS = (
    "API Error: 402 credit balance too low",
    "API Error: 500 internal server error",
    "API Error: 529 overloaded_error",
    "API Error: 429 rate_limit_error",
    "API Error: 429 rate_limit_error, retry after 30 seconds",
    "connection reset by peer",
    "API Error: 418",
)


@surface("transport.texts")
def _transport_texts(env):
    from telegram.error import BadRequest

    from aipager.bot import transport
    env.store(CHAT)
    out = [env.msg(transport.NEEDS_ADMIN_REPLY), env.msg(transport.RESUME_FULL_NEEDS_ADMIN),
           env.msg(transport.RETRY_OTHERS_COMMAND_REPLY)]
    for text in API_ERRORS:
        out.append(env.with_tr(transport._detect_api_error, text)[0])
    tg = MagicMock()
    tg.send_message = AsyncMock(side_effect=[BadRequest("Message is too long"),
                                             MagicMock(message_id=1)])
    env.run(transport._send_with_retry(tg, chat_id=CHAT, text="a" * 5000))
    out.append(tg.send_message.await_args.args[1].lstrip("a"))
    return out


# ---- phase 3b: lifecycle and the leftover dashboard text --------------------------------------------

@surface("lifecycle.recovered")
def _lifecycle_recovered(env):
    env.store(CHAT)
    bot = env.mk_bot()
    out = []
    for live in ({"claude-x1"}, set()):
        sess = _registered(bot, _sess())
        sess.busy_msg_id = 11
        tg = MagicMock()
        tg.edit_message_text = AsyncMock()
        env.run(bot._recover_busy_message(tg, "claude-x1", sess, live))
        out.append(tg.edit_message_text.await_args.args[0])
    return out


@surface("dashboard.loading")
def _dashboard_loading(env):
    from aipager.state import Status
    out = []
    sess = _sess(status=Status.INTERACTIVE)
    sess.pending_permission = {"tool_summary": "AskUserQuestion (loading…)"}
    out += _pinned(env, [sess])
    bot = _status_bot(env)
    sess = _registered(bot, _sess(status=Status.INTERACTIVE))
    sess.pending_permission = {"tool_summary": "AskUserQuestion (loading…)"}
    text, _kb = bot._render_status_list(CHAT)
    out.append(text)
    return out


@surface("dashboard.diff_footer")
def _dashboard_diff_footer(env):
    env.store(CHAT)
    out = []
    for lines in (28, 34):
        bot = env.mk_bot()
        sess = _registered(bot, _sess())
        bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=3))
        content = "\n".join(f"line {i}" for i in range(lines))
        env.run(bot._send_diff_preview(sess, "Write", {"file_path": "/src/a.py",
                                                       "content": content}))
        out += _sent_texts(bot._app.bot.send_message)
    return out


@surface("resume.dashboard")
def _resume_dashboard(env):
    from aipager.state import Status
    env.store(CHAT)
    env.mp.setattr("aipager.bot.dashboard.queue_depth_parts", lambda sess: (2, 1))
    out = []
    bot = env.mk_bot()
    bot._read_status_file = lambda name: {"model": "Opus 4.7", "ctx_pct": 45, "cost": 1.5,
                                          "total_output": 12345}
    sess = _registered(bot, _sess())
    sess.busy_started_at = NOW - 250
    sess.last_hook_at = NOW - 30
    sess.last_lines_added, sess.last_lines_removed = 10, 5
    sess.active_subagents = {"a": {"type": "Explore"}, "b": {"type": "Explore"},
                             "c": {"type": "Plan"}}
    sess.tool_history = [("Read: /src/app.py", True), ("Bash: npm run build", "failed"),
                         ("Edit: /src/app.py", False)]
    out.append(bot._build_session_dashboard(sess))
    sess.active_subagents = {f"a{i}": {"type": f"t{i}"} for i in range(4)}
    for ago, status in ((2, Status.IDLE), (300, Status.IDLE), (3900, Status.IDLE),
                        (None, Status.IDLE), (None, Status.GONE)):
        plain = env.mk_bot()
        plain._read_status_file = lambda name: None
        other = _registered(plain, _sess(status=status))
        other.last_hook_at = NOW - ago if ago else 0.0
        other.last_lines_added = 3
        out.append(plain._build_session_dashboard(other))
    return out


def _gone(bot, name, label, *, gone_at, resumable=True, preview=""):
    from aipager.state import Status
    sess = _sess(name=name, label=label, status=Status.GONE)
    sess.gone_at = gone_at
    sess.claude_session_id = "abc" if resumable else None
    sess.last_assistant_preview = preview
    sess.transcript_path = None
    return _registered(bot, sess)


@surface("resume.picker")
def _resume_picker(env):
    env.store(CHAT)
    out = []
    bot = env.mk_bot()
    text, kb = bot._render_resume_picker(0, CHAT)
    out += [text, *_inline(kb)]
    _gone(bot, "claude-h1", "h1", gone_at=WALL - 10, resumable=False)
    _gone(bot, "claude-h2", "h2", gone_at=WALL - 20, resumable=False)
    text, kb = bot._render_resume_picker(0, CHAT)
    out += [text, *_inline(kb)]
    _gone(bot, "claude-x4", "x4", gone_at=WALL - 30, preview="Fixed the tests.")
    _gone(bot, "claude-x5", "x5", gone_at=WALL - 600)
    _gone(bot, "claude-x6", "x6", gone_at=WALL - 7200)
    _gone(bot, "claude-x7", "x7", gone_at=WALL - 200000)
    _gone(bot, "claude-x8", "x8", gone_at=0.0)
    text, kb = bot._render_resume_picker(0, CHAT)
    out += [text, *_inline(kb)]
    many = env.mk_bot()
    for i in range(23):
        _gone(many, f"claude-p{i}", f"p{i}", gone_at=WALL - 60 * (i + 1))
    text, kb = many._render_resume_picker(1, CHAT)
    out += [text, *_inline(kb)]
    for seconds in (0, 1, 59, 61, 3600, 172800):
        out.append(env.with_tr(bot._fmt_gone_ago, WALL - seconds))
    out.append(env.with_tr(bot._fmt_gone_ago, None))
    return out


# ---- the Mini App (phase 4) -------------------------------------------------------------
# Driven through its HTTP routes with an aiohttp TestClient, as the viewer
# CHAT (personal mode's operator, so the language stored for CHAT is both
# the viewer's and the chat's). The bot's session cores are stubbed: what is
# rendered here is the server's own text (details, chat lines, facts,
# reasons, the schema), never the session operations behind it. Text that
# arrives from code outside the Mini App (a launch failure, "Auto needs an
# admin.") is left out: it is not the Mini App's to translate.

_MINIAPP_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


def _real(module: str, name: str):
    """The unpatched *name* of *module*, read once at import: a surface
    that needs the real one sets it back explicitly (the monkeypatch is
    shared by every surface of one run, and is never undone)."""
    import importlib
    return getattr(importlib.import_module(module), name)


_REAL_CREATE_DIRECTORY = _real("aipager.miniapp.launch", "create_directory")
_REAL_ALLOWED_ROOTS = _real("aipager.miniapp.launch", "allowed_roots")


def _miniapp_hdr(user_id=CHAT):
    import hashlib
    import hmac
    from urllib.parse import urlencode
    fields = {"auth_date": str(int(WALL)), "user": json.dumps({"id": user_id, "first_name": "T"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", _MINIAPP_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(fields)}


def _miniapp(env, *, admin=True, project_dir="/tmp"):
    """A Mini App server for CHAT with frozen clocks and stubbed cores."""
    import importlib
    from aipager.miniapp import server as server_mod
    from aipager.miniapp.server import MiniAppServer
    env.mp.setattr("aipager.config.BOT_TOKEN", _MINIAPP_TOKEN)
    env.mp.setattr("aipager.dtach.inject._PROJECT_DIR", project_dir)
    env.mp.setattr("aipager.miniapp.launch.create_directory", _REAL_CREATE_DIRECTORY)
    env.mp.setattr("aipager.miniapp.launch.allowed_roots", _REAL_ALLOWED_ROOTS)
    env.mp.setattr("aipager.miniapp.auth.time", SimpleNamespace(time=lambda: WALL),
                   raising=False)
    fake = SimpleNamespace(monotonic=lambda: NOW, time=lambda: WALL)
    env.mp.setattr(server_mod, "time", fake)
    sessions_mod = importlib.import_module("aipager.miniapp.sessions")
    env.mp.setattr(sessions_mod, "latest_stats",
                   lambda sess: {"model": "Opus 4.6", "context_pct": 45, "cost_usd": 1.5})
    env.mp.setattr(sessions_mod, "combined_queue_depth", lambda sess: 2)
    env.mp.setattr(sessions_mod, "cached_last_assistant_preview", lambda path: "")
    env.store(CHAT)
    bot = env.mk_bot()
    bot._app.bot.send_message = AsyncMock()
    bot._app.bot.username = "aipager_test_bot"
    bot._is_admin_user = lambda user_id, chat_id: admin
    bot._is_update_admin = lambda user_id, chat_id: admin
    bot._can_prompt_user = lambda user_id, chat_id: True
    bot._update_bot_commands = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    srv = MiniAppServer(bot, bot.registry, port=8799)
    srv._allow_write = lambda user_id: True
    return srv


def _ma_call(env, srv, method, path, body=None):
    from aiohttp.test_utils import TestClient, TestServer

    async def go():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            kwargs = {"headers": _miniapp_hdr()}
            if body is not None:
                kwargs["data"] = json.dumps(body)
            resp = await client.request(method, path, **kwargs)
            return resp.status, await resp.json()
        finally:
            await client.close()
    return env.run(go())


def _ma_sess(srv, label=LABEL, **fields):
    from aipager.state import Status
    sess = srv.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.scope_chat_id = CHAT
    sess.scope_kind = "dm"
    sess.status = fields.pop("status", Status.IDLE)
    for key, value in fields.items():
        setattr(sess, key, value)
    return sess


def _sent(srv) -> list[str]:
    return [c.kwargs["text"] for c in srv.bot._app.bot.send_message.await_args_list]


def _ok(**fields):
    return AsyncMock(return_value=SimpleNamespace(**fields))


@surface("miniapp.mirrors")
def _miniapp_mirrors(env):
    from aipager.state import Status
    srv = _miniapp(env)
    bot = srv.bot
    _ma_sess(srv, cwd="/tmp")
    bot.create_session = AsyncMock(return_value=("claude-n1", ""))
    _ma_call(env, srv, "POST", "/api/sessions", {"name": "n1"})
    _ma_call(env, srv, "POST", "/api/sessions", {"name": "n2", "cwd": "/tmp"})
    _ma_call(env, srv, "POST", "/api/sessions", {"name": "n3", "mode": "full"})
    env.mp.setattr("aipager.miniapp.launch.create_directory",
                   lambda parent, name, roots, confined=False: ("/home/op/proj/web", False, ""))
    _ma_call(env, srv, "POST", "/api/directories", {"parent": "/tmp", "name": "web"})
    # From the defaults, whatever an earlier surface set: a change mirrors.
    from aipager import preferences
    preferences.set_preference(CHAT, "layout", "card")
    preferences.set_preference(CHAT, "answer_length", "none")
    _ma_call(env, srv, "PUT", "/api/preferences/layout", {"value": "merged"})
    _ma_call(env, srv, "PUT", "/api/preferences/answer_length", {"value": "short"})
    _ma_call(env, srv, "PUT", f"/api/sessions/{LABEL}/preferences/answer_length",
             {"value": "short"})
    _ma_call(env, srv, "DELETE", f"/api/sessions/{LABEL}/preferences/answer_length")
    # Into Full access and out of it (a restart each), then Ask -> Auto in
    # place (no keys written: the presses are a stand-in).
    bot._perms_switch_core = _ok(ok=True, reason="done", label=LABEL, err="")
    env.mp.setattr("aipager.dtach.inject.press_mode_cycle", AsyncMock(return_value=True))
    _ma_call(env, srv, "POST", f"/api/sessions/{LABEL}/perms", {"mode": "full"})
    mode_sess = srv.registry.find_by_label(LABEL, scope_chat_id=CHAT)
    mode_sess.perm_mode = "full"
    _ma_call(env, srv, "POST", f"/api/sessions/{LABEL}/perms", {"mode": "ask"})
    mode_sess.perm_mode = "ask"
    _ma_call(env, srv, "POST", f"/api/sessions/{LABEL}/perms", {"mode": "auto"})
    mode_sess.reset_mode_tracking()
    for dropped in (1, 2, 5, 11, 21, 22, 25):
        bot._clear_queue_core = _ok(ok=True, label=LABEL, dropped=dropped)
        _ma_call(env, srv, "POST", f"/api/sessions/{LABEL}/clearqueue")
    for dropped in (0, 1, 2, 5, 21):
        bot._stop_session_core = _ok(ok=True, label=LABEL, dropped=dropped)
        _ma_call(env, srv, "POST", f"/api/sessions/{LABEL}/stop")
    bot._compact_session_core = _ok(reason="sent", label=LABEL)
    _ma_call(env, srv, "POST", f"/api/sessions/{LABEL}/compact")
    bot._switch_model_core = _ok(ok=True, label=LABEL, previous_model="Sonnet 5")
    env.mp.setattr("aipager.bot.session_ops.await_model_change",
                   AsyncMock(return_value="claude-opus-5"))
    _ma_call(env, srv, "POST", f"/api/sessions/{LABEL}/model", {"model": "claude-opus-5"})
    bot._restart_session_core = _ok(ok=True, reason=None, label=LABEL, err="")
    _ma_call(env, srv, "POST", f"/api/sessions/{LABEL}/restart")
    bot._rename_session_core = _ok(changed=True, previous_label=LABEL, new_label="x2")
    _ma_call(env, srv, "POST", f"/api/sessions/{LABEL}/rename", {"label": "x2"})
    bot._kill_session_core = _ok(result="killed", label=LABEL)
    _ma_call(env, srv, "POST", f"/api/sessions/{LABEL}/kill")
    bot._resume_full_gate = lambda sess, override, actor, chat: (None, False)
    bot._do_resume_core = _ok(reason=None, err="")
    _ma_call(env, srv, "POST", f"/api/sessions/{LABEL}/resume")
    _ma_sess(srv, "x3", status=Status.GONE)
    _ma_call(env, srv, "DELETE", "/api/sessions/x3")
    # A finished session whose conversation a Claude Code background job
    # held, brought back from the app: "is back" (claude-background-sessions).
    env.mp.setattr("aipager.session_monitor.SessionMonitor._held_conversation",
                   staticmethod(lambda sess: "held"))
    _ma_sess(srv, "x6", status=Status.GONE)
    _ma_call(env, srv, "POST", "/api/sessions/x6/bringback")
    return _sent(srv)


def _detail_of(env, srv, method, path, body=None) -> str:
    status, data = _ma_call(env, srv, method, path, body)
    assert "detail" in data, (method, path, status, data)
    return data["detail"]


@surface("miniapp.details")
def _miniapp_details(env):
    from aipager.bot.dashboard import RESEND_BUSY, RESEND_NOT_RESENDABLE, RESEND_NOT_WAITING
    from aipager.miniapp import sessions as ma_sessions
    from aipager.state import Status
    out = []
    srv = _miniapp(env)
    bot = srv.bot
    _ma_sess(srv, cwd="/tmp")
    d = lambda *a: out.append(_detail_of(env, srv, *a))  # noqa: E731
    for name in (5, " ", "a" * 70, "-x", "status"):
        d("POST", "/api/sessions", {"name": name})
    for cwd in (5, "/tmp/\x00", "/nonexistent-aipager-dir", "/usr"):
        d("POST", "/api/sessions", {"name": "n1", "cwd": cwd})
    for model in (5, "m" * 130, "-x"):
        d("POST", "/api/sessions", {"name": "n1", "model": model})
    d("POST", "/api/sessions", {"name": LABEL})
    for parent, name in (("", "web"), ("/tmp", 5), ("/tmp", " "), ("/tmp", "a" * 70),
                         ("/tmp", "a/b"), ("/tmp", "a\x00")):
        d("POST", "/api/directories", {"parent": parent, "name": name})
    # The answer route.
    d("POST", f"/api/sessions/{LABEL}/answer")
    srv.registry.find_by_label(LABEL, scope_chat_id=CHAT).status = Status.INTERACTIVE
    for outcome in (RESEND_NOT_WAITING, RESEND_NOT_RESENDABLE, RESEND_BUSY, "dropped"):
        bot._resend_pending_prompt = AsyncMock(return_value=outcome)
        d("POST", f"/api/sessions/{LABEL}/answer")
    srv.registry.find_by_label(LABEL, scope_chat_id=CHAT).status = Status.IDLE
    bot._stop_session_core = _ok(ok=False, label=LABEL, dropped=0)
    d("POST", f"/api/sessions/{LABEL}/stop")
    for result in ("still_running", "resuming"):
        bot._kill_session_core = _ok(result=result, label=LABEL)
        d("POST", f"/api/sessions/{LABEL}/kill")
    bot._resume_full_gate = lambda sess, override, actor, chat: (None, False)
    for reason in ("not_gone", "no_transcript"):
        bot._do_resume_core = _ok(reason=reason, err="")
        d("POST", f"/api/sessions/{LABEL}/resume")
    d("DELETE", f"/api/sessions/{LABEL}")
    gone = _ma_sess(srv, "x4", status=Status.GONE)
    gone.is_resuming = lambda: True
    d("DELETE", "/api/sessions/x4")
    for reason in ("not_live", "already_restarting", "still_stopping"):
        bot._perms_switch_core = _ok(ok=False, reason=reason, label=LABEL, err="")
        d("POST", f"/api/sessions/{LABEL}/perms", {"mode": "full"})
        bot._restart_session_core = _ok(ok=False, reason=reason, label=LABEL, err="")
        d("POST", f"/api/sessions/{LABEL}/restart")
    bot._clear_queue_core = _ok(ok=False, label=LABEL, dropped=0)
    d("POST", f"/api/sessions/{LABEL}/clearqueue")
    for reason in ("not_live", "queue_full", "send_failed"):
        bot._compact_session_core = _ok(reason=reason, label=LABEL)
        d("POST", f"/api/sessions/{LABEL}/compact")
    for model in ("", "-x"):
        d("POST", f"/api/sessions/{LABEL}/model", {"model": model})
    bot._switch_model_core = _ok(ok=False, reason="send_failed", detail="", label=LABEL)
    d("POST", f"/api/sessions/{LABEL}/model", {"model": "opus"})
    for code, text in (("not_live", ma_sessions.MODEL_SWITCH_NOT_LIVE_REASON),
                       ("busy", ma_sessions.MODEL_SWITCH_BUSY_REASON),
                       ("prompt_open", ma_sessions.MODEL_SWITCH_PROMPT_OPEN_REASON),
                       ("unknown", ma_sessions.MODEL_SWITCH_UNKNOWN_REASON),
                       ("switch_pending", ma_sessions.MODEL_SWITCH_PENDING_REASON)):
        bot._switch_model_core = _ok(ok=False, reason=code, detail=text, label=LABEL)
        d("POST", f"/api/sessions/{LABEL}/model", {"model": "opus"})
    bot._switch_model_core = _ok(ok=True, label=LABEL, previous_model="Sonnet 5")
    env.mp.setattr("aipager.bot.session_ops.await_model_change", AsyncMock(return_value=None))
    d("POST", f"/api/sessions/{LABEL}/model", {"model": "opus"})
    for label in ("a b", "x5"):
        if label == "x5":
            _ma_sess(srv, "x5")
        d("POST", f"/api/sessions/{LABEL}/rename", {"label": label})
    # Full access mode needs an admin: creating in it, switching to it.
    srv2 = _miniapp(env, admin=False)
    _ma_sess(srv2)
    out.append(_detail_of(env, srv2, "POST", "/api/sessions", {"name": "n1", "mode": "full"}))
    out.append(_detail_of(env, srv2, "POST", f"/api/sessions/{LABEL}/perms", {"mode": "full"}))
    # No folder yet: nothing in this chat has run anywhere.
    srv3 = _miniapp(env, project_dir="")
    out.append(_detail_of(env, srv3, "POST", "/api/sessions", {"name": "n1", "cwd": "/tmp"}))
    # A Claude Code background job in the way (claude-background-sessions).
    bg_cases = (("bg_busy", ()), ("bg_stop_failed", ("stop_timeout", {})),
                ("bg_stop_failed", ("stop_exit", {"code": 1})),
                ("bg_stop_failed", ("still_running", {})),
                ("bg_stop_failed", ("no_claude", {})), ("bg_no_conversation", ()),
                ("not_parked", ()))
    for reason, err in bg_cases:
        bot._bring_back_core = _ok(ok=False, reason=reason, label=LABEL, err="", bg_err=err)
        out.append(_detail_of(env, srv, "POST", f"/api/sessions/{LABEL}/bringback"))
    for reason, err in bg_cases[:3]:
        bot._restart_session_core = _ok(ok=False, reason=reason, label=LABEL, err="",
                                        bg_err=err)
        out.append(_detail_of(env, srv, "POST", f"/api/sessions/{LABEL}/restart"))
        bot._do_resume_core = _ok(ok=False, reason=reason, err="", bg_err=err)
        out.append(_detail_of(env, srv, "POST", f"/api/sessions/{LABEL}/resume"))
    return out


@surface("miniapp.payload_texts")
def _miniapp_payload_texts(env):
    from aipager.state import Status
    out = []
    srv = _miniapp(env, admin=False)
    _ma_sess(srv, status=Status.BUSY, cwd="/home/op/proj", last_hook_at=NOW - 125,
             busy_started_at=NOW - 192, model_name="Opus 4.6")
    _ma_sess(srv, "x2", status=Status.IDLE, last_hook_at=NOW - 7300)
    _ma_sess(srv, "x3", status=Status.GONE, last_hook_at=NOW - 90000)
    _ma_sess(srv, "x4", status=Status.INTERACTIVE, last_hook_at=NOW - 30)

    def reasons(data):
        texts = []
        for fact in data.get("facts") or []:
            texts += [fact["label"], fact["value"]]
        for spec in (data.get("actions") or {}).values():
            if spec.get("reason"):
                texts.append(spec["reason"])
        for key in ("model_switch", "answer"):
            if data.get(key) and data[key].get("reason"):
                texts.append(data[key]["reason"])
        return texts
    for label in (LABEL, "x2", "x3", "x4"):
        _status, data = _ma_call(env, srv, "GET", f"/api/sessions/{label}")
        out += reasons(data)
    may_prompt = srv.bot._can_prompt_user
    srv.bot._can_prompt_user = lambda user_id, chat_id: False
    for label in (LABEL, "x4"):
        _status, data = _ma_call(env, srv, "GET", f"/api/sessions/{label}")
        out += reasons(data)
    _status, data = _ma_call(env, srv, "GET", "/api/chats")
    out += [c["label"] for c in data["chats"]]
    for path in ("/api/preferences", "/api/session-options",
                 f"/api/sessions/{LABEL}/preferences"):
        _status, data = _ma_call(env, srv, "GET", path)
        for entry in data["schema"]:
            out.append(entry["title"])
            for option in entry["options"]:
                out += [option["label"], option["help"]]
    # In Claude Code's background, with no turn running here
    # (claude-background-sessions).
    srv.bot._can_prompt_user = may_prompt
    _ma_sess(srv, "x5", status=Status.IDLE, last_hook_at=NOW - 30, parked_job="ab12cd34",
             parked_since=WALL - 90)
    _status, data = _ma_call(env, srv, "GET", "/api/sessions/x5")
    out += reasons(data)
    return out


# ---- running them ---------------------------------------------------------------------------

def render_all(mk_bot, run_async, monkeypatch, code: str = "en",
               names=None, decoy: str | None = None) -> dict[str, list[str]]:
    """Every surface (or those in *names*) rendered with *code* stored for
    its chats (and *decoy* for chats it does not render in). Each surface
    patches through its own context, so what one surface pins (a queue
    depth, a restart delay) never leaks into the next; the fixture's own
    patches (the home redirects) stay."""
    out: dict[str, list[str]] = {}
    for name, fn in SURFACES:
        if names is not None and name not in names:
            continue
        with monkeypatch.context() as mp:
            env = Env(mk_bot, run_async, mp, code, decoy=decoy)
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
    "settings.per_session", "settings.problem_reports", "settings.taps", "settings.appearance",
    "menu.commands", "menu.button",
    "card.busy", "card.final", "card.waiting", "card.status_lines", "card.legacy",
    "card.helpers",
    "card.full_log", "card.thinking", "card.paused", "card.compacting", "prompt.detail",
    "agents_line", "prompt.keyboards", "prompt.separate", "prompt.inline",
    "prompt.question_inline", "prompt.loading", "prompt.stop_taps",
    "answer.headers", "answer.job_ends",
    "pinned", "pinned.target", "pinned.answer_taps",
    "status.list", "status.switch",
    # Phase 3b.
    "update.prompt", "update.check", "update.job", "update.steps", "update.marker",
    "update.taps", "update.restart", "report.card", "report.taps", "report.offer", "report.outcomes",
    "auth.refusals", "auth.auto_deny", "group.texts", "group.dropped",
    "transport.texts", "lifecycle.recovered", "dashboard.loading", "dashboard.diff_footer",
    "resume.dashboard", "resume.picker",
    "miniapp.mirrors", "miniapp.details", "miniapp.payload_texts",
    # Phase 5.
    "language.offer", "language.first_contact", "bot.descriptions",
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
    # Phase 3b: data from modules that are not text (install kinds and
    # reasons, restart blockers and reasons, installer output, error types,
    # roles, a report reference, Claude's preview).
    "pipx", "editable install", "npm prefix not writable", "no service unit",
    "running a turn", "waiting on a question", "held answer not yet delivered",
    "claude not found", "downloading", "npm failed", "still resolving", "resolver error",
    "ImportError", "KeyError", "disk full", "no systemd-run", "read_only", "developer",
    "ap1-x", "zzz", "Sentry", "Fixed the tests.", "aipager start", "x9",
    # The Mini App button reads "📱 App" in every language (operator,
    # 2026-10-10), and the language list names each language in its own
    # name: names, not text.
    "App",
) + tuple(loc.endonym for loc in LOCALES.values())

#: Surfaces whose whole text is names that are never translated (the menu
#: button is "📱 App" in every language): no language's script to find there.
NAMES_ONLY = {"menu.button"}

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


# ---- phase 3a ----------------------------------------------------------------------------------

#: Registered surfaces whose migration has not landed yet (rendered for
#: the English snapshot only), and names a surface shows that the pseudo
#: sweep must not count as English, by surface. Both are filled by the
#: phase 3a registry imported below.
PENDING: set[str] = set()

#: The surfaces that carry Claude's ANSWER, which is English in every
#: scenario here: their direction is that text's, not the chat's
#: (multilingual design 5.7, rule 5), so in fa and ar they read left to
#: right. The right-to-left sweeps check them with
#: :func:`assert_follows_claude`; tests/test_message_direction.py covers
#: both directions. The busy card (card.busy, card.final, card.waiting,
#: card.helpers, card.legacy) is not one of them: it takes the chat's
#: direction whatever Claude writes (operator, 2026-10-17), so in fa and
#: ar it is judged like every other right-to-left surface, its English
#: commentary isolated (tests/test_card_follows_chat_language.py).
FOLLOWS_CLAUDE = frozenset({"answer.headers", "held.late_marker"})


def assert_follows_claude(name: str, text: str) -> None:
    """*text* (a surface of :data:`FOLLOWS_CLAUDE`) reads left to right:
    on every line the first character that decides a direction (a mark,
    or a letter even inside an isolate; tags are not text) is U+200E or a
    left-to-right letter."""
    import re
    import unicodedata
    for line in text.split("\n"):
        for ch in re.sub(r"<[^>]+>", "", line):
            if ch == "\u200e":
                break
            bidi = unicodedata.bidirectional(ch)
            assert ch != "\u200f" and bidi not in ("R", "AL"), (name, line)
            if bidi == "L":
                break
KNOWN_EXTRA: dict[str, tuple[str, ...]] = {
    # Claude's own text (an agent's prose, its Agent call's description),
    # agent types, tool names and a tool's file, pattern, query or site:
    # shown as written in every language.
    "agents_line": ("Writing final.md for the pipeline", "Running the full suite",
                    "Writing", "for", "the", "pipeline", "Running", "full", "suite",
                    "pipeline-runner", "state.py", "final.md", "notify.py", "a.py",
                    "n.ipynb", "agents\\_line", "agents_line", "telegram bot api",
                    "docs.python.org", "TodoWrite", "Read",
                    # The activity phrases: Claude Code's English words,
                    # never translated (operator, 2026-10-10).
                    "Reading", "Editing", "Searching", "web", "Fetching", "command",
                    "Starting", "an", "agent", "Using",
                    # The tree's agent types and Claude's words in it.
                    "ship-tester", "ship-developer", "ship-reviewer", "r2-runner",
                    "Explore", "Plan", "general\\_purpose", "general_purpose",
                    "Write and run criteria tests", "Collect line numbers",
                    "Write", "and", "run", "criteria", "tests", "Collect", "line",
                    "numbers", "Editing state.py"),
    # The manual restart steps' commands and key names, shown as plain
    # text in the Mini App: data, never translated.
    "update.restart": ("systemctl --user restart aipager.service", "docker restart",
                       "<container>", "launchctl kickstart -k gui/501/com.aipager.daemon",
                       "pipx install --force aipager", "aipager start", "Ctrl+C",
                       "launchctl", "kickstart", "com.aipager.daemon", "daemon"),
}
from tests import i18n_surfaces_p3a  # noqa: E402,F401
