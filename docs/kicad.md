# `kicadstamp/kicad` – Adapter for KiCad IPC Interaction

## Purpose

The modules in the `kicad/` directory provide a unified interface for interacting with the board in KiCad via the `kipy` library (KiCad Python IPC). They hide low‑level protocol details, ensure resilience to communication failures (retries on “KiCad is busy” errors and connection drops), simplify transaction handling (commit/rollback), and provide convenient access to board objects.

**Key capabilities of the adapter:**

- Searching for components (footprints), pads, zones, nets, vias, and **tracks**.
- Reading custom component fields (e.g., `Role` for automatic selection).
- Getting bounding boxes of objects (for keepout and collision detection) in a single batch request.
- Transactional updates and creation of objects with automatic rollback on errors.
- Flipping components to the opposite side via a GUI action (the only way to get correct mirroring of pads and silkscreen).
- Reading the current selection with group expansion.
- Deleting objects by UUID (for via and track placement registries).
- Creating vias **and tracks** (straight copper segments).
- **Diagnostic and workaround for the known KiCad bug #24966** – crash on the first IPC write when the schematic editor is open.

---

## Structure

```
kicad/
├── __init__.py        # Public API export
├── interfaces.py      # Abstract IBoardAdapter interface
├── adapter.py         # KiCadBoardAdapter implementation
└── pynng_safety.py    # pynng.Socket.close patch (applied on import)
```

---

## `interfaces.py` – Abstract Interface

Defines the abstract base class `IBoardAdapter`, which describes the contract for all board adapters. This allows easy substitution of implementations (e.g., for testing with mocks) and ensures that all necessary methods are present.

**Full list of interface methods:**

| Method | Description |
|--------|-------------|
| `refresh_board()` | Reload board data from KiCad (re‑read all objects). |
| `get_footprint(ref)` | Get a footprint by reference (e.g., `IC1`). |
| `get_footprints()` | Get all footprints on the board. |
| `get_vias()` | Get all existing vias. |
| `get_tracks()` | Get all existing tracks (copper segments). |
| `get_selected_items()` | Get the current selection (expanding groups). |
| `get_field_value(fp, field_name)` | Read a custom field value from a component (e.g., `Role`). |
| `get_footprint_pads(fp)` | Get pads of a footprint. |
| `get_pad_by_number(fp, number)` | Find a pad by number (e.g., `'1'`, `'145'`). |
| `get_zone_by_name(name)` | Find a zone by name (Rule Area). |
| `get_net_by_name(name)` | Find a net by name. |
| `get_all_nets()` | Get all nets on the board. |
| `get_bounding_boxes(items)` | Get bounding boxes (Box2) for a list of objects (batch request). |
| `begin_commit()` | Start a transaction. |
| `push_commit(commit, description)` | Commit the transaction. |
| `drop_commit(commit)` | Roll back the transaction. |
| `update_items(items)` | Update existing objects on the board. |
| `create_items(items)` | Create new objects on the board. |
| `flip_selected(footprints)` | Flip selected footprints to the opposite side. |
| `commit_with_retry(description, work_fn, retries)` | Execute work within a transaction with automatic retries on IPC errors. |
| `create_via(position, net, drill_mm, diameter_mm)` | Create a `Via` object (not yet added to the board). |
| `create_track(start, end, width_mm, net, layer)` | Create a `Track` object (not yet added). |
| `remove_by_id(uuid_str)` | Delete an object by UUID (used by registries). |

The interface deliberately does **not** expose kipy types. Every method
returns/accepts the project's own domain types — `Footprint`, `Pad`, `Via`,
`Track`, `Net`, `Zone` from `kicadstamp/domain/board.py` and `Vector2`/
`Angle`/`BoardLayer`/`Box2` from `kicadstamp/domain/geometry.py`. Only
`adapter.py` imports `kipy.*` and converts at the boundary (see the `*_from_kipy`
mappers in `domain/board.py`).

---

## `adapter.py` – `KiCadBoardAdapter` Implementation

This is the main implementation of the interface, encapsulating all `kipy` calls. It adds internal logic for reliability, diagnostics, and convenience.

### Key Features

#### 1. Resilience to IPC Failures

- **Retries on “KiCad is busy”** – all mutating operations are wrapped in `_mutating_call()`, which retries on `ApiError` with the message "not ready" with increasing delays (up to 3 attempts).
- **Handling connection drops** – on `ConnectionError` (usually meaning KiCad crashed), it raises a clear message pointing to the known bug #24966 and suggests a workaround.
- **`commit_with_retry`** – a wrapper for arbitrary work within a transaction, with automatic rollback and retries on any exception.

#### 2. Workaround for KiCad Bug #24966

Known defect in KiCad 10.0.4: the first write via IPC (`update_items` or `create_items`) can crash the entire process if the schematic editor is open and no interactive edit has been made in the session. The adapter includes:

- **`check_write_crash_risk()`** – called before the first mutating operation. It checks via `get_open_documents()` whether the schematic is open and, if so, logs a warning recommending either closing the schematic or making an interactive edit in PCB.
- **`_mutating_call()` wrapper** – catches `ConnectionError` and logs it as a probable KiCad crash, providing the user with a clear instruction.

Thus, the adapter does not prevent the crash (impossible from the client side) but gives a clear diagnosis and workaround.

#### 3. Batch Requests

`get_bounding_boxes(items)` takes a list of objects and returns a list of `Box2` in a single call to `board.get_item_bounding_box()`. This significantly reduces the number of IPC calls when building keepout areas and checking collisions.

#### 4. Correct Handling of Selection and Groups

`get_selected_items()` does not simply return `list(board.get_selection())`; it additionally expands groups (`Group`) because their `.items` property is always empty, while the actual members are in `.proto.items`. It scans all footprints and vias on the board, matching their UUIDs against the UUIDs in `proto.items` of groups. This is critical for the `extract` command and for cloning‑by‑selection mode.

#### 5. Reading Custom Fields

`get_field_value(footprint, field_name)` filters `footprint.texts_and_fields` by type `Field` (rather than just checking for the `name` attribute), because it can also contain `BoardText` objects (plain text on silkscreen). This ensures we read only the field added in Eeschema.

#### 6. Flip via GUI Action

`flip_selected(footprints)` uses `kicad.run_action("pcbnew.InteractiveEdit.flip")` – the only way to perform a true flip with pad and silkscreen mirroring. Simply assigning `fp.layer = BoardLayer.BL_B_Cu` changes only the layer, not the geometry. After the call, the board must be reloaded via `refresh_board()`.

#### 7. Creating Vias and Tracks

- **`create_via(position, net, drill_mm, diameter_mm)`** – creates a `Via` object with filled fields (type, position, net, diameters) but **does not add** it to the board – it must be passed to `create_items` inside a transaction.
- **`create_track(start, end, width_mm, net, layer)`** – similarly creates a `Track` object (straight copper segment) with filled parameters. It is not added until `create_items` is called.

#### 8. Deletion by UUID

`remove_by_id(uuid_str)` is used by registries to delete obsolete vias and tracks. It creates a `KIID` object and calls the internal adapter method to remove items by UUID. If the object no longer exists, it returns `False` and logs a warning, but does not raise an exception – allowing graceful handling of manually deleted objects.

#### 9. Retrieving Existing Tracks

`get_tracks()` returns all straight tracks on the board. Used by the `TrackRegistry` to reconcile against real objects on subsequent runs, ensuring idempotency.

#### 10. Internal Constants and Settings

- Default timeout is `DEFAULT_TIMEOUT_MS` from `constants.py` (20000 ms).
- The timeout can be overridden via the `timeout_ms` parameter in the constructor.
- Logging is done via the `logging` module with DEBUG/INFO levels.

---

## Relationships with Other Modules

The adapter is used throughout the project wherever KiCad interaction is needed:

| Module | Usage |
|--------|-------|
| `placement/planner.py` | Getting the target component (IC) and its pads. |
| `placement/executor/move_executor.py` | Moving and flipping components. |
| `placement/executor/via_executor.py` | Creating vias and registering them. |
| `placement/executor/track_executor.py` | Creating tracks and registering them. |
| `placement/services/manual_position_calculator.py` | Looking up footprints by refdes. |
| `placement/services/clone_position_calculator.py` | Looking up footprints during cloning (including anchor_role). |
| `placement/services/via_planner.py` | Reading pads and creating thermal vias. |
| `placement/services/component_pool.py` | Building the component pool by `Role` and net. |
| `validation.py` | Checking pad/template existence and via/track net validity. |
| `registry.py` | Checking existing vias/tracks, deleting obsolete ones by UUID. |
| `template_extraction.py` | Reading the current selection and extracting templates (including tracks). |
| `diagnostics/*.py` | Getting bboxes, debugging, testing. |

Thanks to the `IBoardAdapter` interface, the code does not depend on a specific implementation and can be easily tested with mocks.

---

## Usage Notes

- All coordinates and sizes in the adapter are expected in **nanometres** (KiCad's internal units). To convert from mm to nm, use the constant `MM = 1_000_000` from `utils/units.py`.
- In transactions, it is important to catch exceptions and roll back the commit properly to avoid leaving incomplete changes.
- `flip_selected` does not update the local footprint object – after calling it, you must reload the board via `refresh_board()` and re‑fetch the footprint.
- When reading custom fields via IPC, the field must be added in the schematic (Eeschema) and propagated to the board via **Update PCB from Schematic**.
- `get_selected_items()` requires that something is selected in KiCad. If a group is selected, it is correctly expanded into its members.
- The adapter uses the default timeout `DEFAULT_TIMEOUT_MS` from `constants.py` (20000 ms). It can be overridden via the `timeout_ms` parameter when instantiating.
- `remove_by_id()` is used by registries to delete obsolete vias/tracks; if the object with that UUID no longer exists, it returns `False` and logs a warning, but does not raise an exception.

---

## Error Handling and Diagnostics

### Known Issues

- **KiCad crash on first write (#24966)** – the session's first `begin_commit()`/`push_commit()` transaction can crash KiCad if the Schematic Editor is open. The adapter logs a warning and treats a `ConnectionError` as a probable crash. Full write-up: [crash_hunting.md](crash_hunting.md).
- **“KiCad is busy”** – occurs when KiCad is busy with a modal dialog or a long operation. The adapter automatically retries up to 3 times with delays.
- **Stale UUIDs** – when deleting a via/track via `remove_by_id()`, if the object is already gone, the method returns `False` and logs a warning.

### Diagnostic Tools

For debugging connection issues and crashes, use the diagnostic script `diagnose_first_write_crash.py` (see
[diagnose_first_write_crash.md](diagnose_first_write_crash.md)) and the external crash‑capture script
`hunt-proc.ps1` to intercept crash dumps. For the full crash-hunting toolkit (including the `tools/` cleanup
and repeated-run scripts, and how to capture a core dump) see [crash_hunting.md](crash_hunting.md).

---

## Unstable and Undocumented APIs

The adapter uses the following elements that go beyond the stable public API of KiCad/kipy:

| Element | Status | Reason |
|---------|--------|--------|
| `run_action("pcbnew.InteractiveEdit.flip")` | **Unstable** | GUI actions may change between KiCad versions. |
| `Group.proto.items` | **Undocumented** | Internal protobuf field needed to obtain group members. |
| `footprint.texts_and_fields` | **Undocumented** | Used to read custom fields (`Field`). |
| `footprint.definition.items` | **Undocumented** | Used to obtain component pads. |
| `adapter.remove_by_id()` | **New, potentially unstable** | Added recently (July 2025), may change. |
| `board.get_tracks()` | **Undocumented** | Used to retrieve all tracks on the board for registry reconciliation. |
| `board.get_item_bounding_box(list)` | **Undocumented** | Used for batch bounding‑box requests. |
| `kicad.get_open_documents()` | **Official but rarely used** | Used to check if the schematic is open (crash warning). |

All such places are **documented** in the code and covered by unit tests, allowing early detection of breakage when updating KiCad or `kipy`.

---

## Dependencies

- `kipy` – official Python library for KiCad IPC (installed separately, version 0.7.1 or newer). Imported ONLY by `adapter.py`; nothing outside `kicad/` touches it.
- `kipy.board_types` – KiCad data types (FootprintInstance, Pad, Via, Track, Net, Field, Group, BoardLayer, etc.) — internal to `adapter.py`.
- `kipy.geometry` – geometric primitives (Vector2, Box2, Angle) — internal to `adapter.py`.
- `kipy.errors.ApiError`, `kipy.errors.ConnectionError` – for IPC error handling.
- `kicadstamp.domain` (`domain/board.py`, `domain/geometry.py`) – the domain DTOs and value types the interface exposes to the rest of the project.
- Standard Python packages: `logging`, `time`, `typing`.

---

## License

The entire `kicad/` module is distributed under the MIT license, the same as the main project.
