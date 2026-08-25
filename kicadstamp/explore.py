# kicadstamp/explore.py
"""
explore.py — read-only query facade over KiCadBoardAdapter, for ad-hoc
interactive inspection ("select by Role/Cluster/sheet/net, see what came
back") instead of writing a one-off script every time. Grew out of a real
pattern repeated close to a dozen times in one working session: refresh_board()
-> get_footprints() -> filter by Role/Cluster field -> get_footprint_pads()
for nets -> resolve_sheet_path_names() for the sheet instance.

Purely additive and read-only — never touches PlacementRegistry/BatchExecutor,
carries none of the duplicate-via/track risk that mutating code has to guard
against (see registry.py).
"""
from dataclasses import dataclass, field
from typing import Any

from .domain.board import Footprint

from .constants import CLUSTER_FIELD_NAME, DEFAULT_TIMEOUT_MS, ROLE_FIELD_NAME
from .cluster_matching import cluster_prefix_match
from .kicad.adapter import KiCadBoardAdapter
from .sheet_names import build_sheet_name_map, resolve_sheet_path_names


def selection_signature(items) -> tuple:
    """Cheap, stable identity for a raw get_selected_items()/select_items()
    list — just enough to tell "selection changed" from "same selection, new
    tick", with no field reads or extra IPC. Footprints key on their refdes,
    vias/tracks on (type, net name). Moved out of gui/main_window.py's
    _raw_selection_signature (Phase 2 of the gui god-file decomposition) —
    it is pure, selection-related logic that belongs in core; MainWindow now
    calls this directly."""
    parts = []
    for item in items:
        if isinstance(item, Footprint):
            parts.append(("fp", item.ref))
        else:
            parts.append((type(item).__name__, getattr(item, "net_name", None)))
    return tuple(parts)


@dataclass
class Selected:
    """One footprint matched by Board.select() — plus the raw fp handle as
    an escape hatch to call any KiCadBoardAdapter method directly."""
    ref: str
    role: str | None
    cluster: str | None
    sheet: list[str | None]     # full resolve_sheet_path_names() chain
    nets: dict[str, str]           # pad number -> net name
    fp: Footprint = field(repr=False)


class Selection(list):
    """List[Selected] with a human-readable table — no new dependency, plain
    fixed-width columns. ``str(selection)`` returns the table as text; the
    library itself never prints to stdout (only ``show()`` does, as a CLI-only
    convenience)."""

    def __str__(self) -> str:
        if not self:
            return "(empty)"
        headers = ("ref", "role", "cluster", "sheet", "nets")
        rows = []
        for s in self:
            sheet_str = "/".join(x for x in s.sheet if x) or "-"
            net_items = list(s.nets.items())
            nets_str = ", ".join(f"{k}={v}" for k, v in net_items[:3])
            if len(net_items) > 3:
                nets_str += ", ..."
            rows.append((s.ref, s.role or "-", s.cluster or "-", sheet_str, nets_str or "-"))
        widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]

        def fmt(row):
            return "  ".join(c.ljust(w) for c, w in zip(row, widths))

        lines = [fmt(headers), fmt(["-" * w for w in widths])]
        lines += [fmt(r) for r in rows]
        return "\n".join(lines)

    def show(self) -> None:
        """Print the table — CLI-only convenience wrapper around __str__()."""
        print(self)


class Board:
    """Read-only, explicitly-refreshed snapshot of the live board. Never
    auto-refreshes — select() always answers against the snapshot taken at
    the last connect()/refresh() call, so results are stable for the
    lifetime of the object (same discipline as dependency_order.py: never
    guess staleness, make refresh a deliberate step)."""

    def __init__(self, adapter: KiCadBoardAdapter, sheet_names: dict[str, str]):
        self.adapter = adapter
        self.sheet_names = sheet_names
        self._footprints: list[Footprint] = []
        self._role_cache: dict[str, str | None] = {}
        self._cluster_cache: dict[str, str | None] = {}
        self._nets_cache: dict[str, dict[str, str]] = {}
        self._sheet_cache: dict[str, list[str | None]] = {}

    @classmethod
    def connect(cls, timeout_ms: int = DEFAULT_TIMEOUT_MS,
                schematic_dir: str | None = None,
                schematic_files: list[str] | None = None,
                config_path: str = ".") -> "Board":
        """config_path anchors schematic_dir/schematic_files resolution —
        build_sheet_name_map resolves both relative to Path(config_path).parent
        (same convention as registry_path in config/loader.py).
        Pass your actual profile path (e.g. "profiles/3ch-awg-tia.yaml") so a
        schematic_dir copied straight out of that profile resolves the same
        way it does for `apply`/`extract` — the default "." anchors to the
        current working directory instead, for schematic_dir values already
        written relative to cwd."""
        adapter = KiCadBoardAdapter(timeout_ms=timeout_ms)
        sheet_names = (build_sheet_name_map(config_path, schematic_dir, schematic_files or [])
                       if schematic_dir else {})
        board = cls(adapter, sheet_names)
        board.refresh()
        return board

    def refresh(self) -> None:
        """Re-fetches the footprint list and clears every per-footprint
        cache. Call after any board change (manual edit in KiCad, or a
        scripted apply_config() run) — select() never does this for you."""
        self.adapter.refresh_board()
        self._footprints = self.adapter.get_footprints()
        self._role_cache.clear()
        self._cluster_cache.clear()
        self._nets_cache.clear()
        self._sheet_cache.clear()

    @staticmethod
    def _ref(fp: Footprint) -> str:
        return fp.ref

    def _role(self, fp: Footprint) -> str | None:
        ref = self._ref(fp)
        if ref not in self._role_cache:
            self._role_cache[ref] = self.adapter.get_field_value(fp, ROLE_FIELD_NAME)
        return self._role_cache[ref]

    def _cluster(self, fp: Footprint) -> str | None:
        ref = self._ref(fp)
        if ref not in self._cluster_cache:
            self._cluster_cache[ref] = self.adapter.get_field_value(fp, CLUSTER_FIELD_NAME)
        return self._cluster_cache[ref]

    def _nets(self, fp: Footprint) -> dict[str, str]:
        ref = self._ref(fp)
        if ref not in self._nets_cache:
            pads = self.adapter.get_footprint_pads(fp)
            self._nets_cache[ref] = {p.number: p.net_name for p in pads if p.net_name}
        return self._nets_cache[ref]

    def _sheet(self, fp: Footprint) -> list[str | None]:
        ref = self._ref(fp)
        if ref not in self._sheet_cache:
            self._sheet_cache[ref] = resolve_sheet_path_names(fp, self.sheet_names)
        return self._sheet_cache[ref]

    def select(self, ref: str | None = None, role: str | None = None,
               cluster: str | None = None, sheet: str | None = None,
               net: str | None = None) -> Selection:
        """Every argument is an optional, AND-combined filter over the
        current snapshot:
          - ref: exact refdes match.
          - role: exact match against the Role field.
          - cluster: segment-prefix match (cluster_prefix_match, same
            function the real anchor_cluster resolver uses) — NOT exact
            equality, so this previews what apply would actually pick.
          - sheet: membership in the footprint's resolved sheet-instance
            chain (Channel_0/Channel_1/... for a reused hierarchical sheet).
          - net: the footprint has this net on at least one pad.
        """
        result = Selection()
        for fp in self._footprints:
            fp_ref = self._ref(fp)
            if ref is not None and fp_ref != ref:
                continue
            fp_role = self._role(fp)
            if role is not None and fp_role != role:
                continue
            fp_cluster = self._cluster(fp)
            if cluster is not None and not cluster_prefix_match(fp_cluster or '', cluster):
                continue
            fp_sheet = self._sheet(fp)
            if sheet is not None and sheet not in fp_sheet:
                continue
            fp_nets = self._nets(fp)
            if net is not None and net not in fp_nets.values():
                continue
            result.append(Selected(ref=fp_ref, role=fp_role, cluster=fp_cluster,
                                    sheet=fp_sheet, nets=fp_nets, fp=fp))
        return result

    def select_items(self, net: str | None = None, role: str | None = None,
                      cluster: str | None = None, sheet: str | None = None) -> list[Any]:
        """Raw mixed list (FootprintInstance/Via/Track) — the same shape
        adapter.get_selected_items() returns, ready to pass to
        template_extraction.extract_template_from_selection(items=...)
        explicitly, instead of a live GUI selection. Footprints are filtered
        exactly like select() (role/cluster/sheet/net); vias/tracks have no
        Role/Cluster field at all, so only `net` applies to them.

        KNOWN LIMITATION: net alone cannot distinguish same-net components
        across different physical instances — GND vias especially, since GND
        is shared board-wide. Not a sufficient sole source for those cases;
        assemble the list some other way, or keep a live GUI selection for
        that particular subsystem. Not a regression versus today — there is
        currently no way to do this without a mouse at all, this only covers
        the (common) case where the net name itself is already unambiguous."""
        items: list[Any] = [s.fp for s in self.select(role=role, cluster=cluster, sheet=sheet, net=net)]
        if net is not None:
            items.extend(v for v in self.adapter.get_vias() if v.net_name == net)
            items.extend(t for t in self.adapter.get_tracks() if t.net_name == net)
        return items
