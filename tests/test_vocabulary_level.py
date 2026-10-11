""""Language level" is now "Vocabulary level" on every surface that shows
it (the multilingual design, decision 1.2), with every stored key, value
and callback unchanged; and the cadence help describes the clock."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from aipager import preferences
from aipager.bot import settings_menu

TITLE = "🎓 Vocabulary level"


def _texts(kb):
    return [b.text for row in kb.inline_keyboard for b in row]


def _cbs(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row]


def test_the_root_row_and_section_say_vocabulary(tmp_path):
    _text, kb = settings_menu.render_settings_root(555)
    rows = [t for t in _texts(kb) if t.startswith("🎓")]
    assert rows == [f"{TITLE}: Don't apply any rule"]
    text, kb = settings_menu.render_settings_section(555, "level")
    assert text.startswith(TITLE + "\n\n")
    assert _cbs(kb)[:4] == ["_:set:level:none", "_:set:level:simple", "_:set:level:normal",
                            "_:set:level:advanced"]


def test_the_schema_and_its_cached_copy_say_vocabulary():
    level = [s for s in settings_menu.settings_schema() if s["section"] == "level"]
    assert level[0]["title"] == TITLE and level[0]["field"] == "language_level"
    from aipager.bot import session_parity
    assert session_parity._SCHEMA["level"]["title"] == TITLE


def test_the_stored_key_is_unchanged():
    preferences.set_preference(555, "language_level", "simple")
    assert preferences.get_preferences(555).language_level == "simple"
    raw = preferences._ensure_loaded()["555"]
    assert raw == {"language_level": "simple"}


def test_the_mini_app_mirror_says_vocabulary(run_async):
    from aipager.miniapp.server import MiniAppServer
    srv = MiniAppServer.__new__(MiniAppServer)
    srv.bot = MagicMock()
    srv.bot._app.bot.send_message = AsyncMock()
    run_async(srv._mirror_preference_change(-1001, 1, "language_level", "normal"))
    text = srv.bot._app.bot.send_message.await_args.kwargs["text"]
    assert text == f"⚙️ {TITLE} → Normal (changed from the Mini App)"


def test_language_level_is_shown_nowhere():
    root = Path(__file__).resolve().parent.parent
    for path in list((root / "aipager").rglob("*.py")) + [root / "docs" / "commands.md"]:
        assert "Language level" not in path.read_text(encoding="utf-8"), path


def test_cadence_help_describes_the_clock():
    help_on = settings_menu._OPTION_HELP[("cadence", True)]
    assert "minutes" not in help_on and "a minute" not in help_on
    assert "30 s steps" in help_on and "every 30 s" in help_on
    intro = settings_menu._SECTION_INTRO["cadence"]
    assert "switches to minutes" not in intro and "30 s steps" in intro
