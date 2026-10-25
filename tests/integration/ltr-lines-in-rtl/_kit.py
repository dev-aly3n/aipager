"""Shared helpers for the ltr-lines-in-rtl black-box tests.

Two things live here:

- the reading-order oracle of design.md "Success criteria" (judged with
  tests/bidi_uba.py): ``para=1`` models Telegram Desktop / Web A / Web K
  laying out a block of an ``is_rtl`` rich message; ``para=None`` (P2-P3)
  models iOS / Android, where each block takes its direction from its own
  first strong letter;
- ``render_cases()``: a deterministic set of renders through the public
  entry points only (entrypoints.md), recorded once on main 0412268c into
  ``main_bytes_0412268.json`` by running this same file under main's tree
  in a separate interpreter. It renders only what must not change: every
  language's base ``Tr``, ``directed(None)`` / ``directed(False)``, and
  fa/ar in both directions.

Test sources hold no raw direction characters: every one is an escape.
"""

from __future__ import annotations

import re
import time

from tests import bidi_uba

LRM, RLM = "\u200e", "\u200f"
LRI, RLI, FSI, PDI = "\u2066", "\u2067", "\u2068", "\u2069"
CONTROLS = frozenset((LRM, RLM, LRI, RLI, FSI, PDI, "\u061c", "\u200c", "\u200d"))
MARKS = "\u200e\u200f"
PAIR = re.compile("[\u200e\u200f]{2}")
CODE = re.compile(r"<code\b[^>]*>.*?</code>|<pre\b[^>]*>.*?</pre>|```.*?```|`[^`\n]*`",
                  re.S)
TAG = re.compile(r"<[^>]+>")
INVISIBLE = re.compile("[\u200e\u200f\u2066-\u2069\u061c]")

#: Persian words used as Claude's text, an agent's activity and a session
#: name. No digits inside (a digit inside an RTL run is a nested level the
#: phrase-reversal oracle does not model).
FA_RESTAURANT = "رستوران ماهی سد"
FA_PARK = "پارک جنگلی"
FA_NAME = "سفر"
FA_ANSWER = ("چهار تا ایجنت "
             "فرستادم که همزمان "
             "کار می\u200cکنن.")
FA_PROSE = "ابتدا فایل را می\u200cخوانم."
AR_ANSWER = "دعني أقرأ الملف أولا."
EN_ANSWER = "Let me read the file first."


# ---- the reading-order oracle ---------------------------------------------------------

def is_rtl_letter(ch: str) -> bool:
    return bidi_uba.has_rtl_letter(ch)


def _rtl_isolates(line: str) -> list[bool]:
    """For each index: inside an isolate whose content is right to left
    (RLI, or FSI whose first strong letter is RTL)."""
    inside = [False] * len(line)
    stack: list[int] = []
    pairs: list[tuple[int, int]] = []
    for i, ch in enumerate(line):
        if ch in (LRI, RLI, FSI):
            stack.append(i)
        elif ch == PDI and stack:
            pairs.append((stack.pop(), i))
    for start, end in pairs:
        opener, body = line[start], line[start + 1:end]
        rtl = opener == RLI or (opener == FSI and bidi_uba.paragraph_level(body) == 1)
        if rtl:
            for k in range(start + 1, end):
                inside[k] = True
    return inside


def _counted(line: str, lv: list[int | None], i: int) -> bool:
    ch = line[i]
    return lv[i] is not None and not ch.isspace() and ch not in CONTROLS


def order_problems(line: str, para: int | None) -> list[str]:
    """Why *line* (one shown line) does NOT read in its logical order under
    paragraph level *para*; empty when it does.

    Reads in order (design.md "Success criteria"): every shown character
    that is not an RTL letter, nor inside an isolated RTL parameter,
    resolves to an even level; and the visual order of the non-space,
    non-control characters equals the logical order with each RTL phrase
    reversed in place."""
    lv = bidi_uba.levels(line, para)
    rtl_iso = _rtl_isolates(line)
    out = []
    for i, ch in enumerate(line):
        if (_counted(line, lv, i) and not rtl_iso[i] and not is_rtl_letter(ch)
                and lv[i] & 1):
            out.append(f"{ch!r} at {i} has odd level {lv[i]}")
    logical = [i for i in range(len(line)) if _counted(line, lv, i)]
    expected: list[int] = []
    run: list[int] = []
    for i in logical:
        if lv[i] & 1:
            run.append(i)
        else:
            expected += reversed(run)
            run = []
            expected.append(i)
    expected += reversed(run)
    visual = [i for i in bidi_uba.visual_order(line, para) if _counted(line, lv, i)]
    if visual != expected:
        out.append("visual " + "".join(line[i] for i in visual)
                   + " != logical " + "".join(line[i] for i in expected))
    return out


def visual_text(line: str, para: int | None) -> str:
    """The shown characters of *line* from left to right, controls and
    spaces dropped."""
    lv = bidi_uba.levels(line, para)
    return "".join(line[i] for i in bidi_uba.visual_order(line, para)
                   if _counted(line, lv, i))


_BLOCK_MARKER = re.compile(r"^(?:- |#{1,6} |> |\d+\. )")


def lines(text: str) -> list[str]:
    """The non-blank shown lines of a rich message (tags, backticks,
    asterisks and escapes removed; a leading Markdown block marker such as
    ``- `` dropped, since the app draws the bullet itself on the block's
    side; the ``---`` divider dropped, it is a rule, not text)."""
    out = []
    for ln in bidi_uba.shown_lines(text):
        ln = _BLOCK_MARKER.sub("", ln)
        if ln.strip() and ln.strip() != "---":
            out.append(ln)
    return out


def line_with(text: str, needle: str, *, without: str = "\x00") -> str:
    """The one shown line of *text* holding *needle* and not *without*
    (fails otherwise)."""
    found = [ln for ln in lines(text) if needle in ln and without not in ln]
    assert len(found) == 1, (needle, found, text)
    return found[0]


def strip_marks(text: str) -> str:
    for ch in (LRM, FSI, PDI):
        text = text.replace(ch, "")
    return text


def outside_code(text: str) -> str:
    return CODE.sub("", text)


def invisible_in_code_or_tags(text: str) -> list[str]:
    """Code elements, backtick spans and tags holding a direction control."""
    found = [m.group(0) for m in CODE.finditer(text) if INVISIBLE.search(m.group(0))]
    found += [m.group(0) for m in TAG.finditer(text) if INVISIBLE.search(m.group(0))]
    return found


def marked_divider_or_blank(text: str) -> list[str]:
    """Lines that are a divider or blank apart from a direction control."""
    bad = []
    for ln in text.split("\n"):
        bare = INVISIBLE.sub("", ln)
        if ln != bare and bare.strip() in ("", "---"):
            bad.append(ln)
    return bad


# ---- deterministic renders through the entry points ---------------------------------

def _views():
    from aipager.bot import agents_line as al
    A, V = al.AgentRow, al.BlockView
    return {
        "operator": V(agents=(
            A("general-purpose", False,
              description="Geocode remaining villages and rivers, reverse-geocode"),
            A("general-purpose", False, description="Test shab.ir city pages without dates"),
            A("general-purpose", False, description="Fetch OSM bbox data and list shops"),
            A("general-purpose", False, description="Compute sunrise/sunset for Shirgah"),
            A("general-purpose", False, tool="WebSearch", tool_arg=FA_RESTAURANT, parent=0),
            A("general-purpose", False, tool="WebSearch", tool_arg=FA_PARK, parent=0),
            A("general-purpose", False),
            A("general-purpose", False))),
        "prose_fa": V(agents=(A("general-purpose", False, prose=FA_RESTAURANT + "."),)),
        "one_done": V(agents=(A("Explore", True, 42),), agents_done_seconds=42),
        "mixed": V(agents=(A("Explore", True, 42),
                           A("pipeline-runner", False, description="Running the full suite")),
                   shells_running=1),
        "shells_running": V(shells_running=1),
        "shells_two": V(shells_running=2),
        "shells_settled": V(shells_finished=1, shells_finished_seconds=7985,
                            shells_stopped=1, shells_stopped_seconds=250),
        "tool_read": V(agents=(A("Explore", False, tool="Read", tool_arg="state.py"),)),
        "underscore": V(agents=(A("general_purpose", False, prose="_why_ it *broke*"),)),
    }


def card_session(label: str = "x1", commentary: str = "", waiting_agents: int = 0):
    """A busy turn with every code-built card row kind the operator's card
    showed: tool rows, a failed one, a settled agent, live agents (one
    nested), all relative to now so the clocks are stable."""
    from aipager.state import SessionRegistry, Status
    now = time.monotonic()
    reg = SessionRegistry()
    s = reg.get_or_create("claude-" + ("x1" if label == FA_NAME else label))
    s.label = label
    s.status = Status.BUSY
    s.scope_chat_id, s.scope_kind = 4242, "dm"
    s.busy_started_at = now - 125
    s.cost_baseline, s.last_cost_usd = 0.0, 1.21
    s.tool_history = [
        ("Read: /home/alice/a.md", True),
        ("Read: /home/alice/" + FA_NAME + ".md", True),
        ("SendMessage", True),
        ("Bash: npm run build", "failed"),
        ("\U0001f916 general-purpose · 43 tool calls · 17:38", True),
        ("\U0001f916 general-purpose", False),
        ("\U0001f916 general-purpose › general-purpose", False),
    ]
    s.active_subagents = {
        "a1": {"type": "general-purpose",
               "activity": "Bash: Dump listing details and reviews",
               "started_at": now - 38 * 60 - 30, "history_idx": 5, "tools": []},
        "a2": {"type": "general-purpose",
               "card_label": "general-purpose › general-purpose",
               "activity": "Bash: Search for local dairy, products, bread near the village",
               "started_at": now - 37 * 60, "history_idx": 6, "tools": []},
    }
    for k in range(waiting_agents):
        s.active_subagents[f"w{k}"] = {"type": "general-purpose", "started_at": now - 5}
    s.stream_commentary = [(0, commentary)] if commentary else []
    return s


def shift_clocks(s, seconds: float) -> None:
    """Move the clocks :func:`card_session` set on by *seconds*, as if the
    turn and its agents had started that much later: two cards drawn a
    second apart on a slow runner then show the same times."""
    s.busy_started_at += seconds
    for info in s.active_subagents.values():
        info["started_at"] += seconds


def render_cases() -> dict[str, str]:
    """Every output that must stay byte for byte as on main: id -> text."""
    from aipager import i18n
    from aipager.bot import agents_line, animation, held_message
    from aipager.bot.notify import finished_line
    from aipager.i18n import locales

    out: dict[str, str] = {}
    codes = sorted(locales.LOCALES)
    views = _views()
    for code in codes:
        base = i18n.get(code)
        trs = {"base": base, "none": base.directed(None), "false": base.directed(False)}
        if base.rtl:
            trs["true"] = base.directed(True)
        for tname, tr in trs.items():
            key = f"{code}/{tname}"
            for vname, view in views.items():
                out[f"{key}/block/{vname}"] = agents_line.render_block(view, tr=tr)
            for lname, lead in (("ascii", "\U0001f4ac **x1**"),
                                ("fa", "\U0001f4ac **" + FA_NAME + "**")):
                out[f"{key}/finished/{lname}"] = str(finished_line(
                    tr, lead, "md", seconds=250, added=10, removed=5))
            out[f"{key}/late"] = str(held_message.late_marker_text(tr, 180))
            out[f"{key}/fold"] = str(tr.n(animation.FOLD, 5, kind="md"))
    for code in codes:
        base = i18n.get(code)
        for commentary, cname in (("", "none"), (EN_ANSWER, "en")):
            for label in ("x1", FA_NAME):
                for waiting, wname in ((False, "busy"), (True, "waiting")):
                    s = card_session(label, commentary, waiting_agents=3 if waiting else 0)
                    text, _ok = animation.build_stream_card_ex(
                        s, "Working", tr=base, rtl=None, waiting=waiting)
                    out[f"{code}/card/{cname}/{label == FA_NAME}/{wname}"] = text
                    text, _ok = animation.build_stream_card_ex(
                        s, "Working", tr=base, rtl=False, waiting=waiting)
                    out[f"{code}/card-ltr/{cname}/{label == FA_NAME}/{wname}"] = text
    for code in ("fa", "ar"):
        base = i18n.get(code)
        for label in ("x1", FA_NAME):
            s = card_session(label, FA_PROSE)
            text, _ok = animation.build_stream_card_ex(s, "Working", tr=base, rtl=True)
            out[f"{code}/card-rtl/{label == FA_NAME}"] = text
    return out


if __name__ == "__main__":     # recording, run under main's tree only
    import json
    import sys
    json.dump(render_cases(), sys.stdout, ensure_ascii=True, indent=0, sort_keys=True)
