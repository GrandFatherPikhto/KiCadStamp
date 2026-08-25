# kicadstamp/kicad/adapter.py

import time
import logging
from contextlib import contextmanager
from typing import Any
import kipy
from kipy.board_types import BoardLayer as KipyBoardLayer, Field, Group, Pad as KipyPad, Track as KipyTrack, Via as KipyVia, ViaType
from kipy.geometry import Vector2 as KipyVector2, Angle as KipyAngle

from ..domain.geometry import BoardLayer, Box2, Vector2
from kipy.proto.board import board_commands_pb2
from kipy.errors import FutureVersionError

from .interfaces import IBoardAdapter
from ..domain.board import (
    Footprint,
    Net,
    Pad,
    Track,
    Via,
    Zone,
    board_item_from_kipy,
    footprint_from_kipy,
    net_from_kipy,
    pad_from_kipy,
    track_from_kipy,
    unwrap,
    via_from_kipy,
    zone_from_kipy,
)
from ..exceptions import BoardNotFoundError, ComponentNotFoundError, ValidationError, format_fatal_error
from ..utils.units import MM
from ..constants import DEFAULT_TIMEOUT_MS
from ..i18n import _

# Patches pynng.nng.Socket.close with an external timeout on import (see its
# docstring) — must run before any kipy/pynng socket is created, so this is
# the single chokepoint every entry point (GUI, CLI, author_cli, tests) goes
# through just by importing this module.
from . import pynng_safety  # noqa: F401

logger = logging.getLogger(__name__)


class KiCadBoardAdapter(IBoardAdapter):
    def __init__(self, timeout_ms: int = DEFAULT_TIMEOUT_MS):
        logger.debug(_("Initialising KiCadBoardAdapter with timeout {timeout} ms").format(timeout=timeout_ms))
        logger.debug(_("Creating kipy.KiCad instance..."))
        self._kicad = kipy.KiCad(timeout_ms=timeout_ms)
        logger.debug(_("kipy.KiCad instance created"))
        # Connects to a fixed default IPC socket (unless KICAD_API_SOCKET
        # overrides it) — with several native/AppImage KiCad builds
        # installed side by side, it's easy to end up silently talking to
        # the wrong one. Log what we actually got, and warn (non-fatal) if
        # it's newer than the KiCad version kipy was generated against —
        # found 2026-08-09 chasing a footprint-move pad-rotation corruption
        # that only reproduced against a KiCad nightly far ahead of kipy's
        # pinned API version.
        try:
            live_version = self._kicad.get_version()
            logger.info(_("Connected to KiCad {version}").format(version=live_version))
            try:
                self._kicad.check_version()
            except FutureVersionError as exc:
                logger.warning(str(exc))
        except Exception:
            logger.debug(_("Could not query KiCad version after connect"), exc_info=True)
        self._board = None
        self._write_risk_checked = False
        self._footprints_cache: list[Footprint] | None = None
        # get_selected_items() is polled every ~400ms by the GUI's live-
        # selection timer (see main_window.py's _poll_board_selection) —
        # logging its count unconditionally at DEBUG flooded the log file
        # with a "Selected items...: 0" line several times a second,
        # burying anything useful around a specific Redraw (found 2026-08-06,
        # while trying to dig a real placement bug out of the log). Only log
        # when the count actually changes since the last call.
        self._last_selection_log_count: int | None = None
        # Settable by the caller (see kicadstamp_cli.py's --no-selection) —
        # makes get_selected_items() always report "nothing selected",
        # regardless of what's actually highlighted in the PCB editor GUI.
        # ClonePlacement's "by selection" mode (role:/cell: without nets/
        # params) and the selection-narrowing step in _narrow_ambiguous_
        # candidates/resolve_footprint_by_role both read the selection as
        # part of a normal resolution cascade — a stray leftover mouse
        # selection from earlier browsing then fatals or silently changes
        # which candidate wins, with no indication it came from the GUI, not
        # the config. This flag lets a whole apply run opt out of that input.
        self.ignore_selection = False

    @contextmanager
    def temporarily_ignore_selection(self, active: bool):
        """
        Force ignore_selection True for the duration of the `with` block when
        active is True; a no-op (state left exactly as it was) when False.
        OR-composes with an already-True ignore_selection (e.g. from
        --no-selection) rather than overriding it back off — restores
        whatever the previous value was, not unconditionally False.

        See ClonePlacement.ignore_selection (config/models.py) — the
        per-item counterpart of --no-selection, applied around just one
        clone_placement's own anchor/role resolution (clone_position_
        calculator.py, dependency_order.py) instead of the whole run.
        """
        if not active:
            yield
            return
        previous = self.ignore_selection
        self.ignore_selection = True
        try:
            yield
        finally:
            self.ignore_selection = previous

    def refresh_board(self):
        logger.debug(_("Refreshing board from KiCad"))
        self._board = self._kicad.get_board()
        if self._board is None:
            raise BoardNotFoundError(_("Failed to obtain board from KiCad"))
        self._footprints_cache = None
        logger.info(_("Board obtained"))

    def get_board_filename(self) -> str | None:
        """Filename of the live board's .kicad_pcb (e.g. '3CH-AWG-TIA-v102.
        kicad_pcb'), or None if not connected. Source: kipy's Board.name
        (board.py:280-283), which reads DocumentSpecifier.board_filename off
        the live IPC connection. NOTE (empirical, to verify live): whether this
        returns the bare filename or a full path depends on what KiCad's IPC
        layer puts in DocumentSpecifier.board_filename — callers must compare
        by basename stem only (see check_board_identity), never assume the
        shape. Exposed here because nothing outside the adapter could see the
        live board's identity before (validation's check_board_identity needs
        it to fatal early when the config targets a different board)."""
        if self._board is None:
            return None
        return self._board.name

    def close(self) -> None:
        """Explicitly closes the underlying kipy client's pynng socket
        instead of leaving that to the garbage collector. Found live
        (2026-08-04): a silent Windows access violation with NO Python frame
        on the crashing thread (pure native code) — the GUI creates a brand
        new kipy.KiCad() (and pynng.Req0 socket) on every reconnect
        (BoardConnection.connect(), called every time KiCad drops and comes
        back), but never closed the previous one. Across many reconnects in
        one long-lived GUI session, several live sockets pile up and get
        finalized by the GC at unpredictable points on unpredictable
        threads — a plausible trigger for a native crash in a C-extension
        async socket library (pynng). kipy 0.7.1 exposes no public close()
        on KiCad/KiCadClient (checked kicad.py/client.py directly) — this
        reaches into KiCadClient's private _conn, so failure here must never
        propagate (the socket may already be broken, which is often exactly
        why this is being called)."""
        client = getattr(self._kicad, "_client", None)
        if client is None or not getattr(client, "_connected", False):
            return
        conn = getattr(client, "_conn", None)
        if conn is None:
            return
        try:
            conn.close()
        except Exception:
            logger.debug(_("Closing previous kipy connection failed (ignored)"), exc_info=True)

    # --- Search ---
    def get_footprint(self, ref: str) -> Footprint | None:
        for fp in self.get_footprints():
            if fp.ref == ref:
                logger.debug(_("Found footprint {ref}").format(ref=ref))
                return fp
        logger.debug(_("Footprint {ref} not found").format(ref=ref))
        return None

    def get_footprint_by_id(self, uuid_str: str) -> Footprint | None:
        """Refdes-independent lookup, for identity that must survive
        re-annotation (undo.py's move log — see move_executor.py's uuid
        capture)."""
        for fp in self.get_footprints():
            if fp.uuid == uuid_str:
                logger.debug(_("Found footprint by uuid {uuid}").format(uuid=uuid_str))
                return fp
        logger.debug(_("Footprint with uuid {uuid} not found").format(uuid=uuid_str))
        return None

    def get_items_by_id(self, uuid_strs: list[str]) -> list[Any]:
        """
        Point lookups by UUID for arbitrary board items (via/track/...), in
        ONE IPC round trip via kipy's ``Board.get_items_by_id`` (added in
        kipy 0.7.0 / KiCad 10.0.0 — the same GetItemsById command
        ``remove_by_id`` already uses for the delete direction). The
        multi-UUID counterpart of get_footprint_by_id(), which resolves
        refdes-independent identity for copper the same way it already does
        for footprints. UUIDs with no live item are simply absent from the
        result (KiCad returns only what exists) — not an error, mirroring
        the registry's own "a stale UUID is not an error" stance
        (registry.py's reconcile).
        """
        from kipy.proto.common.types import base_types_pb2 as common_types_pb2

        kiids = []
        for uuid_str in uuid_strs:
            kiid = common_types_pb2.KIID()
            kiid.value = uuid_str
            kiids.append(kiid)
        if not kiids:
            return []
        try:
            raw = self._board.get_items_by_id(kiids)
        except Exception as e:
            logger.warning(_("Failed to look up items by id: {type}: {e}")
                           .format(type=type(e).__name__, e=e))
            return []
        return [board_item_from_kipy(item) for item in raw]

    def get_footprints(self) -> list[Footprint]:
        """
        Cached per board generation (cleared by refresh_board()). Anchor/role
        resolution (clone_role_resolver.py), ComponentPool, dependency_order.py
        (which resolves every rule/clone_placement's anchor TWICE — once to
        build the dependency graph, once again to actually plan it) and the
        collision check in move_executor.py each used to call this fresh,
        every time — a single apply run over ~20 rules/clone_placements meant
        dozens of full-board IPC round trips for data that cannot have changed
        since the last explicit refresh_board() (every place in this codebase
        that moves/creates something already calls refresh_board() before the
        next read that needs to see it — see cmd_apply's per-item loop and the
        moves -> vias -> tracks phase boundaries). Confirmed 2026-07-29 by
        reading the call graph (dependency_order.py alone doubles every
        anchor/role resolution); not re-profiled live since the redundancy is
        structural, not data-dependent.
        """
        if self._footprints_cache is None:
            self._footprints_cache = [footprint_from_kipy(fp) for fp in self._board.get_footprints()]
            logger.debug(_("Retrieved {count} footprints").format(count=len(self._footprints_cache)))
        return list(self._footprints_cache)

    def get_vias(self) -> list[Via]:
        vias = [via_from_kipy(v) for v in self._board.get_vias()]
        logger.debug(_("Retrieved {count} vias").format(count=len(vias)))
        return vias

    def get_tracks(self) -> list[Track]:
        tracks = [track_from_kipy(t) for t in self._board.get_tracks()]
        logger.debug(_("Retrieved {count} tracks").format(count=len(tracks)))
        return tracks

    def get_selected_items(self) -> list[Any]:
        """
        Current selection in PCB editor, taking Groups into account — Group's
        .items property as received from the server is ALWAYS EMPTY (just a
        local cache wrapper); actual group members are in .proto.items (list of
        KIID). Expand groups into their real members by matching IDs against all
        footprints/vias on the board.

        self.ignore_selection short-circuits this to always report "nothing
        selected" — see its docstring in __init__.
        """
        if self.ignore_selection:
            return []
        raw_selection = list(self._board.get_selection())
        direct_items = [board_item_from_kipy(item) for item in raw_selection if not isinstance(item, Group)]
        group_uuids = set()
        for item in raw_selection:
            if isinstance(item, Group):
                for kiid in item.proto.items:
                    group_uuids.add(str(kiid.value))

        if group_uuids:
            for fp in self.get_footprints():
                if fp.uuid in group_uuids:
                    direct_items.append(fp)
            for via in self.get_vias():
                if via.uuid in group_uuids:
                    direct_items.append(via)

        if len(direct_items) != getattr(self, "_last_selection_log_count", None):
            logger.debug(_("Selected items (including groups expanded): {count}").format(count=len(direct_items)))
            self._last_selection_log_count = len(direct_items)
        return direct_items

    def select_items(self, items: list[Any]):
        """
        Sets the PCB editor's GUI selection to exactly `items` (replacing
        whatever was selected before) — the write counterpart of
        get_selected_items(). Same clear_selection()+add_to_selection() pair
        flip_selected() already uses, pulled out as its own method for
        callers (the GUI's Role/Cluster tree, "click a node -> highlight on
        board") that just want the highlight, not a GUI action run afterwards.
        Not a mutating/undo-able board edit — selection is editor UI state,
        not board data — so no begin_commit()/push_commit() around it.
        """
        logger.debug(_("Setting GUI selection to {count} items").format(count=len(items)))
        self._board.clear_selection()
        if items:
            self._board.add_to_selection([unwrap(i) for i in items])

    def get_field_value(self, footprint: Footprint, field_name: str) -> str | None:
        """
        Value of a custom component field (e.g., Role for KiCadStamp 4.0).
        IMPORTANT: texts_and_fields contains a mix of actual Field objects
        (name+text.value) and plain BoardText (silkscreen text without a field
        name at all) — filter by type, otherwise we get AttributeError on .name
        for BoardText.
        """
        for item in unwrap(footprint).texts_and_fields:
            if isinstance(item, Field) and item.name == field_name:
                return item.text.value if item.text else None
        return None

    def has_field(self, footprint: Footprint, field_name: str) -> bool:
        """True if footprint carries a field with this name at all — unlike
        get_field_value(), which returns None both for "field missing" and
        for "field present but empty", this distinguishes the two so a
        caller can skip a footprint instead of hitting set_field_value's
        fatal ValidationError mid-batch."""
        return any(isinstance(item, Field) and item.name == field_name
                   for item in unwrap(footprint).texts_and_fields)

    def set_field_value(self, footprint: Footprint, field_name: str, value: str) -> None:
        """
        Write counterpart of get_field_value() — sets a custom footprint
        field's text value IN PLACE. Does not by itself push anything to
        KiCad; the caller still needs update_items([footprint]) inside a
        commit (see set_field_values_bulk below), same as any other
        footprint mutation (see undo.py's fp.position = ...; update_items()
        pattern).

        MUST mutate item.text.value on the Field object found in
        footprint.texts_and_fields — NOT replace it with a fresh Field(...).
        kipy's Footprint.items unwraps each proto item via Field(proto=...)
        (board_types.py's unwrap()), which — unlike reference_field/
        value_field, which use proto_ref= — copies into a brand-new detached
        proto. That copy IS the one object definition.items/texts_and_fields
        keeps around afterwards (Footprint._unwrapped_items), and
        Wrapper.proto's getter calls _pack() before returning, which
        re-serializes _unwrapped_items back into the footprint's proto — so
        mutating the found Field's own .text.value here (not swapping in an
        unrelated new Field) is what update_items() actually sees.

        Fatal (ValidationError) if the footprint has no field with this
        name — same "fatal, not silent" discipline as the rest of the
        project; there is no sensible default for "create a new field from
        scratch" (KIID/position/layer/schematic-symbol sync are all unknown
        here), so this never attempts it.
        """
        for item in unwrap(footprint).texts_and_fields:
            if isinstance(item, Field) and item.name == field_name:
                item.text.value = value
                return
        ref = footprint.ref
        raise ValidationError(format_fatal_error(
            _("cannot set field {field!r}").format(field=field_name),
            [_("{ref} has no field {field!r} on its footprint — add the field once in "
               "the schematic/footprint editor first, this tool never creates one from scratch")
             .format(ref=ref, field=field_name)]
        ))

    def set_field_values_bulk(self, updates: list[Any], description: str) -> bool:
        """
        updates: list of (footprint, field_name, value) triples. Sets them
        all, then pushes every touched footprint in ONE update_items() call
        inside ONE commit — so Ctrl+Z in KiCad undoes the whole batch at
        once (e.g. "set Role on 5 components"), not one undo step per
        component. Reuses commit_with_retry's begin_commit/work_fn/
        push_commit/retry-on-busy pattern.

        Raises ValidationError (propagated from set_field_value, via
        commit_with_retry) without writing anything if ANY footprint in the
        batch is missing the target field — begin_commit()/drop_commit()
        wraps the whole batch, so a mid-batch failure rolls back the ones
        already mutated in this Python process too (they were never sent).

        FIXED (2026-08-03): `touched` used to be built OUTSIDE work() and
        with one plain append() per (footprint, field, value) triple. Any
        footprint with more than one field in this same batch (Role AND
        Cluster — true of every Clear all/Delete selected call, and of any
        Stage that sets both) was appended more than once, so update_items()
        received the SAME FootprintInstance object multiple times in one
        list — found live: this duplicated the physical footprint on the
        real board once per repeat entry (reproduced in isolation: writing
        the identical object twice via update_items() creates a second
        footprint instead of no-op updating the one that's already there).
        Same reasoning made `touched` a local of work(), not the enclosing
        function — commit_with_retry() can call work() again on a retry,
        and the old outer-scope list kept every previous attempt's entries,
        doubling up again on top of the first bug on any retried batch.
        """

        def work():
            touched = []
            seen = set()
            for footprint, field_name, value in updates:
                self.set_field_value(footprint, field_name, value)
                if id(footprint) not in seen:
                    seen.add(id(footprint))
                    touched.append(footprint)
            self.update_items(touched)

        return self.commit_with_retry(description, work)

    def get_footprint_pads(self, footprint: Footprint) -> list[Pad]:
        """
        Returns the list of pads of this footprint. Does not go to the API
        separately — pads are already in footprint.definition.items together
        with fields/graphics; just filter by type. Moved here from planner.py
        to avoid duplication in future places (e.g., keepout building).
        """
        return [pad_from_kipy(item) for item in unwrap(footprint).definition.items if isinstance(item, KipyPad)]

    def get_pad_by_number(self, footprint: Footprint, pad_number: str) -> Pad | None:
        """Finds a specific pad of a footprint by number (e.g., '1', '145')."""
        for pad in self.get_footprint_pads(footprint):
            if pad.number == pad_number:
                return pad
        return None

    def get_zone_by_name(self, name: str) -> Zone | None:
        for z in self._board.get_zones():
            if z.name == name:
                logger.debug(_("Found zone {name}").format(name=name))
                return zone_from_kipy(z)
        logger.debug(_("Zone {name} not found").format(name=name))
        return None

    def get_net_by_name(self, name: str) -> Net | None:
        for n in self._board.get_nets():
            if n.name == name:
                logger.debug(_("Found net {name}").format(name=name))
                return net_from_kipy(n)
        logger.debug(_("Net {name} not found").format(name=name))
        return None

    def get_all_nets(self) -> list[Net]:
        nets = [net_from_kipy(n) for n in self._board.get_nets()]
        logger.debug(_("Retrieved {count} nets").format(count=len(nets)))
        return nets

    # 'grid' (Place > Set Grid Origin, visual only) vs 'drill' (Place >
    # Drill/Place Origin, the auxiliary axis — the actual zero used by
    # drill/position files, and optionally Gerbers via their own "use drill/
    # place file origin" plot option). Used by Point's anchor_origin
    # (config/points.py) — see placement/services/point_resolver.py.
    _BOARD_ORIGIN_KINDS = {"grid": board_commands_pb2.BOT_GRID, "drill": board_commands_pb2.BOT_DRILL}

    def get_board_origin(self, kind: str) -> Vector2:
        v = self._board.get_origin(self._BOARD_ORIGIN_KINDS[kind])
        return Vector2(v.x, v.y)

    # --- Bounding boxes (for collisions — see collision.py) ---
    def get_bounding_boxes(self, items) -> list[Box2 | None]:
        """
        Returns bounding boxes (Box2 | None) for a list of items in ONE request.
        Board.get_item_bounding_box(list) returns List[Optional[Box2]] for a
        sequence of items (for a single item it would return just Box2|None —
        so we always pass a list here).
        """
        if not items:
            return []
        result = self._board.get_item_bounding_box([unwrap(i) for i in items])
        # Defensive normalisation in case it's not a list
        if not isinstance(result, list):
            result = [result]
        converted = []
        for box in result:
            if box is None:
                converted.append(None)
            else:
                converted.append(Box2(pos=Vector2(box.pos.x, box.pos.y),
                                      size=Vector2(box.size.x, box.size.y)))
        return converted

    # --- Transactions ---
    def begin_commit(self):
        logger.debug(_("Beginning transaction"))
        return self._board.begin_commit()

    def push_commit(self, commit, description: str):
        logger.debug(_("Committing transaction: {desc}").format(desc=description))
        self._board.push_commit(commit, description)
        logger.info(_("Transaction committed: {desc}").format(desc=description))

    def drop_commit(self, commit):
        logger.warning(_("Rolling back transaction"))
        self._board.drop_commit(commit)

    def check_write_crash_risk(self):
        """
        Check before the FIRST mutating operation: KiCad 10.0.4 may crash
        entirely on the first API write if the schematic editor is open and no
        interactive edit has been made in the session (null‑deref in
        _eeschema.dll, our report:
        https://gitlab.com/kicad/code/kicad/-/issues/24966).
        Cannot prevent the crash from the client, but can warn.
        Called once; subsequent calls are no‑op.
        """
        if self._write_risk_checked:
            return
        self._write_risk_checked = True
        try:
            from kipy.proto.common.types import DocumentType
            schematics = self._kicad.get_open_documents(DocumentType.DOCTYPE_SCHEMATIC)
        except Exception as e:
            logger.debug(_("Checking open schematics failed: {e}").format(e=e))
            return
        if schematics:
            logger.warning(
                _("Schematic editor is open: if there have been no interactive edits "
                  "in this KiCad session, the first API write may crash KiCad "
                  "(issue #24966). Workaround: move any component + Ctrl+S in "
                  "pcbnew, or close the schematic window during the run.")
            )

    def _mutating_call(self, op_name: str, fn, retries: int = 2, backoff_s: float = 1.5):
        """
        Wrapper for mutating calls: before first — check_write_crash_risk,
        on ApiError 'not ready' — retry with backoff (KiCad busy with modal
        state), on ConnectionError — clear diagnosis instead of raw stack trace:
        pipe break during write = KiCad probably crashed (see issue #24966).
        """
        self.check_write_crash_risk()
        last_exc = None
        for attempt in range(1 + retries):
            try:
                return fn()
            except kipy.errors.ApiError as e:
                if "not ready" in str(e).lower() and attempt < retries:
                    wait = backoff_s * (attempt + 1)
                    logger.warning(_("{op}: KiCad not ready to respond "
                                     "(busy/modal dialog?), retrying in {wait:.1f}s "
                                     "[{attempt}/{retries}]")
                                   .format(op=op_name, wait=wait,
                                           attempt=attempt+1, retries=retries))
                    time.sleep(wait)
                    last_exc = e
                    continue
                raise
            except kipy.errors.ConnectionError as e:
                logger.error(
                    _("{op}: connection to KiCad broke during write — "
                      "KiCad probably crashed (known crash on first API write "
                      "with schematic open: issue #24966; workaround: move "
                      "a component + Ctrl+S in pcbnew before running). Original error: {e}")
                    .format(op=op_name, e=e)
                )
                raise
        raise last_exc

    def _sync_dto_to_kipy(self, dto, kipy_item) -> None:
        """Copy consumer-side DTO mutations back onto the live kipy object.

        Only ``Footprint`` is mutated by consumers before ``update_items``
        (move_executor.py sets ``position``/``angle_deg``; undo.py sets
        ``position``/``angle_deg``). Via/Track DTOs are created, not mutated;
        field writes (``set_field_value``) mutate the kipy object in place.
        """
        if isinstance(dto, Footprint):
            kipy_item.position = KipyVector2.from_xy(dto.position.x, dto.position.y)
            kipy_item.orientation = KipyAngle.from_degrees(dto.angle_deg)
            kipy_item.layer = (KipyBoardLayer.BL_B_Cu if dto.layer == BoardLayer.BL_B_Cu
                               else KipyBoardLayer.BL_F_Cu)

    def update_items(self, items):
        logger.debug(_("Updating {count} items").format(count=len(items)))
        kipy_items = []
        for dto in items:
            kipy_item = unwrap(dto)
            self._sync_dto_to_kipy(dto, kipy_item)
            kipy_items.append(kipy_item)
        return self._mutating_call("update_items",
                                   lambda: self._board.update_items(kipy_items))

    def create_items(self, items):
        logger.debug(_("Creating {count} items").format(count=len(items)))
        created = self._mutating_call("create_items",
                                      lambda: self._board.create_items([unwrap(i) for i in items]))
        created_dtos = [board_item_from_kipy(item) for item in created]
        logger.debug(_("Created {count} items").format(count=len(created_dtos)))
        return created_dtos

    # --- Specialised actions ---
    def flip_selected(self, footprints: list[Footprint]):
        logger.info(_("Flipping {count} footprints via GUI action").format(count=len(footprints)))
        self._board.clear_selection()
        self._board.add_to_selection([unwrap(fp) for fp in footprints])
        self._kicad.run_action("pcbnew.InteractiveEdit.flip")
        self._board.clear_selection()
        # The flip happens server-side via a GUI action — unlike update_items,
        # it does NOT touch the local FootprintInstance objects' .layer, so
        # the get_footprints() cache (see there) is now stale: any cached fp
        # still reports its PRE-flip layer. flip_manager.flip_if_needed()
        # relies on re-fetching after this call to see the real post-flip
        # layer (otherwise its stale fp objects get pushed straight back via
        # update_items(), silently undoing the flip — found live 2026-07-29:
        # fpga_oscill_r_pi_filter's components landing back on F.Cu).
        self._footprints_cache = None
        logger.debug(_("Flip performed"))

    def commit_with_retry(self, description: str, work_fn, retries: int = 1) -> bool:
        """
        FIXED (2026-07-12): previously `commit = self.begin_commit()` was
        inside try, but if begin_commit() ITSELF crashed (real reproducible
        scenario — see history of stuck IPC session and "KiCad is busy"),
        `commit` remained UNDEFINED, and `except: self.drop_commit(commit)`
        crashed with UnboundLocalError, completely masking the real cause.
        Now commit=None before try, drop_commit is called only if commit was
        actually obtained.
        """
        last_exc = None
        for attempt in range(retries + 1):
            commit = None
            try:
                logger.debug(_("Attempt {attempt}/{total} for {desc}")
                             .format(attempt=attempt+1, total=retries+1, desc=description))
                commit = self.begin_commit()
                work_fn()
                self.push_commit(commit, description)
                return True
            except Exception as e:
                last_exc = e
                if commit is not None:
                    try:
                        self.drop_commit(commit)
                    except Exception as drop_exc:
                        logger.error(_("Failed to roll back transaction {desc}: {e}")
                                     .format(desc=description, e=drop_exc))
                logger.warning(_("Error in transaction {desc} (attempt {attempt}): {type}: {e}")
                               .format(desc=description, attempt=attempt+1,
                                       type=type(e).__name__, e=e))
                if attempt == retries:
                    raise
                time.sleep(0.5)
        if last_exc:
            raise last_exc
        return False

    def create_via(self, position: Vector2, net: Net, drill_mm: float, diameter_mm: float) -> Via:
        logger.debug(_("Creating via at ({x:.3f}, {y:.3f}) mm, net={net}")
                     .format(x=position.x/MM, y=position.y/MM, net=net.name))
        via = KipyVia()
        via.type = ViaType.VT_THROUGH
        via.position = KipyVector2.from_xy(position.x, position.y)
        via.net = unwrap(net)
        via.drill_diameter = int(drill_mm * MM)
        via.diameter = int(diameter_mm * MM)
        return Via(uuid="", position=position, net_name=net.name if net else None,
                   drill_mm=drill_mm, diameter_mm=diameter_mm, layer=None, _kipy=via)

    def create_track(self, start: Vector2, end: Vector2, width_mm: float,
                     net: Net, layer: BoardLayer) -> Track:
        logger.debug(_("Creating track ({sx:.3f}, {sy:.3f}) -> ({ex:.3f}, {ey:.3f}) mm, net={net}")
                     .format(sx=start.x/MM, sy=start.y/MM,
                             ex=end.x/MM, ey=end.y/MM, net=net.name))
        track = KipyTrack()
        track.start = KipyVector2.from_xy(start.x, start.y)
        track.end = KipyVector2.from_xy(end.x, end.y)
        track.width = int(width_mm * MM)
        track.net = unwrap(net)
        track.layer = KipyBoardLayer.BL_B_Cu if layer == BoardLayer.BL_B_Cu else KipyBoardLayer.BL_F_Cu
        return Track(uuid="", start=start, end=end, net_name=net.name if net else None,
                     width_mm=width_mm, layer=layer, _kipy=track)

    def remove_by_id(self, uuid_str: str) -> bool:
        """
        Deletes an object (via or any other) by its id (UUID string) —
        needed for the placement registry (delete stale via before creating
        a new one at a different location). Returns True if the delete request
        completed without exception — does NOT guarantee that an object with
        that UUID actually existed (stale UUID is not an error, just no‑op).
        """
        from kipy.proto.common.types import base_types_pb2 as common_types_pb2
        kiid = common_types_pb2.KIID()
        kiid.value = uuid_str
        try:
            self._board.remove_items_by_id([kiid])
            return True
        except Exception as e:
            logger.warning(_("Failed to delete object {uuid}: {type}: {e}")
                           .format(uuid=uuid_str, type=type(e).__name__, e=e))
            return False