# tests/override_store_board_fixtures.py
"""The two-layer board stand-in the reload→snapshot cells are measured on.

Home of the instrument for TWO plans at once, deliberately in one module:

  * `plan_2026_09_24_reload_store_snapshot` (§5.2 Н1–Н7) — "a write into the
    override store must reach `connection.snapshot`";
  * the price question behind it (§2.3) — what does that reprojection cost the
    board?

WHY THE INSTRUMENT HAS TWO LAYERS. The claim under test is "the reprojection
costs ZERO board accesses", and it holds only because the adapter answers the
field reads from its own per-footprint map: `KiCadBoardAdapter._field_values_for`
(`kicadstamp/kicad/adapter.py:545`) scans a footprint's fields ONCE per cache
generation, keyed by uuid, and `refresh_board()` is the only thing that drops
that generation; `get_field_value` (`adapter.py:595`) and `has_field`
(`adapter.py:611`) are then dict hits on the same map.

A single counting fake would therefore count CACHE HITS and report a number
contradicting the promise — the 2026-09-22 lesson (Кk: measure on the layer where
the cache lives; the ~2 s scan is exactly what a naive instrument calls free).
So the layers are:

    Wire                  the BOARD, BELOW the cache: four doors, counted
                          separately (refresh_board, get_footprints, the
                          per-footprint FIELD SCAN, the PAD filter). Non-zero
                          here means board-shaped work;
    CachedAdapter         that per-generation field map, and nothing else;
    FieldOverrideAdapter  the REAL store layer;
    Board                 the REAL kicadstamp.explore.Board — production shape.

Counted separately on purpose (rule 35): "the reload did not re-read the board"
and "the reload did not re-scan the field map" are two different claims, and one
counter could not say which of them broke.
"""
from types import SimpleNamespace

from kicadstamp.adapter_factory import store_for_config
from kicadstamp.config.sexp_format import dict_to_sexp
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.explore import Board
from kicadstamp.field_override_adapter import FieldOverrideAdapter
from kicadstamp.field_overrides import SOURCE_CELL_TABLE, load_field_overrides
from kicadstamp.utils.paths import overrides_path_for_config

#: The symbol uuid the store is keyed by — the LAST hop of the fake footprint's
#: sheet-path chain (see kicadstamp.field_overrides.symbol_uuid_of).
SYMBOL_UUID = "uuid-R1"
FOOTPRINT_REF = "R1"
#: The role/cluster that PHYSICALLY lie on the board, so "effective" and
#: "physical" are tellable apart in a snapshot (Selected carries both).
ON_BOARD_ROLE = "ON_BOARD"
ON_BOARD_CLUSTER = "ON_BOARD_C"


class Wire:
    """The board, as seen from BELOW the adapter's field-map cache.

    Four doors, four counters — and a fifth fact worth naming: the pad door is a
    filter over items already in hand (`adapter.py:718`), so it is real work in
    the processor sense but NOT an IPC round-trip. The counters are the only
    thing this class offers besides `counts()`; the real price lives in what
    stays at zero."""

    def __init__(self, footprint, fields=None):
        self.footprint = footprint
        #: The field map of the ONE footprint this stand-in serves, exactly the
        #: shape `_scan_field_values` builds: name -> value ('' for an empty
        #: value; an ABSENT field is simply not a key).
        self.fields = dict(fields if fields is not None else {
            ROLE_FIELD_NAME: ON_BOARD_ROLE,
            CLUSTER_FIELD_NAME: ON_BOARD_CLUSTER,
        })
        self.refresh_calls = 0
        self.footprint_reads = 0
        self.field_scans = 0
        self.pad_filters = 0

    def refresh_board(self) -> None:
        self.refresh_calls += 1

    def get_footprints(self):
        self.footprint_reads += 1
        return [self.footprint]

    def scan_field_values(self, footprint):
        self.field_scans += 1
        return dict(self.fields)

    def get_footprint_pads(self, footprint):
        self.pad_filters += 1
        return ()

    def counts(self) -> dict:
        """{door: calls} — a plain dict so a cell can diff two readings, and so
        a failure names WHICH door moved (rule 35: separate rows, not one
        aggregate that hides the others)."""
        return {
            "refresh_board": self.refresh_calls,
            "get_footprints": self.footprint_reads,
            "field_scan": self.field_scans,
            "get_footprint_pads": self.pad_filters,
        }


class CachedAdapter:
    """`KiCadBoardAdapter`'s per-generation field map, and nothing else.

    Modelled on the real methods' docstrings (`adapter.py:545-577` for the map,
    `:595-623` for the two readers): keyed by the footprint's uuid, filled by ONE
    scan on the first read, dropped by `refresh_board()`, and shared by
    `get_field_value` and `has_field` — which is why the exists-question costs
    nothing extra. `get_footprints`/`get_footprint_pads` are straight pass-through
    (the real ones have no cache of their own)."""

    def __init__(self, wire):
        self._wire = wire
        self._field_values_cache: dict = {}

    def refresh_board(self) -> None:
        self._field_values_cache.clear()
        self._wire.refresh_board()

    def get_footprints(self):
        return self._wire.get_footprints()

    def get_footprint_pads(self, footprint):
        return self._wire.get_footprint_pads(footprint)

    def _field_values_for(self, footprint) -> dict:
        fp_uuid = getattr(footprint, "uuid", None)
        if not fp_uuid:
            # Uncached, like the real one: no stable key, no shared entry.
            return self._wire.scan_field_values(footprint)
        values = self._field_values_cache.get(fp_uuid)
        if values is None:
            values = self._wire.scan_field_values(footprint)
            self._field_values_cache[fp_uuid] = values
        return values

    def get_field_value(self, footprint, field_name):
        return self._field_values_for(footprint).get(field_name)

    def has_field(self, footprint, field_name) -> bool:
        return field_name in self._field_values_for(footprint)

    def close(self) -> None:
        """The seam's lifetime method — a worker adapter closes what it built."""
        pass


class CountingFace:
    """The adapter's face as the BOARD sees it: every question the snapshot asks,
    counted.

    This is the instrument for the BOARD-level caches (`_role_exists_cache`,
    `_cluster_exists_cache`, `_nets_cache`, `_sheet_cache`). The question there is
    "did the Board have to ask again?", and the count has to sit BELOW the cache
    being probed — between Board and the adapter — exactly as `Wire` sits below the
    adapter's own field map. Counting on `Wire` instead would answer a different
    question ("did the board get touched"), which for a warm adapter is always "no"
    and would therefore hide a dropped Board cache. Measured 2026-09-24: Н4 written
    against the cache DICTS stayed GREEN under a mutation that cleared all eight,
    because rebuilding the snapshot refills them anyway.

    Everything not defined here is delegated unchanged, so the layer underneath
    keeps every capability it had."""

    def __init__(self, adapter):
        self._adapter = adapter
        self.asks = {"has_field": 0, "get_field_value": 0, "get_field_values": 0,
                     "get_footprints": 0, "get_footprint_pads": 0,
                     "refresh_board": 0,
                     # Filled by the cell that probes the sheet chain through the
                     # module's own resolver — the only one of the four with no
                     # adapter call to count (see Н4).
                     "resolve_sheet_path_names": 0}

    def has_field(self, footprint, field_name):
        self.asks["has_field"] += 1
        return self._adapter.has_field(footprint, field_name)

    def get_field_value(self, footprint, field_name):
        self.asks["get_field_value"] += 1
        return self._adapter.get_field_value(footprint, field_name)

    def get_field_values(self, footprint, field_name):
        self.asks["get_field_values"] += 1
        return self._adapter.get_field_values(footprint, field_name)

    def get_footprints(self):
        self.asks["get_footprints"] += 1
        return self._adapter.get_footprints()

    def get_footprint_pads(self, footprint):
        self.asks["get_footprint_pads"] += 1
        return self._adapter.get_footprint_pads(footprint)

    def refresh_board(self):
        self.asks["refresh_board"] += 1
        return self._adapter.refresh_board()

    def __getattr__(self, name):
        return getattr(self._adapter, name)


class Wired:
    """Everything a cell needs, in one object: the connection, the REAL Board
    behind it, the layer, the TWO instruments (one below the adapter's field map,
    one between Board and the adapter), the profile and the bound store."""

    def __init__(self, connection, board, adapter, face, wire, profile, store):
        self.connection = connection
        self.board = board
        self.adapter = adapter          # the FieldOverrideAdapter (REAL)
        self.face = face                # its counted face — Board talks to THIS
        self.wire = wire                # the counting instrument (below it)
        self.profile = profile
        self.store = store

    @property
    def asks(self) -> dict:
        """{question: calls} as the BOARD asked them — see CountingFace."""
        return self.face.asks

    def role_in_snapshot(self, ref=FOOTPRINT_REF):
        """The role the SNAPSHOT serves for `ref` — or a loud sentinel when the
        snapshot does not carry the footprint at all.

        The two failures the cells must tell apart are "no rebuild happened"
        (nothing in the snapshot) and "a rebuild happened against stale caches"
        (the OLD role in a fresh snapshot), and a bare dict lookup would report
        both as a missing key."""
        for selected in self.connection.snapshot:
            if selected.ref == ref:
                return selected.role
        return "<absent from the snapshot>"

    def selected(self, ref=FOOTPRINT_REF):
        for selected in self.connection.snapshot:
            if selected.ref == ref:
                return selected
        return None

    def write_role(self, value, *, field=ROLE_FIELD_NAME,
                   source=SOURCE_CELL_TABLE) -> None:
        """Another holder records and saves — the shape the Refs table writes
        through: an object of its OWN over the same file, saved once.

        It does NOT reach the adapter: the bound store is a different object, and
        the whole point of the write event is that somebody has to RE-READ the
        file. Use `rebind_store()` for that step (production does it inside
        BoardConnection.reload_store, right before the reprojection)."""
        holder = load_field_overrides(overrides_path_for_config(str(self.profile)))
        holder.set(SYMBOL_UUID, FOOTPRINT_REF, field, value, source)
        holder.save()

    def rebind_store(self) -> None:
        """Re-read the store FILE into the layer — the step
        BoardConnection.reload_store performs before it reprojects.

        Kept separate from `write_role()` on purpose: the two are the two halves
        of that method (the file is the truth; the layer holds a copy), and a
        measurement that skips the rebinding measures nothing — it reports the
        PREVIOUS store's values while looking like it measured the new ones."""
        self.adapter.bind_store(load_field_overrides(
            overrides_path_for_config(str(self.profile))))


def board_footprint(*, ref=FOOTPRINT_REF, uuid="fp-uuid-R1",
                    symbol_uuid=SYMBOL_UUID):
    """A footprint every layer of the chain accepts: `ref` for Board._ref, `uuid`
    for the adapter's cache key (adapter.py:565) and the sheet-path chain both
    `symbol_uuid_of` (the store's key) and `resolve_sheet_path_names` read."""
    return SimpleNamespace(
        ref=ref,
        uuid=uuid,
        sheet_path=SimpleNamespace(path=[SimpleNamespace(value=symbol_uuid)]),
        sheet_path_uuids=(symbol_uuid,),
    )


def profile_file(tmp_path, *, name="prof.sexp", source=None):
    """A profile the store hangs off (`overrides/<stem>.fields.json`); `source`
    writes the role_cluster_source switch through the config writer's own
    serializer (a hand-written s-expr is not the format)."""
    profile = tmp_path / name
    profile.write_text(dict_to_sexp({"role_cluster_source": source} if source else {}),
                       encoding="utf-8")
    return profile


def wired_board(tmp_path, *, name="prof.sexp", source=None, bind=True,
                fields=None):
    """The production composition, one store bound and NO snapshot built yet.

    `Wire → CachedAdapter → FieldOverrideAdapter → Board`, inside a real
    `BoardConnection`, with the project's store bound through the layer's own
    `bind_store` — deliberately NOT through `connection.set_project_config`:
    that method is Н5's own subject, and once fixed it reprojects the snapshot
    itself, which would pre-warm the board caches and blur the cold/warm
    distinction Н1 and Н2 are built on.

    `board.refresh()` IS called (the poll's first read: the footprint list is in
    memory, every cache is empty) — so a cell that wants warmth must ask for it
    explicitly, and a cell that wants coldness gets it by construction."""
    from gui.connection import BoardConnection

    profile = profile_file(tmp_path, name=name, source=source)
    wire = Wire(board_footprint(), fields=fields)
    adapter = FieldOverrideAdapter(CachedAdapter(wire))
    face = CountingFace(adapter)
    board = Board(face, {})
    board.refresh()
    connection = BoardConnection()
    connection.board = board
    store = None
    if bind:
        store, resolved_source = store_for_config(str(profile))
        adapter.bind_store(store, source=resolved_source)
    return Wired(connection, board, adapter, face, wire, profile, store)
