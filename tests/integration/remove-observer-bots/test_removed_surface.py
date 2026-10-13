"""What must no longer exist: the observer module, the setting, the
parser, the bot attribute and the Mini App label constant; and the
flood-gate sweeps no longer exempt the deleted module's filename.

design.md success criteria 3 and 9; entrypoints.md "Removed" and
``tests/sweep_rules.py``.
"""

from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys

import pytest

from tests import i18n_tool, sweep_rules


# ---- the module and the setting ----------------------------------------------

def test_the_observer_module_cannot_be_found():
    assert importlib.util.find_spec("aipager.bot.observer") is None


def test_the_observer_module_cannot_be_imported():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("aipager.bot.observer")


def test_config_has_no_observer_bots_setting():
    import aipager.config as config
    assert not hasattr(config, "OBSERVER_BOTS")


def test_config_has_no_observer_bots_parser():
    import aipager.config as config
    assert not hasattr(config, "_parse_observer_bots")


def test_the_mini_app_text_has_no_observers_feature_label():
    from aipager.miniapp import ui_text
    assert not hasattr(ui_text, "REPORT_FEATURE_OBSERVERS")


def test_a_fresh_bot_has_no_observers_attribute(mk_bot, monkeypatch):
    monkeypatch.setenv("OBSERVER_BOTS", "123456:AAx:42")
    bot = mk_bot()
    assert "observers" not in vars(bot)


def test_a_fresh_bot_class_has_no_observers_attribute():
    from aipager.bot import TelegramBot
    assert not hasattr(TelegramBot, "observers")


def _import_config_in_a_child(tmp_path, value):
    """Import ``aipager.config`` in a fresh interpreter with the variable
    set before import (a throwaway HOME, so no real config is read)."""
    env = i18n_tool.isolated_env(tmp_path)
    env["OBSERVER_BOTS"] = value
    code = ("import aipager.config as c, sys; "
            "sys.stdout.write(repr((hasattr(c, 'OBSERVER_BOTS'), "
            "hasattr(c, '_parse_observer_bots'))))")
    return subprocess.run([sys.executable, "-c", code], env=env, cwd=str(tmp_path),
                          capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("value", ["123456:AAx:42", "garbage", ""],
                         ids=["token_shaped", "malformed", "empty"])
def test_config_imported_with_the_variable_set_still_has_no_setting(tmp_path, value):
    proc = _import_config_in_a_child(tmp_path, value)
    assert (proc.returncode, proc.stdout) == (0, "(False, False)")


def test_importing_config_with_the_variable_set_prints_nothing(tmp_path):
    proc = _import_config_in_a_child(tmp_path, "123456:AAcanaryTOKEN:42")
    if "AAcanaryTOKEN" in proc.stderr or "OBSERVER" in proc.stderr:
        pytest.fail("config import mentioned the variable", pytrace=False)


# ---- the flood-gate sweeps police a would-be observer.py -----------------------

def test_the_exempt_set_is_exactly_the_seam_and_the_gate():
    assert sweep_rules.EXEMPT_FILES == {"transport.py", "flood_budget.py"}


def test_observer_py_is_not_on_the_url_allowlist():
    assert "observer.py" not in sweep_rules.URL_ALLOWLIST


def test_a_bot_built_in_a_new_observer_module_is_an_offender():
    assert sweep_rules.bot_construction_offenders(
        "aipager/bot/observer.py", "b = Bot(token='x')\n") != []


def test_a_telegram_url_in_a_new_observer_module_is_an_offender():
    assert sweep_rules.telegram_url_offenders(
        "aipager/bot/observer.py", "u = 'https://api.telegram.org/bot'\n") != []


def test_a_raw_send_in_a_new_observer_module_is_an_offender():
    src = ("async def push(other_bot):\n"
           "    await other_bot.send_message(chat_id=1, text='x')\n")
    assert sweep_rules.gated_family_offenders("aipager/bot/observer.py", src) != []

