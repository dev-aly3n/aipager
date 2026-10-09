"""Check 11, the pseudo-locale half: rendered under ``qps``, every string
of a migrated renderer is marked, so English that slipped past the
catalog shows up as an unmarked ASCII word."""

from __future__ import annotations

import re
import subprocess
import sys

from aipager.i18n import core
from tests import i18n_tool

tool = i18n_tool.load()

_MARKED = re.compile(r"⟦.*?⟧", re.S)
_TOKENS = re.compile(r"/[A-Za-z0-9_]+|@\w+|<[^>]+>|&\w+;|\d+")


def unmarked_words(text: str, known=()) -> list[str]:
    """ASCII words in *text* outside the pseudo markers, after removing
    tokens that are never translated (commands, tags, numbers, names)."""
    rest = _MARKED.sub(" ", text)
    for word in sorted(set(known) | set(core.NEVER_TRANSLATE), key=len, reverse=True):
        rest = rest.replace(word, " ")
    rest = _TOKENS.sub(" ", rest)
    return re.findall(r"[A-Za-z]{2,}", rest)


def test_the_sweep_sees_unmarked_english():
    assert unmarked_words(core.pseudo("Hello /new in Telegram")) == []
    assert unmarked_words("Hello " + core.pseudo("world")) == ["Hello"]


def test_the_pseudo_command(tmp_path):
    tool_path = str(tool.ROOT / "tools" / "i18n.py")
    env = i18n_tool.isolated_env(tmp_path)
    out = subprocess.run([sys.executable, tool_path, "pseudo", "Send /new"],
                         capture_output=True, text=True, check=True, cwd=tool.ROOT, env=env)
    assert out.stdout.strip() == core.pseudo("Send /new")
    out = subprocess.run([sys.executable, tool_path, "pseudo", "--rtl", "Hi"],
                         capture_output=True, text=True, check=True, cwd=tool.ROOT, env=env)
    assert out.stdout.strip() == "\u200f" + core.pseudo("Hi")


ENDONYMS = ("English", "Pseudo", "Pseudo RTL")


def _texts(kb):
    return [b.text for row in kb.inline_keyboard for b in row]


def migrated_renders(monkeypatch) -> list[str]:
    """Every renderer migrated to keys so far, under ``qps``."""
    from aipager import i18n
    from aipager.bot import kbwords, language_menu, mode_labels
    from aipager.bot.lifecycle import LifecycleMixin
    from aipager.i18n import locales
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps"}))
    tr = i18n.get("qps")
    out = []
    for private in (True, False):
        text, kb = language_menu.render_language_menu(tr, private=private, current="qps")
        out += [text, *_texts(kb)]
    out.append(language_menu.confirmation(tr, "qps"))
    out.append(language_menu.row_text(tr, "qps"))
    out += [tr.text(m) for m in (*kbwords.NAV.values(), *kbwords.WORDS.values())]
    out += [tr.text(m) for m in (mode_labels.MODE_AUTO, mode_labels.MODE_ASK,
                                 mode_labels.MODE_ASK_INSTEAD, mode_labels.MODE_AUTO_INSTEAD,
                                 mode_labels.MODE_ASK_FOR_YOU)]
    menu = {c.command: c.description for c in LifecycleMixin._command_list(set(), tr=tr)}
    out.append(menu["language"])
    from aipager.miniapp import server
    out += [tr.text(server._APP_LANGUAGE_TITLE), tr.text(server._APP_LANGUAGE_HINT)]
    from aipager.bot import language_offer
    text, kb = language_offer.render_offer(tr, "qps")
    out += [text, *_texts(kb)]
    return out


def test_every_migrated_renderer_is_marked_under_qps(monkeypatch):
    renders = migrated_renders(monkeypatch)
    assert len(renders) >= 20
    leaks = {t: unmarked_words(t, known=ENDONYMS) for t in renders}
    assert not {t: w for t, w in leaks.items() if w}, leaks
