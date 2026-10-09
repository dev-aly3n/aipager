"""English is byte-identical on every phase 2 surface (G81).

``tests/golden/english_surfaces.json`` was recorded on the base of phase 2
(a2dd9db), before any surface was routed through a key, by running this
module with ``AIPAGER_RECORD_SURFACES=1``. Every surface of the registry
(``tests/i18n_surfaces.py``) must still render exactly those bytes for an
English chat."""

from __future__ import annotations

import os

from tests import i18n_surfaces


def test_every_phase_2_surface_is_byte_identical_in_english(mk_bot, run_async, monkeypatch):
    renders = i18n_surfaces.render_all(mk_bot, run_async, monkeypatch, "en")
    if os.environ.get("AIPAGER_RECORD_SURFACES") == "1":
        i18n_surfaces.GOLDEN.write_text(i18n_surfaces.dump(renders), encoding="utf-8")
    golden = i18n_surfaces.load_golden()
    assert sorted(renders) == sorted(golden)
    for name in golden:
        assert renders[name] == golden[name], name


def test_the_registry_covers_every_phase_2_surface():
    names = {name for name, _fn in i18n_surfaces.SURFACES}
    for prefix in ("keyboard.", "settings.", "menu.", "card.", "agents_line", "prompt.",
                   "answer.", "pinned", "status.", "language."):
        assert any(n.startswith(prefix) for n in names), prefix
