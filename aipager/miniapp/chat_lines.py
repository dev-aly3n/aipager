"""The lines the Mini App posts into a chat after an action there.

Each one is a whole sentence in the language of the chat it is posted
into (the caller passes ``i18n.for_chat(chat_id)``), whoever acted: a
Russian viewer's action in a Persian group posts Persian. Plain text
(the mirrors are sent without a parse mode). English is exactly the line
the Mini App always posted.

Session names, folders and model ids go in as parameters, so a
right-to-left chat isolates them whole; a setting's title and option
come from the settings schema already rendered for the chat (``Safe``,
inserted as they are).
"""

from __future__ import annotations

from aipager.i18n import Msg, Plural, Safe
from aipager.miniapp.phrase import say
from aipager.state import STOP_END_JOB, STOP_KILL_AGENTS

__all__ = ["compacted", "deleted", "folder_created", "killed", "model_switched",
           "perms_switched", "queue_cleared", "renamed", "restarted", "resumed",
           "session_created", "session_setting_changed", "setting_changed", "stopped"]

_CREATED = Msg("app.mirror.created", "🚀 {session} created from the Mini App · {mode}",
               eg={"session": "x1", "mode": "Ask mode"},
               note="Posted in the chat after a Mini App action. {mode} is the permission mode's "
                    "label: its emoji, its word and the word for mode")
_CREATED_IN = Msg("app.mirror.created_in",
                  "🚀 {session} created from the Mini App · {mode}\n📁 {folder}",
                  eg={"session": "x1", "mode": "Ask mode", "folder": "/home/op/web"},
                  note="Posted in the chat after a Mini App action. {mode} is the permission "
                       "mode's label (emoji, word, the word for mode); the second line is the "
                       "folder it runs in")
_FOLDER = Msg("app.mirror.folder", "📁 New folder created from the Mini App:\n{path}",
              eg={"path": "/home/op/web"}, note="Posted in the chat after a Mini App action")
_SETTING = Msg("app.mirror.setting", "⚙️ {setting} → {value} (changed from the Mini App)",
               eg={"setting": "📏 Answer length", "value": "Short"},
               note="Posted in the chat after a Mini App action: a chat setting changed to a "
                    "new value")
_SESSION_SETTING = Msg("app.mirror.session_setting",
                       "⚙️ {setting} → {value} for session {session} "
                       "(changed from the Mini App)",
                       eg={"setting": "📏 Answer length", "value": "Short", "session": "x1"},
                       note="Posted in the chat after a Mini App action: one session's own "
                            "setting")
_SESSION_SETTING_RESET = Msg("app.mirror.session_setting_reset",
                             "⚙️ {setting} reset to default ({value}) for session {session} "
                             "(changed from the Mini App)",
                             eg={"setting": "📏 Answer length", "value": "Short",
                                 "session": "x1"},
                             note="Posted in the chat after a Mini App action: a session "
                                  "follows the chat's setting again")
_TO_AUTO = Msg("app.mirror.to_auto", "🤖 {session} switched to Auto mode from the Mini App",
               eg={"session": "x1"},
               note="Posted in the chat after a Mini App action. Auto is the permission mode "
                    "(as in /mode)")
_TO_FULL = Msg("app.mirror.to_full",
               "🔓 {session} switched to Full access mode from the Mini App",
               eg={"session": "x1"},
               note="Posted in the chat after a Mini App action. Full access is the permission "
                    "mode (as in /mode)")
_MODE_SENT = Msg("app.mirror.mode_sent",
                 "⚙️ Switching {session} to {mode} from the Mini App without a restart.",
                 eg={"session": "x1", "mode": "Auto mode"},
                 note="Posted in the chat after a Mini App action: an Ask/Auto switch was sent; "
                      "the line changes when Claude Code confirms it. {mode} is the mode's "
                      "label, which already says mode")
_TO_ASK = Msg("app.mirror.to_ask", "💬 {session} switched to Ask mode from the Mini App",
              eg={"session": "x1"},
              note="Posted in the chat after a Mini App action. Ask is the permission mode (as "
                   "in /mode)")
_CLEARED = Plural("app.mirror.cleared",
                  one="🧹 Cleared {n} queued message for [{session}] from the Mini App",
                  other="🧹 Cleared {n} queued messages for [{session}] from the Mini App",
                  eg={"session": "x1"}, note="Posted in the chat after a Mini App action")
_COMPACTED = Msg("app.mirror.compacted", "📦 Compact triggered for [{session}] from the Mini App",
                 eg={"session": "x1"},
                 note="Posted in the chat after a Mini App action. Compact is /compact (shrink "
                      "the context)")
_MODEL = Msg("app.mirror.model", "🔄 [{session}] → {model} from the Mini App",
             eg={"session": "x1", "model": "claude-opus-5"},
             note="Posted in the chat after a Mini App action: the session's model was switched")
_RESTARTED = Msg("app.mirror.restarted", "🔁 Restarted [{session}] from the Mini App",
                 eg={"session": "x1"}, note="Posted in the chat after a Mini App action")
_RENAMED = Msg("app.mirror.renamed", "✏️ [{old}] renamed to [{new}] from the Mini App",
               eg={"old": "x1", "new": "x2"}, note="Posted in the chat after a Mini App action")
_STOPPED = Msg("app.mirror.stopped", "⏹️ Stopped [{session}] from the Mini App",
               eg={"session": "x1"}, note="Posted in the chat after a Mini App action")
_STOPPED_DROPPED = Plural("app.mirror.stopped_dropped",
                          one="⏹️ Stopped [{session}] from the Mini App "
                              "({n} queued message discarded)",
                          other="⏹️ Stopped [{session}] from the Mini App "
                                "({n} queued messages discarded)",
                          eg={"session": "x1"}, note="Posted in the chat after a Mini App action")
_KILLED = Msg("app.mirror.killed", "💀 Killed [{session}] from the Mini App",
              eg={"session": "x1"},
              note="Posted in the chat after a Mini App action. Killed: its process was ended")
_RESUMED = Msg("app.mirror.resumed", "♻️ Resumed [{session}] from the Mini App",
               eg={"session": "x1"}, note="Posted in the chat after a Mini App action")
_RESUMED_IN_AUTO = Msg("app.mirror.resumed_in_auto",
                       "♻️ Resumed [{session}] from the Mini App in Auto mode: {reason}",
                       eg={"session": "x1", "reason": "Full access mode needs an admin."},
                       note="Posted in the chat after a Mini App action. Auto is the "
                            "permission mode; the reason is a whole sentence of its own")
_DELETED = Msg("app.mirror.deleted", "🗑️ Deleted [{session}] from the Mini App",
               eg={"session": "x1"}, note="Posted in the chat after a Mini App action")


def session_created(tr, label: str, mode: str, cwd: str) -> str:
    from aipager.bot.mode_labels import mode_label
    mode = Safe(tr.text(mode_label(mode)))
    if cwd:
        return tr.text(_CREATED_IN, session=label, mode=mode, folder=cwd)
    return tr.text(_CREATED, session=label, mode=mode)


def folder_created(tr, path: str) -> str:
    return tr.text(_FOLDER, path=path)


def _setting(tr, field: str, value) -> tuple:
    """A setting's title and the option's label, from the schema in
    *tr*'s language; an unknown field or value shows its id."""
    from aipager.bot.appearance import appearance_schema
    from aipager.bot.settings_menu import settings_schema
    label, shown = field, value
    for section in [*settings_schema(tr=tr), *appearance_schema(tr=tr)]:
        if section["field"] != field:
            continue
        label = Safe(section["title"])
        for option in section["options"]:
            if option["value"] == value:
                shown = Safe(option["label"])
                break
        break
    return label, (shown if isinstance(shown, Safe) else str(shown))


def setting_changed(tr, field: str, value) -> str:
    label, shown = _setting(tr, field, value)
    return tr.text(_SETTING, setting=label, value=shown)


def session_setting_changed(tr, label: str, field: str, value, reset: bool) -> str:
    title, shown = _setting(tr, field, value)
    msg = _SESSION_SETTING_RESET if reset else _SESSION_SETTING
    return tr.text(msg, setting=title, value=shown, session=label)


def perms_switched(tr, label: str, mode: str) -> str:
    """A switch that restarted the session (into or out of Full access)."""
    msg = {"ask": _TO_ASK, "auto": _TO_AUTO, "full": _TO_FULL}[mode]
    return tr.text(msg, session=label)


def mode_sent(tr, label: str, mode: str) -> str:
    """An Ask/Auto switch sent in place, not confirmed yet."""
    from aipager.bot.mode_labels import mode_label
    return tr.text(_MODE_SENT, session=label, mode=Safe(tr.text(mode_label(mode))))


def queue_cleared(tr, label: str, dropped: int) -> str:
    return tr.n(_CLEARED, dropped, kind="text", session=label)


def compacted(tr, label: str) -> str:
    return tr.text(_COMPACTED, session=label)


def model_switched(tr, label: str, model: str) -> str:
    return tr.text(_MODEL, session=label, model=str(model))


def restarted(tr, label: str) -> str:
    return tr.text(_RESTARTED, session=label)


def renamed(tr, previous_label: str, new_label: str) -> str:
    return tr.text(_RENAMED, old=previous_label, new=new_label)


def stopped(tr, label: str, dropped: int, *, outcome=None) -> str:
    """The mirror of a Mini App Stop. One that ended a session's
    background agents says so as every Stop surface does; one that ended
    a job with no keys adds the shells left running (roadmap 8.121 F5,
    ``aipager.bot.stop_text``)."""
    if dropped:
        first = tr.n(_STOPPED_DROPPED, dropped, kind="text", session=label)
    else:
        first = tr.text(_STOPPED, session=label)
    if outcome is not None and getattr(outcome, "mode", "") in (STOP_KILL_AGENTS,
                                                                 STOP_END_JOB):
        from aipager.bot import stop_text
        return stop_text.stopped_line(tr, outcome, html=False, first=first)
    return first


def killed(tr, label: str) -> str:
    return tr.text(_KILLED, session=label)


def resumed(tr, label: str, in_auto: bool) -> str:
    if in_auto:
        from aipager.bot.transport import RESUME_FULL_NEEDS_ADMIN
        return tr.text(_RESUMED_IN_AUTO, session=label,
                       reason=say(tr, RESUME_FULL_NEEDS_ADMIN))
    return tr.text(_RESUMED, session=label)


def deleted(tr, label: str) -> str:
    return tr.text(_DELETED, session=label)
