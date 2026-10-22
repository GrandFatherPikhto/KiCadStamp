# tests/gui/test_sheet_narrowing_warning.py
"""Cells for `gui/docks/sheet_narrowing_warning.warn_once` — the V.3 Log line.

The body was moved out of `gui/dock_hub.py` on 22.10.2026 (plan_2026_10_22_checks_tab, Ч1-Т3)
so the hub only shrinks; the behaviour must stay byte-identical, which is why the move comes
with these cells. The WORDING is still not this module's: it belongs to
`kicadstamp.sheet_names.sheet_narrowing_disabled_line` (pinned by
`tests/config/test_anchor_sheet_root_sheet.py`), so the line host is stubbed here and only the
CAUSE it is handed is watched.

What is pinned here is WHEN the line appears: once per root, only with a live board, only when
the sheet-name map is empty — and never twice, not even for a config that fails to load.
"""
import logging
from types import SimpleNamespace

import pytest

from gui.docks import sheet_narrowing_warning


class _Connection:
    def __init__(self, connected):
        self.is_connected = connected


@pytest.fixture
def line_calls(monkeypatch):
    """The wording host is stubbed; the call itself is what these cells count."""
    calls = []
    monkeypatch.setattr(
        "kicadstamp.sheet_names.sheet_narrowing_disabled_line",
        lambda schematic_dir, files: calls.append((schematic_dir, files)) or "THE LINE")
    return calls


def _config(tmp_path, monkeypatch, *, sheet_names=None, broken=False):
    """A root file plus a `load_config` that answers without touching disk."""
    config = tmp_path / "config.sexp"
    config.write_text("(kicadstamp-config)\n", encoding="utf-8")

    def _load(path):
        if broken:
            raise ValueError("broken config (the docks report it, this line must not)")
        cfg = SimpleNamespace(schematic_dir=None, schematic_files=["Test02.kicad_sch"])
        return cfg, SimpleNamespace(sheet_names=dict(sheet_names or {}))
    monkeypatch.setattr("kicadstamp.config.load_config", _load)
    return config


def test_the_line_is_printed_once_per_root(tmp_path, monkeypatch, line_calls, caplog):
    """Two selection ticks, one line: `checked` is the caller's set and survives both."""
    root = _config(tmp_path, monkeypatch)
    checked = set()

    with caplog.at_level(logging.INFO):
        sheet_narrowing_warning.warn_once(root, _Connection(True), checked)
        sheet_narrowing_warning.warn_once(root, _Connection(True), checked)

    assert caplog.messages == ["THE LINE"]
    assert checked == {str(root)}


def test_a_config_with_sheet_names_stays_silent(tmp_path, monkeypatch, line_calls, caplog):
    """Narrowing works there — nothing to say. The root is still remembered (no re-read)."""
    root = _config(tmp_path, monkeypatch, sheet_names={"Channel_0": {}})
    checked = set()

    with caplog.at_level(logging.INFO):
        sheet_narrowing_warning.warn_once(root, _Connection(True), checked)

    assert caplog.messages == []
    assert checked == {str(root)}


def test_without_a_board_the_root_is_not_spent(tmp_path, monkeypatch, line_calls, caplog):
    """The line is only actionable with a live board — and a root opened BEFORE connecting
    must still get its one line once the board appears."""
    root = _config(tmp_path, monkeypatch)
    checked = set()

    with caplog.at_level(logging.INFO):
        sheet_narrowing_warning.warn_once(root, _Connection(False), checked)
        assert caplog.messages == []
        assert checked == set()

        sheet_narrowing_warning.warn_once(root, _Connection(True), checked)
        sheet_narrowing_warning.warn_once(root, _Connection(True), checked)

    assert caplog.messages == ["THE LINE"]


def test_a_broken_config_is_not_re_read_on_every_tick(tmp_path, monkeypatch, line_calls,
                                                      caplog):
    """`load_config` raising is the docks' news, not this line's — but the root IS spent, so
    a broken config cannot cost a load per selection tick."""
    root = _config(tmp_path, monkeypatch, broken=True)
    checked = set()

    with caplog.at_level(logging.INFO):
        sheet_narrowing_warning.warn_once(root, _Connection(True), checked)
        sheet_narrowing_warning.warn_once(root, _Connection(True), checked)

    assert caplog.messages == []
    assert checked == {str(root)}


def test_no_project_is_a_no_op(tmp_path, monkeypatch, line_calls, caplog):
    """No root (project closed): nothing to look at, nothing remembered."""
    checked = set()

    with caplog.at_level(logging.INFO):
        sheet_narrowing_warning.warn_once(None, _Connection(True), checked)

    assert caplog.messages == []
    assert checked == set()
