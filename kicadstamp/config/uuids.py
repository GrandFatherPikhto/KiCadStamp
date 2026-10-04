# kicadstamp/config/uuids.py
"""config/uuids.py — the FIXED UUID namespaces of the format 2->3 step (У5.2).

Two namespaces live here and nowhere else, so the invariant that matters is
checkable in ONE place:

- :data:`NS_MIGRATION` — the seed namespace of the Р-1 on-disk migration
  (`uuid5(NS, "<file|section|full name>")`); the converter (У3) and the writer
  stamp are the consumers. It is defined here — rather than inline in У3 — so
  that :data:`NS_DERIVED` can be PROVEN distinct from it right now (see the
  mutation row "NS_DERIVED = NS", plan §6). У3 only starts USING it; the value
  never changes once a profile has been lifted.
- :data:`NS_DERIVED` — the namespace of the COMPUTED UUIDs the template
  expansions hand to their generated copies (Р-У5.3). It is deliberately a
  DIFFERENT fixed namespace: a derived copy's uuid is a function of the
  ORIGINAL record's uuid plus the instance identity, and keeping the two
  namespaces apart makes a collision between a lifted record and a derived copy
  structurally impossible, not merely improbable.

``derived_uuid`` is the ONE builder of a generated copy's uuid — never inline
the ``uuid5`` call at a call site, or the two expansions (tree_instances /
sheet_templates) could drift apart.
"""
from __future__ import annotations

from uuid import UUID, uuid5

# Р-1 migration seed namespace. Fixed forever; У3 is the first user.
NS_MIGRATION = UUID("8f0c1e6a-9b3d-4a72-8c5e-1d4f7a2b9e30")

# Derived-copy namespace (Р-У5.3). MUST differ from NS_MIGRATION: a generated
# copy is a NEW record, so its uuid must not live in the same namespace the
# migration mints records in (the invariant is pinned by
# tests/config/test_uuid_derivation.py).
NS_DERIVED = UUID("b2d7c4e1-5a3f-4b8c-9e21-6f0d3a7c5b94")


def derived_uuid(seed: str) -> str:
    """The computed UUID of one generated copy, deterministic in `seed`.

    `seed` is built by the CALLER to name the copy unambiguously (the original
    record's uuid plus the instance/sheet identity) — see the two expansions.
    Deterministic across loads, machines and processes: the same config yields
    the same derived UUIDs, so a redraw never recreates copper that has not
    changed."""
    return str(uuid5(NS_DERIVED, seed))
