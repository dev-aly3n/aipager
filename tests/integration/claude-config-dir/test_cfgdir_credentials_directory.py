"""Test isolation clears ``CREDENTIALS_DIRECTORY`` (entrypoints "Env
vars": "The autouse conftest clears it (like ``CLAUDE_CONFIG_DIR`` and
``CLAUDE_CODE_CUSTOM_OAUTH_URL``)"; review-1 rev-iter1-002).

Under a systemd service the session env reads
``$CREDENTIALS_DIRECTORY/claude_oauth`` before daemon.env, so a
``CLAUDE_CONFIG_DIR=`` there would aim every test at a real config folder.
The only honest black-box check is a pytest run that STARTS with the
variable set: a nested run of ``_inner_credentials_directory.py`` under
the project's own ``tests/conftest.py``, with a throwaway HOME.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
INNER = HERE / "_inner_credentials_directory.py"


@pytest.fixture(scope="module")
def nested_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("credsdir")
    planted = tmp / "operators-real-claude-config"
    planted.mkdir()
    creds = tmp / "creds"
    creds.mkdir()
    (creds / "claude_oauth").write_text(f"CLAUDE_CONFIG_DIR={planted}\n")
    os.chmod(creds / "claude_oauth", 0o600)
    home = tmp / "home"
    home.mkdir()

    env = dict(os.environ)
    for var in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_CUSTOM_OAUTH_URL",
                "PYTEST_XDIST_WORKER", "PYTEST_XDIST_WORKER_COUNT",
                "PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST"):
        env.pop(var, None)
    env.update(HOME=str(home), CREDENTIALS_DIRECTORY=str(creds),
               CFGDIR_PLANTED=str(planted), PYTHONPATH=str(REPO))
    base = Path("/tmp/pytest-of-aly")
    base.mkdir(parents=True, exist_ok=True)
    basetemp = Path(tempfile.mkdtemp(prefix="cfgi", dir=base))
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             "-p", "no:randomly", f"--basetemp={basetemp}", "-rA", str(INNER)],
            cwd=str(REPO), env=env, capture_output=True, text=True, timeout=300)
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)
    return r


def _passed(r, name: str) -> bool:
    return f"PASSED {INNER.relative_to(REPO)}::{name}" in r.stdout


def test_the_nested_run_collects_the_three_checks(nested_run):
    ran = [line for line in nested_run.stdout.splitlines()
           if line.startswith(("PASSED ", "FAILED ", "ERROR "))]
    assert len(ran) == 3, nested_run.stdout[-3000:] + nested_run.stderr[-2000:]


def test_credentials_directory_is_cleared_for_every_test(nested_run):
    assert _passed(nested_run, "test_credentials_directory_is_cleared"), nested_run.stdout[-3000:]


def test_a_credentials_file_config_dir_never_reaches_the_session_env(nested_run):
    assert _passed(nested_run, "test_the_session_env_has_no_planted_config_dir"), \
        nested_run.stdout[-3000:]


def test_a_credentials_file_config_dir_never_reaches_the_resolver(nested_run):
    assert _passed(nested_run, "test_sessions_do_not_resolve_to_the_planted_folder"), \
        nested_run.stdout[-3000:]
