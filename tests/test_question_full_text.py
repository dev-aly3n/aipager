"""Claude's question and its options arrive whole (operator, 2026-10-15).

The card's inline question and the question sent as its own message show
Claude's whole question and every option's whole description. Only past
Telegram's limit on one message's text is anything shortened: the card's
tool rows first, then the longest descriptions, then the question, each at
a word boundary with "…"."""

from __future__ import annotations

import pytest

from aipager import preferences
from aipager.bot import question_form
from aipager.bot.transport import TELEGRAM_MAX_TEXT_LEN
from aipager.state import Status, TrackedSession

CHAT = 256113222  # tests/conftest.py pins config.CHAT_ID to this

QUESTION = ("Both rows read \"🤖 pipeline-runner\" while the agents keep working; "
            "which of these should the card show for an agent that has no label "
            "of its own, and should an older answer drop agents a newer answer "
            "already carries? " * 2).strip()
DESCRIPTIONS = [
    "When a command has no label, show the task the agent was started with, "
    "so two agents of the same type can be told apart at a glance.",
    "When a newer answer carries the same agents, the older answer drops "
    "them, and only the newest answer keeps the live rows.",
    "An agent whose turn ended while its own command still runs stays on the "
    "card until that command is done, with its elapsed time.",
]


def _words(n: int, word: str = "lorem") -> str:
    return " ".join(f"{word}{i}" for i in range(n))


def _options(descs=DESCRIPTIONS):
    return [{"label": f"Option {i}", "description": d} for i, d in enumerate(descs)]


def _card_sess(question=QUESTION, options=None, history=()):
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    sess.scope_chat_id = CHAT
    sess.tool_history = list(history)
    sess.pending_permission = {
        "ask_question": True, "question": question,
        "options": _options() if options is None else options,
        "questions": [], "current_idx": 0, "multi_select": False,
        "cursor_pos": 0, "selected": set(),
    }
    return sess


def _shown(html_text: str) -> str:
    import html
    import re
    return html.unescape(re.sub(r"<[^>]+>", "", html_text))


# ---- shorten -------------------------------------------------------------------------------

def test_shorten_keeps_text_that_fits():
    assert question_form.shorten("short text", 10) == "short text"


def test_shorten_cuts_at_a_word_boundary_with_an_ellipsis():
    out = question_form.shorten("alpha beta gamma delta", 15)
    assert out == "alpha beta…"
    assert question_form.units(out) <= 15


def test_shorten_cuts_a_single_long_word_where_it_is():
    out = question_form.shorten("x" * 50, 10)
    assert out == "x" * 9 + "…"


def test_shorten_counts_utf16_units():
    out = question_form.shorten("🤖" * 10, 7)          # each emoji is two units
    assert out == "🤖" * 3 + "…" and question_form.units(out) == 7


def test_shorten_backs_off_to_a_space_only_when_it_is_near():
    # The last space would keep 1 of 9 characters: cut the word instead.
    assert question_form.shorten("a " + "x" * 50, 10) == "a " + "x" * 7 + "…"


def test_shorten_to_one_unit_leaves_the_ellipsis():
    assert question_form.shorten("anything", 1) == "…"


@pytest.mark.parametrize("limit", [0, -3])
def test_shorten_with_no_room_is_empty(limit):
    assert question_form.shorten("anything", limit) == ""


def test_shown_len_counts_what_telegram_shows():
    assert question_form.shown_len("<b>a&amp;b</b> 🤖") == 6     # the emoji is two


# ---- the card ------------------------------------------------------------------------------

def test_the_card_shows_the_whole_question_and_every_description(mk_bot):
    text = mk_bot()._build_busy_text("jim", "Waiting", _card_sess())
    shown = _shown(text)
    assert f"❓ {QUESTION}" in shown
    for i, desc in enumerate(DESCRIPTIONS):
        assert f"{i + 1}. Option {i} - {desc}" in shown
    assert "…" not in shown[shown.index("❓"):]


def test_a_multi_select_form_is_whole_too(mk_bot):
    sess = _card_sess()
    sess.pending_permission["multi_select"] = True
    shown = _shown(mk_bot()._build_busy_text("jim", "Waiting", sess))
    assert QUESTION in shown and all(d in shown for d in DESCRIPTIONS)


def test_the_tool_rows_give_way_before_the_form(mk_bot):
    history = [(f"Bash: step {i} " + "x" * 200, True) for i in range(15)]
    sess = _card_sess(history=history)
    text = mk_bot()._build_busy_text("jim", "Waiting", sess)
    shown = _shown(text)
    assert question_form.shown_len(text) <= TELEGRAM_MAX_TEXT_LEN
    assert QUESTION in shown and all(d in shown for d in DESCRIPTIONS)
    assert "Bash: step 14" in shown          # the newest rows stay
    assert "Bash: step 0 " not in shown      # the oldest folded into "earlier"
    assert "earlier" in shown


def test_without_a_long_form_every_tool_row_stays(mk_bot):
    history = [(f"Bash: step {i} " + "x" * 200, True) for i in range(15)]
    sess = _card_sess(question="Which?", options=_options(["a", "b"]), history=history)
    shown = _shown(mk_bot()._build_busy_text("jim", "Waiting", sess))
    assert all(f"Bash: step {i} " in shown for i in range(15))


def test_an_over_budget_card_is_shortened_at_a_word_boundary(mk_bot):
    descs = [_words(1000, "d"), _words(40, "e"), "short one"]
    sess = _card_sess(question="Which?", options=_options(descs))
    text = mk_bot()._build_busy_text("jim", "Waiting", sess)
    shown = _shown(text)
    assert question_form.shown_len(text) <= TELEGRAM_MAX_TEXT_LEN
    rows = shown.split("\n")
    first = next(r for r in rows if "1. Option 0 - " in r)
    assert first.endswith("…") and first[:-1].rstrip() == first[:-1]
    assert first[:-1].split(" ")[-1] in _words(1000, "d").split(" ")   # a whole word
    # The shorter ones stay whole: the longest gives first.
    assert f"2. Option 1 - {descs[1]}" in shown and "3. Option 2 - short one" in shown
    assert "Which?" in shown


def test_the_question_gives_way_last_and_never_passes_the_limit(mk_bot):
    question = _words(1200, "q")
    descs = [_words(400, "d")] * 4
    sess = _card_sess(question=question, options=_options(descs))
    text = mk_bot()._build_busy_text("jim", "Waiting", sess)
    assert question_form.shown_len(text) <= TELEGRAM_MAX_TEXT_LEN
    q_line = next(r for r in _shown(text).split("\n") if r.startswith("❓ "))
    assert q_line.endswith("…")


def test_a_form_that_needs_the_whole_card_folds_every_tool_row(mk_bot):
    bot = mk_bot()
    history = [(f"Bash: step {i}", True) for i in range(15)]

    def card(pad: int) -> str:
        descs = [_words(400, "d"), "x" * pad]
        sess = _card_sess(question="Which?", options=_options(descs), history=history)
        return bot._build_busy_text("jim", "Waiting", sess)
    # The longest form that still fits whole: it leaves no room for a row.
    def whole(p: int) -> bool:
        return "…" not in _shown(card(p)).split("❓")[1]
    lo, hi = 0, 4000                                   # whole(lo), not whole(hi)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if whole(mid) else (lo, mid)
    pad = lo
    text = card(pad)
    shown = _shown(text)
    assert question_form.shown_len(text) <= TELEGRAM_MAX_TEXT_LEN
    assert "x" * pad in shown and _words(400, "d") in shown
    assert "Bash: step" not in shown                  # no tool row at all
    assert "15" in shown.split("\n")[1]               # all counted as earlier


def test_labels_give_way_only_when_they_alone_pass_the_limit():
    from aipager import i18n
    tr = i18n.get("en")
    options = [{"label": _words(400, "L"), "description": "d"} for _ in range(3)]
    text = question_form.render(tr, "Which?", options, lead="❓ ")
    assert question_form.shown_len(text) <= TELEGRAM_MAX_TEXT_LEN
    lines = _shown(text).split("\n")
    assert lines[0] == "❓ Which?"                    # the question stays whole
    rows = lines[1:]
    assert len(rows) == 3 and all(r.endswith("… - d") for r in rows)


def _en():
    from aipager import i18n
    return i18n.get("en")


def test_short_descriptions_stay_when_only_the_question_can_make_room():
    question = _words(1300, "q")
    options = [{"label": "Alpha", "description": "short desc one"},
               {"label": "Beta", "description": "another short"}]
    text = question_form.render(_en(), question, options, lead="❓ ")
    lines = _shown(text).split("\n")
    assert question_form.shown_len(text) <= TELEGRAM_MAX_TEXT_LEN
    assert lines[0].endswith("…")
    assert lines[1:] == ["  1. Alpha - short desc one", "  2. Beta - another short"]


def test_a_long_question_gives_before_the_descriptions_vanish():
    # The descriptions alone could just make the room, by going down to
    # "…": the far longer question gives instead, and they stay whole.
    question = _words(500, "q")
    descs = [_words(50, "d")] * 4
    options = [{"label": f"L{i}", "description": d} for i, d in enumerate(descs)]
    whole = question_form.render(_en(), question, options, lead="")
    budget = question_form.shown_len(whole) - sum(len(d) - 1 for d in descs)
    text = question_form.render(_en(), question, options, lead="", budget=budget)
    lines = _shown(text).split("\n")
    assert question_form.shown_len(text) <= budget
    assert lines[0].endswith("…")
    assert lines[1:] == [f"  {i + 1}. L{i} - {d}" for i, d in enumerate(descs)]


def test_a_description_cut_to_nothing_still_shows_the_ellipsis():
    options = [{"label": "A", "description": "x" * 100}]
    budget = question_form.shown_len("Q\n  1. A - ") + 1
    text = question_form.render(_en(), "Q", options, lead="", budget=budget)
    assert _shown(text) == "Q\n  1. A - …"


def test_with_no_room_at_all_every_text_still_shows_its_ellipsis():
    options = [{"label": "Alpha", "description": "desc"}]
    text = question_form.render(_en(), "Question", options, lead="", budget=3)
    assert _shown(text) == "…\n  1. … - …"


def test_texts_too_short_to_make_the_room_are_not_emptied():
    # Cutting the description to "…" saves less than the overrun: it is
    # not cut to nothing; the question and description give together.
    options = [{"label": "A", "description": "xy"}]
    full = question_form.shown_len("Question\n  1. A - xy")
    text = question_form.render(_en(), "Question", options, lead="", budget=full - 2)
    assert _shown(text) == "Quest…\n  1. A - xy"


def test_texts_are_never_emptied_to_make_room():
    # Emptying both would fit; "…" each is the least they keep.
    options = [{"label": "L", "description": "cd"}]
    full = question_form.shown_len("ab\n  1. L - cd")
    text = question_form.render(_en(), "ab", options, lead="", budget=full - 3)
    assert _shown(text) == "…\n  1. L - …"


@pytest.mark.parametrize("desc", [None, 7, ["x"]])
def test_a_description_that_is_not_text_is_left_out(mk_bot, desc):
    options = [{"label": "A", "description": desc}]
    text = question_form.render(_en(), "Which?", options, lead="❓ ")
    assert _shown(text) == "❓ Which?\n  1. A"


@pytest.mark.parametrize("opt", ["x", 5, None])
def test_an_option_that_is_not_a_dict_gets_our_label(opt):
    text = question_form.render(_en(), "Which?", [opt], lead="❓ ")
    assert _shown(text) == "❓ Which?\n  1. Option 1"


def test_a_question_that_is_not_text_shows_a_question_mark(mk_bot):
    bot = mk_bot()
    sess = _card_sess(question=None, options=_options(["a"]))
    assert "❓ ?\n" in _shown(bot._build_busy_text("jim", "Waiting", sess))
    text, _kb = bot._build_ask_keyboard(
        sess, "jim", {"questions": [{"question": None, "options": _options(["a"])}]})
    assert _shown(text).startswith("❓ jim · ?\n")


# ---- the pinned bar's "Answer" re-send -------------------------------------------------------

def test_the_answer_re_send_keeps_a_near_limit_form_whole(mk_bot):
    bot = mk_bot()
    history = [(f"Read: /home/alice/f{i}.py", True) for i in range(15)]
    question = '"a" & "b" & "c"? ' * 60
    descs = [_words(300, "w"), _words(300, "v")]
    sess = _card_sess(question=question, options=_options(descs), history=history)
    card = bot._build_busy_text("jim", "Waiting", sess)
    assert len(card) > TELEGRAM_MAX_TEXT_LEN          # longer as HTML than as shown
    text, _kb = bot._pending_prompt_markup(sess)
    assert text == card
    assert all(d in _shown(text) for d in descs)


def test_the_answer_re_send_still_cuts_a_card_that_does_not_fit(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    sess.scope_chat_id = CHAT
    sess.pending_permission = {"tool_summary": "Bash: " + "x" * 6000, "tool_info": None}
    text, _kb = bot._pending_prompt_markup(sess)
    # Cut to the limit, plus the "…" that marks the cut.
    assert question_form.shown_len(text) <= TELEGRAM_MAX_TEXT_LEN + 1


# ---- the question sent as its own message --------------------------------------------------

def test_the_separate_message_shows_the_whole_question_and_descriptions(mk_bot):
    bot = mk_bot()
    sess = _card_sess()
    text, keyboard = bot._build_ask_keyboard(
        sess, "jim", {"questions": [{"question": QUESTION, "options": _options()}]})
    shown = _shown(text)
    assert shown.startswith(f"❓ jim · {QUESTION}")
    for i, desc in enumerate(DESCRIPTIONS):
        assert f"{i + 1}. Option {i} - {desc}" in shown
    assert [b.text for b in keyboard.inline_keyboard[0]] == ["Option 0", "Option 1",
                                                            "Option 2"]


def test_an_over_budget_separate_message_stays_within_the_limit(mk_bot):
    bot = mk_bot()
    sess = _card_sess()
    descs = [_words(500, "d"), _words(500, "e")]
    text, _kb = bot._build_ask_keyboard(
        sess, "jim", {"questions": [{"question": "Which?", "options": _options(descs)}]})
    assert question_form.shown_len(text) <= TELEGRAM_MAX_TEXT_LEN
    rows = _shown(text).split("\n")
    assert all(r.endswith("…") for r in rows if " - " in r)
    # Cut evenly: neither keeps much more than the other.
    lens = [len(r) for r in rows if " - " in r]
    assert abs(lens[0] - lens[1]) < 20


def test_a_question_alone_past_the_limit_is_shortened(mk_bot):
    text, keyboard = mk_bot()._build_ask_keyboard(
        _card_sess(), "jim", {"questions": [{"question": _words(1500, "q"), "options": []}]})
    assert keyboard is None
    assert question_form.shown_len(text) <= TELEGRAM_MAX_TEXT_LEN
    assert _shown(text).endswith("…")


# ---- right to left ---------------------------------------------------------------------------

def test_rows_of_ours_keep_their_marks_and_claudes_get_none(mk_bot):
    preferences.set_ui_language(CHAT, "fa")
    options = [{"description": DESCRIPTIONS[0]}, {"label": "B", "description": DESCRIPTIONS[1]}]
    text = mk_bot()._build_busy_text("jim", "Waiting", _card_sess(options=options))
    rows = text.split("\n")
    ours = next(r for r in rows if DESCRIPTIONS[0] in r)
    claudes = next(r for r in rows if DESCRIPTIONS[1] in r)
    assert ours.startswith("  \u200f") and ours.endswith("\u200f")   # both ends
    assert "\u200f" not in claudes
    assert DESCRIPTIONS[0] in ours            # whole, marks or not
