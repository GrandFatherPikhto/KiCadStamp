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

``migration_uuid`` / ``migration_folder_uuid`` are the ONE builders of the Р-1
migration identity (step 2 -> 3, У3.1): a record and a folder row of a
format-2 file lifted by the converter. They live here, beside ``NS_MIGRATION``,
so the CONVERTER and the test stub cannot invent two different seeds for the
same record — a divergence no cell could catch from the outside.
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


# ── the Р-1 migration seed (step 2 -> 3) ───────────────────────────────────

def migration_uuid(section: str, full_name: str) -> str:
    """The Р-1 migration UUID of ONE §0 record, deterministic in `(section,
    full name)` and — deliberately — in NOTHING ELSE (Денис, 04.10).

    The seed carries NO file path: `uuid5(NS_MIGRATION, "<section>|<full
    name>")`. The path was dropped so the step is a PURE function of ONE file
    (reopened Р-1): a reference to a record living in ANOTHER file of the graph
    is computed from the reference's own name hint alone, with no graph walk.
    The path added nothing anyway — a UUID is unique within a graph (§0) and a
    full name is unique within its section across the graph (Р43) — and one
    more thing is gained: a shared file of two profiles gets the SAME UUIDs
    whatever the lift order.

    `full_name` is the record's full name (with `/` folder separators, when the
    name has any), NOT the file-scoped key."""
    return str(uuid5(NS_MIGRATION, f"{section}|{full_name}"))


def migration_folder_uuid(section: str, path: str) -> str:
    """The Р-1 migration UUID of ONE folder row (В39), `(section, path)`.

    Seed `uuid5(NS_MIGRATION, "<section>|folder:<path>")` — the `folder:`
    marker keeps a folder from ever colliding with a record whose full name
    happens to equal the folder path. No file path for the same reason as
    :func:`migration_uuid`: a folder row may stand in EACH file that has records
    under it (В39), and every one of them must carry the SAME UUID."""
    return str(uuid5(NS_MIGRATION, f"{section}|folder:{path}"))
