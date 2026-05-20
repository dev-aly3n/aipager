"""In-progress wizard draft persistence (see :mod:`aipager.wizard`).

A multi-step sub-flow (building a group scope member-by-member) holds
its partial state in ``~/.config/aipager/.wizard-draft.json`` so a
crash or mid-flow cancel never strands the operator. Only *completed*
scopes are flushed to ``aipager.yaml``; the half-built one lives here
until confirmed. See architecture §3.0b (resilient wizard).
"""

from __future__ import annotations

import json
import logging
import os

from aipager.wizard._constants import CONFIG_DIR

log = logging.getLogger(__name__)

DRAFT_PATH = CONFIG_DIR / ".wizard-draft.json"


def save_draft(draft: dict) -> None:
    """Atomic-write the in-progress sub-flow state (mode 0600)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DRAFT_PATH.with_suffix(DRAFT_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(draft), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        log.debug("could not chmod %s", tmp, exc_info=True)
    os.replace(tmp, DRAFT_PATH)


def load_draft() -> dict | None:
    """Return the saved draft, or ``None`` if absent / unparseable."""
    if not DRAFT_PATH.exists():
        return None
    try:
        data = json.loads(DRAFT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def clear_draft() -> None:
    """Delete the draft file (no-op if absent)."""
    try:
        DRAFT_PATH.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        log.debug("could not remove %s", DRAFT_PATH, exc_info=True)
