"""Black-box tests for design.md success criterion 19: `COMPACT_CARD_
TIMEOUT_SECONDS` defaults to 180.0, is independently env-overridable, and
does not affect `COMPACT_INFLIGHT_MAX_SECONDS` (a separate, pre-existing
knob).

Follows the `reloaded_config`-style pattern already established in
`tests/test_config_malformed.py` (existing file, read for framework
conventions only): reload `aipager.config` under a patched environment,
then restore the pre-test module state on teardown so no other test in
the session sees a mutated config module.

Per entrypoints.md's own note, tests "should not need to set this --
construct scenarios via push_compacting's own deadline_seconds parameter"
for the CARD-EXPIRY *behavior*, which is why that behavior is exercised
elsewhere (`test_compact_timeout_sweep.py`) via `push_compacting`'s
explicit parameter, not via this env var. This file is scoped exactly to
what entrypoints.md documents about the constant itself: its default, its
overridability, and its independence from the older knob.
"""

from __future__ import annotations

import importlib

import pytest

import aipager.config as _config


@pytest.fixture
def reloaded_config():
    saved = dict(vars(_config))

    def _reload():
        return importlib.reload(_config)

    yield _reload

    for name in set(vars(_config)) - set(saved):
        delattr(_config, name)
    for name, value in saved.items():
        setattr(_config, name, value)


def test_default_is_180_seconds():
    assert _config.COMPACT_CARD_TIMEOUT_SECONDS == 180.0


def test_default_type_is_float():
    assert isinstance(_config.COMPACT_CARD_TIMEOUT_SECONDS, float)


def test_env_override_changes_the_constant(reloaded_config, monkeypatch):
    monkeypatch.setenv("COMPACT_CARD_TIMEOUT_SECONDS", "42.5")
    cfg = reloaded_config()
    assert cfg.COMPACT_CARD_TIMEOUT_SECONDS == 42.5


def test_env_override_does_not_move_compact_inflight_max_seconds(
    reloaded_config, monkeypatch,
):
    """The two knobs are deliberately independent -- overriding the new,
    short card-timeout must not perturb the older, generous inflight-
    warning-suppression window."""
    before = _config.COMPACT_INFLIGHT_MAX_SECONDS
    monkeypatch.setenv("COMPACT_CARD_TIMEOUT_SECONDS", "1.0")
    cfg = reloaded_config()
    assert cfg.COMPACT_INFLIGHT_MAX_SECONDS == before


def test_compact_inflight_max_seconds_env_override_does_not_move_card_timeout(
    reloaded_config, monkeypatch,
):
    """And the reverse: overriding the OLD knob must not perturb the
    NEW, independent card-expiry deadline."""
    monkeypatch.setenv("COMPACT_INFLIGHT_MAX_SECONDS", "9999.0")
    cfg = reloaded_config()
    assert cfg.COMPACT_CARD_TIMEOUT_SECONDS == 180.0
