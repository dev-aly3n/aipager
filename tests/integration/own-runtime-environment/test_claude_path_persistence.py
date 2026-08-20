"""design.md success criterion 11:

    "dump_scopes() after dump_claude_path() preserves claude_path -- the
    'silently wiped by the next `aipager config`' regression test."

Every ``scope`` call below passes ``path=`` explicitly, per this
package's own hard rule (see conftest.py's module docstring and the
task brief's opening warning): ``load_claude_path``/``dump_claude_path``
default to a module-level ``CONFIG_PATH`` bound at *definition* time,
so a call without ``path=`` would silently read/write the operator's
real ``~/.config/aipager/aipager.yaml`` instead of this test's isolated
copy.
"""
from __future__ import annotations

import aipager.scope as scope_mod


def _seed_config(path):
    scope_mod.dump_scopes(
        [scope_mod.Scope(
            chat_id=42, kind="dm", label="owner",
            members=(scope_mod.Member(id=1, label="me", role="owner"),),
        )],
        "TESTTOKEN",
        path=path,
    )


def test_claude_path_survives_a_subsequent_dump_scopes_call(tmp_path):
    cfg_path = tmp_path / "aipager.yaml"
    _seed_config(cfg_path)

    scope_mod.dump_claude_path("/opt/fixture/claude", path=cfg_path)
    assert scope_mod.load_claude_path(path=cfg_path) == "/opt/fixture/claude"

    # Simulate "the next `aipager config` run" -- dump_scopes rebuilds
    # the document from scratch.
    _seed_config(cfg_path)

    assert scope_mod.load_claude_path(path=cfg_path) == "/opt/fixture/claude", (
        "claude_path was silently wiped by dump_scopes() -- it must join "
        "the same preservation list as default_mode/miniapp"
    )


def test_clearing_claude_path_with_empty_string_removes_the_key(tmp_path):
    cfg_path = tmp_path / "aipager.yaml"
    _seed_config(cfg_path)
    scope_mod.dump_claude_path("/opt/fixture/claude", path=cfg_path)

    scope_mod.dump_claude_path("", path=cfg_path)

    assert scope_mod.load_claude_path(path=cfg_path) == ""
    # And clearing it must also survive a subsequent dump_scopes (i.e.
    # dump_scopes must not resurrect a stale value from a backup/cache).
    _seed_config(cfg_path)
    assert scope_mod.load_claude_path(path=cfg_path) == ""


def test_claude_path_coexists_with_default_mode_and_miniapp_preservation(
    tmp_path,
):
    """The preservation list already carries default_mode + miniapp;
    claude_path joining it must not come at the expense of the other
    two silently regressing (a plausible way to "fix" this bug wrong:
    special-case claude_path and accidentally drop the others).
    """
    cfg_path = tmp_path / "aipager.yaml"
    _seed_config(cfg_path)
    scope_mod.dump_miniapp(
        {"enabled": True, "port": 9999, "public_url": "https://example.test"},
        path=cfg_path,
    )
    scope_mod.dump_scopes(
        [scope_mod.Scope(chat_id=42, kind="dm", label="owner")],
        "TESTTOKEN", path=cfg_path, default_mode="auto",
    )
    scope_mod.dump_claude_path("/opt/fixture/claude", path=cfg_path)

    _seed_config(cfg_path)  # another bare "aipager config" run

    assert scope_mod.load_claude_path(path=cfg_path) == "/opt/fixture/claude"
    assert scope_mod.load_default_mode(path=cfg_path) == "auto"
    assert scope_mod.load_miniapp(path=cfg_path).get("enabled") is True
