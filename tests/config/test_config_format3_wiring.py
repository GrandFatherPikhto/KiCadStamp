# tests/config/test_config_format3_wiring.py
"""У4 rework — the WIRING cells the U4 acceptance found empty, plus finding Н1.

  * S2 — profile_copy disables the format-3 stamp while writing.
  * S3 — opening a profile in the GUI sets the active root; closing clears it.
  * S5 — an explicit `graph_root` beats the process-wide active root.
  * S6 — cli_main sets the active root from --config.
  * Н1 — the writer stamp runs at STAGE time too, and the format-3 check and the
    stamp index read through cached_file_read, so they see the SAME graph the
    loader does (the working set), not the older bytes on disk.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from kicadstamp.config import load_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.config_writer import merge_write, upsert_entity, write_config_file
from kicadstamp.config_working_set import (
    WORKING_SET,
    active_graph_root,
    is_stamp_disabled,
    set_active_graph_root,
)
from kicadstamp.exceptions import ValidationError
from tests.fakes.format3 import (  # noqa: F401
    active_root,
    det_uuid,
    format3,
    mint_format3_files,
)


def _write_graph(tmp_path: Path, files: dict) -> dict:
    minted = mint_format3_files(files)
    for name, d in minted.items():
        (tmp_path / name).write_text(dict_to_sexp(d, format_number=3), encoding="utf-8")
    return minted


@pytest.fixture
def working_set(monkeypatch):
    # monkeypatch, not a bare assignment: the process-global working set must be
    # restored even if the test fails (repo hygiene guard).
    monkeypatch.setattr(WORKING_SET, "enabled", True)
    WORKING_SET.clear()
    yield WORKING_SET
    WORKING_SET.clear()


# ── S3: the GUI root_changed wiring sets / clears the active root ──────────

def test_gui_root_change_sets_and_clears_the_active_root(monkeypatch):
    from gui.dock_hub import DockHub

    class _Fake:
        def _update_dirty_indicator(self):
            pass

    monkeypatch.setattr(WORKING_SET, "enabled", False)
    set_active_graph_root(None)

    DockHub._on_root_changed_for_working_set(_Fake(), "/tmp/some/root.sexp")
    assert active_graph_root() == Path("/tmp/some/root.sexp")

    DockHub._on_root_changed_for_working_set(_Fake(), None)
    assert active_graph_root() is None
    WORKING_SET.clear()


# ── S6: the CLI sets the active root from --config ─────────────────────────

def test_cli_sets_the_active_root_from_config():
    from kicadstamp.cli_main import set_cli_active_root

    set_active_graph_root(None)
    try:
        set_cli_active_root(SimpleNamespace(config="/tmp/profiles/p/config.sexp"))
        assert active_graph_root() == Path("/tmp/profiles/p/config.sexp")

        set_cli_active_root(SimpleNamespace())  # a subcommand with no --config
        assert active_graph_root() is None
    finally:
        set_active_graph_root(None)


# ── S5: an explicit graph_root beats the active root ───────────────────────

def test_explicit_graph_root_beats_the_active_root(format3, active_root, tmp_path):
    # Graph A: cell "cap" -> its own (det) uuid.
    _write_graph(tmp_path, {"a.sexp": {"cells": {"cap": {}}}})
    a = tmp_path / "a.sexp"
    # Graph B: a DIFFERENT uuid for a cell of the same name.
    b_uuid = "00000000-0000-0000-0000-0000000000bb"
    b = tmp_path / "b.sexp"
    b.write_text(dict_to_sexp({"cells": {"cap": {"uuid": b_uuid}}}, format_number=3),
                 encoding="utf-8")
    active_root(a)

    write_config_file(
        b,
        {"cells": {"cap": {"uuid": b_uuid}},
         "entities": [{"name": "e", "cell": "cap"}]},
        graph_root=b,
    )

    raw = b.read_text(encoding="utf-8")
    assert b_uuid in raw
    assert det_uuid("cells:cap") not in raw, "resolved by B, not by the active A"
    assert "(entities" in raw


# ── S2: profile_copy disables the stamp while writing ──────────────────────

def test_profile_copy_disables_the_stamp_while_writing(format3, active_root, tmp_path,
                                                       monkeypatch):
    import kicadstamp.config.profile_copy as pc

    src = tmp_path / "src.sexp"
    tgt = tmp_path / "tgt.sexp"
    _write_graph(tmp_path, {"src.sexp": {"cells": {"cap": {}}},
                            "tgt.sexp": {"cells": {}}})
    active_root(tgt)

    observed = []
    original = pc._write_dict_section

    def spy(target, section, entries):
        observed.append(is_stamp_disabled())
        return original(target, section, entries)

    monkeypatch.setattr(pc, "_write_dict_section", spy)

    pc.copy_cell(src, "cap", tgt, target_root=None)

    assert observed, "the writer never ran"
    assert all(observed), "profile_copy must disable the format-3 stamp while writing"


# ── Н1: the working set is stamped at STAGE time ───────────────────────────

def test_staged_new_record_has_uuids_before_save(format3, active_root, working_set,
                                                 tmp_path):
    root = tmp_path / "root.sexp"
    _write_graph(tmp_path, {"root.sexp": {"cells": {"cap": {}}}})
    active_root(root)

    upsert_entity(root, {"name": "e_new", "cell": "cap"})   # staged, NOT saved

    cfg, _ctx = load_config(str(root))
    e = cfg.entities[0]
    assert e.uuid is not None, "the staged record must already carry a uuid"
    assert e.cell_uuid == det_uuid("cells:cap"), "the staged reference must be resolved"


def test_load_sees_the_working_set_in_the_format3_check(format3, active_root,
                                                        working_set, tmp_path):
    root = tmp_path / "root.sexp"
    _write_graph(tmp_path, {"root.sexp": {"cells": {"cap": {}}}})
    active_root(root)

    # Stage INVALID format-3 content directly (bypassing the writer stamp): the
    # check must SEE it and fatal, or the program validates the disk graph while
    # working in the staged one.
    working_set.stage_write(root, {"cells": {"cap": {"comment": "no uuid"}}})

    with pytest.raises(ValidationError):
        load_config(str(root))


def test_reference_to_a_record_only_in_the_working_set_resolves(format3, active_root,
                                                               working_set, tmp_path):
    root = tmp_path / "root.sexp"
    sub = tmp_path / "sub.sexp"
    _write_graph(tmp_path, {"root.sexp": {"include": ["sub.sexp"], "cells": {}},
                            "sub.sexp": {"cells": {}, "entities": []}})
    active_root(root)

    # A new cell lives ONLY in a staged edit of the INCLUDED file; a reference to
    # it from the root must still resolve (the stamp index sees the working set).
    merge_write(sub, {"cells": {"new_cap": {}}}, section="cells")
    upsert_entity(root, {"name": "e_ref", "cell": "new_cap"})

    cfg, _ctx = load_config(str(root))
    assert cfg.entities[0].cell_uuid is not None
