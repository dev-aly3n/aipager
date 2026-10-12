"""The translated docs under docs/i18n/<code>/: one page per English page,
each naming the English page it comes from, with every English heading's
anchor and no link to a page or anchor that does not exist."""

from __future__ import annotations

import subprocess

import pytest

from tests import i18n_tool

tool = i18n_tool.load()

ENGLISH = """# Bot commands

<a id="ignored"></a>
## Slash commands

Text with a [link](groups.md#roles).

### `aipager doctor --json`

## Slash commands

```sh
## not a heading
```
"""

TRANSLATED = """# فرمان‌ها

<!-- i18n-source: {source} @ 6fc084f -->

> ترجمه ([English](../../{english}))

<a id="slash-commands"></a>
## فرمان‌ها

<a id="aipager-doctor---json"></a>
### `aipager doctor --json`

<a id="slash-commands-1"></a>
## فرمان‌ها
"""


def _tree(tmp_path, codes=("fa",)):
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("# aipager\n\n## Install\n", encoding="utf-8")
    for name, source in tool.DOC_PAGES.items():
        if name != "README.md":
            (tmp_path / source).write_text(ENGLISH, encoding="utf-8")
    for code in codes:
        folder = tmp_path / "docs" / "i18n" / code
        folder.mkdir(parents=True)
        for name, source in tool.DOC_PAGES.items():
            if name == "README.md":
                text = ('# aipager\n\n<!-- i18n-source: README.md @ 6fc084f -->\n\n'
                        '<a id="install"></a>\n## نصب\n\n[فرمان‌ها](commands.md#slash-commands)\n')
            else:
                english = source.removeprefix("docs/")
                text = TRANSLATED.format(source=source, english=english)
            (folder / name).write_text(text, encoding="utf-8")
    return tmp_path / "docs" / "i18n" / codes[0] if codes else None


def _check(root):
    return tool.check_docs(root, codes=("fa", "ru"))


def test_the_repository_docs_pass():
    assert tool.check_docs() == []


def test_a_complete_tree_passes(tmp_path):
    _tree(tmp_path)
    assert _check(tmp_path) == []


def test_a_missing_page_is_named(tmp_path):
    (_tree(tmp_path) / "hooks.md").unlink()
    assert _check(tmp_path) == ["docs/i18n/fa/hooks.md: missing"]


def test_a_page_the_english_docs_lack_is_named(tmp_path):
    (_tree(tmp_path) / "extra.md").write_text("# x\n", encoding="utf-8")
    assert _check(tmp_path) == ["docs/i18n/fa/extra.md: not a page the English docs have"]


def test_a_folder_for_no_translated_language_is_named(tmp_path):
    _tree(tmp_path)
    (tmp_path / "docs" / "i18n" / "xx").mkdir()
    assert _check(tmp_path) == ["docs/i18n/xx: not a translated language's folder"]


def test_a_page_with_no_source_line_is_named(tmp_path):
    page = _tree(tmp_path) / "groups.md"
    page.write_text(page.read_text(encoding="utf-8").replace("<!-- i18n-source", "<!-- x"),
                    encoding="utf-8")
    assert _check(tmp_path) == [
        "docs/i18n/fa/groups.md: no <!-- i18n-source: ... @ <commit> --> line"]


def test_a_page_translated_from_another_page_is_named(tmp_path):
    page = _tree(tmp_path) / "groups.md"
    page.write_text(page.read_text(encoding="utf-8").replace("docs/groups.md @", "docs/hooks.md @"),
                    encoding="utf-8")
    assert _check(tmp_path) == [
        "docs/i18n/fa/groups.md: translated from docs/hooks.md, want docs/groups.md"]


def test_a_missing_english_anchor_is_named(tmp_path):
    """A link from another page (any language) to #slash-commands-1 needs
    the anchor, whatever the heading reads in the language."""
    page = _tree(tmp_path) / "security.md"
    page.write_text(page.read_text(encoding="utf-8").replace(
        '<a id="slash-commands-1"></a>\n', ""), encoding="utf-8")
    assert _check(tmp_path) == [
        "docs/i18n/fa/security.md: no anchor 'slash-commands-1' (the English heading's)"]


def test_a_link_to_a_missing_file_is_named(tmp_path):
    page = _tree(tmp_path) / "README.md"
    page.write_text(page.read_text(encoding="utf-8") + "[x](comands.md)\n", encoding="utf-8")
    assert _check(tmp_path) == ["docs/i18n/fa/README.md: link to a missing file: comands.md"]


def test_a_link_to_a_missing_anchor_is_named(tmp_path):
    page = _tree(tmp_path) / "README.md"
    page.write_text(page.read_text(encoding="utf-8") + "[x](groups.md#rols)\n", encoding="utf-8")
    assert _check(tmp_path) == ["docs/i18n/fa/README.md: link to a missing anchor: groups.md#rols"]


def test_a_link_to_an_anchor_of_the_same_page_is_checked(tmp_path):
    page = _tree(tmp_path) / "README.md"
    page.write_text(page.read_text(encoding="utf-8") + "[x](#install)\n[y](#nope)\n",
                    encoding="utf-8")
    assert _check(tmp_path) == ["docs/i18n/fa/README.md: link to a missing anchor: #nope"]


def test_a_link_out_of_the_docs_and_a_web_link_are_fine(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text("# c\n", encoding="utf-8")
    page = _tree(tmp_path) / "README.md"
    page.write_text(page.read_text(encoding="utf-8")
                    + "[c](../../../CHANGELOG.md) [w](https://aipager.run/x.md#y)\n",
                    encoding="utf-8")
    assert _check(tmp_path) == []


def test_a_link_inside_a_code_block_is_not_a_link(tmp_path):
    page = _tree(tmp_path) / "README.md"
    page.write_text(page.read_text(encoding="utf-8") + "```\n[x](nowhere.md)\n```\n",
                    encoding="utf-8")
    assert _check(tmp_path) == []


def test_a_long_dash_outside_code_is_named(tmp_path):
    page = _tree(tmp_path) / "observers.md"
    page.write_text(page.read_text(encoding="utf-8") + "یک — دو\n`a — b`\n",
                    encoding="utf-8")
    assert _check(tmp_path) == [
        "docs/i18n/fa/observers.md: a long dash (U+2014) outside code: یک — دو"]


def test_a_direction_control_is_named(tmp_path):
    page = _tree(tmp_path) / "observers.md"
    page.write_text(page.read_text(encoding="utf-8") + "a‮b\n", encoding="utf-8")
    assert _check(tmp_path) == [
        "docs/i18n/fa/observers.md: a direction control character: a‮b"]


@pytest.mark.parametrize("heading, slug", [
    ("Slash commands", "slash-commands"),
    ("`aipager doctor --json`", "aipager-doctor---json"),
    ("Switching a running session's model", "switching-a-running-sessions-model"),
    ("Command line: `aipager setup` (for coding agents)",
     "command-line-aipager-setup-for-coding-agents"),
    ("Root folders", "root-folders"),
], ids=["plain", "code", "apostrophe", "colon-and-parens", "two-words"])
def test_slugs_are_githubs(heading, slug):
    assert tool.doc_slug(heading) == slug


def test_a_page_behind_its_english_page_is_listed(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@example.com",
                        "-c", "user.name=t", *args], check=True, capture_output=True)

    _tree(tmp_path)
    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "one")
    commit = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "--short", "HEAD"],
                            check=True, capture_output=True, text=True).stdout.strip()
    for page in (tmp_path / "docs" / "i18n" / "fa").glob("*.md"):
        page.write_text(page.read_text(encoding="utf-8").replace("6fc084f", commit),
                        encoding="utf-8")
    git("commit", "-qam", "two")
    assert tool.stale_docs(tmp_path) == []
    (tmp_path / "docs" / "hooks.md").write_text(ENGLISH + "\nmore\n", encoding="utf-8")
    git("commit", "-qam", "three")
    assert tool.stale_docs(tmp_path) == [
        f"docs/i18n/fa/hooks.md: docs/hooks.md changed after {commit}"]
