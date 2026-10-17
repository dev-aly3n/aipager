"""Check 14 of the multilingual design: everything Claude reads stays
byte-identical.

aipager's own text is being routed through ``aipager.i18n`` so it can be
shown in other languages. What Claude reads must never take that path: a
translated marker, deny reason or style line changes Claude's behaviour.
This test renders every Claude-facing producer and compares the result to
``tests/golden/claude_facing.json``, recorded from the code before the
i18n work began. A difference means text Claude reads has changed.

The corpus: the style lines for every reply-style combination, the
``[via Telegram ...]`` marker variants, the typed body of a free prompt, a
template and a command, ``_file_prompt``, the reply-context sentences, the
PreToolUse deny payloads and safety reasons, the PermissionRequest
decisions (including "Denied via aipager by @x" captured from a real deny
tap), the default template, command and model sends, and SESSION.md's
notes. Regenerate only for an intended change to what Claude reads, by
running this file with ``AIPAGER_RECORD_CLAUDE_GOLDEN=1`` (never as part
of an i18n change).
"""

from __future__ import annotations

import datetime as _dt
import itertools
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

GOLDEN = Path(__file__).parent / "golden" / "claude_facing.json"

GROUP = -1001
DM = 555
ALY, BOB = 1, 2
HOOK = {"addr": "/nonexistent/aipager-reply-x.sock", "request_id": "req-1"}


def _scopes():
    from aipager.scope import Member, Scope
    return [
        Scope(chat_id=GROUP, kind="group", label="team", members=(
            Member(id=ALY, label="aly", role="owner"),
            Member(id=BOB, label="bob", role="user"),
        )),
        Scope(chat_id=DM, kind="dm", label="aly DM",
              members=(Member(id=ALY, label="aly", role="owner"),)),
    ]


def _style_lines() -> dict[str, str]:
    from aipager import preferences as p
    out = {}
    for fmt, length, level in itertools.product(
            (False, True), p._VALID_ANSWER_LENGTH, p._VALID_LANGUAGE_LEVEL):
        prefs = p.Preferences(layout="card", simple_formatting=fmt,
                              answer_length=length, language_level=level)
        out[f"style.{int(fmt)}.{length}.{level}"] = p.style_text(prefs)
    return out


def _file_prompts() -> dict[str, str]:
    from aipager.bot.handlers import _file_prompt
    one = [Path("/work/a.png")]
    two = [Path("/work/a.png"), Path("/work/b.pdf")]
    return {
        "file.one": _file_prompt("", one, all_photos=True),
        "file.two": _file_prompt("", two, all_photos=False),
        "file.caption": _file_prompt("fix this", two, all_photos=False),
    }


def _reply_contexts() -> dict[str, str]:
    from aipager.bot import session_ops as so
    date = _dt.datetime(2026, 10, 9, 21, 40)
    out = {}
    for author in ("you", "Telegram user 256113222", None):
        a = author or "none"
        out[f"reply.whole.file.{a}"] = so._whole_message_context(
            "an excerpt", date, author, with_file=True,
            file_path="/path/reply.txt")
        out[f"reply.whole.queued.{a}"] = so._whole_message_context(
            "an excerpt", None, author, with_file=False, file_path=None)
        out[f"reply.nontext.{a}"] = so._nontext_context(date, author)
        for manual in (True, False):
            out[f"reply.highlight.{int(manual)}.{a}"] = so._highlight_context(
                "a fragment", date, author, manual=manual,
                file_path="/path/reply.txt" if manual else None)
    out["reply.external"] = so._external_quote_context("a fragment")
    # The reply-context builder itself, on a reply to an older message
    # with no file (a busy session).
    sess = SimpleNamespace(name="claude-x1", last_msg_id=5, busy_msg_id=None,
                           trigger_msg_id=None)
    reply_to = SimpleNamespace(message_id=3, text="x" * 100, caption=None,
                               date=date, from_user=SimpleNamespace(id=999))
    msg = SimpleNamespace(reply_to_message=reply_to, quote=None)
    out["reply.built.older"] = so.SessionOpsMixin._build_reply_context(
        None, msg, sess, bot_id=999, allow_file=False)
    return out


def _safety() -> dict[str, str]:
    from aipager import safety
    from aipager.dtach import enforce, hook_reply, model_switch_marker
    from aipager.dtach.notify_hook import _TASK_NOTIFICATION_PREFIX
    out = {
        "block_marker": enforce._BLOCK_MARKER,
        "bash_reason": safety.BASH_REASON,
        "bash_search_reason": safety.BASH_SEARCH_REASON,
        "model_switch_allow": model_switch_marker.ALLOW_REASON,
        "task_notification_prefix": _TASK_NOTIFICATION_PREFIX,
        "pretool.deny": enforce.deny_decision_json("Bash command blocked by safety policy"),
        "pretool.soft_deny": enforce.soft_deny_json(
            "SendUserFile refused, nothing was sent: a.png (no such file). Tell "
            "the person which file could not be sent and why."),
        "hook.allow": json.dumps(hook_reply.allow_decision()),
        "hook.allow_always": json.dumps(hook_reply.allow_decision(
            updated_permissions=[{"type": "addRules"}])),
        "hook.deny": json.dumps(hook_reply.deny_decision("Denied via aipager")),
    }
    commands = (
        "sudo rm -rf /", "cat ~/.claude/settings.json", "systemctl stop aipager",
        "claude -p hi", "grep -r token ~", "find / -name x", "ls -R ~",
        "rm -rf ~/.config/aipager", "echo ok",
    )
    for i, cmd in enumerate(commands):
        out[f"bash.{i}"] = repr(safety.bash_violation(cmd, safety.DENY_BASH_PATTERNS))
    out["tool.deny"] = repr(safety.tool_violation("Bash", ("Bash",), ()))
    out["tool.allow"] = repr(safety.tool_violation("Write", (), ("Read",)))
    paths = (
        ("Read", {"file_path": "/home/u/.claude/settings.json"}),
        ("Write", {"file_path": "/home/u/.config/aipager/x"}),
        ("Edit", {"file_path": "/etc/passwd"}),
        ("Read", {"file_path": "\x00"}),
    )
    for i, (tool, ti) in enumerate(paths):
        out[f"path.{i}"] = repr(safety.path_violation(
            tool, ti, ("~/.claude/**", "**/.config/aipager/**"), (),
            cwd="/work", write_roots=("/work",)))
    return out


def _send() -> dict[str, str]:
    """What Claude reads when a file it asked to send is refused."""
    from aipager import send_check as sc
    out = {}
    for code in sc._WHY_WORDS:
        one = sc.Refusal("/work/a.png", code, sc._why(code))
        two = sc.Refusal("b.pdf", "missing", sc._why("missing"))
        out[f"send.reason.{code}.one"] = sc.refusal_reason([one])
        out[f"send.reason.{code}.two"] = sc.refusal_reason([two, one])
    out["send.reason.unreadable_list"] = sc.refusal_reason(
        [sc.Refusal("", "unreadable_list", "")])
    out["send.turn_rules"] = sc.turn_rule_reason(
        "SendUserFile on protected path ~/.claude/**")
    out["send.unchecked"] = sc.SEND_UNCHECKED_REASON
    return out


def _defaults() -> dict[str, str]:
    from aipager import config
    out = {}
    for label, prompt in config._DEFAULT_TEMPLATES:
        out[f"template.{label}"] = prompt
    for label, send in config._DEFAULT_COMMANDS:
        out[f"command.{label}"] = send
    for label, send in config._DEFAULT_MODELS:
        out[f"model.{label}"] = send
    return out


def _session_md() -> dict[str, str]:
    from aipager.policy import load_policy
    from aipager.session_store import build_session_md
    policy = load_policy(Path("/nonexistent/policy.yaml"),
                         Path("/nonexistent/policy.d"))
    return {"session_md": build_session_md(_scopes()[0], policy, "x1")}


def _bot(mk_bot, monkeypatch):
    from aipager.dtach import hook_reply, inject
    from aipager.policy import load_policy
    bot = mk_bot(scopes=_scopes())
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"),
                             Path("/nonexistent/policy.d"))
    bot._app.bot.id = 999
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=77))
    bot._app.bot.edit_message_text = AsyncMock()
    for name in ("_maybe_update_bot_name", "_update_bot_commands",
                 "_edit_busy_raw", "_react"):
        setattr(bot, name, AsyncMock())
    bot._start_animation = MagicMock()
    bot._stop_animation = MagicMock()
    bot._watch_keystroke_answer = MagicMock()
    typed: list[str] = []
    decisions: list[dict] = []

    async def _keys(name, key, *a, **k):
        return True

    async def _text(name, body, *a, **k):
        typed.append(body)
        return True

    def _decide(reply, decision):
        decisions.append(decision)
        return True

    monkeypatch.setattr(inject, "send_keys", _keys)
    monkeypatch.setattr(inject, "send_text_and_enter", _text)
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr(hook_reply, "send_decision", _decide)
    return bot, typed, decisions


def _session(bot, name, chat, kind):
    from aipager.state import Status
    s = bot.registry.get_or_create(name)
    s.label = "x1"
    s.scope_chat_id, s.scope_kind = chat, kind
    s.status = Status.IDLE
    s.busy_msg_id = None
    return s


def _prompts(mk_bot, monkeypatch, run_async) -> dict[str, str]:
    from aipager import policy_snapshot
    bot, typed, _ = _bot(mk_bot, monkeypatch)
    notes: list[dict] = []
    monkeypatch.setattr(policy_snapshot, "write_note",
                        lambda *a, **k: notes.append(k))
    out = {}
    group = _session(bot, "claude-x1__g1001", GROUP, "group")
    dm = _session(bot, "claude-x1__d555", DM, "dm")
    cases = (
        ("group.member", group, "fix the bug", BOB, GROUP),
        ("group.unknown", group, "fix the bug", 4242, GROUP),
        ("dm.owner", dm, "fix the bug", ALY, DM),
        ("template", dm, "Run the tests", ALY, DM),
        ("command", dm, "/compact", ALY, DM),
    )
    for key, sess, text, uid, chat in cases:
        typed.clear()
        notes.clear()
        run_async(bot._inject_prompt(sess, text, "ctx", chat_id=chat,
                                     driver_user_id=uid, msg_id=9))
        out[f"prompt.{key}.typed"] = typed[0]
        out[f"prompt.{key}.style"] = notes[0]["style_text"]
        out[f"prompt.{key}.reply_context"] = notes[0]["reply_context"]
    out["marker.personal"] = repr(
        type(bot)._prompt_marker(SimpleNamespace(scopes=None), group, None))
    return out


def _deny_taps(mk_bot, monkeypatch, run_async) -> dict[str, str]:
    from aipager.state import Status
    out = {}
    for key, uid in (("member", BOB), ("anonymous", ALY)):
        bot, _typed, decisions = _bot(mk_bot, monkeypatch)
        if key == "anonymous":
            bot.scopes = None   # personal mode: nobody to name
        sess = _session(bot, "claude-x1__g1001", GROUP, "group")
        sess.status = Status.INTERACTIVE
        run_async(bot.notify(sess, "permission_prompt", {
            "tool_info": {"name": "Bash", "summary": "Bash: rm -rf build/",
                          "input": {"command": "rm -rf build/"}},
            "hook_reply": dict(HOOK)}))
        q = MagicMock()
        q.data = f"{sess.name}:deny"
        q.from_user = MagicMock(id=uid)
        q.message = MagicMock()
        q.message.chat = MagicMock(id=GROUP)
        q.message.chat_id = GROUP
        q.message.message_id = 77
        q.message.text = "🔐 x1 · Permission needed"
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.edit_message_reply_markup = AsyncMock()
        u = MagicMock()
        u.callback_query = q
        u.effective_chat = q.message.chat
        u.effective_user = q.from_user
        u.message = None
        run_async(bot._handle_callback(u, MagicMock()))
        out[f"deny_tap.{key}"] = json.dumps(decisions, sort_keys=True)
    return out


def build_corpus(mk_bot, monkeypatch, run_async) -> dict[str, str]:
    corpus: dict[str, str] = {}
    for part in (_style_lines(), _file_prompts(), _reply_contexts(), _safety(),
                 _send(), _defaults(), _session_md(),
                 _prompts(mk_bot, monkeypatch, run_async),
                 _deny_taps(mk_bot, monkeypatch, run_async)):
        assert not set(part) & set(corpus)
        corpus.update(part)
    return corpus


def _dump(corpus: dict[str, str]) -> str:
    return json.dumps(corpus, indent=1, sort_keys=True, ensure_ascii=True) + "\n"


def test_claude_facing_text_is_byte_identical(mk_bot, monkeypatch, run_async):
    corpus = build_corpus(mk_bot, monkeypatch, run_async)
    if os.environ.get("AIPAGER_RECORD_CLAUDE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(_dump(corpus), encoding="utf-8")
        pytest.skip("golden corpus recorded")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(corpus) == sorted(golden)
    changed = [k for k in corpus if corpus[k] != golden[k]]
    assert not changed, f"Claude-facing text changed: {changed}"


def test_the_deny_reason_names_the_person():
    """The corpus really holds the tap's deny message, not an empty list."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert "Denied via aipager by @bob" in golden["deny_tap.member"]
    assert '"Denied via aipager"' in golden["deny_tap.anonymous"]
    assert golden["prompt.group.member.typed"].startswith(
        "[via Telegram · @bob · role:user]\n")



# ---- the same bytes in every chat language (multilingual phase 2) ------------

@pytest.mark.parametrize("code", ["fa", "ru"])
def test_claude_reads_the_same_in_persian_and_russian(code, mk_bot, monkeypatch, run_async):
    """Every producer above, with the group and the private chat (and the
    people in them) set to Persian or Russian: Claude reads exactly the
    golden bytes (G73)."""
    from aipager import preferences
    for chat in (GROUP, DM, ALY, BOB):
        preferences.set_ui_language(chat, code)
    corpus = build_corpus(mk_bot, monkeypatch, run_async)
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    changed = [k for k in corpus if corpus[k] != golden.get(k)]
    assert not changed, f"Claude-facing text changed in {code}: {changed}"


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_translated_template_or_command_tap_sends_the_english(
        code, mk_bot, monkeypatch, run_async):
    """The keyboard shows the default template and command buttons in the
    chat's language; a tap on one sends Claude exactly the English prompt
    or command."""
    from aipager import config, i18n, preferences
    from aipager.bot import kbwords
    preferences.set_ui_language(DM, code)
    bot, typed, _ = _bot(mk_bot, monkeypatch)
    bot._template_map = dict(config._DEFAULT_TEMPLATES)
    bot._command_map = dict(config._DEFAULT_COMMANDS)
    sent_commands: list[str] = []

    async def _command(update, payload):
        sent_commands.append(payload)
    bot._send_command = _command
    sent_templates: list[str] = []

    async def _template(update, payload, *, button=""):
        sent_templates.append(payload)
    bot._send_template = _template
    tr = i18n.get(code)
    for (label, payload) in list(config._DEFAULT_TEMPLATES) + list(config._DEFAULT_COMMANDS):
        shown = kbwords.shown_label(tr, label, payload)
        # Commit stays English (the glossary's keep_english); the rest move.
        assert shown != label or label == "Commit", (code, label)
        update = MagicMock()
        update.message = MagicMock(text=shown, message_id=9, reply_to_message=None,
                                   quote=None, external_reply=None, forward_origin=None,
                                   via_bot=None, media_group_id=None, caption=None)
        update.message.reply_text = AsyncMock()
        update.effective_user = MagicMock(id=ALY)
        update.effective_chat = MagicMock(id=DM, type="private")
        run_async(bot._handle_message(update, MagicMock()))
    assert sent_templates == [p for _l, p in config._DEFAULT_TEMPLATES]
    assert sent_commands == [p for _l, p in config._DEFAULT_COMMANDS]
