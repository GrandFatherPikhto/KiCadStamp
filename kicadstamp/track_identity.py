# kicadstamp/track_identity.py
"""Positional identity of copper: one track-matching predicate and its pre-check.

Moved verbatim out of ``kicadstamp/registry.py`` (доделка Р of
plan_2026_10_15_reread_copper_uuid_and_selection_any — the registry's size rule
is "giants only shrink"). The registry re-exports ``track_matches`` and
``filter_existing_tracks``, so every existing importer and test keeps working
through the same public names.

The degenerate-segment RULE itself is NOT here: it lives in
``kicadstamp/degenerate_segments.py`` and is only CALLED from the positional
path below (one rule, many callers).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .placement.commands import TrackCommand

from .constants import POSITION_TOLERANCE_MM
from .degenerate_segments import is_degenerate_segment
from .i18n import _
from .registry_match import same_identity, same_place
from .utils.layers import layer_to_str
from .utils.units import MM

logger = logging.getLogger(__name__)


def points_close(a_x_mm: float, a_y_mm: float, b_x_mm: float, b_y_mm: float) -> bool:
    """Two points match within POSITION_TOLERANCE_MM (0.01 mm), in mm."""
    return (
        abs(a_x_mm - b_x_mm) <= POSITION_TOLERANCE_MM
        and abs(a_y_mm - b_y_mm) <= POSITION_TOLERANCE_MM
    )


def track_matches(live_track, cmd: TrackCommand) -> bool:
    """Shared bidirectional predicate: does the live board track match the
    planned TrackCommand? A segment is UNORIENTED — both start↔start/end↔end
    AND start↔end/end↔start count as the same geometry. Also compares net
    (full hierarchical name as a string), width, and layer (via _layer_to_str).

    Used by BOTH the UUID-path reconcile (TrackRegistry._live_matches) and the
    positional pre-check (filter_existing_tracks) — one predicate, so the two
    can never disagree (plan_2026_08_16_position_based_copper_idempotency.md).

    THE TWO HALVES LIVE IN `registry_match` (Ч1 of
    plan_2026_10_26_select_cell_shifted_registry_pair): `same_place` is the
    position half, `same_identity` the kind/net/layer/width half. This predicate
    is their composition, unchanged in meaning — and the split is what lets a
    caller ask the halves SEPARATELY: a pair at the planned place with another
    width is the record's own copper edited on the board, while a pair away from
    the place on another net is a shifted registry number.

    Deliberately NO degenerate-segment guard here (acceptance Д-1, 19.10): by
    UUID the identity of a segment IS known, so the reconcile path may compare
    its geometry and keep it. Unidentifiability is a property of POSITIONAL
    identity only, and the rule for it lives in :func:`filter_existing_tracks`."""
    return same_place(live_track, cmd) and same_identity(live_track, cmd)


def filter_existing_tracks(to_create: list[TrackCommand], live_tracks) -> list[TrackCommand]:
    """Positional pre-check of tracks — unregistered-copper idempotency
    (analog of ViaPlanner._via_already_exists, but applied STRICTLY AFTER
    registry.reconcile(), on its to_create list: a pre-reconcile skip would
    drop the key from seen_keys and make prune delete the REGISTERED tool
    track, see plan_2026_08_16_position_based_copper_idempotency.md).

    Skips any command whose geometry+net+width+layer already exists among the
    live tracks (manually drawn, created by another mechanism, a previous
    run's channel-copy, or — since 2026-08-31, plan
    2026_08_31_duplicate_tracks_after_tree_redraw — the SAME cell placed
    twice under DIFFERENT registry keys, e.g. a legacy clone_placement AND an
    Entity materialized from a tree: their tracks are positionally identical
    but keyed ``point:/role:`` vs ``name:``, so the registry path alone cannot
    dedupe them). Run unconditionally by ApplyPipeline (Phase 3). SKIP-ONLY:
    never removes and never adopts foreign copper into the registry. Returns
    the filtered list, logs a skip counter.

    POSITIONAL identity only (acceptance Д-1, 19.10): a segment whose ends are
    closer than 2 × the tolerance (degenerate_segments) is NOT identified here —
    every neighbour within the tolerance box answers "same ends", and the guess
    has a bad sign (the pre-check would silently skip copper that is missing).
    Such a command is therefore never skipped; it is created (or kept) and from
    then on recognised by its UUID through the registry. The live list is
    filtered the same way, so a normal command can never pair with a stub."""
    if not to_create:
        return to_create
    # Only segments with a positional identity of their own may answer "already
    # here" (see the docstring). The rule itself lives in degenerate_segments.py.
    positional_pool = [live for live in live_tracks
                       if not is_degenerate_segment(live.start, live.end)]
    kept: list[TrackCommand] = []
    skipped = 0
    for cmd in to_create:
        if (not is_degenerate_segment(cmd.start, cmd.end)
                and any(track_matches(live, cmd) for live in positional_pool)):
            skipped += 1
            logger.debug(_("  track for {owner}: already exists, skipped (positional pre-check)")
                         .format(owner=cmd.owner_ref))
            continue
        kept.append(cmd)
    if skipped:
        logger.info(_("Skipped {count} tracks already present on the board (positional pre-check)")
                    .format(count=skipped))
    return kept
