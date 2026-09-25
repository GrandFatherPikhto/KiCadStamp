# tests/test_config_format_upgrade_on_disk.py
"""Т4: the on-disk format upgrade — `upgrade_graph_on_disk`, run by `load_config`.

Spec: `plan_2026_09_24_config_format_version.md` §Т4 plus «Уточнения к Т4»
(У1–У5), and the clarifications WIN wherever the two disagree.

Cells, by axis (rule 35 — one row per behaviour, so a failing row never hides its
neighbour):

- graph     — every file of the `include:` graph, each file ONCE (a diamond is
              one file, not two); the root is lifted first;
- bytes     — the new text is the WRITER's own output for the lifted data (У1:
              a rebuild, whose side effect is normalization — aliases become
              canonical, defaults are dropped), and the `.bak` keeps the OLD
              bytes;
- quiet     — an already-current graph is not touched at all (no write, no
              `.bak`, `mtime` unchanged); a second open writes nothing and does
              not even parse for the number (the probe cache is warm);
- У2        — a real fixture with EXPLICIT defaults is lifted with no ERROR
              (comparing raw dicts instead of `_strip_defaults` would refuse it
              for ever), and a serializer that would lose meaning is refused with
              the file untouched;
- refusal   — one file NEWER than this build, anywhere in the graph, stops the
              WHOLE sweep before the first write;
- failure   — an unwritable directory leaves the file as it is, logs the reason
              and still loads;
- У3        — unsaved changes in the GUI working set stop the upgrade (the Save
              lifts that file itself);
- JSON      — the JSON side is lifted by the same sweep.
"""
import json
import logging
import os
from pathlib import Path

import pytest

from kicadstamp.config import format_version as fv
from kicadstamp.config.format_version import CURRENT_FORMAT, read_version
from kicadstamp.config.loader import load_config
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.config.upgrade_on_disk import upgrade_graph_on_disk
from kicadstamp.config_working_set import WORKING_SET
from kicadstamp.config_writer import merge_write
from kicadstamp.exceptions import ValidationError

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_working_set():
    """Same isolation as tests/test_config_working_set.py: the working set is a
    process-global singleton, so a leaked staged state would decide the У3 cell
    for every test that follows."""
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


def _old_text(data: dict) -> str:
    """The same content, one number older: `dict_to_sexp` and then the root line
    removed — the same shape the Т5 lift left the tracked fixtures in."""
    return dict_to_sexp(data).replace(f"  (version {CURRENT_FORMAT})\n", "")


def _sexp_graph(tmp_path: Path) -> tuple[Path, Path]:
    """root.sexp (format 1) including sub.sexp (format 1), no key shared."""
    root = tmp_path / "root.sexp"
    sub = tmp_path / "sub.sexp"
    root.write_text(_old_text({"include": ["sub.sexp"], "cells": {"c1": {}}}),
                    encoding="utf-8")
    sub.write_text(_old_text({"cells": {"c2": {}}}), encoding="utf-8")
    return root, sub


# ── the lift itself ────────────────────────────────────────────────────────

def test_the_whole_graph_is_lifted_and_the_old_bytes_are_kept(tmp_path):
    root, sub = _sexp_graph(tmp_path)
    old_root = root.read_text(encoding="utf-8")
    old_sub = sub.read_text(encoding="utf-8")
    assert read_version(root) == 1 and read_version(sub) == 1

    cfg, _ = load_config(str(root))

    assert sorted(cfg.cells) == ["c1", "c2"], "the in-memory content is unchanged"
    assert read_version(root) == CURRENT_FORMAT
    assert read_version(sub) == CURRENT_FORMAT
    # У1, the load-bearing row: the bytes are the WRITER's output for the lifted
    # data. An implementation that merely INSERTS the version line into the old
    # text passes every other assertion here and fails this one.
    assert root.read_text(encoding="utf-8") == dict_to_sexp(
        {"include": ["sub.sexp"], "cells": {"c1": {}}})
    assert sub.read_text(encoding="utf-8") == dict_to_sexp({"cells": {"c2": {}}})
    baks = {p.name.split(".")[0]: p for p in tmp_path.glob("*.bak.*")}
    assert sorted(baks) == ["root", "sub"]
    assert baks["root"].read_text(encoding="utf-8") == old_root
    assert baks["sub"].read_text(encoding="utf-8") == old_sub


def test_the_lift_rebuilds_so_a_legacy_alias_comes_out_canonical(tmp_path):
    """У1 from the other side: the rebuild is what makes the first lift NORMALIZE
    a live file — the legacy `rules:` alias comes out as `(chains)`. Inserting the
    number instead would leave `(rules)` in place."""
    root = tmp_path / "root.sexp"
    root.write_text(_old_text({"rules": []}), encoding="utf-8")
    assert "(rules)" in root.read_text(encoding="utf-8")

    load_config(str(root))

    text = root.read_text(encoding="utf-8")
    assert "(chains)" in text
    assert "rules" not in text
    assert text == dict_to_sexp({"chains": []})


def test_the_lift_warns_with_the_path_to_the_previous_version(tmp_path, caplog):
    """The WARNING is a Log LINE (no modal), and it names the `.bak`: that path is
    the whole rollback story, since there is no undo (Т7 documents the same)."""
    root, _sub = _sexp_graph(tmp_path)

    with caplog.at_level(logging.WARNING, logger="kicadstamp.config.upgrade_on_disk"):
        load_config(str(root))

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2, warnings
    assert all("outdated, lifted to" in m for m in warnings)
    assert all(".bak." in m for m in warnings)


def test_a_json_file_is_lifted_by_the_same_sweep(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"cells": {"c1": {}}}), encoding="utf-8")   # format 1
    assert read_version(p) == 1

    cfg, _ = load_config(str(p))

    assert sorted(cfg.cells) == ["c1"]
    assert json.loads(p.read_text(encoding="utf-8"))["version"] == CURRENT_FORMAT
    baks = list(tmp_path.glob("c.json.bak.*"))
    assert len(baks) == 1
    assert json.loads(baks[0].read_text(encoding="utf-8")) == {"cells": {"c1": {}}}


def test_a_diamond_lifts_the_shared_file_and_its_backup_once(tmp_path):
    """One physical file reached from two branches is ONE file: the walker used
    for display does not dedupe, so the dedupe has to live in the sweep — two
    backs of one file would be two chances to restore the wrong bytes."""
    root = tmp_path / "root.sexp"
    root.write_text(_old_text({"include": ["a.sexp", "b.sexp"],
                               "cells": {"r1": {}}}), encoding="utf-8")
    for name, cell in (("a.sexp", "a1"), ("b.sexp", "b1")):
        (tmp_path / name).write_text(
            _old_text({"include": ["shared.sexp"], "cells": {cell: {}}}),
            encoding="utf-8")
    (tmp_path / "shared.sexp").write_text(_old_text({"cells": {"s1": {}}}),
                                          encoding="utf-8")

    cfg, _ = load_config(str(root))

    assert sorted(cfg.cells) == ["a1", "b1", "r1", "s1"]
    assert len(list(tmp_path.glob("shared.sexp.bak.*"))) == 1
    assert read_version(tmp_path / "shared.sexp") == CURRENT_FORMAT


# ── quiet paths: nothing to do, and nothing done ───────────────────────────

def test_an_already_current_graph_is_not_touched_at_all(tmp_path):
    root = tmp_path / "root.sexp"
    root.write_text(dict_to_sexp({"cells": {"c1": {}}}), encoding="utf-8")
    text = root.read_text(encoding="utf-8")
    mtime = root.stat().st_mtime_ns

    cfg, _ = load_config(str(root))

    assert sorted(cfg.cells) == ["c1"]
    assert root.read_text(encoding="utf-8") == text
    assert root.stat().st_mtime_ns == mtime
    assert list(tmp_path.glob("*.bak.*")) == []


def test_a_repeat_open_writes_nothing_and_parses_nothing_for_the_number(tmp_path, monkeypatch):
    """У4: the probe is keyed by `(path, mtime_ns)`, so a repeat open costs one
    os.stat per file — no write, no `.bak`, not even a parse for the number.

    Measured and stated out loud: the open RIGHT AFTER a lift is cold again, by
    construction — the lift changed the mtime, which is a new cache key (that open
    also recomputes the graph, so it parses every file anyway). The steady state is
    what this cell pins, and it is the state the GUI lives in: up to six load
    bodies at startup, one probe per file per change."""
    root, _sub = _sexp_graph(tmp_path)
    load_config(str(root))                      # 1: lifts both files
    mtime = root.stat().st_mtime_ns
    load_config(str(root))                      # 2: cold probe again (new mtime)
    assert root.stat().st_mtime_ns == mtime, "the lift happened once, not twice"
    baks = sorted(p.name for p in tmp_path.glob("*.bak.*"))
    assert len(baks) == 2

    calls = []
    real = fv.parse_raw_file

    def counting(path):
        calls.append(str(path))
        return real(path)

    monkeypatch.setattr(fv, "parse_raw_file", counting)
    cfg, _ = load_config(str(root))             # 3: everything warm

    assert sorted(cfg.cells) == ["c1", "c2"]
    assert calls == [], "the warm probe must not parse the file again"
    assert root.stat().st_mtime_ns == mtime
    assert sorted(p.name for p in tmp_path.glob("*.bak.*")) == baks


# ── У2: the check before the write is by MEANING ───────────────────────────

def test_an_explicit_default_value_is_dropped_by_the_rebuild_and_still_lifts(tmp_path, caplog):
    """У2 with teeth, and the reason this cell exists.

    `Cell.layer` defaults to `'F.Cu'` and the writer DROPS a field equal to its
    default — it will not even produce such a line itself — so the file has to be
    hand-written, which is exactly what an older tool or a hand edit leaves on
    disk. The rebuilt text then comes back WITHOUT the `layer`, so it is not the
    raw dict, and a check comparing raw dicts would log an ERROR and refuse this
    file on every open, for ever. Measured 25.09.2026: the mutation «compare by
    raw dict» turns exactly this cell red."""
    root = tmp_path / "root.sexp"
    text = _old_text({"cells": {"c1": {}}})
    assert '(cell "c1")' in text
    root.write_text(text.replace('(cell "c1")', '(cell "c1"\n      (layer "F.Cu"))'),
                    encoding="utf-8")
    assert "layer" in root.read_text(encoding="utf-8"), "the default is written out"

    with caplog.at_level(logging.ERROR):
        lifted = upgrade_graph_on_disk(root)                    # the sweep alone

    assert [p.name for p in lifted] == ["root.sexp"], "lifted, not refused"
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []
    after = root.read_text(encoding="utf-8")
    assert "layer" not in after, "the rebuild dropped the default-valued field"
    assert after == dict_to_sexp({"cells": {"c1": {}}})
    assert read_version(root) == CURRENT_FORMAT


def test_a_real_fixture_with_explicit_values_lifts_without_one_error(tmp_path, caplog):
    """У2 on REAL content: `internal_mount/config.sexp` is lifted with no ERROR.

    Measured for this fixture: its rebuild is byte-identical (`(xy 0.0 0.0)` and
    `(origin)` are re-serialized as they are, `_strip_defaults` is a no-op on it),
    so this cell is the no-FALSE-refusal row for a real profile — the teeth of У2
    are in the cell above."""
    src = FIXTURES / "internal_mount" / "config.sexp"
    text = src.read_text(encoding="utf-8")
    assert f"(version {CURRENT_FORMAT})" in text, "the fixture itself is current"
    dst = tmp_path / "config.sexp"
    dst.write_text(text.replace(f"  (version {CURRENT_FORMAT})\n", ""),
                   encoding="utf-8")                                   # format 1

    with caplog.at_level(logging.ERROR):
        load_config(str(dst))

    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []
    assert read_version(dst) == CURRENT_FORMAT
    assert list(tmp_path.glob("config.sexp.bak.*")), "and the previous bytes are kept"
    assert dst.read_text(encoding="utf-8") == dict_to_sexp(sexp_to_dict(text))


def test_a_write_that_would_lose_meaning_is_refused_and_the_file_survives(tmp_path, monkeypatch, caplog):
    """The pre-write check must be a REAL gate. `serialize_config` is swapped for
    one that writes a different MEANING (an empty config) — exactly the shape of
    a buggy converter step: with the check on, the file is untouched; with the
    check off, it is silently emptied. The mutation «the check is off» turns this
    cell red."""
    from kicadstamp.config import upgrade_on_disk as uod

    root = tmp_path / "root.sexp"
    original = _old_text({"cells": {"c1": {}}})
    root.write_text(original, encoding="utf-8")
    monkeypatch.setattr(uod, "serialize_config", lambda path, data, **kw: dict_to_sexp({}))

    with caplog.at_level(logging.ERROR):
        assert upgrade_graph_on_disk(root) == []

    assert "NOT written" in caplog.text
    assert root.read_text(encoding="utf-8") == original, "the file is left as it is"
    assert read_version(root) == 1
    assert list(tmp_path.glob("*.bak.*")) == [], "and no copy was taken either"


def test_the_text_written_is_the_text_that_was_verified(tmp_path, monkeypatch):
    """M12: the bytes on disk are the ones the pre-write check looked at, not a
    fresh serialization of the same data.

    Two serializations of one dict are equal today, so «the writer ignores
    `serialized_text`» changes no behaviour — which is exactly why the property
    needs its own cell: without it the check gates a text nobody writes, and the
    У1 mutation («lift by inserting the number into the old text») walks straight
    through it (measured 25.09.2026: it survived until `serialized_text` went in).

    `serialize_config` is replaced by one that is CORRECT but distinguishable —
    the same meaning, wider indentation — so the disk has to show exactly it."""
    from kicadstamp.config import upgrade_on_disk as uod

    root = tmp_path / "root.sexp"
    root.write_text(_old_text({"cells": {"c1": {}}}), encoding="utf-8")
    injected: dict[str, str] = {}

    def wider(path, data, **kwargs):
        text = dict_to_sexp(data).replace("\n  ", "\n      ")
        injected["text"] = text
        return text

    monkeypatch.setattr(uod, "serialize_config", wider)

    assert [p.name for p in upgrade_graph_on_disk(root)] == ["root.sexp"]
    assert injected["text"] != dict_to_sexp({"cells": {"c1": {}}}), "distinguishable"
    assert root.read_text(encoding="utf-8") == injected["text"], (
        "the verified text is the written text")
    assert read_version(root) == CURRENT_FORMAT


# ── refusal: newer anywhere stops everything BEFORE the first write ────────

def test_a_newer_file_anywhere_stops_the_graph_before_any_write(tmp_path):
    """The refusal has to arrive BEFORE the first write: the root here is older
    than this build and perfectly liftable, and it must still be left alone."""
    root, _sub = _sexp_graph(tmp_path)
    newer = (f"(kicadstamp-config\n  (version {CURRENT_FORMAT + 1})\n"
             f"  (cells\n    (cell \"c2\")\n  )\n)\n")
    (tmp_path / "sub.sexp").write_text(newer, encoding="utf-8")
    original_root = root.read_text(encoding="utf-8")

    with pytest.raises(ValidationError) as excinfo:
        load_config(str(root))

    assert f"format {CURRENT_FORMAT + 1}" in str(excinfo.value)
    assert root.read_text(encoding="utf-8") == original_root, "the OLD file was not lifted"
    assert list(tmp_path.glob("*.bak.*")) == [], "and nothing was copied"
    assert read_version(root) == 1


# ── failures: the file is left alone, the load continues ───────────────────

@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the mode bits, so nothing can fail")
def test_an_unwritable_directory_leaves_the_file_and_still_loads(tmp_path, caplog):
    root = tmp_path / "root.sexp"
    root.write_text(_old_text({"cells": {"c1": {}}}), encoding="utf-8")
    os.chmod(tmp_path, 0o500)
    try:
        with caplog.at_level(logging.ERROR):
            cfg, _ = load_config(str(root))
        assert sorted(cfg.cells) == ["c1"], "the lift happened in memory (Т2)"
        assert "upgrade failed" in caplog.text, "the reason is in the Log"
        assert read_version(root) == 1, "the file is untouched"
        assert list(tmp_path.glob("*.bak.*")) == []
    finally:
        os.chmod(tmp_path, 0o700)


# ── У3: the GUI working set decides ────────────────────────────────────────

def test_unsaved_changes_in_the_working_set_stop_the_upgrade(tmp_path):
    """У3: `load_config` is called with the working set DIRTY by the flush itself
    (step 1 validates the staged graph). Lifting the disk file there would write a
    state the user has not saved — so the sweep stands down and the Save lifts the
    file through the one writer."""
    root = tmp_path / "root.sexp"
    root.write_text(_old_text({"cells": {"c1": {}}}), encoding="utf-8")
    WORKING_SET.enabled = True
    merge_write(root, {"cells": {"c2": {}}}, section="cells")

    cfg, _ = load_config(str(root))

    assert sorted(cfg.cells) == ["c1", "c2"], "the staged content is what loads"
    assert list(tmp_path.glob("*.bak.*")) == []
    assert read_version(root) == 1, "the disk file was NOT lifted"

    errors = WORKING_SET.flush(root)

    assert errors == []
    assert read_version(root) == CURRENT_FORMAT, "the Save stamped the number"
    assert list((tmp_path / ".history").glob("*")), "its own copy is in .history/"


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
