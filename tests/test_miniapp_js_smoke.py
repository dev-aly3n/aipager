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


def _drive_form(node_bin, tmp_path, html, name="page.html", scenario=None):
    """Drive the form harness. `scenario` picks which shape of scope the
    server is pretending to be — see SCENARIOS in the harness."""
    page = tmp_path / name
    page.write_text(html, encoding="utf-8")
    return subprocess.run(
        [node_bin, str(FORM_HARNESS), str(page)] + ([scenario] if scenario else []),
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
    assert "ok: reveal -> Enter -> POST /api/directories -> POST /api/sessions -> PUT" \
        in proc.stdout, proc.stdout


def test_a_scope_with_no_directories_still_works(node_bin, tmp_path):
    """A fresh install has no allowed roots at all. The picker must fall
    back to the lone "Default" option and post `cwd: ""` — the behaviour
    that existed before there was a picker — and must NOT offer New
    folder, which would have nowhere to create.
    """
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_form(node_bin, tmp_path, INDEX_HTML, "empty.html", scenario="empty")
    assert proc.returncode == 0, (
        f"empty-directory scope failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert 'ok: no directories -> one Default row -> cwd ""' in proc.stdout, proc.stdout


def test_new_folder_on_the_default_row_says_why_it_cannot_run(node_bin, tmp_path):
    """"Default" is a selection with no path behind it, so it cannot be a
    parent. Opening New folder on it must state that rather than present
    an empty field and a dead button — the same rule the model reveal
    follows, applied to the one place it was missing.
    """
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_form(
        node_bin, tmp_path, INDEX_HTML, "noparent.html", scenario="noparent",
    )
    assert proc.returncode == 0, (
        f"no-parent scope failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: no parent -> create-folder disabled WITH a stated reason" \
        in proc.stdout, proc.stdout


def test_the_harness_detects_a_dead_create_folder_button(node_bin, tmp_path):
    """Guard the guard for the case above: drop the explanation and the
    no-parent scenario must fail rather than pass on an empty note."""
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace(
        'folderNote.textContent = "Pick a working directory above first.";',
        'folderNote.textContent = "";', 1,
    )
    assert broken != INDEX_HTML, "folder-note code not found"

    proc = _drive_form(
        node_bin, tmp_path, broken, "broken-note.html", scenario="noparent",
    )
    assert proc.returncode != 0, (
        "harness passed a page whose create-folder button is dead and silent"
    )


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
        "      newState.cwd = path;\n      newState.folderOpen = false;",
        "      newState.folderOpen = false;", 1,
    )
    assert broken != INDEX_HTML, "folder-selection code not found"

    proc = _drive_form(node_bin, tmp_path, broken, "broken-folder.html")
    assert proc.returncode != 0, (
        "harness passed a page that creates a folder and then ignores it"
    )


# ===== session detail-page write actions (Stop/Kill/Resume/Delete) ========
#
# Same harness (miniapp_smoke.js), driven with a scenario argument this
# time — the settings-panel test above still passes none, exercising the
# default flow unchanged.

def _drive_controls(node_bin, tmp_path, html, scenario, name="controls.html"):
    page = tmp_path / name
    page.write_text(html, encoding="utf-8")
    return subprocess.run(
        [node_bin, str(HARNESS), str(page), scenario],
        capture_output=True, text=True, timeout=60,
    )


def test_stop_button_sends_one_post_and_refreshes(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "stop_busy")
    assert proc.returncode == 0, (
        f"stop scenario failed when driven:\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )
    assert "ok: stop -> one POST /api/sessions/dev/stop" in proc.stdout, proc.stdout


def test_kill_button_requires_two_taps(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "kill_idle")
    assert proc.returncode == 0, (
        f"kill scenario failed when driven:\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )
    assert "ok: kill requires two taps -> POST /api/sessions/dev/kill -> grid" \
        in proc.stdout, proc.stdout


def test_resume_button_sends_one_post_when_transcript_present(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "resume_gone")
    assert proc.returncode == 0, (
        f"resume scenario failed when driven:\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )
    assert "ok: resume -> one POST /api/sessions/dev/resume" in proc.stdout, proc.stdout


def test_resume_button_is_inert_with_reason_when_no_transcript(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(
        node_bin, tmp_path, INDEX_HTML, "resume_gone_no_transcript",
    )
    assert proc.returncode == 0, (
        f"resume-no-transcript scenario failed when driven:\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )
    assert "ok: resume inert with reason" in proc.stdout, proc.stdout


def test_delete_button_requires_two_taps_and_returns_to_grid(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "delete_gone")
    assert proc.returncode == 0, (
        f"delete scenario failed when driven:\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )
    assert "ok: delete requires two taps -> DELETE /api/sessions/dev -> grid" \
        in proc.stdout, proc.stdout


def test_the_harness_detects_a_kill_that_skips_confirmation(node_bin, tmp_path):
    """Guard the guard: neuter the arm check so a session tap runs the
    "already armed" branch immediately, skipping the confirm step
    entirely. The kill scenario's own zero-fetch-on-first-tap assertion
    must catch this."""
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace("if (!isArmed) {", "if (false) {", 1)
    assert broken != INDEX_HTML, "arm-check code not found — page changed shape"

    proc = _drive_controls(
        node_bin, tmp_path, broken, "kill_idle", name="broken-kill.html",
    )
    assert proc.returncode != 0, (
        "harness passed a page whose Kill button skips the confirm step"
    )


def test_the_harness_detects_a_dead_resume_button_with_no_reason(node_bin, tmp_path):
    """Guard the guard: blank out the disabled-reason text, and a Resume
    button with no transcript renders inert but silent."""
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace(
        "note.textContent = spec.reason;", 'note.textContent = "";', 1,
    )
    assert broken != INDEX_HTML, "reason-rendering code not found — page changed shape"

    proc = _drive_controls(
        node_bin, tmp_path, broken, "resume_gone_no_transcript",
        name="broken-resume.html",
    )
    assert proc.returncode != 0, (
        "harness passed a page whose disabled Resume button shows no reason"
    )


def test_the_form_harness_detects_a_rebuild_on_every_keystroke(node_bin, tmp_path):
    """Guard the guard, and the regression test for the defect itself.

    Wiring the text inputs to the structural render is what the form used
    to do: every character destroyed and rebuilt every option group in
    `#view-new`, which is visible flicker on a phone and throws away the
    open/closed state the operator is looking at. Putting that back must
    fail the harness.
    """
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace(
        'document.getElementById("new-model-name").addEventListener("input", refreshNewForm);',
        'document.getElementById("new-model-name").addEventListener("input", renderNewForm);',
        1,
    )
    assert broken != INDEX_HTML, "model input listener not found"

    proc = _drive_form(node_bin, tmp_path, broken, "broken-rerender.html")
    assert proc.returncode != 0, (
        "harness passed a page that rebuilds every group on every keystroke"
    )


def test_the_form_harness_detects_a_reveal_left_outside_its_group(node_bin, tmp_path):
    """Guard the guard: the whole point of a conditional reveal is that
    the input and the option that revealed it read as one thing. An input
    rendered somewhere else on the page is the layout this replaced."""
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace(
        "        if (opts.reveal && opts.reveal.after === o.value) {\n"
        "          list.appendChild(opts.reveal.node);\n"
        "        }", "", 1,
    )
    assert broken != INDEX_HTML, "reveal placement code not found"

    proc = _drive_form(node_bin, tmp_path, broken, "broken-reveal.html")
    assert proc.returncode != 0, (
        "harness passed a page that never moves a reveal into its group"
    )


def test_the_form_harness_detects_a_stale_collapsed_model_header(node_bin, tmp_path):
    """Guard the guard: the collapsed Model header must show what was
    typed. Rendering the option's own label leaves it reading `Other
    model` forever — it names the row instead of the answer."""
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace(
        "if (modelValueNode) { modelValueNode.textContent = modelValueText(); }", "", 1,
    )
    assert broken != INDEX_HTML, "header-refresh code not found"

    proc = _drive_form(node_bin, tmp_path, broken, "broken-header.html")
    assert proc.returncode != 0, (
        "harness passed a page whose collapsed header ignores what was typed"
    )


def test_the_form_harness_detects_the_duplicated_default_directory(node_bin, tmp_path):
    """Guard the guard: the daemon's own directory is in `directories`
    under its real path, so offering a separate `Default` row as well
    lists one directory twice — which is what the operator saw."""
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace("if (!dirs.length || !defaultDir) {", "if (true) {", 1)
    assert broken != INDEX_HTML, "default-directory branch not found"

    proc = _drive_form(node_bin, tmp_path, broken, "broken-dupe.html")
    assert proc.returncode != 0, (
        "harness passed a page that lists the daemon's directory twice"
    )
