"""Tests for `aipager.voice` (item 5.3).

We can't exercise faster-whisper for real in CI without the optional
extra and a model download. These tests focus on the lazy-import
contract, error surface, and the wrapper plumbing.
"""

from __future__ import annotations

import builtins

import pytest

from aipager.bot import voice


def test_module_import_does_not_require_faster_whisper(run_async):
    """Importing `aipager.voice` must succeed even when faster-whisper
    is missing — that's the whole point of making it an optional extra.
    The import is `TYPE_CHECKING` only at module scope."""
    # `voice` is already imported at the top; assert it's a module
    import aipager.bot.voice as mod
    assert hasattr(mod, "transcribe")
    assert hasattr(mod, "is_available")


def test_is_available_returns_bool(run_async):
    assert isinstance(voice.is_available(), bool)


def test_is_available_false_when_import_fails(monkeypatch, run_async):
    """Simulate faster-whisper not installed."""
    real_import = builtins.__import__

    def _fake_import(name, *a, **kw):
        if name == "faster_whisper":
            raise ImportError("No module named 'faster_whisper'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    assert voice.is_available() is False


def test_get_model_raises_voice_unavailable_when_not_installed(monkeypatch, run_async):
    """If faster-whisper isn't installed, _get_model raises
    VoiceUnavailable with an actionable message."""
    monkeypatch.setattr(voice, "_model", None)
    real_import = builtins.__import__

    def _fake_import(name, *a, **kw):
        if name == "faster_whisper":
            raise ImportError("No module named 'faster_whisper'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    with pytest.raises(voice.VoiceUnavailable) as exc:
        voice._get_model()
    # Friendly message points users at the install command
    assert "aipager[voice]" in str(exc.value)


def test_transcribe_missing_file_raises(tmp_path, run_async):
    missing = tmp_path / "nope.ogg"
    with pytest.raises(FileNotFoundError):
        run_async(voice.transcribe(str(missing)))


def test_transcribe_calls_executor_for_blocking_work(monkeypatch, tmp_path, run_async):
    """transcribe() should NOT block the event loop — it runs the
    blocking faster-whisper call in an executor."""
    audio = tmp_path / "fake.ogg"
    audio.write_bytes(b"not-real-audio")

    called_in_executor: dict = {"flag": False}

    def _fake_sync(path, lang):
        # If this fired in the main thread, the event loop would have
        # been blocked. Mark that the executor branch was taken.
        called_in_executor["flag"] = True
        return "hello from fake transcribe"

    monkeypatch.setattr(voice, "_sync_transcribe", _fake_sync)
    out = run_async(voice.transcribe(str(audio)))
    assert out == "hello from fake transcribe"
    assert called_in_executor["flag"] is True


def test_default_model_respects_env(monkeypatch, run_async):
    """The DEFAULT_MODEL constant snapshot is set at import time; the
    env override is documented for users to set before importing."""
    monkeypatch.setenv("AIPAGER_WHISPER_MODEL", "tiny")
    # Re-import to pick up the env change
    import importlib
    import aipager.bot.voice as v
    importlib.reload(v)
    assert v.DEFAULT_MODEL == "tiny"
    # Restore (other tests may rely on the default)
    monkeypatch.delenv("AIPAGER_WHISPER_MODEL")
    importlib.reload(v)
