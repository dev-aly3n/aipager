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
    # quoted, it is not globbed; a pattern is never a value
    assert not g.command_reaches('echo "${x:-~/.config/[a]ipager/aipager.yaml}"')
    assert not g.command_reaches("echo ${x#~/.config/[a]ipager/aipager.yaml}")


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
    assert g.words("echo # ~/.config/[a]ipager/x") == []
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
    # text inside the body is never a path
    assert not g.command_reaches("cat <<EOF\n" + T + "\nEOF")
    # a quoted delimiter keeps the body's $( ) as text
    assert not g.command_reaches("cat <<'EOF'\n$(" + T + ")\nEOF")
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
            "test-report-5", "desync"} <= sources
    assert sum(c["reach"] for c in _golden()) >= 1500


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

