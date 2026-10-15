# kicadstamp/diagnostics/claude_probe_footprint_models_2026_10_15.py
"""Probe (READ-ONLY, live board): per footprint — the 3D models as the KiCad API
returns them, position, angle, layer and the definition's item/pad counts. Each
run saves a snapshot under a label and prints the difference against the
previous snapshot. It never writes the board.

Written for plan_2026_10_15_footprint_3d_model_loss: Denis cleared the board and
ran F8 (step1), ran the placer (step2), then Tools -> Update Footprints From
Library (step3). The snapshots gave 73 -> 0 -> 73 footprints with a model, the
loss exactly on the footprints the placer moved, each one item short — the
`Footprint3DModel` that kipy's `FootprintInstance.orientation` setter drops when
it rebuilds `definition.items`. Kept to re-check the fix live the same way.

Run (KiCad with the board open in the PCB editor):
    .venv/bin/python kicadstamp/diagnostics/claude_probe_footprint_models_2026_10_15.py step1_f8
    .venv/bin/python kicadstamp/diagnostics/claude_probe_footprint_models_2026_10_15.py step2_placer

Snapshots go to <system temp>/kicadstamp_fp_snapshots (override with --out).
"""
import argparse
import json
import tempfile
from pathlib import Path

from kipy import KiCad


def _snapshot(board) -> dict:
    snap = {}
    for fp in board.get_footprints():
        ref = fp.reference_field.text.value
        d = fp.definition
        snap[ref] = {
            "lib": str(d.id.name) if d.id else "",
            "models": [m.filename for m in d.models],
            "x": fp.position.x, "y": fp.position.y,
            "angle": round(fp.orientation.degrees, 3),
            "layer": int(fp.layer),
            "items": len(d.items),
            "pads": len(d.pads),
        }
    return snap


def _print_diff(prev: dict, snap: dict) -> None:
    for ref in sorted(set(prev) | set(snap)):
        a, b = prev.get(ref), snap.get(ref)
        if a is None or b is None:
            print(f"    {ref}: {'appeared' if a is None else 'gone'}")
            continue
        parts = []
        for k in b:
            if a.get(k) == b[k]:
                continue
            if k in ("x", "y"):
                parts.append(f"{k} {a[k] / 1e6:.3f}->{b[k] / 1e6:.3f}")
            elif k == "models":
                parts.append(f"models {len(a[k])}->{len(b[k])}")
            else:
                parts.append(f"{k} {a[k]}->{b[k]}")
        if parts:
            print(f"    {ref}: {', '.join(parts)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("label", help="snapshot name, e.g. step1_f8")
    ap.add_argument("--out", type=Path,
                    default=Path(tempfile.gettempdir()) / "kicadstamp_fp_snapshots")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    snap = _snapshot(KiCad(timeout_ms=5000).get_board())
    with_models = sum(1 for v in snap.values() if v["models"])
    print(f"[{args.label}] footprints {len(snap)}, with models via API {with_models}")
    missing = sorted(r for r, v in snap.items() if not v["models"])
    print(f"  without models ({len(missing)}): {' '.join(missing)}")

    previous = sorted(args.out.glob("*.json"), key=lambda p: p.stat().st_mtime)
    (args.out / f"{args.label}.json").write_text(json.dumps(snap, indent=1))
    if previous:
        print(f"  diff vs {previous[-1].stem}:")
        _print_diff(json.loads(previous[-1].read_text()), snap)


if __name__ == "__main__":
    main()
