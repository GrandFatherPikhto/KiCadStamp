# `kicadstamp/cloner` – File‑Based Cloner (Channel Analysis Without IPC)

## Purpose

The `cloner/` module provides the `clone-extract` command for offline (no‑IPC) analysis of hierarchical KiCad projects. It is designed for:

- **Taking channel snapshots** – extracting all components, tracks, and vias belonging to a specific channel instance (e.g., `Channel_0`) into a readable YAML file.
- **Building a twin map** – automatically determining the correspondence of components and nets between different instances of the same hierarchical sheet (template).
- **Visualising “foreign” copper** – identifying global nets (power, ground) that pass through the channel boundaries, so that the designer can consciously handle them when cloning.

**Key difference** from the rest of KiCadStamp: it **does not require** a running KiCad instance and works exclusively with project files (`.net` and `.kicad_pcb`). This makes it ideal for preliminary analysis, studying existing layouts, and preparing `ClonePlacement` configurations.

---

## Module Structure

```
cloner/
├── __init__.py           # Public API export
├── extract.py            # Main entry point – channel extraction to YAML
├── plan.py               # Phase 3 – auto clone_placements block + net_matching verify
├── models.py             # Dataclasses for all entities (components, segments, vias, twin map)
├── netlist.py            # .net file parsing, twin map construction
├── pcb.py                # .kicad_pcb parsing, channel data extraction
└── sexp.py               # Utilities for S‑expressions on top of sexpdata
```

---

## Modules and Their Functions

### `sexp.py` – S‑Expression Utilities

**Purpose:**  
Provides lightweight helper functions for navigating the syntax trees returned by the `sexpdata` library. `sexpdata` was chosen over specialised parsers (e.g., `kinparse`) because it is more resilient to new tokens and syntactic extensions in KiCad (the format has been stable since version 6, while `kinparse` stumbles on KiCad 10 netlists).

**Key Functions:**

| Function | Description |
|----------|-------------|
| `sval(x)` | Extracts a string value from a `sexpdata.Symbol`; otherwise returns `x` as‑is. |
| `is_node(n, key)` | Checks if a node is a list whose first element is `key`. |
| `children(node, key)` | Returns all child nodes with the given key. |
| `child(node, key, default)` | Returns the first child node with the key, or `default`. |
| `atom(node, key, default)` | Returns the value of the second element of the first child node (an atom). |
| `load_file(path)` | Loads an S‑expression from a file. |

---

### `models.py` – Data Models

**Purpose:**  
Defines dataclasses for storing information from the netlist and the board, as well as for representing the twin map and channel snapshot.

**Key Models:**

| Class | Description |
|-------|-------------|
| `NetlistComponent` | Component from the netlist: `ref`, `value`, `footprint`, `sheet_names`, `sheet_tstamps`, `uuid`. Includes properties `channel` (extracts channel name from the path) and `inner_key` (a unique twin‑search key: `{inner_path}#{uuid}`). |
| `ChannelInfo` | Channel instance information: name, sheet UUID, component list by `inner_key`, and local nets. |
| `TwinMap` | Twin map: dictionary `channels` (name → `ChannelInfo`) and `components` (`inner_key` → dictionary `{channel_name: ref}`). Includes methods `twin_ref(ref, src_ch, dst_ch)` to find a twin ref, and `twin_net(net, src_ch, dst_ch)` to translate a local net name. |
| `PcbFootprint` | Footprint from the board: `uuid`, `ref`, `lib_id`, `path` (hierarchical path), coordinates, rotation, layer. Property `channel_uuid` extracts the first segment of the path. |
| `PcbSegment` | Track segment: UUID, start/end coordinates, width, layer, net. |
| `PcbVia` | Via: UUID, position, dimensions, layers, net. |
| `ChannelPcbSnapshot` | Channel snapshot: lists of footprints, segments, and vias belonging to the channel, plus separate lists `foreign_segments` and `foreign_vias` (global nets inside the channel’s bounding box). Has a `bbox_mm()` method to compute the bounding rectangle. |

---

### `netlist.py` – Netlist Parsing and Twin Map Construction

**Purpose:**  
Parses the `.net` file, extracts all components with their hierarchical paths, identifies channel instances, and builds the twin map (`TwinMap`).

**Twin‑building algorithm:**

1. For each component in the netlist, determine the channel name (the first segment of the path, e.g., `Channel_0`).
2. Compute `inner_key = {inner_path_inside_channel}#{symbol_uuid_in_template}`.
3. Components with the same `inner_key` in different channels are considered twins.
4. Groups where a component is not present in all channels are marked as “incomplete” and logged as a warning (these components will not be cloned via mapping).

**Key Functions:**

| Function | Description |
|----------|-------------|
| `parse_netlist(net_path)` | Parses the netlist, filters out `unconnected` nets, and returns components, local nets per channel, and global nets. |
| `build_twin_map(comps, local_by_ch)` | Builds a `TwinMap` from the component list and local nets. Logs incomplete groups. |

**Why not by refdes?**  
Component numbering between channels can be non‑linear (e.g., `FB602` → `FB1602` → `FB1102`). Using `inner_key` (path + UUID) guarantees exact matching independent of numbering order.

---

### `pcb.py` – Board Parsing and Channel Selection

**Purpose:**  
Parses the `.kicad_pcb` file, extracts all footprints, segments, and vias, and then filters them by channel membership.

**Channel membership criteria:**

- **Footprints:** the first segment of their hierarchical `path` must match the channel sheet UUID (from the netlist). **Name‑based matching is not used** – only UUID.
- **Segments and vias:** their net must start with the prefix `/Channel_N/`. This indicates a net local to that channel.
- **Foreign elements:** segments and vias of global nets (e.g., `GND`, `+3V3`) that physically lie inside the channel’s bounding box (bbox with a 1 mm margin) are collected into separate `foreign_segments` and `foreign_vias` lists. They are not included in the clone but are important to know for manual power/ground connections.

**Key Classes and Methods:**

| Class/Method | Description |
|--------------|-------------|
| `PcbDocument` | Represents the loaded board. In the constructor, it parses and stores the net table, footprints, segments, and vias. |
| `_net_ref(node)` | Extracts the net from a node, supporting both numeric IDs (old format) and string names (new KiCad 10 format). |
| `snapshot_channel(channel_name, channel_uuid, bbox_margin_mm=1.0)` | Filters by UUID and net prefixes, computes the bbox, and returns a `ChannelPcbSnapshot`. |

---

### `extract.py` – Orchestration and Serialisation

**Purpose:**  
Ties together the netlist and board, builds the channel snapshot, and serialises it to YAML. This is the main entry point for the `clone-extract` command.

**Algorithm:**

1. Parse the netlist (`parse_netlist`) and build the twin map (`build_twin_map`).
2. Check that the requested channel exists in the map.
3. Load the board (`PcbDocument`) and obtain the channel snapshot (`snapshot_channel`).
4. For each footprint in the snapshot, compute twin refdes in other channels (via `TwinMap.twin_ref`).
5. Convert the snapshot and twin map into a Python dictionary ready for YAML (coordinates rounded to 4 decimals).
6. Write the result to the output file.

**Functions:**

| Function | Description |
|----------|-------------|
| `snapshot_to_dict(snap, twin)` | Serialises `ChannelPcbSnapshot` and `TwinMap` into a dictionary, adding `twins` for each component. |
| `extract_channel(net_path, pcb_path, channel, output_yaml)` | The main function called from the CLI. Performs all steps and returns the result dictionary. |

### `plan.py` – Auto clone_placements block (Phase 3)

**Purpose:** turns the file-based snapshot into a ready `clone_placements:` block
for a channel clone — WITHOUT manually writing params:/nets:. Also hosts the
net_matching verification shared with the live `channel-copy` flow.

**Key Functions:**

| Function | Description |
|----------|-------------|
| `plan_clone_placements(...)` | Generates the `clone_placements:` LIST of records (one per target channel): name/cluster/cell/xy, params `{channel: N}`, and nets `{role: net}` auto-derived from the source channel's real pad nets via `TwinMap.twin_net` — local nets prefix-remapped `/Channel_0/...` → `/Channel_N/...`, a role with exactly one global net keeps it as-is, bridging/multi-net roles are deliberately left to the cell's own auto-derived net_template (never a silent guess). |
| `verify_channel_net_mapping(...)` | Runs net_matching (Kuhn + Tarjan SCC, Phase 0) on two channels' role↔net evidence. SCC ambiguity / non-isomorphism is returned as DIAGNOSTICS, never a stop (safe-default: every member of an ambiguous SCC is a valid answer). |
| `clone_placements_to_dict(...)` | Wraps the generated records under the `clone_placements:` top-level key (the config list-section shape). |

Per-footprint `Role` + pad nets come from the `.kicad_pcb` parser
(`PcbFootprint.role` / `.pad_nets`, Phase 3) and are also included in the
snapshot output.

---

## Output YAML Snapshot Format

```yaml
channel: Channel_0
channel_sheet_uuid: 12345678-1234-1234-1234-123456789abc
summary:
  footprints: 42
  segments: 156
  vias: 38
  foreign_segments_in_bbox: 12
  foreign_vias_in_bbox: 4
bbox_mm:
  x0: 50.0
  y0: 60.0
  x1: 80.0
  y1: 90.0

footprints:
  - ref: C601
    lib_id: "Capacitor_SMD:C_0402_1005Metric"
    x_mm: 52.34
    y_mm: 64.56
    rotation_deg: 0.0
    layer: F.Cu
    uuid: "abc-123..."
    twins:
      Channel_1: C1601
      Channel_2: C1101

segments:
  - start: [52.0, 65.0]
    end: [53.0, 66.0]
    width_mm: 0.2
    layer: F.Cu
    net: /Channel_0/DAC_Signal
    uuid: "def-456..."

vias:
  - at: [52.5, 65.5]
    size_mm: 0.6
    drill_mm: 0.3
    layers: [F.Cu, B.Cu]
    net: GND
    uuid: "ghi-789..."

foreign_in_bbox:
  note: "Global net copper inside the channel boundaries: not included in the clone; channel connections to global rails should be handled deliberately."
  segment_nets: [GND, +3V3]
  via_nets: [GND]
```

---

## Relationships with Other Modules

The `cloner/` module is **self‑contained** and does not depend on `kicad/adapter.py`, `placement/`, or `geometry/`. It is used in `kicadstamp_cli.py` via the `clone-extract` command, and `plan.py` additionally reuses the Phase 0 `net_matching` module (Kuhn+SCC) and the config s-expr converter.

Its output (YAML snapshots) is intended for **manual analysis** by the developer. The `clone-plan` command automates the next step — generating the ready `clone_placements:` block from the snapshot without hand-writing `params`/`nets` (Phase 3).

---

## CLI Usage

```bash
python kicadstamp_cli.py clone-extract --net project.net --pcb project.kicad_pcb --channel Channel_0 --output snapshot.yaml
```

**Parameters:**

| Parameter | Description |
|-----------|-------------|
| `--net` | Path to the `.net` file (exported from Eeschema). |
| `--pcb` | Path to the `.kicad_pcb` file. |
| `--channel` | Channel name to extract (e.g., `Channel_0`). |
| `--output` | Path to the output YAML file. |
| `-v, --verbose` | Verbose logging. |

**Example output:**

```
[Channel_0] footprints: 42, segments: 156, vias: 38 -> snapshot.yaml
```

### `clone-plan` — auto clone_placements block

```bash
python kicadstamp_cli.py clone-plan --net project.net --pcb project.kicad_pcb \
  --source Channel_0 --cell dac --output clone_placements.sexp
```

**Parameters:**

| Parameter | Description |
|-----------|-------------|
| `--net` | Path to the `.net` file. |
| `--pcb` | Path to the `.kicad_pcb` file. |
| `--source` | Source channel (the one the cell was extracted from). |
| `--cell` | Cell (template) name to clone. |
| `--targets` | Target channels, comma-separated (default: every other channel). |
| `--cluster` | Cluster tag base (default: the target channel name). |
| `--xy` | Absolute position in mm (default: the target channel's own bbox origin). |
| `--anchor-role` / `--anchor-sheet` | Anchor by Role field (+ sheet narrowing). |
| `--output` | Output `.sexp` file with the `clone_placements:` block. |

The output is a ready `clone_placements:` block in the **s-expr** config grammar —
it loads into the config (apply can run it) without manual net definition.

---

## Practical Usage Example

1. **Export the netlist** from Eeschema (`File → Export → Netlist...`) and choose the `KiCad` format.
2. Ensure you have the board file (`.kicad_pcb`).
3. Run `clone-extract` for one of the channels to see which components and nets are present and who their twins are in other channels.
4. Examine the resulting YAML:
   - Verify that all components have twins in all channels (incomplete groups will be logged as warnings).
   - Pay attention to `foreign_in_bbox` – this indicates which global nets pass through this channel and will need to be consciously connected in the clone (via spoke vias or separate components).
5. Run `clone-plan` to auto-generate the `clone_placements:` block for every target channel (cluster/cell/xy + params `{channel: N}` + nets `{role: net}`), writing it straight into the s-expr config — no manual `params`/`nets`.
6. If the role→net mapping is ambiguous (electrically symmetric roles), `clone-plan` and `channel-copy` report the SCC group as a DIAGNOSTIC warning — never a stop; disambiguate by sheet/cluster.

---

## Developer Notes

- **Avoiding `kinparse`:** `sexpdata` was chosen because it is more robust to KiCad format changes. The KiCad 10 netlist contains syntactic constructs that `kinparse` cannot handle.
- **KiCad 10 support:** In `pcb.py`, nets can be either numeric IDs or string names; the code handles both, ensuring compatibility with the new format.
- **Incomplete groups:** If a component is missing from one of the channels, it is logged as a warning. Such components will not be cloned automatically and must be added manually or excluded from the template.
- **Foreign elements:** Only global segments and vias lying inside the channel’s bbox (with a 1 mm margin) are collected. This avoids cluttering the snapshot with distant elements and focuses on potentially conflicting ones.
- **No dependency on live KiCad:** All operations are offline, making `clone-extract` safe and fast for preliminary analysis.

---

## License

The `cloner/` module is distributed under the MIT license, the same as the main project.