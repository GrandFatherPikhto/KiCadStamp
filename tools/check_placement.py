"""tools/check_placement.py — read-only: is every part on ITS cluster's slot, and whose slot does a misplaced part occupy?

For every cluster INSTANCE on the board (footprints grouped by Cluster field + sheet path) whose
cluster maps to exactly one cell, the frame is found by CONSENSUS: every pair of roles proposes a
(rotation, mirror) through the product's own fit (cell_frame.fit_cell_frame), the proposal that puts
the most roles within RIGID_TOLERANCE_MM wins. Roles outside that consensus are MISPLACED. For each
misplaced part the probe looks for a slot of ANOTHER instance it now occupies (a "stolen" spot), and
for a part of another cluster standing on this instance's empty slot.

Nothing is written: the profile is copied into a mktemp dir (relative schematic paths made absolute
in the COPY), the board is only read, the copy is deleted.

usage: .venv/bin/python tools/check_placement.py <profile dir | profile .sexp> [--slot-mm 0.5] [--plan dryrun.txt]

Every real run (not --plan) prints NEW / GONE against the previous run of the same profile and
remembers itself in ~/.local/state/kicadstamp/placement_check_<profile>.json — run it after every
placement action and the action that steals a part shows up as NEW.
"""
import itertools
import math
import re
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kicadstamp.adapter_factory import create_board_adapter          # noqa: E402
from kicadstamp.cell_frame import (RIGID_TOLERANCE_MM, fit_cell_frame,  # noqa: E402
                                   pair_deviation_mm, rotate_ydown_mm)
from kicadstamp.config import load_config                            # noqa: E402
from kicadstamp.constants import DEFAULT_TIMEOUT_MS                  # noqa: E402
from kicadstamp.sheet_names import resolve_sheet_path_names          # noqa: E402
from kicadstamp.utils.units import MM                                # noqa: E402


def _config_name(profile: Path) -> str:
    """The profile's main config file: config.sexp, else <profile>.sexp, else the only *.sexp."""
    for name in ("config.sexp", f"{profile.name}.sexp"):
        if (profile / name).is_file():
            return name
    found = sorted(f.name for f in profile.glob("*.sexp"))
    if len(found) == 1:
        return found[0]
    raise SystemExit(f"cannot tell the config file in {profile}: {found or 'no *.sexp'} — pass the .sexp path")


def _load_copy(profile: Path, cfg_name: str):
    tmp = Path(tempfile.mkdtemp(prefix="kstamp_stolen_"))
    copy = tmp / "p"
    shutil.copytree(profile, copy)
    cfg_file = copy / cfg_name
    text = cfg_file.read_text(encoding="utf-8")
    text = re.sub(r'"((?:\.\./)+[^"]*)"',
                  lambda m: '"%s"' % (profile / m.group(1)).resolve(), text)
    cfg_file.write_text(text, encoding="utf-8")
    cfg, ctx = load_config(cfg_file)
    return tmp, cfg_file, cfg, ctx


def _cluster_cells(cfg) -> dict:
    """cluster -> set of cell names, from entities (the instances the trees place)."""
    out = defaultdict(set)
    for ent in getattr(cfg, "entities", None) or []:
        if getattr(ent, "cluster", None) and getattr(ent, "cell", None):
            out[ent.cluster].add(ent.cell)
    return out


def _map(stored, rot, mirror):
    px, py = rotate_ydown_mm(stored[0], stored[1], rot)
    return (-px if mirror else px), py


def _consensus(slots, fps):
    """slots: role -> (along, across, angle); fps: role -> footprint. Returns
    (ref_role, rot, mirror, inliers:set, dev:dict) or None."""
    roles = sorted(fps)
    best = None
    for r0, r1 in itertools.permutations(roles, 2):
        s0, f0 = slots[r0], fps[r0]
        lx0, ly0 = f0.position.x / MM, f0.position.y / MM
        pair = (slots[r1][0] - s0[0], slots[r1][1] - s0[1],
                fps[r1].position.x / MM - lx0, fps[r1].position.y / MM - ly0)
        fit = fit_cell_frame([pair])
        if fit is None:
            continue
        dev = {}
        for r in roles:
            p = (slots[r][0] - s0[0], slots[r][1] - s0[1],
                 fps[r].position.x / MM - lx0, fps[r].position.y / MM - ly0)
            dev[r] = pair_deviation_mm(p, fit.rotation_deg, fit.mirror)
        inl = {r for r in roles if dev[r] <= RIGID_TOLERANCE_MM}
        key = (len(inl), -sum(dev[r] for r in inl))
        if best is None or key > best[0]:
            best = (key, (r0, fit.rotation_deg, fit.mirror, inl, dev))
    return None if best is None else best[1]


def main(argv):
    target = Path(argv[0]).resolve()
    if target.is_file():                 # a path to the profile's .sexp
        profile, cfg_name = target.parent, target.name
    else:
        profile, cfg_name = target, _config_name(target)
    slot_mm = float(argv[argv.index("--slot-mm") + 1]) if "--slot-mm" in argv else 0.5
    tmp, cfg_file, cfg, ctx = _load_copy(profile, cfg_name)
    try:
        sheet_names = dict((ctx.sheet_names if ctx else {}) or {})
        cl_cells = _cluster_cells(cfg)
        adapter = create_board_adapter(timeout_ms=DEFAULT_TIMEOUT_MS, config_path=str(cfg_file))
        try:
            adapter.refresh_board()
            # the open board must be THIS profile's project, or every number below is nonsense
            board = Path(adapter.get_board_filename() or "").stem
            sheet = Path(getattr(ctx, "root_sheet", None) or "").stem
            if board and sheet and board != sheet:
                print(f"REFUSED: KiCad has {board!r} open, the profile is for {sheet!r} — open that board first")
                return 2
            fps = list(adapter.get_footprints())
            if "--plan" in argv:      # simulate: positions AFTER a dry-run plan (kicadstamp_cli apply --dry-run output)
                import dataclasses
                from kicadstamp.domain.geometry import Vector2
                plan = {m[0]: (float(m[1]), float(m[2]), float(m[3])) for m in re.findall(
                    r"^  ([A-Z]+[0-9]+): \(([-\d.]+), ([-\d.]+)\) mm, angle=([-\d.]+)",
                    Path(argv[argv.index("--plan") + 1]).read_text(), re.M)}
                fps = [dataclasses.replace(f, position=Vector2.from_xy(int(round(plan[f.ref][0] * MM)),
                                                                       int(round(plan[f.ref][1] * MM))),
                                           angle_deg=plan[f.ref][2]) if f.ref in plan else f for f in fps]
                print(f"SIMULATED: {sum(1 for f in fps if f.ref in plan)} footprints moved to their planned place")
            info = {}
            for fp in fps:
                info[fp.ref] = (adapter.get_field_value(fp, "Role"),
                                adapter.get_field_value(fp, "Cluster"),
                                # the instance is the TOP sheet (an entity's `sheet`: Channel_0, FPGA, MCU);
                                # a cluster may span its sub-sheets (DAC_BUF: Channel_0/DAC + Channel_0/OpAmp)
                                next(iter(resolve_sheet_path_names(fp, sheet_names)), None) or "/")
        finally:
            adapter.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    by_inst = defaultdict(lambda: defaultdict(list))   # (cluster, sheet) -> role -> [fp]
    for fp in fps:
        role, cluster, sheet = info[fp.ref]
        if role and cluster:
            by_inst[(cluster, sheet)][role].append(fp)

    print(f"board footprints: {len(fps)}; cluster instances: {len(by_inst)}; "
          f"tolerance {RIGID_TOLERANCE_MM} mm, slot match {slot_mm} mm")
    report = []          # (inst, cell, n, inliers, rot, mirror, misplaced[], missing[], dups[], angle_bad[])
    slots_world = []     # (x, y, inst, role, owner_ref) — expected position of every role of a TRUSTED instance
    skipped = []
    for inst, role_fps in sorted(by_inst.items()):
        cells = cl_cells.get(inst[0], set())
        if len(cells) != 1:
            skipped.append((inst, "no entity cell" if not cells else f"{len(cells)} cells"))
            continue
        cell_name = next(iter(cells))
        cell = cfg.cells.get(cell_name)
        if cell is None:
            skipped.append((inst, f"cell {cell_name!r} missing"))
            continue
        slots = {c.role: (float(c.offset_along_mm), float(c.offset_across_mm), float(getattr(c, "angle_deg", 0.0)))
                 for c in cell.components}
        dups = [(r, [f.ref for f in l]) for r, l in role_fps.items() if len(l) > 1]
        one = {r: l[0] for r, l in role_fps.items() if len(l) == 1 and r in slots}
        foreign = sorted(r for r in role_fps if r not in slots)
        missing = sorted(r for r in slots if r not in role_fps)
        if len(one) < 2:
            skipped.append((inst, f"{len(one)} matched role(s) — no frame"))
            continue
        cons = _consensus(slots, one)
        if cons is None:
            skipped.append((inst, "no usable pair"))
            continue
        r0, rot, mirror, inl, dev = cons
        f0 = one[r0]
        x0, y0 = f0.position.x / MM, f0.position.y / MM
        mis = []
        undecided = len(inl) < 2          # e.g. a two-role cell whose pair disagrees: nobody can be blamed
        for r in sorted(one):
            if r in inl and not undecided:
                continue
            dx, dy = _map((slots[r][0] - slots[r0][0], slots[r][1] - slots[r0][1]), rot, mirror)
            mis.append((r, one[r].ref, (one[r].position.x / MM, one[r].position.y / MM),
                        (x0 + dx, y0 + dy), dev[r]))
        ang = []
        a0 = f0.angle_deg
        for r in sorted(inl):
            f = one[r]
            a = f.angle_deg
            if a is None or a0 is None:
                break
            want = (slots[r][2] - slots[r0][2]) * (-1 if mirror else 1)
            got = float(a) - float(a0)
            d = (got - want + 180.0) % 360.0 - 180.0
            if abs(d) > 0.1:
                ang.append((r, f.ref, d))
        trusted = len(inl) >= 2
        if trusted:
            for r in slots:
                dx, dy = _map((slots[r][0] - slots[r0][0], slots[r][1] - slots[r0][1]), rot, mirror)
                owner = one.get(r)
                slots_world.append((x0 + dx, y0 + dy, inst, r, owner.ref if owner else None))
        report.append((inst, cell_name, len(one), len(inl), rot, mirror, mis, missing, dups, ang, foreign, trusted))

    def near_slot(x, y, own_inst, own_role):
        best = None
        for sx, sy, inst, role, owner in slots_world:
            if inst == own_inst and role == own_role:
                continue
            d = math.hypot(sx - x, sy - y)
            if d <= slot_mm and (best is None or d < best[0]):
                best = (d, inst, role, owner)
        return best

    issues = {}      # stable key -> one-line text: what the NEXT run is compared against
    bad = [r for r in report if r[6] or r[8] or r[9]]
    print(f"instances checked: {len(report)}; clean: {len(report) - len(bad)}; with problems: {len(bad)}; "
          f"skipped: {len(skipped)}\n")
    for inst, cell_name, n, ninl, rot, mirror, mis, missing, dups, ang, foreign, trusted in bad:
        print(f"[{inst[0]} @ {inst[1]}] cell {cell_name!r}: {ninl}/{n} roles agree "
              f"(rot {rot:+.0f}, mirror {mirror}){'' if trusted else '  — FRAME NOT TRUSTED'}")
        for r, ref, live, want, d in mis:
            tag = "PAIR?" if not trusted else "MISPLACED"
            line = (f"   {tag} {r} ({ref}) at ({live[0]:.3f}, {live[1]:.3f}), its slot ({want[0]:.3f}, "
                    f"{want[1]:.3f}) — {d:.3f} mm off")
            hit = near_slot(live[0], live[1], inst, r)
            if hit:
                d2, oinst, orole, owner = hit
                line += (f"\n      → stands on slot {orole} of [{oinst[0]} @ {oinst[1]}] ({d2:.3f} mm)"
                         f"{'' if owner is None else f', whose own part is {owner}'}")
            print(line)
            issues[f"MIS|{ref}|{live[0]:.2f}|{live[1]:.2f}"] = (
                f"{ref} ({r} of {inst[0]} @ {inst[1]}) at ({live[0]:.3f}, {live[1]:.3f}), {d:.3f} mm off its slot"
                + ("" if not hit else f", on slot {hit[2]} of {hit[1][0]}"))
        for r, refs in dups:
            print(f"   DUPLICATE role {r}: {', '.join(refs)}")
            issues[f"DUP|{inst[0]}|{inst[1]}|{r}"] = f"duplicate role {r} in {inst[0]} @ {inst[1]}: {', '.join(refs)}"
        for r, ref, d in ang:
            print(f"   TURNED {r} ({ref}) by {d:+.1f} deg relative to the cell")
            issues[f"TURN|{ref}|{d:.0f}"] = f"{ref} ({r} of {inst[0]}) turned {d:+.1f} deg"
        if missing:
            print(f"   (absent roles: {', '.join(missing)})")
        if foreign:
            print(f"   (roles not in the cell: {', '.join(foreign)})")
    # parts of instances with an EMPTY slot occupied by someone else
    print("\n--- empty slots taken by another cluster's part ---")
    owners = {}
    for fp in fps:
        owners[fp.ref] = fp
    taken = 0
    for sx, sy, inst, role, owner in slots_world:
        if owner is not None:
            continue
        for fp in fps:
            if math.hypot(fp.position.x / MM - sx, fp.position.y / MM - sy) <= slot_mm:
                r, c, s = info[fp.ref]
                print(f"   slot {role} of [{inst[0]} @ {inst[1]}] is empty; {fp.ref} ({r} of {c} @ {s}) stands there")
                issues[f"TAKEN|{inst[0]}|{inst[1]}|{role}|{fp.ref}"] = (
                    f"{fp.ref} ({r} of {c}) stands on the empty slot {role} of {inst[0]} @ {inst[1]}")
                taken += 1
    if not taken:
        print("   none")
    if skipped:
        print("\n--- skipped instances ---")
        for inst, why in skipped:
            print(f"   [{inst[0]} @ {inst[1]}]: {why}")
    if "--plan" not in argv:
        _compare_with_last(profile.name, issues)
    return 0


def _compare_with_last(name, issues):
    """NEW / GONE against the previous run of this profile, then remember this run.
    Kept outside profiles/ (machine state, never Syncthing): ~/.local/state/kicadstamp/."""
    import json
    import time
    state = Path.home() / ".local" / "state" / "kicadstamp" / f"placement_check_{name}.json"
    print("\n=== CHANGES since the last run ===")
    if state.is_file():
        last = json.loads(state.read_text(encoding="utf-8"))
        old = last.get("issues", {})
        new_keys = [k for k in issues if k not in old]
        gone_keys = [k for k in old if k not in issues]
        print(f"   last run: {last.get('when', '?')}")
        if not new_keys and not gone_keys:
            print("   nothing changed")
        for k in new_keys:
            print(f"   NEW   {issues[k]}")
        for k in gone_keys:
            print(f"   GONE  {old[k]}")
    else:
        print("   first run — this result is the baseline")
    print(f"   now: {len(issues)} problem(s) on the board")
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"when": time.strftime("%Y-%m-%d %H:%M:%S"), "issues": issues},
                                ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
