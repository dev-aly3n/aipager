"""Shared error formatting for the aipager CLI.

Thin wrapper over :mod:`aipager.ui`. All visual concerns live in
``ui.py``; this module's job is to:

- Provide the ``friendly_error`` / ``friendly_warn`` API that the rest
  of the CLI calls.
- Install a global ``sys.excepthook`` that renders uncaught exceptions
  as a friendly panel with a bug-report link.
- Decorate subcommand handlers with ``with_friendly_errors`` so common
  OS-level failures (PermissionError, ENOSPC, etc.) become actionable
  one-liners instead of tracebacks.
"""

from __future__ import annotations

import errno
import functools
import sys
from typing import Callable, TypeVar

from aipager.ui import err_block, warn_block

ISSUE_URL = "https://github.com/dev-aly3n/aipager/issues"

T = TypeVar("T")


def friendly_error(*lines: str, bug: bool = False) -> None:
    """Print a multi-line error block to stderr.

    The first line is the title; remaining lines form the body. Pass
    ``bug=True`` for unexpected internal errors — adds a footer link
    to the issue tracker. For user-actionable misconfiguration (missing
    token, bad path, etc.) leave ``bug=False``: those aren't bug reports.
    """
    if not lines:
        return
    title = lines[0]
    body = [line for line in lines[1:]]
    err_block(title, body, bug=bug, issue_url=ISSUE_URL)


def friendly_warn(*lines: str) -> None:
    """Print a multi-line warning block to stderr."""
    if not lines:
        return
    title = lines[0]
    body = [line for line in lines[1:]]
    warn_block(title, body)


def install_excepthook() -> None:
    """Replace ``sys.excepthook`` with a friendly one-screen summary."""
    def _hook(exc_type, exc, tb):  # noqa: ANN001
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        friendly_error(
            "aipager hit an unexpected error.",
            "",
            f"  {exc_type.__name__}: {exc}",
            bug=True,
        )
    sys.excepthook = _hook


def _explain_os_error(e: OSError) -> tuple[str, str] | None:
    """Translate a common errno into (headline, hint), or None if generic."""
    en = getattr(e, "errno", None)
    path = getattr(e, "filename", None) or ""
    suffix = f": {path}" if path else ""
    if en == errno.EACCES or isinstance(e, PermissionError):
        return (f"permission denied{suffix}",
                "  Check the file's ownership and permissions.")
    if en == errno.ENOENT or isinstance(e, FileNotFoundError):
        return (f"not found{suffix}", "  Confirm the path exists.")
    if en == errno.ENOSPC:
        return ("disk full", "  Free some space and retry.")
    if en == errno.EROFS:
        return (f"read-only filesystem{suffix}",
                "  Mount writable or pick another location.")
    if en == errno.EEXIST:
        return (f"already exists{suffix}",
                "  Remove or back up the existing path first.")
    return None


def with_friendly_errors(fn: Callable[..., T]) -> Callable[..., T]:
    """Decorator: wrap a subcommand handler so OS errors print friendly."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            return fn(*args, **kwargs)
        except KeyboardInterrupt:
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
