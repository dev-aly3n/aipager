"""The aipager release, as a constant the hook can read for free.

``aipager.__version__`` reads the installed metadata (importlib.metadata,
about 30 ms), which the hook and the status line never pay for. They
stamp this constant on their datagrams instead, so the daemon can tell
when Claude Code's hooks already run newer code than it does. Imports
nothing. Bumped with every release (CONTRIBUTING.md, step 1);
``tests/test_version_file.py`` keeps it equal to pyproject's version.
"""

__version__ = "1.3.3"
