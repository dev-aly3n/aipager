"""Execute the Mini App's JavaScript and drive a real interaction.

Every other test in this suite inspects the page as *text*. That has now
missed four separate runtime defects in a row, each of which looked
perfectly correct when read:

  * `pollTick` treating a third view as a session detail, bouncing the
    operator out of the New session form
  * `hidden` being silently overridden by an author `display:` rule
  * `saveSessionPreference` called with transposed arguments
  * `sessionSaveSeq` deleted by a refactor, so every tap on a per-session
    setting threw `ReferenceError` on the handler's first line and did
    nothing at all

The last one is the reason this file exists. A static check cannot catch a
missing declaration; running the code can, in about 40ms. node is present
on this machine and in CI images generally, and the test skips cleanly
where it is not — a skipped test is honest, a green text-inspection test
that cannot see the bug is not.

The harness (`tests/js/miniapp_smoke.js`) is a ~120-line DOM shim: enough
`createElement`/`appendChild`/`addEventListener`/`click` to render the
settings groups and tap one. It stubs `fetch` and records the requests, so
the assertion is "tapping an option issues the right PUT", which is
precisely what the operator reported as not working.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).parent / "js" / "miniapp_smoke.js"
FORM_HARNESS = Path(__file__).parent / "js" / "miniapp_newsession.js"


@pytest.fixture(scope="module")
def node_bin():
    exe = shutil.which("node") or shutil.which("nodejs")
    if not exe:
        pytest.skip("node not available — JS smoke test skipped")
    return exe


def test_tapping_a_per_session_setting_issues_the_write(node_bin, tmp_path):
    """Render the real page, expand a settings group, tap an option, and
    assert a PUT to that session's own preference route goes out.

    Fails on: a missing/renamed variable, a handler that throws, a
    transposed argument, a group that will not expand, a control disabled
    despite `can_edit`, or a wrong URL.
    """
    from aipager.miniapp.static import INDEX_HTML

    page = tmp_path / "page.html"
    page.write_text(INDEX_HTML, encoding="utf-8")

    proc = subprocess.run(
        [node_bin, str(HARNESS), str(page)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, (
        "the page's own JavaScript failed when driven:\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: expand -> tap -> PUT" in proc.stdout, proc.stdout


def test_the_harness_actually_detects_a_broken_page(node_bin, tmp_path):
    """Guard the guard. If the shim silently stopped exercising the page,
    the test above would pass on anything — the failure mode this whole
    file exists to escape.

    Reintroduces the exact shipped bug (delete the `sessionSaveSeq`
    declaration) and asserts the harness rejects that page.
    """
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace("var sessionSaveSeq = Object.create(null);", "", 1)
    assert broken != INDEX_HTML, "declaration not found — page changed shape"

    page = tmp_path / "broken.html"
    page.write_text(broken, encoding="utf-8")

    proc = subprocess.run(
        [node_bin, str(HARNESS), str(page)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode != 0, (
        "harness passed a page whose save handler throws on every tap — "
        "it is no longer exercising the interaction"
    )


def _drive_form(node_bin, tmp_path, html, name="page.html"):
    page = tmp_path / name
    page.write_text(html, encoding="utf-8")
    return subprocess.run(
        [node_bin, str(FORM_HARNESS), str(page)],
        capture_output=True, text=True, timeout=60,
    )


def test_new_session_form_applies_every_setting_it_offers(node_bin, tmp_path):
    """Drive the real form end to end: render controls for model, working
    directory, permission mode and the four reply-style settings; type a
    full model name; create a folder and have it selected; then submit and
    check what each request actually carried.

    The reply-style settings are the only part of the form applied by the
    CLIENT after creation (via the per-session preferences route) rather
    than passed to the launch, so nothing on the server side would notice
    if that step silently stopped happening. The typed model and the
    created folder have the same property in reverse — the server sees
    only what the form chose to send.
    """
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_form(node_bin, tmp_path, INDEX_HTML)
    assert proc.returncode == 0, (
        f"new-session form failed when driven:\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )
    assert "ok: form -> POST /api/directories -> POST /api/sessions -> PUT" \
        in proc.stdout, proc.stdout


def test_the_form_harness_detects_a_dropped_preference_step(node_bin, tmp_path):
    """Guard the guard: if the client stopped applying the chosen
    reply-style settings, the harness must say so rather than pass."""
    from aipager.miniapp.static import INDEX_HTML

    # neuter the post-create preference writes
    broken = INDEX_HTML.replace(
        "var writes = Object.keys(newState.prefs).map(function (field) {",
        "var writes = [].map(function (field) {", 1,
    )
    assert broken != INDEX_HTML, "preference-application code not found"

    proc = _drive_form(node_bin, tmp_path, broken, "broken.html")
    assert proc.returncode != 0, (
        "harness passed a page that silently drops the chosen settings"
    )


def test_the_form_harness_detects_a_dropped_typed_model(node_bin, tmp_path):
    """Guard the guard: send the picked *option* instead of the resolved
    choice, and a typed full model name silently becomes the sentinel.

    This is the shape of the bug the form is most exposed to — the value
    on screen and the value posted are computed in two different places.
    """
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace("model: chosenModel(),", "model: newState.model,", 1)
    assert broken != INDEX_HTML, "model-resolution code not found"

    proc = _drive_form(node_bin, tmp_path, broken, "broken-model.html")
    assert proc.returncode != 0, (
        "harness passed a page that posts the sentinel instead of the typed model"
    )


def test_the_form_harness_detects_a_folder_that_is_not_selected(node_bin, tmp_path):
    """Guard the guard: creating the folder but not selecting it would
    launch the session in whatever was picked before — silently, since the
    folder really was created and the notice really did appear."""
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace(
        "      newState.cwd = path;\n      nameEl.value = \"\";", "      nameEl.value = \"\";", 1,
    )
    assert broken != INDEX_HTML, "folder-selection code not found"

    proc = _drive_form(node_bin, tmp_path, broken, "broken-folder.html")
    assert proc.returncode != 0, (
        "harness passed a page that creates a folder and then ignores it"
    )
