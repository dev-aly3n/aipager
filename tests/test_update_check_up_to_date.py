"""The check payload's ``up_to_date``: the Mini App's Updates card shows
only the summary line when it is set (nothing to do), so it must be set
only when both lookups succeeded, nothing newer can be installed and no
installed version waits for a restart."""

from __future__ import annotations

import pytest

from aipager.bot import update_flow
from tests.i18n_surfaces import update_status

_CURRENT = dict(ap_newer=False, latest="1.0.1", cc_newer=False, cc_latest="2.1.300")


def _waiting():
    status = update_status(**_CURRENT)
    status["aipager"].update(installed="1.0.2", latest="1.0.2", installed_ok=True)
    return status


def _installed(installed_ok):
    """Newer than the newest release on record (a manual install the
    index lookup lags behind), so no restart is offered: a note says it."""
    status = update_status(**_CURRENT)
    status["aipager"].update(installed="1.0.2", installed_ok=installed_ok)
    return status


@pytest.mark.parametrize("status, want", [
    (update_status(**_CURRENT), True),
    (update_status(), False),                                            # both newer
    (update_status(ap_newer=False, latest="1.0.1"), False),              # Claude Code newer
    (update_status(cc_newer=False, cc_latest="2.1.300"), False),         # aipager newer
    (update_status(cc_newer=False, cc_latest="2.1.300", upgradable=False,
                   reason="editable install"), False),                   # can't update here
    (update_status(ap_newer=False, latest="1.0.1", blocked="npm prefix not writable"),
     False),                                                             # Claude Code held back
    (update_status(latest=None, cc_newer=False, cc_latest="2.1.300"), False),  # lookup failed
    (update_status(ap_newer=False, latest="1.0.1", cc_newer=False, cc_latest=None),
     False),                                                             # lookup failed
    (update_status(ap_newer=False, latest="1.0.1", cc=None, cc_newer=False), False),
    (_waiting(), False),                                                 # waits for a restart
    (_installed(False), False),                    # installed but does not load: reinstall
    (_installed(None), False),                     # installed, not running yet
], ids=["current", "both-newer", "claude-newer", "aipager-newer", "held-back",
        "claude-held-back", "aipager-unchecked", "claude-unchecked", "claude-unknown",
        "installed-waiting", "installed-broken", "installed-not-running"])
def test_up_to_date_only_when_there_is_nothing_to_do(status, want):
    payload = update_flow.check_payload(status, show_paths=False)
    assert payload["up_to_date"] is want
    if not want and payload["offer"] is None and payload["notes"]:
        # The note is what the card must keep showing.
        assert "installed" in payload["notes"][0]
    if want:
        assert payload["offer"] is None and not payload["restart_offer"]
        assert payload["summary"] == "Everything is up to date."
