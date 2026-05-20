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


def _handle_draft() -> None:
    """Offer to resume/discard a leftover in-progress scope draft."""
    from aipager.wizard.scope_flows import resume_or_discard_draft
    from aipager.wizard.scope_io import read_config
    try:
        _, token = read_config()
    except Exception:
        token = ""
    resume_or_discard_draft(token, "")


def run() -> int:
    """Entry point for ``aipager config``.

    - ``aipager.yaml`` present → scope edit menu.
    - only the v1 ``config.env`` present (un-started old install) →
      migrate to v2 in place, then the edit menu.
    - neither → first-run wizard (token → owner DM scope, no mode
      question).

    A leftover wizard draft (interrupted scope add) is offered for
    resume/discard before the edit menu in either edit path.
    """
    from aipager.wizard.scope_io import config_exists

    if config_exists():
        _handle_draft()
        return _edit_flow()
    if CONFIG_ENV.exists():
        from aipager.migrate import migrate_to_v2
        migrate_to_v2()
        _handle_draft()
        return _edit_flow()
    return _first_run_flow()


def main() -> None:
    sys.exit(run())
