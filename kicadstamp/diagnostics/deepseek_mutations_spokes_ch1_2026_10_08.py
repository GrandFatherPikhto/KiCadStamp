# kicadstamp/diagnostics/deepseek_mutations_spokes_ch1_2026_10_08.py
"""Acceptance mutations for Ч1 of techdocs/handoff/deepseek/plan/plan_2026_10_08_remove_spokes.md
(Д2: detach the spoke copper from the registry; Д1: refuse a non-empty chains:), DeepSeek, 2026-10-08.

Grown from deepseek_mutations_tree_node_ref_2026_10_08.py (rule 38), whose machinery
is deepseek_mutations_refresh_mixed_2026_10_05.py — the SAME guards: basename-resolved
tests under tests/, `_drop_pyc`, the `original.count(old) != 1` refusal (a non-unique
template is a MISS, not a kill), ПРОМАХ when nothing goes red, and a control that MUST
survive.

WHAT IS BEING PROVEN (Д2 — kicadstamp/config/registry_upgrade.py, the read gate in
kicadstamp/registry.py, the schema constants in kicadstamp/persistence.py):

  * M1 the detach keeps the spoke entry (the predicate never fires) — the entry is
       pruned on the next apply and the copper is deleted from the board
  * M2 the detach takes the CELL copper too (the predicate is too broad) — the
       neighbouring cell record's copper is deleted
  * M3 the sweep stamps the OLD schema number (2) on a lifted file
  * M4 a schema-2 registry is not recognised, so the spoke keys are never detached
  * M5 the format-3 read gate accepts schema 2 again — the spoke copper would be pruned
  * M6 the format-3 registry schema stays 2 (the lift and the gate disagree)
  * M7 the `__spoke__` literal is renamed — every cell-copper key's role part changes
  * M8 the `chains:` skip is dropped — the schema is raised although the config
       still PLANS the spoke copper (the file's copper is then re-created or pruned
       away: the hole the Д2 fix-up closes)
  * M9 the WARNING lists EVERY entry (no "first 10 + …" truncation)
  * M10 the truncated WARNING has no FULL list at DEBUG beside it
  * K1 a cosmetic comment in the lift -> MUST survive

The guard set is tests/placement/test_registry_upgrade_on_disk.py (the whole Д2 axis:
detach, the board double's zero deletions, the neighbour keys, `.bak`, the no-op, the
schema-2 refusal).

Д1 (the loader refusal for a non-empty `chains:` / a chain-kind tree node) moved to
Ч3 by the acceptance of 09.10 — its rows will be added there, not here.

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_spokes_ch1_2026_10_08.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

GUARDS = ["test_registry_upgrade_on_disk.py"]

RU = "kicadstamp/config/registry_upgrade.py"
REG = "kicadstamp/registry.py"
PERS = "kicadstamp/persistence.py"
CONST = "kicadstamp/constants.py"

_SPOKE_PREDICATE = (
    "    return len(parts) == 4 and parts[0].startswith(_SPOKE_ANCHOR_PREFIX)\n")

MUTATIONS = [
    # 1 — the detach never fires: the spoke entry STAYS in the lifted registry, so
    # reconcile prunes it on the next apply and the copper is deleted from the board.
    ("M1 the detach keeps the spoke entry", RU,
     _SPOKE_PREDICATE,
     "    return False  # MUTATION: nothing is detached\n",
     "die", GUARDS, ()),
    # 2 — the detach is too broad: it takes every record-anchored key too, so a
    # neighbouring cell record's copper is detached and then deleted.
    ("M2 the detach takes the cell copper too", RU,
     _SPOKE_PREDICATE,
     "    return len(parts) == 4 and parts[0].split(\":\", 1)[0] != \"imprint\""
     "  # MUTATION\n",
     "die", GUARDS, ()),
    # 3 — the sweep writes the OLD schema number: the file is stamped 2 while the
    # reader (and the lift's own target) expect 3.
    ("M3 the sweep stamps the old schema", RU,
     "            data = {\"schema_version\": TARGET_SCHEMA_VERSION, **new_entries}\n",
     "            data = {\"schema_version\": _UUID_KEY_SCHEMA_VERSION,"
     " **new_entries}  # MUTATION\n",
     "die", GUARDS, ()),
    # 4 — a schema-2 registry is not recognised as liftable: the spoke keys survive
    # (they are neither mapped nor detached) and the copper is pruned.
    ("M4 a schema-2 registry is not lifted", RU,
     "_UUID_KEY_SCHEMA_VERSION = 2\n",
     "_UUID_KEY_SCHEMA_VERSION = 99  # MUTATION\n",
     "die", GUARDS, ()),
    # 5 — the read gate accepts schema 2 again (the pre-Д2 boundary): a registry
    # still carrying the spoke keys is read, and reconcile prunes their copper.
    ("M5 the read gate accepts schema 2", REG,
     "    older = (isinstance(version, int) and not isinstance(version, bool)\n"
     "             and version < REGISTRY_SCHEMA_VERSION_FORMAT3)\n",
     "    older = (isinstance(version, int) and not isinstance(version, bool)\n"
     "             and version < 2)  # MUTATION\n",
     "die", GUARDS, ()),
    # 6 — the format-3 schema stays 2: the lift stamps 2 and the gate reads 2, but
    # the lifted file is the SCHEMA-3 shape (spokes gone) — new cells expect 3.
    ("M6 the format-3 schema stays 2", PERS,
     "REGISTRY_SCHEMA_VERSION_FORMAT3 = 3\n",
     "REGISTRY_SCHEMA_VERSION_FORMAT3 = 2  # MUTATION\n",
     "die", GUARDS, ()),
    # 7 — the `__spoke__` literal is renamed: every CELL-copper key's role part
    # changes, i.e. a registry migration nobody wrote (plan «НЕ УДАЛЯТЬ» 1).
    ("M7 the __spoke__ literal is renamed", CONST,
     # constants.py ends without a trailing newline — leave it out of the template.
     "SPOKE_LEVEL_ROLE_PLACEHOLDER = \"__spoke__\"",
     "SPOKE_LEVEL_ROLE_PLACEHOLDER = \"__spoke_renamed__\"",
     "die", GUARDS, ()),
    # 8 — the chains skip is dropped: the registry IS lifted while the config still
    # plans the spoke copper. The file then gets the target schema with its spoke
    # copper re-created (keys detached) or pruned later (keys kept) — the hole the
    # Д2 fix-up closes. Both Д2 cells spell out "the file is left alone", so either
    # shape of the hole kills them.
    ("M8 the chains skip is dropped", RU,
     "            _debug_listed(path, \"chains\",\n"
     "                          [chain_effective_name(c) for c in chains])\n"
     "            continue\n",
     "            pass  # MUTATION: lift anyway\n",
     "die", GUARDS, ()),
    # 9 — the truncation is dropped: a live profile's ~355 keys would be printed in
    # full in ONE WARNING line (the acceptor's surviving C1).
    ("M9 the WARNING lists every entry", RU,
     "    if len(items) > limit:\n"
     "        shown += \", …\"\n",
     "    if len(items) > limit:\n"
     "        pass  # MUTATION: no truncation\n",
     "die", GUARDS, ()),
    # 10 — the full list is no longer logged at DEBUG, so the truncated WARNING has
    # nothing to grep (the acceptor's surviving C2).
    ("M10 no FULL list at DEBUG", RU,
     "    if items:\n"
     "        logger.debug(\"registry {path}: {what} (full list): {keys}\".format(\n"
     "            path=path, what=what, keys=\", \".join(sorted(items))))\n",
     "    if items:\n"
     "        pass  # MUTATION: no DEBUG list\n",
     "die", GUARDS, ()),
    # K1 — a cosmetic comment in the lift: MUST survive.
    ("K1 a cosmetic comment in the lift", RU,
     "# The schema a format-3 registry carries. Taken from the persistence module's",
     "# The schema a format-3 registry carries. TAKEN from the persistence module's",
     "survive", GUARDS, ()),
]


if __name__ == "__main__":
    rig.MUTATIONS = MUTATIONS
    rig.main()
