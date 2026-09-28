# tests/fakes/resolver.py
"""The `_FakeResolver` doubles — the SURFACE all six of them had in common.

Ф1.4d-2. Six classes named `_FakeResolver` stood in for
``kicadstamp.placement.services.component_resolver.ComponentResolver``: five in
tests/test_tree_position.py, one in tests/test_tree_inner_point.py. Unlike the
earlier families these were never byte-for-byte equal, and the difference is why
this module is a BASE class and not one shared fake:

  * every one of the six hands back its OWN footprint double (`_FakeFp`),
    defined in the enclosing test function. `test_pivot_ref_outside_the_mount_
    subtree_stays_local` returns a footprint at (300 mm, 400 mm) PRECISELY
    because that position is not the one its assertion expects. A single shared
    class returning a single shared footprint would make that cell pass for the
    wrong reason — the trap the family exists to keep out.
  * two of them ASSERT the anchor arguments they were called with, and that
    assertion is the point of their cell. They override `resolve_anchor_fp` and
    call ``super()``, so the shared part is still shared and the local claim is
    still local.

The FOOTPRINT comes in through `RESOLVED_FP_FACTORY`, a zero-argument factory —
normally the local `_FakeFp` CLASS. A factory and not an instance on purpose:
every call gets a FRESH double, exactly as the six copies did (`_FakeFp()`), so
a consumer that mutates what it got cannot leak into the next call.

What the base DOES carry is the surface — and it is where both real drifts
happened, which is why tests/test_fakes_conformance.py compares it parameter by
parameter against the real class rather than by method name:

  * the constructor. The real one is ``(adapter, config, sheet_names, *,
    snapshot=None)``. `snapshot` arrived with Т2-4а of
    plan_2026_09_22_live_adapter_class, and four of the six doubles swallowed it
    with ``**kwargs`` instead of naming it — a double that absorbs an argument
    cannot notice a rename. The base names it, and the cell fails on the next
    signature move instead of quietly tolerating it.
  * `resolve_anchor_fp`'s five parameters: one of the six spelled them
    ``(*args, **kwargs)``, so a rename would have gone unnoticed there too.

Only the anchor read is doubled. The six never defined `build_pools`, and no
path these tests exercise calls it, so the base does not invent it.
"""
from __future__ import annotations


class FakeComponentResolver:
    """The shared surface of the six; the footprint is injected by the subclass."""

    #: A zero-argument factory of the footprint `resolve_anchor_fp` hands back —
    #: normally the local file's `_FakeFp` CLASS. Left as None on the base so a
    #: subclass that forgets it fails loudly instead of returning nonsense.
    RESOLVED_FP_FACTORY = None

    def __init__(self, adapter, config, sheet_names, *, snapshot=None) -> None:
        # The four attribute names mirror the real class: `cfg` (not `config`) is
        # what the real __init__ stores, and a caller reading `resolver.snapshot`
        # back is exactly what the snapshot threading does.
        self.adapter = adapter
        self.cfg = config
        self.sheet_names = sheet_names
        self.snapshot = snapshot

    def resolve_anchor_fp(self, anchor_ref, anchor_role, anchor_sheet,
                          anchor_cluster, label=""):
        return self.RESOLVED_FP_FACTORY()


__all__ = ["FakeComponentResolver"]
