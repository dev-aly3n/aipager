"""Shared error formatting for the aipager CLI.

Centralizes the look and feel of every user-facing error so the codebase
has one visual style (✗ for errors, ⚠ for warnings, multi-line block to
stderr) and one place to inject the GitHub issues link.

Use ``friendly_error`` / ``friendly_warn`` from any subcommand. Wrap
entry points with ``with_friendly_errors`` to translate the small set of
common OS-level exceptions (PermissionError, OSError, etc.) into
actionable output instead of raw tracebacks. Install ``install_excepthook``
once in ``cli.main()`` to catch anything that slips through.
"""

from __future__ import annotations

import errno
import functools
import sys
from typing import Callable, TypeVar

ISSUE_URL = "https://github.com/dev-aly3n/aipager/issues"

_BUG_HINT = (
    "If this looks like a bug, please open an issue with the output of "
    f"`aipager doctor`:\n      {ISSUE_URL}"
)

T = TypeVar("T")


def friendly_error(*lines: str, bug: bool = False) -> None:
    """Print a multi-line error block to stderr with a leading ✗ marker.

    Pass ``bug=True`` for unexpected internal errors — appends a link
    to the issue tracker. For user-actionable misconfiguration (missing
    token, bad path, etc.) leave ``bug=False``: those aren't bug reports.
    """
    if lines:
        print(f"✗ {lines[0]}", file=sys.stderr)
        for line in lines[1:]:
            print(line, file=sys.stderr)
    if bug:
        print("", file=sys.stderr)
        print(_BUG_HINT, file=sys.stderr)


def friendly_warn(*lines: str) -> None:
    """Print a multi-line warning block to stderr with a leading ⚠ marker."""
    if lines:
        print(f"⚠ {lines[0]}", file=sys.stderr)
        for line in lines[1:]:
            print(line, file=sys.stderr)


def install_excepthook() -> None:
    """Replace ``sys.excepthook`` with a friendly one-screen summary.

    Catches uncaught exceptions in the CLI entry path. KeyboardInterrupt
    is left for the caller (the CLI dispatcher handles it explicitly).
    """
    def _hook(exc_type, exc, tb):  # noqa: ANN001
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        friendly_error(
            "aipager hit an unexpected error.",
            "",
            f"  {exc_type.__name__}: {exc}",
            "",
            bug=True,
        )
    sys.excepthook = _hook


def _explain_os_error(e: OSError) -> tuple[str, str] | None:
    """Translate a common errno into (headline, hint), or None if generic."""
    en = getattr(e, "errno", None)
    path = getattr(e, "filename", None) or ""
    suffix = f": {path}" if path else ""
    if en == errno.EACCES or isinstance(e, PermissionError):
        return (f"permission denied{suffix}", "  Check the file's ownership and permissions.")
    if en == errno.ENOENT or isinstance(e, FileNotFoundError):
        return (f"not found{suffix}", "  Confirm the path exists.")
    if en == errno.ENOSPC:
        return ("disk full", "  Free some space and retry.")
    if en == errno.EROFS:
        return (f"read-only filesystem{suffix}", "  Mount writable or pick another location.")
    if en == errno.EEXIST:
        return (f"already exists{suffix}", "  Remove or back up the existing path first.")
    return None


def with_friendly_errors(fn: Callable[..., T]) -> Callable[..., T]:
    """Decorator: wrap a subcommand handler so OS errors print friendly."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            return fn(*args, **kwargs)
        except KeyboardInterrupt:
            print("", file=sys.stderr)
            friendly_warn("Cancelled.")
            sys.exit(130)
        except SystemExit:
            raise
        except OSError as e:
            hint = _explain_os_error(e)
            if hint is not None:
                friendly_error(hint[0], hint[1])
            else:
                friendly_error(f"{type(e).__name__}: {e}", bug=True)
            sys.exit(1)
    return wrapper
