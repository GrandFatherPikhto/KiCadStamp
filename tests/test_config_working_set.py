# tests/test_config_working_set.py
"""Direct unit tests for the ConfigWorkingSet staging layer (2026-09-01, plan
techdocs/handoff/deepseek/plan_2026_09_01_project_save_model.md).

The working set is a process-global singleton that is OFF by default — the
pre-existing suite (and the CLI) never enables it, so they keep the physical
write path. These tests enable it explicitly and assert the staged-read /
staged-write / flush / backup behaviour."""
import pytest

from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.config_writer import merge_write, read_data, upsert_list_entry
from kicadstamp.config_working_set import WORKING_SET, backup_to_history


def _write_sexp(path, data: dict) -> None:
    path.write_text(dict_to_sexp(data), encoding="utf-8")


def _read_sexp(path) -> dict:
    return sexp_to_dict(path.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _isolate_working_set():
    """Reset the process-global working set around every test (it is a
    singleton, so tests must not leak staged state or listeners into each
    other)."""
    ws = WORKING_SET
    old_enabled = ws.enabled
    old_listeners = list(ws._listeners)
    ws.clear()
    ws._listeners = []
    ws.enabled = False
    yield
    ws.clear()
    ws._listeners = []
    ws.enabled = old_enabled
    for fn in old_listeners:
        ws.add_listener(fn)


# ── read/write interception (steps 1) ────────────────────────────────────

def test_disabled_by_default_writes_physically(tmp_path):
    path = tmp_path / "cfg.sexp"
    merge_write(path, {"cells": {"c1": {}}}, section="cells")
    # Working set untouched; file on disk.
    assert not WORKING_SET.is_dirty()
    assert "c1" in _read_sexp(path)["cells"]


def test_stage_write_shadows_disk_for_reads(tmp_path):
    path = tmp_path / "cfg.sexp"
    _write_sexp(path, {"cells": {"c1": {}}})
    WORKING_SET.enabled = True
    merge_write(path, {"cells": {"c2": {}}}, section="cells")
    # Not on disk yet — read_data returns the staged content.
    assert "c2" not in _read_sexp(path)["cells"]
    assert "c2" in read_data(path)["cells"]
    assert WORKING_SET.is_dirty()
    # Merge accumulates against staged content.
    merge_write(path, {"cells": {"c3": {}}}, section="cells")
    assert set(read_data(path)["cells"]) == {"c1", "c2", "c3"}


def test_stage_new_file_is_readable_before_it_exists(tmp_path):
    path = tmp_path / "brand_new.sexp"  # does not exist on disk
    WORKING_SET.enabled = True
    upsert_list_entry(path, "thermal_via_arrays", {"name": "A", "pad": "9"})
    # read_data must see the staged __new__ file even though it isn't on disk.
    data = read_data(path)
    assert data["thermal_via_arrays"][0]["name"] == "A"
    assert not path.exists()


def test_stage_invalidates_graph_cache(tmp_path):
    from kicadstamp.config.loader import load_config
    root = tmp_path / "root.sexp"
    _write_sexp(root, {"cells": {"c1": {}}})
    load_config(str(root))  # prime the graph cache
    WORKING_SET.enabled = True
    merge_write(root, {"cells": {"c2": {}}}, section="cells")
    cfg, _ = load_config(str(root))
    # Without invalidate_graph_path on stage this would hand back the stale
    # cached Config without "c2".
    assert "c2" in cfg.cells


def test_second_staged_mutation_is_visible_to_graph_cache(tmp_path):
    """Regression (plan 2026_09_04_staged_delete_stale_tree_and_save_hotkey,
    Bug A — Config-tree Delete left the leaf until Save/restart): once a graph
    result has been recomputed OVER STAGED CONTENT (its trace records no disk
    read for the staged file), a FURTHER staged mutation must still be visible.
    The staged-content branch of cached_file_read used to return BEFORE
    recording the path in the active graph-computation trace, so the recomputed
    graph entry listed NO files -> stage_write's invalidate_graph_path() had
    nothing to evict and the graph kept handing back the pre-mutation state
    until a physical disk write changed the file's mtime."""
    from kicadstamp.config.loader import load_config
    from kicadstamp.config_writer import read_data, write_data

    root = tmp_path / "root.sexp"
    _write_sexp(root, {"cells": {"c1": {}, "c2": {}}})
    load_config(str(root))  # prime the graph cache from disk
    WORKING_SET.enabled = True

    # First staged mutation (add c3) — recomputes the graph entry over staged
    # content. This is where the stale entry's file set loses the staged path.
    merge_write(root, {"cells": {"c3": {}}}, section="cells")
    assert "c3" in load_config(str(root))[0].cells

    # Second staged mutation (remove c1) — the delete shape that used to stay
    # invisible until Save/restart.
    data = read_data(root)
    del data["cells"]["c1"]
    write_data(root, data)
    cfg, _ = load_config(str(root))
    assert "c1" not in cfg.cells
    assert "c3" in cfg.cells


def test_dirty_flag_and_listeners(tmp_path):
    path = tmp_path / "cfg.sexp"
    _write_sexp(path, {})
    WORKING_SET.enabled = True
    calls = []
    WORKING_SET.add_listener(lambda: calls.append(1))
    merge_write(path, {"cells": {"c1": {}}}, section="cells")
    assert calls == [1]
    assert WORKING_SET.is_dirty()
    WORKING_SET.clear()
    assert not WORKING_SET.is_dirty()
    assert calls == [1, 1]  # clear() notifies too


# ── flush (step 5) ───────────────────────────────────────────────────────

def test_flush_writes_clears_and_backs_up(tmp_path):
    root = tmp_path / "root.sexp"
    _write_sexp(root, {"cells": {"c1": {}}})
    WORKING_SET.enabled = True
    merge_write(root, {"cells": {"c2": {}}}, section="cells")
    assert "c2" not in _read_sexp(root)["cells"]

    errors = WORKING_SET.flush(root)

    assert errors == []
    assert "c2" in _read_sexp(root)["cells"]   # committed to disk
    assert not WORKING_SET.is_dirty()          # working set cleared
    assert WORKING_SET.enabled is True         # staging re-enabled
    backups = list((tmp_path / ".history").glob("root_*.sexp"))
    assert backups                             # dated backup in .history/


def test_flush_aborts_on_invalid_staged_graph_without_writing(tmp_path):
    root = tmp_path / "root.sexp"
    _write_sexp(root, {})                       # empty, valid root on disk
    WORKING_SET.enabled = True
    # Individually-plausible staged files that are invalid TOGETHER: root
    # includes bad.sexp, whose cell carries an unknown key.
    WORKING_SET.stage_write(root, {"include": ["bad.sexp"]})
    bad = tmp_path / "bad.sexp"
    WORKING_SET.stage_write(bad, {"cells": {"c1": {"unknown_key": 1}}})

    errors = WORKING_SET.flush(root)

    assert errors                              # validation aborted the flush
    assert _read_sexp(root) == {}              # nothing written
    assert not bad.exists()                    # __new__ file not created
    assert not (tmp_path / ".history").exists()  # no backups made
    assert WORKING_SET.is_dirty()              # still staged (not cleared)


def test_flush_creates_new_and_deletes_staged_files(tmp_path):
    root = tmp_path / "root.sexp"
    victim = tmp_path / "victim.sexp"
    _write_sexp(root, {"include": ["victim.sexp"]})
    _write_sexp(victim, {"cells": {"c1": {}}})
    WORKING_SET.enabled = True
    # Stage: root drops its include, victim is deleted, and a brand-new file
    # is created.
    WORKING_SET.stage_write(root, {"include": []})
    WORKING_SET.stage_delete(victim)
    fresh = tmp_path / "fresh.sexp"
    WORKING_SET.stage_write(fresh, {"cells": {"f1": {}}})

    errors = WORKING_SET.flush(root)

    assert errors == []
    assert not victim.exists()                 # __deleted__ removed
    assert fresh.exists()                      # __new__ created
    assert _read_sexp(root) == {"include": []} # include dropped


# ── backup_to_history (step 2) ───────────────────────────────────────────

def test_backup_to_history_dated_and_never_overwrites(tmp_path):
    root = tmp_path / "root.sexp"
    _write_sexp(root, {"cells": {"c1": {}}})
    b1 = backup_to_history(root, root.parent)
    b2 = backup_to_history(root, root.parent)
    assert b1.parent == (tmp_path / ".history")
    assert b1 != b2                            # a second backup never clobbers
    assert b1.exists() and b2.exists()
    assert b1.name.startswith("root_") and b1.name.endswith(".sexp")
    assert b2.name.startswith("root_") and b2.name.endswith(".sexp")


def test_backup_to_history_encodes_subdir_paths(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    cfg = sub / "a.sexp"
    _write_sexp(cfg, {})
    backup = backup_to_history(cfg, tmp_path)
    # The relative path is encoded in the name so two same-stem files from
    # different directories never collide.
    assert "sub__a_" in backup.name


# ── Д4: the format guard on the GUI's MAIN write path ──────────────────────
# With a project open every dock edit is staged, and ConfigWorkingSet.flush is
# what puts it on disk — so the refusal write_config_file makes has to hold here
# too, or the one case the refusal exists for stays silent on the busiest path.

def test_flush_refuses_to_overwrite_a_file_that_became_newer(tmp_path):
    """Step 1 of the flush cannot see this: it validates the staged OVERLAY, not
    the disk. Before Д4 the flush wrote our older content over the newer file and
    reported NO error."""
    from kicadstamp.config.format_version import CURRENT_FORMAT

    root = tmp_path / "root.sexp"
    _write_sexp(root, {"cells": {"c1": {}}})
    WORKING_SET.enabled = True
    merge_write(root, {"cells": {"c2": {}}}, section="cells")

    # Meanwhile another machine lifted the file and Syncthing delivered it.
    newer = f"(kicadstamp-config\n  (version {CURRENT_FORMAT + 1})\n  (cells)\n)\n"
    root.write_text(newer, encoding="utf-8")

    errors = WORKING_SET.flush(root)

    assert errors, "the refusal must reach the flush's error list"
    assert root.read_text(encoding="utf-8") == newer, "the disk file is untouched"


def test_flush_leaves_no_second_copy_beside_the_file(tmp_path):
    """The `.history/` copy taken before the write IS the copy (decided during the
    Т3 acceptance: no second copy of the same bytes), so a format-1 file saved
    through the GUI gets the number and a history entry, and no `.bak`."""
    from kicadstamp.config.format_version import CURRENT_FORMAT

    root = tmp_path / "root.sexp"
    root.write_text("(kicadstamp-config\n  (cells)\n)\n", encoding="utf-8")  # format 1
    WORKING_SET.enabled = True
    merge_write(root, {"cells": {"c1": {}}}, section="cells")

    errors = WORKING_SET.flush(root)

    assert errors == []
    assert list(tmp_path.glob("root.sexp.bak.*")) == []
    assert list((tmp_path / ".history").glob("*")), "the history copy exists"
    assert f"(version {CURRENT_FORMAT})" in root.read_text(encoding="utf-8")


def test_flush_does_not_write_a_file_whose_copy_failed(tmp_path):
    """«Файл, копия которого не снялась, не пишем» (Т3 acceptance): the copy is
    the only way back from a bad Save, so a failed copy must stop THAT file's
    write instead of being reported and ignored — which is what it used to do."""
    root = tmp_path / "root.sexp"
    original = "(kicadstamp-config\n  (cells)\n)\n"
    root.write_text(original, encoding="utf-8")
    # A FILE where the .history/ DIRECTORY must be: the copy cannot come off.
    (tmp_path / ".history").write_text("a file, not a directory", encoding="utf-8")
    WORKING_SET.enabled = True
    merge_write(root, {"cells": {"c1": {}}}, section="cells")

    errors = WORKING_SET.flush(root)

    assert any("history backup failed" in e for e in errors), errors
    assert root.read_text(encoding="utf-8") == original, "not written"
