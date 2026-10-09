"""Catch problems from the daemon's own logs, by shape only (roadmap 8.112).

One :class:`ReportLogHandler` sits on the ``aipager``, ``asyncio`` and
``telegram.ext`` loggers of ``aipager start``. ``asyncio``'s default
exception handler logs a failed task there (with the exception attached),
and so does the Telegram library for its own errors, so this one handler
sees what a loop exception handler would, at the same moment.

From a log record it reads only: the attached exception (its type and
traceback OBJECTS, through :mod:`aipager.report.fingerprint`), the logger
name, the level, and the call site (``pathname`` inside the package,
``lineno``, ``funcName``). Never the message, its arguments, ``extra``
text or a formatted traceback. It records:

- every aipager WARNING-or-worse line in the hourly digest (site and
  level only);
- an attached exception by its fingerprint (or, for an environment error,
  its counter), from ERROR lines and from WARNING lines that carry one;
- an aipager ERROR line without an exception by its call site, as an
  anomaly (such lines are often the user's setup, so never a bug).

It never raises, never logs, and ignores records logged from inside its
own work (a thread-local guard), so it cannot recurse.
"""

from __future__ import annotations

import logging
import threading

from aipager.report import fingerprint as fp
from aipager.report import schema as sc
from aipager.report import store

#: The loggers the handler is attached to.
LOGGERS = ("aipager", "asyncio", "telegram.ext")
#: A record may name its own trigger with ``extra={TRIGGER_ATTR: ...}``
#: (the daemon's crash path says ``crash``); anything else is ignored.
TRIGGER_ATTR = "aipager_report_trigger"
_TRIGGERS = frozenset(sc.SCHEMA["errors"][1]["trigger"].values)
_LOGGER_LEAF = sc.SCHEMA["errors"][1]["logger"]

_inside = threading.local()
_installed: list[logging.Handler] = []


def _level_name(levelno) -> str | None:
    if type(levelno) is not int or levelno < logging.WARNING:
        return None
    if levelno >= logging.CRITICAL:
        return "CRITICAL"
    return "ERROR" if levelno >= logging.ERROR else "WARNING"


def _call_site(record) -> tuple[str, object, object] | None:
    """``(file, line, fn)`` of a call site in the package, else None. The
    line and function are checked by the store's schema with the rest."""
    file = fp._relative_file(getattr(record, "pathname", None))
    if file is None:
        return None
    return file, getattr(record, "lineno", None), getattr(record, "funcName", None)


def _exception(record):
    exc_info = getattr(record, "exc_info", None)
    return exc_info[1] if isinstance(exc_info, tuple) and len(exc_info) == 3 else None


def _trigger(record, logger: str, site) -> str:
    named = getattr(record, "aipager_report_trigger", None)  # TRIGGER_ATTR
    if isinstance(named, str) and named in _TRIGGERS:
        return named
    if logger == "asyncio":
        return "task"
    if site is not None and site[0] == "aipager/bot/lifecycle.py" and site[2] == "_on_telegram_error":
        return "ptb_handler"
    return "log_exception"


def capture(record) -> None:
    """Record what *record* says about a problem (see the module). A
    malformed record records nothing and raises nothing (the store's
    recorders never raise either); :meth:`ReportLogHandler.emit` is the
    backstop for anything unforeseen."""
    level = _level_name(getattr(record, "levelno", None))
    name = getattr(record, "name", None)
    if level is None or not isinstance(name, str):
        return
    logger = name if _LOGGER_LEAF.check(name) == name else None
    ours = name == "aipager" or name.startswith("aipager.")
    site = _call_site(record) if ours else None
    if site is not None:
        file, line, _fn = site
        store.record_digest(f"{file}:{line}", level)
    # The Mini App runs inside the daemon; its own loggers name it.
    where = ("miniapp" if name == "aipager.miniapp" or name.startswith("aipager.miniapp.")
             else "daemon")
    exc = _exception(record)
    if exc is not None:  # the store refuses anything that is no exception
        store.record_exception(exc, where=where, trigger=_trigger(record, name, site),
                               logger=logger)
    elif level != "WARNING" and site is not None:
        file, line, fn = site
        store.record_site("log_error", file=file, line=line, fn=fn, where=where,
                          trigger="log_error", tier="anomaly", logger=logger)


class ReportLogHandler(logging.Handler):
    """The capture point: see the module docstring."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)

    def emit(self, record) -> None:
        if getattr(_inside, "active", False):
            return
        _inside.active = True
        try:
            capture(record)
        except Exception:  # noqa: BLE001 - a log handler must never raise
            pass
        finally:
            _inside.active = False


def install() -> None:
    """Attach one handler to :data:`LOGGERS` (again: a no-op)."""
    if _installed:
        return
    handler = ReportLogHandler()
    for name in LOGGERS:
        logging.getLogger(name).addHandler(handler)
    _installed.append(handler)


def uninstall() -> None:
    """Detach the handler (tests)."""
    for handler in _installed:
        for name in LOGGERS:
            logging.getLogger(name).removeHandler(handler)
    _installed.clear()
