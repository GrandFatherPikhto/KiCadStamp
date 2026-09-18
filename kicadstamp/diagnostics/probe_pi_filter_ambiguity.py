#!/usr/bin/env python3
"""
probe_pi_filter_ambiguity.py — prints Role/Cluster/human-readable sheet path
and nets for chosen refs.

Input:
    Optional refdes arguments (default: C139/C143/C148).

Expected:
    For each target ref: ROLE/CLUSTER field values, resolved sheet-path names,
    and the sorted pad nets — the same data clone_role_resolver uses, to see
    why anchor_sheet/anchor_cluster do not narrow ambiguous candidates down to
    one (see the FATAL ERROR ambiguity list in --apply output).

Live KiCad:
    Yes — requires a running KiCad with the board open.

Run:
    python -m kicadstamp.diagnostics.probe_pi_filter_ambiguity [REF ...]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.config import load_config
from kicadstamp.adapter_factory import create_board_adapter
from kicadstamp.sheet_names import resolve_sheet_path_names
from kicadstamp.constants import ROLE_FIELD_NAME, CLUSTER_FIELD_NAME

CONFIG_PATH = str(Path(__file__).resolve().parents[2] / "boards" / "3ch-awg-tia" / "3ch-awg-tia.yaml")
DEFAULT_TARGET_REFS = {"C139", "C143", "C148"}


def main():
    target_refs = set(sys.argv[1:]) or DEFAULT_TARGET_REFS
    cfg = load_config(CONFIG_PATH)
    print(f"sheet_names: {len(cfg.sheet_names)} записей\n")

    adapter = create_board_adapter(config_path=CONFIG_PATH)
    adapter.refresh_board()

    for fp in adapter.get_footprints():
        ref = fp.reference_field.text.value
        if ref not in target_refs:
            continue
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        cluster = adapter.get_field_value(fp, CLUSTER_FIELD_NAME)
        names = resolve_sheet_path_names(fp, cfg.sheet_names)
        pads = adapter.get_footprint_pads(fp)
        nets = sorted({p.net.name for p in pads if p.net and p.net.name})
        print(f"{ref}:")
        print(f"  Role={role!r}  Cluster={cluster!r}")
        print(f"  sheet path (человекочитаемо): {names}")
        print(f"  nets: {nets}")
        print()


if __name__ == "__main__":
    main()
