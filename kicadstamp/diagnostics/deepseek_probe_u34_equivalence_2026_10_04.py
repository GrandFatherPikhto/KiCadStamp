#!/usr/bin/env python3
# kicadstamp/diagnostics/deepseek_probe_u34_equivalence_2026_10_04.py
"""У3.4 part A (plan §7) — format-2 -> format-3 equivalence on COPIES.

For every working profile of the У3.0 inventory this probe:

  0. copies the profile to ``profiles/<name>-u34copy`` and ``-u34copy2`` (rule
     28/34: SAME depth as the original, because the config's ``root_sheet`` is
     relative four levels up). The ORIGINAL is never opened — not even for
     reading — and ``profiles/3ch-awg-tia-v103-copy`` (Denis's own copy) is
     never touched nor copied. Every copy is removed in a ``finally``.
  1. BOUNDARY BEFORE THE FIRST BYTE: every file of the copy's ``include:`` graph
     AND both registry paths (``registry_paths_for_config``, honouring an
     explicit ``registry_path:``/``track_registry_path:``) must resolve INSIDE
     the copy directory. Otherwise the profile is SKIPPED WHOLE — lifting a
     "copy" whose include:/registry points out would write the ORIGINAL or the
     KiCad project.
  2. "before" FIRST, into a scratch file: ``CURRENT_FORMAT = 2`` ->
     ``load_config`` -> ``asdict(cfg)`` + both registries' key sets.
  3. "after": ``CURRENT_FORMAT = 3`` -> ``load_config`` lifts the graph on disk
     and the registries 1 -> 2 -> ``asdict(cfg)`` with ``uuid``/``*_uuid``/
     ``folders`` stripped must EQUAL "before"; the format-3 load must not fatal;
     the registries must be schema 2 with 0 orphans, their keys a bijection of
     the "before" keys.
  4. a repeat open writes NOTHING (contents AND mtimes of every graph file and
     registry), and each file carries exactly ONE ``.bak``.
  5. the second copy, lifted independently in another directory, carries the
     SAME uuid strings byte for byte (the seed carries no path).

A profile whose config graph does not reach every config file (the У3.0
inventory lists ten files across seven profiles) has its standalone extra files
processed as their OWN roots too, so all ten inventory files are covered.

The format-2 "before" read itself lifts a format-1 file to 2 (the product's own
normalization) and leaves a ``.bak``; those preparatory backups are removed from
the disposable copy before the 2 -> 3 lift under test, so "exactly one .bak per
file" is the backup OF THE LIFT, not two stacked ones.

Run from the repo root:  .venv/bin/python kicadstamp/diagnostics/deepseek_probe_u34_equivalence_2026_10_04.py
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from kicadstamp.config import format_version  # noqa: E402
from kicadstamp.config.format3 import _f3_records  # noqa: E402
from kicadstamp.config.format_version import parse_raw_file, read_version  # noqa: E402
from kicadstamp.config.includes import walk_include_tree  # noqa: E402
from kicadstamp.config.loader import load_config  # noqa: E402
from kicadstamp.config.registry_upgrade import (  # noqa: E402
    _build_index,
    map_registry_key,
    read_registry_schema,
)
from kicadstamp.utils.paths import registry_paths_for_config  # noqa: E402

PROFILES_DIR = REPO_ROOT / "profiles"
# Denis's own copy is NOT part of the У3.0 inventory and must not be touched.
SKIP_DIRS = {"3ch-awg-tia-v103-copy", ".stfolder"}
# Infrastructure directories a profile owns that are not config graph files.
_INFRA = ("registry", "tracks", "logs", "operational", "overrides", ".history",
          ".stfolder", "__pycache__")

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SCRATCH = Path(tempfile.gettempdir()) / "u34_equivalence_before"


# ── profile / file discovery ────────────────────────────────────────────────

def _profile_dirs() -> list[Path]:
    out = []
    for p in sorted(PROFILES_DIR.iterdir()):
        if not p.is_dir() or p.name in SKIP_DIRS:
            continue
        if p.name.endswith(("-u34copy", "-u34copy2")):
            continue
        out.append(p)
    return out


def _config_files(profile: Path) -> list[Path]:
    files = []
    for f in sorted(profile.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in (".sexp", ".json"):
            continue
        if any(part in _INFRA for part in f.relative_to(profile).parts[:-1]):
            continue
        if f.name.startswith("."):
            continue
        files.append(f)
    return files


def _record_count(path: Path) -> int:
    try:
        data, _v = parse_raw_file(path)
        return sum(1 for _ in _f3_records(data))
    except Exception:
        return -1


def _pick_root(files: list[Path]) -> Path | None:
    named = [f for f in files if f.name == "config.sexp"]
    if named:
        return named[0]
    sexps = [f for f in files if f.suffix.lower() == ".sexp"]
    if len(sexps) == 1:
        return sexps[0]
    pool = sexps or files
    if not pool:
        return None
    return max(pool, key=_record_count)


def _graph_paths(root: Path) -> list[Path]:
    out: list[Path] = []

    def visit(node) -> None:
        out.append(Path(node.path).resolve())
        for child in node.children:
            visit(child)

    visit(walk_include_tree(str(root)))
    return out


def _registry_paths(root: Path) -> tuple[Path, Path]:
    data, _v = parse_raw_file(root)
    via, trk = registry_paths_for_config(
        str(root), data.get("registry_path"), data.get("track_registry_path"))
    return Path(via).resolve(), Path(trk).resolve()


# ── identity stripping / diffs ──────────────────────────────────────────────

def _strip_identity(obj):
    if isinstance(obj, dict):
        return {k: _strip_identity(v) for k, v in obj.items()
                if k != "folders" and k != "uuid" and not k.endswith("_uuid")}
    if isinstance(obj, (list, tuple)):
        return [_strip_identity(x) for x in obj]
    return obj


def _diff_paths(a, b, prefix="", limit=20, out=None):
    if out is None:
        out = []
    if len(out) >= limit:
        return out
    if type(a) is not type(b):
        out.append(prefix or "<root>")
        return out
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{prefix}.{k}".lstrip("."))
                if len(out) >= limit:
                    return out
            else:
                _diff_paths(a[k], b[k], f"{prefix}.{k}".lstrip("."), limit, out)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{prefix}[len {len(a)}!={len(b)}]")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                _diff_paths(x, y, f"{prefix}[{i}]", limit, out)
    elif a != b:
        out.append(f"{prefix}: {a!r} != {b!r}")
    return out


# ── registries ──────────────────────────────────────────────────────────────

def _read_keys(path: Path) -> set:
    if not path.exists():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(raw, dict):
        return set()
    return {k for k in raw if k != "schema_version"}


def _registry_report(before_keys: set, after_keys: set, cfg_after, path: Path) -> dict:
    """Orphans/ambiguity and the before -> after key bijection, through the
    PRODUCT's own mapper (so the probe cannot disagree with the lift)."""
    schema = read_registry_schema(path)
    if not before_keys and not after_keys:
        return {"present": False, "schema": schema, "n_before": 0, "n_after": 0,
                "orphans": 0, "ambiguous": 0, "bijective": True}
    idx = _build_index(cfg_after)
    mapped, orphans, ambiguous = set(), 0, 0
    for key in before_keys:
        new_key, problem = map_registry_key(key, idx)
        if problem is None:
            mapped.add(new_key)
        elif problem == "ambiguous":
            ambiguous += 1
        elif problem == "orphan":
            orphans += 1
    return {"present": True, "schema": schema, "n_before": len(before_keys),
            "n_after": len(after_keys), "orphans": orphans,
            "ambiguous": ambiguous, "bijective": mapped == after_keys}


# ── uuid collection (cross-copy byte equality) ──────────────────────────────

def _collect_uuids(path: Path) -> list[str]:
    data, _v = parse_raw_file(path)
    text = json.dumps(data, default=str)
    return sorted(_UUID_RE.findall(text))


def _snapshot(paths: list[Path]) -> dict:
    snap = {}
    for p in paths:
        if p.exists():
            snap[str(p)] = (p.read_bytes(), p.stat().st_mtime_ns)
    return snap


def _bak_count(path: Path) -> int:
    return len(list(path.parent.glob(path.name + ".bak*")))


# ── one root ────────────────────────────────────────────────────────────────

def _process_root(copy_dir: Path, rel: Path, tag: str) -> dict:
    """Before/after/repeat on ONE root inside `copy_dir` (a resolved dir)."""
    root = copy_dir / rel
    graph = _graph_paths(root)
    via_path, trk_path = _registry_paths(root)

    # ── "before": CURRENT_FORMAT = 2, captured FIRST into the scratch file ──
    format_version.CURRENT_FORMAT = 2
    cfg_before, _ = load_config(str(root))
    before_via = _read_keys(via_path)
    before_trk = _read_keys(trk_path)
    scratch = _SCRATCH / f"{tag}.json"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text(json.dumps(
        {"root": str(root), "asdict": asdict(cfg_before),
         "registry_keys": {"via": sorted(before_via), "tracks": sorted(before_trk)}},
        indent=2, default=str), encoding="utf-8")

    # Drop the preparatory 1 -> 2 backups (disposable copy) so the 2 -> 3 lift
    # is the only one leaving a .bak.
    for p in graph:
        for bak in p.parent.glob(p.name + ".bak*"):
            bak.unlink()

    # ── "after": the REAL lift on disk under CURRENT_FORMAT = 3 ─────────────
    format_version.CURRENT_FORMAT = 3
    cfg_after, _ = load_config(str(root))
    after_asdict = asdict(cfg_after)

    equal = _strip_identity(asdict(cfg_before)) == _strip_identity(after_asdict)
    diff = [] if equal else _diff_paths(
        _strip_identity(asdict(cfg_before)), _strip_identity(after_asdict))

    reg_via = _registry_report(before_via, _read_keys(via_path), cfg_after, via_path)
    reg_trk = _registry_report(before_trk, _read_keys(trk_path), cfg_after, trk_path)

    # ── repeat open: 0 bytes written, one .bak per file ─────────────────────
    watched = list(graph) + [via_path, trk_path]
    snap_before = _snapshot(watched)
    load_config(str(root))
    reopened_zero = snap_before == _snapshot(watched)
    baks = {p.name: _bak_count(p) for p in graph}

    return {
        "rel": rel, "root": root, "graph": graph, "equal": equal, "diff": diff,
        "reg_via": reg_via, "reg_trk": reg_trk,
        "reopened_zero": reopened_zero, "baks": baks,
    }


# ── one profile ─────────────────────────────────────────────────────────────

def _process_profile(profile: Path) -> dict:
    copy1 = (PROFILES_DIR / (profile.name + "-u34copy")).resolve()
    copy2 = (PROFILES_DIR / (profile.name + "-u34copy2")).resolve()
    row = {"profile": profile.name, "skipped": None, "error": None,
           "files": 0, "f1": 0, "f2": 0, "records": 0, "lifted_files": 0,
           "equal": None, "diff": [], "reopened_zero": None, "baks": None,
           "uuids_match": None, "reg_via": None, "reg_trk": None, "roots": []}

    for c in (copy1, copy2):
        shutil.rmtree(c, ignore_errors=True)
    try:
        shutil.copytree(profile, copy1)
        shutil.copytree(profile, copy2)
    except OSError as e:
        row["error"] = f"copy failed: {e}"
        shutil.rmtree(copy1, ignore_errors=True)
        shutil.rmtree(copy2, ignore_errors=True)
        return row

    try:
        # Discovery and counting run on the COPY — the original is only ever
        # copied (rule 28), never parsed.
        src_files = _config_files(copy1)
        row["files"] = len(src_files)
        for f in src_files:
            v = read_version(f)
            if v == 1:
                row["f1"] += 1
            elif v == 2:
                row["f2"] += 1
            row["records"] += max(_record_count(f), 0)
        root_name = _pick_root(src_files)
        if root_name is None:
            row["error"] = "no config file"
            return row

        src_rels = [f.resolve().relative_to(copy1) for f in src_files]
        main_rel = root_name.resolve().relative_to(copy1)
        main_graph_rels = {p.relative_to(copy1) for p in _graph_paths(copy1 / main_rel)}
        roots_rel = [main_rel] + [r for r in src_rels if r not in main_graph_rels]
        row["roots"] = [r.as_posix() for r in roots_rel]

        # ── boundary BEFORE the first byte ──────────────────────────────────
        offending: list[str] = []
        for rel in roots_rel:
            for base in (copy1, copy2):
                try:
                    graph = _graph_paths(base / rel)
                except Exception as e:  # reported, never masked
                    row["error"] = f"include walk failed: {type(e).__name__}: {e}"
                    return row
                for p in graph:
                    if not p.is_relative_to(base):
                        offending.append(str(p))
                via_p, trk_p = _registry_paths(base / rel)
                for p in (via_p, trk_p):
                    if not p.is_relative_to(base):
                        offending.append(str(p))
        if offending:
            row["skipped"] = "outside copy: " + "; ".join(sorted(set(offending)))
            return row

        # ── every root: before / after / repeat ─────────────────────────────
        reps = []
        for rel in roots_rel:
            tag = f"{profile.name}__{rel.as_posix().replace('/', '_')}"
            reps.append(_process_root(copy1, rel, tag))

        # ── copy 2: the SAME lift in another directory, same uuids ──────────
        format_version.CURRENT_FORMAT = 3
        uuids_match = True
        for rep in reps:
            load_config(str(copy2 / rep["rel"]))
            for p1 in rep["graph"]:
                relf = p1.relative_to(copy1)
                if _collect_uuids(p1) != _collect_uuids(copy2 / relf):
                    uuids_match = False

        # ── aggregate ───────────────────────────────────────────────────────
        all_graph = sorted({p for rep in reps for p in rep["graph"]})
        row["lifted_files"] = sum(1 for p in all_graph if read_version(p) == 3)
        row["equal"] = all(rep["equal"] for rep in reps)
        row["reopened_zero"] = all(rep["reopened_zero"] for rep in reps)
        row["uuids_match"] = uuids_match
        merged: dict[str, int] = {}
        for rep in reps:
            for name, n in rep["baks"].items():
                merged[name] = max(merged.get(name, 0), n)
        row["baks"] = merged
        row["diff"] = [d for rep in reps for d in rep["diff"]]
        for rep in reps:                     # the main root owns the registries
            if rep["rel"] == main_rel:
                row["reg_via"] = rep["reg_via"]
                row["reg_trk"] = rep["reg_trk"]
        if row["reg_via"] is None:
            for rep in reps:
                if rep["reg_via"]["present"] or rep["reg_trk"]["present"]:
                    row["reg_via"], row["reg_trk"] = rep["reg_via"], rep["reg_trk"]
                    break
        return row
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"
        return row
    finally:
        shutil.rmtree(copy1, ignore_errors=True)
        shutil.rmtree(copy2, ignore_errors=True)


# ── output ──────────────────────────────────────────────────────────────────

def _reg_cell(r: dict | None) -> str:
    if r is None:
        return "—"
    if not r["present"]:
        return "нет файла"
    flag = "ok" if (r["bijective"] and r["orphans"] == 0
                    and r["ambiguous"] == 0) else "!!"
    return (f"{r['n_before']}/{r['n_after']} сх{r['schema']} "
            f"сирот={r['orphans']} неодн={r['ambiguous']} {flag}")


def _yn(v) -> str:
    if v is None:
        return "—"
    return "да" if v else "НЕТ"


def main() -> int:
    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    profiles = _profile_dirs()
    print(f"=== У3.4 equivalence probe: {len(profiles)} profile(s) ===")
    print(f"    scratch 'before' dumps: {_SCRATCH}")
    rows = []
    try:
        for prof in profiles:
            print(f"\n--- {prof.name} ---", flush=True)
            rows.append(_process_profile(prof))
    finally:
        for prof in profiles:
            for suffix in ("-u34copy", "-u34copy2"):
                shutil.rmtree(PROFILES_DIR / (prof.name + suffix),
                              ignore_errors=True)

    print("\n\n=== TABLE (markdown) ===")
    print("| профиль | файлов (ф1/ф2) | записей | поднято | «до»=«после» | "
          "повторно 0 байт | .bak | UUID двух копий | реестр via | реестр tracks | "
          "пропущен/почему |")
    print("|---|---:|---:|---:|---|---|---|---|---|---|---|")
    stop = False
    for r in rows:
        if r["skipped"]:
            print(f"| {r['profile']} | {r['files']} ({r['f1']}/{r['f2']}) | "
                  f"{r['records']} | — | — | — | — | — | — | — | "
                  f"**пропущен: {r['skipped']}** |")
            continue
        if r["error"]:
            print(f"| {r['profile']} | {r['files']} ({r['f1']}/{r['f2']}) | "
                  f"{r['records']} | — | — | — | — | — | — | — | "
                  f"**ошибка: {r['error']}** |")
            continue
        baks = "=".join(str(v) for v in (r["baks"] or {}).values())
        baks_ok = all(v == 1 for v in (r["baks"] or {}).values())
        print(f"| {r['profile']} | {r['files']} ({r['f1']}/{r['f2']}) | "
              f"{r['records']} | {r['lifted_files']} | {_yn(r['equal'])} | "
              f"{_yn(r['reopened_zero'])} | {baks}{'' if baks_ok else ' !!'} | "
              f"{_yn(r['uuids_match'])} | {_reg_cell(r['reg_via'])} | "
              f"{_reg_cell(r['reg_trk'])} | — |")
        if not r["equal"]:
            stop = True

    print("\n=== per-profile detail ===")
    for r in rows:
        print(f"\n## {r['profile']}")
        if r["skipped"]:
            print(f"  SKIPPED: {r['skipped']}")
            continue
        if r["error"]:
            print(f"  ERROR: {r['error']}")
            continue
        print(f"  files={r['files']} (f1={r['f1']} f2={r['f2']}) "
              f"records={r['records']} lifted={r['lifted_files']}")
        print(f"  roots: {r['roots']}")
        print(f"  before==after: {r['equal']}")
        if r["diff"]:
            print(f"  first diff paths ({len(r['diff'])}):")
            for d in r["diff"][:20]:
                print(f"    {d}")
        print(f"  repeat open writes nothing: {r['reopened_zero']}")
        print(f"  .bak per file: {r['baks']}")
        print(f"  uuids identical across the two copies: {r['uuids_match']}")
        print(f"  via registry:    {r['reg_via']}")
        print(f"  tracks registry: {r['reg_trk']}")

    print("\n=== VERDICT ===")
    if stop:
        print("STOP: a profile's 'before' != 'after' — see the diff paths above.")
        return 2
    print("no 'before' != 'after' divergence. (Other !! cells are findings, "
          "read them in the table.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
