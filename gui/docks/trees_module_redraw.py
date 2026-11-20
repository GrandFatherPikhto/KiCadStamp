# gui/docks/trees_module_redraw.py
"""The Redraw doors of an EMBEDDED tree's tab — the tree is placed through its module.

plan_2026_11_15_embedded_tree_redraw_through_module (Denis, 15.11): «хочется поработать с
деревом отдельно, а фигушки» — the tab of a tree that a `module` node places used to REFUSE,
because it could only lay the tree out from its OWN anchor (measured (−32.000, +35.000) mm on
HP-Power, −1.000/+4.000 mm on the STM32 snapshot). Refusing was right, working there is better:

    Redraw from the tab of an embedded tree puts every node where the module's own Redraw would.

So the tab hands over the WHOLE CHAIN of module markers as `selected_refs` (п. 2 / Р2: each level,
innermost first — `gui.docks.trees_embedding.placement_chain`) and narrows what gets APPLIED to
the names the door was asked about (`only_names`, the one new worker parameter). The stage-2
layout then takes the owner's live anchor — `curated_forest_module_content` — and nothing of the
host trees' own content moves, because it is computed, not applied.

What does NOT change: a redraw of a tree nobody embeds (these helpers return None and the door
runs its own path), and the Redraw of the module IN its owner.

What is still refused (п. 5): a tree placed by two or more markers — `placements()` names every
one, because which place to take is genuinely unknown. Never a fallback to the tree's own anchor.
"""
from __future__ import annotations

import logging
from typing import Optional

from kicadstamp.i18n import _

from ..connection import worker_timeout_ms
from ..worker import start_long_op
from ._common import ERROR_STYLE, show_message
from .cascade import run_curated_forest_redraw_worker
from .redraw_drift import log_redraw_drift
from .trees_embedding import (Placement, chain_selection, placement_chain, placements,
                              several_markers_line)
from .trees_view_helpers import collect_tree_refs

logger = logging.getLogger(__name__)

__all__ = [
    "embedded_redraw_line",
    "redraw_embedded",
    "refuse_embedded_redraw",
    "start_embedded_redraw",
]


def redraw_embedded(dock, tree, *, only_names=None, drift_refs=None, trigger=None,
                    node_ref: Optional[str] = None) -> Optional[bool]:
    """The three-state answer every door needs: `None` — nobody places `tree`, run your own
    path; `False` — placed and REFUSED (two or more markers, the Log line is said); `True` —
    the forest redraw was started in the module's frame.

    One function for the four doors on purpose: the refusal and the start must never drift
    apart, and a door that forgets one of them goes back to laying the content out from the
    tree's own anchor."""
    if refuse_embedded_redraw(dock._trees, tree, node_ref=node_ref):
        return False
    return start_embedded_redraw(dock, tree, only_names=only_names,
                                 drift_refs=drift_refs, trigger=trigger)


def refuse_embedded_redraw(trees, tree, *, node_ref: Optional[str] = None) -> bool:
    """True — and ONE Log line — when `tree` may NOT be redrawn from its tab at all.

    The only case left is TWO OR MORE markers (plan п. 5 / Р2): the two owners may sit in
    different frames, so "take one of them" is a guess, and `several_markers_line` names every
    marker instead. `node_ref` is accepted so the four doors can call this with the same
    arguments they used to pass to `trees_embedding.refuse_embedded_tree` (the wording no longer
    depends on it: a two-marker tree is refused for its whole tab, not per node)."""
    found = placements(trees, tree)
    if len(found) < 2:
        return False
    show_message(several_markers_line(tree.name, found), ERROR_STYLE, logger)
    return True


def embedded_redraw_line(tree, chain: list[Placement]) -> str:
    """The ONE startup line of the plan's п. 6: THROUGH WHAT this run places the tree.

    A chain is printed outermost-first ("'mid' in 'outer' -> 'inner' in 'mid'"), i.e. in the
    order a reader walks the config, ending with the marker that sits closest to this tab."""
    return _("Redraw of tree {tree!r} goes through module {where}").format(
        tree=tree.name,
        where=" -> ".join(step.where() for step in reversed(chain)))


def start_embedded_redraw(dock, tree, *, only_names=None, drift_refs=None,
                          trigger=None) -> Optional[bool]:
    """Start the FOREST redraw of `tree` in its module's frame.

    `None` — nobody places `tree`: the caller runs its own (unchanged) path. This is the answer
    for a standalone tree and for the case the door would otherwise duplicate. A generated
    `tree_instances` copy is NOT such a case: what the config forbids is the PAIR «template ↔ its
    own instance», so a THIRD tree may place the copy — and then the door goes through that module
    exactly like any other embedded tree (Ф4 of plan_2026_11_19_forest_index_keys_and_instances;
    the wording here used to claim the copy «can never be module-placed at all»).
    `False` — placed, but the run could not start (no config/root, or the first-run heads-up said
    no): the caller must NOT continue down its own path, that would be the Grid-Origin redraw.
    `True` — a worker was started; `trigger` (if given) is its busy guard.

    `only_names` — what to APPLY; None means "everything the chain plans", which for a whole-tree
    Redraw is exactly the tree plus the content of the modules it embeds (the same set the
    module's own Redraw in the owner would move). `drift_refs` is the journal's set: the names
    that really move, so the Log line never claims a part the run will not touch."""
    chain = placement_chain(dock._trees, tree)
    if not chain:
        return None
    if dock._cfg is None or not dock._root_path or not dock._trees:
        dock._show_status(_("Nothing to redraw."))
        return False
    if not dock._confirm_first_run_redraw():
        return False
    logger.info(embedded_redraw_line(tree, chain))
    log_redraw_drift(dock, tree,
                     drift_refs if drift_refs is not None
                     else (only_names if only_names is not None else collect_tree_refs(tree)))
    payload = {
        "config_path": str(dock._root_path),
        "cfg": dock._cfg,
        "ctx": dock._ctx,
        "trees": dock._trees,
        # The chain (markers only) — NOT this tree's refs: the planner has to see every host
        # level, or the stage-2 layout takes the host's own anchor (Ч0 п.3).
        "selected_refs": chain_selection(dock._trees, tree),
        "only_names": set(only_names) if only_names is not None else None,
        "timeout_ms": worker_timeout_ms(dock._main_window.connection),
    }
    widgets = [trigger] if trigger is not None else []
    dock._active_op = start_long_op(
        dock._main_window.connection, widgets,
        run_curated_forest_redraw_worker, dock._finish_redraw,
        dock._on_redraw_failed, payload,
        busy_text=_("placing"))
    return True
