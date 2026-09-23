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
from .adapter_factory import create_board_adapter
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
    an escape hatch to call any KiCadBoardAdapter method directly.

    role/cluster are the field VALUES (None when the field is missing OR
    empty — adapter.get_field_value returns None for both, see its own
    docstring). role_field_exists/cluster_field_exists are the separate
    "does the footprint have this field AT ALL" facts, read via
    adapter.has_field() — Pending changes needs them to avoid treating a
    physically-absent field as a diff to apply (see
    handoff_2026_08_27_pending_exclude_missing_board_fields). Default True
    so any manually-constructed Selected (tests, other callers) that
    doesn't care about this scenario is unaffected.

    TWO values per field, since Т5г (plan_2026_09_18_field_overrides_store):

      * `role`/`cluster` are the values IN FORCE — OUR stored value when the
        store has one, the board's otherwise. This is what the GUI reads: the
        role pickers must offer a role someone noted only in KiCadStamp, or it
        cannot be addressed by role at all;
      * `board_role`/`board_cluster` are what PHYSICALLY lies on the board. The
        diff's Board column needs this one (С27) — otherwise Pending would
        compare the store with itself and stop being a diff.

    Both come from ONE read (`FieldOverrideAdapter.get_field_values`, С28): the
    layer holds them together, so carrying the pair costs nothing and the two can
    never describe different instants. A hand-built Selected (tests, other
    callers) has no second truth, so `__post_init__` fills the board side from
    the effective one — which is exactly the pre-store world.
    """
    ref: str
    role: str | None
    cluster: str | None
    sheet: list[str | None]     # full resolve_sheet_path_names() chain
    nets: dict[str, str]           # pad number -> net name
    fp: Footprint = field(repr=False)
    # Default True (the trailing default fields — kept AFTER the required
    # ones so a manually-constructed Selected without them is unaffected).
    role_field_exists: bool = True
    cluster_field_exists: bool = True
    # The PHYSICAL board values (Т5г). None means "not told apart from the
    # effective value" — see __post_init__.
    board_role: str | None = None
    board_cluster: str | None = None

    def __post_init__(self) -> None:
        """A Selected built by hand (or by a caller that only knows one truth)
        answers both questions the same way — the single-truth world this class
        lived in before Т5г, kept so every existing construction stays honest."""
        if self.board_role is None:
            self.board_role = self.role
        if self.board_cluster is None:
            self.board_cluster = self.cluster

    @property
    def role_from_store(self) -> bool:
        """True when the role IN FORCE is not the one on the board — i.e. it came
        from the override store. This is the entire mark the Components tree shows
        (С26): "effective ≠ physical" needs no extra bookkeeping."""
        return self.role != self.board_role

    @property
    def cluster_from_store(self) -> bool:
        """Same for the cluster (С26)."""
        return self.cluster != self.board_cluster


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
        # The PHYSICAL side of the same reads (Т5г): filled together with the
        # caches above, from ONE call each — see _both().
        self._board_role_cache: dict[str, str | None] = {}
        self._board_cluster_cache: dict[str, str | None] = {}
        self._role_exists_cache: dict[str, bool] = {}
        self._cluster_exists_cache: dict[str, bool] = {}
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
        # The profile rides along: the GUI's own poll adapter (and every other
        # Board.connect caller) then honours the override store
        # (plan_2026_09_18_field_overrides_store Т2).
        adapter = create_board_adapter(timeout_ms=timeout_ms, config_path=config_path)
        # Gate on EITHER source (2026-09-11, plan project_settings_single_source
        # Этап 2): the GUI's RootMetadataDock now fills schematic_files and
        # CLEARS schematic_dir, so gating on schematic_dir alone would silently
        # return an empty name map even though schematic_files is populated.
        sheet_names = (build_sheet_name_map(config_path, schematic_dir, schematic_files or [])
                       if (schematic_dir or schematic_files) else {})
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
        self._board_role_cache.clear()
        self._board_cluster_cache.clear()
        self._role_exists_cache.clear()
        self._cluster_exists_cache.clear()
        self._nets_cache.clear()
        self._sheet_cache.clear()

    def forget_role_cluster_values(self) -> None:
        """Forget the Role/Cluster values this snapshot holds, WITHOUT reading
        the board (plan_2026_09_24_reload_store_snapshot §3.1).

        When the override STORE changes — our own write, or a project switch —
        the board is exactly as it was while the values IN FORCE are not: the two
        role/cluster pairs cached here were resolved against the store that was
        bound at the time, so a snapshot rebuilt out of these caches would
        describe a store nobody has any more. This drops that resolution and
        nothing else; the caller then rebuilds (BoardConnection._rebuild_snapshot)
        and the reads go to the freshly bound store.

        WHICH CACHES, and why exactly these four. Eight derived caches live on
        this object (see __init__), plus the footprint list itself:

          * `_role_cache` / `_cluster_cache` — the values IN FORCE, i.e. THE
            store-dependent pair. Dropped;
          * `_board_role_cache` / `_board_cluster_cache` — the PHYSICAL values,
            filled by the SAME statement as the pair above (`_both`: one board
            read per field, both truths kept together). Dropped WITH them, as a
            pair — see the trap below;
          * `_role_exists_cache` / `_cluster_exists_cache` — LEFT ALONE. They
            answer "does this footprint carry the field at all", and the override
            layer deliberately answers THAT about the board
            (field_override_adapter.py's has_field), so the store cannot make
            them stale;
          * `_nets_cache` / `_sheet_cache` — LEFT ALONE. Nets and sheet chains do
            not depend on the store either, and `_nets` builds one `Pad` object
            per footprint: real processor work for no new information;
          * `_footprints` (the list) — NEVER. Dropping it means
            `adapter.get_footprints()`, i.e. the full IPC round-trip this method
            exists to avoid.

        THE TRAP, because these four are TWO PAIRS and not four independent
        cells. Dropping the PHYSICAL pair alone is not a redundant flush but a
        crash: `_board_role()` calls `_role()` first, and with `_role_cache` still
        warm that returns at once, leaving the physical lookup to index a key
        nobody will refill — a KeyError in the middle of a snapshot. Dropping the
        EFFECTIVE pair alone is behaviourally sufficient for the values in force
        (`_role()` writes both halves in one statement), and that is exactly why
        this method does all four: the next person to "optimise it down to two
        caches" must not be free to pick the pair that falls over. Both readings
        were measured — plan §5.3а.

        By construction it costs ZERO accesses to KiCad: the four dictionaries are
        in memory, `_footprints` is kept, and the adapter's own per-footprint
        field map is untouched (it holds the BOARD's values, which did not
        change)."""
        self._role_cache.clear()
        self._cluster_cache.clear()
        self._board_role_cache.clear()
        self._board_cluster_cache.clear()

    @staticmethod
    def _ref(fp: Footprint) -> str:
        return fp.ref

    def _both(self, fp: Footprint, field_name: str) -> tuple:
        """(effective, physical) for one field — from ONE board read (Т5г/С28).

        The override layer is the only place where both truths are ever in hand
        at once (it reads the board's value before deciding whether ours wins), so
        asking it for the pair costs nothing and spares this snapshot a SECOND
        pass over the footprints — and a second pass would describe a board that
        had already moved on, which is exactly why the plan rejected "two
        snapshots" outright.

        A BARE adapter has one truth, so the physical value IS the effective one:
        the pre-store world, kept structurally rather than by luck (golden С2)."""
        # On the TYPE, not the instance: a Mock (or any dynamic stand-in) answers
        # every attribute name with a Mock, and unpacking THAT is a ValueError in
        # the middle of a snapshot. Asking the class "does this adapter implement
        # the pair-read?" is the honest question anyway.
        getter = getattr(type(self.adapter), "get_field_values", None)
        if getter is None:
            value = self.adapter.get_field_value(fp, field_name)
            return (value, value)
        return getter(self.adapter, fp, field_name)

    def _role(self, fp: Footprint) -> str | None:
        """The Role IN FORCE (ours when the store has one), as before."""
        ref = self._ref(fp)
        if ref not in self._role_cache:
            self._role_cache[ref], self._board_role_cache[ref] = self._both(fp, ROLE_FIELD_NAME)
        return self._role_cache[ref]

    def _cluster(self, fp: Footprint) -> str | None:
        """The Cluster IN FORCE (ours when the store has one), as before."""
        ref = self._ref(fp)
        if ref not in self._cluster_cache:
            self._cluster_cache[ref], self._board_cluster_cache[ref] = self._both(
                fp, CLUSTER_FIELD_NAME)
        return self._cluster_cache[ref]

    def _board_role(self, fp: Footprint) -> str | None:
        """The Role PHYSICALLY on the board — the diff's Board column (С27).

        Deliberately not a second read: `_role` above already filled both halves
        of the pair in one call."""
        self._role(fp)
        return self._board_role_cache[self._ref(fp)]

    def _board_cluster(self, fp: Footprint) -> str | None:
        """The Cluster PHYSICALLY on the board — see _board_role."""
        self._cluster(fp)
        return self._board_cluster_cache[self._ref(fp)]

    def _role_exists(self, fp: Footprint) -> bool:
        """Does this footprint have the Role field AT ALL (vs. the value
        being None/empty) — adapter.has_field() on the SAME fp object the
        value read already has, no extra IPC (see Selected's docstring on
        why the exists-fact is needed separately from the value)."""
        ref = self._ref(fp)
        if ref not in self._role_exists_cache:
            self._role_exists_cache[ref] = self.adapter.has_field(fp, ROLE_FIELD_NAME)
        return self._role_exists_cache[ref]

    def _cluster_exists(self, fp: Footprint) -> bool:
        ref = self._ref(fp)
        if ref not in self._cluster_exists_cache:
            self._cluster_exists_cache[ref] = self.adapter.has_field(fp, CLUSTER_FIELD_NAME)
        return self._cluster_exists_cache[ref]

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
            result.append(Selected(
                ref=fp_ref, role=fp_role, cluster=fp_cluster,
                role_field_exists=self._role_exists(fp),
                cluster_field_exists=self._cluster_exists(fp),
                sheet=fp_sheet, nets=fp_nets, fp=fp,
                # The board's own values, from the SAME reads above (Т5г):
                # role/cluster stay the values in force for the pickers, these
                # two are what the Board column must show (С27).
                board_role=self._board_role(fp),
                board_cluster=self._board_cluster(fp)))
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
