"""`claude auth status` reports presence; this reports whether it WORKS.

A revoked or expired token still answers ``{"loggedIn": true}`` — verified
against claude 2.1.235 — so the cheap check cannot see the one failure
that matters most: every session parks on the login screen and hangs BUSY
forever with no error anywhere.

The classification below is deliberately lopsided. Only two shapes are
ever reported to the operator; everything else is `unknown`, which means
silence. Warning someone that their credential is bad because their
network happened to be down, on every restart, would train them to
ignore the one message that matters.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aipager import claude_resolve
from aipager.claude_resolve import CredentialCheck, validate_credential


def _probe(monkeypatch, *, code=0, out="", exc=None):
    def _fake(claude_path, env, cwd, timeout):
        if exc is not None:
            raise exc
        return code, out
    monkeypatch.setattr(claude_resolve, "_run_probe", _fake)


# ---- classification -----------------------------------------------------

def test_exit_zero_is_valid(monkeypatch):
    _probe(monkeypatch, code=0, out="ok\n")
    assert validate_credential("/x/claude", {}).state == "valid"


@pytest.mark.parametrize("output", [
    "Failed to authenticate. API Error: 401 OAuth access token is invalid.",
    "API Error: 401",
    "Invalid API key · Please run /login",
    '{"type":"error","error":{"type":"authentication_error"}}',
])
def test_auth_rejection_shapes_are_rejected(monkeypatch, output):
    """The real 2.1.235 message is the first one; the rest are shapes the
    same failure has taken elsewhere."""
    _probe(monkeypatch, code=1, out=output)
    assert validate_credential("/x/claude", {}).state == "rejected"


def test_not_logged_in_is_absent_not_rejected(monkeypatch):
    """Distinct from `rejected`: nothing is configured at all, which the
    operator fixes differently (log in, vs. your token expired)."""
    _probe(monkeypatch, code=1, out="Not logged in · Please run /login")
    assert validate_credential("/x/claude", {}).state == "absent"


# ---- everything ambiguous is silence ------------------------------------

def test_timeout_is_unknown_not_rejected(monkeypatch):
    """An unreachable API does not fail fast — it hangs and retries. If
    that were classified as a bad credential, every restart on a flaky
    network would fire a false alarm."""
    _probe(monkeypatch, exc=subprocess.TimeoutExpired(cmd="claude", timeout=20))
    r = validate_credential("/x/claude", {})
    assert r.state == "unknown"
    assert "timed out" in (r.detail or "")


def test_missing_binary_is_unknown(monkeypatch):
    _probe(monkeypatch, exc=FileNotFoundError("gone"))
    assert validate_credential("/x/claude", {}).state == "unknown"


def test_oserror_is_unknown(monkeypatch):
    _probe(monkeypatch, exc=OSError("permission denied"))
    assert validate_credential("/x/claude", {}).state == "unknown"


def test_unrecognised_failure_is_unknown_not_guessed_at(monkeypatch):
    """A non-zero exit we cannot interpret must NOT be assumed to be an
    auth problem — that is how a warning becomes noise."""
    _probe(monkeypatch, code=3, out="Execution error")
    assert validate_credential("/x/claude", {}).state == "unknown"


def test_never_raises_on_an_unexpected_exception(monkeypatch):
    _probe(monkeypatch, exc=RuntimeError("something exotic"))
    assert validate_credential("/x/claude", {}).state == "unknown"


# ---- it must not litter -------------------------------------------------

def test_probe_cleans_up_its_temp_cwd_and_transcript(monkeypatch, tmp_path):
    """Claude writes a transcript per run under ~/.claude/projects/<slug>.
    One per daemon start, kept forever, would be a slow leak in the
    operator's home directory."""
    fake_home = tmp_path / "home"
    (fake_home / ".claude" / "projects").mkdir(parents=True)
    monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: fake_home))

    seen = {}

    def _fake(claude_path, env, cwd, timeout):
        seen["cwd"] = cwd
        # simulate what claude does: write a transcript for this cwd
        proj = fake_home / ".claude" / "projects" / str(cwd).replace("/", "-")
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "session.jsonl").write_text("{}")
        return 0, "ok"

    monkeypatch.setattr(claude_resolve, "_run_probe", _fake)

    assert validate_credential("/x/claude", {}).state == "valid"

    assert not Path(seen["cwd"]).exists(), "temp cwd was left behind"
    leftovers = list((fake_home / ".claude" / "projects").iterdir())
    assert leftovers == [], f"probe transcripts accumulated: {leftovers}"


def test_cleanup_never_touches_a_real_project_directory(monkeypatch, tmp_path):
    """The cleanup deletes a directory; it must be incapable of deleting
    one of the operator's actual project histories.

    The point is that this must be able to FAIL. A previous version
    seeded a "real" project directory that the cleanup could never have
    computed as its target no matter which guard was removed, so it
    proved nothing. Here the probe is forced to run in a directory whose
    slug IS the seeded project — the only thing standing between the
    cleanup and that history is the `_PROBE_DIR_PREFIX` check. Delete
    that check and this test fails.
    """
    fake_home = tmp_path / "home"
    projects = fake_home / ".claude" / "projects"
    projects.mkdir(parents=True)
    monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: fake_home))

    # A cwd that does NOT carry the probe prefix...
    victim_cwd = tmp_path / "important-work"
    victim_cwd.mkdir()
    monkeypatch.setattr(claude_resolve, "_make_probe_dir",
                        lambda: str(victim_cwd))

    # ...whose transcript directory we seed as a real history.
    real = projects / claude_resolve._probe_project_dir(str(victim_cwd)).name
    real.mkdir()
    (real / "session.jsonl").write_text("precious")

    _probe(monkeypatch, code=0, out="ok")
    validate_credential("/x/claude", {})

    assert real.is_dir(), "cleanup deleted a real project history"
    assert (real / "session.jsonl").read_text() == "precious"


# ---- the slug rule, measured against the real binary --------------------

@pytest.mark.parametrize("cwd,expected", [
    # Measured against claude 2.1.235 on 2026-08-19 by running the probe
    # in each directory and reading back what appeared under
    # ~/.claude/projects. NOT derived from the implementation.
    ("/tmp", "-tmp"),
    ("/tmp/aipager-slug.test_dir-x.y", "-tmp-aipager-slug-test-dir-x-y"),
])
def test_project_slug_matches_claude_codes_real_rule(cwd, expected, monkeypatch,
                                                     tmp_path):
    """Every non-alphanumeric character becomes a hyphen — not just "/".

    The first implementation replaced only "/", which computed the wrong
    transcript directory for any path containing "." or "_" — including
    tempfile.mkdtemp's own suffix alphabet. Cleanup then silently
    no-opped and probe transcripts accumulated in the operator's home
    directory forever.
    """
    monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: tmp_path))
    assert claude_resolve._probe_project_dir(cwd).name == expected


def test_probe_dir_name_survives_slugification_unchanged():
    """The probe's own directory must slugify to itself, or cleanup is
    computing a path Claude never wrote."""
    name = Path(claude_resolve._make_probe_dir()).name
    try:
        assert claude_resolve._probe_project_dir(name).name == name
    finally:
        import shutil as _sh
        _sh.rmtree(Path(claude_resolve.tempfile.gettempdir()) / name,
                   ignore_errors=True)


# ---- the money guard ----------------------------------------------------

def test_the_suite_refuses_to_run_the_real_probe():
    """tests/conftest.py's _no_real_credential_probe must be load-bearing:
    every real invocation costs the operator an API call.

    Deliberately a path that cannot exist. An earlier version named
    `/usr/bin/claude`, which on this machine is a real, working install —
    so if the guard were ever disabled, this very test would have been
    the thing that spent the operator's money. The guard raises before
    the path is ever used, so a nonexistent one exercises exactly the
    same code with no blast radius.
    """
    with pytest.raises(AssertionError, match="costs money"):
        validate_credential("/nonexistent/claude", {})


def test_credential_check_defaults_detail_to_none():
    assert CredentialCheck("valid").detail is None


def test_probe_dir_creation_failure_cannot_escape(monkeypatch):
    """`validate_credential` promises "never raises", and
    `doctor.check_claude_auth` promises "never FAILs" — but the probe
    directory is created before the try/finally, and nothing between
    `check_claude_auth` and `cmd_doctor` catches anything. An unwritable
    /tmp would therefore have crashed the whole `aipager doctor` command
    rather than degrading to one inconclusive row.
    """
    def _boom():
        raise OSError("read-only file system")

    monkeypatch.setattr(claude_resolve, "_make_probe_dir", _boom)
    r = validate_credential("/x/claude", {})
    assert r.state == "unknown"
    assert "probe dir" in (r.detail or "")


def test_doctor_survives_a_probe_that_cannot_even_start(monkeypatch):
    """The same failure, one level up: the auth row degrades, the command
    still runs."""
    from aipager import doctor

    install = claude_resolve.ClaudeInstall(
        path="/x/claude", realpath="/x/claude", version="2.1.235")
    monkeypatch.setattr(
        claude_resolve, "resolve_claude_binary",
        lambda **kw: claude_resolve.ResolvedClaude(chosen=install))
    monkeypatch.setattr(
        claude_resolve, "detect_auth",
        lambda *a, **k: claude_resolve.AuthStatus(True, "oauth_token", "env"))

    def _boom():
        raise OSError("read-only file system")

    monkeypatch.setattr(claude_resolve, "_make_probe_dir", _boom)

    r = doctor.check_claude_auth()          # must not raise
    assert r.status in (doctor.OK, doctor.WARN)
