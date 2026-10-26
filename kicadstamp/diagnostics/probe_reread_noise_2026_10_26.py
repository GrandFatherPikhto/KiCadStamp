# kicadstamp/diagnostics/probe_reread_noise_2026_10_26.py
"""probe_reread_noise_2026_10_26.py — where the "updated" noise of a tree re-read comes from.

Ч0 of plan_2026_10_26_reread_updated_noise (worker: DeepSeek). A re-read of a
tree's inter-node copper names ~80 records "updated" while the track/via COUNTS
are unchanged, so the operator cannot tell a real change from noise. The verdict
is taken by `internode_capture._same_geometry`, which compares two records by
exact float equality and pairwise by order (`zip`).

WHAT THIS PROBE DOES — and what it refuses to do
------------------------------------------------
* The BOARD is only READ (`get_footprints`/`get_tracks`/`get_vias` + the
  role/pad lookups the capture already performs). Nothing is written to it.
* The PROFILE IS NOT TOUCHED AT ALL. The profile directory is copied into a
  fresh temp dir and every relative path inside the copy (`root_sheet`,
  `schematic_files`, `include:`) is rewritten to an ABSOLUTE path pointing at the
  ORIGINAL location — that is what lets a copy in `/tmp` load at all (rule 34).
  `load_config` writes on its own (format/registry lifts), so reading the
  original would already be a write.
* No second copy of the capture logic exists here: the classification is
  `internode_capture._classify_area` and the records come from
  `internode_capture.capture_unit` — the SAME steps `plan_internode_reread` runs.
* The write path is the product's own: `config_writer.read_data`/`write_data`
  plus `net_trace_extract.net_trace_to_dict` and `config.form_identity.
  replace_entry`, i.e. exactly the three calls `TreesDock._stage_net_traces`
  makes for a record that lives in the root file (this profile keeps all of
  `net_traces:` in `config.sexp` — the probe ASSERTS that and reports an identity
  it cannot stage instead of inventing another file).

THE FOUR MEASUREMENTS
---------------------
А0  file round-trip, NO board: `read_data` -> `write_data` -> `load_config` over
    the stored `net_traces:` and `_same_geometry` on every record. Any "not
    equal" here is pure precision loss of the writer/reader.
А1  fresh capture -> the product's write -> `load_config` -> fresh capture again:
    `_same_geometry(read_back, fresh)` per written record, plus what the next
    re-read reports. Any "not equal" is pure noise, at ANY board state.
Б   (REFERENCE ONLY — depends on the board) fresh capture against the STORED
    records of the profile, exactly as the re-read does it.
В   two fresh captures in a row against the SAME cfg, no write in between: is
    the ORDER and DIRECTION of the copper stable at all?

Every "not equal" pair is put into ONE category, in this order (the order is the
point: each step rules out the previous one):
    counts      — different track/via counts (not noise: a real add/loss)
    order       — equal as a multiset, exact floats, order ignored
    direction   — equal as a multiset after swapping a track's ends
    precision   — equal as a multiset after rounding both sides to N digits
                  (the smallest such N is reported)
    direction+precision — both of the above together
    other       — max |Δ| in mm and the field that produced it
A pair whose every element moves by ONE AND THE SAME vector is additionally
marked `rigid=+` with that vector: that is the signature of the ANCHOR POINT
parting ways with the copper (the footprint moved, the copper did not), not of a
rounding artefact.

Rule 39 — a category that also fires on a pair that is a record against ITSELF is
empty. The probe therefore runs the same classifier on `_same_geometry(nt, nt)`
for every record and refuses to report a category that fires there.

USAGE
    .venv/bin/python -m kicadstamp.diagnostics.probe_reread_noise_2026_10_26 \
        --profile profiles/3ch-awg-tia-v103/config.sexp --tree fpga [--json out.json]

Read-only on the board; the temp copy is deleted unless --keep is given.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from kicadstamp.adapter_factory import create_board_adapter
from kicadstamp.config import net_trace_effective_name
from kicadstamp.constants import DEFAULT_TIMEOUT_MS
from kicadstamp.config.form_identity import replace_entry
from kicadstamp.config.loader import load_config
from kicadstamp.config_working_set import graph_root, set_active_graph_root
from kicadstamp.config_writer import read_data, write_data
from kicadstamp.internode_capture import (
    _classify_area,
    _same_geometry,
    capture_unit,
    plan_internode_reread,
)
from kicadstamp.net_trace_extract import net_trace_to_dict

#: The write step of every stored number — `internode_nodes._items_from_unit`
#: rounds to this many decimals before anything reaches the file.
WRITE_DIGITS = 4
#: How many categories' example lines the text report prints.
SHOW_EXAMPLES = 8


# ── the profile copy ───────────────────────────────────────────────────────
def _absolutize(value: Any, base: Path) -> Any:
    """One stored path made absolute against `base`; already-absolute kept."""
    if not isinstance(value, str) or not value:
        return value
    path = Path(value)
    return str(path if path.is_absolute() else (base / path).resolve())


def _absolutize_paths(data: dict, base: Path) -> list[str]:
    """Rewrite every RELATIVE path of the copy in place; returns what was changed.

    Only the three keys the loader resolves against the profile directory are
    touched. `include:` entries are `str` or `{"path": ...}` dicts.
    """
    changed: list[str] = []
    for key in ("root_sheet",):
        if key in data:
            before = data[key]
            data[key] = _absolutize(before, base)
            if data[key] != before:
                changed.append(key)
    for key in ("schematic_files", "include"):
        items = data.get(key)
        if not isinstance(items, list):
            continue
        out = []
        for item in items:
            if isinstance(item, dict) and "path" in item:
                before = item["path"]
                item["path"] = _absolutize(before, base)
                if item["path"] != before:
                    changed.append(f"{key}[].path")
            else:
                before = item
                item = _absolutize(before, base)
                if item != before:
                    changed.append(f"{key}[]")
            out.append(item)
        data[key] = out
    return changed


def _copy_profile(profile: Path, workdir: Path) -> Path:
    """A loadable copy of the profile in `workdir`; returns the copied config path."""
    src_dir = profile.parent
    copy_dir = workdir / src_dir.name
    shutil.copytree(src_dir, copy_dir, ignore=shutil.ignore_patterns(".history"))
    copy_config = copy_dir / profile.name
    data = read_data(copy_config)
    changed = _absolutize_paths(data, src_dir.resolve())
    if changed:
        # A format-3 write stamps the graph and therefore needs to know WHICH
        # graph it is writing into — the product's writers get this from the
        # open profile (`set_active_graph_root`), a CLI from `--config`. The
        # named context manager is that same switch, scoped to this write.
        with graph_root(copy_config):
            write_data(copy_config, data)
    print(f"profile copy   : {copy_config}")
    print(f"absolute paths : {sorted(set(changed)) or '—'}")
    return copy_config


# ── the categories ─────────────────────────────────────────────────────────
def _track_key(track, *, flip: bool = False, digits: int | None = None):
    start = (track.start_along_mm, track.start_across_mm)
    end = (track.end_along_mm, track.end_across_mm)
    if flip:
        start, end = end, start
    width = track.width_mm
    if digits is not None:
        start = tuple(round(v, digits) for v in start)
        end = tuple(round(v, digits) for v in end)
        width = round(width, digits)
    return (start, end, width, track.layer)


def _via_key(via, *, digits: int | None = None):
    values = (via.offset_along_mm, via.offset_across_mm,
              via.drill_mm, via.diameter_mm)
    if digits is not None:
        values = tuple(round(v, digits) for v in values)
    return values


def _multiset(record, *, flip: bool = False, digits: int | None = None) -> Counter:
    """The record's copper as a multiset — order and direction forgotten only
    when asked for. Width and layer stay part of every element, so a different
    track can never stand in for another one."""
    return Counter(
        [_track_key(t, flip=flip, digits=digits) for t in record.tracks]
        + [("via",) + _via_key(v, digits=digits) for v in record.vias])


def _max_delta(old, new) -> tuple[float, str]:
    """(largest |Δ| in mm between the two records, the field it came from).

    Paired by index, which is enough for the one thing it is used for: an
    `other`-category pair has already failed the multiset tests, so its order is
    either equal or irrelevant to the question "how far apart is this"."""
    worst, field = 0.0, "—"
    pairs: list[tuple[float, str]] = []
    for a, b in zip(old.tracks, new.tracks):
        for name, va, vb in (
                ("start_along_mm", a.start_along_mm, b.start_along_mm),
                ("start_across_mm", a.start_across_mm, b.start_across_mm),
                ("end_along_mm", a.end_along_mm, b.end_along_mm),
                ("end_across_mm", a.end_across_mm, b.end_across_mm),
                ("width_mm", a.width_mm, b.width_mm)):
            pairs.append((abs(va - vb), name))
    for a, b in zip(old.vias, new.vias):
        for name, va, vb in (
                ("offset_along_mm", a.offset_along_mm, b.offset_along_mm),
                ("offset_across_mm", a.offset_across_mm, b.offset_across_mm),
                ("drill_mm", a.drill_mm, b.drill_mm),
                ("diameter_mm", a.diameter_mm, b.diameter_mm)):
            pairs.append((abs(va - vb), name))
    for delta, name in pairs:
        if delta > worst:
            worst, field = delta, name
    return worst, field


def _rigid_shift(old, new) -> tuple[float, float] | None:
    """The single (dx, dy) every element of `old` moved by, or None.

    Elements are paired greedily by nearest end-point (a track may be stored
    either way round). One common vector for ALL of them means the copper and
    the anchor point no longer share a frame — the anchor footprint moved, or the
    copper did, but the piece itself kept its shape."""
    if len(old.tracks) != len(new.tracks) or len(old.vias) != len(new.vias):
        return None
    deltas: list[tuple[float, float]] = []
    free = list(new.tracks)
    for a in old.tracks:
        if not free:
            return None
        best = min(free, key=lambda b: min(
            abs(a.start_along_mm - b.start_along_mm)
            + abs(a.start_across_mm - b.start_across_mm),
            abs(a.start_along_mm - b.end_along_mm)
            + abs(a.start_across_mm - b.end_across_mm)))
        free.remove(best)
        forward = (abs(a.start_along_mm - best.start_along_mm)
                   + abs(a.start_across_mm - best.start_across_mm)
                   <= abs(a.start_along_mm - best.end_along_mm)
                   + abs(a.start_across_mm - best.end_across_mm))
        if forward:
            deltas.append((best.start_along_mm - a.start_along_mm,
                           best.start_across_mm - a.start_across_mm))
        else:
            deltas.append((best.end_along_mm - a.start_along_mm,
                           best.end_across_mm - a.start_across_mm))
    for a, b in zip(old.vias, new.vias):
        deltas.append((b.offset_along_mm - a.offset_along_mm,
                       b.offset_across_mm - a.offset_across_mm))
    if not deltas:
        return None
    dx, dy = deltas[0]
    for other_dx, other_dy in deltas[1:]:
        if abs(other_dx - dx) > 1e-9 or abs(other_dy - dy) > 1e-9:
            return None
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return None
    return (dx, dy)


def classify_pair(old, new) -> dict[str, Any]:
    """ONE category for a pair `_same_geometry` called "not equal", plus the
    numbers a decision needs (the smallest precision N, the worst |Δ|, whether
    the whole record moved by one vector)."""
    if len(old.tracks) != len(new.tracks) or len(old.vias) != len(new.vias):
        return {"category": "counts", "digits": None, "max_delta_mm": None,
                "field": "tracks/vias",
                "counts": [len(old.tracks), len(old.vias),
                           len(new.tracks), len(new.vias)]}
    result: dict[str, Any] = {"category": "other", "digits": None}
    worst, field = _max_delta(old, new)
    if _multiset(old) == _multiset(new):
        result["category"] = "order"
    elif _multiset(old, flip=True) == _multiset(new):
        result["category"] = "direction"
    else:
        # "Rounding to N digits made the multisets equal" is only EVIDENCE when
        # the numbers really are that close: at N=3 a 10 mm move can swap two
        # segments into a match (measured: a false "precision" hit at 10.66 mm).
        # A precision verdict therefore has to survive `max |Δ| <= 10^-N`;
        # anything else stays "other" and carries its real distance.
        for digits in range(WRITE_DIGITS + 4, 0, -1):
            if worst > 10.0 ** -digits:
                break
            if _multiset(old, digits=digits) == _multiset(new, digits=digits):
                result.update(category="precision", digits=digits)
                break
            if _multiset(old, flip=True, digits=digits) == \
                    _multiset(new, flip=True, digits=digits):
                result.update(category="direction+precision", digits=digits)
                break
    if result["category"] in ("other", "precision", "direction+precision"):
        result["max_delta_mm"] = worst
        result["field"] = field
    rigid = _rigid_shift(old, new)
    result["rigid_shift_mm"] = rigid
    return result


def _tally(pairs: Iterable[tuple[Any, Any]]) -> dict[str, dict]:
    """Category -> {"count", "digits", "worst_mm", "rigid", "examples"}."""
    out: dict[str, dict] = {}
    for old, new in pairs:
        verdict = classify_pair(old, new)
        bucket = out.setdefault(verdict["category"], {
            "count": 0, "digits": set(), "worst_mm": 0.0, "rigid": 0,
            "examples": []})
        bucket["count"] += 1
        if verdict.get("digits"):
            bucket["digits"].add(verdict["digits"])
        if verdict.get("max_delta_mm"):
            bucket["worst_mm"] = max(bucket["worst_mm"],
                                     verdict["max_delta_mm"])
        if verdict.get("rigid_shift_mm"):
            bucket["rigid"] += 1
        if len(bucket["examples"]) < SHOW_EXAMPLES:
            bucket["examples"].append({
                "identity": net_trace_effective_name(new),
                **{k: v for k, v in verdict.items()
                   if k in ("digits", "max_delta_mm", "field",
                            "rigid_shift_mm")},
            })
    for bucket in out.values():
        bucket["digits"] = sorted(bucket["digits"])
    return out


def _print_tally(title: str, tally: dict[str, dict]) -> None:
    print(f"\n== {title} ==")
    if not tally:
        print("   no 'not equal' pairs")
        return
    for category, bucket in sorted(tally.items()):
        line = f"   {category:22s} {bucket['count']:4d}"
        if bucket["digits"]:
            line += f"   digits {bucket['digits']}"
        if bucket["worst_mm"]:
            line += f"   worst |Δ| {bucket['worst_mm']:.6f} mm"
        if bucket["rigid"]:
            line += f"   rigid-shift {bucket['rigid']}"
        print(line)
        for example in bucket["examples"]:
            print(f"      {example}")


def _retry(op: str, fn, *, attempts: int = 6, backoff_s: float = 3.0):
    """Run `fn`, again and again, while KiCad answers "busy".

    The product wraps MUTATING calls this way (`adapter._mutating_call`); a read
    has no such wrapper, and the board this probe measures is being dragged BY
    HAND at the same time — KiCad then answers AS_BUSY for whole seconds. The
    match is by CODE, exactly like the product's (its old substring test never
    fired — see `_mutating_call`'s docstring), and a busy match is the only thing
    retried: a real error propagates on the first attempt."""
    from kipy.errors import ApiError, ApiStatusCode

    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except ApiError as exc:
            if getattr(exc, "code", None) != ApiStatusCode.AS_BUSY:
                raise
            wait = backoff_s * (attempt + 1)
            print(f"   {op}: KiCad busy, retrying in {wait:.0f}s "
                  f"[{attempt + 1}/{attempts}]", flush=True)
            time.sleep(wait)
            last = exc
    raise SystemExit(f"{op}: KiCad stayed busy for {attempts} attempt(s): {last}")


# ── fresh capture (the re-read's own steps, no plan) ───────────────────────
def _area(adapter) -> tuple[list, list]:
    """(footprints, copper items) of the WHOLE board, the way the re-read worker
    collects them: nothing is sorted or filtered here."""
    return list(adapter.get_footprints()), \
        list(adapter.get_tracks()) + list(adapter.get_vias())


def _fresh_records(adapter, cfg, tree, config_path, sheet_names) -> list:
    """Every unit of the tree's inter-node copper as a record — `_classify_area` +
    `capture_unit`, i.e. exactly `plan_internode_reread` minus the stored-record
    matching, so two calls measure the CAPTURE and nothing else."""
    fps, items = _area(adapter)
    classification = _classify_area(
        adapter, cfg, tree, config_path=config_path, area_items=items,
        area_footprints=fps, sheet_names=sheet_names)
    records = []
    names: list[str] = [net_trace_effective_name(nt) for nt in cfg.net_traces]
    for unit in classification.units:
        record, warning = capture_unit(
            adapter, unit, components=classification.components,
            node_by_ref=classification.node_name_label_by_ref,
            node_sheet_by_ref=classification.anchor_sheet_by_ref,
            sheet_names=sheet_names, existing=None, existing_names=names,
            resolver=classification.resolver)
        if warning:
            print(f"   capture warning: {warning}")
        if record is None:
            continue
        names.append(net_trace_effective_name(record))
        records.append(record)
    return records


# ── the write (the product's own three calls) ──────────────────────────────
def _stage(copy_config: Path, records: list) -> list[str]:
    """Write the records into the COPY through the product's write path and
    return the identities actually written."""
    data = read_data(copy_config)
    entries = data.setdefault("net_traces", [])
    written: list[str] = []
    for record in records:
        entry = net_trace_to_dict(record)
        identity = net_trace_effective_name(record)
        replaced = replace_entry(entries, entry, identity=identity,
                                 identity_of=lambda e: e.get("name") or e.get("net"))
        written.append(identity)
        print(f"   staged {identity!r}: {'replaced' if replaced else 'appended'}")
    write_data(copy_config, data)
    return written


# ── the experiments ───────────────────────────────────────────────────────
def _experiment_a0(copy_config: Path, cfg_stored) -> dict:
    """А0 — the FILE round-trip alone: read -> write -> load, no board."""
    data = read_data(copy_config)
    write_data(copy_config, data)
    cfg_again, _ctx = load_config(str(copy_config))
    by_name = {net_trace_effective_name(nt): nt for nt in cfg_stored.net_traces}
    pairs = [(by_name[net_trace_effective_name(nt)], nt)
             for nt in cfg_again.net_traces
             if net_trace_effective_name(nt) in by_name]
    not_equal = [(a, b) for a, b in pairs if not _same_geometry(a, b)]
    print(f"\n== А0  file round-trip of the stored net_traces: "
          f"{len(pairs)} record(s), {len(not_equal)} 'not equal' ==")
    _print_tally("А0 categories", _tally(not_equal))
    return {"records": len(pairs), "not_equal": len(not_equal),
            "categories": _tally(not_equal)}


def _element_text(record, limit: int = 6) -> str:
    """The record's elements in THEIR OWN order, short enough to read.

    This is what makes an `order` verdict checkable by eye instead of trusted:
    the same tuples, written twice, in a different sequence."""
    parts = [
        f"({t.start_along_mm:g},{t.start_across_mm:g})->"
        f"({t.end_along_mm:g},{t.end_across_mm:g}) w{t.width_mm:g} {t.layer}"
        for t in record.tracks[:limit]]
    parts += [f"via({v.offset_along_mm:g},{v.offset_across_mm:g} "
              f"d{v.drill_mm:g}/{v.diameter_mm:g})"
              for v in record.vias[:limit]]
    return " | ".join(parts)


def _experiment_b(cfg_stored, plan) -> dict:
    """Б — REFERENCE: the fresh capture against the STORED records."""
    by_name = {net_trace_effective_name(nt): nt for nt in cfg_stored.net_traces}
    pairs = [(by_name[net_trace_effective_name(c.record)], c.record)
             for c, _counts in plan.updated
             if net_trace_effective_name(c.record) in by_name]
    same_counts = [p for p in pairs
                   if len(p[0].tracks) == len(p[1].tracks)
                   and len(p[0].vias) == len(p[1].vias)]
    print(f"\n== Б  fresh vs STORED (reference only): added {len(plan.added)}, "
          f"updated {len(plan.updated)} (of them same counts {len(same_counts)}), "
          f"unchanged {len(plan.unchanged)}, missing {len(plan.missing)} ==")
    _print_tally("Б categories (all updated)", _tally(pairs))
    _print_tally("Б categories (same counts only)", _tally(same_counts))
    shown = 0
    for old, new in same_counts:
        if shown >= 2 or classify_pair(old, new)["category"] != "order":
            continue
        shown += 1
        print(f"   ORDER example {net_trace_effective_name(old)!r}:")
        print(f"      stored: {_element_text(old)}")
        print(f"      fresh : {_element_text(new)}")
    return {"added": len(plan.added), "updated": len(plan.updated),
            "updated_same_counts": len(same_counts),
            "unchanged": len(plan.unchanged), "missing": len(plan.missing),
            "categories_all": _tally(pairs),
            "categories_same_counts": _tally(same_counts)}


def _sequence_digests(records: list) -> dict[str, str]:
    """identity -> digest of the ORDER of its copper elements.

    Two runs of the probe are two PROCESSES, and an order that comes out of a
    set/dict iteration would differ between them by string-hash randomisation —
    so "the capture keeps its order" can only be claimed after comparing this map
    ACROSS runs (the JSON of two runs is diffed in the Ч0 report)."""
    import hashlib

    out: dict[str, str] = {}
    for record in records:
        sequence = [_track_key(t) for t in record.tracks] + \
                   [("via",) + _via_key(v) for v in record.vias]
        out[net_trace_effective_name(record)] = \
            hashlib.sha1(repr(sequence).encode("utf-8")).hexdigest()[:12]
    return out


def _experiment_v(first: list, second: list) -> dict:
    """В — two fresh captures in a row, no write: is the capture itself stable?"""
    def key(record):
        return (frozenset(record.pads or ()), record.net)

    by_sig_first = {key(r): r for r in first}
    by_sig_second = {key(r): r for r in second}
    collisions = len(first) - len(by_sig_first)
    common = sorted(set(by_sig_first) & set(by_sig_second),
                    key=lambda k: (sorted(k[0]), k[1] or ""))
    pairs = [(by_sig_first[k], by_sig_second[k]) for k in common]
    not_equal = [(a, b) for a, b in pairs if not _same_geometry(a, b)]
    print(f"\n== В  two fresh captures in a row: {len(first)} / {len(second)} "
          f"record(s), matched by pad signature+net {len(common)} "
          f"(signature collisions {collisions}), 'not equal' {len(not_equal)} ==")
    if set(by_sig_first) != set(by_sig_second):
        lost = [sorted(k[0]) for k in set(by_sig_first) ^ set(by_sig_second)]
        print(f"   the two captures took DIFFERENT pieces: {lost}")
    _print_tally("В categories", _tally(not_equal))
    return {"first": len(first), "second": len(second), "common": len(common),
            "collisions": collisions, "not_equal": len(not_equal),
            "categories": _tally(not_equal),
            "sequence_digests": _sequence_digests(first)}


def _experiment_a1(adapter, copy_config: Path, tree, plan, sheet_names) -> dict:
    """А1 — fresh capture -> the product's write -> load -> capture again."""
    written_records = [c.record for c, _counts in plan.updated] + \
        [c.record for c in plan.added]
    if not written_records:
        print("\n== А1  nothing to write: the re-read reported no updated/added "
              "record, so the round-trip is measured on А0 only ==")
        return {"written": 0, "not_equal": 0, "categories": {}}
    print(f"\n== А1  fresh capture -> write -> load -> capture again "
          f"({len(written_records)} record(s)) ==")
    written = set(_stage(copy_config, written_records))
    cfg_written, _ctx = load_config(str(copy_config))
    read_back = {net_trace_effective_name(nt): nt
                 for nt in cfg_written.net_traces if net_trace_effective_name(nt) in written}
    pairs = [(read_back[net_trace_effective_name(r)], r)
             for r in written_records if net_trace_effective_name(r) in read_back]
    not_equal = [(a, b) for a, b in pairs if not _same_geometry(a, b)]
    print(f"   read back {len(read_back)} of {len(written)}; "
          f"'not equal' against the capture that was written: {len(not_equal)}")
    _print_tally("А1 categories", _tally(not_equal))

    tree_again = _tree_named(cfg_written, tree.name)
    fps, items = _retry("read the board", lambda: _area(adapter))
    second = _retry("the next re-read", lambda: plan_internode_reread(
        adapter, cfg_written, tree_again, config_path=str(copy_config),
        area_items=items, area_footprints=fps, sheet_names=sheet_names))
    rerun_updated = [net_trace_effective_name(c.record) for c, _ in second.updated
                     if net_trace_effective_name(c.record) in written]
    rerun_unchanged = [name for name in second.unchanged if name in written]
    rerun_missing = [name for name in second.missing if name in written]
    print(f"   the NEXT re-read of the same board over the written profile: "
          f"unchanged {len(second.unchanged)} (of the written "
          f"{len(rerun_unchanged)}), updated among the written "
          f"{len(rerun_updated)}, missing among the written {len(rerun_missing)}")
    if rerun_updated:
        print(f"   still updated: {rerun_updated}")
    if rerun_missing:
        print(f"   written but reported missing: {rerun_missing}")
    return {"written": len(written), "read_back": len(read_back),
            "not_equal": len(not_equal), "categories": _tally(not_equal),
            "next_reread_updated": rerun_updated,
            "next_reread_unchanged_of_written": len(rerun_unchanged),
            "next_reread_missing_of_written": rerun_missing,
            "next_reread_unchanged": len(second.unchanged)}


def _self_control(records: list) -> int:
    """Rule 39: the classifier on a record against ITSELF must say "no pair"."""
    fired = 0
    for record in records:
        if not _same_geometry(record, record):
            fired += 1
            print(f"   CONTROL FIRED on {net_trace_effective_name(record)!r}: "
                  f"a record is not equal to itself — the comparison itself is "
                  f"the defect")
    print(f"\n== control: _same_geometry(record, record) over {len(records)} "
          f"record(s) — fired {fired} ==")
    return fired


def _tree_named(cfg, name: str):
    for tree in cfg.trees:
        if tree.name == name:
            return tree
    raise SystemExit(f"tree {name!r} is not in this profile; it has: "
                     f"{[t.name for t in cfg.trees]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default="profiles/3ch-awg-tia-v103/config.sexp")
    parser.add_argument("--tree", default="fpga")
    parser.add_argument("--json", default=None, help="dump the measurement here")
    parser.add_argument("--keep", action="store_true", help="keep the temp copy")
    args = parser.parse_args(argv)

    profile = Path(args.profile).resolve()
    if not profile.is_file():
        raise SystemExit(f"no such profile: {profile}")

    workdir = Path(tempfile.mkdtemp(prefix="reread-noise-"))
    print(f"temp dir       : {workdir}{' (kept)' if args.keep else ''}")
    adapter = None
    result: dict[str, Any] = {"profile": str(profile), "tree": args.tree}
    try:
        copy_config = _copy_profile(profile, workdir)
        # Every later write of this run targets the COPY — the active root is
        # what tells the format-3 stamp which graph it is stamping.
        set_active_graph_root(copy_config)
        cfg, ctx = load_config(str(copy_config))
        sheet_names = dict(getattr(ctx, "sheet_names", None) or {})
        tree = _tree_named(cfg, args.tree)
        print(f"tree           : {tree.name!r}; stored net_traces: {len(cfg.net_traces)}")
        result["tree"] = tree.name
        result["stored_records"] = len(cfg.net_traces)

        result["a0"] = _experiment_a0(copy_config, cfg)

        # DEFAULT_TIMEOUT_MS, never a literal: the repo owns ONE IPC timeout
        # (tests/repo/test_timeout_sweep.py), and a probe waiting longer than the
        # product is a probe measuring a different machine.
        adapter = create_board_adapter(timeout_ms=DEFAULT_TIMEOUT_MS,
                                       config_path=str(copy_config))
        adapter.refresh_board()
        print(f"live board     : {adapter.get_board_filename()}")
        fps, items = _retry("read the board", lambda: _area(adapter))
        print(f"board          : {len(fps)} footprint(s), {len(items)} copper item(s)")

        plan = _retry("the re-read", lambda: plan_internode_reread(
            adapter, cfg, tree, config_path=str(copy_config),
            area_items=items, area_footprints=fps, sheet_names=sheet_names))
        result["b"] = _experiment_b(cfg, plan)

        first = _retry("capture 1", lambda: _fresh_records(
            adapter, cfg, tree, str(copy_config), sheet_names))
        second = _retry("capture 2", lambda: _fresh_records(
            adapter, cfg, tree, str(copy_config), sheet_names))
        result["v"] = _experiment_v(first, second)
        result["control_fired"] = _self_control(first)

        result["a1"] = _experiment_a1(adapter, copy_config, tree, plan, sheet_names)
    finally:
        if adapter is not None:
            adapter.close()
        if args.json:
            Path(args.json).write_text(
                json.dumps(_jsonable(result), ensure_ascii=False, indent=2),
                encoding="utf-8")
            print(f"\njson           : {args.json}")
        if args.keep:
            print(f"temp copy kept : {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)
    return 0


def _jsonable(value):
    """Sets and Counters are not JSON — keep the report readable, no more."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items() if k != "examples_set"}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(str(v) for v in value)
    return value


if __name__ == "__main__":
    sys.exit(main())
