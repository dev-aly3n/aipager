"""The phase 3a surface registry is complete (multilingual phase 3a,
design section G): no surface is left pending, and every family of keys
phase 3a added has surfaces that render it in every language."""

from __future__ import annotations

import json
from pathlib import Path

from tests import i18n_surfaces as shared
from tests import i18n_surfaces_p3a as p3a

SOURCE = Path(__file__).resolve().parent.parent / "tools" / "i18n" / "source.jsonl"

#: The key families phase 3a added, each with the surface family that
#: renders it (``toast.`` keys are the pop-ups of the ``taps.`` surfaces
#: and of every family's tap surfaces).
FAMILIES = {
    "safety": "safety.", "held": "held.", "help": "help.", "home": "home.",
    "route": "route.", "modecard": "modecard.", "appcmd": "appcmd.", "whoami": "whoami.",
    "media": "media.", "newflow": "newflow.", "sess": "sess.", "sendnow": "sendnow.",
    "chatmove": "chatmove.", "notice": "notice.", "toast": "taps.",
}

#: The modules phase 3a migrated.
OWNED = {
    "aipager/bot/handlers.py", "aipager/bot/new_flow.py", "aipager/bot/session_parity.py",
    "aipager/bot/session_ops.py", "aipager/bot/send_now.py", "aipager/bot/held_message.py",
    "aipager/bot/notify.py", "aipager/bot/voice.py", "aipager/bot/card_owner.py",
    "aipager/bot/tap_gate.py", "aipager/bot/chat_migration.py", "aipager/bot/callbacks.py",
    "aipager/bot/safety_notice.py",
    # "peer message authority": the card and halt lines about a message
    # from another session.
    "aipager/bot/peer_notice.py",
}


def _source():
    return [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_no_surface_is_left_pending():
    assert p3a.P3A_PENDING == frozenset()
    assert not (shared.PENDING & p3a.P3A_SURFACES)
    assert p3a.P3A_SURFACES <= shared.MIGRATED


def test_every_family_has_surfaces():
    names = p3a.P3A_SURFACES
    for family, surface_prefix in FAMILIES.items():
        assert any(name.startswith(surface_prefix) for name in names), family


#: The pop-up family is shared by every phase (phase 2's dashboard and
#: language menu, phase 3b's ``toast.upd.``/``toast.rep.``/``toast.auth.``),
#: so for it only the sub-prefixes phase 3a introduced are checked.
TOAST_PREFIXES = tuple("toast." + p for p in (
    "clear_", "compact_", "gate_", "kill_", "new_", "owner_", "perms_", "retry_",
    "sendnow_", "sess_", "stop_", "unknown_action"))


def _is_3a_key(key: str) -> bool:
    family = key.split(".", 1)[0]
    if family == "toast":
        return key.startswith(TOAST_PREFIXES)
    return family in FAMILIES


def test_every_family_key_comes_from_a_migrated_module():
    """A key of a phase 3a family lives in a module phase 3a migrated."""
    for row in _source():
        if _is_3a_key(row["k"]):
            assert row["file"] in OWNED, (row["k"], row["file"])


def test_the_toast_prefixes_are_phase_3a_s_own():
    """The sub-prefixes select phase 3a's pop-up keys and none of the
    other phases' (so the check above holds on the merged tree too)."""
    rows = [row for row in _source() if row["k"].startswith(TOAST_PREFIXES)]
    assert len(rows) >= 70
    for key in ("toast.upd.checking", "toast.rep.in_dm", "toast.auth.not_listed",
                "toast.session_not_found", "toast.not_working", "toast.invalid_callback"):
        assert not _is_3a_key(key), key


def test_the_owned_modules_have_keys():
    files = {row["file"] for row in _source()}
    assert OWNED <= files
