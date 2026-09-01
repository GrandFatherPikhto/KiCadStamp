# `kicadstamp/geometry` – Geometry Utilities

## Purpose

The modules in the `geometry/` directory provide low‑level geometric functions and classes used for calculating positions of components, vias, and tracks; building keepout areas; searching for free space; predicting pad positions after movement/rotation; generating thermal via grids; and transforming local template coordinates to global board coordinates.

These modules are **independent** of KiCad and the adapter – they operate solely on coordinates and vectors, making them easy to test and reuse. They are primarily used in `placement/services/manual_position_calculator.py`, `placement/services/clone_position_calculator.py`, `via_planner.py`, and other placement modules.

---

## Structure

```
geometry/
├── __init__.py             # Public API export
├── keepout.py              # Keepout rectangles and free‑space search
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

**Key Classes and Functions:**

| Name | Description |
|------|-------------|
| `Rect` | AABB rectangle. Constructor: `Rect(min_x, min_y, max_x, max_y)`. Methods: `from_bbox(bbox, clearance)` – creates a rectangle from a `Box2` with clearance; `from_circle(center, radius)` – approximates a circle as a square; `intersects(other)` – checks intersection. |
| `point_is_clear(point, via_radius, keepout)` | Checks whether the point is free (the circle of radius `via_radius` does not intersect any keepout rectangle). |
| `build_keepout(bboxes, clearance_mm, mm_per_unit)` | Takes a list of `Box2` (from the adapter) and builds a list of `Rect` with clearance `clearance_mm` on each side. Skips `None` elements. |
| `find_free_point(ideal, keepout, via_radius, preferred_direction=None, step_mm=0.1, max_radius_mm=3.0, n_directions=8)` | Searches for the nearest free point around `ideal` in expanding rings. On each ring, it first tries `preferred_direction` (if given), then `n_directions` points evenly around the circle. Returns a `Vector2` or `None`. |
| `find_free_point_along_line(ideal, keepout, via_radius, line_direction, step_mm=0.1, max_radius_mm=3.0)` | Searches for a free point along a straight line through `ideal` with direction `line_direction` (unit vector). Checks `ideal`, then steps out in both directions. |

**Used in:** `via_planner.py` **only** for placing thermal vias (the only case requiring automatic search). For all other vias (spoke‑level and component‑level), search is not used because they are placed strictly by template coordinates.

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
- Supports **mirroring** (`mirror=True`) – the whole construction is reflected along the X axis, layers are inverted, and component angles are recalculated as `180°−φ`.
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
| `keepout.py` | `via_planner.py` | Building keepout and searching for free spots for thermal vias. |
| `thermal_grid.py` | `via_planner.py` | Generating thermal via positions. |
| `spoke_layout.py` | `manual_position_calculator.py` | Template transformation for manual spokes (vias and tracks). |
| `clone_geometry.py` | `clone_position_calculator.py` | Template transformation for cloned placements (with tracks and mirror). |
| `pad_projection.py` | Diagnostic scripts | Checking the pad mirroring convention. |

---

## Usage Examples

### 1. Building keepout and finding a free point (for thermal vias)

```python
from kicadstamp.geometry.keepout import build_keepout, find_free_point
from kicadstamp.domain.geometry import Vector2

# Get pad bounding boxes via the adapter
bboxes = adapter.get_bounding_boxes(pads)
keepout = build_keepout(bboxes, clearance_mm=0.2)

ideal = Vector2.from_xy(10_000_000, 20_000_000)
via_radius = 0.3 * MM  # 0.3 mm in nanometres

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
# Component angles are recalculated as 180°−φ, layers are inverted
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
