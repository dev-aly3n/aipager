"""Voice-message transcription for `aipager` (item 5.3).

Optional feature shipped behind the ``aipager[voice]`` install extra.
The default install doesn't pull faster-whisper or its dependencies, so
no-voice users see zero install-size impact.

Telegram voice messages arrive as Opus-encoded .ogg files. We hand them
to faster-whisper (a CTranslate2 reimplementation of OpenAI Whisper)
which runs locally on CPU — no cloud calls, no API key, audio never
leaves the machine.

First-time call downloads the model from Hugging Face (~74 MB for
``base``, cached at ``~/.cache/huggingface/hub/``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

log = logging.getLogger(__name__)

# Model size: tiny / base / small / medium / large-v3.
# ``base`` is the sweet spot for short voice memos: ~74 MB, decent
# multilingual quality, < 5 s on CPU for a 30 s clip.
DEFAULT_MODEL = os.environ.get("AIPAGER_WHISPER_MODEL", "base")

# Process-wide cached model — built lazily on first transcription so
# `import aipager.voice` is free for users who never send voice messages.
_model: "WhisperModel | None" = None
_model_lock = threading.Lock()


class VoiceUnavailable(Exception):
    """Raised when faster-whisper isn't installed or can't be loaded."""


def is_available() -> bool:
    """Cheap probe: does the runtime have faster-whisper available?"""
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _get_model() -> "WhisperModel":
    """Lazy-load the Whisper model, caching it on the module."""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise VoiceUnavailable(
                "faster-whisper is not installed. Run:\n"
                "    uv tool install --reinstall 'aipager[voice]'\n"
                "or:\n"
                "    pip install 'aipager[voice]'"
            ) from e
        log.info("loading whisper model %r (first time may download ~74 MB)",
                 DEFAULT_MODEL)
        # int8 quantization keeps memory + latency low on CPU. The model
        # is ~74 MB on disk for `base` and ~30 MB in RAM at int8.
        _model = WhisperModel(DEFAULT_MODEL, device="cpu", compute_type="int8")
        return _model


def _sync_transcribe(audio_path: str, language: str | None = None) -> str:
    """Blocking transcription. Called from an executor by ``transcribe``."""
    model = _get_model()
    # beam_size=1 is fastest; for short voice memos quality is fine.
    # Language=None lets the model autodetect (default behavior).
    segments, _info = model.transcribe(
        audio_path, beam_size=1, language=language,
    )
    return " ".join(seg.text.strip() for seg in segments).strip()


async def transcribe(audio_path: str, language: str | None = None) -> str:
    """Async wrapper. Runs faster-whisper in the default executor so it
    doesn't block the asyncio event loop. Returns the transcript text
    or raises :class:`VoiceUnavailable` if the extra isn't installed.

    ``language`` is an optional ISO-639-1 hint (``"en"``, ``"es"`` etc.).
    Leave as ``None`` for autodetection.
    """
    if not Path(audio_path).exists():
        raise FileNotFoundError(audio_path)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync_transcribe, audio_path, language)


__all__ = [
    "DEFAULT_MODEL",
    "VoiceUnavailable",
    "is_available",
    "transcribe",
]
