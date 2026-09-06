# YAML Configuration Reference

Everything about **writing** a KiCadStamp config from scratch: root fields, every section
(`cells:`/`chains:`/`clone_placements:`/`thermal_via_arrays:`/`points:`), `include:`, and
`extract_profiles:`/`clone_profiles:`. For running commands against a config, see
[docs/commands.md](commands.md); for coding placement in Python instead of hand-writing YAML, see
[docs/python.md](python.md); for the module/class architecture behind all of this, see
[docs/architect.md](architect.md).

Every example on this page is drawn from a real, currently-loading config —
`boards/3ch-awg-tia/profiles/*.sexp` — not invented syntax. Field names match
`kicadstamp/config/models.py` exactly as of 2026-08-01.

---

## Root fields

```yaml
# boards/3ch-awg-tia/profiles/fpga.sexp
registry_path: registries/fpga.json
track_registry_path: registries/fpga.tracks.registry.json
log_file: ../logs/fpga.log
schematic_dir: ../../../test_boards/3CH-AWG-TIA
layer: B.Cu

thermal_via_arrays:
  - ...

include:
  - templates/fpga_pi_filters.sexp
  - fpga_extracts.sexp
  - chains/fpga_spokes.sexp

clone_placements:
  ...
```

| Field | Type | Meaning |
|---|---|---|
| `layer` | string | `F.Cu`\|`B.Cu` — default layer for the `chains:`/ManualSpoke path only. `clone_placements:` each carry their own `layer:`, unaffected by this. |
| `cells` | mapping | Inline `Cell` definitions (see below). Rare to write by hand — usually populated by `extract`; can be split across files via `include:` (see below). |
| `points` | mapping | Named, reusable anchors (see **Points** below). |
| `include` | list | Other YAML files to merge in — see **`include:`** below. |
| `chains` | list | ManualSpoke chains — see **`chains:`** below. (Legacy `chains:` is still read as an alias.) |
| `clone_placements` | list | TemplatePlacer placements — see **`clone_placements:`** below. |
| `entities` | list | Entity records — the "what" of a placement, WITHOUT any position — see **`entities:`** below. |
| `trees` | list | Placement trees — the ONLY place a position can live — see **`trees:`** below. |
| `tree_instances` | list | Sheet-parameterized references to a template tree — see **`tree_instances:`** below. |
| `thermal_via_arrays` | list | Any number of thermal via grids, each independently named/anchored — see **`thermal_via_arrays:`** below. |
| `place_components` | bool | Default `true`. `false` moves/creates vias and tracks but leaves component positions untouched. |
| `skip_existing_components` | bool | Default `false`. Skip components (and their vias/tracks) already at the target position — cheap idempotency for re-runs. Note (2026-08-31): the TRACK positional pre-check runs regardless of this flag — it only skips a planned track that already exists at the exact position/net/width/layer, so it can never remove copper, only prevent literal duplicates. |
| `via_keepout_clearance_mm`, `via_search_step_mm`, `via_search_max_radius_mm`, `via_search_n_directions` | numbers | Free-space search parameters, used only by thermal via placement. |
| `schematic_dir` | string | Folder with the project's `*.kicad_sch` files, for `anchor_sheet` resolution. Relative to this YAML file's own location, like `registry_path`. |
| `schematic_files` | list of strings | Extra `.kicad_sch` files outside `schematic_dir` (e.g. the root sheet, if it lives elsewhere). |
| `registry_path`, `track_registry_path` | strings | Explicit paths for the placement/track registries (see [docs/placement.md](placement.md)). Default: derived from the config file's own name/path if unset. |
| `log_file` | string | Log file for `apply` runs against this config — avoids retyping `--log-file` every time. The CLI's own `--log-file` flag wins if both are given. |
| `board_name` | string | Optional. Name of the board (`.kicad_pcb`) this config targets, e.g. `3CH-AWG-TIA-v102` (the extension may be omitted). When set, `apply` fatals BEFORE any other check if the board actually open in KiCad has a different name — a clear "wrong board" error instead of a misleading failure deep in some other subsystem (a real incident: a stale `schematic_dir` pointing at a previous board revision surfaced as an unrelated `anchor_sheet` fatal in Extract). Compared case-insensitively by basename stem only, never the full path (config and board live in unrelated directory trees, and paths differ across machines). Unset = no check, old profiles unaffected. |

**Deprecated, fatal on load (no silent fallback):** `templates_file`/`template_files` (renamed to
`cells_file`/`cell_files`, themselves folded into `include:` on 2026-08-02 — see next), `cells_file`/
`cell_files` (external `Cell` files are now just listed under `include:`, same as any other split-off
section — wrap the external file's content in a `cells:` key), `target_ref`/`side` at the root.

---

## `cells:` — defining reusable geometry

A `Cell` (until 2026-07-31, `SpokeTemplate`) is a piece of geometry described **once**, in its own
local coordinate system (`along`/`across`, rotation-invariant — always described at `rotation_deg=0`),
then placed (and rotated/mirrored/shifted as a whole) wherever it's used — by a `chains:` spoke or a
`clone_placements:` entry.

A `Cell` can be a **leaf** (`vias:`/`components:`/`tracks:`), a **composite** (`clone_placements:`
nesting other cells), or both at once.

### Leaf cell

```yaml
# boards/3ch-awg-tia/profiles/templates/ldo_3v3.sexp — a file listed under
# power.sexp's include: (external Cell files are wrapped in cells:, same
# shape as an inline block, since cells_file:/cell_files: were folded into
# include: on 2026-08-02)
cells:
  p3v3_ldo:
    layer: F.Cu
    vias:
      - offset_along_mm: -7.415
        offset_across_mm: -2.28
        net: GND
        drill_mm: 0.5
        diameter_mm: 1.0
      - offset_along_mm: -7.415
        offset_across_mm: 2.28
        net: '{PWR_IN}'          # {placeholder} — resolved from params: at placement time
        drill_mm: 0.5
        diameter_mm: 1.0
    components:
      - role: LDO_3V3             # matched by the Role custom field, not a specific ref
        offset_along_mm: 0.0
        offset_across_mm: 0.0
        angle_deg: 0.0
        net_template: '{PWR_OUT}' # for ClonePlacement's by-nets role matching only
        net_template_pad: '1'     # optional: which pad of the resolved candidate carries that net
    tracks:
      - start_along_mm: -5.04
        start_across_mm: -2.28
        end_along_mm: -7.415
        end_across_mm: -2.28
        width_mm: 0.8
        net: GND
```

- `vias:` — `offset_along_mm`/`offset_across_mm` (local), `net:` (`null`/omitted means "inherit the
  chain's net" — only `chains:`/ManualSpoke supports that; `clone_placements:` fatals on a via with no
  net, since it has no single "chain net" to fall back to), `drill_mm`, `diameter_mm`. Same `null`
  convention for `tracks:`' own `net:` below. The old GUI Extract dock's per-net **"Chain net (null)"**
  checkbox (2026-08-05, removed with the dock in Phase F 2026-09-01) wrote exactly this — see
  `extract_profiles:`' `rule_nets:` below for the CLI/profile-file equivalent (`--rule-net`).
  A via/track net may instead be resolved live from a role's real pad — `net_from_role:` /
  `net_from_role_pad:` (see `clone_placements:`/net-from-role) — and this now works on BOTH
  placement paths, including `chains:`/ManualSpoke (2026-09-05, Bug 3 GND-duplication fix): a
  GND-assigned via of a bypass role (`net_from_role` + pad `'2'`) is planned as GND, not as the
  chain's net — it needs neither `net: null` nor a literal.
- `components:` — `role:` (matched against the board's `Role` custom field, **not** a refdes — the
  same role can resolve to a different real component every time the cell is placed), local
  offset+angle, its own `vias:` (same shape, nested under the component), optional `layer:` (only
  when it differs from the cell's own layer — e.g. a bottom-side component in an otherwise top-side
  cell), and `net_template:` (used only by `clone_placements:`'s by-nets role matching — see below;
  `chains:`/ManualSpoke ignores it entirely, matching roles purely by `(chain.net, Role)`).
  Optional `net_template_pad:` (2026-08-16) — only meaningful together with `net_template:`: which
  pad of the resolved candidate carries that role's net, for roles whose real component has MORE
  than one non-rule net (a regulator/diode/inductor — LDO VIN/VOUT, etc.). Without it, the Placer's
  "Auto-fill from board" requires the candidate to have exactly one non-rule net total and skips
  multi-pad roles; with it, that one specific pad is read directly, deterministically. It does NOT
  affect the by-nets resolver at apply time (that one already searches by an already-known expected
  net — pad count doesn't matter there), and it is fatal to load if set without `net_template:`
  (mirrors via/track's `net_from_role_pad`-without-`net_from_role` check).
  Optional `net_template_same_as_role:` (2026-08-16) — the safer ALTERNATIVE to `net_template_pad:`
  (mutually exclusive with it, fatal if both set): names ANOTHER role of THIS cell whose resolved
  net (lemma-2-safe, exactly one non-rule net) is electrically IDENTICAL to this role's. Electrical
  topology (which nets are the same node) is guaranteed to survive between clone instances; a pad
  number is not — found live 2026-08-16: an R/C's identifying net sat on pad 2 in one routed
  instance and pad 1 in another (identical geometry, verified not rotation/mirror), silently wrong
  for 3 of 13 roles. Prefer `net_template_same_as_role` whenever a lemma-2 sibling exists; extract
  writes it automatically when one is available in the same selection. Fixed-pinout parts (ICs/
  diodes/polarized caps) stay safe with `net_template_pad:` and keep using it.
- `tracks:` — straight segments only (no arcs); a polyline is just several consecutive `tracks:`
  entries sharing an endpoint. Collisions with existing copper are **not** checked by this tool —
  KiCad's own DRC is the source of truth for that, by design (see [docs/geometry.md](geometry.md)).
- `layer:` at the cell's own top level — the layer it was extracted on; components/tracks without
  their own `layer:` inherit it.
- `anchor_xy:` / `anchor_role:` (`+anchor_pad:`) — the cell's MOUNT POINT and its identity (reworked
  2026-09-05, design_2026_09_05 v2). Stored local offsets always live in the bbox-anchored frame;
  `anchor_xy [x, y]` is the mount point A in that frame — the cell point that coincides with a
  placement's origin at materialization. Geometry reads A and places content as
  `element = origin + rotate(offset − A)` (`cell_mount_offset`, clone_geometry/spoke_layout);
  absent all three = A = (0,0) — the default bbox corner (a no-op for a freshly extracted default
  cell). `anchor_role` names the mount's component (identity / live surrogate for Trees auto-anchor,
  refresh and live reads) and, given without a pad or `anchor_xy`, A is derived as that component's
  centre; `anchor_pad` narrows `anchor_role` to a specific pad whose point is expressed by
  `anchor_xy` (a pad offset is not derivable from config). `anchor_role` must name one of this cell's
  own `components:`; a role-only `anchor_xy` must equal that role's centre.
- `comment:` — optional free-form note shown in the GUI's Cell editor and as a marker on the config
  tree leaf. A plain schema field (survives YAML/s-expr round-trips), NOT a YAML `#` comment.
- Every `components:` entry **requires** a non-empty `role:` — a missing/`null` role used to either
  crash with a bare `KeyError` or silently propagate into placement as a confusing runtime "role None
  is in cell but not found anywhere on board"; now a clear load-time error (found live 2026-08-06).

### Composite cell (recursive, since 2026-07-31)

```yaml
# a Cell whose content is other cells, not raw geometry
p3v3_ldo_composite:
  clone_placements:
    - name: ldo_reg
      cell: p3v3_ldo        # the leaf cell above
      xy: [0.0, 0.0]        # relative to THIS composite's own (0,0) — see the xy: note below
      nets:
        LDO_1V2: '+3V3_DIRTY'
      params:
        PWR_IN: '+5V'
        PWR_OUT: '+3V3_DIRTY'
    - name: led_spoke
      cell: led_spoke
      xy: [-2.0, 5.0]
      params:
        PWR_IN: '+3V3'
        PWR_OUT: '/Power/+3V3_LED'
```

Each nested entry is a `CellPlacement` — **not** the same type as a top-level `clone_placements:`
entry (`ClonePlacement`). It's deliberately narrower: **closed boundary**, no `anchor_ref`/
`anchor_role`/`anchor_sheet`/`anchor_cluster`/`anchor_pad`/`by_selection`/`ignore_selection` at all —
those only make sense for something attached to the live board, and a nested placement never is.
Fields: `name` (required — used to build this nested item's own registry key), `cell:` **or** `role:`
(same meaning as `ClonePlacement.cell`/`role`, mutually exclusive), `xy:`, `rotation_deg:`, `mirror:`,
`layer:`, `sheet:`, `cluster:` (added 2026-08-26 — the placement's OWN identity for INTERNAL role
narrowing, the same meaning as `ClonePlacement.sheet`/`cluster` below: when a shared-rail role
(e.g. `+3V3` on a PI-filter) has several identical physical instances on the board, these narrow them
the same way they do for a top-level `ClonePlacement`; they are NOT external anchors — `anchor_*`
remain deliberately absent, the position still comes from the parent). `sheet:` when unset
(`null`/omitted) is INHERITED from the enclosing placement (the top-level `ClonePlacement` one level
up), chained through arbitrarily deep nesting (2026-08-26) — so a reusable composite cell (one
`dac_buf` definition cloned into `CH0_DAC_BUF`/`CH1_DAC_BUF`) resolves per-channel without
hardcoding the channel into the nested entries. Set it explicitly only when a nested placement
genuinely lives on a different sheet than its parent. `nets:`/`params:` may use a `{sheet}`
placeholder in a hierarchical net path (e.g. `/{sheet}/DAC/+3V3_DVDD`) — at apply time it resolves
to this nested placement's EFFECTIVE sheet (inherited or explicit), injected into its own
`params["sheet"]` (see `clone_position_calculator.py::_resolve_one_level`; an explicit
`params["sheet"]` wins). Same `{placeholder}` machinery as `ClonePlacement.anchor_sheet`'s
`Channel_{channel}`. `net_overrides:`, `refs:` — no param scoping, a nested placement never inherits
`params`/`nets` from its parent, same convention `ClonePlacement.params` already has.

Mirroring a **composite** cell (non-empty `clone_placements:`) is a fatal error, not implemented yet —
mirroring a **leaf** cell works as always.

Composite cells are also auto-generated by Extract's **Sub-placements** tab (2026-08-25, see
[docs/gui.md](gui.md)): when a selection sweeps up an already-existing top-level `clone_placement`
whole, the dock writes it as a `clone_placements:` reference instead of copying its geometry flat.
Since 2026-08-26 the dock also carries the source placement's `sheet:`/`cluster:` into the new
nested entry (own-identity for internal role narrowing, same as above), and templatizes a literal
`sheet` path segment in the copied `nets:`/`params:` to `{sheet}` — so a freshly extracted
reusable composite stays per-channel instead of carrying the source channel's path. When every
sub-placement in one extract batch is on the SAME sheet, `sheet:` is omitted on all of them
entirely (2026-08-26) — the reusable composite then lets the future parent's inheritance supply
the sheet per channel, instead of baking this extract's channel into the nested entries forever.
A cyclic reference graph (A → B → A) is rejected both at load time and — as the last line of defence,
for configs assembled in memory — by the resolver itself (a clean `ValidationError` naming the full
path, not a Python `RecursionError`).

> **Reading `xy:` in someone else's YAML — it's the same field name in three different coordinate
> frames, worth knowing which one you're in:**
> 1. On a `ClonePlacement` **with** an anchor (`anchor_ref`/`anchor_role`/`anchor_point`) — a flat
>    shift **from the resolved anchor position**.
> 2. On a `ClonePlacement` **without** any anchor — an **absolute** board coordinate.
> 3. On a `CellPlacement` (nested inside a composite `Cell`) — a shift from the **parent cell's own
>    local (0,0)**, never the board or a live anchor.
>
> Same chain everywhere ("flat shift, no automatic rotation"), three different origins — check for a
> sibling `anchor_*` field, and whether you're inside a `Cell` definition or a top-level
> `clone_placements:`, before trusting a bare `xy:` number.

---

## `chains:` — ManualSpoke (radial decoupling around one IC)

The original, oldest mechanism (renamed from `chains:` 2026-09-01 — plan rules_to_chains): a group
of spokes (small cells, usually a decap pair) placed radially around specific pads of **one**
anchor component, drawing real components from a pool by `(net, Role)` — not tied to a specific
refdes, so re-annotation-safe. Does **not** support tracks between spokes across different pads
(each spoke is self-contained). The old `chains:` key is still READ (alias) for backward
compatibility; [`tools/convert_rules_to_chains.py`](../tools/convert_rules_to_chains.py) rewrites
an existing profile to `chains:`.

> **Two `chains:` on the same net that consume the same `(role, cluster)` pool are a fatal
> validation error** (2026-08-20): the pool is rebuilt per chain with no ownership/distance, so
> whichever chain runs later silently takes components meant for its neighbour (a Redraw of one
> chain is where this is most visible). Fix: give the spokes of one of them a distinguishing
> `cluster:` so each chain draws from its own pool.

```yaml
# boards/3ch-awg-tia/profiles/rules/fpga_spokes.sexp
chains:
- net: +3V3_VCCIO
  name: +3V3_VCCIO       # optional — defaults to net if omitted, see below
  anchor_role: FPGA
  retired: false
  skip: false
  spokes:
  - pad: '17'
    cell: fpga_cap_pair_spoke
    shift_x_mm: 1.2
    shift_y_mm: -0.5
    rotation_deg: 90.0
    cluster: FPGA_PWR_BANK
  - pad: '26'
    cell: fpga_cap_pair_spoke
    shift_x_mm: 1.2
    shift_y_mm: -2.4
    rotation_deg: 90.0
    cluster: FPGA_PWR_BANK
```

**`Chain` fields:**

| Field | Meaning |
|---|---|
| `net` | Required. The net every spoke's components/vias resolve against (component pool lookup is `(net, Role)`). |
| `anchor_ref` **or** `anchor_role` (`+anchor_sheet`/`anchor_cluster`) **or** `anchor_point` | Exactly one — the anchor component whose pads the spokes attach to. |
| `name` | Optional, for `--only`. Defaults to `net` (see `chain_effective_name`). **Not** a grouping mechanism — don't reuse one `name:` across several chains to bundle them for `--only`; use a shared `Cluster` (`anchor_cluster`/`spoke.cluster`) instead. Two chains that resolve to the same effective name is a fatal load error. |
| `retired` | Default `false`. `true` = "does not exist on the board" — prunes this chain's via/track registry entries. Always wins over `--only`/`--cluster`. |
| `skip` | Default `false`. `true` = "leave alone this run" — narrows work like `--only`/`--cluster` would, but inline, without protecting/pruning the registry either way. |
| `comment` | Optional. Free-form note shown in the GUI — a plain schema field, not a YAML comment. |

**`ManualSpoke` (one entry of `spokes:`) fields:**

| Field | Meaning |
|---|---|
| `pad` | Required. Which pad of the chain's anchor this spoke attaches to (a string, like KiCad — `'17'`, not `17`). |
| `cell` | Required. The `Cell` (must be a leaf) this spoke places. |
| `shift_x_mm`/`shift_y_mm` | Board-absolute mm shift from the pad centre to the spoke's own origin (not local/rotated — tuned visually per spoke). |
| `rotation_deg` | Rotation of the resulting origin and all cell contents. |
| `cluster` | Optional — narrows which physical component pool entry a role resolves to when the same net+Role combination isn't unique on its own. |
| `retired`/`skip` | Same meaning as on `Chain`, scoped to just this one spoke. |

---

## `clone_placements:` — ClonePlacement (TemplatePlacer)

> **Migration (2026-08-30):** `clone_placements:` is the LEGACY placement path, still alive during
> the migration so live profiles keep working. New-style profiles use `entities:` + `trees:` instead
> (see below); [`tools/convert_placements.py`](../tools/convert_placements.py) converts a legacy
> profile (run it on a COPY — it writes a timestamped `.bak` first).

Applies a `Cell` at a new location — unlike `chains:` (anchor is always an IC pad), the anchor here is
just a name (`anchor_id` in the registry is `f"name:{name}"`), so it's the mechanism for repeated
multi-component sections (PI-filters, DAC channels, LDO subsystems) as well as one-off ones.

```yaml
# boards/3ch-awg-tia/profiles/fpga.sexp
clone_placements:
  - name: p3v3_vccio_pi_filter
    retired: false
    skip: false
    cell: fpga_in_pi_filter
    anchor_cluster: Pi_Filter_3V3_VCCIO
    anchor_role: FPGA
    anchor_pad: '139'
    xy: [-6.0, -6.0]
    params:
      PWR_IN: '+3V3'
      PWR_OUT: '+3V3_VCCIO'
```

**Positioning — two modes:**
- **Anchored** (`anchor_ref`/`anchor_role`(+`anchor_sheet`+`anchor_cluster`)/`anchor_point` set) —
  origin = centre of `anchor_pad` if given, else the anchor footprint's own centre, or the resolved
  Point's own position for `anchor_point`. `xy:` is then a flat shift from that (no auto-rotation —
  see the `xy:` note above), `rotation_deg` rotates only the cell's contents.
- **Absolute** (no anchor field set at all) — `xy:` is a required, literal board coordinate.

**Role → real component — three modes:**
- **By nets** (repeated sections — PI-filters, DAC channels), when `params`/`nets` are present, OR
  since Phase 2 step 2.3 when the implicit mode (no `params`/`nets`/`by_selection`) can auto-derive
  the whole cell on the live board: each
  role inside the cell resolves against a real net, via `nets:` (literal `role: net`) and/or
  `params:` (fills `{placeholder}`s in the cell's own `net_template:` fields, same substitution as
  via/track `net:`). Since Phase 2 step 2.1 these are OPTIONAL overrides: a role with no explicit
  net auto-derives its expected net from the live board (`derive_role_nets` — the unique instance's
  designated net, or the single non-rule net shared by all its candidates), and a LITERAL local
  `/Channel_0/...` `net_template` is prefix-remapped to the target channel (`TwinMap.twin_net`
  semantics). Ambiguous candidates (2+ matching the resolved net) are narrowed by
  the placement's own `sheet` → its own `Cluster` (the placement's `name:` — NOT `anchor_sheet`/`anchor_cluster`,
  which narrow only the anchor, see "Anchored" above) → current board selection → physical proximity
  to the anchor → a fatal error, in that order — see `clone_role_resolver.py`'s docstrings for the
  exact cascade.
- **By selection** (rare, one-off sections — a single MCU): explicitly with `by_selection: true`
  (needed when `params` is present only for via/track net resolution and would otherwise be misread
  as "by nets" mode), or — for the implicit no-`params`/`nets` case — only when the cell CANNOT
  auto-derive on the live board (Phase 2 step 2.3: a genuine one-off with no unambiguous source
  instance). Roles resolve against whatever's currently selected on the live board in the PCB editor.
- **By Cluster tag** (single component only — `cluster:` set, added 2026-08-06): the ONE component
  already tagged with that Cluster PCB field (assigned beforehand, e.g. via the GUI's Components tree
  or fieldstool) — no selection, no nets, no narrowing cascade. Zero or more than one match is fatal
  (Cluster is meant to be unique per instance, unlike Role — a shared category). `nets`/`params`/
  `by_selection` are meaningless here and fatal if set alongside `cluster`.

**Full field reference:**

| Field | Meaning |
|---|---|
| `name` | Required — registry identity fallback, `--only` target, shows up in every diagnostic message. |
| `sheet` | Optional. Own-identity sheet — narrows ambiguous Cluster+Role INSIDE the cell when this cell is cloned across reused sheets (a reused hierarchical sheet clones IDENTICAL custom fields onto every instance, so only the sheet can tell two physical copies apart — e.g. one PI-filter section per channel). NOT `anchor_sheet` — that narrows only the external anchor search (see `anchor_role` above). Split 2026-08-15 from `anchor_sheet`, completing the 2026-08-14 `anchor_cluster` split — same (Sheet, Cluster, Role) convention. Since 2026-08-16 the GUI's Auto-fill/Nets/Params narrowing (PlacerDock) feeds this same `sheet` into its live-board candidate search too — a reused-sheet Cluster+Role ambiguity narrows in the GUI exactly like it does at apply time. |
| `xy` | Required. See the anchored/absolute modes above and the `xy:` note. |
| `cell` **or** `role` **or** `cluster` | Exactly one. `cell:` references `cells:` (inline or `include:`d). `role:`/`cluster:` both synthesise a temporary one-component cell on the fly (for a placement not worth a whole cell file) — `role:` matches the live Role field (a category, ambiguity gets narrowed), `cluster:` matches an already-assigned Cluster field directly (meant to already be unique, no narrowing). |
| `rotation_deg` | Default `0.0`. Rotates the cell's contents (anchored mode) or the whole thing (absolute mode). |
| `anchor_ref` / `anchor_role`(+`anchor_sheet`+`anchor_cluster`) / `anchor_point` | Optional, mutually exclusive — see **Positioning** above. |
| `anchor_pad` | Optional, only meaningful with an anchor set — narrows the anchor to a specific pad rather than the footprint's centre. |
| `nets` | OPTIONAL (Phase 2 step 2.1): `{role: literal_net}` — explicit by-nets role mapping; when absent the role's expected net auto-derives from the live board. |
| `params` | `{placeholder: value}` — fills `{placeholder}`s in the cell's `net_template:`/via/track `net:` fields; presence alone (even with empty `nets`) selects by-nets mode unless `by_selection: true`. |
| `net_overrides` | OPTIONAL (Phase 2 step 2.1): `{resolved_net: replacement_net}` — final string substitution after the rest of net resolution, for edge cases the placeholder system can't express directly. |
| `refs` | `{role: refdes}` — explicit override, bypassing net-based search entirely; last resort when candidates are electrically indistinguishable. |
| `retired` / `skip` | Same convention as `Chain` — see above. `retired` always wins over `--only`/`--cluster`. |
| `by_selection` | Default `false`. Forces selection mode even when `params`/`nets` are present. |
| `ignore_selection` | Default `false`. Per-item counterpart of the CLI's `--no-selection`: treats the live GUI selection as empty for THIS placement's own resolution, regardless of the global flag — OR-composes with it. |
| `layer` | `F.Cu`\|`B.Cu`\|unset (inherit the cell's own layer, place verbatim). |
| `mirror` | Default `false`. Mirrors the whole construction — contradiction with `layer` (mirror without a layer change, or vice versa) is a fatal load error, since it would be physically meaningless. |
| `comment` | Optional. Free-form note shown in the GUI — a plain schema field, not a YAML comment. |

**Deprecated, fatal on load:** `origin_x_mm`/`origin_y_mm` (renamed to `xy: [x, y]`), `side` (replaced
by explicit `layer:`+`mirror:`).

---

## `entities:` — the "what" of a placement, without position

Since 2026-08-30 the former `clone_placements:` family splits into TWO concepts (authoritative
grammar: [`techdocs/handoff/deepseek/design_2026_08_30_entity_placement_grammar.md`](../techdocs/handoff/deepseek/design_2026_08_30_entity_placement_grammar.md)):

1. **`Entity`** (this section) — everything about a thing EXCEPT where it stands: `cell` (form),
   `nets`/`params`/`net_overrides` (electrics) and the instantiation identity
   (`cluster`/`sheet`/`retired`/`skip`/`ignore_selection`/`by_selection`/`refs`/`layer`/`mirror`/
   `comment`). **Position fields (`xy`/`anchor_*`/`rotation`) are forbidden on an Entity — a load-time
   fatal, not a silent ignore.**
2. **Placement = a `trees:` node** — see **`trees:`** below. `node.ref` for `kind "placement"` resolves
   into `Entity.name`; the node's `xy`/polar/`rotation` is where the Entity stands.

So "an Entity no tree node references" is a legitimate, explicitly *not placed* entity.

```sexp
(entities
  (entity
    (name "DAC_BUF_CH0")                    ; REQUIRED — unique across the include graph;
                                            ;   the --only / registry identity
    (cell "dac_buf")                        ; REQUIRED — reference into cells:
    (nets (ROLE1 "NET1") (ROLE2 "NET2"))    ; optional role -> net
    (params ("{PH}" "value"))               ; optional {placeholder} values
    (net_overrides (ROLE "NET"))            ; optional final rename
    (cluster "CH0")                         ; Cluster TAG written onto the board at Apply
    (sheet "Channel_0")                     ; optional — own identity for role narrowing
    (retired true) (skip true) (ignore_selection true) ; optional, default false
    (by_selection true)                     ; optional per-instance selection resolution
    (refs (ROLE "R1"))                      ; optional per-instance role -> ref
    (layer "F.Cu")                          ; optional; mirror only together with a layer change
    (mirror true)
    (comment "note")))
```

| Field | Meaning |
|---|---|
| `name` | REQUIRED — unique across the whole include graph; the `--only`/registry identity (replaces `ClonePlacement`'s effective name). |
| `cell` | REQUIRED — reference into `cells:` (the form to place). |
| `nets` | Optional `{role: literal_net}` — the same by-nets role mapping as `ClonePlacement.nets`. |
| `params` | Optional `{placeholder: value}` — fills `{placeholder}`s in the cell's `net_template:`/via/track `net:` fields. |
| `net_overrides` | Optional `{resolved_net: replacement_net}` — final substitution after the rest of net resolution. |
| `cluster` | Optional (unlike `ClonePlacement`) — the Cluster TAG written onto the board at Apply; an entity may exist "not placed" (no tree node) without a tag. |
| `sheet` | Optional own-identity sheet — narrows ambiguous Cluster+Role inside the cell across reused sheets. |
| `retired` / `skip` | Optional, default `false` — same convention as `Chain`. |
| `ignore_selection` | Optional, default `false` — per-item counterpart of `--no-selection`. |
| `by_selection` | Optional, default `false` — per-instance selection-based role resolution. |
| `refs` | Optional `{role: refdes}` — per-instance explicit override, bypasses net-based search. |
| `layer` / `mirror` | Optional — same cross-validation as `ClonePlacement` (mirror without a layer change, or vice versa, is a fatal load error). |
| `comment` | Optional free-form note shown in the GUI. |

A `scheme_list`-based Entity (design_2026_09_05_scheme_list.md) is different:
instead of `cell:` it references a recorded Scheme List and carries `sheet` = the
TARGET sheet. Empty `sheet` (or equal to the record's `source_sheet`) means the
record is replayed "in place" (its own refs move); a different `sheet` resolves
the recorded components' TWINS on that sheet and remaps their local nets to it.
Apply/Redraw of such an Entity never goes through the clone/cell machinery
(`scheme_list_apply.py` — the recorded snapshot is refdes-literal, not a Role
template). `mirror`/`layer` are meaningless here and are fatal at load (v1 has
no mirror formula).

---

## `trees:` — the ONLY place a position can live

Since 2026-08-30 the `trees:` section the TreesDock edits is also the **placement store** for
entities: a node with `kind "placement"` whose `ref` is an `Entity.name` IS that entity's placement —
"where it stands" is `parent_position + node offset`, exactly the semantics of `node_position`
(`kicadstamp/tree_position.py`). There is no separate `placements:` section.

```sexp
(tree (name "dac_buf_ch0")
  (anchor (origin))                            ; absolute, or:
  ; (anchor (ref "CONN_PM5V"))                 ;   by refdes (external = live-board-only)
  ; (anchor (role "FPGA") (sheet "...") (cluster "...") (pad "A1"))
  ; (anchor (point "P1"))
  (node (ref "DAC_BUF_CH0") (kind placement) (xy 10.0 20.0) (rotation 90.0)
        (children ...)))                       ; nested nodes form a rigid group
```

- `kind "clone"` was renamed to `kind "placement"`; on load `"clone"` is still accepted as an alias
  for `"placement"` during the migration. `chain`/`coordinate`/`point`/`external` node kinds are unchanged.
- `node.ref` for `kind "placement"` resolves to `Entity.name`, not the old `clone_placements:` list.
- A flat single placement = a tree with one node under `(anchor (origin))` or a component/point anchor.
- The one-ref-per-node tree chain means an Entity is always 1:1 with its tree node — an Entity cannot
  stand in two places.

**An `(anchor (ref ...))` may point at an Entity** — the tree is then anchored on ANOTHER tree's
placement node (cross-tree entity anchoring, since Phase 4.1 live). Because an Entity carries no
position of its own, such an anchor base is resolved RECURSIVELY at materialization: find the
(single) tree whose `kind "placement"` node references that Entity, resolve ITS anchor base
(origin/ref/role/another Entity ref — recursion), then compose that node's own offset on top. The
recursion is cycle-guarded; an Entity with no placement node, one referenced by more than one node,
or a chain that loops back into itself is a CONFIG error (fatal, never silently skipped).

**Module embedding (2026-09-02, plan_2026_09_02_tree_module_embedding.md):** a `trees:` node may also
have `kind "module"` — its `ref` is the NAME of ANOTHER tree, which is embedded as a rigid sub-layout
(e.g. the `fpga` tree embeds the `ch0_dac_buf` tree). The module node's own `xy`/`polar`/`rotation`
position its MARKER in the parent; an optional `(pivot-xy x y)` or `(pivot-polar r a)` (mutually
exclusive, module only) says which point INSIDE the referenced tree's own local frame lands exactly on
the marker (absent = the referenced tree's origin). A module ref is a tree name, NOT a config record —
it is exempt from the one-ref-per-node file-wide check, so the SAME child tree may be embedded by
several different parents; a duplicate inside ONE parent, an unknown/self reference and a module cycle
(A⊃B⊃A) are config fatals (validated at link/Save). When embedded, the referenced tree's OWN anchor is
ignored — its content is laid out from the module's pivot-inverted marker (pure geometry, applied as a
non-persistent position override by the forest-wide redraw).

---

## `tree_instances:` — one template tree, instantiated per schematic sheet (2026-09-02)

`Channel_1/2` of `DAC_BUF` are the SAME geometry as `Channel_0` — same cells/clusters/roles — and
differ only by the schematic SHEET (`Channel_1`/`Channel_2`). Copy-pasting the whole tree + its
`entities:` records N times silently drifts the moment the geometry changes. `tree_instances:`
declares the tree ONCE — a normal `trees:` entry plus its Entity records (its own `sheet`/anchor
`sheet` set to a real sheet, so the template stays live-re-readable by Role+Sheet+Cluster) — and
instantiates it per reuse:

```yaml
tree_instances:
  - template: dac_buf_tpl        # name of an existing trees: entry (the template)
    name: ch1_dac_buf            # the generated tree's name
    sheet: Channel_1             # substituted into the generated copies
```

Expansion runs inside `load_config()`, right after `include:` + `sheet_templates:` resolution and
before any per-entry loader: each declaration materializes a FULL Tree (a deep copy of the template —
every node's `ref` gets a `__{instance.name}` suffix recursively, the role anchor's `sheet` becomes
the instance's) plus one Entity per template `placement` node (a deep copy renamed to the new ref,
`sheet` = the instance's) and one `net_traces:` record per template `net_trace` node (see below). The
materialized records are ordinary for every consumer (embedding/redraw/apply) and go through the SAME
`_load_tree`/`_load_entity`/`_load_net_trace`, duplicate-name, one-record-per-net, rule-2 and
layer/mirror checks as hand-written ones — nothing is validated twice.

- **Not a separate file format:** `tree_instances:` lives ONLY in the main dict config (NOT in the
  `*.trees` s-expr format — it has no Entity records at all). It IS an `include:`-mergeable list
  section.
- **The template stays editable and alive (a normal tree):** its Entity records KEEP their own
  `sheet` — needed for live re-reading by Role+Sheet+Cluster (Q2, revised 2026-09-02) — while the
  generated COPY unconditionally gets the instance's `sheet`. Expansion only ever deep-copies; the
  template and the file on disk are never mutated.
- **v1.1 template constraints (each a load-time fatal):** the template's anchor must be `role`-based;
  every template node is `kind "placement"` (or unset/auto) and must reference an existing
  `entities:` record, OR `kind "net_trace"` and reference an existing `net_traces:` record by its net.
  (chain/coordinate/clone/module nodes inside a template stay unsupported.)
- **net_trace nodes inside a template (v1.1):** a `kind "net_trace"` node's `ref` is a real board NET
  (e.g. `/Channel_0/DAC/+3V3_AVDD`) that must stay a valid net name for the planner/KiCad — so it is
  NOT suffixed with `__{instance.name}` like a placement ref. Instead the net's LEADING SHEET SEGMENT
  is replaced with the instance `sheet` (`/Channel_1/DAC/+3V3_AVDD`), independently on the record's
  `net`, on every `tracks[].net` and every `vias[].net`; the generated `net_traces:` copy's
  `anchor_sheet` is unconditionally set to the instance `sheet`. The old sheet comes from the template
  tree's own role-anchor `sheet` — a net whose leading segment isn't it is a fatal (it's not this
  template's copper), never silently rewritten. Distinct per-instance nets keep the one-record-per-net
  dedup happy for free.
- **Generated instances are never persisted** as literal `trees:` entries: the TreesDock Save writes
  only hand-written trees, and the untouched `tree_instances:` section regenerates the instances on
  every load — no duplication. An instance's geometry is edited by editing the template (in the Trees
  dock) and the declaration's `name`/`sheet` (Tools → **Instances…**, see the GUI docs).
- `name` must be unique across ALL trees (a clash — with a hand-written tree or another instance — is
  the usual duplicate-name fatal at load).

---

## `sheet_templates:` — declaring a group once, instantiating per sheet

`Channel_0/1/2` are three instances of the same reused hierarchical sheet pair — the Cluster/Role
tags are IDENTICAL across instances (fieldstool tags the shared sheet FILE, not a specific
instance), only `sheet:`/`anchor_sheet:` tells two physical copies apart. Instead of copy-pasting
the same `clone_placements:`/`coordinate_placements:` entries N times — N independent lists that
silently drift the moment Channel_0's design changes again — declare them once and let
`load_config()` expand them once per sheet:

```yaml
sheet_templates:
  channel:
    sheets: [Channel_0, Channel_1, Channel_2]
    coordinate_placements:
    - cluster: OP_AMP
      role: OP_AMP
      # name: is auto-generated per sheet for multi-sheet templates (see Identity below)
      sheet: self
      x_mm: 9.0
      y_mm: 0.0
      rotation_deg: 270.0
      anchor_role: AD_DAC
      anchor_sheet: self
    clone_placements:
    - name: PIF_AVDD          # Cluster tag — never touched by expansion
      cell: dac_pi_filter
      sheet: self
      xy: [2.0, 1.0]
      rotation_deg: 90.0
      anchor_role: AD_DAC
      anchor_pad: '18'
      anchor_cluster: AD_DAC
      anchor_sheet: self
      params:
        FB_PI_FLT: /$SHEET/DAC/+3V3_AVDD
```

Expansion runs inside `load_config()`, right after `include:` resolution and before any per-entry
loader — by the time `_load_clone_placement`/`_load_coordinate_placement` and the duplicate-name
checks see the data, generated entries are indistinguishable from hand-written ones (downstream
loaders never need to know this mechanism exists).

**Reserved tokens, ONLY inside `sheet_templates:` blocks (nowhere else in the schema):**
- `sheet: self` / `anchor_sheet: self` → the literal generated sheet name (`Channel_0`/`Channel_1`/...).
  Deliberately NOT auto-filling every omitted `anchor_sheet:` — an `anchor_role: FPGA` entry must
  NOT gain an `anchor_sheet:` it never asked for (FPGA is a single, non-sheet-scoped instance); only
  an explicit `self` is substituted, an absent field stays absent.
- `$SHEET` inside string values (`params:`/`nets:`/`net_overrides:`) → the same substitution, for
  hierarchical net paths like `/$SHEET/DAC/+3V3_AVDD`.

**Identity** (`placer_name:` for `clone_placements`, `name:` for `coordinate_placements` — NOT
`clone_placements'` own `name:`, which is the Cluster tag and is never touched), split by
`len(sheets)`:
- `len(sheets) == 1` → identity is taken LITERALLY from the template (or the usual default,
  `{cluster}/{role}` for coordinate_placements) with NO prefix — this is what lets a single-sheet
  regression stay byte-identical.
- `len(sheets) >= 2` → identity is ALWAYS generated as `{sheet}_{base}` (base = the explicitly-set
  value, or the same default), OVERWRITING the template's own — per-sheet uniqueness is
  structurally guaranteed, since the duplicate-name check keys off the effective name which does
  NOT incorporate `sheet`.

`sheet_templates:` is a dict section, so it merges through `include:` exactly like `cells:`/`points:`
(fatal on a duplicate template name across files — see `include:` below).

---

## `thermal_via_arrays:` — thermal via grids

```yaml
# boards/3ch-awg-tia/profiles/fpga.sexp
thermal_via_arrays:
  - name: fpga_thermal
    retired: false
    skip: false
    anchor_role: FPGA
    pad: '145'
    net: GND
    rows: 4
    cols: 4
    margin_mm: 0.5
    pattern: grid
    drill_mm: 0.3
    diameter_mm: 0.5
```

A real list (2026-08-02, generalized once a second IC needing thermal vias — AD9707, one per channel —
showed up) — same shape as `chains:`/`clone_placements:`: any number of entries, each independently
named/anchored/retired/skipped, and each can live in a different file via `include:` (`thermal_via_arrays`
is a merged list section, same as `chains`/`clone_placements`). `name:` is **required** on every entry (used
for `--only` and the registry identity `f"thermal:{name}"`) and must be **unique across the whole list**
(fatal at load otherwise — `--only` couldn't tell same-named entries apart).

| Field | Meaning |
|---|---|
| `anchor_ref` / `anchor_role`(+`anchor_sheet`+`anchor_cluster`) / `anchor_point` | Exactly one — the IC whose thermal pad gets the grid. |
| `pad` | Which pad of the anchor is the thermal pad (grid is centred on it). |
| `net` | Default `GND`. |
| `rows`/`cols` | Grid dimensions. |
| `margin_mm` | Clearance from the pad edge to the first via. |
| `pattern` | `grid` or `staggered`. |
| `drill_mm`/`diameter_mm` | Via dimensions. |
| `retired` | Default `false`. Same "does not exist" meaning as elsewhere. |
| `skip` | Default `false`. Same "leave alone this run" meaning as elsewhere. |
| `comment` | Optional. Free-form note shown in the GUI — a plain schema field, not a YAML comment. |

**Deprecated, fatal on load:** the old singular `thermal_via_array:` (a mapping, not a list — rename to
`thermal_via_arrays:` and wrap the block in a YAML list), `target_ref` (renamed to `anchor_ref`), the old
`enabled:` (renamed and inverted to `retired:` — `enabled: true` ≠ `retired: true`, don't do a literal
find-and-replace on an old config, re-check the intended sense).

---

## `scheme_lists:` — a recorded snapshot of a real board region

The answer to "I've already routed this region and want to replay it (or its
twin) later": a Scheme List records a real, already-routed region as an
explicit list of literal refdes + the copper that reaches their pads (tracks
and vias on ALL copper layers of the stack — F.Cu/In1.Cu..In30.Cu/B.Cu).
Unlike a Cell (abstract Role), identity here is the literal `ref`, so a record
is a snapshot, not a template. Records are captured from the live board (Tools
→ Scheme Lists → Record...) and physically live in an included
`scheme_lists.json` (the section itself is format-agnostic — see `include:`).

```yaml
# scheme_lists.json — included via include: (the .sexp syntax is identical)
scheme_lists:
  - name: amp_avdd            # identity — the --only and Entity.scheme_list key
    anchor_ref: R1            # one of the components below: offset origin + clone anchor
    # anchor_pad: '1'         # optional — anchor on a pad's centre instead
    # anchor_rotation_deg: 0.0  # anchor's ABSOLUTE angle at capture — how the
    #                            # raw offsets/rotations below must be rotated
    #                            # back onto a target node (P4 apply); default 0.0
    source_sheet: Channel_0   # the sheet the record was captured on (top-level name)
    components:               # literal refs, board-frame offsets from the anchor
      - ref: R1
        offset_along_mm: 0.0
        offset_across_mm: 0.0
        rotation_deg: 0.0
      - ref: C1
        offset_along_mm: 10.0
        offset_across_mm: 0.0
        rotation_deg: 90.0
    vias:                     # literal nets; offsets in the anchor frame
      - offset_along_mm: 10.0
        offset_across_mm: 0.0
        drill_mm: 0.3
        diameter_mm: 0.6
        net: "/Channel_0/AMP/+5V"
    tracks:                   # literal nets + literal copper layer (a STRING)
      - start_along_mm: 0.0
        start_across_mm: 0.0
        end_along_mm: 10.0
        end_across_mm: 0.0
        width_mm: 0.25
        layer: F.Cu           # any copper layer: F.Cu/In1.Cu/.../In30.Cu/B.Cu
        net: "/Channel_0/AMP/+5V"
    boundary_nets:            # copper that only touched EXCLUDED footprints
      - net: "/Channel_0/AMP/GND"
        action: exclude       # v1: ONLY exclude (drop the whole component, warning)
        external_ref: J1      # diagnostics: which outside footprint dragged this copper
```

Rules: one real `ref` may be recorded in at most ONE Scheme List (fatal at
load — cloning one record must not move a component another expects);
`anchor_ref` must be one of the record's own `components[].ref`. Capture runs
the SAME connectivity-closure filter as Cell extraction (only copper reaching
a recorded ref's pad is kept), pre-filtered to the refs' bbox + 1 mm; the
dropped excluded-material copper becomes `boundary_nets` — one `action` per
NET, so Reread stays deterministic. Track `layer` is a free string covering
the full copper stack. Component/via/track offsets and rotations are stored RAW
(board frame) and `anchor_rotation_deg` records the anchor's absolute angle at
capture — Apply/Redraw (P4) compensates with it instead of scanning
`components[]`. Cloning a record onto another (twin) sheet goes through
an Entity with `scheme_list:` (not `cell:`) — the stored-record format above
is what capture/Reread write.

---

## `net_traces:` — one net's copper, following one anchor pad

The answer to "I hand-routed the FPGA↔DAC bus and don't want to re-route it
every time I move the FPGA": captures ALL of one net's live copper (tracks +
vias) as LOCAL offsets from an anchor pad, then re-resolves it LIVE on every
`apply`/Redraw from the anchor's *current* position — move the anchor in KiCad,
run `apply --only=<net>`, and the whole net trace follows it (old copper is
deleted by the registry, new one created at the new position). One record =
ONE net (no net lists inside a record — lists live in only one place in this
project, the spokes).

```yaml
# boards/3ch-awg-tia/profiles/net_traces.sexp  (include:'d into the config)
net_traces:
  - net: DAC_DB0            # the net's name — also the --only identity
    anchor_role: FPGA       # anchor footprint by Role (whole-board search)
    # anchor_sheet: ...     # optional — narrow the Role search by sheet
    # anchor_cluster: ...   # optional — narrow by Cluster (prefix match)
    # anchor_pad: '42'      # optional — anchor on this pad's centre, not the fp centre
    tracks:
      - start_along_mm: 1.0
        start_across_mm: 2.0
        end_along_mm: 3.0
        end_across_mm: 4.0
        width_mm: 0.2
        net: DAC_DB0
        layer: F.Cu
    vias:
      - offset_along_mm: 5.0
        offset_across_mm: 6.0
        net: DAC_DB0
        drill_mm: 0.3
        diameter_mm: 0.6
    # retired: true   # "does not exist on the board right now" (registry protection dropped)
    # skip: true      # "skip just this run" (registry protection kept)
```

- **`net`** (required) — the network name. Unique per record (fatal at load if
  two records share a net). Local hierarchical nets keep their full
  `/Channel_0/...` form.
- **`anchor_role`** (required) — the Role field of the anchor footprint,
  resolved over the WHOLE live board (never the mouse selection) at BOTH
  extract time (origin) and apply time (anchor) — the same
  `resolve_footprint_by_role` search Chain/ClonePlacement use. Optional
  `anchor_sheet`/`anchor_cluster` narrow that role's ambiguity, optional
  `anchor_pad` moves the anchor point from the footprint centre to a specific
  pad's centre.
- **`tracks`/`vias`** — the copper as local (along/across) offsets from the
  anchor point, the exact `TemplateTrack`/`TemplateVia` shape `cells:` use.
  The net is ALWAYS written explicitly on each element (there is no enclosing
  Chain to inherit a net from).
- **`retired`/`skip`** — the same convention as every other section.
- **`comment`** — optional free-form note shown in the GUI (a plain schema
  field, not a YAML comment).
- **`--only=<net>`** selects exactly one record for a Redraw; the registry
  gives idempotency (a repeat run with an unmoved anchor creates 0 new items).

Extraction is a CLI command, not the mouse-selection `extract`:
`kicadstamp_cli.py extract-net --net DAC_DB0 --anchor-role FPGA [--anchor-pad 42]
--output <config.sexp>` appends/replaces the record under `net_traces:`.

**Design note (see `techdocs/handoff/deepseek/plan_2026_08_21_net_traces.md`):**
deliberately NOT a `Cell`+`ClonePlacement` pair — a net trace is single-instance
by design (no reuse at multiple anchors), so the usual two-layer indirection
would be pure overhead. Extract-time origin and apply-time anchor are the SAME
field set, hence one flat record.

---

## `points:` — named, reusable anchors

```yaml
# boards/3ch-awg-tia/profiles/points.sexp
points:
  p3v3_ldo_origin:
    anchor_role: C_OUT_BYPASS
    anchor_cluster: In_Pi_Filter_Pos
    anchor_pad: '1'
```

Then referenced by name from `Chain`/`ClonePlacement`/`ThermalViaArrayConfig`:

```yaml
clone_placements:
  - name: 3v3_ldo
    cell: p3v3_ldo
    anchor_point: p3v3_ldo_origin
    xy: [-50.0, 35.0]
```

A `Point` resolves once, is cached, and everything that references it by `anchor_point:` gets the
same resolved position — added 2026-07-31 as a real node in the placement dependency graph (not text
substitution), so ordering/freshness across a run is handled automatically, the same way any other
anchor dependency is.

| Field | Meaning |
|---|---|
| `name` | The key under `points:` — not a separate field, just how it's referenced. |
| `anchor_ref` / `anchor_role`(+`anchor_sheet`+`anchor_cluster`) **or** `anchor_pad` | Live-board anchor, same fields/resolution as `Chain`/`ClonePlacement`. |
| `anchor_point` | Chain to another, already-defined point by name — points can reference points (a cycle is caught by the same graph algorithm that catches any other anchor cycle). |
| `xy` | A literal, absolute board coordinate — no live anchor at all. **(0, 0) here is the drawing sheet's corner, not any physical board reference** — see `anchor_origin` below for that. |
| `anchor_origin` | `'grid'` (Place > Set Grid Origin, visual only — no exported file uses it) or `'drill'` (Place > Drill/Place Origin, the auxiliary axis — drill/position files are always relative to it, Gerbers optionally via their own plot-dialog option). Read LIVE via kipy, not a config literal. |
| `shift_x_mm`/`shift_y_mm` | Board-absolute mm shift layered on top of `anchor_ref`/`anchor_role`/`anchor_point`/`anchor_origin` (not on `xy:` — fatal if both are set, just edit the literal coordinate instead). |
| `comment` | Optional. Free-form note shown in the GUI — a plain schema field, not a YAML comment. |

Exactly one of `{anchor_ref or anchor_role, anchor_point, xy, anchor_origin}` must be the base —
fatal at load otherwise. `Chain`/`ThermalViaArrayConfig`'s own `anchor_point:` requires the referenced
point to resolve to an actual footprint (no shift, not `xy:`-literal, not `anchor_origin`-based, and
not chained through one that has any of those) — they need to look up a specific pad by number from
it; `ClonePlacement` only ever needs a coordinate, so any Point works there, `anchor_origin` included.

---

## `include:` — splitting a profile across files

```yaml
# boards/3ch-awg-tia/profiles/power.sexp
include:
  - points.sexp
  - power_extracts.sexp
  - pn5v_filters.sexp
  - p3v3_ldo.sexp
```

Each entry is either a bare path string, or `{path: <str>, enabled: <bool>}` to switch a whole
included file off without deleting or commenting it out.

- **List sections** (`chains`, `clone_placements`, `thermal_via_arrays`) — concatenated: this file's own
  entries first, then each included file's, in listed order. (Real placement order at `apply` time is
  decided separately, by actual anchor dependencies, not YAML order — see [docs/placement.md](placement.md).)
- **Dict sections** (`cells`, `points`, `extract_profiles`, `clone_profiles`) — merged key-by-key,
  **fatal** on a key defined in two different files — included files are meant to be genuinely
  independent subsystems, so a repeated name is far more likely a copy-paste mistake than an
  intentional override.
- **Any other top-level key** (`layer:`, `schematic_dir:`, `registry_path:`, …)
  inside an *included* (non-root) file has no defined multi-file merge chain and is a **fatal** error —
  move it to the root config instead. (This used to be silently dropped — a real, repeatedly-hit bug
  class on `boards/3ch-awg-tia`, now caught at load time.)
- Cycles and diamond-includes (the same file reachable twice) are both fatal.

`include:` is general-purpose and used by both `load_config()` (`chains`/`clone_placements`/
`thermal_via_arrays`/`cells`/`points`) and the CLI's own profile loader (`extract_profiles`/
`clone_profiles`), so one subsystem file can carry a mix of everything it needs — this is also how
external `Cell` files work now: list the file under `include:` and wrap its content in a `cells:` key
(`cells_file:`/`cell_files:`, a separate, differently-shaped mechanism, were folded into `include:` on
2026-08-02 — one way to split ANY section across files instead of two incompatible ones).

---

## `extract_profiles:` / `clone_profiles:` — saved `extract`/`clone-extract` arguments

Not part of `Config`/`load_config()` — a separate, CLI-only mechanism (`kicadstamp_cli.py extract
--profiles ... --profile ...`) for saving repeated `extract`/`clone-extract` invocations as named YAML
entries instead of retyping long flag lists. See [docs/commands.md](commands.md) for the full
CLI-level walkthrough; the syntax:

```yaml
# boards/3ch-awg-tia/profiles/power_extracts.sexp
extract_profiles:
  p5v_pi_filter:
    name: 5v_pi_filter
    output: boards/3ch-awg-tia/profiles/templates/power_pi_filters.sexp
    params:
      PWR_IN: '+5V_DIRTY'
      PWR_OUT: '+5V'
    net_template:
      '+5V_DIRTY': '{PWR_IN}'
      '+5V': '{PWR_OUT}'
```

`extract_profiles:` entries accept: `name`, `output`, `params`, `net_template`, `net_template_role`,
`rule_nets`, `origin_by_via_net`, `origin_by_component_role`, `origin_by_component_pad`,
`origin_by_component_cluster`, `origin_by_component_sheet` — unknown keys
are a fatal error (a typo'd key, e.g. `origin-by-via-net` with the wrong separator, used to be silently
ignored). `clone_profiles:` (for `clone-extract`) accepts: `net`, `pcb`, `channel`, `output`.

`params`/`net_template`/`net_template_role`/`rule_nets` are **OPTIONAL overrides**
(Phase 1/2, 2026-08-28): via/track nets resolve from roles (`net_from_role`),
bridging roles auto-derive their `net_template`, and `{param}` channel patterns
are auto-discovered — these keys are only needed for rare manual exceptions
(soft-deprecated, kept for backward compatibility).

`rule_nets:` (2026-08-05, `--rule-net LITERAL`, repeatable) — a list of literal net names to write as
`net: null` on any matching via/track instead of the literal (or an alias) — see the `vias:` note
above on what `null` means there. Only useful for a cell meant to be placed via **`chains:`**/
ManualSpoke on more than one `Chain` with a DIFFERENT net each (e.g. a decoupling-cap-pair cell reused
once per power rail) — `net_template`/`params` (`{PLACEHOLDER}`) is the mechanism for the OTHER case,
reuse across `clone_placements:`, and does nothing for `chains:`/ManualSpoke (`ManualSpoke` has no
`params:` field to resolve a template against at all). Fatal if the same net is in both `rule_nets:`
and `params`/`net_template` — pick one per net.

`output:` can be set once at the file's own root as a fallback for every profile inside it, if they
all write to the same cells file — a profile that needs a different one still overrides it directly.

---

## See also

- [docs/commands.md](commands.md) — the CLI commands (`apply`/`extract`/`undo`/`clone-extract`) that
  consume everything documented here.
- [docs/python.md](python.md) — building the same `Chain`/`ClonePlacement`/`Cell` objects from Python
  instead of hand-writing YAML.
- [docs/architect.md](architect.md) — the module architecture (`config/`, `placement/`, `geometry/`)
  behind this schema.
- [docs/placement.md](placement.md) — what actually happens with this config at `apply` time
  (dependency ordering, the registry, collision handling).
