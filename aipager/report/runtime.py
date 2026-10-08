"""Problem reports around the daemon's start and stop (roadmap 8.112).

``aipager start`` calls :func:`daemon_starting` once it holds the daemon
lock (so only one process ever writes these files) and
:func:`daemon_stopped` when its event loop has ended. Neither raises.
"""

from __future__ import annotations

from aipager.report import markers, store

#: Where an unclean exit is recorded: the start command that the killed
#: daemon was running under (a call-site fingerprint, the same every time).
CRASH_SITE = {"file": "aipager/cli/daemon.py", "line": 0, "fn": "_cmd_start"}


def daemon_starting(*, service: bool, now=None) -> None:
    """Read the store; judge how the last daemon ended from a running
    marker it left; mark this one as running; start the first-install
    clock if this is the first start. An unclean exit of a daemon that
    ran as a service is a bug; one in the foreground (a terminal closed
    under ``aipager start``) is only an anomaly."""
    try:
        store.load()
        previous = markers.previous_exit(now)
        if previous is not None:
            kind, was_service = previous
            if kind == "crash":
                store.record_site("crash", **CRASH_SITE, where="daemon", trigger="crash",
                                  tier="bug" if was_service else "anomaly", now=now)
                store.note_exit("crash", now)
            elif kind == "reboot":
                store.note_exit("reboot", now)
        markers.write_running(service=service, now=now)
        markers.first_start(now)
        store.save_if_dirty(force=True)
    except Exception:  # noqa: BLE001 - reports must never stop a start
        pass


def daemon_stopped(*, clean: bool, now=None) -> None:
    """The daemon's loop ended: cleanly, or on an error the log handler
    has already recorded. Either way the marker goes, so the next start
    does not count this exit twice."""
    store.note_exit("clean" if clean else "crash", now)
    store.save_if_dirty(force=True)
    markers.clear_running()  # none of the three raises
