"""`aipager policy validate` — lint the user-owned policy files.

Read-only: loads ``policy.yaml`` / ``policy.d/`` (and ``aipager.yaml``
if present, to cross-check role references), reports any problems, and
mutates nothing.
"""

from __future__ import annotations

import argparse


def cmd_policy_validate(args: argparse.Namespace) -> int:
    from aipager.policy import validate_policy_files
    from aipager.scope import ScopeConfigError, load_scopes

    scopes = None
    try:
        loaded = load_scopes()
        if loaded is not None:
            scopes = loaded[0]
    except ScopeConfigError as e:
        print(f"aipager.yaml: {e}")
        return 1

    problems = validate_policy_files(scopes=scopes)
    if problems:
        print("policy validation failed:")
        for p in problems:
            print(f"  • {p}")
        return 1
    print("policy OK")
    return 0
