"""The display labels of a session's permission mode.

What is SHOWN (buttons, cards, /settings, the Mini App's chat lines) goes
through these keys so a later phase can translate it. What is TYPED and
STORED stays English and never passes through here: ``/mode auto|ask``,
callback data, ``new_session_mode`` values, ``skip_perms``. No code reads a
shown label back.

Sentences around a label stay literals with the label interpolated until
they are migrated themselves.
"""

from __future__ import annotations

from aipager.i18n import Msg

MODE_AUTO = Msg("mode.auto", "🤖 Auto", note="Permission mode: Claude runs tools without asking")
MODE_ASK = Msg("mode.ask", "💬 Ask", note="Permission mode: Claude asks before each tool")
MODE_ASK_INSTEAD = Msg("mode.ask_instead", "💬 Ask instead",
                       note="Button on the /new card: start the session in Ask mode")
MODE_AUTO_INSTEAD = Msg("mode.auto_instead", "🤖 Auto instead",
                        note="Button on the /new card: start the session in Auto mode")
MODE_ASK_FOR_YOU = Msg("mode.ask_for_you", "💬 Ask for you (🤖 Auto is for admins)",
                       note="The mode a member who is not an admin gets on /new")


def mode_label(auto: bool) -> Msg:
    """``MODE_AUTO`` or ``MODE_ASK``."""
    return MODE_AUTO if auto else MODE_ASK
