# kicadstamp/diagnostics/probe_registry_keys_inventory.py
"""У5.0 inventory probe (plan plan_2026_10_02_uuid_format_2_to_3.md §6.2).

READ ONLY, on COPIES of the profiles (rule 28): reads each profile's
registry/config.registry.json and tracks/config.tracks.registry.json, classifies
every key by its `anchor_id` form, and resolves the record-name part against the
profile's own FULLY LOADED config (materialized tree_instances / sheet_templates
included, via loader._load_config_uncached — no on-disk write).

Prints, per profile: key count per anchor_id prefix; how many keys resolve to a
record and in which section; how many go through a FALLBACK identity (record
without `name:` — cluster / cluster-role / net); how many are AMBIGUOUS; how many
are ORPHANS; how many name a record that has no name at all.

Run:  python -m kicadstamp.diagnostics.probe_registry_keys_inventory <profile-dir> ...
"""
import json
import sys
from collections import Counter
from pathlib import Path

from kicadstamp.config.loader import _load_config_uncached
from kicadstamp.config.models import (
    chain_effective_name,
    clone_placement_effective_name,
    coordinate_placement_effective_name,
    entity_effective_name,
    imprint_effective_name,
    net_trace_effective_name,
)

_LIST_SECTIONS = ("chains", "clone_placements", "thermal_via_arrays",
                  "coordinate_placements", "net_traces", "entities", "imprints")
_DICT_SECTIONS = ("cells", "points")
_FREE_SECTIONS = ("extract_profiles", "clone_profiles", "sheet_templates")


def _config_of(root: Path):
    cfg, _ctx = _load_config_uncached(str(root))
    return cfg


def _identities(cfg) -> tuple[dict, dict]:
    """({identity: {sections}}, {identity: 'named'|'fallback'}) over the FULLY
    loaded config (so materialized tree_instances/sheet_templates copies count)."""
    by_section: dict[str, set] = {}
    kind: dict[str, str] = {}

    def add(identity, section, named):
        if not identity:
            return
        by_section.setdefault(identity, set()).add(section)
        prev = kind.get(identity)
        kind[identity] = "named" if (named or prev == "named") else "fallback"

    for section in _DICT_SECTIONS + _FREE_SECTIONS:
        for name in (getattr(cfg, section, None) or {}):
            add(name, section, True)

    for e in (cfg.entities or []):
        add(entity_effective_name(e), "entities", bool(getattr(e, "name", None)))
    for c in (cfg.clone_placements or []):
        add(clone_placement_effective_name(c), "clone_placements",
            bool(getattr(c, "name", None)))
    for t in (cfg.thermal_via_arrays or []):
        add(t.name, "thermal_via_arrays", True)
    for nt in (cfg.net_traces or []):
        add(net_trace_effective_name(nt), "net_traces", bool(getattr(nt, "name", None)))
    for cp in (cfg.coordinate_placements or []):
        add(coordinate_placement_effective_name(cp), "coordinate_placements",
            bool(getattr(cp, "name", None)))
    for ch in (cfg.chains or []):
        add(chain_effective_name(ch), "chains", bool(getattr(ch, "name", None)))
    for imp in (cfg.imprints or []):
        add(imprint_effective_name(imp), "imprints", True)
    return by_section, kind


def _anchor_form(anchor_id: str) -> str:
    for prefix in ("name:", "point:", "anchor:", "role:", "pad:",
                   "thermal:", "net:", "imprint:"):
        if anchor_id.startswith(prefix):
            return prefix[:-1]
    return "other"


def _record_name(anchor_id: str, form: str):
    if form in ("anchor", "role", "pad"):
        return None                      # physics: refdes / role / pad number
    if form == "point":
        return anchor_id[len("point:"):].split(":", 1)[0]
    if form == "name":
        return anchor_id[len("name:"):].split("/", 1)[0]
    if form == "thermal":
        return anchor_id[len("thermal:"):]
    if form == "net":
        return anchor_id[len("net:"):]
    if form == "imprint":
        return anchor_id[len("imprint:"):].split(":", 1)[0]
    return None


def _target_sections(form: str) -> set:
    return {
        "name": {"entities", "clone_placements"},
        "point": {"points"},
        "thermal": {"thermal_via_arrays"},
        "net": {"net_traces"},
        "imprint": {"entities"},
    }.get(form, set())


def report(root: Path) -> None:
    print(f"\n=== {root.name} ===")
    config = next((p for p in sorted(root.glob("*.sexp"))), None)
    if config is None:
        print("  no root config found — skip")
        return
    print(f"  root: {config.name}")
    cfg = _config_of(config)
    by_section, kind = _identities(cfg)
    # PART 2 of the key — template_name (accept note 04.10). Per Р-У5.1 it also
    # moves to a UUID (the cell name / net-trace identity), and it is present on
    # every key, including the pad: ones the anchor_id check calls "physics".
    cell_names = set(cfg.cells or {})
    net_trace_names = {net_trace_effective_name(nt) for nt in (cfg.net_traces or [])}

    for label, reg_path in (("vias", root / "registry" / "config.registry.json"),
                            ("tracks", root / "tracks" / "config.tracks.registry.json")):
        if not reg_path.exists():
            print(f"  {label}: no registry file")
            continue
        raw = json.loads(reg_path.read_text(encoding="utf-8"))
        entries = {k: v for k, v in raw.items() if k != "schema_version"}
        forms = Counter()
        resolved = Counter()
        fallback = 0
        ambiguous = 0
        orphans = 0
        ex_orphans = []
        for key in entries:
            parts = key.split("|")
            anchor_id = parts[0]
            form = _anchor_form(anchor_id)
            forms[form] += 1
            name = _record_name(anchor_id, form)
            if name is None:
                resolved["physics"] += 1
                continue
            targets = _target_sections(form)
            hit = (by_section.get(name) or set()) & targets
            if not hit:
                orphans += 1
                if len(ex_orphans) < 5:
                    ex_orphans.append(name)
                continue
            if len(hit) > 1:
                ambiguous += 1
                resolved["AMBIGUOUS"] += 1
                continue
            resolved[next(iter(hit))] += 1
            if kind.get(name) == "fallback":
                fallback += 1
        print(f"  {label}: {len(entries)} keys | forms {dict(forms)}")
        print(f"    resolved {dict(resolved)} | record-without-name {fallback} | "
              f"ambiguous {ambiguous} | orphans {orphans}")
        if ex_orphans:
            print(f"    orphan sample: {ex_orphans}")

        # template_name (parts[1]) — cells / net_traces / the literal
        # "thermal_via_array" (via_planner.py:303); orphan and ambiguous counted
        # the same way as the anchor_id part. Expected: orphans 0, else STOP.
        tm_cells = tm_net_traces = tm_literal = tm_ambiguous = tm_orphans = 0
        tm_examples = []
        for key in entries:
            parts = key.split("|")
            if len(parts) < 2:
                continue
            tm_name = parts[1]
            if _anchor_form(parts[0]) == "thermal":
                tm_literal += 1
                continue
            in_cells = tm_name in cell_names
            in_nt = tm_name in net_trace_names
            hits = int(in_cells) + int(in_nt)
            if hits > 1:
                tm_ambiguous += 1
            elif in_nt:
                tm_net_traces += 1
            elif in_cells:
                tm_cells += 1
            else:
                tm_orphans += 1
                if len(tm_examples) < 5:
                    tm_examples.append(tm_name)
        print(f"    template_name: cells {tm_cells} | net_traces {tm_net_traces} | "
              f"literal 'thermal_via_array' {tm_literal} | ambiguous {tm_ambiguous} | "
              f"orphans {tm_orphans}")
        if tm_examples:
            print(f"    template_name orphan sample: {tm_examples}")


def main(argv):
    roots = [Path(a) for a in argv] or sorted(Path("profiles").glob("*-u5copy"))
    for root in roots:
        report(root)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
