"""aipager.shell_glob: the floor's bash-faithful glob and brace check.

Brace results and glob verdicts below were checked against bash 5.2 (a
throwaway HOME, ``set -f`` for brace expansion, ``shopt -s nullglob`` for
globs). tests/golden/shell_glob_bash.json holds a fixed set of spellings
with real bash's verdict, so no test here runs bash.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from aipager import safety
from aipager import shell_glob as g
from aipager.safety import DENY_BASH_PATTERNS, bash_violation

GOLDEN = Path(__file__).resolve().parent / "golden" / "shell_glob_bash.json"
UNLIT = {ord(k): v for k, v in g._UNLIT.items()}


def _word(text: str) -> str:
    """*text* as one word from the lexer."""
    found = g.words(text)
    assert len(found) == 1, found
    return found[0]


def _expand(text: str) -> set[str]:
    return {r.translate(UNLIT) for r in g.expand_braces(_word(text))}


def _reach(path: str, dotglob: bool = False) -> str:
    return g.word_reach(_word(path), dotglob)


# ----- the lexer -----

def test_words_keeps_only_words_that_can_expand():
    assert g.words("ls -la src/*.py README.md") == ["src/*.py"]
    assert g.words("cd ..") == [".."]


def test_a_quoted_special_character_is_not_syntax():
    w = _word("'*'x?")
    assert "*" not in w and w.translate(UNLIT) == "*x?"
    assert _reach("~/.config/'*'?/x") == g.CLEAR
    assert not g.command_reaches("cat ~/.config/'*'/x")
    assert _reach("~/.config/*/x") == g.REACH


def test_an_escaped_bracket_is_a_member():
    # aip[a\]]ger: the bracket holds a and ], so it matches aipager
    assert _reach(r"~/.config/aip[a\]]ger/x") == g.REACH


def test_double_quotes_keep_their_escapes_and_hide_expansions():
    assert _word('"a\\$b*"x?').translate(UNLIT) == "a$b*x?"
    assert g.OPAQUE in _word('"$HOME"/x*')


def test_a_quoted_dot_step_still_climbs():
    # a quoted .. never makes a brace sequence, but it is still a path step
    assert g.command_reaches('cat ~/.config/nvim/".."/[a]ipager/x')
    assert g.command_reaches("cat ~/.config/nvim/'.'/../[a]ipager/x")
    # also once braces were expanded
    assert g.command_reaches('cat ~/.config/nvim/".."/{[a]ipager,x}/y')


def test_ansi_c_quoting_is_decoded():
    assert _reach(r"~/.config/$'\x61'ipage?/x") == g.REACH


def test_a_line_continued_inside_a_word_is_joined():
    assert _reach("~/.config/aip\\\nage?/x") == g.REACH


def test_a_variable_or_substitution_is_opaque():
    assert _word("~/.config/$X/*").count(g.OPAQUE) == 1
    assert _word("~/.config/${X#a}/*").count(g.OPAQUE) == 1
    assert _word('"${X:-a}"/*').count(g.OPAQUE) == 1


def test_a_default_value_is_a_second_reading():
    # unquoted, ${x:-word} may become word, and that is globbed
    assert g.words("~/.config/${X:-a}/*") == ["~/.config/" + g.OPAQUE + "/*",
                                              "~/.config/a/*"]
    assert g.command_reaches("echo ${x:-~/.config/[a]ipager/aipager.yaml}")
    assert g.command_reaches("cat ~/.config/${x:=[a]ipager}/aipager.yaml")
    assert g.command_reaches("cat ~/.config/${x:-${y:-[a]ipager}}/aipager.yaml")
    # quoted, it is not globbed, and a pattern is never a value; but every
    # operation's text is read as words of its own all the same (review-7
    # rev-iter7-003: a reading may only add words). Only the unquoted
    # value goes into the word's second reading.
    assert "~/.config/[a]ipager/x" in g.words(
        'echo "${x:-~/.config/[a]ipager/x}"')
    assert "~/.config/[a]ipager/x" in g.words(
        "echo ${x#~/.config/[a]ipager/x}")
    assert g.words("echo ${x#a}/*") == [g.OPAQUE + "/*"]


def test_commands_inside_an_expansion_are_read():
    assert g.command_reaches("echo ${x:-$(cat ~/.config/[a]ipager/x)}")
    assert g.command_reaches('echo "${x:-`cat ~/.config/[a]ipager/x`}"')
    assert g.command_reaches("echo $(( $(cat ~/.config/[a]ipager/x | wc -l) ))")
    assert g.command_reaches("cat <<E\n$(cat ~/.config/[a]ipager/x)\nE")


def test_a_command_substitution_is_read_as_more_words():
    assert g.command_reaches("echo $(cat ~/.config/[a]ipager/x)")
    assert g.command_reaches("echo `cat ~/.config/[a]ipager/x`")
    # the closing backtick does not glue an unknown value to the word
    assert g.command_reaches("echo `ls ~/.config/[a]ipager`")
    # a substitution inside a word stays part of it
    assert g.command_reaches("cat ~/.config/`echo x`/../aipager/x")
    assert g.command_reaches("cat ~/.config/$(echo x)/../aipager/x")


def test_a_group_glued_to_a_word_stays_in_it():
    assert _word("~/.config/@(aipager|x)/y") == "~/.config/@(aipager|x)/y"
    assert _reach("~/.config/@(aipager|x)/y") == g.REACH
    # an array assignment's ( splits words
    assert g.words("a=(~/.config/nvim/*.lua)") == ["~/.config/nvim/*.lua"]


# ----- where bash's parser ends things (checked in bash 5.2) -----

T = "cat ~/.config/[a]ipager/aipager.yaml"


@pytest.mark.parametrize("cmd", [
    # review-5 rev-iter5-001: a bare { inside ${...} does not nest
    "x=a; echo ${x#{} ; cat ~/.config/[a]ipager/aipager.yaml",
    "echo ${#{} ; cat ~/.config/[a]ipager/aipager.yaml",
    'echo "${x/{/}" ; cat ~/.local/share/[a]ipager/x',
    "echo ${x:-{} && cat ~/.config/aip[a\\]]ger/aipager.yaml",
    "echo hi # \"it's\" ${\ncat ~/.config/[[:a]ipager/aipager.yaml",
])
def test_the_review_5_commands_are_blocked(cmd):
    assert g.command_reaches(cmd)
    assert bash_violation(cmd, DENY_BASH_PATTERNS) == safety.BASH_REASON


@pytest.mark.parametrize("pre", [
    "echo ${x#'}'} ; ", "echo \"${x#'}'}\" ; ", 'echo ${x:-"}"} ; ',
    "echo ${x:-$(echo })} ; ", "echo ${x:-`echo }`} ; ",
    "echo ${x:-${y:-}}} ; ", "echo ${x/\\}/} ; ", "echo ${x:-$'\\''} ; ",
])
def test_a_dollar_brace_ends_where_bash_ends_it(pre):
    words, sure = g.lex(pre + T)
    assert sure
    assert g.command_reaches(pre + T)


def test_only_a_dollar_brace_nests():
    assert g.lex("echo ${x#{}")[1]
    assert g.lex("echo ${x:-${y}}")[1]
    assert not g.lex("echo ${x:-${y}")[1]


@pytest.mark.parametrize("cmd", [
    "echo ${x", "echo $(x", "echo `x", "echo 'x", 'echo "x', "echo $'x",
    "echo $'x\\'", "cat <<E\nx", "cat <<\nx", "cat << ; ls", "echo @(x", "echo $((1",
    "echo $[1", "echo ${x:-'}", "echo \"${x:-'}\"", "cat <<'E\nx\nE",
    # in double quotes $' is a $ and a quote
    "echo \"${x:-$'\\''}\"",
])
def test_something_left_open_makes_the_read_unsure(cmd):
    assert not g.lex(cmd)[1]


@pytest.mark.parametrize("cmd", [
    "echo ${x}", "echo $(x)", "echo `x`", "echo 'x'", 'echo "x"',
    "echo $'x\\''", "cat <<E\nx\nE", "echo @(x)", "echo $((1))", "echo $[1]",
    "cat <<E\nx\nE\n", "echo a # 'b", "cat <<<'x'",
])
def test_a_closed_command_reads_sure(cmd):
    assert g.lex(cmd)[1]


def test_an_unsure_read_refuses_a_command_that_could_name_a_folder():
    assert g.command_reaches("echo ${x ; cat ~/.config/nvim/init.lua")
    assert g.command_reaches("echo $'\\x2econfig' ${x")
    assert not g.command_reaches("echo ${x ; cat ~/projects/a/*.py")
    # only the first copy (as typed) is read for sureness: grep for ${
    assert not g.command_reaches("grep -n '${' ~/.config/nvim/*.lua",
                                 "grep -n ${ ~/.config/nvim/*.lua")


def test_nesting_too_deep_is_unsure():
    assert not g.lex("echo " + "$(" * 200 + "x" + ")" * 200)[1]
    assert g.lex("echo " + "$(" * 20 + "x" + ")" * 20)[1]
    assert g.command_reaches("echo " + "\"$(" * 200 + " ~/.config/x")


def test_a_comment_starts_only_where_a_word_starts():
    assert g.words("echo # *.py") == []
    assert g.command_reaches("echo # it's\n" + T + " # '")
    assert g.command_reaches("echo a;#b'\n" + T)
    # mid-word # is plain, so the quote after it is real
    assert not g.command_reaches("echo a#b'c ; " + T + " '")
    assert not g.command_reaches("echo $x#'\n" + T + "'")
    assert not g.command_reaches("echo ${x}#'\n" + T + "'")
    # a line continued before # still starts no word
    assert not g.command_reaches("echo a\\\n#'\n" + T + "'")
    assert g.command_reaches("echo \\\n# x\n" + T)


def test_a_here_document_body_is_not_words():
    assert g.command_reaches("cat <<EOF\nit's\nEOF\n" + T + " # '")
    assert g.command_reaches("cat <<'EOF'\nit's\nEOF\n" + T)
    assert g.command_reaches("cat <<-EOF\n\tit's\n\tEOF\n" + T)
    assert g.lex("cat <<-EOF\n\tx\n\tEOF\nls")[1]
    assert g.command_reaches("cat <<A <<B\n'\nA\n\"\nB\n" + T)
    assert g.command_reaches("cat <<EOF ; " + T + "\nbody\nEOF")
    assert g.command_reaches("cat <<\"E\"OF\n'\nEOF\n" + T)
    assert g.command_reaches("cat <<E\\OF\n'\nEOF\n" + T)
    assert g.command_reaches("cat <<$'EOF'\n'\nEOF\n" + T)
    # text inside the body is no words (it is read again as words when
    # it could name a folder: test_text_set_aside_is_read_again)
    assert g.words("cat <<EOF\n*.py\nEOF") == []
    # a quoted delimiter keeps the body's $( ) as text
    assert g.words("cat <<'EOF'\n$(ls *.py)\nEOF") == []
    assert g.words("cat <<EOF\n$(ls *.py)\nEOF") == ["*.py"]
    # the closing line must match exactly; one that never comes is unsure
    assert not g.lex("cat <<EOF\nx\nEOF \n")[1]
    assert g.command_reaches("cat <<EOF\nx\nEOF \n" + T)
    # an unquoted body joins a continued line before comparing
    assert g.lex("cat <<E\na\\\nE\nb\nE\n")[1]
    assert not g.lex("cat <<E\na\\\nE\n")[1]
    assert g.lex("cat <<'E'\na\\\nE\n")[1]


def test_a_here_string_is_not_a_here_document():
    assert g.command_reaches("cat <<<'x' ; " + T)
    assert g.lex("cat <<<x\n" + T)[1]


@pytest.mark.parametrize("cmd", [
    "(( x = 1 << 2 ))\n" + T + "\n2",
    "echo $((1<<2)); " + T + "\n2",
    "echo $((1<<2))\n" + T + "\n2",
    "echo $[1<<2]; " + T + "\n2",
    "echo $[1<<2]\n" + T + "\n2",
    "for ((i=0;i<1;i++)); do " + T + "; done",
    "echo $(( (1) )) ; " + T,
])
def test_a_shift_in_arithmetic_is_not_a_here_document(cmd):
    assert g.lex(cmd)[1]
    assert g.command_reaches(cmd)
    # read as commands, not as a here-document body read again
    assert "*.py" in g.words(cmd.replace(T, "ls *.py"))


@pytest.mark.parametrize("cmd", [
    "echo $(case a in a) echo \"it's\";; esac) ; " + T + " ; echo \"'\"",
    "echo \"$(case a in a) echo \"it's\";; esac)\" ; " + T + " ; echo \"'\"",
    "echo $(case a in (a) echo;; esac) ; " + T,
    "echo $(echo # )\n) ; " + T,
    "echo $(echo ')') ; " + T,
    'echo "$(echo ")")" ; ' + T,
    "echo $(cat <<E\n)\nE\n) ; " + T,
])
def test_a_command_substitution_ends_where_bash_ends_it(cmd):
    assert g.lex(cmd)[1]
    assert g.command_reaches(cmd)


def test_quotes_do_not_count_in_a_backtick_body():
    # bash ends `echo '` at the second backtick
    cmd = "echo `echo '`' ; " + T
    assert not g.lex(cmd)[1]
    assert g.command_reaches("echo `echo '` ; " + T)
    assert g.lex("echo `echo '` ; echo '`'")[1]
    assert g.command_reaches("echo `echo \\`` ; " + T)
    assert g.command_reaches('echo "`echo \\"`" ; ' + T)


def test_a_backtick_body_loses_its_escapes():
    # \" becomes " only inside double quotes (checked in bash 5.2)
    body = 'echo \\"; cat ~/.config/[a]ipager/x; echo \\"'
    assert g.command_reaches("echo `" + body + "`")
    assert not g.command_reaches('echo "`' + body + '`"')


def test_a_body_left_open_counts_only_when_it_could_name_a_folder():
    # bash parses a backtick body on its own: left open, it hides only
    # its own text
    assert not g.command_reaches("echo `echo '` ; cat ~/.config/nvim/*.lua")
    assert g.command_reaches("echo `echo ' ~/.config/[a]ipager/x` ; ls")
    assert not g.command_reaches("cat <<E\n`echo '`\nE\nls ~/.config/nvim/*")
    assert g.command_reaches("cat <<E\n`echo ' ~/.config/[a]ipager/x`\nE\nls")
    assert g.command_reaches("echo $[ ' ~/.config/[a]ipager/x ] ; ls")
    # no comments in arithmetic: the quote after # still counts
    assert g.command_reaches("echo $[ #' ~/.config/[a]ipager/x ] ; ls")


# ----- brace expansion (results as bash 5.2 gives them) -----

@pytest.mark.parametrize("word,want", [
    ("{a,b}c", {"ac", "bc"}),
    ("{a{b,c}}", {"{ab}", "{ac}"}),
    ("{{a,b}}", {"{a}", "{b}"}),
    ("{a,b{c,d}}", {"a", "bc", "bd"}),
    ("x{a}y{b,c}", {"x{a}yb", "x{a}yc"}),
    ("{a}b,c}", {"a}b", "c"}),
    ("{a}b}c,d}", {"a}b}c", "d"}),
    ("{{a}b}c,d}", {"{a}b}c", "d"}),
    ("{a}{b,c}", {"{a}b", "{a}c"}),
    ("{{a,b}c..d}", {"ac..d", "bc..d"}),
    ("{{a,b}c...}", {"ac...", "bc..."}),
    ("{{a,b}c..}", {"{ac..}", "{bc..}"}),
    ("{x..{a,b}}y", {"x..ay", "x..by"}),
    ("{x/../y}", {"{x/../y}"}),
    ("{x/..{a..c}}", {"{x/..{a..c}}"}),
    ('{x".."{a,b}}', {"{x..a}", "{x..b}"}),
    (r"{a\..b{c,d}}", {"{a..bc}", "{a..bd}"}),
    (r"{a\,b}", {"{a,b}"}),
    ("x{},a}", {"x}", "xa"}),
    ("{a,{}b}", {"a", "{}b"}),
    ("~/.config/{{nvim/..,n,x}vim/../aipa}ger",
     {"~/.config/nvim/..vim/../aipager", "~/.config/nvim/../aipager",
      "~/.config/xvim/../aipager"}),
])
def test_brace_expansion_matches_bash(word, want):
    assert want <= _expand(word)


def test_a_quoted_comma_splits_only_when_nothing_else_does():
    # bash: {x","y..z} -> x,y..z ; {x\,y..z} -> {x,y..z}. The lexer
    # does not tell a quoted comma from an escaped one, so both readings
    # are listed for either.
    assert "x,y..z" in _expand('{x","y..z}')
    assert "{x,y..z}" in _expand(r"{x\,y..z}")
    assert _expand("{x,y..z}") == {"x", "y..z"}


def test_a_leading_empty_pair_is_read_both_ways():
    # bash skips a {} that starts a piece; a quote before it is not seen
    got = _expand("{},a}")
    assert "{},a}" in got and {"}", "a"} <= got


def test_sequences():
    assert {"a", "b", "c"} <= _expand("{a..c}")
    assert _expand("{1..3}x") >= {g.OPAQUE + "x"}
    with pytest.raises(g.Unsure):
        g.expand_braces("{Z..a}")


def test_brace_caps():
    with pytest.raises(g.Unsure):
        g.expand_braces("{a,b}" * 9)
    g.expand_braces("{a,b}" * 8)
    with pytest.raises(g.Unsure):
        g.expand_braces("{a," * 40 + "b" + "}" * 40)
    g.expand_braces("{a," * 30 + "b" + "}" * 30)
    with pytest.raises(g.Unsure):
        g.expand_braces("{a,b}" + "x" * 9000)
    with pytest.raises(g.Unsure):
        g.expand_braces("{" + "a" * 3000 + "," + "b" * 3000 + "}" + "{c,d}" * 5)
    g.expand_braces("{" + "a" * 3000 + "," + "b" * 3000 + "}{c,d}{e,f}")


def test_a_word_with_no_possible_brace_is_not_scanned():
    assert g.expand_braces("{" * 50_000) == ["{" * 50_000]
    assert g.expand_braces("{a}" * 20_000) == ["{a}" * 20_000]


# ----- the glob matcher -----

@pytest.mark.parametrize("seg", [
    "aipage?", "a*", "*", "[a]ipager", "[!x]ipager", "[^b]ipager", "[]a]ipager",
    "[[:alpha:]]ipager", "[[:lower:]]ipager", "[[:upper:]]ipager",
    "[[=a=]]ipager", "[[.a.]]ipager", "aipage[[:alpha:]]", "[a-c]ipager",
    "[A-C]ipager", "[[:alpha:][:digit:]]ipager", "[![:digit:]]ipager",
    "[[:a]ipager", "[[=a]ipager", "[[:alpha]ipager", "aipage[[:r]",
    "AIPAGE?", "a[i,]pager", "[a,]ipager",
])
def test_a_glob_that_matches_aipager(seg):
    assert g._classify(seg, False) & g._A


@pytest.mark.parametrize("seg", [
    "[[:lower:]]ip", "nvim*", "*.conf", "[!a]ipager", "[b-z]ipager",
    "[[:x]ipager", "[[=x]ipager",
    "[[:digit:]]ipager", "[[:foo:]]ipager", "aipage",
    "a?????????", "????????",
])
def test_a_glob_that_cannot_match_aipager(seg):
    assert not g._classify(seg, False) & g._A


@pytest.mark.parametrize("seg", [
    "[[.a]ipager", "[[:a[.b]ipager", "[[:a]x[=b]ipager", "[a-.]ipager",
    "[z-a]ipager", "[[.ab.]]ipager", "@(aipager)", "!(x)", "[[:al]pha:]]x",
    # bash reads [[] as [, but a bracket holding [ stays refused
    "[[]ipager", "[a[b]x",
])
def test_what_the_matcher_does_not_model_is_unsure(seg):
    assert g._classify(seg, False) == g._ALL


@pytest.mark.parametrize("seg", [
    # tester-iter5-001: a group past the longest name's length
    ".config?(x)", ".config!(x)", ".config@(|x)", "aipager?(x)", "aipager!(x)",
    "aipager@(|x)", "*(.config)", "share@(|x)",
])
def test_a_group_anywhere_in_a_segment_is_unsure(seg):
    assert g._classify(seg, False) == g._ALL


@pytest.mark.parametrize("seg", [
    # tester-iter5-002: a POSIX item whose character is [
    "aip[a[=[=]ger", "aip[a[:[:]ger", "aip[]a[=[=]ger", ".conf[]i[=[=]g",
    "aip[a[.[.]ger", "[[=[=]a]ipager",
])
def test_a_posix_item_of_an_open_bracket_is_unsure(seg):
    assert g._classify(seg, False) == g._ALL


def test_an_equivalence_class_before_two_brackets_is_read_both_ways():
    # bash 5.2: a*[[=a=]]r] matches a], aipager and apager (the bracket
    # runs on to the last ]); the first reading alone (a* a r]) cannot
    seg = "a*[[=a=]]r]"
    assert g._classify(seg, False) & g._A
    assert not g._can_match(g._items(seg), "aipager", False)
    assert g._can_match(g._items(seg, True), "aipager", False)


def test_two_such_brackets_are_unsure():
    assert g._classify("[[=a=]]x][[=b=]]y]z]", False) == g._ALL


def test_the_leading_dot_must_be_plain():
    for seg in ("?config", "*config", "[.]config", "*.config", "[[:punct:]]config"):
        assert not g._classify(seg, False) & g._C, seg
    for seg in (".c*", ".co?fig", ".[c]onfig", ".*", ".CONFIG"):
        assert g._classify(seg, False) & g._C, seg
    assert g._classify("*", True) & g._C
    assert g._classify("?config", True) & g._C


def test_a_glob_that_may_be_a_dot_step():
    for seg in (".*", ".?", ".[.]", "..*"):
        assert g._classify(seg, False) & g._D, seg
    for seg in ("*", "?", "*.py", ".c*x"):
        assert not g._classify(seg, False) & g._D, seg


def test_a_long_segment_is_not_parsed_past_the_longest_name():
    assert g._items("a" * 8) is None
    assert g._items("[a]" * 7 + "*") is not None
    start = time.perf_counter()
    assert g._classify("?x" * 50_000, False) == 0
    assert g._classify("[" * 50_000 + "x", False) == g._ALL
    assert time.perf_counter() - start < 0.5


def test_literal_names_compare_in_any_case():
    assert g._classify("AIPAGER", False) == g._A
    assert g._classify(".Config", False) == g._C
    assert g._classify("x" + g.OPAQUE, False) == g._O


# ----- the walk -----

@pytest.mark.parametrize("path", [
    "~/.config/aipager/x",
    "~/.config/nvim/../aipager/x",
    "~/.config/a=b/../aipager/x",
    "~/.config/a:b/../aipager/x",
    "~/.config/a/b/../../aipager/x",
    "~/.config//./aipager/x",
    "../.config/aipager/x",
    "~/.local/share/aipager/x",
    "~/.local/state/[a]ipager/x",
    "~/.local/s*/aipager/x",
    "~/.l*/s????/a*/x",
    "~/.c*/[[:alpha:]]ipager/x",
    "~/.config/nvim/.?/aipager/x",
    "~/.config/nvim/.*/aipager/x",
    "~/.local/**/x",
    "~/.config/**/x",
    # passing through the folder counts
    "~/.config/aipager/../nvim",
])
def test_paths_through_aipagers_folders(path):
    assert g._walk(path, False) == g.REACH


@pytest.mark.parametrize("path", [
    "~/.config/nvim/init.lua",
    "~/.config/nvim/../fish",
    "~/.local/lib/aipager/x",
    "~/.local/share/nvim/x",
    "~/aipager/x",
    "~/.cache/aipager/x",
    ".*/x",
    "~/.config/*.conf",
    "~/*/aipager/x",
])
def test_paths_elsewhere(path):
    assert g._walk(path, False) == g.CLEAR


def test_a_dot_step_after_a_value_not_seen_reads_both_ways():
    # one segment up, or still somewhere not seen: $X/../x reads like $X/x
    o = g.OPAQUE
    assert g._walk("~/.config/" + o + "/../aipager", False) == g.REACH
    assert g._walk(o + "/../.config/[a]ipager", False) == g.REACH
    assert g._walk(o + "/../.config/nvim", False) == g.CLEAR
    assert g._walk(o + "/.config/nvim", False) == g.CLEAR
    assert g._walk(o + "/../../x/aipager", False) == g.CLEAR
    # $X may be two folders: ~/.config/a/b/../../aipager
    assert g._walk("~/.config/" + o + "/../../aipager", False) == g.REACH
    # each .. after it adds a reading: past the cap the path is unsure
    assert g._walk("~/" + o + "/.." * 30, False) == g.UNSURE
    assert g._walk("~/.config/" + o + "/.*/aipager", False) == g.REACH
    assert not g.command_reaches("cp -r $PROJECT/../.config/nvim /tmp/x")


def test_a_dot_step_glob_with_nothing_before_it_is_read_all_ways():
    assert g._walk(".*/x", False) == g.CLEAR
    assert g._walk("x/.*/.config/aipager", False) == g.REACH


def test_too_many_readings_are_unsure():
    assert g._walk("~/x/" + ".*/" * 4 + "y", False) == g.UNSURE
    assert g._walk("~/x/" + ".*/" * 2 + "y", False) == g.CLEAR


def test_dotglob_lets_a_star_be_a_hidden_folder():
    assert g._walk("~/*/aipager/x", True) == g.REACH
    assert g._walk("~/**", True) == g.UNSURE


def test_brace_results_are_counted_across_a_command():
    # 16 words of 256 results each fit the budget of 4096; a 17th does not
    word = "~/.config/x" + "{a,b}" * 8 + "/%d"
    sixteen = " ".join(word % k for k in range(16))
    assert not g.command_reaches("ls " + sixteen)
    assert g.command_reaches("ls " + sixteen + " " + word % 16)
    # words of one result do not count
    assert not g.command_reaches("ls " + " ".join(
        "~/.config/nvim/*.%d" % k for k in range(5000)))


def test_segment_flags_are_shared_by_a_commands_words(monkeypatch):
    known: dict[str, int] = {}
    g.word_reach("~/.config/[n]vim/a", False, known)
    assert "[n]vim" in known
    calls = []
    real = g._classify

    def counting(seg, dotglob):
        calls.append(seg)
        return real(seg, dotglob)

    monkeypatch.setattr(g, "_classify", counting)
    g.command_reaches("ls ~/.config/[n]vim/a ~/.config/[n]vim/b")
    assert calls.count("[n]vim") == 1


def test_a_long_path_is_read_in_one_pass():
    path = "~/.config/" + "a/" * 40_000 + ".." * 0 + "x"
    start = time.perf_counter()
    assert g._walk(path, False) == g.CLEAR
    assert time.perf_counter() - start < 0.5


# ----- the decision -----

def test_only_a_word_that_could_name_a_hidden_folder_is_expanded():
    assert not g.may_name_start("src/{a,b}/*.py", False)
    assert g.may_name_start("~/.c{o,x}nf*", False)
    assert g.may_name_start("~/x.y/.l{a..z}cal", False)
    assert not g.may_name_start("x.y/z", False)
    assert g.may_name_start("*", True)


def test_unsure_counts_only_on_a_word_that_could_name_one():
    w = "~/.config/" + "{a,b}" * 9
    assert g.word_reach(w) == g.UNSURE
    assert g.word_reach("~/.config/x/" + ".*/" * 4 + "y") == g.UNSURE
    assert g.word_reach("~/projects/" + "{a,b}" * 9) == g.CLEAR


@pytest.mark.parametrize("cmd", [
    "shopt -s dotglob; cat ~/*/aipager/aipager.yaml",
    "GLOBIGNORE=x; cat ~/*/aipager/aipager.yaml",
])
def test_dotglob_is_read_from_the_command(cmd):
    assert g.command_reaches(cmd)
    assert not g.command_reaches("cat ~/*/aipager/aipager.yaml")


def test_both_copies_are_read():
    raw = 'cat ~/.config/"[a]"ipager/x'
    assert not g.command_reaches(raw)
    assert g.command_reaches(raw, safety.dequote(raw))


# ----- in the floor -----

def test_the_marker_is_in_the_floor_and_never_matches_as_a_regex():
    import re
    pat = safety.BASH_GLOB_REACHES_AIPAGER_DIR
    assert pat in DENY_BASH_PATTERNS
    assert re.search(pat, "cat ~/.config/[a]ipager/x") is None
    assert pat not in safety.DEQUOTED_BASH_PATTERNS


@pytest.mark.parametrize("cmd", [
    "cat ~/.config/[[:a]ipager/aipager.yaml",
    "cat ~/.config/a[i[=]pager/aipager.yaml",
    "cat ~/.config/aip[a\\]]ger/aipager.yaml",
    "cat ~/.config/nvim/{..,/}/aipager/aipager.yaml",
    "cat ~/.config/{nvim/..,x}/aipager/aipager.yaml",
    "cat ~/.config/[a,]{ipager/aipager.yaml,x}",
    "cat ~/.config/{x/y,./}aipager/aipager.yaml",
    "cat ~/.{config,x}/{aipager,a.b}/aipager.yaml",
    "shopt -s extglob\ncat ~/.config/@(aipager)/aipager.yaml",
    "cat ~/.config/a=b/../aipager/aipager.yaml",
])
def test_the_floor_blocks_through_the_matcher(cmd):
    assert bash_violation(cmd, DENY_BASH_PATTERNS) == safety.BASH_REASON
    assert bash_violation(cmd, (safety.BASH_GLOB_REACHES_AIPAGER_DIR,)) is not None


@pytest.mark.parametrize("cmd", [
    "ls ~/.config/[[:lower:]]ip",
    "cp ~/.config/{pip/pip.conf,git/config} /tmp/x",
    "cat ~/.config/{git/config,nvim/x}",
    "ls ~/.config/nvim/{lua,after}",
    "ls .*/x",
    "echo {1..10}",
    "mkdir -p build/{debug,release}/{bin,lib}",
    "find . -name '[[:upper:]]*'",
])
def test_ordinary_commands_pass_the_matcher(cmd):
    assert bash_violation(cmd, (safety.BASH_GLOB_REACHES_AIPAGER_DIR,)) is None


# ----- against real bash (stored) -----

def _golden() -> list[dict]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]


def test_every_spelling_bash_takes_to_aipagers_folders_is_blocked():
    missed = [c["cmd"] for c in _golden()
              if c["reach"] and bash_violation(c["cmd"], DENY_BASH_PATTERNS) is None]
    assert missed == []


def test_the_stored_set_covers_the_review_and_report_spellings():
    sources = {c["source"].split()[0] for c in _golden() if c["reach"]}
    assert {"review-4", "test-report-4", "iter3", "iter4", "fuzz", "review-5",
            "test-report-5", "desync", "review-6", "test-report-6", "iter7",
            "desync7"} <= sources
    assert sum(c["reach"] for c in _golden()) >= 2400


def test_decoys_blocked_stay_few():
    """Spellings bash does not take there that the floor refuses anyway
    (mostly what the matcher cannot read): at most the count when the
    set was made, so a change that refuses more shows here."""
    decoys = [c for c in _golden() if c["source"] == "fuzz decoy"]
    blocked = [c for c in decoys
               if bash_violation(c["cmd"], DENY_BASH_PATTERNS) is not None]
    assert len(decoys) == 910
    assert len(blocked) <= DECOYS_BLOCKED


#: Fuzz decoys the floor refused when the stored set was made.
DECOYS_BLOCKED = 29


# ----- time -----

_SHAPES = ["{", "}", "{,}", "{a/", "{./", "{..,", "{x/y,.}", "[", "[]", "[!]",
           "[[:", "[[:a]", "[[=a=]]x]", "a/.{.,}/", ".*/", "{a}", "a/../",
           "$(", "'*'", '"[a]"', "@(", "\\{"]


@pytest.mark.parametrize("unit", _SHAPES)
@pytest.mark.parametrize("prefix", ["cat ~/.config/", "cat ~/.config/{",
                                    "cat ~/.local/share/", "cat "])
def test_the_matcher_is_linear(prefix, unit):
    def best(size: int) -> float:
        cmd = prefix + unit * (size // len(unit)) + "; ls ~/.config/nvim x"
        dq = safety.dequote(cmd)
        out = float("inf")
        for _ in range(3):
            start = time.perf_counter()
            g.command_reaches(cmd, dq)
            out = min(out, time.perf_counter() - start)
        return out
    small, large = best(25_000), best(100_000)
    assert large < 0.5
    assert large < max(8 * small, 0.02)


#: Many words, each with its own work (review-5 rev-iter5-002: words
#: expanding near the cap; tester-iter5-004: open groups), and the shapes
#: the parser reads (here-documents, comments, arithmetic, backticks).
_MANY = [
    "ls .c{.*,b}{.*,b}{.*,b}{a,b}{a,b}{a,b}{a,b}{a,b}/.*/.*/.*/%d ",
    "@(a|", "${x#{} ", "`echo '` ", "<<E ", "<<E\n", "# ' \n",
    "$((1<<2)) ", "${x:-[a]} ", "$(case a in a) b;; esac) ", "~/.c*/a?(x) ",
    "x[a[=[=]y ",
    # iteration 7: subscripts, =~ groups, glued reserved words, text read
    # again, $' read two ways
    "a[1 #]=x; ", "[[ x =~ (a #) ]]; ", "if(true) then :; fi; ",
    "# ~/.config/nvim x\n", "echo '$'; echo $'\\x61' ", "a=(1 ')' 2); ",
    "$[" * 47 + "1" + "]" * 47 + " ",
    # iteration 8: one long word meeting a glued ``(`` again and again
    # (rev-iter7-001: 7-10 s when each ``(`` joined the word), here-document
    # bodies full of specials (tester-iter7-004), ``${...}`` text read as
    # words, ``$[`` with quotes nested deep
    "x(a)", "f()@", "~$f()", "/f()",
    "$[''" * 47 + "1" + "]" * 47 + " ",
    "${x/a/~/.config/nvim} ", "echo '${' ; ls ~/.config/nvim ; ",
    "cat <<'E'\n~/.config ${ $( ` ' \" $' #\nE\n",
    "cat <<E\n~/.config \\${ \\$( \\` ' \" $' #\nE\n",
]


def _many(unit: str, size: int) -> str:
    out, k = [], 0
    total = 0
    while total < size:
        piece = unit % k if "%d" in unit else unit
        out.append(piece)
        total += len(piece)
        k += 1
    return "".join(out)


@pytest.mark.parametrize("tail", ["; cat ~/.config/aipager/x",
                                  "; ls ~/.config/nvim x"])
@pytest.mark.parametrize("unit", _MANY)
def test_many_costly_words_stay_fast(unit, tail):
    cmd = _many(unit, 100_000) + tail
    best = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        verdict = bash_violation(cmd, DENY_BASH_PATTERNS)
        best = min(best, time.perf_counter() - start)
    assert best < 0.5
    if "aipager" in tail:
        assert verdict is not None



# ----- a reading may only add words (review-6, test-report-6; bash 5.2) -----

@pytest.mark.parametrize("blank", [
    "\r", "\v", "\f", "\x1c", "\x1f", "\x85", "\xa0", "\u2003", "\u3000"])
def test_only_a_space_or_tab_is_a_blank(blank):
    """rev-iter6-001: bash reads ``x<char>#*`` as one word, so the ``#``
    starts no comment."""
    assert g.words("ls x" + blank + "#*") == ["x" + blank + "#*"]
    assert g.lex("echo x" + blank + "#' ; ls")[1] is False
    assert g.command_reaches("echo x" + blank + "# ; " + T)


@pytest.mark.parametrize("cmd,want", [
    ("a[1 #]=x; ls *.py", ["a[1 #]=x", "*.py"]),
    ("a[1 #]+=x; ls *.py", ["a[1 #]+=x", "*.py"]),
    ("x=1 a[0 #]=1 true *.py", ["a[0 #]=1", "*.py"]),
    ("a=([0 #]=1 [1]=2); ls *.py", ["[0 #]=1", "[1]=2", "*.py"]),
    ("a=( x [2 ; echo b ]=3 ); ls *.py", ["[2 ; echo b ]=3", "*.py"]),
    ("if a[1 #]=x; then ls *.py; fi", ["a[1 #]=x", "*.py"]),
    # not where an assignment may stand: bash splits at the blank
    ("a[b[1] #]=x; ls *.py", ["a[b[1] #]=x", "*.py"]),
    ("echo a[1 #] *.py", ["a[1"]),
    ("a=(x[0 #]=1) *.py", ["x[0"]),
    ("for a[1 #] in *.py", ["a[1"]),
])
def test_an_assignment_subscript_is_one_word(cmd, want):
    """rev-iter6-002: where an assignment may stand, bash reads
    ``name[...]`` to its matching ``]`` as one word (blanks, ``#`` and
    ``<<`` in it), and so a word starting with ``[`` in ``a=(...)``."""
    assert g.words(cmd) == want


@pytest.mark.parametrize("cmd", [
    "a[1<<2]=x\n" + T + "\n2]=x",
    "a[1<<E]+=x\n" + T + "\nE]+=x",
    "a[1 #$(" + T + ")]=1",
    "a=([0 #$(" + T + ")]=1)",
    "x=1 a[0 #$(" + T + ")]=1 true",
])
def test_the_review_6_subscript_commands_are_blocked(cmd):
    assert g.lex(cmd)[1]
    assert g.command_reaches(cmd)
    assert bash_violation(cmd, DENY_BASH_PATTERNS) == safety.BASH_REASON


_LP, _RP = g._lit("("), g._lit(")")


@pytest.mark.parametrize("cmd,want", [
    # a group's parens are characters (bash never globs this word) unless
    # an extended glob's ?*+@! comes first; a glob after the group makes
    # the word one that words() returns
    ("[[ x =~ (a #)* ]]; ls *.py", ["[[", _LP + "a #" + _RP + "*", "*.py"]),
    ("[[ x =~ ( #)* ]]; ls *.py", ["[[", _LP + " #" + _RP + "*", "*.py"]),
    ("[[ x =~ x(a\n#)? ]] && ls *.py",
     ["[[", "x" + _LP + "a\n#" + _RP + "?", "*.py"]),
    ("[[ x =~ @(a #) ]]; ls *.py", ["[[", "@(a #)", "*.py"]),
    ("[[ x =~ (a #) ]]; ls *.py", ["[[", "*.py"]),
    ("[[ x =~ a|#b ]]; ls *.py", ["[[", "*.py"]),
    ("[[ ! x =~ a|#b && y ]]; ls *.py", ["[[", "*.py"]),
    # only the word after =~, only inside [[ ]]
    ("[[ x == y ]]; (ls #) ; ls *.py", ["[["]),
    ("[[ a ]]; echo =~ a|#b ; ls *.py", ["[["]),
])
def test_a_regex_pattern_keeps_its_groups_and_bars(cmd, want):
    """rev-iter6-003: in ``[[ x =~ ... ]]`` the pattern word holds its
    ``(...)`` groups (blanks and ``#`` inside) and ``|``."""
    assert g.words(cmd) == want


@pytest.mark.parametrize("cmd", [
    "[[ x =~ (#) ]]; " + T,
    "[[ x =~ ( #) ]]; " + T,
    "[[ x =~ a|#b ]]; " + T,
    "[[ x =~ (a #$(" + T + ")) ]]",
])
def test_the_review_6_pattern_commands_are_blocked(cmd):
    assert g.lex(cmd)[1]
    assert g.command_reaches(cmd)


@pytest.mark.parametrize("head", [
    "if(", "while(", "until(", "time(", "!(", "coproc(", "{(",
    "if true; then(", "f()("])
def test_a_reserved_word_glued_to_a_paren_opens_a_subshell(head):
    """rev-iter6-004: ``if(``, ``f()(`` and the like end the word: the
    ``(`` opens a subshell, where ``<<`` is a here-document."""
    cmd = head + "cat <<E\n) <<F\nE\n)\nls *.py\nF"
    assert "*.py" in g.words(cmd)
    assert g.command_reaches(head + "cat <<E\n) <<F\nE\n)\n" + T + "\nF")
    # an extended glob glued to a word stays one word
    assert g.words("echo x@(a|#b) *.py") == ["x@(a|#b)", "*.py"]


def test_text_set_aside_is_read_again():
    """rev-iter6-005: a comment, a here-document's delimiter or body, a
    subscript or a ``=~`` group is read again as plain words when it
    could name a folder, so bash reading it some other way never hides a
    word."""
    assert g.words("echo # ~/.config/[a]ipager/x") == ["~/.config/[a]ipager/x"]
    assert g.command_reaches("echo x # ; " + T)
    assert g.command_reaches("cat <<E\n" + T + "\nE")
    assert g.command_reaches("cat <<'E'\n$(" + T + ")\nE")
    assert g.command_reaches("cat <<E~/.config/[a]ipager/x\nE~/.config/[a]ipager/x")
    assert g.command_reaches("echo $[ " + T)
    assert "~/.config/[a]ipager/x" in g.words(
        "echo # x # ~/.config/[a]ipager/x")
    # a subscript bash may not read as one: its text is read again
    assert ".config/[a]ipager/x]" in g.words("a[1 .config/[a]ipager/x]=1")
    # text that cannot name a folder is not read again
    assert g.words("echo # *.py") == []
    assert g.words("cat <<E\n*.py\nE") == []


def test_an_empty_quoted_delimiter_is_a_here_document():
    """rev-iter6-008: ``<<''``, ``<<""``, ``<<$''`` and ``<<$""`` are
    legal; the body ends at the first empty line."""
    for d in ("''", '""', "$''", '$""'):
        assert g.lex("cat <<" + d + "\nx\n\nls")[1]
    assert not g.lex("cat << ; ls")[1]


@pytest.mark.parametrize("pre", [
    "echo '$'; ", "grep -n 'TODO$' notes.txt; ", "grep -E '^a|b$' f && ",
    "echo 'cost: 5$'\n", "echo \"$'\"; ", "echo \"\\$'\"; ",
    "echo # it's 5$'\n", "cat <<'E'\n5$'\nE\n", "cat <<E\n5\\$'\nE\n",
])
def test_a_dollar_quote_bash_does_not_read_shifts_nothing(pre):
    """tester-iter6-001: a ``$'`` bash does not read as ANSI-C quoting
    (in quotes, a comment, a here-document body) must not change how a
    later ``$'...'`` is read, in the matcher or in the regex rules."""
    assert g.command_reaches(pre + "cat ~/$'\\x2e'config/[a]ipager/x")
    for target in ("cat ~/.config/$'\\x61'ipager/aipager.yaml",
                   "$'\\x61'ipager setup group --force",
                   "python3 -c $'import \\x61ipager.scope'",
                   "cd ~/$'\\x2e'config && cat aipager/x"):
        assert bash_violation(pre + target, DENY_BASH_PATTERNS) is not None


def test_the_words_bash_reads_are_a_copy_of_their_own():
    """Only bash's own ``$'`` decoded (:func:`spelled`): ``\\xargs`` is
    ``xargs`` there, where decoding every escape makes it a newline."""
    cmd = "echo '$'; \\xargs aipager; echo 'x'"
    assert "xargs aipager" in g.spelled(cmd)
    assert bash_violation(cmd, DENY_BASH_PATTERNS) is not None
    assert g.spelled('cat "$HOME"/.config/$\'\\x61\'ipager/x') == (
        "cat $HOME/.config/aipager/x")
    assert g.spelled("cd $HOME/x") == "cd $HOME/x"
    # the regex rules see it with its paths made plain: here \x20 is
    # x20 to bash (outside $'...'), a blank once every escape is decoded
    cmd = "echo '$'; cat ~/.config/nvim\\x20/../$'\\x61'ipager/x"
    regex_only = tuple(p for p in DENY_BASH_PATTERNS
                       if p != safety.BASH_GLOB_REACHES_AIPAGER_DIR)
    assert bash_violation(cmd, regex_only) is not None


def test_every_escape_decoded_is_a_copy_of_its_own():
    """Every escape decoded as if all of the command sat in ``$'...'``
    (``dequote(..., True)``): bash decodes the ``$'`` in ``${x/*/...}``,
    which the word reading keeps as typed."""
    cmd = "x=a; echo '$'; pkill -f ${x/*/$'\\x61'ipager}"
    assert "${x/*/aipager}" in safety.dequote(cmd, True)
    assert bash_violation(cmd, DENY_BASH_PATTERNS) is not None


def test_a_numeric_escape_may_name_a_folder():
    """Decoded wherever it stands, not only after a ``$'`` read from the
    left (``echo '$'`` would shift that)."""
    assert g._mentions("$'\\x2e'config", False)
    assert g._mentions("echo '$'; echo $'\\056\\143onfig'", False)
    assert g._mentions("echo `echo $'\\\\x2e'config`", False)
    assert not g._mentions("echo $'\\x41' ~/projects", False)
    assert g.command_reaches("echo '$'; cat ~/$'\\x2e'config/[a]ipager/x")


def test_nothing_is_read_when_nothing_could_name_a_folder(monkeypatch):
    """tester-iter6-002: a command where no copy could name ``.config``
    or ``.local`` is not read at all."""
    def boom(*a):
        raise AssertionError("read")
    monkeypatch.setattr(g, "_read", boom)
    assert not g.command_reaches("(" * 5000 + "x" + ")" * 5000 + " ; ls *.py")


def test_an_open_dollar_bracket_is_read_once():
    """rev-iter6-007: ``$[`` left open 47 deep is read as plain words
    once, not once per level."""
    def best(levels: int) -> float:
        cmd = "$[" * levels + "x" * 100_000 + " ~/.config/nvim/x"
        out = float("inf")
        for _ in range(3):
            start = time.perf_counter()
            assert bash_violation(cmd, DENY_BASH_PATTERNS) is not None
            out = min(out, time.perf_counter() - start)
        return out
    assert best(47) < 3 * best(1) + 0.02


def test_a_word_only_quotes_or_dollar_quotes_make_is_read():
    """The matcher skips a copy that holds no word worth expanding; quotes
    taken out, ``'.''.'`` is a ``..`` step, and ``$'...'`` may make one."""
    assert g.command_reaches("cat ~/.config/x/'.''.'/aipager/x")
    assert g.command_reaches("cat ~/.config/x/$'\\x2e\\x2e'/aipager/x")


# ----- iteration 8 (review-7, test-report-7; bash 5.2) -----

def test_function_keyword_starts_a_command_for_its_name():
    """``function f()(`` opens a subshell after ``f()``, where ``<<`` is a
    here-document (bash 5.2 runs its body)."""
    cmd = "function f()(cat <<E\n) <<F\nE\n)\nls *.py\nF"
    assert "*.py" in g.words(cmd)


def test_an_empty_glued_group_is_not_read_as_commands(monkeypatch):
    """A function's ``()`` glued to its name is taken as it is: no nested
    read of commands per ``()`` (``f()`` repeated cost one call each)."""
    calls = []
    real = g._Lexer.commands

    def spy(self, *a, **k):
        calls.append(1)
        return real(self, *a, **k)

    monkeypatch.setattr(g._Lexer, "commands", spy)
    words = g.words("f() { :; }; " * 50 + "ls *.py")
    assert "*.py" in words
    assert len(calls) == 1


@pytest.mark.parametrize("cmd", [
    "# $[ cat ~/.config/[a]ipager/x ]",
    "# $[ $[ cat ~/.config/[a]ipager/x ] ]",
    "cat <<'E'\n$[ cat ~/.config/[a]ipager/x ]\nE",
])
def test_a_flat_read_reads_arithmetic_as_words(cmd):
    """rev-iter7-004: an arithmetic-only ``$[...]`` inside text read
    again is read as words there, not set aside a second time."""
    assert "~/.config/[a]ipager/x" in g.words(cmd)


def test_a_copy_that_nests_too_deep_counts():
    """A quote-free copy can nest where the command as typed does not
    (``'$($(...'``); past the nesting cap its words are missing, so the
    check fails closed whichever copy it is."""
    cmd = "echo '" + "$(" * 60 + "' ; eval 'cat ~/.config/[a]ipager/x'"
    assert g.lex(cmd)[1]
    assert g.command_reaches(cmd, safety.dequote(cmd))
    assert bash_violation(cmd, DENY_BASH_PATTERNS) is not None
    # deep but naming nothing: nothing is read at all
    assert not g.command_reaches("echo " + "$(" * 60 + " ; ls *.py")


def test_a_second_reading_that_nests_too_deep_counts():
    """Text set aside (here a comment) whose second reading runs past the
    nesting cap may hide words there: the check fails closed."""
    cmd = "echo x # " + "$(" * 60 + " ; cat ~/.config/[a]ipager/x"
    out, sure, _, deep = g._read(cmd, False)
    assert deep and not sure
    assert g.command_reaches(cmd)


@pytest.mark.parametrize("text,want", [
    ("~/.config ${ $( ` ' \" $' #", False),
    ("~/.config/nvim $(ls) ${x} $[1]", False),
    ("~/.config/[a]ipager", True),
    ("x{a,b}", True),
    ("~/.config/*", True),
    ("x(y)", True),
    ("'.''.'", True),
    ("\\.\\.", True),
    ("$'\\x2e\\x2e'", True),
    ("$'\\056.'", True),
    ("${x:-[a]}", True),
    ("$((1))", True),
])
def test_only_text_that_can_give_a_checked_word_is_read_again(text, want):
    """A flat read gives a word the matcher checks only from a glob,
    brace or group character that opens no expansion, or two dots."""
    assert g._may_give_worth(text) is want


def test_a_bracket_pair_is_the_one_a_scan_finds():
    """The ``$[`` pairs from one table per text are the ones a scan from
    each ``[`` finds, also in a slice of the text."""
    import random
    rnd = random.Random(8)
    for _ in range(300):
        text = "".join(rnd.choice("[]x'$") for _ in range(rnd.randrange(1, 40)))
        table = g._Brackets(text)
        lo = rnd.randrange(0, len(text))
        hi = rnd.randrange(lo, len(text) + 1)
        for at in range(lo, hi):
            if text[at] != "[":
                continue
            depth, want = 1, len(text)
            for k in range(at + 1, len(text)):
                depth += {"[": 1, "]": -1}.get(text[k], 0)
                if depth == 0:
                    want = k
                    break
            assert table.close(at) == want
            # in the slice text[lo:hi] the same pair, or none
            sliced = min(table.close(at) - lo, hi - lo)
            sub = g._Brackets(text[lo:hi]).close(at - lo)
            assert sliced == min(sub, hi - lo)


def test_text_that_cannot_give_a_checked_word_is_not_read_again(monkeypatch):
    """Set-aside text that names ``.config`` but holds no glob, brace,
    group or ``..`` (the ``(``, ``{``, ``[`` of ``$(``, ``${``, ``$[`` do
    not count) is not read a second time; text that does, is."""
    flat = []
    real = g._Lexer.commands

    def spy(self, *a, **k):
        if self.flat:
            flat.append(1)
        return real(self, *a, **k)

    monkeypatch.setattr(g._Lexer, "commands", spy)
    g.words("cat <<'E'\n~/.config ${ $( ` ' \" $' #\nE\n" * 20)
    assert flat == []
    g.words("cat <<'E'\n~/.config/[a]ipager\nE\n")
    assert flat == [1]


def test_a_copy_that_cannot_give_a_checked_word_is_not_read(monkeypatch):
    """A quote-free copy whose text holds no glob, brace, group or ``..``
    (the openers of ``$(``, ``${``, ``$[`` do not count) is not read; the
    command as typed still is (one left open refuses it)."""
    reads = []
    real = g._read

    def spy(copy, *a, **k):
        reads.append(copy)
        return real(copy, *a, **k)

    monkeypatch.setattr(g, "_read", spy)
    cmd = "cat <<'E'\n~/.config ${ $( ` ' \" $' #\nE\n" * 3
    copies = (safety.dequote(cmd), safety.dequote(cmd, True))
    assert not g.command_reaches(cmd, *copies)
    assert reads == [cmd]


def test_an_arithmetic_only_body_is_not_read_as_words(monkeypatch):
    """``$[...]`` with no quote, escape or substitution is set aside (read
    again only if it could name a folder), not read word by word: 47
    nested levels are one read."""
    calls = []
    real = g._Lexer.commands

    def spy(self, *a, **k):
        calls.append(1)
        return real(self, *a, **k)

    monkeypatch.setattr(g._Lexer, "commands", spy)
    assert g.lex("echo " + "$[" * 47 + "1" + "]" * 47 + " ; ls *.py") == (
        ["*.py"], True)
    assert len(calls) == 1


def test_a_nested_dollar_bracket_in_a_body_closes():
    """A ``$[`` inside a ``$[...]`` body read on its own finds its own
    ``]`` (the pairs of the whole text, shifted to the body)."""
    assert g.lex("echo $[ ''~/.config/nvim + $[ 2 ] ] ; ls *.py")[1]
    assert g.lex("cat <<E\n$[ ''~/.config/nvim + $[ 2 ] ]\nE\nls *.py")[1]


def test_a_quoted_dollar_bracket_nest_is_read_once(monkeypatch):
    """``$[''`` nested 47 deep: the brackets are paired in one pass over
    the text, not scanned again for every ``$[`` (review-7: a scan per
    level cost 0.38 s at 100 KB)."""
    scans = []
    real = g._BRACKET_RE

    class Counting:
        def finditer(self, *a):
            scans.append(1)
            return real.finditer(*a)

    monkeypatch.setattr(g, "_BRACKET_RE", Counting())
    unit = "$[''" * 47 + "1" + "]" * 47 + " "
    out, sure, _, _ = g._read(unit * 20 + "; ls ~/.config/nvim/x", False)
    assert sure
    assert len(scans) == 1


def test_a_subscript_word_holds_no_copy_of_what_is_nested():
    """A nested expansion in a subscript is one unknown value in the
    word around it (a group, so the segment may match anything); its own
    words are read where it stands."""
    found = g.words("a[$(ls ~/.config/[a]ipager/x)]=1 ; ls *.py")
    assert "a[" + g._NESTED + "]=1" in found
    assert "~/.config/[a]ipager/x" in found
    assert g.command_reaches("a[$(x)]/../.config/[a]ipager/x")


_NEST_FILL = ("x ~/.config/nvim " * 6000)[:100_000]


@pytest.mark.parametrize("head,tail", [
    ("a[$(", ")]=1"),
    ("a[$(# ", "\n)]=1"),
    ("cat <<E\n$(", "\n)\nE\n"),
    ("a=([$(", ")]=1)"),
    ("[[ x =~ (", ") ]]"),
    ("${x/a/", "}"),
    ("${x:-${y/a/", "}}"),
    ("# $[", "]"),
], ids=["subscript", "comment-in-subscript", "heredoc", "compound",
        "regexp", "replacement", "default-replacement", "flat-arith"])
def test_nested_set_aside_text_is_read_again_once(head, tail):
    """rev-iter7-005: text set aside inside text set aside is read again
    once (the outermost span only), not once per level: 23 levels around
    100 KB cost about what one does."""
    def best(levels: int) -> float:
        if head.startswith("cat <<E"):
            cmd = _NEST_FILL
            for k in range(levels):
                cmd = "cat <<E%d\n$(%s\n)\nE%d\n" % (k, cmd, k)
        else:
            cmd = head * levels + _NEST_FILL + tail * levels
        cmd += " ; ls ~/.config/nvim x"
        out = float("inf")
        for k in range(3):
            c = ": r%d ; %s" % (k, cmd)
            start = time.perf_counter()
            bash_violation(c, DENY_BASH_PATTERNS)
            out = min(out, time.perf_counter() - start)
        return out
    # reading again per level costs 0.7-1 s here; one level's own words
    # (a 100 KB word met once at depth 23) may cost a little more
    assert best(23) < 3 * best(1) + 0.2


@pytest.mark.parametrize("cmd", [
    "cat ${x/a/~/.config/[a]ipager/aipager.yaml}",
    "cat ${x//?/~/.config/[a]ipager/x}",
    "cat ${x/#a/~/.config/[a]ipager/x}",
    "cat ${x/%a/~/.config/[a]ipager/x}",
    "cat ${x/a/~/.local/share/[a]ipager/x}",
    "cat ${a[0]/a/~/.config/[a]ipager/x}",
    "cat ${!x/a/~/.config/[a]ipager/x}",
    "cat ${x/a/${unset_v:-~/.config/[a]ipager/x}}",
    "cat ${unset_v:-${x/a/~/.config/[a]ipager/x}}",
    "cat ${x:+${x/a/~/.config/[a]ipager/x}}",
    "cat ${x#a}${x/a/~/.config/[a]ipager/x}",
])
def test_a_replacement_may_be_the_value(cmd):
    """tester-iter7-001: bash expands ``~`` and globs in the replacement
    of ``${x/pat/rep}`` (``x=a; cat ${x/a/~/.config/[a]ipager/x}`` reads
    the file), as in a ``${x:-default}``."""
    assert g.command_reaches(cmd)


def test_a_replacement_is_the_value_in_its_word():
    """The replacement goes into the word's second reading, so a word
    whose replacement holds only part of the name reaches too."""
    assert g.command_reaches("cat ${x/a/~/.config/[a]ip}ager/x")
    assert g.command_reaches("cat ~/.config/${x/a/[a]}ipager/x")
    # a pattern operation's text is a word of its own, never in the value
    assert g.words("cat ~/.config/${x#[a]}ipager/x") == ["[a]"]
    # a replacement with no new text deletes: the value is not known
    assert "~/.config/[a]ipager/x" not in g.words(
        "cat ~/.config/${x/[a]}ipager/x")
    # the whole text of a ${...} is also read again as plain words
    assert "x#~/.config/[a]ipager/x" in g.words(
        "echo ${x#~/.config/[a]ipager/x}")
    # quoted, a default is not globbed: never in the word's second reading
    assert not g.command_reaches('cat "~/.config/${x:-[a]}ipager/x"')
    # words read before a ${...} stay (a $(...) inside it is more words)
    assert "*.py" in g.words(
        "ls *.py ; echo ${x:-$(cat ~/.config/[a]ipager/x)}")


@pytest.mark.parametrize("op", [
    "#", "##", "%", "%%", ":?", "?", "^", "^^", ",", ",,", "@", ":", ""])
def test_every_operation_text_is_read_as_words(op):
    """rev-iter7-003: whatever the operation, the text of a ``${...}`` is
    read as words, as a word of its own and again as plain words."""
    found = g.words("echo ${x" + op + "~/.config/[a]ipager/x} ; ls *.py")
    assert any(w.endswith("~/.config/[a]ipager/x") for w in found), found
    assert "*.py" in found


@pytest.mark.parametrize("cmd", [
    "grep -n '${HOME' x ; eval 'cat ~/.config/[a]ipager/aipager.yaml'",
    "echo '${' ; bash -c 'cat ~/.config/[a]ipager/aipager.yaml'",
    "echo \"${\" ; eval 'cat ~/.config/[a]ipager/aipager.yaml'",
    "echo $'${' ; eval 'cat ~/.local/share/[a]ipager/x'",
    "echo '${x#' ; eval 'cat ~/.config/aip[a]ger/x'",
    "echo '${' ; eval 'cat ~/.config/nvim/../[a]ipager/x'",
    "echo '${' ; eval 'cat ~/.config/{aipager,x}/aipager.yaml'",
])
def test_a_quoted_open_param_hides_nothing(cmd):
    """rev-iter7-003: in a quote-free copy a quoted ``${`` opens (and runs
    to the end, or to a ``}`` far off); the text after it is still read as
    words (set aside and read again)."""
    assert bash_violation(cmd, DENY_BASH_PATTERNS) is not None


def test_an_open_param_is_read_on_in_a_quote_free_copy():
    """The command as typed is sure and has no reaching word; its
    quote-free copy leaves ``${`` open to the end. Only reading the text
    after that ``${`` finds the word."""
    cmd = "grep -n '${HOME' x ; eval 'cat ~/.config/[a]ipager/aipager.yaml'"
    copy = safety.dequote(cmd)
    assert g.lex(cmd)[1] and not g.command_reaches(cmd)
    assert not g.lex(copy)[1]
    assert g.command_reaches(cmd, copy)


def test_a_flat_read_reads_on_after_an_open_param():
    """A second (flat) reading reads the text after the outermost ``${``
    left open as commands, once: later ``${`` are plain text there."""
    assert "~/.config/[a]ipager/x" in g.words(
        "# ${ ; cat ~/.config/[a]ipager/x")
    assert "~/.config/[a]ipager/x" in g.words(
        "# ${x:-${ ; cat ~/.config/[a]ipager/x")
    assert "~/.config/[a]ipager/x" in g.words(
        "# ${ ${ ; cat ~/.config/[a]ipager/x")
    # from the outermost one: the text before an inner open ``${`` too
    assert "~/.config/[a]ipager/x" in g.words(
        "# ${ ; cat ~/.config/[a]ipager/x ; ${")


def test_a_regex_pattern_is_never_a_path():
    """Review-7 nit: bash never globs the word after ``=~``; a group in it
    not after an extended glob's ``?*+@!`` would be a syntax error
    anywhere else, so its parens are characters."""
    assert not g.command_reaches("[[ $p =~ ^~/.config/(nvim|fish) ]]")
    assert not g.command_reaches("[[ $p =~ ~/.config/(aipager) ]]")
    assert not g.command_reaches("[[ $p =~ ^~/.config/(nvim|(fish)) ]]")
    assert g.command_reaches("[[ $p =~ ~/.config/@(aipager) ]]")
    assert g.command_reaches("[[ $p =~ ~/.config/[a]ipager ]]")


def test_a_repeated_backtick_body_is_read_once(monkeypatch):
    """The same backtick body adds the same words and sureness, so it is
    read once per read; a body that may set text aside, or one read while
    spelled text is collected, is read at each place."""
    calls = []
    real = g._Lexer.commands

    def spy(self, *a, **k):
        calls.append(self.text)
        return real(self, *a, **k)

    monkeypatch.setattr(g._Lexer, "commands", spy)
    assert g.words("echo `ls *.py` " * 50) == ["*.py"]
    assert calls.count("ls *.py") == 1
    calls.clear()
    g.words("echo `ls # x` " * 5)
    assert calls.count("ls # x") == 5
    calls.clear()
    g._read("echo `ls *.py` " * 5, False, None, True)
    assert calls.count("ls *.py") == 5
    # a body left open makes every read that meets it unsure
    assert not g.lex("echo `echo \\`x ~/.config/nvim` ; " * 3)[1]


def test_a_cached_body_keeps_its_effect_on_sureness():
    """A body recorded as leaving the read unsure makes it unsure again
    where it is met again (the cache keeps that, not only the words)."""
    for recorded in (False, True):
        lexer = g._Lexer("echo `ls` ; ls *.py", [], bodies={"ls": recorded})
        lexer.commands()
        assert lexer.sure is recorded


# ----- iteration 9 -----

@pytest.mark.parametrize("seg", [
    # tester-iter8-004: a range from a collating symbol or another item
    "a[[.a.]-z]pager", "a[[.a.]-x]pager", "a[[.-.]-z]pager", "a[[=a=]-z]pager",
    # a range in the run-on reading after an equivalence class
    "a[[=x=]]h-j]pager", "a[[=a=]]-z]pager", "a[[=a=]]!-~]pager",
    "a[[=a=]][h-j]pager", "a[a[=a=]]-z]pager", "a[][=a=]]-z]pager",
])
def test_a_range_bash_reads_from_an_item_is_unsure(seg):
    assert g._classify(seg, False) == g._ALL


@pytest.mark.parametrize("seg", [
    "a[[.a.]-]pager", "a[[=x=]-]pager", "a[[=x=]]i]pager", "[[=n=]]v[a,]",
])
def test_an_item_without_such_a_range_is_still_read(seg):
    assert g._classify(seg, False) != g._ALL


@pytest.mark.parametrize("cmd", [
    # tester-iter8-005: bash 5.2 puts the matched text at a bare ``&``
    "cat ~/.config/${x/a/&ipager}/x",
    "cat ~/.config/${x/a/&i}pager/x",
    "cat ${x/a/~/.config/&ipager/x}",
    "cat ${x//a/~/.config/&ipager/x}",
    # a glob pattern stands for the matched text as itself
    "cat ${x/[a]/~/.config/&ipager/x}",
    "cat ${x/a*/~/.config/&/x}",
    # a pattern holding a value: the matched text is not known
    "cat ~/.config/${x/$y/&ipager}/x",
])
def test_a_bare_ampersand_in_a_replacement_is_the_matched_text(cmd):
    assert g.command_reaches(cmd)


@pytest.mark.parametrize("cmd", [
    "cat ~/.config/${x/a/\\&ipager}/x",
    "cat ~/.config/${x/a/'&'ipager}/x",
    "cat ~/.config/${x/b/&ipager}/x",
    "cat ~/.config/${x/a/&nvim}/x",
    "cat ~/.config/${x#&ipager}/x",
])
def test_an_ampersand_that_is_no_matched_text_is_plain(cmd):
    assert not g.command_reaches(cmd)


def test_a_plain_pattern_stands_as_one_bracket_per_character():
    assert "~/.config/[a]ipager/x" in g.words("cat ~/.config/${x/a/&ipager}/x")
    assert "[a][b]" in g.words("echo ${x/ab/&}")


@pytest.mark.parametrize("cmd", [
    # tester-iter8-007: a value glued to a glob may be empty
    "cat ~/.config/[a]ipager${unset_v}/x",
    "cat ~/.config/[a]ipager$unset_v/x",
    "cat ~/.config/[a]ipager${unset_v@Q}/x",
    "cat ~/.config/[a]ipager${x:0:0}/x",
    "cat ~/.config/[a]ipager${!zzq*}/x",
    "cat ~/.config/[a]ipager$(:)/x",
    "cat ~/.config/$x*/x",
])
def test_a_value_glued_to_a_glob_may_be_empty(cmd):
    assert g.command_reaches(cmd)


def test_an_empty_value_changes_only_a_glob_segment():
    assert g._classify("[a]ipager" + g.OPAQUE, False) & g._A
    assert not g._classify("[n]vim" + g.OPAQUE, False) & g._A
    # a plain name split by a value stays the documented open route
    assert not g._classify("aip" + g.OPAQUE + "ager", False) & g._A
    assert not g.command_reaches("cat ~/.config/[n]vim$x/init.lua")
