"""conftest's ``_isolate_status_line_files`` keeps every test off the real
``/tmp/claude-status-*.json`` files live sessions write. Kept apart from
test_statusline_file.py, which pins its own dir on top, so this checks
conftest alone. Reads and writes nothing."""

from aipager import statusline_file


def test_conftest_points_status_line_reads_away_from_real_tmp(tmp_path):
    assert statusline_file.STATUS_DIR != "/tmp"
    path = statusline_file.status_file_path("claude-x")
    assert not str(path).startswith("/tmp/claude-status-")
    assert path.parent.is_relative_to(tmp_path)
