#!/usr/bin/env python3
"""Standalone repro script: does an IPC-driven footprint move + save corrupt
pad-level rotation data for footprints with heterogeneous per-pad library
rotation (e.g. QFP packages)?

Does NOT depend on kicadstamp — only on kicad-python (kipy), the official
IPC API bindings. Meant to be run twice, against two different KiCad
instances/versions, pointed at two otherwise-identical throwaway projects
each containing one footprint (e.g. IC1) with non-zero per-pad rotation.

Usage:
    python3 ipc_pad_rotation_repro.py <path-to-project>.kicad_pcb [reference]

    reference defaults to "IC1".

What it does:
    1. Connects to whichever KiCad instance is currently running.
    2. Finds the footprint by reference, snapshots its pads' angles
       (library-level, from footprint.definition.pads).
    3. Nudges the footprint's position by 1mm via update_items() (an
       ordinary IPC write, exactly what a placement tool would do).
    4. Re-reads the pads live, right after the write (before saving).
    5. Saves the board via board.save() (IPC — no manual Ctrl+S needed).
    6. Re-reads the pads live again, AND re-parses the same footprint's
       pad angles directly out of the saved .kicad_pcb file on disk.
    7. Prints a before/after table and a plain verdict.
"""

import re
import sys
from pathlib import Path

from kipy import KiCad
from kipy.geometry import Vector2


def snapshot_live(footprint) -> dict[str, float]:
    """pad number -> rotation angle in degrees, as reported live by the API.

    The kipy wrapper always returns an Angle (defaulting to 0.0) — it does
    not distinguish "explicitly zero" from "unset" the way the raw
    .kicad_pcb S-expression does (an omitted angle in `(at X Y)` means the
    same thing as `(at X Y 0)`). Normalize exact 0.0 to None here so it
    compares equal to the file parser's "no angle written" reading below —
    otherwise every always-zero pad falsely shows up as "changed".
    """
    return {
        pad.number: (pad.padstack.angle.degrees or None)
        for pad in footprint.definition.pads
    }


def snapshot_from_file(pcb_path: Path, ref: str) -> dict[str, float | None]:
    """Parse pad angles for the footprint with the given reference straight
    out of the raw .kicad_pcb S-expression text, independent of the IPC API
    entirely (this is what actually gets reloaded if you reopen the board)."""
    text = pcb_path.read_text()

    # Find the (footprint ...) block whose (property "Reference" "<ref>" ...)
    # matches, by locating the reference string and walking outward/inward
    # with simple paren balancing (good enough for this narrow purpose).
    ref_marker = f'"Reference" "{ref}"'
    ref_pos = text.find(ref_marker)
    if ref_pos == -1:
        raise ValueError(f"Reference {ref!r} not found in {pcb_path}")

    # Walk backward from ref_pos to the start of the enclosing (footprint ...).
    start = text.rfind("(footprint ", 0, ref_pos)
    if start == -1:
        raise ValueError(f"Could not find enclosing (footprint ...) for {ref!r}")

    # Walk forward from start, balancing parens, to find the block's end.
    depth = 0
    end = start
    for i, ch in enumerate(text[start:], start):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    block = text[start:end]

    angles: dict[str, float | None] = {}
    for m in re.finditer(
        r'\(pad\s+"(\d+)"\s+\S+\s+\S+\s*\(at\s+[-\d.]+\s+[-\d.]+(?:\s+([-\d.]+))?\)',
        block,
    ):
        num, angle = m.group(1), m.group(2)
        angles[num] = float(angle) if angle is not None else None
    return angles


def diff_table(before: dict, after: dict) -> tuple[list[str], bool]:
    lines = []
    changed = False
    for num in sorted(before, key=lambda n: int(n)):
        b, a = before.get(num), after.get(num)
        mark = ""
        if b != a:
            changed = True
            mark = "  <-- CHANGED"
        lines.append(f"  pad {num:>4}: {b!s:>8} -> {a!s:>8}{mark}")
    return lines, changed


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    pcb_path = Path(sys.argv[1]).resolve()
    ref = sys.argv[2] if len(sys.argv) > 2 else "IC1"

    kicad = KiCad(timeout_ms=10000)
    print(f"Connected to KiCad {kicad.get_version()}")

    board = kicad.get_board()

    def find_fp():
        # get_footprints() is a live RPC, not cached — always re-fetch
        # rather than reuse a stale local object, so every snapshot below
        # genuinely reflects the server's current state.
        footprints = board.get_footprints()
        fp = next((f for f in footprints if f.reference_field.text.value == ref), None)
        if fp is None:
            refs = [f.reference_field.text.value for f in footprints]
            print(f"Reference {ref!r} not found. Footprints on board: {refs}")
            sys.exit(1)
        return fp

    fp = find_fp()
    print(f"Found {ref} at position {fp.position}, orientation {fp.orientation}")

    before_live = snapshot_live(fp)
    print(f"\nPad angles BEFORE move (live, {len(before_live)} pads).")

    # Nudge the footprint by 1mm on X — a plain IPC move, same as any
    # placement tool would do (fp.position = ...; update_items([fp])).
    nudge_mm = 1.0
    fp.position = Vector2.from_xy_mm(fp.position.x / 1e6 + nudge_mm, fp.position.y / 1e6)
    board.update_items([fp])
    print(f"\nMoved {ref} by {nudge_mm}mm on X via update_items().")

    after_live_prewrite = snapshot_live(find_fp())
    lines, changed = diff_table(before_live, after_live_prewrite)
    print(f"\nPad angles right after the IPC write, BEFORE save (live, re-fetched):")
    print("\n".join(lines))
    print("--> CHANGED already at this point!" if changed else "--> unchanged, as expected.")

    board.save()
    print("\nSaved via board.save().")

    after_live_postsave = snapshot_live(find_fp())
    lines, changed_live = diff_table(before_live, after_live_postsave)
    print(f"\nPad angles AFTER save (live, re-fetched from KiCad):")
    print("\n".join(lines))

    after_file = snapshot_from_file(pcb_path, ref)
    lines, changed_file = diff_table(before_live, after_file)
    print(f"\nPad angles AFTER save (parsed straight from {pcb_path.name} on disk):")
    print("\n".join(lines))

    print("\n" + "=" * 60)
    if changed_live or changed_file:
        print(f"VERDICT: pad rotation CORRUPTED (live={changed_live}, on-disk={changed_file})")
    else:
        print("VERDICT: pad rotation intact, no corruption observed.")
    print("=" * 60)


if __name__ == "__main__":
    main()
