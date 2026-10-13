"""The Mini App's Create folder (operator, 2026-10-12: the "+" glued into
the name field was "very bad in ux"; "make it a text create folder
instead. make it robust").

The page's script runs in the form harness (tests/js/miniapp_newsession.js,
scenario ``folder``): a text button under the field, one request at a time,
every kind of answer handled, and a name table judged by the server's own
validator so the page never refuses what the server accepts or the reverse.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from aipager.miniapp.phrase import Phrase

FORM_HARNESS = Path(__file__).parent / "js" / "miniapp_newsession.js"

#: Names the page and the server must judge alike (server: launch.validate_new_dir_name).
NAMES = ["my-project", "a", "A1.b_c-d", "v2.0", "a..b", "  padded  ", "", "   ",
         "x" * 64, "x" * 65, "a/b", "..", ".", ".hidden", "-dash", "_under", "a b",
         "naïve", "پروژه", "名前", "a\\b", "tab\there", "semi;colon",
         # Pasted edges where JavaScript's trim() and Python's strip() differ.
         "x\x1c", "x\x85", "\ufeffx", "\u3000x\u3000", "x\u2028", "\xa0x",
         # Astral characters: one code point (Python's len()), two UTF-16
         # units (JavaScript's .length), so the reason differs if the page
         # counts units.
         "😀", "😀" * 33, "😀" * 64, "😀" * 65, "𝒳" * 32, "𝒳" * 33, "a" + "𝒳" * 63,
         "x" * 63 + "😀", "x" * 64 + "😀"]


@pytest.fixture(scope="module")
def node_bin():
    exe = shutil.which("node") or shutil.which("nodejs")
    if not exe:
        pytest.skip("node not available")
    return exe


def _reveal_markup() -> str:
    from aipager.miniapp.static import INDEX_HTML
    m = re.search(r'<div id="new-folder-reveal" class="reveal">(.*?)\n  </div>', INDEX_HTML, re.S)
    assert m, "the new-folder reveal is missing"
    return m.group(1)


def _drive(node_bin, tmp_path, html, names=()):
    page = tmp_path / "folder.html"
    page.write_text(html, encoding="utf-8")
    from aipager.miniapp import launch
    table = []
    for n in names:
        reason = launch.validate_new_dir_name(n)[1]
        # The page words these two exactly as the server does; the rest
        # (empty, a NUL) the page does not word.
        shown = (str(reason) if reason is launch._FOLDER_PATTERN
                 or isinstance(reason, Phrase) else None)
        table.append([n, reason == "", shown])
    env = dict(os.environ, AIPAGER_TEST_FOLDER_NAMES=json.dumps(table))
    return subprocess.run([node_bin, str(FORM_HARNESS), str(page), "folder"],
                          capture_output=True, text=True, timeout=60, env=env)


def test_create_folder_is_a_text_button_under_the_field():
    reveal = _reveal_markup()
    field = re.search(r'<div class="prefix-field"[^>]*>(.*?)</div>', reveal, re.S)
    assert field, reveal
    assert "new-folder-create" not in field.group(1), "the button is still inside the field"
    assert "<button" not in field.group(1)
    button = re.search(r'<button[^>]*id="new-folder-create"[^>]*>(.*?)</button>', reveal, re.S)
    assert button, reveal
    tag, label = button.group(0), button.group(1)
    assert 'data-i18n="ui.new.create_folder"' in tag
    assert label == "Create folder", label
    assert "<svg" not in tag and "aria-label" not in tag
    assert re.search(r'class="[^"]*\bprimary\b[^"]*\btonal\b', tag), tag
    # The reason shows under the button, so the button never moves under
    # the thumb when a reason appears or clears (one to three lines,
    # depending on the language).
    assert reveal.index('id="new-folder-create"') < reveal.index('id="new-folder-note"')


def test_the_path_field_reads_left_to_right_in_every_language():
    """A path is left to right: in a Persian or Arabic page the field kept
    the page's direction and the typed name sat to the LEFT of its parent
    (the screenshot read "demo …/aipager-users/x/")."""
    reveal = _reveal_markup()
    assert re.search(r'<div class="prefix-field" dir="ltr">', reveal), reveal


def test_the_reason_is_announced_from_the_first_one():
    """The note is read out as it changes (aria-live). Hidden with
    display:none while empty, it is out of the tree and a screen reader
    can miss the first reason; the live note stays in the tree, empty."""
    from aipager.miniapp.static import INDEX_HTML
    from aipager.miniapp.static._styles import CSS
    assert re.search(r'<div id="new-folder-note" class="reveal-note is-live" aria-live="polite">',
                     _reveal_markup())
    assert re.search(r"\.reveal-note\.is-live:empty\s*\{\s*display:\s*block;", CSS)
    # Every state the page gives the note keeps it live.
    states = re.findall(r'folderNote\.className = "([^"]+)"', INDEX_HTML)
    assert states and all("is-live" in c.split() for c in states), states


def test_the_page_mirrors_the_servers_folder_name_rule():
    """Kept in sync on purpose: the page refuses before a request, the
    server is the gate, and the two must never disagree."""
    from aipager.miniapp import launch
    from aipager.miniapp.static import INDEX_HTML
    rx = re.search(r"var FOLDER_NAME_RE = /(.+?)/;", INDEX_HTML)
    mx = re.search(r"var FOLDER_NAME_MAX = (\d+);", INDEX_HTML)
    assert rx and mx, "the page's folder-name rule is missing"
    assert rx.group(1) == launch._VALID_DIR_NAME.pattern
    assert int(mx.group(1)) == launch.MAX_DIR_NAME_LENGTH


def test_the_page_trims_a_name_as_the_server_does():
    """The server strips with Python's str.strip(); JavaScript's trim()
    strips a different set (it keeps \\x1c-\\x1f and \\x85, drops
    \\ufeff), so the page carries Python's set and must match it."""
    from aipager.miniapp.static import INDEX_HTML
    m = re.search(r'var PY_SPACE = "(.+?)" \+\s*"(.+?)";', INDEX_HTML, re.S)
    assert m, "the page's whitespace class is missing"
    cls = re.compile("[" + m.group(1) + m.group(2) + "]")
    page = {c for c in range(0x10000) if cls.match(chr(c))}
    python = {c for c in range(0x10000) if chr(c).isspace()}
    assert page == python, sorted(page ^ python)


def test_create_folder_runs_once_and_handles_every_answer(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML
    proc = _drive(node_bin, tmp_path, INDEX_HTML, NAMES)
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert "ok: create folder is a text button, one request at a time" in proc.stdout


@pytest.mark.parametrize("old, new, what", [
    ("if (newState.folderBusy || !folder || !newState.cwd || folderNameError(folder) ||",
     "if (!folder || !newState.cwd || folderNameError(folder) ||",
     "the single-flight guard"),
    ("return res.json().then(function (data) { return data; }, function () { return null; })",
     "return res.json().then(function (data) { return data; })",
     "the non-JSON answer"),
    ("(r.status === 429 && t(\"ui.new.folder_too_many\")) ||", "",
     "the rate-limit reason"),
    ("!canCreate || newState.folderBusy;", "!canCreate;",
     "the busy disabled state"),
    ('folderBtn.textContent = t(newState.folderBusy ? "ui.new.creating" : "ui.new.create_folder");',
     'folderBtn.textContent = t("ui.new.create_folder");',
     "the busy label"),
    ("if (!FOLDER_NAME_RE.test(trimmed)) { return t(\"ui.new.folder_pattern\"); }", "",
     "the client name check"),
    ("if (r.status !== 200 || !r.data || !r.data.path) {",
     "if (r.status !== 200 || !r.data) {",
     "the OK-with-no-path check"),
    ('folderBtn.setAttribute("aria-busy", newState.folderBusy ? "true" : "false");', "",
     "the busy marker for screen readers"),
    ("var trimmed = folderNameTrim(value);", "var trimmed = (value || \"\").trim();",
     "Python's trim"),
    ("folderNameError(folder) ||\n        !(newOptions && newOptions.can_create)) {",
     "folderNameError(folder)) {",
     "Enter's can-create gate"),
    ('    } else if (!canCreate) {\n      // The disabled button\'s reason, the one the server would give.\n'
     '      folderNote.textContent = t("ui.new.folder_forbidden");\n'
     '      folderNote.className = "reveal-note is-live";\n', "    ",
     "the forbidden button's reason"),
    ('document.getElementById("new-folder-name").readOnly = newState.folderBusy;', "",
     "the read-only name while busy"),
    ("if (folderNameTrim(nameEl.value) === folder) { nameEl.value = \"\"; }",
     "nameEl.value = \"\";",
     "keeping a name typed meanwhile"),
    ("if (folderNameTrim(nameEl.value) === folder) { nameEl.value = \"\"; }", "",
     "clearing the sent name"),
    ("    // Create folder's label follows the request, not the markup.\n    paintFolderBusy();\n",
     "", "the busy label across a language switch"),
    ("if (Array.from(trimmed).length > FOLDER_NAME_MAX) {",
     "if (trimmed.length > FOLDER_NAME_MAX) {",
     "counting code points"),
], ids=["single-flight", "not-json", "rate-limit", "busy-disabled", "busy-label", "name-check",
        "ok-without-path", "aria-busy", "python-trim", "enter-can-create", "forbidden-reason",
        "read-only", "keep-typed", "clear-sent", "language-switch",
        "code-points"])
def test_the_harness_catches_a_broken_create_folder(node_bin, tmp_path, old, new, what):
    """Guard the guards: each piece of the robust flow, removed, fails the
    scenario instead of passing it."""
    from aipager.miniapp.static import INDEX_HTML
    broken = INDEX_HTML.replace(old, new, 1)
    assert broken != INDEX_HTML, f"{what}: code not found"
    proc = _drive(node_bin, tmp_path, broken, NAMES)
    assert proc.returncode != 0, f"the harness passed a page without {what}"
