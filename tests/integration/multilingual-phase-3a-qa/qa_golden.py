"""Re-records ``golden_base.json``: what the base tree (``S.BASE``, this
branch's merge-base) showed in English, so the tests compare against a
committed file and never call git.

Only run when ``AIPAGER_RECORD_QA_GOLDEN=1`` (see conftest.py). It archives
the base commit (``git archive``, never a checkout) into a temporary
folder, copies the scenario drivers and ``qa_record.py`` into it, runs them
there under pytest in a subprocess with a throwaway HOME, and reads the
base's literal baseline and safety.py's imports with ``git show``. The
worktree is never written to except the golden file itself."""

from __future__ import annotations

import ast
import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "golden_base.json"


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True).stdout


def imports_of(source: str) -> list[str]:
    """Every module *source* imports (``from x import y`` as ``x``)."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add("." * node.level + (node.module or ""))
    return sorted(names)


def _scenarios(S, ref: str, root: Path) -> dict:
    tree = root / "tree"
    tree.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(_git(S.REPO, "archive", ref))) as tar:
        kw = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        tar.extractall(tree, **kw)
    runner = tree / "tests" / "qa_p3a_runner"
    runner.mkdir()
    (runner / "__init__.py").write_text("")
    shutil.copy(HERE / "qa_scenarios.py", runner / "qa_scenarios.py")
    shutil.copy(HERE / "qa_record.py", runner / "test_qa_record.py")
    out = root / "out"
    out.mkdir()
    home = root / "home"
    home.mkdir()
    env = S.clean_env(home, tree)
    env["QA_P3A_OUT"] = str(out)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--basetemp", str(root / "bt"), "tests/qa_p3a_runner/test_qa_record.py"],
        cwd=tree, env=env, capture_output=True, text=True, timeout=600)
    recorded = {p.stem: json.loads(p.read_text()) for p in out.glob("*.json")}
    missing = sorted((set(S.SCENARIOS) | {"_constants"}) - set(recorded))
    if proc.returncode or missing:
        raise RuntimeError(f"the {ref} recorder failed (missing {missing}):\n"
                           + proc.stdout[-4000:] + proc.stderr[-2000:])
    return recorded


def record(S, root: Path, ref: str | None = None) -> dict:
    """Record *ref* (the base by default) into a dict shaped like the
    golden file."""
    ref = ref or S.BASE
    recorded = _scenarios(S, ref, root)
    constants = recorded.pop("_constants")
    return {
        "base": ref,
        "scenarios": {name: recorded[name] for name in sorted(recorded)},
        "constants": constants,
        "literal_baseline": json.loads(_git(S.REPO, "show",
                                            f"{ref}:tests/i18n_literal_baseline.json")),
        "safety_imports": imports_of(_git(S.REPO, "show", f"{ref}:aipager/safety.py").decode()),
    }


def write(golden: dict) -> None:
    GOLDEN.write_text(json.dumps(golden, ensure_ascii=True, indent=1, sort_keys=True) + "\n",
                      encoding="utf-8")
