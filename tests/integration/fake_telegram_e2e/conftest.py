"""Shared fixtures for the black-box fake-telegram-e2e tests.

Every throwaway folder lives under /var/tmp/aft-XXXXXXXX: short (socket paths
stay well below the 108-byte limit), never /tmp/claude-*, never under the real
home, and removed at teardown.
"""

from __future__ import annotations

import os
import pwd
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PYTHON = sys.executable
# The account's real home from the password database (what the HOME guard compares).
REAL_HOME = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)

# Variables that would leak the invoking shell's setup into a probe.
_SCRUB_PREFIXES = ("AIPAGER_", "CLAUDE_TG_", "CLAUDE_CODE_", "MINIAPP_", "OBSERVER_")
_SCRUB_EXACT = ("XDG_RUNTIME_DIR", "CREDENTIALS_DIRECTORY", "PYTHONPATH")


def scrubbed_env(**overrides: str | None) -> dict[str, str]:
    """A child env without any aipager/Telegram setting from the caller.

    A value of None in ``overrides`` removes the key."""
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(_SCRUB_PREFIXES) and k not in _SCRUB_EXACT
    }
    env["PYTHONPATH"] = str(REPO)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for k, v in overrides.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return env


@pytest.fixture
def scratch():
    """A short throwaway root with ``i`` (instance dir) and ``h`` (fake HOME)."""
    root = Path(tempfile.mkdtemp(prefix="aft-", dir="/var/tmp"))
    assert not str(root).startswith("/tmp/claude-")
    assert not str(root).startswith(REAL_HOME)
    (root / "i").mkdir()
    (root / "h").mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)
