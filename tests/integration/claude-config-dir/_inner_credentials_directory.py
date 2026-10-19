"""Run only by ``test_cfgdir_credentials_directory.py`` in a nested pytest
whose environment has ``CREDENTIALS_DIRECTORY`` pointing at a folder whose
``claude_oauth`` names ``CLAUDE_CONFIG_DIR=<planted>``. The name does not
start with ``test_`` so the normal run never collects it."""

from __future__ import annotations

import os
from pathlib import Path

PLANTED = Path(os.environ["CFGDIR_PLANTED"])


def test_credentials_directory_is_cleared():
    assert "CREDENTIALS_DIRECTORY" not in os.environ


def test_the_session_env_has_no_planted_config_dir():
    from aipager import daemon_secrets
    assert daemon_secrets.build_session_env().get("CLAUDE_CONFIG_DIR") != str(PLANTED)


def test_sessions_do_not_resolve_to_the_planted_folder():
    from aipager import claude_paths
    assert PLANTED not in claude_paths.for_sessions().config_home.parents and \
        claude_paths.for_sessions().config_home != PLANTED
