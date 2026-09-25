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

import json
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
    directory, permission mode and the reply-style settings; type a
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
        'note.className = "menu-note";\n        note.textContent = plain(spec.reason);',
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
        '      showNotice("This session changed. Reopen the menu.");\n'
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


def test_the_action_menu_icons_are_inline_svg_symbols():
    """Replaces the emoji-code-point pin (roadmap 8.44): the emoji icon
    language rendered differently per platform and is retired for inline
    line icons. Every action the menu can show has a symbol in the page's
    own sprite, and the menu builds its icon from the action key."""
    import re

    from aipager.miniapp.static import INDEX_HTML

    order = re.search(r"var ACTION_ORDER = \[([^\]]*)\]", INDEX_HTML)
    assert order, "ACTION_ORDER not found"
    keys = re.findall(r'"(\w+)"', order.group(1))
    assert len(keys) == 9, keys
    symbols = set(re.findall(r'<symbol id="i-([\w-]+)"', INDEX_HTML))
    missing = [k for k in keys if k not in symbols]
    assert not missing, f"actions with no icon: {missing}"
    assert "glyph.innerHTML = icon(key);" in INDEX_HTML
    # And no emoji icon survives as a CSS `content:` glyph.
    assert not re.search(r"\.menu-item\.act-\w+::before", INDEX_HTML)


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

    # The detail goes through plain() since roadmap 8.44 (em dashes in
    # server strings are shown as " - ").
    old = ('      showNotice(plain((r.data && r.data.detail) || "Couldn\'t rename."), '
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
        # Was "... server — nothing changed.": the page carries no em
        # dash (operator text rule, roadmap 8.44).
        ('"Couldn\'t reach the server. Nothing changed."', "err"),
        ('"Couldn\'t rename."', "err"),
    ]:
        idx = INDEX_HTML.index(phrase)
        tail = INDEX_HTML[idx:idx + 120]
        assert f'"{kind}"' in tail, f"{phrase} is not classified as {kind}"


# ===== running-session model picker (roadmap 8.35) ==========================

def test_picking_a_model_posts_it_and_shows_switching_then_the_new_model(
    node_bin, tmp_path,
):
    """The session page's Model control: tap a row, exactly one POST to
    this session's model route carrying the row's label, "switching…"
    while the request is open, the new model once the server confirms."""
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "model_switch")
    assert proc.returncode == 0, (
        f"model_switch scenario failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: model pick -> POST /api/sessions/dev/model -> switching… -> Opus 5.5" \
        in proc.stdout, proc.stdout


def test_an_unconfirmed_switch_says_so(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "model_unconfirmed")
    assert proc.returncode == 0, (
        f"model_unconfirmed scenario failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    # Was "not confirmed — check the session": no em dash in page text
    # (roadmap 8.44); the header now reads "not confirmed (check the session)".
    assert "-> not confirmed (check the session)" in proc.stdout, proc.stdout


def test_the_model_picker_is_inert_while_busy_with_the_reason(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "model_busy")
    assert proc.returncode == 0, (
        f"model_busy scenario failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "ok: model picker inert while busy, with the reason" in proc.stdout, proc.stdout


def test_the_model_harness_detects_a_missing_switching_state(node_bin, tmp_path):
    """Guard the guard: a page that never shows "switching…" must fail."""
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace('var MODEL_SWITCHING_TEXT = "switching…";',
                                'var MODEL_SWITCHING_TEXT = "";', 1)
    assert broken != INDEX_HTML, "switching text not found — page changed shape"
    proc = _drive_controls(node_bin, tmp_path, broken, "model_switch")
    assert proc.returncode != 0, "harness passed a page with no switching state"


# ----- Settings -> Updates (admin self-update, roadmap 8.36) ------------------

def _drive_smoke(node_bin, tmp_path, html, scenario):
    page = tmp_path / f"{scenario}.html"
    page.write_text(html, encoding="utf-8")
    return subprocess.run(
        [node_bin, str(HARNESS), str(page), scenario],
        capture_output=True, text=True, timeout=60,
    )


def test_updates_block_hidden_without_can_update(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_smoke(node_bin, tmp_path, INDEX_HTML, "updates_hidden")
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert "ok: no can_update -> updates block hidden" in proc.stdout


def test_updates_block_renders_one_check_button_and_looks_nothing_up(node_bin, tmp_path):
    """Roadmap 8.43 (operator decision 2026-09-25): opening Settings shows
    ONE "Check for updates" button and asks only for the running job; no
    version lookup until the button is tapped. Replaces the 8.36 test that
    pinned the three always-on buttons (Update Claude Code / Update aipager
    / Both)."""
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_smoke(node_bin, tmp_path, INDEX_HTML, "updates_render")
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert "ok: can_update -> one Check for updates button, no version lookup" in proc.stdout


def test_updates_check_then_one_update_button_posts_start(node_bin, tmp_path):
    """Tap Check, see the Checking… state, the result lines and ONE button
    for only the product that has an update, then tap it: the requests are
    exactly GET /api/update, POST check, POST start {"kind": "claude"}."""
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_smoke(node_bin, tmp_path, INDEX_HTML, "updates_check_then_update")
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert "ok: Check -> Checking… -> lines + one button -> POST start" in proc.stdout


def test_updates_check_with_an_aipager_update_shows_the_restart_note(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_smoke(node_bin, tmp_path, INDEX_HTML, "updates_check_aipager")
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert "ok: aipager newer -> one Update aipager button + restart note" in proc.stdout


def test_updates_check_with_nothing_newer_offers_no_update(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_smoke(node_bin, tmp_path, INDEX_HTML, "updates_check_nothing_newer")
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert "ok: nothing newer -> summary + Check again, no update button" in proc.stdout


def test_updates_403_hides_block_not_expired(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_smoke(node_bin, tmp_path, INDEX_HTML, "updates_forbidden")
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert "ok: 403 -> updates block hidden, app not expired" in proc.stdout


def test_updates_poll_only_while_a_job_runs(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_smoke(node_bin, tmp_path, INDEX_HTML, "updates_poll_running")
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert "ok: running job -> polls every 3 s, no check or start buttons" in proc.stdout


def test_updates_stop_polling_once_the_job_finished(node_bin, tmp_path):
    """Roadmap 8.43: a finished job brings back "Check for updates" (was:
    the three start buttons, retired by the operator's 2026-09-25 request)."""
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_smoke(node_bin, tmp_path, INDEX_HTML, "updates_no_poll_terminal")
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert "ok: finished job -> no poll, Check for updates back" in proc.stdout


def test_updates_offer_nothing_while_a_restart_is_pending(node_bin, tmp_path):
    """Review rev-iter1-001: the pending restart holds the update lock, so
    the block must not re-offer Update buttons in that window."""
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_smoke(node_bin, tmp_path, INDEX_HTML, "updates_restart_pending")
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert "ok: pending restart -> no buttons, no poll" in proc.stdout


def test_updates_start_refused_while_shutting_down_says_so(node_bin, tmp_path):
    """Review rev-iter2-002: a start during a daemon shutdown is a 503
    ``shutting_down``, and the page tells the admin why."""
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_smoke(node_bin, tmp_path, INDEX_HTML, "updates_shutting_down")
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert "ok: 503 shutting_down -> notice says aipager is shutting down" in proc.stdout


# ===== Settings tab: chat-wide preferences (roadmap 8.40) =================
#
# `savePreference` lost its `saveSeq` declaration in 6a82ec5 and every tap
# on the Settings tab threw ReferenceError before the fetch — shipped from
# 0.7.0 on. The per-session flow above never touched this handler.

def test_settings_tab_tap_sends_the_put_and_paints_optimistically(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "scope_save")
    assert proc.returncode == 0, (
        f"settings-tab save failed when driven:\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )
    assert "ok: scope tap -> PUT /api/preferences/answer_length -> optimistic -> saved" \
        in proc.stdout, proc.stdout


def test_settings_tab_failed_put_restores_the_previous_value(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_controls(node_bin, tmp_path, INDEX_HTML, "scope_save_fails")
    assert proc.returncode == 0, (
        f"settings-tab failed-save scenario failed:\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )
    assert "ok: scope tap -> PUT /api/preferences/answer_length -> optimistic -> " \
        "restored on failure" in proc.stdout, proc.stdout


@pytest.mark.parametrize("scenario", ["scope_save", "scope_save_fails"])
def test_the_harness_detects_the_missing_save_seq(node_bin, tmp_path, scenario):
    """Guard the guard: the exact shipped bug — no `saveSeq` declaration —
    must fail both Settings-tab scenarios."""
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace("var saveSeq = Object.create(null);", "", 1)
    assert broken != INDEX_HTML, "saveSeq declaration not found — page changed shape"

    proc = _drive_controls(
        node_bin, tmp_path, broken, scenario, name="broken-saveseq.html",
    )
    assert proc.returncode != 0, (
        "harness passed a Settings tab whose save handler throws on every tap"
    )


def test_the_harness_detects_a_failed_put_that_keeps_the_optimistic_value(
    node_bin, tmp_path,
):
    """Guard the guard: drop the rollback and a failed write must fail the
    scenario rather than leave a button that lies about what is stored."""
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace("settingsData.values[field] = previous;", "", 1)
    assert broken != INDEX_HTML, "rollback code not found — page changed shape"

    proc = _drive_controls(
        node_bin, tmp_path, broken, "scope_save_fails", name="broken-rollback.html",
    )
    assert proc.returncode != 0, (
        "harness passed a Settings tab that keeps an unsaved value after a failed PUT"
    )


# ===== em dashes in server strings (roadmap 8.44) ==========================

def test_the_harness_detects_a_reason_shown_with_its_em_dash(node_bin, tmp_path):
    """Guard the guard for plain(): the page shows server reasons (shared
    with the chat, where they keep their em dash) with " - " instead. Drop
    the normalisation on the menu note and the no-transcript scenario,
    which compares against plain(NO_TRANSCRIPT_REASON), must fail."""
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace(
        "        note.textContent = plain(spec.reason);",
        "        note.textContent = spec.reason;", 1,
    )
    assert broken != INDEX_HTML, "menu-note normalisation not found"

    proc = _drive_controls(
        node_bin, tmp_path, broken, "resume_gone_no_transcript",
        name="broken-plain.html",
    )
    assert proc.returncode != 0, (
        "harness passed a page that shows a server reason with its em dash"
    )


def test_the_harness_detects_a_model_reason_shown_with_its_em_dash(node_bin, tmp_path):
    """Same guard for the model control's disabled reason."""
    from aipager.miniapp.static import INDEX_HTML

    broken = INDEX_HTML.replace(
        'var reason = state.available ? "" : plain(state.reason || "");',
        'var reason = state.available ? "" : (state.reason || "");', 1,
    )
    assert broken != INDEX_HTML, "model-note normalisation not found"

    proc = _drive_controls(
        node_bin, tmp_path, broken, "model_busy", name="broken-plain-model.html",
    )
    assert proc.returncode != 0, (
        "harness passed a page that shows the model reason with its em dash"
    )


# ===== the lantern grid and Answer in chat (roadmap 8.44) ==================

@pytest.mark.parametrize("scenario, expected", [
    ("grid_render", "ok: grid -> 2 beacons, 3 lanterns, 3 on the shelf, pulse sentence"),
    ("grid_keyed", "ok: a changed poll reuses the tile and moves its ring"),
    ("grid_unchanged", "ok: an unchanged poll touches nothing"),
    ("grid_reorder", "ok: reorder moves the same tiles, no FLIP without its APIs"),
    ("grid_reorder_flip", "ok: reorder animates with one requestAnimationFrame"),
    ("grid_empty", "ok: empty grid -> empty state with one New session button"),
    ("grid_expired", "ok: expired -> no New session button"),
    ("answer_single", "ok: answer -> POST /api/sessions/alpha/answer -> close"),
    ("answer_multi", "ok: answer with two waiting -> stays and says Sent to the chat"),
    ("answer_double", "ok: a double tap sends one request"),
    ("answer_409", "ok: a refused answer shows the server's reason, plain"),
    ("answer_403", "ok: 403 -> can't answer here, app not expired"),
    ("answer_viewer", "ok: viewer -> Answer in chat disabled with a reason"),
    ("detail_answer",
     "ok: session page -> Answer in chat -> POST /api/sessions/alpha/answer"),
])
def test_lantern_grid_and_answer_scenarios(node_bin, tmp_path, scenario, expected):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_smoke(node_bin, tmp_path, INDEX_HTML, scenario)
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert expected in proc.stdout, proc.stdout


# Guard the guard for each mechanism above: break it in the page and the
# scenario that names it must fail.
_GRID_MUTANTS = [
    pytest.param(
        "      if (item) { update(item, s); } else { item = map[s.label] = build(s); }",
        "      item = map[s.label] = build(s);",
        "grid_keyed", id="keyed-reuse"),
    pytest.param(
        "  function canFlip(sample) {",
        "  function canFlip(sample) { return true;",
        "grid_reorder", id="flip-feature-check"),
    pytest.param(
        '    if (sig === gridSig) { return; }     // nothing on screen would change',
        "",
        "grid_unchanged", id="unchanged-poll-gate"),
    pytest.param(
        '        if (others <= 1 && tg && typeof tg.close === "function") {',
        '        if (tg && typeof tg.close === "function") {',
        "answer_multi", id="close-only-when-alone"),
    pytest.param(
        '    postSessionAction(label, "answer", "POST").then(function (r) {',
        '    apiFetch("/api/sessions/" + encodeURIComponent(label) + "/answer")'
        '.then(function (r) {',
        "answer_403", id="answer-not-through-apiFetch"),
    pytest.param(
        "    if (answering[label]) { return; }     // one request per tap, never two\n",
        "",
        "answer_double", id="in-flight-guard"),
    pytest.param(
        "    b.answer.disabled = !gridCanAct || !!answering[s.label];",
        "    b.answer.disabled = false;",
        "answer_viewer", id="viewer-disabled"),
    pytest.param(
        "    setText(b.summary, plain(s.waiting_summary) ||",
        "    setText(b.summary, s.waiting_summary ||",
        "grid_render", id="tray-summary-plain"),
    pytest.param(
        "      if (s.status === \"waiting\") { waiting.push(s); }",
        "      if (false) { waiting.push(s); }",
        "grid_render", id="waiting-to-the-tray"),
    pytest.param(
        '    unreachable = state === "offline" || state === "expired";',
        '    unreachable = false;',
        "grid_expired", id="no-new-session-while-expired"),
    pytest.param(
        "    document.getElementById(\"new-session-btn\").hidden = unreachable || gridEmpty;",
        "    document.getElementById(\"new-session-btn\").hidden = unreachable;",
        "grid_empty", id="one-new-session-when-empty"),
]


@pytest.mark.parametrize("old, new, scenario", _GRID_MUTANTS)
def test_the_grid_harness_detects_a_broken_mechanism(node_bin, tmp_path, old, new, scenario):
    from aipager.miniapp.static import INDEX_HTML

    assert INDEX_HTML.count(old) == 1, f"mutation site not found once: {old!r}"
    broken = INDEX_HTML.replace(old, new, 1)
    proc = _drive_smoke(node_bin, tmp_path, broken, scenario)
    assert proc.returncode != 0, (
        f"harness passed a page with {old!r} broken\nstdout: {proc.stdout}"
    )


def test_the_harness_rejects_a_fetch_outside_the_api(node_bin, tmp_path):
    """The page may only talk to /api/ on its own origin. Point one fetch
    somewhere else and even an unrelated, passing scenario must fail."""
    from aipager.miniapp.static import INDEX_HTML

    old = '      apiFetch("/api/sessions")\n'
    assert INDEX_HTML.count(old) == 1
    broken = INDEX_HTML.replace(old, '      apiFetch("/elsewhere/sessions")\n', 1)
    proc = _drive_smoke(node_bin, tmp_path, broken, "settings")
    assert proc.returncode != 0, proc.stdout
    assert "outside /api/" in proc.stderr


# ===== the session page (roadmap 8.44) ====================================

@pytest.mark.parametrize("scenario, expected", [
    ("detail_activity", "ok: session page -> ring, state line, 4 beads, running last"),
    ("detail_quick_stop", "ok: quick stop -> one POST /api/sessions/dev/stop"),
    ("detail_quick_resume", "ok: quick resume -> one POST /api/sessions/dev/resume"),
    ("detail_unchanged", "ok: an unchanged detail poll touches nothing"),
])
def test_session_page_scenarios(node_bin, tmp_path, scenario, expected):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_smoke(node_bin, tmp_path, INDEX_HTML, scenario)
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert expected in proc.stdout, proc.stdout


_DETAIL_MUTANTS = [
    pytest.param(
        '    var tools = (data.timeline || []).filter(function (r) { return r.kind === "tool"; });',
        "    var tools = data.timeline || [];",
        "detail_activity", id="beads-are-tools-only"),
    pytest.param(
        "    var recent = tools.slice(-8);",
        "    var recent = tools.slice(0, 2);",
        "detail_activity", id="beads-are-the-latest"),
    pytest.param(
        "      prev.innerHTML = escapeHtml(data.last_message)",
        "      prev.innerHTML = String(data.last_message)",
        "detail_activity", id="reply-escaped"),
    pytest.param(
        '    var key = (a.stop && a.stop.available) ? "stop"',
        '    var key = (a.stop) ? "kill"',
        "detail_quick_stop", id="quick-is-stop"),
    pytest.param(
        '    if (detailChanged("facts", data.facts)) { renderFacts(data.facts || []); }',
        "    renderFacts(data.facts || []);",
        "detail_unchanged", id="detail-section-gate"),
]


@pytest.mark.parametrize("old, new, scenario", _DETAIL_MUTANTS)
def test_the_session_page_harness_detects_a_broken_mechanism(
        node_bin, tmp_path, old, new, scenario):
    from aipager.miniapp.static import INDEX_HTML

    assert INDEX_HTML.count(old) == 1, f"mutation site not found once: {old!r}"
    broken = INDEX_HTML.replace(old, new, 1)
    proc = _drive_smoke(node_bin, tmp_path, broken, scenario)
    assert proc.returncode != 0, (
        f"harness passed a page with {old!r} broken\nstdout: {proc.stdout}"
    )


# ===== new-session quick chips (roadmap 8.44) ==============================

def test_the_quick_chips_choose_exactly_what_their_rows_choose(node_bin, tmp_path):
    """"Recent" offers the directories behind the grid's sessions, newest
    first; "Suggested" offers the catalog models that carry a hint. A chip
    selects through the same path as its group row, and the create
    request carries what the chips chose."""
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_form(node_bin, tmp_path, INDEX_HTML, "chips.html", scenario="chips")
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert "ok: Recent chip -> proj, Suggested chip -> Opus, POST carries both" \
        in proc.stdout, proc.stdout


@pytest.mark.parametrize("old, new", [
    pytest.param("                 pick: function () { pickCwd(d); } };",
                 "                 pick: function () {} };", id="dir-chip-wiring"),
    pytest.param("                   pick: function () { pickModel(m.label); } };",
                 "                   pick: function () {} };", id="model-chip-wiring"),
    pytest.param("    return ranked.slice(0, 4);", "    return dirs.slice(0, 4);",
                 id="recent-order"),
    pytest.param("      ((newOptions && newOptions.models) || []).filter(function (m) { return !!m.hint; })",
                 "      ((newOptions && newOptions.models) || [])",
                 id="suggested-have-hints"),
])
def test_the_form_harness_detects_broken_chips(node_bin, tmp_path, old, new):
    from aipager.miniapp.static import INDEX_HTML

    assert INDEX_HTML.count(old) == 1, f"mutation site not found once: {old!r}"
    broken = INDEX_HTML.replace(old, new, 1)
    proc = _drive_form(node_bin, tmp_path, broken, "broken-chips.html", scenario="chips")
    assert proc.returncode != 0, f"harness passed broken chips: {old!r}"


# ===== the Telegram layer: MainButton, theme, swipes (roadmap 8.44) =======

@pytest.mark.parametrize("scenario, expected", [
    ("detail_waiting_mainbutton",
     "ok: waiting session -> MainButton Answer in chat, hidden under the menu"),
    ("theme_changed", "ok: themeChanged -> data-scheme dark"),
])
def test_telegram_layer_scenarios(node_bin, tmp_path, scenario, expected):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_smoke(node_bin, tmp_path, INDEX_HTML, scenario)
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert expected in proc.stdout, proc.stdout


def test_mainbutton_carries_start_session_on_the_form(node_bin, tmp_path):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_form(node_bin, tmp_path, INDEX_HTML, "mb.html", scenario="mainbutton")
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert "ok: MainButton Start session -> POST /api/sessions, swipes restored" \
        in proc.stdout, proc.stdout


def test_the_harness_detects_an_unguarded_mainbutton(node_bin, tmp_path):
    """Guard the guard: the default mock has no MainButton (an older
    Telegram client). Drop the feature check and ordinary scenarios,
    which never touch the new API, must fail."""
    from aipager.miniapp.static import INDEX_HTML

    old = "    var mb = mainButton();\n    if (!mb) { return; }\n"
    assert INDEX_HTML.count(old) == 1
    broken = INDEX_HTML.replace(old, "    var mb = tg.MainButton;\n", 1)
    for scenario in ("stop_busy", "grid_render"):
        proc = _drive_smoke(node_bin, tmp_path, broken, scenario)
        assert proc.returncode != 0, f"{scenario} passed with an unguarded MainButton"


@pytest.mark.parametrize("old, new, scenario, form", [
    pytest.param("                 is_active: !document.getElementById(\"new-create\").disabled };",
                 "                 is_active: true };",
                 "mainbutton", True, id="mainbutton-mirrors-validity"),
    pytest.param("    } else if (!overlayCloser && currentView.type === \"detail\" && lastDetailData &&",
                 "    } else if (currentView.type === \"detail\" && lastDetailData &&",
                 "detail_waiting_mainbutton", False, id="mainbutton-hidden-under-overlay"),
    pytest.param("    var off = currentView.type === \"new\" || !!overlayCloser;",
                 "    var off = false;",
                 "mainbutton", True, id="swipes-off-on-the-form"),
    pytest.param("    try { tg.onEvent(\"themeChanged\", applyScheme); } catch (e) { /* older client */ }",
                 "", "theme_changed", False, id="theme-event"),
])
def test_the_harness_detects_a_broken_telegram_layer(node_bin, tmp_path, old, new, scenario, form):
    from aipager.miniapp.static import INDEX_HTML

    assert INDEX_HTML.count(old) == 1, f"mutation site not found once: {old!r}"
    broken = INDEX_HTML.replace(old, new, 1)
    if form:
        proc = _drive_form(node_bin, tmp_path, broken, "broken-tg.html", scenario=scenario)
    else:
        proc = _drive_smoke(node_bin, tmp_path, broken, scenario)
    assert proc.returncode != 0, f"harness passed a broken Telegram layer: {old!r}"


# ===== settings schema text shown plain (roadmap 8.44) =====================
#
# settings_schema() is shared with /settings in the chat: its titles lead
# with an emoji and its labels and help carry em dashes. Every surface that
# renders it (Settings tab, session settings, the new form) goes through
# renderOptionGroup, which must show it plain.

_PROBE_GROUP = {
    "section": "probe", "field": "probe_field", "title": "🧪 Probe — group",
    "options": [
        {"value": "a", "label": "On — a probe label", "help": "help — text"},
        {"value": "b", "label": "Off", "help": ""},
    ],
}


@pytest.fixture
def real_schema(tmp_path, monkeypatch):
    """The real settings_schema() plus a probe group that always carries an
    emoji title and em dashes, handed to the harness through the env."""
    from aipager.bot.settings_menu import settings_schema

    schema = settings_schema() + [_PROBE_GROUP]
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("AIPAGER_TEST_SCHEMA", str(path))
    return schema


def test_settings_surfaces_show_schema_text_plain(node_bin, tmp_path, real_schema):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_smoke(node_bin, tmp_path, INDEX_HTML, "schema_plain")
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert "ok: schema text -> no em dash, bare titles, header shows the lead" in proc.stdout


def test_the_new_form_shows_schema_text_plain(node_bin, tmp_path, real_schema):
    from aipager.miniapp.static import INDEX_HTML

    proc = _drive_form(node_bin, tmp_path, INDEX_HTML, "schema.html", scenario="schema_plain")
    assert proc.returncode == 0, f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    assert "ok: form schema text -> no em dash, bare titles, header shows the lead" \
        in proc.stdout, proc.stdout


_SCHEMA_PLAIN_MUTANTS = [
    pytest.param("    title.textContent = groupTitle(opts.title);",
                 "    title.textContent = opts.title;", id="title"),
    pytest.param("      : (currentOpt ? labelLead(currentOpt.label) : \"-\");",
                 "      : (currentOpt ? currentOpt.label : \"-\");", id="header-value"),
    pytest.param("        main.textContent = plain(o.label);",
                 "        main.textContent = o.label;", id="row-label"),
    pytest.param("          help.textContent = plain(o.help);",
                 "          help.textContent = o.help;", id="row-help"),
]


@pytest.mark.parametrize("old, new", _SCHEMA_PLAIN_MUTANTS)
def test_the_harness_detects_schema_text_shown_raw(node_bin, tmp_path, real_schema, old, new):
    """Guard the guard: drop any one normalisation and both the settings
    scenario and the new-form scenario must fail."""
    from aipager.miniapp.static import INDEX_HTML

    assert INDEX_HTML.count(old) == 1, f"mutation site not found once: {old!r}"
    broken = INDEX_HTML.replace(old, new, 1)
    proc = _drive_smoke(node_bin, tmp_path, broken, "schema_plain")
    assert proc.returncode != 0, f"settings scenario passed with {old!r} broken"
    proc = _drive_form(node_bin, tmp_path, broken, "broken-schema.html", scenario="schema_plain")
    assert proc.returncode != 0, f"form scenario passed with {old!r} broken"
