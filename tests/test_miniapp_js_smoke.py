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
    assert "ok: stop -> menu -> one POST /api/sessions/dev/stop" in proc.stdout, proc.stdout


def test_kill_requires_confirming_a_modal(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "kill_idle")
    assert proc.returncode == 0, (
        f"kill scenario failed when driven:\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )
    assert "ok: kill -> menu -> modal -> POST /api/sessions/dev/kill -> grid" \
        in proc.stdout, proc.stdout


def test_resume_button_sends_one_post_when_transcript_present(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "resume_gone")
    assert proc.returncode == 0, (
        f"resume scenario failed when driven:\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )
    assert "ok: resume -> menu -> one POST /api/sessions/dev/resume" in proc.stdout, proc.stdout


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


def test_delete_requires_confirming_a_modal_and_returns_to_grid(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "delete_gone")
    assert proc.returncode == 0, (
        f"delete scenario failed when driven:\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )
    assert "ok: delete -> menu -> modal -> DELETE /api/sessions/dev -> grid" \
        in proc.stdout, proc.stdout


def test_the_harness_detects_a_kill_that_skips_confirmation(node_bin, tmp_path):
    """Guard the guard: make every action act straight from the menu, so
    Kill never raises its confirm modal. The kill scenario's own
    "issued a request before any confirmation" assertion must catch it."""
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace(
        "    if (!CONFIRM_ACTIONS[action]) {", "    if (true) {", 1,
    )
    assert broken != INDEX_HTML, "confirm-routing code not found — page changed shape"

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
        'note.className = "menu-note";\n        note.textContent = spec.reason;',
        'note.className = "menu-note";\n        note.textContent = "";', 1,
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


def test_back_closes_the_confirm_modal_instead_of_leaving_the_page(
    node_bin, tmp_path,
):
    """Telegram's back button is registered once at startup and used to
    go straight to the grid. With a modal open that is a trapdoor, not a
    dismissal: the operator loses the page as well as the dialog. Back
    must close the top layer and leave them where they were — and must
    still navigate once nothing is open."""
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(
        node_bin, tmp_path, INDEX_HTML, "modal_back_closes", name="back.html",
    )
    assert proc.returncode == 0, (
        f"back scenario failed when driven:\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )
    assert "ok: back closes the modal and stays on the page" in proc.stdout, proc.stdout


def test_the_backdrop_cancels_without_performing_the_action(node_bin, tmp_path):
    """A stray tap outside the dialog must mean "no", never "yes"."""
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(
        node_bin, tmp_path, INDEX_HTML, "backdrop_cancels", name="backdrop.html",
    )
    assert proc.returncode == 0, (
        f"backdrop scenario failed when driven:\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )
    assert "ok: backdrop cancels, no request issued" in proc.stdout, proc.stdout


def test_the_harness_detects_back_leaving_the_page_with_a_modal_open(
    node_bin, tmp_path,
):
    """Guard the guard for the trapdoor: restore the old unconditional
    back handler and the scenario must fail rather than pass."""
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace(
        "      if (overlayCloser) { overlayCloser(); return; }\n      showGrid();",
        "      showGrid();", 1,
    )
    assert broken != INDEX_HTML, "back-handler code not found — page changed shape"

    proc = _drive_controls(
        node_bin, tmp_path, broken, "modal_back_closes", name="broken-back.html",
    )
    assert proc.returncode != 0, (
        "harness passed a page where Back abandons the session page"
    )


def test_the_harness_detects_a_backdrop_that_confirms(node_bin, tmp_path):
    """Guard the guard: wire the backdrop to the confirm action instead
    of to cancel — a stray tap would then delete a session."""
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace(
        'document.getElementById("overlay").addEventListener("click", closeOverlay);',
        'document.getElementById("overlay").addEventListener("click", onConfirmTap);', 1,
    )
    assert broken != INDEX_HTML, "backdrop wiring not found — page changed shape"

    proc = _drive_controls(
        node_bin, tmp_path, broken, "backdrop_cancels", name="broken-backdrop.html",
    )
    assert proc.returncode != 0, (
        "harness passed a page whose backdrop performs the destructive action"
    )


def test_a_session_with_no_actions_shows_no_kebab(node_bin, tmp_path):
    """A status the daemon has never characterised yields an empty
    `actions` object. Offering the ⋮ anyway would open an empty menu —
    an affordance that promises something and delivers nothing."""
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(
        node_bin, tmp_path, INDEX_HTML, "no_actions", name="noactions.html",
    )
    assert proc.returncode == 0, (
        f"no-actions scenario failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: no actions -> no kebab, no empty menu" in proc.stdout, proc.stdout


def test_the_harness_detects_a_kebab_offered_with_no_actions(node_bin, tmp_path):
    """Guard the guard for the above."""
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace(
        "    kebab.hidden = count === 0;", "    kebab.hidden = false;", 1,
    )
    assert broken != INDEX_HTML, "kebab-visibility code not found — page changed shape"

    proc = _drive_controls(
        node_bin, tmp_path, broken, "no_actions", name="broken-kebab.html",
    )
    assert proc.returncode != 0, (
        "harness passed a page that offers a kebab with nothing behind it"
    )


def test_reset_to_defaults_asks_before_discarding_overrides(node_bin, tmp_path):
    """It clears every one of a session's own settings with no undo, and
    it used to do that on a single tap. It now goes through the same
    confirm dialog the destructive session actions use — and cancelling
    must leave the overrides untouched, not merely close the dialog."""
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(
        node_bin, tmp_path, INDEX_HTML, "reset_confirm", name="reset.html",
    )
    assert proc.returncode == 0, (
        f"reset scenario failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: reset asks, cancel is safe, confirm clears the override" \
        in proc.stdout, proc.stdout


def test_the_harness_detects_a_reset_that_skips_its_confirmation(node_bin, tmp_path):
    """Guard the guard: restore the old one-tap behaviour and the
    scenario must fail rather than pass."""
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace(
        '    openConfirm({\n      title: "Reset settings to defaults?"',
        '    resetSessionSettings(); return; openConfirm({\n'
        '      title: "Reset settings to defaults?"', 1,
    )
    assert broken != INDEX_HTML, "reset-confirm code not found — page changed shape"

    proc = _drive_controls(
        node_bin, tmp_path, broken, "reset_confirm", name="broken-reset.html",
    )
    assert proc.returncode != 0, (
        "harness passed a page where Reset to defaults fires on one tap"
    )


def test_a_menu_left_open_closes_when_the_session_changes_under_it(
    node_bin, tmp_path,
):
    """The menu is built once from the last poll. If the session changes
    underneath — someone kills it from chat, its transcript is cleaned
    up — leaving the menu up would have it offering something the server
    would now refuse, and silently redrawing it would move rows under a
    finger. It closes and says why.

    Covers both shapes of change: a different set of actions, and the
    same set with one newly unavailable.
    """
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(
        node_bin, tmp_path, INDEX_HTML, "menu_drift_closes", name="drift.html",
    )
    assert proc.returncode == 0, (
        f"drift scenario failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: a changed session closes its open menu and says so" \
        in proc.stdout, proc.stdout


def test_a_cancelled_confirm_does_not_leak_into_the_next_one(node_bin, tmp_path):
    """`confirmAction` (session actions) and `confirmRun` (Reset) are
    separate fields consumed by one handler. A stale one would make the
    dialog on screen perform the OTHER action."""
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(
        node_bin, tmp_path, INDEX_HTML, "confirm_isolation", name="isolation.html",
    )
    assert proc.returncode == 0, (
        f"isolation scenario failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: a cancelled confirm does not leak into the next one" \
        in proc.stdout, proc.stdout


def test_the_harness_detects_a_menu_that_ignores_a_changed_session(
    node_bin, tmp_path,
):
    """Guard the guard. This mechanism shipped with ZERO coverage in the
    first pass — the whole block could be deleted and all ten scenarios
    still passed."""
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace(
        "    if (overlayCloser && menuSignature &&\n"
        "        actionsSignature(data) !== menuSignature) {\n"
        "      closeOverlay();\n"
        '      showNotice("This session changed — reopen the menu.");\n'
        "    }", "", 1,
    )
    assert broken != INDEX_HTML, "drift-guard code not found — page changed shape"

    proc = _drive_controls(
        node_bin, tmp_path, broken, "menu_drift_closes", name="broken-drift.html",
    )
    assert proc.returncode != 0, (
        "harness passed a page whose open menu ignores the session changing"
    )


def test_the_harness_detects_a_change_check_blind_to_availability(
    node_bin, tmp_path,
):
    """Guard the guard, narrower: a signature built from which actions
    exist — ignoring whether each can run — misses a Resume going
    inert."""
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace(
        '        return k + ":" + (data.actions[k].available ? "1" : "0");',
        "        return k;", 1,
    )
    assert broken != INDEX_HTML, "signature code not found — page changed shape"

    proc = _drive_controls(
        node_bin, tmp_path, broken, "menu_drift_closes", name="broken-sig.html",
    )
    assert proc.returncode != 0, (
        "harness passed a page whose change check ignores availability"
    )


def test_the_page_contains_no_stray_control_characters():
    """The CSS and JS live in plain (non-raw) Python triple-quoted
    strings, so a CSS escape like `\\1F480` is eaten by Python as the
    octal escape `\\1` and ships as U+0001 followed by the literal text
    "F480" — which is exactly what the operator saw in the action menu.

    Nothing caught it: the page still parsed, every driven scenario still
    passed, and the damage was purely visual. A sweep for C0 controls is
    the cheap general guard, since any future escape written the same way
    lands here too.
    """
    from aipager.miniapp.static import INDEX_HTML

    allowed = {"\n", "\t", "\r"}
    bad = sorted({
        ch for ch in INDEX_HTML
        if (ord(ch) < 0x20 or ord(ch) == 0x7F) and ch not in allowed
    })
    assert not bad, (
        "control characters in the served page — almost certainly a CSS or "
        f"JS backslash escape eaten by Python: {[hex(ord(c)) for c in bad]}"
    )


def test_the_action_menu_icons_are_real_code_points():
    """The specific regression: each menu icon must be an actual glyph,
    not the tail of a mangled escape."""
    import re

    from aipager.miniapp.static import INDEX_HTML

    found = dict(re.findall(
        r"\.menu-item\.act-(\w+)::before \{ content: \"([^\"]*)\"; \}", INDEX_HTML,
    ))
    assert set(found) == {
        "stop", "kill", "resume", "delete",
        "clearqueue", "compact", "perms", "restart", "rename",
    }, found
    for action, glyph in found.items():
        assert glyph, f"{action} has no icon"
        assert not re.search(r"[0-9A-F]{3,}", glyph), (
            f"{action}'s icon is hex text, not a character: {glyph!r}"
        )
        assert ord(glyph[0]) > 0x2000, (
            f"{action}'s icon starts with U+{ord(glyph[0]):04X}, not a symbol"
        )


# ===== Mini App session MENU actions (perms/clearqueue/compact/restart/
#       rename) — design.md: "Mini App session menu actions" ============
#
# Same harness (miniapp_smoke.js), new scenarios. Every guard below is
# verified per the project's own burned lesson (spec.md/design.md):
# remove the production line, confirm THIS test fails with a specific
# reason, then confirm the mutation actually reproduces the defect
# (the mutated string genuinely differs, not an accidental no-op) —
# see implementation.md's guard -> mutation -> failing-test table.

def test_perms_idle_opens_confirm_and_posts(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "perms_idle")
    assert proc.returncode == 0, (
        f"perms_idle scenario failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: perms idle -> menu -> modal -> POST /api/sessions/dev/perms" \
        in proc.stdout, proc.stdout


def test_perms_busy_uses_stop_task_wording(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "perms_busy")
    assert proc.returncode == 0, (
        f"perms_busy scenario failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: perms busy -> Stop task & switch wording -> POST /api/sessions/dev/perms" \
        in proc.stdout, proc.stdout


def test_the_harness_detects_perms_busy_falling_back_to_idle_wording(node_bin, tmp_path):
    """Guard the guard: drop the busy-specific title branch so perms
    always uses the idle wording, and the busy scenario must catch it."""
    from aipager.miniapp.static import INDEX_HTML

    old = (
        '        title: busy\n'
        '          ? "Stop the current task and switch " + label + " to " + targetLabel + "?"\n'
        '          : "Switch " + label + " to " + targetLabel + " mode?",\n'
    )
    new = '        title: "Switch " + label + " to " + targetLabel + " mode?",\n'
    assert old in INDEX_HTML, "perms busy-wording branch not found — page changed shape"
    broken = INDEX_HTML.replace(old, new, 1)
    assert broken != INDEX_HTML, "mutation was a no-op"

    proc = _drive_controls(
        node_bin, tmp_path, broken, "perms_busy", name="broken-perms-busy.html",
    )
    assert proc.returncode != 0, (
        "harness passed a page where busy perms uses the idle wording"
    )


def test_perms_auto_requires_admin_is_inert_with_reason(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(
        node_bin, tmp_path, INDEX_HTML, "perms_auto_requires_admin",
    )
    assert proc.returncode == 0, (
        f"perms_auto_requires_admin scenario failed:\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: perms auto requires admin -> disabled with reason, no fetch" \
        in proc.stdout, proc.stdout


def test_restart_idle_opens_confirm_and_posts(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "restart_idle")
    assert proc.returncode == 0, (
        f"restart_idle scenario failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: restart idle -> menu -> modal -> POST /api/sessions/dev/restart" \
        in proc.stdout, proc.stdout


def test_restart_busy_opens_confirm_and_posts(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "restart_busy")
    assert proc.returncode == 0, (
        f"restart_busy scenario failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: restart busy -> menu -> modal -> POST /api/sessions/dev/restart" \
        in proc.stdout, proc.stdout


def test_clearqueue_busy_acts_without_confirmation(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "clearqueue_busy")
    assert proc.returncode == 0, (
        f"clearqueue_busy scenario failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: clearqueue -> menu -> one POST /api/sessions/dev/clearqueue" \
        in proc.stdout, proc.stdout


def test_the_harness_detects_clearqueue_gaining_a_confirmation(node_bin, tmp_path):
    """Guard the guard: Clear queue is recoverable and must NOT gain a
    confirm step — if CONFIRM_ACTIONS ever grows to include it, the
    scenario's own single-POST assertion must catch it."""
    from aipager.miniapp.static import INDEX_HTML

    old = "var CONFIRM_ACTIONS = { kill: true, delete: true, perms: true, restart: true };"
    new = ("var CONFIRM_ACTIONS = { kill: true, delete: true, perms: true, "
           "restart: true, clearqueue: true };")
    assert old in INDEX_HTML, "CONFIRM_ACTIONS declaration not found — page changed shape"
    broken = INDEX_HTML.replace(old, new, 1)
    assert broken != INDEX_HTML, "mutation was a no-op"

    proc = _drive_controls(
        node_bin, tmp_path, broken, "clearqueue_busy", name="broken-clearqueue.html",
    )
    assert proc.returncode != 0, (
        "harness passed a page where Clear queue now asks for confirmation"
    )


def test_compact_busy_queues_acts_without_confirmation(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "compact_busy_queues")
    assert proc.returncode == 0, (
        f"compact_busy_queues scenario failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: compact busy -> menu -> one POST /api/sessions/dev/compact (queues)" \
        in proc.stdout, proc.stdout


def test_compact_idle_sends_acts_without_confirmation(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "compact_idle_sends")
    assert proc.returncode == 0, (
        f"compact_idle_sends scenario failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: compact idle -> menu -> one POST /api/sessions/dev/compact (sends)" \
        in proc.stdout, proc.stdout


def test_compact_queue_full_is_inert_with_reason(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "compact_queue_full")
    assert proc.returncode == 0, (
        f"compact_queue_full scenario failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: compact queue full -> disabled with reason, no fetch" in proc.stdout, proc.stdout


def test_rename_valid_prefills_and_posts_the_new_label(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "rename_valid")
    assert proc.returncode == 0, (
        f"rename_valid scenario failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: rename valid -> menu -> modal -> POST /api/sessions/dev/rename" \
        in proc.stdout, proc.stdout


def test_the_harness_detects_a_rename_field_that_is_not_prefilled(node_bin, tmp_path):
    """Guard the guard: drop the pre-fill so the rename field opens
    empty instead of showing the current label."""
    from aipager.miniapp.static import INDEX_HTML

    old = "    input.value = label;\n"
    new = '    input.value = "";\n'
    assert old in INDEX_HTML, "rename pre-fill line not found — page changed shape"
    broken = INDEX_HTML.replace(old, new, 1)
    assert broken != INDEX_HTML, "mutation was a no-op"

    proc = _drive_controls(
        node_bin, tmp_path, broken, "rename_valid", name="broken-rename-prefill.html",
    )
    assert proc.returncode != 0, (
        "harness passed a page whose rename field opens without the current label"
    )


def test_rename_client_side_invalid_disables_save_and_sends_nothing(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "rename_client_side_invalid")
    assert proc.returncode == 0, (
        f"rename_client_side_invalid scenario failed:\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: rename client-side invalid -> Save disabled, no fetch" \
        in proc.stdout, proc.stdout


def test_the_harness_detects_a_disabled_save_that_still_submits(node_bin, tmp_path):
    """Guard the guard, and the regression test for the shape of bug
    this project has shipped before: a disabled Confirm/Save button
    that a test harness (which does not enforce HTML `disabled`
    semantics the way a real browser does) can still click straight
    through to a submit."""
    from aipager.miniapp.static import INDEX_HTML

    old = '    if (document.getElementById("confirm-ok").disabled) { return; }\n'
    assert old in INDEX_HTML, "onConfirmTap's disabled guard not found — page changed shape"
    broken = INDEX_HTML.replace(old, "", 1)
    assert broken != INDEX_HTML, "mutation was a no-op"

    proc = _drive_controls(
        node_bin, tmp_path, broken, "rename_client_side_invalid",
        name="broken-confirm-guard.html",
    )
    assert proc.returncode != 0, (
        "harness passed a page whose disabled Save button still submits"
    )


def test_rename_server_conflict_shows_the_detail_verbatim(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "rename_server_conflict")
    assert proc.returncode == 0, (
        f"rename_server_conflict scenario failed:\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: rename server conflict -> notice shows the server detail verbatim" \
        in proc.stdout, proc.stdout


def test_the_harness_detects_a_dropped_server_detail_on_rename_failure(node_bin, tmp_path):
    """Guard the guard: a rename failure that shows a generic message
    instead of the server's own `detail` would silently hide WHY the
    rename was refused (which label is already taken, etc.)."""
    from aipager.miniapp.static import INDEX_HTML

    old = ('      showNotice((r.data && r.data.detail) || "Couldn\'t rename.", '
           '"err");\n')
    new = '      showNotice("Couldn\'t rename.", "err");\n'
    assert old in INDEX_HTML, "rename failure-notice line not found — page changed shape"
    broken = INDEX_HTML.replace(old, new, 1)
    assert broken != INDEX_HTML, "mutation was a no-op"

    proc = _drive_controls(
        node_bin, tmp_path, broken, "rename_server_conflict",
        name="broken-rename-detail.html",
    )
    assert proc.returncode != 0, (
        "harness passed a page that drops the server's rename-failure detail"
    )


def test_menu_grouping_divider_sits_between_the_two_groups(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "menu_grouping_divider")
    assert proc.returncode == 0, (
        f"menu_grouping_divider scenario failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: divider sits between the last control item and the first destructive one" \
        in proc.stdout, proc.stdout


def test_the_harness_detects_a_missing_menu_divider(node_bin, tmp_path):
    """Guard the guard. This mechanism ships with ZERO coverage the
    moment its own scenario is removed — pinning it the same way the
    pre-existing menu-drift guard was pinned after shipping uncovered."""
    from aipager.miniapp.static import INDEX_HTML

    old = (
        '      if (CONFIRM_ACTIONS[key] && !dividerInserted && controlRendered > 0) {\n'
        '        var divider = document.createElement("div");\n'
        '        divider.className = "menu-divider";\n'
        '        menu.appendChild(divider);\n'
        '        dividerInserted = true;\n'
        '      }\n'
        '      if (!CONFIRM_ACTIONS[key]) { controlRendered++; }\n'
    )
    new = '      if (!CONFIRM_ACTIONS[key]) { controlRendered++; }\n'
    assert old in INDEX_HTML, "divider-insertion code not found — page changed shape"
    broken = INDEX_HTML.replace(old, new, 1)
    assert broken != INDEX_HTML, "mutation was a no-op"

    proc = _drive_controls(
        node_bin, tmp_path, broken, "menu_grouping_divider", name="broken-divider.html",
    )
    assert proc.returncode != 0, (
        "harness passed a page with no divider between the menu groups"
    )


def test_the_notice_is_a_floating_toast_not_an_in_flow_banner():
    """Notices must not shift the page.

    As an in-flow element the notice appeared and vanished 3.5s later,
    pushing everything below it down and then yanking it back — so tapping
    Kill made the list jump under your finger just as you read the result.
    Pinned here because the fix is entirely CSS: a future edit that drops
    `position: fixed` would silently reintroduce the shift with every test
    still green.
    """
    from aipager.miniapp.static import INDEX_HTML

    start = INDEX_HTML.index("#notice {")
    block = INDEX_HTML[start:INDEX_HTML.index("}", start)]
    assert "position: fixed" in block, "notice is back in normal flow"
    assert "display: none" not in block, (
        "display toggling defeats both the transition and the fixed layout"
    )
    # The toast has to sit above the confirm dialog (60) and its backdrop
    # (50): 'Session killed.' answers an action taken inside that dialog.
    zline = [ln for ln in block.splitlines() if "z-index" in ln]
    assert zline, "notice has no z-index — it can render behind the dialog"
    assert int(zline[0].split(":")[1].strip().rstrip(";")) > 60


def test_showNotice_toggles_a_class_rather_than_inline_display():
    """The JS half of the same guarantee — setting style.display would
    override the stylesheet and put the toast back in flow."""
    from aipager.miniapp.static import INDEX_HTML

    start = INDEX_HTML.index("function showNotice(")
    # Slice to the end of the function, not a fixed byte count: a fixed
    # 900 broke the moment showNotice grew a `kind` parameter, and a test
    # that silently stops covering the line it names is worse than none.
    body = INDEX_HTML[start:INDEX_HTML.index("function apiFetch(", start)]
    assert 'classList.add("is-visible")' in body
    # Match the ASSIGNMENT, not the words: the function's own comment
    # explains why `el.style.display` is not used, and a substring check
    # trips over that explanation. (This project has done exactly this
    # before — a CSS comment warning about an escape contained the escape.)
    assert "style.display =" not in body, "showNotice sets inline display again"
    assert "style.display=" not in body


def test_the_toast_carries_an_outcome_icon_and_colour():
    """A toast should read as success or failure before the words do.

    The icon is built in the DOM rather than via CSS `content:` on purpose:
    a CSS escape in this same non-raw Python stylesheet string was once
    mangled and rendered as the literal text "F480" on screen.
    """
    from aipager.miniapp.static import INDEX_HTML

    start = INDEX_HTML.index("function showNotice(")
    body = INDEX_HTML[start:INDEX_HTML.index("function apiFetch(", start)]
    assert 'createElement("span")' in body, "icon is not a DOM node"
    assert "✓" in body and "!" in body, "no success/failure glyphs"
    # The message itself must never be interpolated as markup — server
    # `detail` strings reach this function verbatim.
    assert "innerHTML" not in body, "server detail could be treated as markup"

    for cls in ("#notice.toast-ok", "#notice.toast-err", "#notice.toast-info"):
        assert cls in INDEX_HTML, f"{cls} has no styling"


def test_every_notice_states_whether_it_worked():
    """A toast with no kind falls back to neutral, which is right for a
    genuinely neutral message but wrong for an outcome. Pin that the
    outcome-bearing call sites actually say which they are, so a new one
    added later without a kind stands out here rather than shipping as a
    grey 'i' after a failed action."""
    from aipager.miniapp.static import INDEX_HTML

    for phrase, kind in [
        ('"Saved."', "ok"),
        ('"Couldn\'t reach the server — nothing changed."', "err"),
        ('"Couldn\'t rename."', "err"),
    ]:
        idx = INDEX_HTML.index(phrase)
        tail = INDEX_HTML[idx:idx + 120]
        assert f'"{kind}"' in tail, f"{phrase} is not classified as {kind}"
