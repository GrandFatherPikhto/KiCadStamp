"""
probe_channel_twins.py — one-off live probe (read-only): shows the channel
layout of the open board exactly as channel_copy.py sees it.

For every footprint with a usable sheet_path.path chain it prints:
  - twin groups: inner_key (path[1:]) -> [(channel_uuid, ref), ...] — members
    with more than one entry are TWINS of the same symbol across channels;
  - channel name -> sheet uuid mapping (from the local-net pad prefix, the same
    rule channel_copy.resolve_channel_uuids uses);
  - the refs/roles/clusters of the SOURCE channel, so a pivot refdes can be
    chosen for --pivot.

Run:
    python -m kicadstamp.diagnostics.probe_channel_twins [CHANNEL]
"""
from collections import defaultdict
import sys

from kicadstamp.kicad.adapter import KiCadBoardAdapter
from kicadstamp.constants import ROLE_FIELD_NAME, CLUSTER_FIELD_NAME
from kicadstamp.channel_copy import _channel_name_of_fp


def main():
    adapter = KiCadBoardAdapter()
    adapter.refresh_board()
    all_fps = adapter.get_footprints()

    groups = defaultdict(list)
    for fp in all_fps:
        chain = [str(u.value) for u in fp.sheet_path.path]
        if len(chain) < 2:
            continue
        inner = "/" + "/".join(chain[1:])
        groups[inner].append((chain[0], fp.reference_field.text.value))

    print("=== TWIN GROUPS (inner_key -> channel_uuid/ref) ===")
    twins = 0
    for inner, members in sorted(groups.items()):
        if len(members) > 1:
            twins += 1
            print(f"  {inner}: " + ", ".join(f"{u}/{r}" for u, r in members))
    print(f"groups with 2+ members (real twins): {twins}")

    name_to_uuid = defaultdict(set)
    for fp in all_fps:
        name = _channel_name_of_fp(adapter, fp)
        if name is None:
            continue
        chain = [str(u.value) for u in fp.sheet_path.path]
        if chain:
            name_to_uuid[name].add(chain[0])
    print("\n=== CHANNEL NAME -> SHEET UUID ===")
    for name, uuids in sorted(name_to_uuid.items()):
        print(f"  {name}: {sorted(uuids)}")

    src = sys.argv[1] if len(sys.argv) > 1 else "Channel_0"
    src_uuids = name_to_uuid.get(src, set())
    print(f"\n=== COMPONENTS OF {src} (uuid {sorted(src_uuids)}) ===")
    for fp in all_fps:
        chain = [str(u.value) for u in fp.sheet_path.path]
        if not chain or (src_uuids and chain[0] not in src_uuids):
            continue
        ref = fp.reference_field.text.value
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        cluster = adapter.get_field_value(fp, CLUSTER_FIELD_NAME)
        x = fp.position.x / 1e6
        y = fp.position.y / 1e6
        print(f"  {ref:8s} role={role or '':20s} cluster={cluster or '':24s} "
              f"({x:8.3f}, {y:8.3f}) mm  uuid={chain[-1][:8]}")

    adapter.close()


if __name__ == "__main__":
    main()
