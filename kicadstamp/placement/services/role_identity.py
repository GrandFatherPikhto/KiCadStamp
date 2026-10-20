# kicadstamp/placement/services/role_identity.py
"""The IDENTITY half of an anchor_role resolve: which candidates the caller's
snapshot offers, and which object the resolver returns for the chosen one.

Extracted from `clone_role_resolver.py` by the "giants only shrink" rule (Д-2 of
the Ч1 acceptance). Both functions carry the SAME rule — a non-empty snapshot is
an identity CACHE, and only the live board may say "not there" (fact Б of
plan_2026_10_15_honest_live_read) — so they live together, away from the
narrowing cascade that merely consumers them.

The rule has three shapes, all of them "ask the live board":
  * no row for the role in a non-empty snapshot;
  * a row whose ref the live board no longer has;
  * a row whose ref exists on the live board but carries ANOTHER role (a snapshot
    of the previous board can name an unrelated R21 — Д-3 of the same acceptance).
"""
from ...constants import ROLE_FIELD_NAME
from ...domain.board import Footprint


def role_candidates(adapter, role: str, snapshot) -> list:
    """Every footprint carrying `role` — the IDENTITY half of the resolver.

    From the caller's board snapshot (``connection.snapshot``, one ``Selected``
    per footprint, its ``role`` being the same store-aware value the adapter's
    own ``get_field_value`` answers) when the caller has one, else by sweeping
    the board: one ``get_footprints()`` plus one ``get_field_value()`` PER
    footprint (the 996 field scans of the 22.09.2026 UI-thread measurement).

    The position is deliberately NOT taken from here: ``Selected.fp`` belongs to
    the snapshot's generation, so the caller of the snapshot branch re-reads the
    CHOSEN ref from the adapter — see `current_generation`.

    An EMPTY snapshot is treated as NO snapshot, deliberately: the connection
    carries ``[]`` until its first poll rebuilds it, and "the board has nothing"
    would be a lie about a board nobody has looked at yet (found live — the
    re-hang guard of tests/gui/test_trees_dock.py went from green to a fatal the
    moment a stand-in's empty snapshot was believed). Both ends of the question
    — the emptiest board and the missing role — are the same fatal anyway."""
    if snapshot:
        from_snapshot = [selected.fp for selected in snapshot
                         if selected.role == role]
        if from_snapshot:
            return from_snapshot
        # A MISS in a non-empty snapshot is NOT an answer (fact Б, Denis 15.10,
        # plan_2026_10_15_honest_live_read): the snapshot can have been built
        # from the PREVIOUS board, and "the board has nothing" would then be a
        # lie about a component standing right in front of the user. The LIVE
        # board decides below — the same rule as its neighbour
        # resolve_footprint_by_cluster_role. Only a HIT is free.
    return [fp for fp in adapter.get_footprints()
            if adapter.get_field_value(fp, ROLE_FIELD_NAME) == role]


def current_generation(adapter, chosen: Footprint, snapshot,
                       role: str | None) -> Footprint | None:
    """The object the resolver RETURNS: `chosen` itself when the candidates came
    from the adapter, and the adapter's OWN current generation of it when they
    came from a snapshot.

    Why the re-read (plan_2026_09_22_board_door_finish §4.3): a snapshot answers
    "which footprint is this", never "where is it NOW" — its ``Selected.fp`` was
    copied at poll time.

    None — the snapshot's ref has left the live board (deleted, renamed, or the
    snapshot belongs to ANOTHER board), OR the live footprint with that refdes is
    a DIFFERENT part (one ``get_field_value`` ROLE read on the cached footprint:
    a snapshot from another board can name an unrelated R21 — Д-3 of the Ч1
    acceptance). Either way that is a snapshot fact, not a board answer, so the
    caller asks the LIVE board (fact Б of plan_2026_10_15_honest_live_read). Only
    the live sweep may say "not found"."""
    if not snapshot:
        return chosen
    live = adapter.get_footprint(chosen.ref)
    if live is None:
        return None
    return live if adapter.get_field_value(live, ROLE_FIELD_NAME) == role else None
