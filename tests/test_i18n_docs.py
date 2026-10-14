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

TRANSLATED = """<div dir="rtl">

# فرمان‌ها

<!-- i18n-source: {source} @ 6fc084f -->

> ترجمه ([English](../../{english}))

## <a id="slash-commands"></a>فرمان‌ها

<div dir="ltr">

```sh
aipager doctor
```

</div>

### <a id="aipager-doctor---json"></a>`aipager doctor --json`

## <a id="slash-commands-1"></a>فرمان‌ها

</div>
"""

README = """<div dir="rtl">

# aipager

<!-- i18n-source: README.md @ 6fc084f -->

## <a id="install"></a>نصب

[فرمان‌ها](commands.md#slash-commands)

</div>
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
                text = README
            else:
                english = source.removeprefix("docs/")
                text = TRANSLATED.format(source=source, english=english)
            if code not in tool._RTL_CODES:
                text = text.replace('<div dir="rtl">\n\n', "").replace('<div dir="ltr">\n\n', "")
                text = text.replace("\n</div>\n", "\n")
            (folder / name).write_text(text, encoding="utf-8")
    return tmp_path / "docs" / "i18n" / codes[0] if codes else None


def _check(root):
    return tool.check_docs(root, codes=("fa", "ru"))


def _append(page, extra):
    """Add lines at the end of a page, inside its closing </div>."""
    text = page.read_text(encoding="utf-8")
    if text.rstrip().endswith("</div>"):
        head = text.rstrip()[:-len("</div>")]
        page.write_text(f"{head}{extra}\n</div>\n", encoding="utf-8")
    else:
        page.write_text(text + extra, encoding="utf-8")


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
        '<a id="slash-commands-1"></a>', ""), encoding="utf-8")
    assert _check(tmp_path) == [
        "docs/i18n/fa/security.md: no anchor 'slash-commands-1' (the English heading's)"]


def test_a_link_to_a_missing_file_is_named(tmp_path):
    _append(_tree(tmp_path) / "README.md", "[x](comands.md)\n")
    assert _check(tmp_path) == ["docs/i18n/fa/README.md: link to a missing file: comands.md"]


def test_a_link_to_a_missing_anchor_is_named(tmp_path):
    _append(_tree(tmp_path) / "README.md", "[x](groups.md#rols)\n")
    assert _check(tmp_path) == ["docs/i18n/fa/README.md: link to a missing anchor: groups.md#rols"]


def test_a_link_to_an_anchor_of_the_same_page_is_checked(tmp_path):
    _append(_tree(tmp_path) / "README.md", "[x](#install)\n[y](#nope)\n")
    assert _check(tmp_path) == ["docs/i18n/fa/README.md: link to a missing anchor: #nope"]


def test_a_link_out_of_the_docs_and_a_web_link_are_fine(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text("# c\n", encoding="utf-8")
    _append(_tree(tmp_path) / "README.md",
            "[c](../../../CHANGELOG.md) [w](https://aipager.run/x.md#y)\n")
    assert _check(tmp_path) == []


def test_a_link_inside_a_code_block_is_not_a_link(tmp_path):
    _append(_tree(tmp_path) / "README.md",
            '<div dir="ltr">\n\n```\n[x](nowhere.md)\n```\n\n</div>\n')
    assert _check(tmp_path) == []


def test_a_long_dash_outside_code_is_named(tmp_path):
    _append(_tree(tmp_path) / "hooks.md", "یک \u2014 دو\n`a \u2014 b`\n")
    assert _check(tmp_path) == [
        "docs/i18n/fa/hooks.md: a long dash (U+2014) outside code: یک \u2014 دو"]


def test_a_direction_control_is_named(tmp_path):
    _append(_tree(tmp_path) / "hooks.md", "a\u202eb\n")
    assert _check(tmp_path) == [
        "docs/i18n/fa/hooks.md: a direction control character: a\u202eb"]


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


def test_a_left_to_right_page_needs_no_div(tmp_path):
    _tree(tmp_path, codes=("ru",))
    assert _check(tmp_path) == []


def test_a_right_to_left_page_must_start_in_an_rtl_div(tmp_path):
    page = _tree(tmp_path) / "hooks.md"
    page.write_text(page.read_text(encoding="utf-8").replace('<div dir="rtl">\n\n', "", 1),
                    encoding="utf-8")
    assert _check(tmp_path) == ['docs/i18n/fa/hooks.md: does not start with <div dir="rtl">']


def test_a_right_to_left_page_must_end_its_div(tmp_path):
    page = _tree(tmp_path) / "hooks.md"
    page.write_text(page.read_text(encoding="utf-8").rstrip()[:-len("</div>")], encoding="utf-8")
    assert _check(tmp_path) == ["docs/i18n/fa/hooks.md: does not end with </div>"]


def test_code_on_a_right_to_left_page_must_sit_in_an_ltr_div(tmp_path):
    page = _tree(tmp_path) / "hooks.md"
    page.write_text(page.read_text(encoding="utf-8").replace('<div dir="ltr">\n\n', "", 1),
                    encoding="utf-8")
    assert _check(tmp_path) == [
        'docs/i18n/fa/hooks.md: a code block outside <div dir="ltr">: line 11']


def test_code_on_a_right_to_left_page_must_close_its_ltr_div(tmp_path):
    page = _tree(tmp_path) / "hooks.md"
    page.write_text(page.read_text(encoding="utf-8").replace("```\n\n</div>\n\n###", "```\n\n###", 1),
                    encoding="utf-8")
    assert _check(tmp_path) == [
        "docs/i18n/fa/hooks.md: a code block with no </div> after it: line 15"]


def test_a_list_numbered_with_persian_digits_is_named(tmp_path):
    """Markdown only reads ASCII digits as a list number, so "۱. " runs the
    items together into one paragraph."""
    _append(_tree(tmp_path) / "hooks.md", "\u06f1. \u06cc\u06a9\n")
    assert _check(tmp_path) == [
        "docs/i18n/fa/hooks.md: a numbered list item needs Latin digits (1.): \u06f1. \u06cc\u06a9"]


# ---- right-to-left order on GitHub ------------------------------------------------
# GitHub gives each paragraph, heading and list dir="auto", and inside a
# right-to-left line the bidi algorithm joins or flips left-to-right runs:
# the README's "... Telegram. Claude ..." read backwards, "/kill" showed
# "kill/" (operator, 2026-10-13). Invisible marks fix each place.

FA = "\u06a9\u0646\u062a\u0631\u0644"            # a Persian word
FA2 = "\u0631\u0648\u06cc \u06a9\u0627\u0645\u067e\u06cc\u0648\u062a\u0631"


def _rtl_problems(tmp_path, extra):
    _append(_tree(tmp_path) / "hooks.md", extra)
    return [p for p in _check(tmp_path) if "--fix-rtl" in p]


def _kinds(text):
    return sorted(kind for _n, _c, kind, _s in tool.rtl_bidi_problems(text))


def test_a_sentence_ending_on_an_english_word_before_one_starting_with_one_is_named(tmp_path):
    found = _rtl_problems(tmp_path, f"{FA} \u0628\u0627 Telegram. Claude {FA2}.\n")
    assert len(found) == 1 and "punctuation between two left-to-right runs" in found[0]
    assert "Telegram. Claude" in found[0] and found[0].startswith("docs/i18n/fa/hooks.md:")


@pytest.mark.parametrize("line", [
    f"Claude {FA2} \u0627\u062c\u0631\u0627 \u0645\u06cc\u200c\u0634\u0648\u062f.",
    "- `aipager start` \u0631\u0627 \u0628\u0632\u0646\u06cc\u062f.",
    "## Claude \u0648 aipager",
    "> [Claude Code](https://claude.com) \u0631\u0627 \u0646\u0635\u0628 \u06a9\u0646\u06cc\u062f.",
], ids=["paragraph", "list-item", "heading", "quote"])
def test_a_block_starting_with_an_english_word_is_named(tmp_path, line):
    found = _rtl_problems(tmp_path, line + "\n")
    assert any("starts with a left-to-right word" in p for p in found), found


@pytest.mark.parametrize("line", [
    f"{FA} \u0628\u0627 Telegram.&rlm; Claude \u0631\u0648\u06cc.",
    f"&rlm;Claude {FA2}.",
    "- &rlm;`aipager start` \u0631\u0627 \u0628\u0632\u0646\u06cc\u062f.",
    "## &rlm;Claude \u0648 aipager",
    f"{FA} \u0628\u0627 Telegram. &rlm;Claude \u0631\u0648\u06cc.",
    f"{FA} \u0645\u062b\u0644 e.g.&lrm; foo \u0631\u0648\u06cc.",
    f"{FA} \u062f\u0633\u062a\u0648\u0631 &lrm;`/kill` \u0631\u0627 \u0628\u0632\u0646\u06cc\u062f.",
], ids=["join", "paragraph", "list-item", "heading", "join-after-the-blank",
        "kept-together-on-purpose", "lead"])
def test_the_marked_forms_pass(tmp_path, line):
    assert _rtl_problems(tmp_path, line + "\n") == []


@pytest.mark.parametrize("line", [
    "\u0645\u06cc\u200c\u06af\u0648\u06cc\u062f \u00abStart it, then stop it.\u00bb \u0648 \u062a\u0645\u0627\u0645.",
    "\u0645\u06cc\u200c\u06af\u0648\u06cc\u062f (\"Open t.me and press Start, then run this.\") \u0648 \u062a\u0645\u0627\u0645.",
    "\u0645\u06cc\u200c\u06af\u0648\u06cc\u062f \u201cStart it, then stop it.\u201d \u0648 \u062a\u0645\u0627\u0645.",
    "\u06a9\u062f `a. b` \u0627\u0633\u062a.",
    "Install aipager. Then run it.",
    "\u0646\u0633\u062e\u0647\u0654 2.1.251 \u0648 Write/Edit \u0647\u0633\u062a.",
    "\u0631\u0648\u06cc macOS Tahoe (26.x) \u0648 Portugu\u00eas (Brasil) \u0647\u0633\u062a.",
    "\u06a9\u0646\u062a\u0631\u0644 \u00ab[Telegram. Claude](u)\u00bb \u0631\u0648\u06cc.",
], ids=["guillemets", "double-quotes", "curly-quotes", "code-span", "all-english", "no-gap",
        "an-english-name-keeps-its-brackets", "a-link-inside-a-quotation"])
def test_quotes_code_names_and_english_lines_pass(tmp_path, line):
    assert _rtl_problems(tmp_path, line + "\n") == []


def test_a_quotation_closes(tmp_path):
    """After a closing \u00bb or \u201d the text is prose again."""
    for close in ("\u00aba.\u00bb ", "\u201ca.\u201d "):
        assert _kinds(f'<div dir="rtl">\n\n{FA} {close}Telegram. Claude {FA2}.\n\n</div>\n') == ["join"]


@pytest.mark.parametrize("block", [
    '<div dir="ltr">\n\n```\n\n\u0628\u0627 Telegram. Claude \u0631\u0648\u06cc\n\nClaude \u0631\u0648\u06cc\n\n```\n\n</div>\n',
    "\n    Telegram. Claude \u0631\u0648\u06cc\n    Claude \u0631\u0648\u06cc\n\n",
], ids=["fenced", "indented"])
def test_code_blocks_are_not_read(tmp_path, block):
    assert _rtl_problems(tmp_path, block) == []


def test_a_table_cell_reads_right_to_left_even_in_english(tmp_path):
    table = "| x | y |\n|---|---|\n| `new`\u060c `status` | \u062a\u0648\u0636\u06cc\u062d |\n"
    found = _rtl_problems(tmp_path, table)
    assert len(found) == 1 and "new\u060c status" in found[0], found


def test_a_table_cell_is_fixed_in_place():
    row = "| `a`: `b` | `c\\|d`\u060c `e` | \u0631\u0648\u06cc |"
    fixed = tool.fix_rtl_bidi('<div dir="rtl">\n\n| x | y | z |\n|---|---|---|\n' + row + "\n\n</div>\n")
    assert "| `a`:&rlm; `b` | `c\\|d`\u060c&rlm; `e` | \u0631\u0648\u06cc |" in fixed


@pytest.mark.parametrize("line, joined", [
    ("\u0646\u0633\u062e\u0647\u200c\u0647\u0627\u06cc `latest`\u060c `1.2` \u0647\u0633\u062a.", "latest\u060c 1.2"),
    ("\u0641\u0631\u0645\u0627\u0646\u200c\u0647\u0627\u06cc `/mode auto`\u060c `/mode full` \u0647\u0633\u062a.", "auto\u060c /mode"),
    ("\u0632\u0628\u0627\u0646\u200c\u0647\u0627: \u0420\u0443\u0441\u0441\u043a\u0438\u0439 \u00b7 English \u062a\u0645\u0627\u0645.", "\u0420\u0443\u0441\u0441\u043a\u0438\u0439 \u00b7 English"),
    ("\u0627\u06cc\u0646 Telegram... Claude \u0631\u0648\u06cc.", "Telegram... Claude"),
    ("**\u067e\u06cc\u0627\u0645 aipager.** Claude \u0631\u0648\u06cc.", "aipager. Claude"),
], ids=["digit-after-english", "neutral-before-a-letter", "other-scripts", "a-run-of-dots",
        "after-bold"])
def test_a_join_is_judged_like_the_browser(tmp_path, line, joined):
    found = _rtl_problems(tmp_path, f"\u0627\u06cc\u0646 {line}\n")
    joins = [p for p in found if "punctuation between two left-to-right runs" in p]
    assert len(joins) == 1 and joined in joins[0], found


def test_a_run_of_dots_gets_one_mark_after_it():
    fixed = tool.fix_rtl_bidi('<div dir="rtl">\n\n\u0627\u06cc\u0646 Telegram... Claude \u0631\u0648\u06cc.\n\n</div>\n')
    assert "Telegram...&rlm; Claude" in fixed


@pytest.mark.parametrize("line, fixed", [
    (f"{FA} \u062f\u0633\u062a\u0648\u0631 `/kill` \u0631\u0627 \u0628\u0632\u0646\u06cc\u062f.", f"{FA} \u062f\u0633\u062a\u0648\u0631 &lrm;`/kill` \u0631\u0627 \u0628\u0632\u0646\u06cc\u062f."),
    (f"{FA} \u0628\u0627 @BotFather \u0631\u0628\u0627\u062a \u0628\u0633\u0627\u0632\u06cc\u062f.", f"{FA} \u0628\u0627 &lrm;@BotFather \u0631\u0628\u0627\u062a \u0628\u0633\u0627\u0632\u06cc\u062f."),
    (f"{FA} \u067e\u0648\u0634\u0647\u0654 ~/.claude \u0627\u0633\u062a.", f"{FA} \u067e\u0648\u0634\u0647\u0654 &lrm;~/.claude \u0627\u0633\u062a."),
    (f"{FA} \u0632\u0645\u0627\u0646 10m-1h \u0627\u0633\u062a.", f"{FA} \u0632\u0645\u0627\u0646 &lrm;10m-1h \u0627\u0633\u062a."),
    (f"{FA} (\u00ab/stop\u00bb \u0631\u0627) \u0628\u0632\u0646\u06cc\u062f.", f"{FA} (\u00ab&lrm;/stop\u00bb \u0631\u0627) \u0628\u0632\u0646\u06cc\u062f."),
], ids=["slash-command", "at-name", "home-path", "range", "after-a-quote-mark"])
def test_a_word_starting_with_a_symbol_after_persian_keeps_its_start(line, fixed):
    """Without the mark "/kill" shows "kill/": the slash takes the Persian
    side. The mark goes before the code span, never inside it."""
    page = f'<div dir="rtl">\n\n{line}\n\n</div>\n'
    assert _kinds(page) == ["lead"]
    assert tool.fix_rtl_bidi(page) == page.replace(line, fixed)


@pytest.mark.parametrize("line, fixed", [
    ("3. `registry.save()`.", "3. `registry.save()`&lrm;."),
    ("\u0627\u06cc\u0646 `<task-notification>` (Claude \u06a9\u0647 \u062a\u0645\u0627\u0645 \u0634\u062f) \u0627\u0633\u062a.",
     "\u0627\u06cc\u0646 &lrm;`<task-notification>`&lrm; (Claude \u06a9\u0647 \u062a\u0645\u0627\u0645 \u0634\u062f) \u0627\u0633\u062a."),
    ("\u067e\u0648\u0634\u0647\u0654 `packaging/snap/` \u0631\u0627 \u0628\u0628\u06cc\u0646\u06cc\u062f.", "\u067e\u0648\u0634\u0647\u0654 `packaging/snap/`&lrm; \u0631\u0627 \u0628\u0628\u06cc\u0646\u06cc\u062f."),
], ids=["call-in-a-list", "before-a-persian-bracket", "path"])
def test_code_ending_with_a_symbol_before_persian_keeps_its_end(line, fixed):
    page = f'<div dir="rtl">\n\n1. \u0627\u0648\u0644 {FA}.\n{line}\n\n</div>\n'
    assert "trail" in _kinds(page)
    assert tool.fix_rtl_bidi(page) == page.replace(line, fixed)


def test_marks_inside_brackets_turn_them_and_the_next_word_is_kept():
    """A right-to-left mark inside "( , )" turns the brackets right to left
    (the algorithm's N0), so "-R" after "(" would show "R-": it gets its
    own left-to-right mark."""
    page = f'<div dir="rtl">\n\n\u0627\u06cc\u0646 `grep -r` (`-R`\u060c `--recursive`\u060c `-d recurse`) {FA}.\n\n</div>\n'
    fixed = tool.fix_rtl_bidi(page)
    assert "(&lrm;`-R`\u060c&rlm; &lrm;`--recursive`\u060c&rlm; &lrm;`-d recurse`)" in fixed
    assert tool.rtl_bidi_problems(fixed) == []


def test_no_mark_goes_inside_code():
    page = '<div dir="rtl">\n\n\u067e\u06cc\u0627\u0645 (`\u06a9\u0627\u0631 \u062a\u0645\u0627\u0645 \u0634\u062f. \u062f\u0648\u0628\u0627\u0631\u0647 /new \u0628\u0641\u0631\u0633\u062a\u06cc\u062f.`) \u0627\u0633\u062a.\n\n</div>\n'
    fixed = tool.fix_rtl_bidi(page)
    code = "`\u06a9\u0627\u0631 \u062a\u0645\u0627\u0645 \u0634\u062f. \u062f\u0648\u0628\u0627\u0631\u0647 /new \u0628\u0641\u0631\u0633\u062a\u06cc\u062f.`"
    assert code in fixed


def test_a_double_backtick_span_is_code():
    assert _kinds('<div dir="rtl">\n\n\u0627\u06cc\u0646 ``a`. b`` \u0627\u0633\u062a.\n\n</div>\n') == []


@pytest.mark.parametrize("text, kinds", [
    (f"{FA} \u0627\u0648\u0644.\nClaude {FA2}.\n", []),
    (f"> {FA} \u0627\u0648\u0644.\n> Claude {FA2}.\n", []),
    (f"## {FA}\nClaude {FA2}.\n", ["start"]),
    (f"{FA} \u0627\u0648\u0644.\n## {FA}\nClaude {FA2}.\n", ["start"]),
], ids=["second-line-of-a-paragraph", "second-line-of-a-quote", "paragraph-after-a-heading",
        "a-heading-ends-the-paragraph-before-it"])
def test_only_a_blocks_first_line_decides_its_direction(text, kinds):
    assert _kinds(f'<div dir="rtl">\n\n{text}\n</div>\n') == kinds


def test_a_lists_first_item_decides_for_the_whole_list():
    """GitHub puts dir="auto" on the list, not the item: an English first
    item lays out the Persian items after it left to right."""
    page = f'<div dir="rtl">\n\n- `aipager start`\n- {FA} {FA2}.\n- {FA}.\n\n</div>\n'
    assert _kinds(page) == ["start"]
    assert "- &rlm;`aipager start`" in tool.fix_rtl_bidi(page)
    english = '<div dir="rtl">\n\n- `aipager start`\n- `aipager stop`\n\n</div>\n'
    assert _kinds(english) == []


def test_an_arabic_indic_digit_keeps_its_own_direction():
    """A Persian digit after an English word turns English (the bidi
    algorithm's W7); an Arabic-Indic one does not, so no mark is needed."""
    persian = '<div dir="rtl">\n\n\u0627\u06cc\u0646 `latest`\u060c `\u06f1.\u06f2` \u0647\u0633\u062a.\n\n</div>\n'
    arabic = '<div dir="rtl">\n\n\u0647\u0630\u0627 `latest`\u060c `\u0661.\u0662` \u0647\u0646\u0627.\n\n</div>\n'
    assert _kinds(persian) == ["join"]
    assert _kinds(arabic) == []


def test_the_fix_only_adds_marks_and_leaves_nothing():
    text = ('<div dir="rtl">\n\nClaude \u0628\u0627 Telegram. Claude \u0631\u0648\u06cc `/kill`.\n\n'
            "- `a`\u060c `b` \u0648 `c` (`d`) \u0647\u0633\u062a.\n\n"
            "| x | y |\n|---|---|\n| `e`: `f` | \u0631\u0648\u06cc |\n\n</div>\n")
    fixed = tool.fix_rtl_bidi(text)
    assert tool.rtl_bidi_problems(fixed) == []
    assert fixed.replace("&rlm;", "").replace("&lrm;", "") == text
    assert "Claude \u0628\u0627 Telegram.&rlm; Claude \u0631\u0648\u06cc &lrm;`/kill`" in fixed
    assert fixed.startswith('<div dir="rtl">\n\n&rlm;Claude')


def test_a_mark_in_a_heading_keeps_its_anchor():
    """GitHub drops the mark from a heading's anchor, and so does the
    anchor check."""
    assert tool.doc_slug("&rlm;Claude \u0648 aipager") == tool.doc_slug("Claude \u0648 aipager")
    assert tool.doc_slug("\u200fClaude") == tool.doc_slug("Claude")


def test_a_list_item_starting_with_a_command_gets_both_marks_in_order():
    """The right-to-left mark first (the item reads right to left), then the
    left-to-right one (the command keeps its slash)."""
    page = '<div dir="rtl">\n\n- `/help` \u0631\u0627 \u0628\u0632\u0646\u06cc\u062f.\n\n</div>\n'
    assert _kinds(page) == ["lead", "start"]
    assert "- &rlm;&lrm;`/help`" in tool.fix_rtl_bidi(page)


def _page(body):
    return f'<div dir="rtl">\n\n{body}\n\n</div>\n'


def test_code_inside_link_text_stays_code():
    """The mark after a link whose text is code ending with a symbol goes
    after the whole link, never into the code or the URL."""
    page = _page("\u067e\u0648\u0634\u0647\u0654 [`save()`](#s) \u0631\u0627 \u0628\u0628\u06cc\u0646\u06cc\u062f.")
    assert tool.fix_rtl_bidi(page) == _page("\u067e\u0648\u0634\u0647\u0654 [`save()`](#s)&lrm; \u0631\u0627 \u0628\u0628\u06cc\u0646\u06cc\u062f.")


def test_an_escaped_symbol_gets_its_mark_before_the_backslash():
    """"\\-\\-recursive": the mark goes before the backslash; between the
    backslash and the dash it would escape the "&" and show as text."""
    page = _page("\u06a9\u0646\u062a\u0631\u0644 \\-\\-recursive \u0631\u0648\u06cc.")
    fixed = tool.fix_rtl_bidi(page)
    assert fixed == _page("\u06a9\u0646\u062a\u0631\u0644 &lrm;\\-\\-recursive \u0631\u0648\u06cc.")
    assert tool.rtl_bidi_problems(fixed) == []
    label = _page("\u06a9\u0646\u062a\u0631\u0644 \\<label\\> \u0631\u0648\u06cc.")
    assert tool.fix_rtl_bidi(label) == _page("\u06a9\u0646\u062a\u0631\u0644 &lrm;\\<label\\>&lrm; \u0631\u0648\u06cc.")


def test_an_escaped_backtick_is_text():
    assert "join" in _kinds(_page("\u0627\u06cc\u0646 \\`a. b\\` \u0631\u0648\u06cc."))


def test_a_lone_backtick_is_text():
    assert _kinds(_page("\u0627\u06cc\u0646 ` Telegram. Claude \u0631\u0648\u06cc.")) == ["join"]


def test_a_join_closed_by_single_emphasis_is_found():
    assert _kinds(_page("\u0627\u06cc\u0646 *Telegram.* Claude \u0631\u0648\u06cc.")) == ["join"]


def test_a_paragraph_after_a_blank_line_in_an_item_is_its_own_block_and_a_loose_list():
    page = _page("- \u0627\u0648\u0644 \u0641\u0627\u0631\u0633\u06cc.\n\n  Claude \u0631\u0648\u06cc \u06a9\u0627\u0645\u067e\u06cc\u0648\u062a\u0631.\n- \u062f\u0648\u0645.")
    assert _kinds(page) == ["start"]
    assert tool.rtl_loose_lists(page) == [3]
    assert tool.rtl_loose_lists(_page("- \u0627\u0648\u0644.\n- \u062f\u0648\u0645.")) == []


def test_a_loose_list_is_named(tmp_path):
    _append(_tree(tmp_path) / "hooks.md", "- \u0627\u0648\u0644.\n\n- \u062f\u0648\u0645.\n")
    found = [p for p in _check(tmp_path) if "a list with a blank line inside it" in p]
    assert len(found) == 1 and found[0].startswith("docs/i18n/fa/hooks.md:"), found


def test_every_line_of_an_indented_code_block_is_skipped():
    assert _kinds(_page("\u0641\u0627\u0631\u0633\u06cc.\n\n    \u0641\u0627\u0631\u0633\u06cc\n    Telegram. Claude \u0631\u0648\u06cc\n    Claude \u0631\u0648\u06cc")) == []


def test_an_html_line_ends_the_paragraph_before_it():
    page = _page('\u0641\u0627\u0631\u0633\u06cc.\n<a id="x"></a>\nClaude \u0631\u0648\u06cc.')
    assert [(n, c, k) for n, c, k, _s in tool.rtl_bidi_problems(page)] == [(5, 0, "start")]


def test_a_quote_ends_a_list():
    """A new list after a quote has its own first item."""
    page = _page("- \u0641\u0627\u0631\u0633\u06cc\n> \u0641\u0627\u0631\u0633\u06cc \u062f\u06cc\u06af\u0631\n- `aipager start`\n- \u0641\u0627\u0631\u0633\u06cc")
    assert _kinds(page) == ["start"]


def test_a_lead_after_a_literal_bracket_goes_inside_it():
    assert tool.fix_rtl_bidi(_page("\u0627\u06cc\u0646 [/stop] \u0631\u0627 \u0628\u0632\u0646\u06cc\u062f.")) == _page("\u0627\u06cc\u0646 [&lrm;/stop] \u0631\u0627 \u0628\u0632\u0646\u06cc\u062f.")


def test_a_sentence_split_over_two_lines_is_judged_whole():
    page = _page("\u06a9\u0646\u062a\u0631\u0644 \u0628\u0627 Telegram.\nClaude \u0631\u0648\u06cc \u06a9\u0627\u0645\u067e\u06cc\u0648\u062a\u0631.")
    assert _kinds(page) == ["join"]
    assert "Telegram.&rlm;\nClaude" in tool.fix_rtl_bidi(page)


@pytest.mark.parametrize("body, fixed", [
    ("> > Claude \u0631\u0648\u06cc \u06a9\u0627\u0645\u067e\u06cc\u0648\u062a\u0631.", "> > &rlm;Claude \u0631\u0648\u06cc \u06a9\u0627\u0645\u067e\u06cc\u0648\u062a\u0631."),
    ("> - Claude \u0631\u0648\u06cc \u06a9\u0627\u0645\u067e\u06cc\u0648\u062a\u0631.", "> - &rlm;Claude \u0631\u0648\u06cc \u06a9\u0627\u0645\u067e\u06cc\u0648\u062a\u0631."),
], ids=["nested-quote", "list-in-a-quote"])
def test_the_start_mark_goes_after_every_quote_and_list_marker(body, fixed):
    assert tool.fix_rtl_bidi(_page(body)) == _page(fixed)


def test_an_entity_reads_as_its_character():
    """"&lt;label&gt;" in text is "<label>": a symbol at each end."""
    page = _page("**\u062c\u0648\u0627\u0628 \u062f\u0627\u062f\u0646 \u0628\u0647 &lt;label&gt;** \u0631\u0627 \u0628\u0628\u06cc\u0646\u06cc\u062f.")
    assert tool.fix_rtl_bidi(page) == _page("**\u062c\u0648\u0627\u0628 \u062f\u0627\u062f\u0646 \u0628\u0647 &lrm;&lt;label&gt;**&lrm; \u0631\u0627 \u0628\u0628\u06cc\u0646\u06cc\u062f.")


def test_a_bare_url_is_one_word_and_no_mark_goes_into_it():
    page = _page("\u0627\u06cc\u0646 <https://aipager.run/x>. Claude \u0631\u0648\u06cc.")
    assert tool.fix_rtl_bidi(page) == _page("\u0627\u06cc\u0646 <https://aipager.run/x>.&rlm; Claude \u0631\u0648\u06cc.")


def test_a_link_with_brackets_or_a_title_keeps_its_url_whole():
    page = _page("\u0627\u06cc\u0646 [x](https://e.com/a_(b)). Claude \u0631\u0648\u06cc [y](#z \"t\"). Claude \u0631\u0648\u06cc.")
    fixed = tool.fix_rtl_bidi(page)
    assert "(https://e.com/a_(b))" in fixed and '(#z "t")' in fixed
    assert fixed.count("&rlm;") == 2


def test_the_fix_says_so_when_marks_do_not_settle(monkeypatch):
    monkeypatch.setattr(tool, "rtl_bidi_problems", lambda text: [(1, 0, "start", "x")])
    with pytest.raises(ValueError, match="did not settle"):
        tool.fix_rtl_bidi("x")


def test_strikethrough_marks_are_not_shown():
    assert _kinds(_page("\u0627\u06cc\u0646 ~~Telegram.~~ Claude \u0631\u0648\u06cc.")) == ["join"]


def test_a_quote_written_as_an_entity_is_a_quotation():
    assert _kinds(_page("\u0645\u06cc\u200c\u06af\u0648\u06cc\u062f &quot;Start it. Then stop.&quot; \u0648 \u062a\u0645\u0627\u0645.")) == []


def test_entities_read_as_their_characters():
    """"&nbsp;" is a blank (so a join), "&amp;" is "&" (so a symbol at the
    word's end, not the ";" of the entity)."""
    assert _kinds(_page("\u06a9\u0646\u062a\u0631\u0644 \u0628\u0627 Telegram.&nbsp;Claude \u0631\u0648\u06cc.")) == ["join"]
    assert tool.fix_rtl_bidi(_page("\u06a9\u0646\u062a\u0631\u0644 foo&amp; \u0631\u0648\u06cc.")) == _page("\u06a9\u0646\u062a\u0631\u0644 foo&amp;&lrm; \u0631\u0648\u06cc.")


@pytest.mark.parametrize("body, kinds", [
    ("\u06a9\u0646\u062a\u0631\u0644 &lt;label&gt; foo \u0631\u0648\u06cc.", ["lead"]),
    ("\u06a9\u0646\u062a\u0631\u0644 \u00abfoo/\u00bb \u0631\u0648\u06cc.", ["trail"]),
    ("\u06a9\u0646\u062a\u0631\u0644 \u00ab\u0641\u0627\u0631\u0633\u06cc\u00bbcd/ \u0631\u0648\u06cc.", ["trail"]),
    ("\u06a9\u0646\u062a\u0631\u0644 cd\u0641\u0627\u0631\u0633\u06cc/ \u0631\u0648\u06cc.", []),
], ids=["english-after-it", "inside-a-quotation", "a-quotation-ends-the-word", "a-persian-end"])
def test_a_word_gets_a_trail_only_before_right_to_left_text(body, kinds):
    assert _kinds(_page(body)) == kinds


def test_a_bare_url_ending_with_a_slash_gets_its_mark_after_the_brackets():
    page = _page("\u06a9\u0646\u062a\u0631\u0644 <https://x.com/a/> \u0631\u0648\u06cc.")
    assert tool.fix_rtl_bidi(page) == _page("\u06a9\u0646\u062a\u0631\u0644 <https://x.com/a/>&lrm; \u0631\u0648\u06cc.")


def test_an_english_loose_list_on_a_right_to_left_page_is_not_named():
    assert tool.rtl_loose_lists(_page("- one.\n\n- two.")) == []
    assert tool.rtl_loose_lists(_page("\u0645\u062a\u0646.\n\n- \u0627\u0648\u0644.\n\n- \u062f\u0648\u0645.")) == [5]


def test_an_indented_paragraph_in_a_list_is_text_not_code():
    assert "start" in _kinds(_page("- \u0627\u0648\u0644.\n\n    Claude \u0631\u0648\u06cc \u06a9\u0627\u0645\u067e\u06cc\u0648\u062a\u0631."))


def test_an_indented_line_right_after_a_fence_is_code():
    assert _kinds(_page("```\nx\n```\n    Telegram. Claude \u0631\u0648\u06cc")) == []


def test_a_table_ends_a_list():
    page = _page("- \u0641\u0627\u0631\u0633\u06cc\n\n| a | b |\n|---|---|\n- `aipager start`\n- \u0641\u0627\u0631\u0633\u06cc")
    assert "start" in _kinds(page)


@pytest.mark.parametrize("body, kinds", [
    ("\u0627\u06cc\u0646 `aipager config` / `aipager setup` \u0631\u0648\u06cc.", ["join"]),
    ("\u06a9\u0646\u062a\u0631\u0644 foo/ bar \u0631\u0648\u06cc.", []),
    ("\u06a9\u0646\u062a\u0631\u0644 a/b \u0631\u0648\u06cc.", []),
], ids=["spaced", "after-a-word", "inside-a-word"])
def test_only_a_spaced_slash_separates(body, kinds):
    assert _kinds(_page(body)) == kinds


def test_an_image_shows_no_text():
    assert _kinds(_page("\u06a9\u0646\u062a\u0631\u0644 ![Claude](a.png). Claude \u0631\u0648\u06cc.")) == []


def test_a_sign_before_digits_keeps_its_side():
    assert tool.fix_rtl_bidi(_page("\u062f\u0645\u0627 -5 \u062f\u0631\u062c\u0647 \u0627\u0633\u062a.")) == _page("\u062f\u0645\u0627 &lrm;-5 \u062f\u0631\u062c\u0647 \u0627\u0633\u062a.")


def test_the_docs_command_puts_the_marks_in(tmp_path):
    page = _tree(tmp_path) / "hooks.md"
    _append(page, f"{FA} \u0628\u0627 Telegram. Claude {FA2}.\n")
    assert any("--fix-rtl" in p for p in _check(tmp_path))
    assert tool.fix_rtl_docs(tmp_path, codes=("fa",)) == [page]
    assert "Telegram.&rlm; Claude" in page.read_text(encoding="utf-8")
    assert not any("--fix-rtl" in p for p in _check(tmp_path))
    assert tool.fix_rtl_docs(tmp_path, codes=("fa",)) == []


def test_a_trail_goes_before_closing_quotes_and_punctuation():
    """They take the text's side; the word's own symbol keeps the word's."""
    page = _page("\u06a9\u0646\u062a\u0631\u0644 \u00abfoo/\u00bb \u0648 bar/. \u0631\u0648\u06cc.")
    assert tool.fix_rtl_bidi(page) == _page(
        "\u06a9\u0646\u062a\u0631\u0644 \u00abfoo/&lrm;\u00bb \u0648 bar/&lrm;. \u0631\u0648\u06cc.")


def test_code_ending_with_a_symbol_gets_one_trail():
    page = _page("\u067e\u0648\u0634\u0647\u0654 `packaging/snap/` \u0631\u0627 \u0628\u0628\u06cc\u0646\u06cc\u062f.")
    assert _kinds(page) == ["trail"]


def test_code_spans_joined_only_by_emphasis_stay_apart():
    """`\u0641\u0627`**`/b`**: two code spans; the second one's slash still
    needs its mark (as one span holding Persian it would get none)."""
    assert _kinds(_page("\u0627\u06cc\u0646 `\u0641\u0627`**`/b`** \u0631\u0648\u06cc.")) == ["lead"]


def test_the_docs_command_line_puts_the_marks_in(tmp_path, monkeypatch, capsys):
    page = _tree(tmp_path) / "hooks.md"
    _append(page, f"{FA} \u0628\u0627 Telegram. Claude {FA2}.\n")
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    assert tool.main(["docs", "--fix-rtl"]) == 0
    assert "marked docs/i18n/fa/hooks.md" in capsys.readouterr().out
    assert "Telegram.&rlm; Claude" in page.read_text(encoding="utf-8")

