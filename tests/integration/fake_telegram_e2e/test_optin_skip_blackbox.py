"""Black-box: the fake-Telegram scenarios are opt-in.

design.md success criterion: "With AIPAGER_E2E_FAKETG unset, the default run
starts no daemon and every faketg module skips." Runs a nested pytest in a
subprocess. The Claude credential is always scrubbed and the backend is forced
to "real", so the credential guard stays in force as a second barrier: even a
broken opt-in guard skips instead of starting a daemon.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from .conftest import PYTHON, REPO, scrubbed_env

FAKETG = str(REPO / "tests" / "e2e" / "faketg")


def _pytest(*args: str, **env) -> str:
    e = scrubbed_env(AIPAGER_E2E_FAKETG_CLAUDE="real", CLAUDE_CODE_OAUTH_TOKEN=None, **env)
    proc = subprocess.run([PYTHON, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs",
                           FAKETG, *args], env=e, cwd=str(REPO), capture_output=True,
                          text=True, timeout=300)
    return proc.stdout + proc.stderr


@pytest.fixture(scope="module")
def default_run():
    return _pytest()


@pytest.fixture(scope="module")
def e2e_without_optin():
    return _pytest("-m", "e2e")


def test_default_run_selects_no_faketg_test(default_run):
    assert re.search(r"\b0 passed|no tests ran|\d+ deselected", default_run)


def test_default_run_passes_nothing(default_run):
    assert not re.search(r"\b[1-9]\d* passed", default_run)


def test_e2e_marker_without_optin_skips(e2e_without_optin):
    assert re.search(r"\b[1-9]\d* skipped", e2e_without_optin)


def test_e2e_marker_without_optin_runs_nothing(e2e_without_optin):
    assert not re.search(r"\b[1-9]\d* (passed|failed|error)", e2e_without_optin)


def test_e2e_marker_without_optin_names_the_switch(e2e_without_optin):
    assert "AIPAGER_E2E_FAKETG=1" in e2e_without_optin


def test_real_mode_without_credential_skips_with_hint():
    out = _pytest("-m", "e2e", AIPAGER_E2E_FAKETG="1")
    assert "AIPAGER_E2E_FAKETG_CLAUDE=standin" in out and not re.search(
        r"\b[1-9]\d* (passed|failed|error)", out)
