"""``default_mode`` in aipager.yaml (2026-10-11): the key nothing ever read
(roadmap 8.89) is gone. Its values ("auto" meant bypass) would mean
something else now that there are three modes, so an old file still loads,
nothing acts on the key, and the next rewrite drops it."""

from __future__ import annotations

import pytest
import yaml

from aipager import scope as scope_mod
from aipager.scope import Member, Scope, dump_scopes


def _scopes():
    return [Scope(chat_id=123, kind="dm", label="owner DM",
                  members=(Member(id=123, label="owner", role="owner"),))]


def _old_file(path, mode):
    path.write_text(yaml.safe_dump({
        "schema_version": 2, "bot_token": "tok", "default_mode": mode,
        "scopes": [{"kind": "dm", "chat_id": 123, "label": "owner DM",
                    "members": [{"id": 123, "label": "owner", "role": "owner"}]}],
    }))


def test_the_reader_is_gone():
    assert not hasattr(scope_mod, "load_default_mode")


def test_dump_scopes_takes_no_default_mode():
    with pytest.raises(TypeError):
        dump_scopes(_scopes(), "tok", None, default_mode="auto")  # type: ignore[call-arg]


@pytest.mark.parametrize("mode", ["ask", "auto", "unsafe"])
def test_an_old_file_loads_and_a_rewrite_drops_the_key(tmp_path, mode):
    cfg = tmp_path / "aipager.yaml"
    _old_file(cfg, mode)
    scopes, token = scope_mod.load_scopes(cfg)
    assert token == "tok" and [s.chat_id for s in scopes] == [123]

    dump_scopes(scopes, token, cfg)

    assert "default_mode" not in yaml.safe_load(cfg.read_text())
    assert scope_mod.load_scopes(cfg)[0][0].chat_id == 123


def test_a_file_without_the_key_stays_without_it(tmp_path):
    cfg = tmp_path / "aipager.yaml"
    dump_scopes(_scopes(), "tok", cfg)
    dump_scopes(_scopes(), "tok", cfg)
    assert "default_mode" not in yaml.safe_load(cfg.read_text())
