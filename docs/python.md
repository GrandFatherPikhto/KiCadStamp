# Coding placement in Python: `explore`/`author`

Two additive, optional modules for using KiCadStamp as a library instead of (or alongside) the
CLI/YAML workflow — neither changes the YAML config format or the `apply`/`extract` pipeline, both are
thin wrappers around what already exists. For the YAML syntax itself, see
[docs/config.md](config.md); for the CLI commands, see [docs/commands.md](commands.md).

This page has two parts: an API reference (`kicadstamp.explore`/`kicadstamp.author`), then a worked
walkthrough mirroring a real script that ships in this repo
(`boards/3ch-awg-tia/scripts/dac_channels.py`).

---

## `kicadstamp.explore` — read-only querying

Grew out of a recurring pattern: answering "which components have `Role=X`", "what net is this pad
on", "which sheet instance (`Channel_0`/`Channel_1`/...) is this footprint under" by writing a new
throwaway script every time. `Board.select()` replaces that with one reusable call.

```python
from kicadstamp.explore import Board

board = Board.connect(config_path="boards/3ch-awg-tia/profiles/power.yaml",
                       schematic_dir="../../../test_boards/3CH-AWG-TIA")

board.select(role="AD_DAC").show()
# ref   role    cluster  sheet      nets
# ----  ------  -------  ---------  ----
# IC2   AD_DAC  -        Channel_0  ...
# IC3   AD_DAC  -        Channel_1  ...
# IC4   AD_DAC  -        Channel_2  ...

# same ambiguity that causes a real fatal in apply if left unaddressed: role
# repeats twice per channel — select() shows it up front instead of failing
# mid-run.
board.select(role="R_TERM_P", sheet="Channel_0").show()
# ref  role      cluster  sheet      nets
# ---  --------  -------  ---------  ----
# R33  R_TERM_P  -        Channel_0  ...
# R39  R_TERM_P  -        Channel_0  ...

# escape hatch: .fp is the raw FootprintInstance, for anything not covered here
comp = board.select(ref="IC2")[0]
comp.nets           # {'21': '/Channel_0/DAC/DAC_OUT_P', ...}
comp.fp.position     # raw kipy object (nanometres)
```

`select()` filters (all optional, AND-combined):

| Filter | Match |
|---|---|
| `ref` | exact refdes |
| `role` | exact `Role` field value |
| `cluster` | **segment-prefix** — same as the real `anchor_cluster` resolver (`Channel_1` matches `Channel_1/1V2_PLL`, not `Channel_10`) |
| `sheet` | membership in the footprint's resolved sheet-instance chain |
| `net` | any pad on this net |

`Board` is a **stable snapshot**, taken at `connect()`/`refresh()` — it never re-fetches on its own.
Call `board.refresh()` after any board change (a manual edit in KiCad, or a scripted
`apply_config()`/`cli_main(..., --apply)` run) before trusting the next `select()`.

`select_items(...)` returns the raw mixed list (footprints/vias/tracks) `get_selected_items()` would,
built from filters instead of a live GUI selection — pass straight to
`template_extraction.extract_template_from_selection(items=...)` for a scripted `extract` (`net` alone
can't disambiguate same-net components across different physical instances of a shared net like `GND`
— fine for the common case where the net name is already unique, otherwise keep a live GUI selection
for that one subsystem).

---

## `kicadstamp.author` — coding placement instead of copy-pasting YAML

Per-channel `clone_placements` written by hand are exactly where copy-paste mistakes creep in (wrong
`nets:` key, duplicate `anchor_pad:`, a sheet name copied from the wrong neighbour) — a `for` loop
can't make those. `ClonePlacement`/`Rule` (`kicadstamp.config`) are plain dataclasses — build them
directly, with real Python variables instead of `{placeholder}` YAML substitution:

```python
from kicadstamp.config import ClonePlacement

clones = [
    ClonePlacement(
        name=f"channel_{i}_ad9707", role="AD_DAC",
        anchor_role="FPGA", anchor_sheet=ch,
        nets={"AD_DAC": f"/Channel_{i}/DAC/DAC_OUT_P"},
        xy=(0.0, 25.0 - 25.0 * i), rotation_deg=270.0 - 90.0 * i,
        retired=False, skip=False,
    )
    for i, ch in enumerate(["Channel_0", "Channel_1", "Channel_2"])
]
```

`xy=` is a plain 2-tuple (not `origin_x_mm=`/`origin_y_mm=` — those were renamed 2026-07-31, see the
`clone_placements:` section of [docs/config.md](config.md)). `retired=`/`skip=` default `False` on the
dataclass — spelling them out above is only for clarity, not required.

**Option (a) — the standard entry point (`cli_main`):**

Every script under `boards/*/scripts/*.py` uses this — one place for the `--apply`/`--dry-run`/
`--verbose` argparse boilerplate, instead of every script reinventing it. `cli_main` lives in
`kicadstamp.author_cli` (split out of `kicadstamp.author` 2026-08-11 so the author module stays a
pure library):

```python
# boards/3ch-awg-tia/scripts/my_subsystem.py
from pathlib import Path
from kicadstamp.author_cli import cli_main
from kicadstamp.config import ClonePlacement

HERE = Path(__file__).resolve().parent
OUTPUT = HERE.parent / "generated" / "my_subsystem.yaml"

def build() -> list:
    return [ClonePlacement(...), ...]

if __name__ == "__main__":
    cli_main(build, str(OUTPUT), str(HERE.parent / "profiles/power.yaml"), description=__doc__)
```

```bash
python boards/3ch-awg-tia/scripts/my_subsystem.py             # writes OUTPUT only, never touches the board
python boards/3ch-awg-tia/scripts/my_subsystem.py --apply --dry-run --verbose   # plan, no write
python boards/3ch-awg-tia/scripts/my_subsystem.py --apply                      # writes OUTPUT, then applies it
```

`root_config_path` (the third argument, `.../profiles/power.yaml` above) is what actually gets loaded
and applied with `--apply` — it's the one carrying `schematic_dir`/`registry_path`, and
(via `include:`) it's expected to pick up `OUTPUT` itself, so the registry sees the FULL board config,
not just this one script's slice (a partial `Config` built from one script alone is unsafe for
registry pruning — see the "Getting it wrong" section below).

**Option (b) — lower-level pieces**, if `cli_main` doesn't fit (e.g. no `--apply` gating wanted at
all):

```python
from kicadstamp.author import dump_clone_placements, dump_rules, dump_template, apply_config

dump_clone_placements(clones, "boards/3ch-awg-tia/generated/dac_channels.yaml")   # {'clone_placements': [...]}
dump_rules(rules, "boards/3ch-awg-tia/generated/fpga_spokes.yaml")                # {'rules': [...]}
dump_template({"my_cell": {"vias": [...], "components": [...]}}, "templates/my_cell.yaml")

# straight into the live apply pipeline, bypassing the generated-YAML step entirely:
from kicadstamp.config import load_config
cfg, ctx = load_config("boards/3ch-awg-tia/profiles/power.yaml")
cfg.clone_placements.extend(clones)
apply_config(cfg, "boards/3ch-awg-tia/profiles/power.yaml", ctx=ctx, dry_run=True)
```

`apply_config`'s `config_path` argument is **not cosmetic** — same reasoning as `root_config_path`
above: when `cfg.registry_path`/`cfg.track_registry_path` are unset, they're derived from it. A
throwaway placeholder path here would misfile or collide registries between unrelated scripted runs.

---

## Getting it wrong: skipping the generated-YAML step

It's tempting to skip writing `OUTPUT` and go straight from `build()` to `apply_config()` on a `Config`
assembled from just this one script's `clones`. Don't — `registry.reconcile()`'s pruning
(`known_anchor_ids`) needs the **full** `cfg.clone_placements` (every subsystem, via `include:`) to
know what's still supposed to exist; a `Config` built from one script's slice alone would make pruning
think every *other* subsystem's vias/tracks are stale and delete them. Always: write the generated
YAML → load the real root config (which `include:`s it) → apply. `cli_main` already does exactly this.

---

## Worked example: real script, `boards/3ch-awg-tia/scripts/dac_channels.py`

This mirrors a real script in the repo — read it alongside this walkthrough (`AD_DAC_LAYOUT`/
`PASSIVE_LAYOUT`/`OP_AMPS` are per-channel lookup tables, not formulas, since each channel's DAC sits
on a different side of the FPGA).

### Step 1 — look before you leap

Before writing any placement code, use `explore` to see what you're actually dealing with — don't
guess at Role/Cluster/net names or assume a Role is unique:

```python
from kicadstamp.explore import Board

board = Board.connect(config_path="boards/3ch-awg-tia/profiles/power.yaml",
                       schematic_dir="../../../test_boards/3CH-AWG-TIA")
board.select(role="AD_DAC").show()
```

### Step 2 — express the repetition as a loop, verify offsets don't need re-guessing per channel

`xy:` is a **flat shift from the anchor — never auto-rotated** by the engine (see
[docs/config.md](config.md)'s note on `xy:`'s three meanings). Copying Channel_0's offset numbers onto
a differently-rotated Channel_1/2 would silently misplace the passive. `dac_channels.py` handles this
by rotating the verified Channel_0 baseline with the same primitive the engine itself uses
(`kipy.geometry.Vector2.rotate()`, matching `geometry/spoke_layout.py`'s `rotate_local_offset`) —
computed once, then visually verified live in KiCad, not hand-guessed per channel:

```python
AD_DAC_LAYOUT = {
    0: (0.0, 25.0, 270.0),
    1: (25.0, 0.0, 0.0),
    2: (0.0, -25.0, 90.0),
}

def build() -> list:
    clones = []
    for channel, (x, y, rot) in AD_DAC_LAYOUT.items():
        clones.append(ClonePlacement(
            name=f"channel_{channel}_ad9707", role="AD_DAC",
            anchor_role="FPGA", anchor_sheet=f"Channel_{channel}",
            nets={"AD_DAC": f"/Channel_{channel}/DAC/DAC_OUT_P"},
            xy=(x, y), rotation_deg=rot,
        ))
    # ... PASSIVE_LAYOUT/OP_AMPS follow the same per-channel-table shape
    return clones
```

A `for` loop physically cannot make the mistakes that come from copy-pasting three similar YAML blocks
by hand: a wrong `nets:` key, a duplicated `anchor_pad:` line, a sheet name copied from the wrong
neighbour — all real bugs hit while writing this exact subsystem by hand before it was scripted.

### Step 3 — try it

```bash
python boards/3ch-awg-tia/scripts/dac_channels.py --apply --dry-run --verbose
```

Then re-run `board.refresh()` + `board.select(...)` to confirm the result with the same tool used to
investigate ambiguity in Step 1 — closes the "did it actually do what I meant" question without
opening KiCad.

### Step 4 — apply for real, keep the generated YAML in git

```bash
python boards/3ch-awg-tia/scripts/dac_channels.py --apply
```

`OUTPUT` (`boards/3ch-awg-tia/generated/dac_channels.yaml`) is committed — plain, diffable YAML, even
though a Python script authored it. `boards/3ch-awg-tia/profiles/dac_channels.yaml` picks it up via
`include:`, the normal way. The script stays in the repo too, so re-running it after a real board
change (or extending it to a 4th channel) regenerates the same file instead of hand-editing it.

---

## A second real example: read-only generation, `build_p3v3_ldo_cell.py`

Not every script needs `cli_main`/`--apply` at all — `boards/3ch-awg-tia/scripts/build_p3v3_ldo_cell.py`
only ever reads the live board (`kicadstamp.explore.Board`, never mutates anything) to measure real pad
positions, then writes a `Cell` definition via `dump_template()`:

```python
from kicadstamp.author import dump_template
from kicadstamp.explore import Board
from kicadstamp.utils.units import MM

board = Board.connect(config_path="boards/3ch-awg-tia/profiles/power.yaml")
ldo_fp = board.select(role="LDO_3V3")[0].fp
origin_x_mm, origin_y_mm = ldo_fp.position.x / MM, ldo_fp.position.y / MM
# ... measure other live pad positions, subtract origin ...

dump_template({"p3v3_ldo_composite": {"clone_placements": [...]}},
              "boards/3ch-awg-tia/profiles/templates/p3v3_ldo_composite.yaml")
```

This is the general pattern for anything geometry-dependent that isn't safely derivable from YAML
numbers alone (component-centre-to-pad offsets, footprint dimensions) — measure it against the real
board with `explore`, don't hand-guess it. See the script's own docstring for the full rationale (it
exists specifically because `CellPlacement`, the nested-cell type, has no live anchor fields at all —
only a literal `xy:` relative to the parent cell — so turning an anchor-resolved position into that
literal number needs a real measurement).

---

## See also

- [docs/config.md](config.md) — the YAML schema these Python objects mirror field-for-field.
- [docs/commands.md](commands.md) — the CLI (`apply`/`extract`) these scripts wrap or replace.
- [docs/placement.md](placement.md) — what `apply_config()` actually does once it's called
  (dependency ordering, the registry, collision handling) — same pipeline either way.
