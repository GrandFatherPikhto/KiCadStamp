#!/usr/bin/env python3
"""
probe_scheme_list_layers.py — read-only live probe for the Scheme List layer
extension (plan_2026_09_05_scheme_list.md Step 0).

Answers three questions for the open board:
  1. How does kipy name/number its BoardLayer enum members (the inner copper
     layers In1.Cu/In2.Cu in particular)? — pure enum introspection, no KiCad
     needed.
  2. What copper layers does the live board's routed copper actually sit on
     (raw per-layer track/via counts, read BEFORE the domain's binary
     _layer_from_kipy mapping would collapse inner layers to F.Cu)?
  3. Is there any real routed copper on the inner layers (In1.Cu/In2.Cu)?

Run (KiCad should be running with the test board open):
    .venv/bin/python -m kicadstamp.diagnostics.probe_scheme_list_layers

The enum-introspection part (question 1) also runs without a live board.
"""
from collections import Counter
import sys


def _layer_enum_members():
    """Return [(member_name, int_value), ...] from kipy's BoardLayer enum,
    tolerating both protobuf EnumTypeWrapper and a plain IntEnum."""
    from kipy.board_types import BoardLayer

    items = []
    # protobuf EnumTypeWrapper exposes keys()/items() (Mapping-like)
    if hasattr(BoardLayer, "items"):
        for name in BoardLayer.keys():
            try:
                items.append((name, BoardLayer.Value(name)))
            except Exception:
                items.append((name, "?"))
        return items
    # IntEnum fallback
    for member in BoardLayer:
        items.append((member.name, member.value))
    return items


def _kipy_name_to_display(name: str) -> str:
    """'BL_In1_Cu' -> 'In1.Cu' (copper layers), else the raw name."""
    if name.startswith("BL_"):
        return name[3:].replace("_", ".")
    return name


def probe_enum():
    print("=== 1. kipy BoardLayer enum (name = value) ===")
    try:
        members = _layer_enum_members()
    except Exception as exc:  # pragma: no cover - depends on environment
        print(f"  could not import kipy.board_types.BoardLayer: {type(exc).__name__}: {exc}")
        return None

    copper = [(n, v) for n, v in members if "_Cu" in n]
    print(f"  total members: {len(members)}; copper-looking: {len(copper)}")
    for name, value in members:
        if "_Cu" in name:
            print(f"    {name} = {value}   (display: {_kipy_name_to_display(name)})")
    # A few extra non-copper examples for context
    others = [f"{n}={v}" for n, v in members if "_Cu" not in n][:5]
    print(f"  non-copper examples (first 5): {others}")
    return dict(members)


def probe_board():
    print("\n=== 2. Live board: routed copper per raw layer ===")
    try:
        import kipy
    except Exception as exc:  # pragma: no cover - depends on environment
        print(f"  kipy import failed: {type(exc).__name__}: {exc}")
        return
    try:
        from kipy.board_types import BoardLayer
    except Exception as exc:  # pragma: no cover
        print(f"  BoardLayer import failed: {type(exc).__name__}: {exc}")
        return

    try:
        kc = kipy.KiCad()
    except Exception as exc:  # pragma: no cover - no live KiCad
        print(f"  could not connect to KiCad: {type(exc).__name__}: {exc}")
        print("  (run with KiCad open if you want the live-board part)")
        return

    try:
        board = kc.get_board()

        tracks = list(board.get_tracks())
        vias = list(board.get_vias())
        footprints = list(board.get_footprints())

        def layer_of(item):
            # Tracks/footprints carry a .layer; kipy's Via has none (through
            # vias span all copper layers by definition).
            raw = getattr(item, "layer", None)
            if raw is None:
                return None
            try:
                return BoardLayer.Name(raw)
            except Exception:
                return str(raw)

        track_layers = Counter(l for t in tracks if (l := layer_of(t)) is not None)
        fp_layers = Counter(l for f in footprints if (l := layer_of(f)) is not None)

        print(f"  board: {len(footprints)} footprints, {len(tracks)} tracks, "
              f"{len(vias)} vias (through — span every copper layer)")
        all_cu = sorted({*track_layers, *fp_layers})
        print("  layer           tracks  footprints")
        for name in all_cu:
            print(f"    {_kipy_name_to_display(name):14s} "
                  f"{track_layers.get(name, 0):6d} {fp_layers.get(name, 0):11d}")

        inner_track_hits = [n for n in all_cu if "In" in n and track_layers.get(n)]
        if inner_track_hits:
            print(f"\n  RESULT: routed copper (tracks) found on inner layers: {inner_track_hits}")
        else:
            print("\n  RESULT: no routed copper (tracks) on any inner layer "
                  "(all tracks are on F.Cu/B.Cu)")
    finally:
        try:
            kc.close()
        except Exception:
            pass


def main():
    probe_enum()
    probe_board()
    print("\ndone.")


if __name__ == "__main__":
    sys.exit(main())
