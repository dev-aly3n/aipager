"""aipager — Telegram remote control for Claude Code sessions."""


def __getattr__(name: str) -> str:
    # ``__version__`` is read on first use, not at import: every
    # ``aipager.*`` import runs this file, and the hook and status line
    # helpers Claude Code starts on each event never need it, while
    # reading it costs ~30 ms of their start (importlib.metadata).
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError, version

        try:
            value = version("aipager")
        except PackageNotFoundError:
            value = "0.0.0+unknown"
        globals()["__version__"] = value  # once per process, as before
        return value
    raise AttributeError(f"module 'aipager' has no attribute {name!r}")
