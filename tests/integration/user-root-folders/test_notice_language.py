"""The root folder deny reasons render in the chat's language (design.md
success criterion 13).

The reasons come from real hook decisions (``enforce.decide``), not
hand-written strings; each is rendered with ``safety_notice.reason_text``
/ ``notice_html`` for every shipped language."""

from __future__ import annotations

import html
import sys

import pytest

from aipager import i18n
from aipager.bot import safety_notice

kit = sys.modules["_userroot_kit"]
BOB, GROUP = kit.BOB, kit.GROUP

LANGS = ["ar", "de", "es", "fa", "fr", "id", "it", "ko", "pt-BR", "ru", "tr", "uk", "vi",
         "zh-Hans"]


@pytest.fixture
def reasons(hook, use, team_root, home):
    """``{kind: (tool, reason)}`` produced by the hook for a user's turn."""
    proj = team_root / "proj"
    proj.mkdir(parents=True)
    old = home / "aipager"
    old.mkdir()
    use(BOB, GROUP)
    out = {
        "read": ("Read", hook("Read", {"file_path": str(home / "notes.txt")}, proj)),
        "root-cwd-write": ("Write", hook("Write", {"file_path": str(old / "a"), "content": "x"},
                                         old)),
        "root-cwd-search": ("Grep", hook("Grep", {"pattern": "x"}, old)),
    }
    for kind, (_tool, block) in out.items():
        assert block is not None, kind
    return {k: (tool, block["reason"]) for k, (tool, block) in out.items()}


KINDS = ["read", "root-cwd-write", "root-cwd-search"]


@pytest.mark.parametrize("kind", KINDS)
def test_reason_parses_as_aipagers_own(reasons, kind):
    _tool, reason = reasons[kind]
    assert safety_notice.parse_reason(reason) is not None


@pytest.mark.parametrize("kind", KINDS)
def test_english_reason_is_shown_as_written(reasons, kind):
    _tool, reason = reasons[kind]
    assert safety_notice.reason_text(i18n.get("en"), reason) == html.escape(reason)


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("kind", KINDS)
def test_reason_is_translated(reasons, kind, lang):
    _tool, reason = reasons[kind]
    assert safety_notice.reason_text(i18n.get(lang), reason, kind="text") != reason


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("kind", KINDS)
def test_translated_reason_keeps_the_tool_name(reasons, kind, lang):
    tool, reason = reasons[kind]
    assert tool in safety_notice.reason_text(i18n.get(lang), reason, kind="text")


@pytest.mark.parametrize("kind", KINDS)
def test_persian_notice_is_persian(reasons, kind):
    import re
    _tool, reason = reasons[kind]
    assert re.search("[؀-ۿ]", safety_notice.notice_html(i18n.get("fa"), "x1", reason))


@pytest.mark.parametrize("lang", ["en"] + LANGS)
@pytest.mark.parametrize("kind", KINDS)
def test_no_em_dash_in_the_rendered_reason(reasons, kind, lang):
    _tool, reason = reasons[kind]
    assert "—" not in safety_notice.reason_text(i18n.get(lang), reason, kind="text")


def test_other_root_reason_is_a_protected_path_notice(hook, use, team_root, tmp_path, scopes):
    from aipager.scope import Member, Scope
    far = tmp_path / "srv" / "zed"
    sl = scopes + [Scope(chat_id=50, kind="dm", label="zed DM",
                         members=(Member(id=50, label="zed", role="user"),), root=str(far))]
    proj = team_root / "proj"
    proj.mkdir(parents=True)
    use(BOB, GROUP, sl)
    reason = hook("Read", {"file_path": str(far / "a")}, proj)["reason"]
    assert safety_notice.reason_text(i18n.get("ru"), reason, kind="text") != reason
