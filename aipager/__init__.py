"""aipager — Telegram remote control for Claude Code sessions."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("aipager")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
