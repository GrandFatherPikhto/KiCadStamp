# `kicadstamp/geometry` – Geometry Utilities

## Purpose

The modules in the `geometry/` directory provide low‑level geometric functions and classes used for calculating positions of components, vias, and tracks; deriving a pad's own copper area (rotation‑invariant); building keepout areas; searching for free space; predicting pad positions after movement/rotation; generating thermal via grids; and transforming local template coordinates to global board coordinates.

These modules are **independent** of KiCad and the adapter – they operate solely on coordinates and vectors, making them easy to test and reuse. They are primarily used in `placement/services/manual_position_calculator.py`, `placement/services/clone_position_calculator.py`, `via_planner.py`, and other placement modules.

---

## Structure

```
geometry/
├── __init__.py             # Public API export
├── keepout.py              # Keepout obstacles (Rect + a pad's own area) and free‑space search
├── pad_area.py             # The pad's own copper area, in the pad's axes
├── pad_projection.py       # Pad position prediction (only for diagnostics)
├── spoke_layout.py         # Template transformation for ManualSpoke (vias and tracks)
├── clone_geometry.py       # Template transformation for ClonePlacement (with tracks and mirror)
└── thermal_grid.py         # Thermal via grid generation
```

---

## Modules and Their Functions

### `keepout.py` – Keepout Areas and Free‑Space Search

**Purpose:**
Defines the `Rect` (axis‑aligned bounding box) class and provides functions for building keepout areas from bounding boxes, checking point clearance (accounting for via radius), and searching for free space around an ideal position (spiral or along a line).

**Obstacles are MIXED** (since 2026‑09‑15): a `Rect` (board axes — KiCad's bounding box, or a via planned earlier in the same run) and a `PadArea` (`pad_area.py` — a pad's own copper in the PAD's axes) both answer ONE predicate, `blocks_via(point, radius)`. A keepout built from the pads' own areas therefore does not inherit the rotation defects of the bounding box, while `Rect` keeps its historical behaviour bit for bit.

**Key Classes and Functions:**

| Name | Description |
|------|-------------|
| `Rect` | AABB rectangle. Constructor: `Rect(min_x, min_y, max_x, max_y)`. Methods: `from_bbox(bbox, clearance)` – creates a rectangle from a `Box2` with clearance; `from_circle(center, radius)` – approximates a circle as a square; `intersects(other)` – checks intersection; `blocks_via(point, radius)` – does a via of that radius touch me (the historical square‑via test, unchanged). |
| `point_is_clear(point, via_radius, keepout)` | Checks whether the point is free — nothing in `keepout` answers `blocks_via`. `keepout` is a mixed list of `Rect` and `PadArea`. |
| `build_keepout(bboxes, clearance_mm, mm_per_unit)` | Takes a list of `Box2` (from the adapter) and builds a list of `Rect` with clearance `clearance_mm` on each side. Skips `None` elements. Kept for callers that only have boxes; a pad that can build its own area is better served by `pad_area.pad_area_of`. |
| `find_free_point(ideal, keepout, via_radius, preferred_direction=None, step_mm=0.1, max_radius_mm=3.0, n_directions=8)` | Searches for the nearest free point around `ideal` in expanding rings. On each ring, it first tries `preferred_direction` (if given), then `n_directions` points evenly around the circle. Returns a `Vector2` or `None`. Takes the same mixed obstacle list. |
| `find_free_point_along_line(ideal, keepout, via_radius, line_direction, step_mm=0.1, max_radius_mm=3.0)` | Searches for a free point along a straight line through `ideal` with direction `line_direction` (unit vector). Checks `ideal`, then steps out in both directions. Same mixed obstacle list. |

**Used in:** `via_planner.py` **only** for placing thermal vias (the only case requiring automatic search). For all other vias (spoke‑level and component‑level), search is not used because they are placed strictly by template coordinates.

---

### `pad_area.py` – The Pad's Own Copper Area

**Purpose:**
Turns the fields of an ALREADY‑READ pad into the pad's own copper area — a rectangle in the **pad's own axes**. Keepout, the Extract selection closure and the inter‑node copper attachment all need "is this point on that pad", and KiCad's axis‑aligned bounding box is a wrong answer to it for a rotated pad.

Two measured reasons (2026‑09‑15, KiCad 10.0.6 + kipy 10.0.1, `profiles/3ch-awg-tia-v103`, IC2 at 315°): the box is an axis‑aligned AABB around a **rotated** rectangle, so the 0.300 × 0.850 mm signal pad reads 0.813 × 0.813 mm — 2.6× its copper area — and the same copper can read "occupied" at 45° and "free" at 0°; and for a footprint rotated by an angle that is not a multiple of 90° that box was measured to come back shifted by one and the same offset for every pad of the footprint (1.724 mm on Denis's session — the measurement the "may be shifted" warning is about; an IPC‑applied rotation on the Linux test board did not reproduce it, and the probe prints the number per pad). Nothing in this module reads the board: every input is a field of a pad that has already been read (`position`, `size`, `shape`, `offset`, `trapezoid_delta`, `angle_rad`), so building the area costs no IPC call at all.

**Key Functions:**

| Name | Description |
|------|-------------|
| `PadArea` | Frozen value: `center` (nm — `position` + the pad's own `offset` rotated by the pad angle), `half_w`, `half_h` (nm) and `angle_deg` (the pad's ABSOLUTE angle, the footprint's rotation already included). Methods: `contains(point, margin=0)` — the point is transformed into the pad's axes and compared there; `blocks_via(point, radius)` — the same conservative "square via corner" test `Rect` always did, but applied in the pad's axes, so it is invariant under the footprint's rotation; `inflated(margin)` — a grown copy (the clearance stays "per side", exactly as `Rect.from_bbox` had it); `bounds()` — the axis‑aligned board‑frame box, for consumers that genuinely need an AABB. |
| `pad_area_of(pad)` | The pad's own area, or `None` when the caller must fall back to KiCad's box: a `custom`/`unknown` shape, or a padstack without a usable `size`. `rect`, `roundrect`, `chamfered`, `oval` and `circle` are bounded by `size`; `trapezoid` grows both half‑sizes by `max(|delta.x|, |delta.y|)`, deliberately conservative under either reading of the delta axes. A pad double that carries no `shape` attribute at all reads as a plain rectangle with no offset. |
| `pad_angle_deg(pad)` | The pad's absolute angle in degrees — the project keeps radians on the DTO and degrees at the rotation primitive, so the conversion lives here. |
| `pad_shape_center(pad)` | The centre of the pad SHAPE — what the thermal via grid must be centred on, never the hole. |
| `warn_bbox_fallback(logger, ref, pad)` | Logs (naming the ref and the pad number) that a pad without an area of its own is about to be measured by KiCad's box — and only when the pad angle is NOT a multiple of 90°, the one case where that box was measured to be shifted. |

**Used in:** `via_planner._build_keepout` (the keepout obstacles), `thermal_grid.compute_thermal_via_grid` (the grid centre) and `template_selection._inflated_boxes` (the seam the Extract closure and `internode_copper.find_copper_units` share).

---

### `pad_projection.py` – Pad Position Prediction

**Purpose:**  
Predicts the absolute position of a specific pad after moving and rotating the component, accounting for possible flipping (mirroring). The logic is centralised here to avoid duplication across the project.

**Key Functions:**

| Name | Description |
|------|-------------|
| `local_pad_offset(fp, pad)` | Returns the pad offset relative to the footprint centre in the **unrotated** local coordinate system (constant geometry). |
| `predict_pad_position(fp, pad, dest, angle_deg, needs_flip)` | Predicts the absolute pad position after moving the footprint to `dest` and rotating by `angle_deg`, with local X mirroring if `needs_flip=True`. |

**Used in:**  
In the current KiCadStamp version, this function **is not used** in the main code, because all vias (including GND vias) are computed solely from template geometry without accessing the live board. However, it is kept for diagnostic scripts (e.g., `test_pad_mirror_convention.py`) and for potential future use (e.g., manual position adjustment or collision checking).

---

### `spoke_layout.py` – Transformation for ManualSpoke

**Purpose:**  
Transforms template local coordinates (`along`, `across`) to absolute board coordinates for `ManualSpoke` (pad‑based placement) with the spoke’s `(shift_x, shift_y)` and `rotation_deg`. It also computes the final component rotation and generates all vias (at both the spoke and component levels) as `ResolvedVia` with absolute coordinates and nets (if `net` is omitted, `rule.net` is used).

**Important:** `ManualSpoke` also generates tracks (spoke‑level `TemplateTrack` entries), the same way `ClonePlacement` does. A track with `net = None` inherits `chain.net` – the same convention as `TemplateVia` – which is what lets one template (e.g. `cap_pair_standard`) be reused across chains with different nets without hardcoding a net per chain.

**Key Classes and Functions:**

| Name | Description |
|------|-------------|
| `rotate_local_offset(along_mm, across_mm, rotation_deg)` | Rotates the local vector `(along, across)` by the given angle about the origin (no translation). |
| `local_to_absolute(origin, along_mm, across_mm, rotation_deg)` | Transforms a local vector to an absolute position relative to `origin`, applying rotation. |
| `ResolvedVia` | A fully resolved via: absolute position, net, drill and diameter parameters. |
| `ResolvedTrack` | A fully resolved track segment: start and end points (absolute), width, net, absolute layer. Used by both `ManualSpoke` and `ClonePlacement`. |
| `ComponentLayout` | Describes the placement of one component: ref, role, position, angle, list of vias. |
| `SpokeLayout` | Describes all elements of a spoke: origin, vias (spoke level), components, tracks. |
| `apply_spoke_geometry(pad_position, spoke, template, rule_net, role_to_ref)` | Main function. Takes the FPGA pad position, spoke data (`ManualSpoke`), template (`SpokeTemplate`), rule net, and role→ref mapping. Returns a `SpokeLayout` with absolute coordinates for all elements, including resolved tracks. |

**Used in:** `manual_position_calculator.py` for computing component and via positions.

---

### `clone_geometry.py` – Transformation for ClonePlacement

**Purpose:**  
Analogous to `spoke_layout.py`, but for `ClonePlacement` (cloned placements). Differences:
- `origin` can be absolute `(origin_x_mm, origin_y_mm)` or a shift from an anchor (if `anchor_ref` or `anchor_role` is set).
- `net` of each via and track is resolved via `net_resolution.resolve_net()` using `params` and `net_overrides` – there is no default `rule_net`. If `net` is missing, a fatal error is raised.
- Supports binding to an anchor component/pad via `anchor_ref`/`anchor_role` and `anchor_pad`.
- Supports **mirroring** (`mirror=True`) – the whole construction is reflected along the X axis and component angles are recalculated as `180°−φ`; only the outer pair of copper layers swaps (`F.Cu` ↔ `B.Cu`), while copper on an **inner** layer keeps its own layer (`utils/layers.mirror_layer` — an inner layer is a plane of the BOARD, not a side of the construction).
- **Supports tracks** – they are transformed in the same way as vias and components, inheriting the layer from the template (if not explicitly set).

**Key Classes and Functions:**

| Name | Description |
|------|-------------|
| `_resolve_clone_via(origin, via, rotation_deg, clone, mirror)` | Converts a `TemplateVia` to `ResolvedVia` using `resolve_net` for the net. Respects `mirror`. |
| `_resolve_clone_track(origin, track, rotation_deg, clone, tpl_layer, mirror)` | Converts a `TemplateTrack` to `ResolvedTrack` using `resolve_net`. Respects `mirror` and layer inheritance. |
| `_mirror_x(origin, p)` | X‑mirrors a point relative to the vertical axis through `origin`. |
| `apply_clone_geometry(clone, template, role_to_ref, anchor_position=None, mirror=False)` | Main function. Takes a `ClonePlacement`, template, role→ref mapping, and an optional anchor position. Returns a `SpokeLayout` with absolute coordinates for all elements (including tracks). |

**Used in:** `clone_position_calculator.py` for computing component, via, and track positions for cloned placements.

---

### `thermal_grid.py` – Thermal Via Grid Generation

**Purpose:**
Computes absolute coordinates for an array of thermal vias under a thermal pad (e.g., `IC1`). Accounts for pad size, edge margins, row/column counts, and staggered patterns.

The local grid (rows/columns/stagger/margin) is laid out in the **pad's own axes** and then turned by the pad's ABSOLUTE angle with the project's own rotation primitive — `rotate_local_offset` (`spoke_layout.py`), the same y‑down formula as `cell_frame.rotate_ydown_mm`. The inline `x·cos − y·sin, x·sin + y·cos` this replaced turned the array the OTHER way round, which is invisible while the grid maps onto itself (an unstaggered grid at 0/90/180/270°, a square one also at 45°) and visible for a staggered grid at 90°/270° or a rectangular one at 45°. The grid is centred on the centre of the pad SHAPE (`pad_area.pad_shape_center`) — `position` plus the pad's own offset, rotated — never on the hole: an offset pad has its copper elsewhere. A pad with no position at all is a fatal, not a grid around the origin.

**Key Functions:**

| Name | Description |
|------|-------------|
| `get_pad_size(pad)` | Returns the copper layer size `(width, height)` of the pad in nanometres. Raises `GeometryError` if copper layers are missing. |
| `compute_thermal_via_grid(pad, rows, cols, margin_mm, stagger=False)` | Generates a list of absolute positions for vias evenly distributed inside the pad with the given `margin_mm`. The `stagger` parameter enables a staggered pattern. Returns a list of `Vector2`. |

**Used in:** `via_planner.py` for planning thermal vias.

---

## Relationships with Other Modules

| Module | Used in | Purpose |
|--------|---------|---------|
| `keepout.py` | `via_planner.py` | Building keepout (from the pads' own areas and from KiCad's boxes) and searching for free spots for thermal vias. |
| `pad_area.py` | `keepout.py`, `thermal_grid.py`, `template_selection.py` | The pad's own copper area, in the pad's axes — no board access. |
| `thermal_grid.py` | `via_planner.py` | Generating thermal via positions. |
| `spoke_layout.py` | `manual_position_calculator.py` | Template transformation for manual spokes (vias and tracks). |
| `clone_geometry.py` | `clone_position_calculator.py` | Template transformation for cloned placements (with tracks and mirror). |
| `pad_projection.py` | Diagnostic scripts | Checking the pad mirroring convention. |

---

## Usage Examples

### 1. Building keepout and finding a free point (for thermal vias)

```python
from kicadstamp.geometry.keepout import Rect, find_free_point
from kicadstamp.geometry.pad_area import pad_area_of
from kicadstamp.domain.geometry import Vector2

pads = adapter.get_footprint_pads(fp)              # pads already read
clearance = int(0.2 * MM)

# Obstacles: each pad's OWN area where it has one (no IPC at all, and correct
# under any rotation of the footprint), KiCad's box only for the pads without
# one — asked for in ONE batch.
keepout = []
for pad in pads:
    area = pad_area_of(pad)
    if area is not None:
        keepout.append(area.inflated(clearance))
fallback = [p for p in pads if pad_area_of(p) is None]
for bbox in adapter.get_bounding_boxes(fallback):
    if bbox is not None:
        keepout.append(Rect.from_bbox(bbox, clearance))

ideal = Vector2.from_xy(10_000_000, 20_000_000)
via_radius = 0.25 * MM  # 0.25 mm in nanometres

free_point = find_free_point(ideal, keepout, via_radius, preferred_direction=(1, 0))
if free_point is None:
    print("No free position found")
else:
    print(f"Free point: ({free_point.x/MM:.3f}, {free_point.y/MM:.3f}) mm")
```

### 2. Generating a thermal via grid

```python
from kicadstamp.geometry.thermal_grid import compute_thermal_via_grid
from kicadstamp.kicad.adapter import KiCadBoardAdapter

adapter = KiCadBoardAdapter()
adapter.refresh_board()
fp = adapter.get_footprint("IC1")
pad = adapter.get_pad_by_number(fp, "145")

points = compute_thermal_via_grid(pad, rows=4, cols=4, margin_mm=0.5, stagger=False)
for p in points:
    print(f"Via at ({p.x/MM:.3f}, {p.y/MM:.3f}) mm")
```

### 3. Transforming a template for cloning (with tracks and mirror)

```python
from kicadstamp.geometry.clone_geometry import apply_clone_geometry
from kicadstamp.config import load_config

cfg = load_config("config.sexp")
template = cfg.templates["pi_filter_4"]
clone = cfg.clone_placements[0]
role_to_ref = {"PI_FILTER_C1": "C601", "PI_FILTER_FB": "FB601"}

# Without mirroring
layout = apply_clone_geometry(clone, template, role_to_ref, mirror=False)
for via in layout.vias:
    print(f"Via: ({via.position.x/MM:.3f}, {via.position.y/MM:.3f}) mm, net={via.net}")
for track in layout.tracks:
    print(f"Track: ({track.start.x/MM:.3f}, {track.start.y/MM:.3f}) -> "
          f"({track.end.x/MM:.3f}, {track.end.y/MM:.3f}), net={track.net}")

# With mirroring (flips the whole construction to the opposite side)
layout_mirrored = apply_clone_geometry(clone, template, role_to_ref, mirror=True)
# Component angles are recalculated as 180°−φ; F.Cu/B.Cu swap, inner copper keeps its layer
```

---

## Notes

- All coordinates and sizes are expected in **nanometres** unless otherwise noted (fields with `_mm` accept millimetres and are converted internally using `MM = 1_000_000` from `utils/units.py`).
- `find_free_point` and `find_free_point_along_line` are currently **only** used for thermal vias – all other vias are placed strictly by template coordinates without search.
- The `spoke_layout.py` and `clone_geometry.py` modules are completely independent of zones and boundaries – all geometry is defined in the template’s local system, making configuration rotation‑invariant and predictable.
- For `ClonePlacement`, via and track nets **must** be specified (either via `net` in the template or via `nets`/`net_overrides` in the clone), because there is no default `rule_net`. Otherwise, a `ValidationError` is raised.
- The `pad_projection.py` module is kept for diagnostic purposes and potential future extensions, although it is not used in the main flow.
- **Tracks are only supported in `ClonePlacement`.** They are not planned for `ManualSpoke` (this is an architectural decision).

---

## Testing

The geometry modules are covered by unit tests (in `tests/test_*.py`) that verify:
- Correct transformation of local coordinates to absolute.
- Keepout construction and free‑point search.
- Thermal via grid generation.
- Pad position prediction (including empirical verification of the mirroring convention).
- Track transformation in `clone_geometry.py` (rotation, mirroring, layer inheritance).

For manual verification of the pad mirroring convention, the diagnostic script `test_pad_mirror_convention.py` performs an actual flip and rotation of a component in KiCad and compares the predictions against the actual pad position.

---

## License

All geometry modules are distributed under the MIT license, the same as the main project.
