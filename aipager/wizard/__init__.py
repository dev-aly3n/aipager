"""Interactive setup wizard for ``aipager config``.

Submodules:

- ``first_run`` — fresh-install flow (token + mode + chat + write env + deps + settings).
- ``team_setup`` — team.yaml creation flow.
- ``edit_menu`` — re-running wizard when a config already exists.
- ``telegram_api`` — Telegram REST helpers used by first_run / edit flows.
- ``settings_patch`` — patches ``~/.claude/settings.json`` with hooks + statusLine.
- ``daemon_io`` — config.env read/write + SIGUSR1 hot-reload signalling.
- ``display`` — questionary wrapper, spinner context, current-config panel.
- ``_constants`` — shared paths, hook event names, prompt style.

The entry points are :func:`run` (called by ``aipager config`` via the
CLI dispatcher) and :func:`main` (for ``python -m aipager.wizard``).
"""

from __future__ import annotations

import sys

from aipager.wizard._constants import CONFIG_ENV
from aipager.wizard.edit_menu import _edit_flow
from aipager.wizard.first_run import _first_run_flow


def run() -> int:
    """Entry point for ``aipager config``.

    - No config.env on disk → first-run wizard (full token + mode + chat
      + team-or-not + deps + settings + write).
    - config.env present → edit menu showing current state.
    """
    if not CONFIG_ENV.exists():
        return _first_run_flow()
    return _edit_flow()


def main() -> None:
    sys.exit(run())
