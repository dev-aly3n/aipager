"""Black-box, iteration 2: the busy card's fold summaries and the pinned
bar's three flood lines in Persian and Russian (entrypoints.md "Busy card
... fold summaries", "Pinned bar: ... the three flood lines").

Fold: a run of tool calls between two pieces of Claude commentary folds
into ``<details><summary>▸ N tool calls</summary>`` (observed: runs of 3
or more). Boundary values for the Russian forms: 3, 4, 22, 24 (few),
5, 11, 12, 25 (many), 21 (one). The expected forms are read from the
shipped ``ru.jsonl`` (this checks the form is PICKED, not its wording).

Flood lines: slow mode after a Telegram warning (a retry-after), paused at
the rate floor (a ban), paused at the hourly limit. The limiter is the
real one; its state is the external boundary (Telegram's flood control)
and is set through its own methods.
"""

from __future__ import annotations

import re
import time

import pytest

from aipager import i18n, preferences
from aipager.bot import animation
from aipager.state import SessionRegistry, Status
from tests.i18n_ml2_bb import (
    BIDI_CONTROLS,
    DM_A,
    LATIN_DIGITS,
    RLM,
    add_session,
    catalog,
    code_segments,
    make_bot,
    strip_marks,
)

FORM_OF = {3: "few", 4: "few", 22: "few", 24: "few", 21: "one",
           5: "many", 11: "many", 12: "many", 25: "many"}
SUMMARY = re.compile(r"<summary>(.*?)</summary>")


def _card(n, code, commentary=("First.", "Second.")):
    reg = SessionRegistry()
    s = reg.get_or_create("claude-x12__d4242")
    s.label = "x12"
    s.scope_chat_id, s.scope_kind = 4242, "dm"
    s.status = Status.BUSY
    s.busy_started_at = time.monotonic() - 30
    s.tool_history = [(f"Read: /tmp/a{i}.py", True) for i in range(n + 2)]
    s.stream_commentary = [(0, commentary[0]), (n, commentary[1])]
    return animation.build_stream_card(s, "Working", tr=i18n.get(code))


def _summaries(card):
    return SUMMARY.findall(card)


@pytest.mark.parametrize("n", sorted(FORM_OF))
def test_a_russian_fold_picks_the_cldr_form(n):
    want = catalog("ru")["card.fold"]["v"][FORM_OF[n]].replace("{n}", str(n))
    assert _summaries(_card(n, "ru")) == [want]


@pytest.mark.parametrize("n", [3, 5, 12])
def test_an_english_fold_is_todays_text(n):
    assert _summaries(_card(n, "en")) == [f"▸ {n} tool calls"]


@pytest.mark.parametrize("n", [3, 12, 25])
def test_a_persian_fold_counts_in_persian_digits(n):
    fa = str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴"
                                                      "۵۶۷۸۹"))
    (summary,) = _summaries(_card(n, "fa"))
    assert fa in summary


@pytest.mark.parametrize("n", [3, 12])
def test_a_persian_fold_has_no_latin_digit(n):
    (summary,) = _summaries(_card(n, "fa"))
    assert not LATIN_DIGITS.search(summary)


#: Claude's commentary in Persian: the card reads right to left.
PERSIAN = ("\u0627\u0648\u0644.", "\u062f\u0648\u0645.")


def test_a_persian_fold_summary_starts_right_to_left_inside_the_element():
    (summary,) = _summaries(_card(5, "fa", PERSIAN))
    assert summary.startswith(RLM)


def test_a_persian_fold_under_english_commentary_starts_left_to_right():
    # Claude writes English: the whole card reads left to right, the
    # Persian words kept whole (multilingual design 5.7, rule 5).
    (summary,) = _summaries(_card(5, "fa"))
    assert summary.startswith("\u200e") and RLM not in summary


def test_a_persian_fold_reads_the_catalog():
    (summary,) = _summaries(_card(5, "fa", PERSIAN))
    want = catalog("fa")["card.fold"]["v"]["other"].replace("{n}", "۵")
    assert strip_marks(summary) == want


def test_a_persian_fold_puts_no_mark_inside_a_tag():
    card = _card(5, "fa")
    assert not any(BIDI_CONTROLS.search(tag) for tag in re.findall(r"<[^>]*>", card))


def test_a_persian_fold_leaves_the_folded_tool_rows_byte_identical():
    assert code_segments(_card(5, "fa")) == code_segments(_card(5, "en"))


def test_a_russian_fold_has_no_marks():
    assert not BIDI_CONTROLS.search(_card(5, "ru"))


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_fold_never_holds_an_em_dash(code):
    assert "\u2014" not in _card(12, code)


# ---- the pinned bar's flood lines ------------------------------------------------------


@pytest.fixture
def flood(mk_bot, monkeypatch, tmp_path):
    from aipager.bot import dashboard
    from aipager.bot.flood_budget import BudgetRateLimiter
    made = []

    def _make(code, regime):
        lim = BudgetRateLimiter(signal_path=str(tmp_path / f"sig{len(made)}"))
        made.append(lim)
        monkeypatch.setattr(dashboard, "get_rate_limiter", lambda: lim)
        bot = make_bot(mk_bot)
        preferences.set_ui_language(DM_A, code)
        s = add_session(bot, chat=DM_A, label="x1", status=Status.BUSY)
        if regime == "slow":
            lim.note_retry_after(DM_A, 5.0)
        elif regime == "rate":
            lim.note_ban(DM_A, 1.0)
        elif regime == "hourly":
            share = lim.hourly_usage(DM_A)["ornament_budget"]
            lim.restore([{"chat_id": DM_A, "hourly": [[time.time() - 60.0, share + 1, 0]]}])
        return bot._render_pinned(DM_A, [s])[0].split("\n")

    yield _make
    for lim in made:
        lim.reset()


REGIMES = [("slow", "flood.slow"), ("rate", "flood.paused_rate"),
           ("hourly", "flood.paused_hourly")]


@pytest.mark.parametrize("regime, key", REGIMES)
def test_a_russian_flood_line_reads_the_catalog(flood, regime, key):
    assert catalog("ru")[key]["v"] in flood("ru", regime)


@pytest.mark.parametrize("regime, key", REGIMES)
def test_a_persian_flood_line_reads_the_catalog(flood, regime, key):
    lines = [strip_marks(x) for x in flood("fa", regime)]
    assert catalog("fa")[key]["v"] in lines


@pytest.mark.parametrize("regime, key", REGIMES)
def test_a_persian_flood_line_starts_right_to_left(flood, regime, key):
    want = catalog("fa")[key]["v"]
    (line,) = [x for x in flood("fa", regime) if strip_marks(x) == want]
    assert line.startswith(RLM)


@pytest.mark.parametrize("regime, want", [
    ("slow", "\U0001f422 slow mode after a Telegram warning"),
    ("rate", "⏸ card updates paused (rate limit)"),
    ("hourly", "⏸ card updates paused (hourly limit)")])
def test_an_english_flood_line_is_todays_text(flood, regime, want):
    assert want in flood("", regime)


@pytest.mark.parametrize("regime", ["slow", "rate", "hourly"])
def test_russian_flood_lines_have_no_marks(flood, regime):
    assert not BIDI_CONTROLS.search("\n".join(flood("ru", regime)))


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_no_flood_line_in_a_calm_chat(flood, code):
    lines = [strip_marks(x) for x in flood(code, "calm")]
    keys = ("flood.slow", "flood.paused_rate", "flood.paused_hourly")
    assert not any(catalog(code)[k]["v"] in lines for k in keys)


def test_the_persian_flood_line_keeps_telegram_latin(flood):
    want = catalog("fa")["flood.slow"]["v"]
    assert "Telegram" in want and want in [strip_marks(x) for x in flood("fa", "slow")]
