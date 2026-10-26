# Board and config checks (`tools/check_*.py`)

Three read-only command-line checks for hunting placement problems on a real board. Each takes a
**profile** (its folder or its `.sexp`) and answers one question:

| script | question | needs KiCad |
|---|---|---|
| [`check_placement.py`](#check_placementpy) | Does every part stand on the slot of **its** cluster? Whose slot does a misplaced part occupy? | yes, with this profile's board open |
| [`check_config_records.py`](#check_config_recordspy) | Does every record of the `.sexp` address a part of **its** cluster and sheet? | yes, with this profile's board open |
| [`check_footprints_vs_library.py`](#check_footprints_vs_librarypy) | Are the footprints of the **saved** board still what the library has (3D models, polygons, Role/Cluster fields)? | no, it reads the saved file |

They are scripts, not GUI buttons, on purpose: a check is a tool for hunting a bug, not a product
feature. Run them from the project root with the project's virtual environment:

```bash
.venv/bin/python tools/check_placement.py profiles/3ch-awg-tia-v103
.venv/bin/python tools/check_config_records.py profiles/3ch-awg-tia-v103
.venv/bin/python tools/check_footprints_vs_library.py profiles/3ch-awg-tia-v103
```

**Nothing is written.** The board is only read. The two live checks load the profile from a temporary
copy (relative `../` schematic paths are made absolute in the copy) and delete the copy afterwards; the
profile folder is never touched.

**Wrong board open → refusal.** The two live checks compare the board KiCad has open with the
profile's `root_sheet` and refuse (exit code 2) when they differ:

```
REFUSED: KiCad has '3CH-AWG-TIA-v103' open, the profile is for 'Test02' — open that board first
```

**Lines you can ignore.** Loading a profile may print `N track segment(s) are shorter than 2 × the
position tolerance …` (the config loader's note about very short copper segments of `net_traces`;
nothing is deleted) and `Connected KiCad version … is newer than the API version of kicad-python`
(the usual kipy/KiCad version gap). Neither is about the check.

**Which one when:**

* parts look scattered after a placement → `check_placement.py`;
* a placement refuses, or takes a part you did not expect → `check_config_records.py`;
* DRC says `lib_footprint_mismatch`, or after **Tools → Update Footprints from Library** →
  `check_footprints_vs_library.py`, then F8 if it reports lost fields.

**The same three checks from the GUI.** The **Checks** tab next to the Log (View → Checks — see
[docs/gui.md](gui.md), section Checks) runs these very scripts in their own process and shows their
output: the buttons, the exit codes and the reports are the ones described here, with the status
line coloured by the code (0 green, 1 yellow, 2 red). The scripts read the profile **from disk** —
save the profile (Ctrl+S) before a run while the GUI has unsaved edits, and for
`check_footprints_vs_library.py` save the board in KiCad.

---

## `check_placement.py`

**Question:** is every part on the slot its cluster's cell gives it — and if not, whose slot does it
stand on?

```bash
.venv/bin/python tools/check_placement.py <profile dir | profile .sexp> [--slot-mm 0.5] [--plan dryrun.txt]
```

### How it works

1. The board's footprints are grouped into **cluster instances**: same `Cluster` field and same top
   sheet (`Channel_0`, `FPGA`, `MCU`, or `/` for the root).
2. Each instance is matched to the cell of its entity (`entities` of the profile). An instance whose
   cluster has no entity, or several cells, is skipped.
3. The instance's frame (rotation, mirror) is found by **consensus**: every pair of roles proposes a
   frame through the product's own fit (`cell_frame.fit_cell_frame`); the proposal that puts the most
   roles within 0.05 mm (`RIGID_TOLERANCE_MM`) wins.
4. Roles outside the consensus are misplaced. For each, the check looks for a slot of **another**
   instance within `--slot-mm` (0.5 mm) of where the part stands — a "stolen" spot.
5. Empty slots (a role the cell has but the instance lacks) are checked for a part of another cluster
   standing on them.

### Output

```
board footprints: 336; cluster instances: 73; tolerance 0.05 mm, slot match 0.5 mm
instances checked: 64; clean: 49; with problems: 15; skipped: 9

[PIF_3V3_VCCIO @ FPGA] cell 'pif_3v3_vccio': 3/5 roles agree (rot +0, mirror False)
   MISPLACED C_OUT_BULK (C100) at (100.141, 122.418), its slot (101.445, 93.056) — 29.392 mm off
      → stands on slot C_OUT_BULK of [PIF_1V2_VCCD_PLL @ FPGA] (0.000 mm), whose own part is C98
```

| tag | meaning |
|---|---|
| `[CLUSTER @ sheet] cell '…': N/M roles agree` | the instance and its cell; N of the M matched roles fit one frame |
| `MISPLACED` | the part is outside the consensus frame; the line gives where it stands, where its slot is, and the distance |
| `→ stands on slot R of [OTHER @ sheet]` | the misplaced part occupies another instance's slot; `whose own part is X` names the part that should be there |
| `FRAME NOT TRUSTED` / `PAIR?` | fewer than two roles agree (typically a two-role cell): something is off, but the check cannot say which part. The frame is fitted to one part (it always shows `0.000 mm off`), the whole difference lands on the other |
| `DUPLICATE role R` | two parts of one instance carry the same role |
| `TURNED R (X) by …` | the part is on its slot but rotated relative to the cell (more than 0.1°) |
| `(absent roles: …)` | roles the cell lists but the instance does not have on the board |
| `(roles not in the cell: …)` | roles the instance has but the cell does not list |
| `--- empty slots taken by another cluster's part ---` | a slot with no own part, occupied by a part of another cluster |
| `--- skipped instances ---` | `no entity cell`, `N cells`, `cell '…' missing`, `1 matched role(s) — no frame`, `no usable pair` |

### Changes since the last run

Every real run (not `--plan`) compares its findings with the previous run of the **same profile** and
remembers itself in `~/.local/state/kicadstamp/placement_check_<profile>.json` (machine state, outside
`profiles/`). The GUI's **Checks** tab runs this same script and therefore shares that one baseline: a
`NEW` line may name a change made from the tab (or from the terminal), because both runs remember
themselves in the same file.

```
=== CHANGES since the last run ===
   last run: 2026-10-21 09:33:24
   GONE  C30 (C_IN_BYPASS of PIF_3V3_VDD @ MCU) at (186.304, 131.348), 36.399 mm off its slot
   now: 30 problem(s) on the board
```

* `NEW` — appeared since the last run: the action you did in between moved a part to the wrong place;
* `GONE` — fixed since the last run;
* `nothing changed` / `first run — this result is the baseline`.

**Catching the action that misplaces parts:** bring the board to a clean state, run the check once (the
baseline), then run it after every placement action (Apply, a tree redraw, Redraw selected). The first
`NEW` names the guilty action.

### `--plan`: check a placement before it happens

```bash
python kicadstamp_cli.py apply profiles/3ch-awg-tia-v103/config.sexp --dry-run > dryrun.txt
.venv/bin/python tools/check_placement.py profiles/3ch-awg-tia-v103 --plan dryrun.txt
```

The check reads the planned positions (`  C100: (x, y) mm, angle=a` lines of the dry run), moves the
footprints there **in memory** and checks the result. A `--plan` run is not remembered and prints no
changes.

### Exit code

`0` clean, `1` problems found (the report says which), `2` refused (wrong board open). A `--plan` run
reports the same way; it only skips the NEW/GONE comparison.

The code says only WHETHER there is anything in the report — the report itself stays the truth. That
is what a shell `&&` chain and the GUI's **Checks** tab colour their status line by.

---

## `check_config_records.py`

**Question:** does every record of the profile address a part of **its** cluster on **its** sheet?

```bash
.venv/bin/python tools/check_config_records.py <profile dir | profile .sexp>
```

The config never names a refdes (`R15`, `C4`): a record addresses a part by **Role + Cluster + sheet**.
This check answers every such address against the open board with the product's own rules — cluster by
`cluster_matching.cluster_prefix_match` (segment prefix, case-insensitive), sheet by
`sheet_names.sheet_in_path` (`"/"` is the root sheet).

### What is checked

| section | records | rule |
|---|---|---|
| entities | every role of each entity's cell | exactly one part with that Role in the entity's cluster on the entity's sheet |
| parts that no cell lists | every part of a cluster instance that has an entity | its Role is listed by a cell of that instance |
| pinned `refs` | an entity's explicit `refs` (role → refdes) | the part exists and carries that Role, that Cluster, that sheet |
| anchors | `anchor_role`/`anchor_cluster`/`anchor_sheet` of `net_traces`, mount nodes, tree anchors, `points`, `thermal_via_arrays` | exactly one part |
| copper ends | roles in a net_trace's `pads` and in a track's/via's `net_from_role` | the role exists on the board (no cluster is demanded: the copper's other end may stand in another cluster) |

Retired entities and net traces are not checked. An entity without a cluster (addressed by refs or
sheet only) is listed at the end as not checked.

### Output

```
--- entities: cell roles → parts of the entity's cluster ---
[conn_mcu_usart_mcu] cell 'conn_mcu_usart', CONN_MCU_USART @ MCU
   ABSENT    role R_SERIES_TX: the role stands on R15 (MCU @ MCU)

--- parts of a recorded cluster that no cell lists ---
   UNLISTED  Y1 (Role XTAL of XTAL_8MHZ @ MCU): no cell of XTAL_8MHZ @ MCU has it

--- anchors (net_traces, mount nodes, tree anchors, points, thermal vias) ---
   SHEET     tree fpga node 'fpga_22_oscill': anchor FPGA of FPGA @ Channel_0 — resolves to IC1 only because sheet narrowing is soft — the record says 'Channel_0', IC1 stands on FPGA @ FPGA
   117 anchor(s) checked

result: 7 problem(s)
```

| verdict | meaning | what to do |
|---|---|---|
| `ABSENT` | no part of the record's cluster on its sheet carries the role. The line says where the role stands instead — `the role stands on R15 (MCU @ MCU)` — or that it is on no part at all | a part tagged with the wrong Cluster in the schematic (fix the schematic, then F8), or a stale role in the cell (a renamed role: refresh the cell) |
| `UNTAGGED` | the role is on the record's sheet, but the part has **no** Cluster field | set the Cluster in the schematic, F8 — or check the board with `check_footprints_vs_library.py` (KiCad may have renamed the field) |
| `AMBIGUOUS` | several parts match — placement will refuse | give the parts distinct roles, or narrow the record (sheet, cluster) |
| `SHEET` | the record's sheet is wrong, yet it resolves to the one part of its cluster on another sheet — because sheet narrowing only applies when it leaves at least one candidate | correct the record's `sheet`; it works today by luck and breaks as soon as a second candidate appears |
| `UNLISTED` | a part of a recorded cluster instance whose Role no cell of that instance has | usually the other half of a renamed role (`ABSENT` OSC + `UNLISTED` XTAL); otherwise add the role to the cell or retag the part |
| `PINNED` | an entity's `refs` entry names a missing part, or a part with another Role/Cluster/sheet | fix the `refs` entry |

### Exit code

`0` clean, `1` problems found, `2` refused (wrong board open).

---

## `check_footprints_vs_library.py`

**Question:** are the footprints of the **saved** board still what the library has?

```bash
.venv/bin/python tools/check_footprints_vs_library.py <board.kicad_pcb | profile dir | profile .sexp> [--lib-dir <footprints dir>] [--all]
```

It reads the **saved file**, not KiCad: save the board first. KiCad does not need to run. Given a profile,
it opens the board next to the profile's `root_sheet`.

### Which library directory

The order is KiCad's own: `--lib-dir`, else `$KICAD10_FOOTPRINT_DIR`, else the same variable inside
`kicad_common.json`, else the platform default of a stock install (`/usr/share/kicad/footprints`, the
macOS app bundle, or `%PROGRAMFILES%\KiCad\<version>\share\kicad\footprints`). `kicad_common.json` and
the user's `fp-lib-table` are read from KiCad's settings directory **for the platform** —
`~/.config/kicad/10.0` on Linux, `%APPDATA%\kicad\10.0` on Windows, `~/Library/Preferences/kicad/10.0`
on macOS. When none of them is a directory the check **refuses** (exit 2) and prints every place it
looked — a run that cannot reach any library says so, instead of reporting every footprint as "not
checked".

### What is checked

For every footprint, its library copy is found through the project's and the user's `fp-lib-table`
(`${KIPRJMOD}` and the path variables of `kicad_common.json` expanded), else `<lib-dir>/<lib>.pretty`.
Then:

| check | catches |
|---|---|
| number of 3D models | models lost when a script rotates footprints through kicad-python 0.8.0 (`FootprintInstance.orientation`; fixed on kicad-python main, not yet released) |
| number of points of every `fp_poly`, in order | the polygon closing point dropped by IPC `UpdateItems` → DRC `lib_footprint_mismatch` (KiCad issue #25889) |
| Role/Cluster present | fields lost after **Update Footprints from Library** (KiCad issue #25887) |
| `KiLib_Generator` value = the library's | one of **our** fields renamed to the library field `KiLib_Generator` (#25887) — even when Role/Cluster survived (e.g. `Sim.Pins`) |

### Output

```
profile Test04.sexp -> board /home/denis/Projects/KiCad/Tests/Test02/Test02.kicad_pcb
board Test02.kicad_pcb: 12 footprint(s); library /usr/share/kicad/footprints
FIELDS LOST: 8 footprint(s) lack Role or Cluster — KiCadStamp would read them wrong; run F8 (update fields from schematic) BEFORE any placement:
  C1     Role=C_IN_BYPASS      Cluster=—                      KiLib_Generator=LDO_P3V3
FIELD RENAMED: 1 footprint(s) carry KiLib_Generator with a value that is not the library's — some other field of ours was renamed; run F8:
  D1     KiLib_Generator=1=K 2=A                library has SMD_2terminal_chip_molded
result: 3 ok, 9 damaged, 0 not checked
```

| line | meaning | what to do |
|---|---|---|
| `REF lib:name: 3D models 0 of 1` | the footprint lost its 3D model | Tools → Update Footprints from Library for that footprint (only 3D models checked), then F8 |
| `REF lib:name: fp_poly points [3] vs library [4]` | a polygon lost a point (DRC `lib_footprint_mismatch`) | the same targeted update; harmless for copper |
| `FIELDS LOST` | Role or Cluster is missing; `KiLib_Generator` holds our value | **F8 before any placement** (KiCadStamp refuses to place on such a board anyway) |
| `FIELD RENAMED` | `KiLib_Generator` holds a value that is not the library's: another field of ours was renamed | F8 |
| `fields: ok …; N also carry KiLib_Generator` | Role/Cluster intact; the library field was only added beside them | nothing |
| `library copy not found — not checked` | no library copy reachable | pass `--lib-dir`, or check the fp-lib-table |

`--all` also prints the clean footprints.

**Update Footprints from Library, safely:** update only what you need (one footprint, or only 3D
models), leave the text options off, press F8 right after, and run this check.

### Exit code

`0` clean, `1` damage found, `2` refused (no such file, not a board or profile, or no reachable
footprint library directory).

---

## `--json PATH`: the same run, for a machine

Every check accepts `--json PATH` (`merge_duplicate_mounts.py` included). The HUMAN report on
stdout does not change by one byte, and the machine report is written in the SAME run — one
board read, one config load (`kicadstamp/check_report.py` is the one place the shape lives).

```bash
.venv/bin/python tools/check_placement.py profiles/3ch-awg-tia-v103 --json /tmp/r.json
```

* `schema` — the shape version; a reader that does not know the number must say so;
* `script`, `when`, `profile`, `board`;
* `result` — `exit` (the very code the command returned), `problems`, `ok`, `skipped`,
  `not_checked` (`ok` is `null` where a check does not count it);
* `sections[]` — every section the run performed, in print order, each with `problems[]`
  (empty means "checked, clean"); the panel hides empty sections, the summary still lists them;
* a problem is `{key, kind, text, refs[], where, to_node}`: `text` is the line stdout showed,
  `kind` is one of a closed vocabulary (`MISPLACED`, `TAKEN`, `SHEET`, `ABSENT`, `MODELS`,
  `FIELDS_LOST`, `MERGE`, …), `refs` are the parts the overlay may mark, `to_node`
  `{tree, node, cell}` is where "to the node" goes — filled only when the check really knows;
* `changes` — NEW/GONE by that same `key` against the previous run of the same profile
  (`~/.local/state/kicadstamp/<script>_<profile>.json`, `baseline: true` on the first run).
  `check_placement.py` used to keep `placement_check_<profile>.json`: that file is still READ,
  so the first run after the move does not report everything as new. A dry-run merge has NO
  `changes` (nothing to compare), and instead carries `applied` — `false` for a dry run or a
  refusal, `true` after a successful `--write` — plus `refusal` with the reason;
* `notes[]` — `SKIPPED` / `NOT_CHECKED` / `INFO` lines that are not problems. A footprint
  whose library copy is missing is a `NOT_CHECKED` note (and `result.not_checked`), NOT a
  problem: "we could not look" must not read like "we found damage";
* `result.problems` is exactly the sum over the sections — a reader that counts the tree and a
  reader that reads the header can never disagree;
* `to_node` for anything about an INSTANCE is the tree node whose `ref` IS the entity's name
  (that is what Denis's trees hold — a mount node is anchored on its HOST's pad, so an anchor
  lookup finds next to nothing), and it is `null` rather than a guess when the name is not
  unique.

Rule: change a check's findings → change its `kind`s HERE and in `check_report.KINDS`; the
Checks tab switches on `kind`, never on the prose.

## Related

* `tools/kicad_bugs/` — standalone kicad-python scripts that reproduce the upstream KiCad and
  kicad-python bugs these checks detect.
* [docs/diagnostics.md](diagnostics.md) — the developer probes of `kicadstamp/diagnostics/`.
