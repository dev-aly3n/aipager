"""design.md success criterion 12:

    "The rendered unit contains LoadCredential=, Environment=PATH=, and
    StartLimitIntervalSec=0 inside [Unit]; contains no
    After=network-online.target and no ExecStartPre."

entrypoints.md's "NOT exported" section is explicit that this must be
checked by "read[ing] the rendered file on disk after `service
install`, not the Python template" -- this test never imports the
template constant, only the file `aipager service install` actually
writes. This exercises the "no existing unit -> write" branch (a fresh
install, design.md section 7), which needs no interactive prompt.

Every ``systemctl``/``loginctl`` invocation is intercepted by this
package's autouse ``fake_service_run`` fixture -- see conftest.py's
module docstring point 3 for why that's not optional on this machine.

IMPORTANT: ``LINUX_UNIT_PATH`` must be read via LIVE module-attribute
access (``service_mod.LINUX_UNIT_PATH`` at point of use), never via
``from aipager.service import LINUX_UNIT_PATH`` at module top level --
that form binds the value at test-COLLECTION time, before
``tests/conftest.py``'s ``_isolate_home_paths`` autouse fixture has
patched it, so it would silently capture the operator's REAL
``~/.config/systemd/user/aipager.service`` path instead. Caught while
writing this file: the first draft did exactly that and its own
``assert not ...exists()`` isolation check correctly failed against a
real path on this machine.
"""
from __future__ import annotations

import aipager.service as service_mod


def _parse_unit_sections(text: str) -> dict[str, list[str]]:
    """A systemd unit isn't strict INI (repeated keys, no value quoting
    rules), so this is a deliberately simple section-splitter rather
    than configparser -- good enough to check "does line X appear
    between [Unit] and the next [Section] header".
    """
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped  # keep brackets: "[Unit]", "[Service]", ...
            sections[current] = []
        elif current is not None:
            sections[current].append(stripped)
    return sections


def test_fresh_install_unit_contains_required_lines_in_correct_sections(
    tmp_path,
):
    assert not service_mod.LINUX_UNIT_PATH.exists(), (
        "test isolation bug: a unit file already exists before this test "
        "wrote anything"
    )

    rc = service_mod._install_linux(yes=False)
    assert rc == 0

    assert service_mod.LINUX_UNIT_PATH.exists(), (
        "service install did not write a unit file"
    )
    text = service_mod.LINUX_UNIT_PATH.read_text()
    sections = _parse_unit_sections(text)

    assert "[Unit]" in sections and "[Service]" in sections

    assert any(line == "StartLimitIntervalSec=0" for line in sections["[Unit]"]), (
        f"StartLimitIntervalSec=0 must be inside [Unit] (systemd v229+ "
        f"requirement -- under [Service] it silently no-ops), got "
        f"[Unit] lines: {sections['[Unit]']!r}"
    )
    assert not any(
        line == "StartLimitIntervalSec=0" for line in sections.get("[Service]", [])
    ), "StartLimitIntervalSec=0 must NOT be inside [Service] (silent no-op)"

    assert any(line.startswith("LoadCredential=") for line in sections["[Service]"])
    assert any(line.startswith("Environment=PATH=") for line in sections["[Service]"])

    assert "After=network-online.target" not in text
    assert "ExecStartPre" not in text


def test_fresh_install_environment_path_is_non_empty_and_includes_local_bin(
    tmp_path,
):
    service_mod._install_linux(yes=True)

    text = service_mod.LINUX_UNIT_PATH.read_text()
    sections = _parse_unit_sections(text)
    path_lines = [
        line for line in sections["[Service]"] if line.startswith("Environment=PATH=")
    ]
    assert len(path_lines) == 1
    path_value = path_lines[0][len("Environment=PATH="):]
    assert path_value.strip() != "", "Environment=PATH= must not be empty"
    assert "/.local/bin" in path_value, (
        f"design.md: '~/.local/bin ensured first' -- got PATH={path_value!r}"
    )
