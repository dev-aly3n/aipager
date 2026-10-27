"""One direction per message that carries Claude's text (multilingual
design 5.7, rule 5; operator live test 2026-10-10).

In a Persian or Arabic chat talking with Claude in English, the answer
(its header, its Finished line, the agents block under it, a late marker)
reads LEFT TO RIGHT: no U+200F at a line start, a U+200E where a line would
otherwise start with a right-to-left word, every right-to-left phrase kept
whole in U+2067...U+2069, parameters still isolated, numbers in the
language's digits. When Claude writes Persian or Arabic it reads right to
left as before; before Claude has written anything it follows the prompt
the turn answers; with neither, the chat's language. The busy card is
aipager's own (operator, 2026-10-17): it keeps the chat's direction
whatever Claude or the prompt is written in, Claude's English commentary
on it isolated left to right; only the merged layout's finished card,
which carries the answer, takes the answer's direction. English never
changes.

The card's rotating verb is the chat language's own (a short list per
language); English keeps SPINNER_VERBS byte for byte.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import config, i18n
from aipager.bot import agents_line, animation, card_rows, held_message
from aipager.bot.notify import finished_line
from aipager.bot.rich_message import message_rtl, text_direction
from aipager.dtach import hook_receiver
from aipager.i18n import Safe, locales
from aipager.state import SessionRegistry, Status
from tests import i18n_surfaces as surf
from tests.footer_text import unfooter

RLM, LRM, FSI, RLI, PDI = "\u200f", "\u200e", "\u2068", "\u2067", "\u2069"
LTR_LANGS = sorted(c for c, loc in locales.LOCALES.items() if not loc.rtl)
RTL_LANGS = ("fa", "ar")
ALL_LANGS = sorted(locales.LOCALES)
TRANSLATED = [c for c in ALL_LANGS if c != "en"]

#: Claude's words (and a prompt) in each script.
ENGLISH = "Let me read the file first."
PERSIAN = "ابتدا فایل را می‌خوانم."
ARABIC = "دعني أقرأ الملف أولا."
OWN_SCRIPT = {"fa": PERSIAN, "ar": ARABIC}


def _line_starts(text: str) -> list[str]:
    return [line for line in text.split("\n") if line.strip()]


def _no_rlm_at_line_start(text: str) -> bool:
    return not any(line.startswith(RLM) or line.startswith("⏳ " + RLM)
                   or line.startswith("✅ " + RLM) for line in _line_starts(text))


def _session(prompt: str = "", commentary: tuple[str, ...] = (), chat: int = 0):
    reg = SessionRegistry()
    s = reg.get_or_create("claude-aipager_boss")
    s.label = "aipager_boss"
    s.status = Status.BUSY
    if chat:
        s.scope_chat_id, s.scope_kind = chat, "dm"
    s.busy_started_at = time.monotonic() - 123
    s.cost_baseline, s.last_cost_usd = 0.0, 1.21
    s.tool_history = ([("Agent: x", True), ("Write: /tmp/x/args-dir.txt", True)]
                      + [(f"Bash: ls {i}", True) for i in range(3)]
                      + [("Read: /a", True), ("Read: /b", True), ("\U0001f916 pipeline-runner", False)])
    s.active_subagents = {"a1": {"type": "pipeline-runner",
                                 "activity": "Bash: Read verb msgs and animate loop",
                                 "started_at": time.monotonic() - 45, "history_idx": 7,
                                 "tools": []}}
    s.stream_commentary = [(0, c) for c in commentary]
    s.turn_prompt = prompt
    return s


def _status(card: str) -> str:
    return card.rsplit("\n", 1)[-1]


# ---- the i18n core: Tr.directed ------------------------------------------------------------

@pytest.mark.parametrize("code", LTR_LANGS)
def test_a_left_to_right_language_marks_its_lines_only_in_a_right_to_left_message(code):
    tr = i18n.get(code)
    assert tr.directed(None) is tr and tr.directed(False) is tr
    assert tr.directed(True, rich=False) is tr
    twin = tr.directed(True)
    assert twin is tr.directed(True) and twin is not tr
    assert twin.ltr_in_rtl and not tr.ltr_in_rtl
    assert not twin.rtl and not twin.language_rtl and twin.code == code
    assert twin.directed(None) is tr and twin.directed(False) is tr
    assert twin.directed(True) is twin


@pytest.mark.parametrize("code", RTL_LANGS)
def test_a_right_to_left_language_keeps_its_own_direction_by_default(code):
    tr = i18n.get(code)
    assert tr.directed(None) is tr and tr.directed(True) is tr
    ltr = tr.directed(False)
    assert ltr is not tr and not ltr.rtl and ltr.language_rtl and ltr.code == code
    assert ltr.directed(True) is tr and ltr.directed(False) is ltr


@pytest.mark.parametrize("code", RTL_LANGS)
def test_left_to_right_puts_no_rlm_but_an_lrm_where_a_line_opens_right_to_left(code):
    ltr = i18n.get(code).directed(False)
    text = ltr.md(animation.FINISHING)            # a line that is all the language's words
    assert RLM not in text
    assert text.startswith(LRM + RLI) and text.endswith(PDI)


@pytest.mark.parametrize("code", RTL_LANGS)
def test_left_to_right_keeps_the_isolates_and_the_digits(code):
    ltr = i18n.get(code).directed(False)
    rtl = i18n.get(code)
    lead = "\U0001f4ac **aipager\\_boss**"
    shown = finished_line(ltr, lead, "md", seconds=38)
    assert FSI + lead + PDI in shown
    assert rtl.clock(38) in shown and "0:38" not in shown


@pytest.mark.parametrize("code", RTL_LANGS)
def test_a_line_that_opens_left_to_right_gets_no_mark(code):
    ltr = i18n.get(code).directed(False)
    shown = ltr.md(animation.CARD_STATUS, mark=Safe("✅"), session="x1",
                   verb=Safe(animation._verb(ltr, "Working")))
    assert not shown.startswith(LRM) and RLM not in shown


_ENGLISH_RENDERS = (
    lambda t: finished_line(t, "\U0001f4ac **x1**", "md", seconds=250, added=3),
    lambda t: t.n(animation.FOLD, 5, kind="md"),
    lambda t: held_message.late_marker_text(t, 600),
)


def test_english_renders_byte_for_byte_with_no_direction_or_left_to_right():
    en = i18n.get("en")
    for render in _ENGLISH_RENDERS:
        assert render(en) == render(en.directed(None)) == render(en.directed(False))
        assert render(en.directed(True, rich=False)) == render(en)


def test_english_in_a_right_to_left_message_gets_marks_only():
    en = i18n.get("en")
    for render in _ENGLISH_RENDERS:
        marked = str(render(en.directed(True)))
        assert marked.startswith(LRM) and marked.endswith(LRM) and RLM not in marked
        bare = marked.replace(LRM, "").replace(FSI, "").replace(PDI, "")
        assert bare == render(en)


# ---- the direction rule ----------------------------------------------------------------------

@pytest.mark.parametrize("text,want", [
    ("", None), ("12 34", None), ("```\n$ ls\n```", False), (ENGLISH, False),
    (PERSIAN, True), (ARABIC, True), ("слово", False),
], ids=["empty", "digits", "code", "english", "persian", "arabic", "russian"])
def test_text_direction(text, want):
    assert text_direction(text) is want


def test_message_rtl_takes_the_first_text_with_a_letter_then_the_chat():
    assert message_rtl(True, ENGLISH, PERSIAN) is False
    assert message_rtl(False, "", PERSIAN) is True
    assert message_rtl(True, "", "42") is True
    assert message_rtl(False, "", "") is False


@pytest.mark.parametrize("prompt,raw,want", [
    ("[via Telegram · @bob]\nhello there", "hello there", "hello there"),
    ("[via Telegram · @bob]\nquoted context\nhello there", "hello there", "hello there"),
    ("[via Telegram]\nfresh words", "stale", "fresh words"),
    ("typed in the terminal", "stale", "typed in the terminal"),
], ids=["marker", "framed", "stale-raw", "terminal"])
def test_the_prompt_text_is_the_persons_own(prompt, raw, want):
    assert hook_receiver._person_text(prompt, raw) == want


# ---- the busy card ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", RTL_LANGS)
def test_card_under_english_commentary_keeps_the_chats_direction(code):
    # Changed 2026-10-17: the card follows the chat's language, not
    # Claude's English (was: reads left to right with no U+200F).
    s = _session(commentary=(ENGLISH,))
    card = unfooter(animation.build_stream_card(s, "Calculating", tr=i18n.get(code)))
    assert _status(card).startswith(RLM + "⏳ **" + FSI + "aipager_boss" + PDI + "**")
    assert RLI not in card
    # Claude's English sentence keeps its own direction inside the card.
    assert "> " + LRM + "\u2066" + ENGLISH + PDI + LRM in card.split("\n")
    agent = [line for line in card.split("\n") if "pipeline-runner" in line][0]
    assert agent.startswith("⏳ " + RLM)          # an agent row opens right to left
    assert animation._card_rtl(s, i18n.get(code)) is True


@pytest.mark.parametrize("code", RTL_LANGS)
def test_card_under_own_script_commentary_reads_right_to_left(code):
    s = _session(commentary=(OWN_SCRIPT[code],))
    card = unfooter(animation.build_stream_card(s, "Calculating", tr=i18n.get(code)))
    assert _status(card).startswith(RLM + "⏳")
    assert LRM not in card and RLI not in card
    assert animation._card_rtl(s, i18n.get(code)) is True


@pytest.mark.parametrize("code", RTL_LANGS)
@pytest.mark.parametrize("prompt", [ENGLISH, "own", "", "12"],
                         ids=["english-prompt", "own-prompt", "no-prompt", "digit-prompt"])
def test_card_before_commentary_follows_the_chat_whatever_the_prompt(code, prompt):
    # Changed 2026-10-17 (was: follows the prompt, then the chat).
    s = _session(prompt=OWN_SCRIPT[code] if prompt == "own" else prompt)
    s.active_subagents = {}
    s.tool_history = []
    card = unfooter(animation.build_stream_card(s, "Working", tr=i18n.get(code)))
    assert card.startswith(RLM)
    assert LRM not in card
    assert animation._card_rtl(s, i18n.get(code)) is True


@pytest.mark.parametrize("code,want", [("fa", True), ("ar", True), ("en", False),
                                       ("de", False)])
def test_neither_commentary_nor_prompt_turns_the_card(code, want):
    # Changed 2026-10-17 (was: Claude's commentary wins over the prompt).
    for prompt, words in ((PERSIAN, ENGLISH), (ENGLISH, PERSIAN), (ARABIC, ARABIC),
                          (ENGLISH, ENGLISH), ("", "")):
        s = _session(prompt=prompt, commentary=(words,) if words else ())
        assert animation._card_rtl(s, i18n.get(code)) is want


@pytest.mark.parametrize("code", RTL_LANGS)
def test_the_final_card_status_line_reads_right_to_left(code):
    # Changed 2026-10-17 (was: reads left to right under an English prompt).
    s = _session(prompt=ENGLISH, commentary=(ENGLISH,))
    s.active_subagents = {}
    status = _status(unfooter(animation.build_stream_card(
        s, animation.FINAL_VERB, final=True, tr=i18n.get(code))))
    tr = i18n.get(code)
    assert status.startswith(RLM + "✅ **") and status.endswith(RLM)
    assert LRM not in status and RLI not in status
    assert " · " + str(tr.md(animation._VERB_MSGS["Done"])) + " · " in status
    assert tr.clock(123) in status and "Agent ×" + tr.num(1) in status


@pytest.mark.parametrize("code", RTL_LANGS)
def test_the_fold_summary_and_still_running_row_read_left_to_right(code):
    ltr = i18n.get(code).directed(False)
    fold = ltr.n(animation.FOLD, 5, kind="md")
    assert fold.startswith(LRM + "▸ " + RLI) and RLM not in fold
    row = card_rows.live_agent_final(ltr, {"type": "pipeline-runner"})
    assert row.startswith(LRM) and RLM not in row


@pytest.mark.parametrize("code,other,want", [("fa", ENGLISH, True), ("ar", ENGLISH, True),
                                             ("en", PERSIAN, False), ("en", ARABIC, False)])
@pytest.mark.parametrize("final", [False, True], ids=["live", "final"])
def test_the_rich_card_edit_sends_the_chats_direction(code, other, want, final, mk_bot,
                                                      run_async, monkeypatch):
    # Changed 2026-10-17 (was: is_rtl False under an English prompt in fa/ar).
    from aipager import preferences
    from aipager.bot import animation as anim
    preferences.set_ui_language(4242, code)
    bot = mk_bot()
    s = _session(prompt=other, commentary=(other,), chat=4242)
    s.busy_msg_id = 55
    bot.registry._sessions[s.name] = s
    edit = AsyncMock(return_value={"message_id": 55})
    monkeypatch.setattr(anim, "edit_message_text_rich", edit)
    run_async(bot._edit_busy_rich(s, "Calculating", final=final))
    assert edit.await_args.kwargs["is_rtl"] is want
    status = unfooter(edit.await_args.args[2]).rsplit("\n", 1)[-1]
    assert status.startswith(RLM) is want


@pytest.mark.parametrize("code", RTL_LANGS)
def test_the_first_card_text_follows_the_chat(code, mk_bot, run_async):
    # Changed 2026-10-17 (was: follows the prompt).
    from aipager import preferences
    preferences.set_ui_language(4242, code)
    bot = mk_bot()
    s = _session(prompt=ENGLISH, chat=4242)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=7))
    run_async(bot.send_busy(s))
    text = bot._app.bot.send_message.await_args.args[1]
    assert text.startswith(RLM) and RLI not in text
    s.turn_prompt = OWN_SCRIPT[code]
    run_async(bot.send_busy(s))
    assert bot._app.bot.send_message.await_args.args[1] == text


def test_an_english_card_is_the_same_whatever_the_prompt():
    a = _session(prompt=PERSIAN, commentary=(ENGLISH,))
    b = _session(prompt="", commentary=(ENGLISH,))
    en = i18n.get("en")
    assert animation.build_stream_card(a, "Calculating", tr=en) == \
        animation.build_stream_card(b, "Calculating", tr=en)


# ---- the answer ------------------------------------------------------------------------------

def _answer(env, summary, *, prompt="", agents=False):
    rich = surf._rich(env)
    bot = env.mk_bot()
    sess = surf._registered(bot, surf._sess(status=Status.IDLE))
    sess.busy_started_at = surf.NOW - 38
    sess.busy_started_wall = 0.0
    sess.turn_prompt = prompt
    if agents:
        sess.bg_agent_started("a1", "pipeline-runner", surf.NOW)
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    env.run(bot.notify(sess, "idle_prompt", {"summary": summary}))
    # As shown: the footer blocks under the divider read as their text.
    return unfooter(rich.await_args.args[1]), rich.await_args.kwargs["is_rtl"], sess


@pytest.mark.parametrize("code", RTL_LANGS)
def test_an_english_answer_reads_left_to_right_header_and_agents_block(
        code, mk_bot, run_async, monkeypatch):
    env = surf.Env(mk_bot, run_async, monkeypatch, code)
    env.store(surf.CHAT)
    text, is_rtl, sess = _answer(env, ENGLISH, agents=True)
    assert is_rtl is False
    head, *_rest = text.split("\n", 1)
    assert head.startswith(LRM + FSI + "\U0001f4ac") and RLI in head
    assert RLM not in text
    block = text.rsplit("\n\n" + ENGLISH + "\n\n", 1)[1]
    assert block and all(not line.startswith(RLM) for line in block.split("\n"))
    assert LRM + RLI in block                          # its own words, left aligned
    assert sess.agents_lines[0]["is_rtl"] is False


@pytest.mark.parametrize("code", RTL_LANGS)
def test_an_answer_in_the_chats_script_reads_right_to_left(code, mk_bot, run_async, monkeypatch):
    env = surf.Env(mk_bot, run_async, monkeypatch, code)
    env.store(surf.CHAT)
    text, is_rtl, sess = _answer(env, OWN_SCRIPT[code], agents=True)
    assert is_rtl is True
    assert text.startswith(RLM) and LRM not in text and RLI not in text
    block = text.rsplit("\n\n", 3)
    assert all(part.startswith(RLM) for part in block[-3:])
    assert sess.agents_lines[0]["is_rtl"] is True


#: An English answer that is mostly a box-drawn table, and a Chinese one:
#: left to right (box drawing and CJK once counted as right-to-left).
BOX_TABLE_ANSWER = "Here is the result:\n\n```\n" + "\n".join(
    ["\u250c" + "\u2500" * 24 + "\u252c" + "\u2500" * 24 + "\u2510"]
    + ["\u2502 row %02d" % i + " " * 17 + "\u2502 ok" + " " * 21 + "\u2502"
       for i in range(16)]
    + ["\u2514" + "\u2500" * 24 + "\u2534" + "\u2500" * 24 + "\u2518"]) + "\n```"
CHINESE_ANSWER = "\u4f60\u597d\uff0c\u8fd9\u662f\u7ed3\u679c\u3002"


@pytest.mark.parametrize("code", ["en", "fa"])
@pytest.mark.parametrize("answer", ["box-table", "chinese"])
def test_a_box_table_or_cjk_answer_is_sent_left_to_right(code, answer, mk_bot, run_async,
                                                         monkeypatch):
    env = surf.Env(mk_bot, run_async, monkeypatch, code)
    env.store(surf.CHAT)
    summary = BOX_TABLE_ANSWER if answer == "box-table" else CHINESE_ANSWER
    _text, is_rtl, _ = _answer(env, summary, prompt=ENGLISH)
    assert is_rtl is False


@pytest.mark.parametrize("code", RTL_LANGS)
def test_a_letterless_answer_follows_the_prompt(code, mk_bot, run_async, monkeypatch):
    env = surf.Env(mk_bot, run_async, monkeypatch, code)
    env.store(surf.CHAT)
    text, is_rtl, _ = _answer(env, "`42`", prompt=ENGLISH)
    assert is_rtl is False and not text.startswith(RLM)
    text, is_rtl, _ = _answer(env, "`42`", prompt=OWN_SCRIPT[code])
    assert is_rtl is True and text.startswith(RLM)
    text, is_rtl, _ = _answer(env, "`42`")
    assert is_rtl is True and text.startswith(RLM)    # the chat's language


@pytest.mark.parametrize("code", RTL_LANGS)
def test_the_agents_block_keeps_the_answers_direction_when_it_changes(
        code, mk_bot, run_async, monkeypatch):
    env = surf.Env(mk_bot, run_async, monkeypatch, code)
    env.store(surf.CHAT)
    _text, _rtl, sess = _answer(env, ENGLISH, agents=True)
    bot = env.mk_bot()
    rec = sess.agents_lines[0]
    sess.bg_agents["a1"]["tool"] = "Read"
    sess.bg_agents["a1"]["tool_arg"] = "state.py"
    edit = AsyncMock(return_value={"message_id": 77})
    monkeypatch.setattr("aipager.bot.notify.edit_message_text_rich", edit)
    run_async(bot._tick_agents_line(sess, rec, surf.NOW + 60))
    sent = unfooter(edit.await_args.args[2])
    assert edit.await_args.kwargs["is_rtl"] is False
    assert RLM not in sent.rsplit("\n\n" + ENGLISH + "\n\n", 1)[1]


# ---- aipager's own messages keep the chat's direction --------------------------------------

@pytest.mark.parametrize("code", RTL_LANGS)
def test_aipager_only_surfaces_stay_right_to_left(code, mk_bot, run_async, monkeypatch):
    names = {"settings.root", "language.list", "status.list", "transport.texts",
             "prompt.separate", "pinned", "auth.refusals", "notice.context",
             "notice.quiet_turn", "notice.session_end", "route.queue_full"}
    assert names <= {n for n, _f in surf.SURFACES}
    renders = surf.render_all(mk_bot, run_async, monkeypatch, code, names=names)
    assert renders
    for name, texts in renders.items():
        for text in texts:
            assert LRM not in text and RLI not in text, (name, text)
        assert any(RLM in text for text in texts), name


# ---- the spinner verbs ------------------------------------------------------------------------

def test_english_keeps_every_spinner_verb():
    en = i18n.get("en")
    assert animation.spinner_verbs(en) == list(config.SPINNER_VERBS)
    for verb in config.SPINNER_VERBS:
        assert animation._verb(en, verb) == animation._md_escape(verb)
        assert animation._verb(en, verb, "html") == verb


def test_the_spinner_messages_are_english_spinner_verbs():
    assert len(animation.SPINNER_MSGS) == 12
    assert all(str(m) in config.SPINNER_VERBS for m in animation.SPINNER_MSGS)
    assert all(m.key.startswith("card.verb.spin_") for m in animation.SPINNER_MSGS)


def _catalog_values(code: str, prefix: str) -> set[str]:
    path = i18n.core.LOCALES_DIR / f"{code}.jsonl"
    values = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if entry["k"].startswith(prefix):
            values.add(entry["v"])
    return values


@pytest.mark.parametrize("code", TRANSLATED)
def test_each_language_rotates_its_own_verbs(code):
    tr = i18n.get(code)
    own = _catalog_values(code, "card.verb.spin_")
    assert len(own) == 12
    verbs = animation.spinner_verbs(tr)
    assert len(verbs) == 12
    shown = {surf_strip(animation._verb(tr, v)) for v in verbs}
    assert shown == own
    # A verb id from the English list (a language switched mid-turn)
    # still shows a word of this language.
    for verb in config.SPINNER_VERBS:
        assert surf_strip(animation._verb(tr, verb)) in own


@pytest.mark.parametrize("code", TRANSLATED)
def test_the_card_shows_the_chats_verb(code):
    s = _session(commentary=(ENGLISH,))
    card = unfooter(animation.build_stream_card(s, "Calculating", tr=i18n.get(code)))
    want = surf_strip(animation._verb(i18n.get(code), "Calculating"))
    assert want in _catalog_values(code, "card.verb.spin_")
    assert want in surf_strip(_status(card)) and "Calculating" not in card


def test_an_english_card_shows_the_english_verb():
    s = _session(commentary=(ENGLISH,))
    status = _status(unfooter(animation.build_stream_card(s, "Noodling", tr=i18n.get("en"))))
    assert status.startswith("⏳ **aipager_boss** · Noodling · ")


def surf_strip(text: str) -> str:
    for mark in (RLM, LRM, FSI, RLI, PDI):
        text = text.replace(mark, "")
    return text


# ---- where the prompt comes from ---------------------------------------------------------

def _receiver():
    registry = SessionRegistry()
    return registry, hook_receiver.HookReceiver(registry, AsyncMock())


def _datagram(recv, run_async, **fields):
    run_async(recv._on_datagram(json.dumps(fields).encode()))


def test_a_fresh_turns_prompt_is_kept_for_its_direction(run_async):
    registry, recv = _receiver()
    _datagram(recv, run_async, hook_event_name="UserPromptSubmit", session="claude-jim",
              prompt=PERSIAN)
    assert registry.get("claude-jim").turn_prompt == PERSIAN


def test_a_telegram_prompt_keeps_the_persons_words_not_the_marker(run_async):
    registry, recv = _receiver()
    sess = registry.get_or_create("claude-jim")
    sess.last_prompt = "hello there"
    _datagram(recv, run_async, hook_event_name="UserPromptSubmit", session="claude-jim",
              prompt="[via Telegram · @bob]\nhello there")
    assert sess.turn_prompt == "hello there"


def test_a_message_absorbed_by_a_running_turn_does_not_change_it(run_async):
    registry, recv = _receiver()
    _datagram(recv, run_async, hook_event_name="UserPromptSubmit", session="claude-jim",
              prompt=ENGLISH)
    _datagram(recv, run_async, hook_event_name="UserPromptSubmit", session="claude-jim",
              prompt=PERSIAN)
    assert registry.get("claude-jim").turn_prompt == ENGLISH


def test_an_empty_prompt_report_keeps_what_the_send_set(run_async):
    registry, recv = _receiver()
    sess = registry.get_or_create("claude-jim")
    sess.turn_prompt = PERSIAN
    _datagram(recv, run_async, hook_event_name="UserPromptSubmit", session="claude-jim")
    assert sess.turn_prompt == PERSIAN


def test_a_telegram_send_that_starts_a_turn_sets_the_prompt(mk_bot):
    bot = mk_bot()
    sess = bot.registry.get_or_create("claude-jim")
    sess.status = Status.IDLE
    bot._adopt_trigger(sess, 5, PERSIAN)
    assert sess.turn_prompt == PERSIAN
    sess.status = Status.BUSY
    bot._adopt_trigger(sess, 6, ENGLISH)
    assert sess.turn_prompt == PERSIAN


@pytest.mark.parametrize("code", RTL_LANGS)
def test_the_agents_reserve_is_counted_per_direction(code):
    ltr = i18n.get(code).directed(False)
    rtl = i18n.get(code)
    agents_line._RESERVE.clear()
    ltr_alone = agents_line.reserve_bytes(ltr)
    agents_line._RESERVE.clear()
    rtl_reserve = agents_line.reserve_bytes(rtl)
    ltr_reserve = agents_line.reserve_bytes(ltr)
    # Its own size, never the other direction's cached one.
    assert ltr_reserve == ltr_alone != rtl_reserve
    assert agents_line.reserve_bytes(rtl) == rtl_reserve
    view = agents_line.BlockView(
        agents=tuple(agents_line.AgentRow(f"general_purpose{i}", False, tool="Read",
                                          tool_arg="x" * 60) for i in range(6)),
        shells_running=3)
    block = agents_line.render_block(view, ltr)
    assert RLM not in block
    assert len(("\n\n" + block).encode("utf-8")) <= ltr_reserve


# ---- the phrases of a left-to-right line -------------------------------------------------

@pytest.mark.parametrize("code", RTL_LANGS)
def test_a_middle_dot_ends_a_phrase(code):
    ltr = i18n.get(code).directed(False)
    view = agents_line.BlockView(agents=(agents_line.AgentRow("x", False),),
                                 shells_running=1)
    head = agents_line.render_block(view, ltr).split("\n", 1)[0]
    assert " · " in head
    left, right = head.split(" · ", 1)
    assert left.count(RLI) == 1 and right.count(RLI) == 1


@pytest.mark.parametrize("code", RTL_LANGS)
def test_a_sentence_keeps_its_full_stop_and_a_phrase_its_brackets(code):
    ltr = i18n.get(code).directed(False)
    promise = ltr.md(agents_line.PROMISE)
    assert promise.endswith("." + PDI)
    header = finished_line(ltr, "\U0001f4ac **x1**", "md", seconds=38)
    assert header.endswith(")" + PDI)
    assert header.count(RLI) == 1


@pytest.mark.parametrize("code", RTL_LANGS)
def test_a_count_before_a_phrase_reads_with_it(code):
    ltr = i18n.get(code).directed(False)
    fold = ltr.n(animation.FOLD, 5, kind="md")
    assert RLI + ltr.num(5) in fold


# ---- the merged answer and an interim answer -------------------------------------------

def _chat_session(bot, code):
    from aipager import preferences
    preferences.set_ui_language(4242, code)
    s = _session(chat=4242)
    s.active_subagents = {}
    s.tool_history = [("Read: /a", True)]
    s.stream_commentary = []
    bot.registry._sessions[s.name] = s
    return s


@pytest.mark.parametrize("code", RTL_LANGS)
@pytest.mark.parametrize("script", ["english", "own"])
def test_the_merged_answer_takes_the_answers_direction(code, script, mk_bot, run_async,
                                                       monkeypatch):
    from aipager.bot import notify
    bot = mk_bot()
    s = _chat_session(bot, code)
    s.busy_msg_id = 55
    edit = AsyncMock(return_value={"message_id": 55})
    monkeypatch.setattr(notify, "edit_message_text_rich", edit)
    answer = ENGLISH if script == "english" else OWN_SCRIPT[code]
    assert run_async(bot._send_merged_final(s, answer)) is True
    text, is_rtl = unfooter(edit.await_args.args[2]), edit.await_args.kwargs["is_rtl"]
    # The card's status line: before the divider above the answer's
    # result line (the card has a divider of its own above its status).
    head = text.split(f"\n\n{notify._MERGED_SEPARATOR}\n\n💬", 1)[0]
    status = head.rsplit("\n", 1)[-1]
    assert is_rtl is (script == "own")
    assert status.startswith(RLM) is (script == "own")


@pytest.mark.parametrize("code", RTL_LANGS)
def test_an_interim_answer_and_its_block_take_the_answers_direction(code, mk_bot, run_async,
                                                                    monkeypatch):
    from aipager.bot import notify
    bot = mk_bot()
    s = _chat_session(bot, code)
    s.bg_agent_started("a1", "pipeline-runner", time.monotonic())
    rich = AsyncMock(return_value={"message_id": 91})
    monkeypatch.setattr(notify, "send_rich_message", rich)
    run_async(bot._deliver_job_interim(s, ENGLISH, time.time()))
    text, is_rtl = rich.await_args.args[1], rich.await_args.kwargs["is_rtl"]
    assert is_rtl is False and RLM not in text and LRM + RLI in text
    assert s.agents_lines[0]["is_rtl"] is False


# ---- the card's other frames ----------------------------------------------------------------

@pytest.mark.parametrize("code", RTL_LANGS)
def test_a_moved_paused_card_takes_the_cards_direction(code, mk_bot, run_async, monkeypatch):
    # Changed 2026-10-17: the chat's direction under an English prompt
    # (was: is_rtl False, the line left to right).
    from aipager.bot import animation as anim
    bot = mk_bot()
    s = _chat_session(bot, code)
    s.turn_prompt = ENGLISH
    s.stream_commentary = [(0, ENGLISH)]
    s.busy_msg_id = 55
    s.stream_last_rendered = anim._PAUSED_CARD_TEXT
    rich = AsyncMock(return_value={"message_id": 56})
    monkeypatch.setattr(anim, "send_rich_message", rich)
    run_async(bot._send_card_copy(s, 9, verb="Working", final=False, waiting=False))
    text = rich.await_args.args[1]
    assert rich.await_args.kwargs["is_rtl"] is True
    assert text.startswith(RLM) and RLI not in text and LRM not in text


@pytest.mark.parametrize("code", RTL_LANGS)
def test_the_paused_line_takes_the_cards_direction(code, mk_bot, run_async, monkeypatch):
    from aipager.bot import animation as anim
    bot = mk_bot()
    s = _chat_session(bot, code)
    s.turn_prompt = ENGLISH
    s.busy_msg_id = 55
    limiter = MagicMock()
    limiter.minimal_mode = MagicMock(return_value=True)
    monkeypatch.setattr(anim, "get_rate_limiter", lambda: limiter)
    bot._edit_busy_raw = AsyncMock(return_value=True)
    assert run_async(bot._render_paused_card(s)) is True
    text = bot._edit_busy_raw.await_args.args[1]
    # Changed 2026-10-17: the chat's direction (was: left to right).
    assert text.startswith(RLM) and RLI not in text


@pytest.mark.parametrize("code", ["en", "fa", "ru"])
def test_the_animation_rotates_the_chats_list(code, mk_bot, run_async):
    bot = mk_bot()
    s = _chat_session(bot, code)
    s.busy_msg_id = 55
    seen = []

    async def _tick(sess, verb, waiting):
        seen.append(verb)
        if len(seen) >= 30:
            sess.busy_msg_id = 0
        return True

    bot._animate_tick = _tick
    bot._first_tick_delay = lambda sess: 0
    bot._card_wake = lambda sess: 0
    run_async(bot._animate_busy(s))
    want = (set(config.SPINNER_VERBS) if code == "en"
            else {str(m) for m in animation.SPINNER_MSGS})
    assert set(seen) == want


@pytest.mark.parametrize("code", RTL_LANGS)
@pytest.mark.parametrize("agents", [0, 1], ids=["finishing", "working"])
def test_the_waiting_card_keeps_the_chats_direction_under_english(code, agents):
    # Changed 2026-10-17 (was: reads left to right, no U+200F).
    s = _session(commentary=(ENGLISH,))
    s.status = Status.IDLE
    if not agents:
        s.active_subagents = {}
    card = unfooter(animation.build_stream_card(s, "Working", waiting=True, tr=i18n.get(code)))
    status = _status(card)
    assert status.startswith(RLM) and status.endswith(RLM) and RLI not in status


def test_a_middle_dot_between_two_phrases_splits_them():
    from aipager.i18n import core
    a, b = "الف", "باء"
    assert core._isolate_rtl_phrases(f"{a} · {b}", "md") == \
        f"{RLI}{a}{PDI} · {RLI}{b}{PDI}"


@pytest.mark.parametrize("code", RTL_LANGS)
def test_the_merged_answer_direction_is_never_judged_by_aipagers_words(
        code, mk_bot, run_async, monkeypatch):
    # A letterless answer to an English prompt: the timeline's own words
    # (the language's) must not turn the message right to left.
    from aipager.bot import notify
    bot = mk_bot()
    s = _chat_session(bot, code)
    s.label = "x"
    s.tool_history = []
    s.turn_prompt = ENGLISH
    s.busy_msg_id = 55
    edit = AsyncMock(return_value={"message_id": 55})
    monkeypatch.setattr(notify, "edit_message_text_rich", edit)
    assert run_async(bot._send_merged_final(s, "`42`")) is True
    assert edit.await_args.kwargs["is_rtl"] is False


# ---- review iteration 1 -----------------------------------------------------------------

@pytest.mark.parametrize("code", RTL_LANGS)
def test_a_held_short_english_answer_keeps_its_direction(code, mk_bot, run_async, monkeypatch):
    # The stored text holds aipager's header in the chat's script, which
    # outnumbers a short English answer: the direction must travel with
    # the held answer, never be guessed again from that text.
    from aipager.bot import held as held_mod
    from aipager.bot.rich_message import RichMessageFloodBanned, detect_rtl
    env = surf.Env(mk_bot, run_async, monkeypatch, code)
    env.store(surf.CHAT)
    monkeypatch.setattr("aipager.bot.notify.send_rich_message",
                        AsyncMock(side_effect=RichMessageFloodBanned(600, surf.CHAT)))
    bot = env.mk_bot()
    sess = surf._registered(bot, surf._sess(status=Status.IDLE))
    sess.busy_started_at = surf.NOW - 38
    sess.busy_started_wall = 0.0
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    env.run(bot.notify(sess, "idle_prompt", {"summary": "Done."}))
    (entry,) = held_mod.HELD.pending(surf.CHAT)
    assert entry.is_rtl is False and detect_rtl(entry.rich_text) is True
    sent = AsyncMock(return_value={"message_id": 5})
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", sent)
    bot._confirm_held = MagicMock()
    run_async(bot.flush_held_answers(sess))
    assert sent.await_args.kwargs["is_rtl"] is False
    marker = sent.await_args.args[1].split("\n", 1)[0]
    assert marker == held_message.late_marker_text(i18n.get(code).directed(False),
                                                   entry.held_seconds())


@pytest.mark.parametrize("code", RTL_LANGS)
def test_the_full_attached_line_opens_left_to_right_past_its_tag(code):
    from aipager.bot import notify
    shown = i18n.get(code).directed(False).html(notify.FULL_ATTACHED)
    assert shown.startswith(LRM) and RLM not in shown


def test_a_direction_mark_ends_a_phrase():
    from aipager.i18n import core
    a, b = "الف", "باء"
    assert core._isolate_rtl_phrases(f"{a} {LRM}{b}", "md") == \
        f"{RLI}{a}{PDI} {LRM}{RLI}{b}{PDI}"


def test_nothing_invisible_goes_inside_a_code_element():
    from aipager.i18n import core
    a = "الف"
    shown = core._isolate_rtl_phrases(f"{a} <code>{a}</code> {a}", "html")
    assert f"<code>{a}</code>" in shown
    assert shown == f"{RLI}{a}{PDI} <code>{a}</code> {RLI}{a}{PDI}"


@pytest.mark.parametrize("marker", ["- ", "> ", "# ", "1. "], ids=["list", "quote", "head", "num"])
def test_a_block_marker_stays_first_on_a_left_to_right_line(marker):
    from aipager.i18n import core
    a = "الف"
    assert core._ltr_render(f"{core._LINE_SLOT}{marker}{a}", "md") == \
        f"{marker}{LRM}{RLI}{a}{PDI}"


def test_a_line_that_opens_with_an_isolated_phrase_gets_an_lrm():
    # For an app that does not skip isolates: what the isolate holds opens
    # the line right to left.
    from aipager.i18n import core
    a = "الف"
    assert core._ltr_render(f"{core._LINE_SLOT}{FSI}{a}{PDI} Latin", "md") == \
        f"{LRM}{FSI}{a}{PDI} Latin"
    assert core._ltr_render(f"{core._LINE_SLOT}{FSI}x{PDI} Latin", "md") == \
        f"{FSI}x{PDI} Latin"


@pytest.mark.parametrize("code", RTL_LANGS)
def test_a_letterless_answer_follows_the_turns_commentary_before_the_prompt(
        code, mk_bot, run_async, monkeypatch):
    env = surf.Env(mk_bot, run_async, monkeypatch, code)
    env.store(surf.CHAT)
    rich = surf._rich(env)
    bot = env.mk_bot()
    sess = surf._registered(bot, surf._sess(status=Status.IDLE))
    sess.busy_started_at = surf.NOW - 38
    sess.busy_started_wall = 0.0
    sess.turn_prompt = OWN_SCRIPT[code]
    sess.stream_commentary = [(0, ENGLISH)]
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._maybe_update_bot_name = AsyncMock()
    env.run(bot.notify(sess, "idle_prompt", {"summary": "`42`"}))
    assert rich.await_args.kwargs["is_rtl"] is False
    assert not rich.await_args.args[1].startswith(RLM)


@pytest.mark.parametrize("code", RTL_LANGS)
@pytest.mark.parametrize("path", ["merged-edit", "merged-new", "interim"])
def test_every_held_answer_carries_its_direction(code, path, mk_bot, run_async, monkeypatch):
    from aipager.bot import held as held_mod
    from aipager.bot import notify
    from aipager.bot.rich_message import RichMessageFloodBanned
    bot = mk_bot()
    s = _chat_session(bot, code)
    s.busy_msg_id = 55
    banned = AsyncMock(side_effect=RichMessageFloodBanned(600, 4242))
    monkeypatch.setattr(notify, "edit_message_text_rich", banned)
    monkeypatch.setattr(notify, "send_rich_message", banned)
    if path == "interim":
        run_async(bot._deliver_job_interim(s, "Done.", time.time()))
    else:
        run_async(bot._send_merged_final(s, "Done.", send_as_new=path == "merged-new",
                                         reply_to=3))
    (entry,) = held_mod.HELD.pending(4242)
    assert entry.is_rtl is False
