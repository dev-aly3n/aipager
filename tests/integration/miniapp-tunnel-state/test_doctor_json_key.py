"""``aipager doctor --json`` keeps the Mini App row under the key
``miniapp`` (design.md success criterion "--json key miniapp
unchanged"), now carrying the tunnel's WARN while it fails."""
from __future__ import annotations

import pytest

from aipager import doctor

from miniapp_tunnel_state_bb import write_status  # noqa: I001 (the package conftest)


@pytest.fixture
def keyed(monkeypatch, status_file_seams):
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.config.MINIAPP_PORT", 8765)
    monkeypatch.setattr("aipager.miniapp.server.miniapp_extra_available", lambda: True)
    write_status(phase="slow", reason="dns_incomplete", attempt=8)
    return dict(doctor.run_all_keyed())


def test_the_json_key_is_still_miniapp(keyed):
    assert "miniapp" in keyed


def test_the_json_row_carries_the_tunnel_warning(keyed):
    assert keyed["miniapp"].status == doctor.WARN
