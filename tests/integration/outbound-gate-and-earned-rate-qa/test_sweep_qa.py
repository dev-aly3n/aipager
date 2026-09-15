"""R2 / row D — the static sweep, criteria 6 and 7.

The 0.7.11 sweep walked only ``aipager/bot/*.py``, omitted
``delete_message`` and ``send_chat_action``, and blanket-exempted the four
busiest files — so it could not see any of the 2026-09-15 leak. R2 widens
all three at once, and the predicate flips from "any call in the send
family" to "any call on a receiver that is not the limiter-bound bot".

Every row here feeds SYNTHETIC source to the importable rules, and the
last section runs them over the real tree. Nothing is written into
``aipager/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.sweep_rules import (
    BOT_RECEIVERS,
    EXEMPT_FILES,
    GATED_FAMILIES,
    bot_construction_offenders,
    gated_family_offenders,
    telegram_url_offenders,
    untolerated_send_offenders,
)

BOT = "aipager/bot/x.py"
MINIAPP = "aipager/miniapp/x.py"


def _tolerant(body: str) -> str:
    return f"async def f():\n    try:\n        {body}\n    except Exception:\n        pass\n"


# ===== criterion 7 — the shape of the sweep itself ======================

def test_exempt_is_exactly_the_three_files_d8_names():
    """Criterion 7. Every file REMOVED from ``_EXEMPT`` is a file the
    sweep now protects — that is the whole point of moving enforcement to
    the chokepoint, and a fourth entry silently gives one back."""
    assert EXEMPT_FILES == {"transport.py", "flood_budget.py", "observer.py"}


@pytest.mark.parametrize("family", ["delete_message", "send_chat_action"])
def test_the_two_families_the_old_sweep_missed_are_covered(family):
    """D-8. The old list omitted both, which is why the ``delete_message``
    at ``notify.py:1964`` and the typing bubble were invisible to CI."""
    assert family in GATED_FAMILIES


def test_the_sanctioned_receivers_are_the_documented_five():
    """P-1's condition: allowing the bare name ``bot`` is only safe
    because the constructor sweep forbids building a second one."""
    assert BOT_RECEIVERS == {"self._app.bot", "self.bot._app.bot", "bot",
                             "app.bot", "self._bot"}


# ===== criterion 6 — each synthetic offender is named ==================

def test_a_call_on_a_ptb_update_object_is_still_an_offender():
    """P-1's OTHER condition: ``message.reply_text`` must stay an
    offender outside the seam — that is the 8.17b sentinel contract the
    27 ``MUTED`` tests depend on, and losing it would silently undo it."""
    assert gated_family_offenders(BOT, _tolerant("await message.reply_text('hi')"))


def test_a_call_on_the_limiter_bound_bot_is_not_an_offender():
    """The flip side, and the point of the chokepoint: this one IS gated
    by construction, so flagging it would make the sweep unusable."""
    assert gated_family_offenders(
        BOT, _tolerant("await self._app.bot.send_message(chat_id=1, text='x')")) == []


def test_a_miniapp_call_on_a_stray_receiver_is_an_offender():
    """D-8: the walk covers ``aipager/miniapp/*.py`` too. Thirteen
    unguarded sends lived there on 0.7.12."""
    assert gated_family_offenders(
        MINIAPP, _tolerant("await other.send_message(chat_id=1, text='x')"))


def test_a_miniapp_call_on_the_bots_own_bot_is_fine():
    assert gated_family_offenders(
        MINIAPP,
        _tolerant("await self.bot._app.bot.send_message(chat_id=1, text='x')")) == []


@pytest.mark.parametrize("family", ["delete_message", "send_chat_action"])
def test_the_new_families_are_flagged_on_a_stray_receiver(family):
    assert gated_family_offenders(BOT, _tolerant(f"await query.{family}(1)"))


def test_an_exempt_file_is_not_swept():
    """The seam, the gate and the observer are the three files that must
    be able to call a bot directly."""
    assert gated_family_offenders(
        "aipager/bot/transport.py",
        _tolerant("await message.reply_text('hi')")) == []


# ===== the URL sweep ====================================================

def test_a_hardcoded_api_url_is_an_offender():
    assert telegram_url_offenders(
        BOT, "URL = 'https://api.telegram.org/bot%s/sendMessage'\n")


def test_an_f_string_api_url_is_an_offender():
    """R2 names f-strings explicitly: a formatted URL is the exact shape a
    second, ungated transport takes."""
    assert telegram_url_offenders(
        BOT, "def f(t):\n    return f'https://api.telegram.org/bot{t}/x'\n")


def test_the_allowlisted_diagnostic_callers_are_left_alone():
    """The carve-out is minimal and commented: these run out of the
    daemon process, with no limiter to route through."""
    assert telegram_url_offenders(
        "aipager/doctor.py", "U = 'https://api.telegram.org/bot'\n") == []


def test_a_docstring_mentioning_the_api_is_not_an_offender():
    """Docstrings are skipped, which is what keeps ``errors.py``'s
    explanatory prose from tripping the sweep on day one."""
    assert telegram_url_offenders(
        BOT, '"""We never call api.telegram.org directly."""\n') == []


# ===== the constructor sweep ===========================================

def test_a_second_application_builder_is_an_offender():
    assert bot_construction_offenders(BOT, "a = ApplicationBuilder().build()\n")


def test_a_hand_built_bot_is_an_offender():
    """A ``telegram.Bot`` built anywhere else has its own budget and its
    own ignorance of the mute."""
    assert bot_construction_offenders(BOT, "b = Bot(token='x')\n")


def test_the_observers_own_bot_is_allowed():
    assert bot_construction_offenders(
        "aipager/bot/observer.py", "b = Bot(token='x')\n") == []


def test_the_applications_own_builder_is_allowed():
    assert bot_construction_offenders(
        "aipager/bot/lifecycle.py", "a = ApplicationBuilder().build()\n") == []


# ===== the tolerance sweep (P-3, mandatory) ============================

def test_a_send_outside_a_try_is_an_offender():
    """P-3. A bare ``FloodMuted`` from the new gate propagating out of one
    of ``NotifyMixin.notify``'s ~20 direct sends would abort the rest of
    the turn — LOSING THE ANSWER THE GATE EXISTS TO PROTECT."""
    assert untolerated_send_offenders(
        BOT, "async def f():\n    await self._app.bot.send_message(chat_id=1, text='x')\n")


def test_a_send_inside_a_broad_except_is_tolerated():
    assert untolerated_send_offenders(
        BOT, _tolerant("await self._app.bot.send_message(chat_id=1, text='x')")) == []


def test_a_send_inside_a_flood_muted_arm_is_tolerated():
    """The narrow arm is the honest one, and must count."""
    src = ("async def f():\n    try:\n        await self._app.bot.send_message("
           "chat_id=1, text='x')\n    except FloodMuted:\n        pass\n")
    assert untolerated_send_offenders(BOT, src) == []


def test_a_send_inside_an_unrelated_except_is_still_an_offender():
    """Error guessing: ``except TimeoutError`` does not catch a
    ``FloodMuted``, so the turn still dies."""
    src = ("async def f():\n    try:\n        await self._app.bot.send_message("
           "chat_id=1, text='x')\n    except TimeoutError:\n        pass\n")
    assert untolerated_send_offenders(BOT, src)


# ===== the real tree ====================================================

def _sources(package: str):
    root = Path(__file__).resolve().parents[3] / "aipager" / package
    return [(f"aipager/{package}/{p.name}", p.read_text()) for p in
            sorted(root.glob("*.py"))]


@pytest.mark.parametrize("package", ["bot", "miniapp"])
def test_the_real_tree_has_no_ungated_send(package):
    """Criterion 6's last clause. This is the row that fails the day
    someone adds a send on a stray receiver."""
    offenders = [o for name, src in _sources(package)
                 for o in gated_family_offenders(name, src)]
    assert offenders == []


@pytest.mark.parametrize("package", ["bot", "miniapp"])
def test_the_real_tree_tolerates_a_flood_refusal_everywhere(package):
    offenders = [o for name, src in _sources(package)
                 for o in untolerated_send_offenders(name, src)]
    assert offenders == []


@pytest.mark.parametrize("package", ["bot", "miniapp"])
def test_the_real_tree_builds_no_second_bot(package):
    offenders = [o for name, src in _sources(package)
                 for o in bot_construction_offenders(name, src)]
    assert offenders == []


def test_the_whole_package_hardcodes_no_api_url():
    root = Path(__file__).resolve().parents[3] / "aipager"
    offenders = [o for p in sorted(root.rglob("*.py"))
                 for o in telegram_url_offenders(
                     str(p.relative_to(root.parent)), p.read_text())]
    assert offenders == []
