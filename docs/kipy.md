# Using the kicad-python API in KiCadStamp

All official documentation for the KiCad Python bindings (`kicad-python`) is available at: **[https://docs.kicad.org/kicad-python-main/](https://docs.kicad.org/kicad-python-main/)**.

In the `KiCadStamp` project, all calls to KiCad are encapsulated in the `KiCadBoardAdapter` class (`kicad/adapter.py`), and are also used in `undo.py` and `diagnostics`. Below is a complete list of all APIs used, with their status and links to documentation.

---

## 1. Connection and Basic Operations

| Function / Class | Documentation | Status |
| :--- | :--- | :--- |
| `kipy.KiCad` (constructor) | [KiCad — kicad-python](https://docs.kicad.org/kicad-python-main/kicad.html#kipy.KiCad) | Stable |
| `kipy.KiCad.get_board()` | [KiCad.get_board](https://docs.kicad.org/kicad-python-main/kicad.html#kipy.KiCad.get_board) | Stable |
| `kipy.KiCad.run_action()` | [KiCad.run_action](https://docs.kicad.org/kicad-python-main/kicad.html#kipy.KiCad.run_action) | **Unstable** (official warning) |

---

## 2. Board Operations and Transactions

| Function / Class | Documentation | Status |
| :--- | :--- | :--- |
| `kipy.board.Board` | [Board — kicad-python](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board) | Stable |
| `Board.begin_commit()` | [Board.begin_commit](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.begin_commit) | Stable |
| `Board.push_commit()` | [Board.push_commit](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.push_commit) | Stable |
| `Board.drop_commit()` | [Board.drop_commit](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.drop_commit) | Stable |
| `Board.create_items()` | [Board.create_items](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.create_items) | Stable |
| `Board.update_items()` | [Board.update_items](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.update_items) | Stable |
| `Board.remove_items_by_id()` | [Board.remove_items_by_id](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.remove_items_by_id) | Stable |
| `Board.get_item_bounding_box()` | [Board.get_item_bounding_box](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.get_item_bounding_box) | Stable |
| `Board.get_selection()` | [Board.get_selection](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.get_selection) | Stable |
| `Board.add_to_selection()` | [Board.add_to_selection](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.add_to_selection) | Stable |
| `Board.clear_selection()` | [Board.clear_selection](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.clear_selection) | Stable |
| `Board.get_vias()` | [Board.get_vias](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.get_vias) | Stable |
| `Board.get_tracks()` | [Board.get_tracks](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.get_tracks) | **Undocumented** |
| `Board.get_zones()` | [Board.get_zones](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.get_zones) | Stable |
| `Board.get_nets()` | [Board.get_nets](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.get_nets) | Stable |

---

## 3. Geometric Primitives

| Function / Class | Documentation | Status |
| :--- | :--- | :--- |
| `kipy.geometry.Vector2` | [Vector2 — kicad-python](https://docs.kicad.org/kicad-python-main/utilities.html#kipy.geometry.Vector2) | Stable |
| `Vector2.from_xy()` | [Vector2.from_xy](https://docs.kicad.org/kicad-python-main/utilities.html#kipy.geometry.Vector2.from_xy) | Stable |
| `kipy.geometry.Angle` | [Angle — kicad-python](https://docs.kicad.org/kicad-python-main/utilities.html#kipy.geometry.Angle) | Stable |
| `Angle.from_degrees()` | [Angle.from_degrees](https://docs.kicad.org/kicad-python-main/utilities.html#kipy.geometry.Angle.from_degrees) | Stable |
| `Angle.degrees` (property) | [Angle.degrees](https://docs.kicad.org/kicad-python-main/utilities.html#kipy.geometry.Angle.degrees) | Stable |
| `kipy.geometry.Box2` | [Box2 — kicad-python](https://docs.kicad.org/kicad-python-main/utilities.html#kipy.geometry.Box2) | Stable |

---

## 4. Working with Components (Footprints)

| Function / Class | Documentation | Status |
| :--- | :--- | :--- |
| `kipy.board_types.FootprintInstance` | [FootprintInstance — kicad-python](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.FootprintInstance) | Stable |
| `Board.get_footprints()` | [Board.get_footprints](https://docs.kicad.org/kicad-python-main/board.html#kipy.board.Board.get_footprints) | Stable |
| `FootprintInstance.reference_field` | [reference_field](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.FootprintInstance.reference_field) | Stable |
| `FootprintInstance.value_field` | [value_field](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.FootprintInstance.value_field) | Stable |
| `FootprintInstance.position` | [position](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.FootprintInstance.position) | Stable |
| `FootprintInstance.orientation` | [orientation](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.FootprintInstance.orientation) | Stable |
| `FootprintInstance.layer` | [layer](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.FootprintInstance.layer) | Stable |

---

## 5. Working with Pads

| Function / Class | Documentation | Status |
| :--- | :--- | :--- |
| `kipy.board_types.Pad` | [Pad — kicad-python](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Pad) | Stable |
| `Pad.number` | [number](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Pad.number) | Stable |
| `Pad.position` | [position](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Pad.position) | Stable |
| `Pad.net` | [net](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Pad.net) | Stable |
| `Pad.padstack` | [padstack](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Pad.padstack) | Stable |

---

## 6. Working with Nets

| Function / Class | Documentation | Status |
| :--- | :--- | :--- |
| `kipy.board_types.Net` | [Net — kicad-python](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Net) | Stable |
| `Net.name` | [name](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Net.name) | Stable |
| `Net.code` | [code](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Net.code) | **Deprecated** |

---

## 7. Working with Vias

| Function / Class | Documentation | Status |
| :--- | :--- | :--- |
| `kipy.board_types.Via` | [Via — kicad-python](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Via) | Stable |
| `Via.position` | [position](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Via.position) | Stable |
| `Via.net` | [net](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Via.net) | Stable |
| `Via.drill_diameter` | [drill_diameter](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Via.drill_diameter) | Stable |
| `Via.diameter` | [diameter](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Via.diameter) | Stable |

---

## 8. Working with Tracks

| Function / Class | Documentation | Status |
| :--- | :--- | :--- |
| `kipy.board_types.Track` | [Track — kicad-python](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Track) | Stable |
| `Track.start` | [start](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Track.start) | Stable |
| `Track.end` | [end](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Track.end) | Stable |
| `Track.width` | [width](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Track.width) | Stable |
| `Track.net` | [net](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Track.net) | Stable |
| `Track.layer` | [layer](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Track.layer) | Stable |

---

## 9. Working with Zones

| Function / Class | Documentation | Status |
| :--- | :--- | :--- |
| `kipy.board_types.Zone` | [Zone — kicad-python](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Zone) | Stable |
| `Zone.name` | [name](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Zone.name) | Stable |
| `Zone.outline` | [outline](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Zone.outline) | Stable |

---

## 10. Custom Fields (`Field`)

| Component | Documentation | Status |
| :--- | :--- | :--- |
| `kipy.board_types.Field` | [Field — kicad-python](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.Field) | Stable |
| `Field.name` | (not explicitly documented) | Stable |
| `Field.text.value` | (not explicitly documented) | Stable |

---

## 11. Helper Types and Constants

| Component | Documentation | Status |
| :--- | :--- | :--- |
| `kipy.board_types.BoardLayer` | [BoardLayer — kicad-python](https://docs.kicad.org/kicad-python-main/board.html#kipy.board_types.BoardLayer) | Stable |
| `kipy.proto.common.types.KIID` | [KIID — kicad-python](https://docs.kicad.org/kicad-python-main/kicad.html#kipy.proto.common.types.KIID) | Stable |

---

# Use of Unstable, Undocumented, and Deprecated APIs in KiCadStamp

The KiCadStamp project solves real‑world automation problems for placing components, vias, and tracks in KiCad. Some of these tasks **cannot** be accomplished while staying strictly within the stable public API. Therefore, the project consciously uses:

- **unstable** (officially not guaranteed) methods;
- **undocumented** internal fields;
- **deprecated** properties;
- **workarounds** to compensate for non‑intuitive getter/setter behaviour.

All such places are **documented** in the code, and their functionality is **verified** by unit tests, allowing early detection of breakage when updating KiCad or `kipy`.

---

## 1. Unstable APIs

### `kicad.run_action(action)`

**Where used:**  
- `kicad/adapter.py` – `flip_selected()` calls `self._kicad.run_action("pcbnew.InteractiveEdit.flip")`.

**Why:**  
This is the **only** way to perform a true component flip with pad and silkscreen mirroring. Simply changing the `.layer` field does not have the desired effect – the component remains visually unchanged.

**Risk:**  
The `run_action` method and action names are officially marked as **unstable** in the `kipy` documentation. They may change or be removed in any KiCad version without warning.

**Alternative:**  
None – without this, correct flipping is impossible.

**Mitigation:**  
Tests that use flipping (e.g., `test_two_phase_execution.py`) indirectly verify the functionality of this call.

---

## 2. Undocumented Internal Fields

### `FootprintInstance.texts_and_fields` and `FootprintInstance.definition.items`

**Where used:**  
- `kicad/adapter.py` – `get_field_value()` reads `fp.texts_and_fields`, filtering for `Field` objects.
- `kicad/adapter.py` – `get_footprint_pads()` reads `fp.definition.items`, filtering for pads.

**Why:**  
Custom fields (e.g., `Role`) are only accessible via `texts_and_fields`. This is an undocumented approach, but it is widely used in the community.  
Component pads are also only accessible via `definition.items`, as `board.get_pads()` does not contain a back‑reference to the parent footprint.

**Risk:**  
The internal structure of the footprint definition may change, breaking field and pad reading.

**Alternative:**  
For pads – geometric mapping (by coordinates), but it is unreliable in dense layouts. For fields – parsing the `.net` file, but that is slower and requires an external file.

**Mitigation:**  
Unit tests (`test_kicad.py`, `test_full_pipeline_templates.py`) use mocks and verify field reading through `adapter.get_field_value()`.

---

### `Board.get_item_bounding_box()` with a list argument

**Where used:**  
- `kicad/adapter.py` – `get_bounding_boxes()` passes a list of objects to `board.get_item_bounding_box(list(items))`.

**Why:**  
When passed a list, the method returns a list of `Box2` for each object, allowing a single batch request instead of many individual calls.

**Risk:**  
The behaviour of the method with a list argument is not explicitly documented, but it has been stable across all `kipy` versions.

**Alternative:**  
Calling `get_item_bounding_box` for each object individually – inefficient.

**Mitigation:**  
Tests in `test_full_pipeline_templates.py` use this method for building keepout.

---

### `Board.get_tracks()`

**Where used:**  
- `kicad/adapter.py` – `get_tracks()` is used in `TrackRegistry` to retrieve all tracks on the board during registry reconciliation.

**Why:**  
Needed for idempotent track creation – on subsequent runs, the tool must check which tracks already exist. Analogous to `get_vias()` for vias.

**Risk:**  
The `get_tracks()` method is not documented in the official KiCad/kipy documentation, but it works stably in current versions.

**Alternative:**  
None – without access to the list of tracks, idempotency cannot be implemented.

**Mitigation:**  
Integration tests (e.g., `test_registry.py`) verify the operation of the track registry.

---

### `Group.proto.items`

**Where used:**  
- `kicad/adapter.py` – `get_selected_items()` expands groups using `Group.proto.items`.

**Why:**  
The `.items` property of a group is always empty (local cache); the actual members are stored in the protobuf field `.proto.items`. Without access to this field, groups cannot be handled correctly.

**Risk:**  
The internal protobuf structure may change.

**Alternative:**  
None – this is the only way to obtain group members.

**Mitigation:**
Diagnostic scripts and `extract` tests use selection with groups.

---

### `KiCadClient._conn` — kipy has no public `close()` (upstream tracking)

**Where used:**
- `kicad/adapter.py` – `close()` reaches `self._kicad._client._conn.close()` to explicitly close the pynng socket instead of leaving that to the garbage collector.

**Why:**
Found live (2026-08-04): a silent Windows access violation with no Python frame on the crashing thread — every GUI reconnect creates a fresh `kipy.KiCad()` (and pynng socket) without closing the previous one, and several never-closed sockets finalized by the GC at unpredictable points on unpredictable threads is the plausible trigger. kipy 0.7.1 exposes **no public `close()`** on `KiCad`/`KiCadClient` (checked `kicad.py`/`client.py` directly), so this reaches into the private `_conn`.

**Risk:**
The private shape (`KiCadClient._connected` / `_conn`) may change in any kipy version.

**Alternative:**
None until kipy ships a public close.

**Upstream tracking:**
- kipy 0.7.1: no public `close()` (verified by reading `kicad.py`/`client.py`).
- **Action:** track the kipy issue tracker for a public `KiCad.close()` / `KiCadClient.close()`; when it lands, replace the private-attribute reach with the public call and remove this subsection.
- **Guard:** `TestClose` in `tests/kicad/test_kicad.py` pins the private-attribute shape against exactly this reach (closes correctly / no-op before connect / no-op without `_conn` / swallows a broken socket).

---

## 3. Deprecated APIs

### `Net.code`

**Where used:**  
- Not used in the main code (only in diagnostic scripts `diagnostics/` for debugging).

**Why:**  
May be useful for matching nets by code during debugging.

**Risk:**  
The `Net.code` property is marked as **deprecated** in the `kipy` documentation and will be removed in future versions.

**Alternative:**  
Use `Net.name` – and that is exactly what is done in all critical scenarios.

**Mitigation:**  
Not used in the main logic, so removal of `Net.code` will not affect the program.

---

## 4. Non‑Standard Getter/Setter Behaviour

### Getters return copies of objects (assignment to attributes is a no‑op)

**Where used:**  
In the codebase, the construct `obj.attribute.x = value` is **never** used – the entire object is always reassigned (e.g., `fp.position = Vector2(...)`).

**Problem:**  
In `kipy`, getters (e.g., `.position`, `.net`) return a **copy** of the object, not a reference. Assigning to an attribute of this copy (e.g., `fp.position.x = 1000`) **does not change** the original – it is a silent no‑op.

**Validation:**  
This behaviour has been confirmed by static tests (in earlier versions of the project) and is accounted for in the code.

**Alternative:**  
Always reassign the entire object: `fp.position = Vector2.from_xy(...)`.

---

### `FootprintInstance.orientation` setter does not accept `float`

**Where used:**  
The project always uses `Angle.from_degrees()` to set orientation.

**Problem:**  
The `.orientation` setter expects an `Angle` object. Passing a number raises a `TypeError`.

**Alternative:**  
Always use `Angle.from_degrees()` or `Angle.from_radians()`.

---

## Conclusion

KiCadStamp **intentionally** uses APIs that go beyond the stable public interface because only this allows solving real‑world automation tasks for placing components, vias, and tracks. However, all such places are:

- **clearly documented** in code comments;
- **accompanied by unit tests** covering critical scenarios;
- **provided with workarounds** (e.g., reassigning objects instead of modifying attributes) to minimise risks.

Thus, the tool remains reliable even in the face of an unstable API, and when updating KiCad or `kipy`, the tests promptly signal potential issues.