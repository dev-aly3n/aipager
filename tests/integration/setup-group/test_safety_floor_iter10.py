"""Iteration 10 of the safety floor checks (design.md success criteria 15
and 16; docs/security.md "aipager's own commands and folders" and
"Searches through Bash"): the size and time bounds closed this round, at
the full 100 KB a hook command may carry.

- The escaped-quote family iteration 9 found quadratic (an escaped quote,
  an empty quote pair or ``$'``/``$"``, an escaped separator, a search
  name, repeated in one word), and the same chains with real separators
  (quadratic in main 5c286755's own search patterns: 5.8-7.8 s at 50 KB).
  Each is read in bounded time before a harmless tail and still refused
  before a home search.
- More than 8 KB of matched text put in place of ``&`` in one command is
  refused (docs/security.md), quickly and in bounded memory.

Only the documented surface is used: ``safety.bash_violation`` with
``safety.DENY_BASH_PATTERNS`` (entrypoints.md "Safety check surface").
Timing cases take the best of three runs, each a different command (a
distinct leading ``: rN;``), so no cache keyed by the text helps. The
budget is 0.5 s at 100 KB, as in iteration 9's file (the bar is 0.35 s on
an idle machine; the tester's idle sweep measured these under 0.25 s).
"""

from __future__ import annotations

import itertools
import time
import tracemalloc

import pytest

from aipager.safety import DENY_BASH_PATTERNS, bash_violation

B = chr(92)  # a backslash
BT = "`"
Q = '"'
SIZE = 100_000
BUDGET_S = 0.5
HARMLESS_TAIL = " ; ls ~/.config/nvim"
SEARCH_TAIL = "\ngrep -rn aipager ~"


def _blocked(cmd: str) -> bool:
    return bash_violation(cmd, DENY_BASH_PATTERNS) is not None


_run = itertools.count()


def _best_of_three(cmd: str) -> float:
    best = float("inf")
    for _ in range(3):
        c = ": r%d;%s" % (next(_run), cmd)
        t0 = time.perf_counter()
        bash_violation(c, DENY_BASH_PATTERNS)
        best = min(best, time.perf_counter() - t0)
    return best


def _fill(unit: str, tail: str) -> str:
    return unit * max(1, (SIZE - len(tail)) // len(unit)) + tail


# The four heads iteration 9 found slow, before each escaped separator.
_HEADS = [("esc-dq-pair", B + Q + Q), ("esc-dq-dollar", B + Q + "$" + Q),
          ("esc-sq-pair", B + "''"), ("esc-sq-dollar", B + "'$'")]
_SEPS = [("semi", B + ";"), ("amp", B + "&"), ("pipe", B + "|"),
         ("paren", B + "("), ("backtick", B + BT)]
_TOOLS = [("rg", "rg x "), ("grep-r", "grep -r ")]
ESCAPED_QUOTE_FAMILY = [
    ("%s-%s-%s" % (h, s, t), hu + su + tu)
    for (h, hu), (s, su), (t, tu) in itertools.product(_HEADS, _SEPS, _TOOLS)
]

# The same chains with real separators (fixes-10: quadratic on main).
REAL_SEPARATOR_CHAINS = [
    ("dq-esc-pair-semi-rg", B + Q + Q + ";rg x "),
    ("sq-esc-sq-semi-rg", "'" + B + "''" + ";rg x "),
    ("dq-esc-pair-backtick-rg", B + Q + Q + BT + "rg x "),
    ("sq-esc-pair-pipe-grep", B + "''" + "|grep -r "),
    ("dq-esc-dollar-amp-du", B + Q + "$" + Q + "&du -a "),
]

ALL_SHAPES = ESCAPED_QUOTE_FAMILY + REAL_SEPARATOR_CHAINS
_shapes = pytest.mark.parametrize("unit", [r[1] for r in ALL_SHAPES],
                                  ids=[r[0] for r in ALL_SHAPES])


@_shapes
def test_long_quote_chain_before_a_harmless_tail_is_under_budget(unit):
    assert _best_of_three(_fill(unit, HARMLESS_TAIL)) < BUDGET_S


@_shapes
def test_long_quote_chain_before_a_home_search_is_under_budget(unit):
    assert _best_of_three(_fill(unit, SEARCH_TAIL)) < BUDGET_S


@_shapes
def test_long_quote_chain_before_a_home_search_is_denied(unit):
    assert _blocked(_fill(unit, SEARCH_TAIL)) is True


# More than 8 KB of matched text in place of & (docs/security.md).
_P = "a" * 50_000
AMP_FLOODS = [
    ("one-long-pattern", "cat ~/.config/${x/" + _P + "/" + "&" * 40 + "}/x"),
    ("many-amps", "cat ~/.config/${x/" + "a" * 600 + "/" + "&" * 40_000 + "}/x"),
    # each & spelling a 49 KB match: 2.4 GB if every & were spelled out
    ("pattern-times-amps",
     "cat ~/.config/${x/" + "a" * 49_000 + "/" + "&" * 49_000 + "}/x"),
    ("in-double-quotes", 'cat ~/.config/"${x/' + _P + "/&&&}" + '"/x'),
    ("from-a-default", "cat ~/.config/${x/" + _P + "/${z:-&&}}/x"),
    ("many-short-replacements",
     "cat " + " ".join("~/.config/${x/" + "a" * 60 + "/&}"
                       for _ in range(1_500))),
]
_floods = pytest.mark.parametrize("cmd", [r[1] for r in AMP_FLOODS],
                                  ids=[r[0] for r in AMP_FLOODS])


@_floods
def test_ampersand_flood_is_refused(cmd):
    assert _blocked(cmd) is True


@_floods
def test_ampersand_flood_is_decided_under_budget(cmd):
    assert _best_of_three(cmd) < BUDGET_S


@_floods
def test_ampersand_flood_stays_in_bounded_memory(cmd):
    tracemalloc.start()
    try:
        bash_violation(": r%d;%s" % (next(_run), cmd), DENY_BASH_PATTERNS)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 64 * 1024 * 1024
