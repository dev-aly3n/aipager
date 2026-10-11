"""Check 11, the ratchet: the count of hard-coded English strings in each
module may only go down. A new user-facing sentence goes through
``aipager.i18n`` with its English inline; a lower count is written back
with ``python tools/i18n.py check --write-baseline`` so it cannot creep up
again."""

from __future__ import annotations

from tests import i18n_tool

tool = i18n_tool.load()


def test_the_tree_matches_the_baseline():
    assert tool.check_ratchet(tool.literal_counts(), tool.load_baseline()) == []


def test_a_new_literal_fails(tmp_path):
    pkg = tmp_path / "aipager"
    pkg.mkdir()
    (pkg / "m.py").write_text('def f():\n    return "Hello there"\n', encoding="utf-8")
    baseline = tool.literal_counts(pkg)
    assert baseline == {"aipager/m.py": 1}
    (pkg / "m.py").write_text('def f():\n    return "Hello there", "Good bye"\n',
                              encoding="utf-8")
    problems = tool.check_ratchet(tool.literal_counts(pkg), baseline)
    assert any("new hard-coded English" in p for p in problems)
    (pkg / "n.py").write_text('X = "A new file"\n', encoding="utf-8")
    assert any("aipager/n.py" in p for p in tool.check_ratchet(tool.literal_counts(pkg),
                                                             baseline))


def test_a_lower_count_asks_for_a_new_baseline():
    problems = tool.check_ratchet({"aipager/m.py": 1}, {"aipager/m.py": 3})
    assert any("--write-baseline" in p for p in problems)


def test_what_does_not_count():
    src = '''
"""A module docstring with words."""
import logging
log = logging.getLogger(__name__)
def f(tr):
    """A docstring."""
    "a bare string"
    log.debug("a log line here %s", 1)
    x = Msg("a.b", "A message here")
    y = tr.html("a.c", "Inline English here")
    z = ("ab", "single", "a b", "x y")
    return "Counted sentence", f"Also counted {x}"
'''
    assert tool.count_literals(src) == 2
