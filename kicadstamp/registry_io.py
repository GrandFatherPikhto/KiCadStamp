# kicadstamp/registry_io.py
"""The registry files' SERIALISATION — loaded, validated, written.

MOVED here from ``registry.py`` (Ч1 of
plan_2026_10_27_orphan_anchor_keys_own_nothing, Denis 27.10): that file had 775
lines against an 800-line ceiling, and the orphan-rule touches ``reconcile`` — the
extraction had to happen BEFORE the rule, as its own commit, so the acceptance sees
a pure move and a rule separately.

WHAT lives here, and only this: the two entry dataclasses, the schema gate (which
versions this build READS and what the writer stamps), the unlifted-registry guard,
``load_registry`` / ``save_registry`` / ``load_track_registry`` /
``save_track_registry`` and the ONE reader both GUI doors call
(``load_registry_entries``).

WHAT does NOT: the composite KEY (``make_registry_key`` / ``record_key_part``), the
ownership predicate and ``reconcile`` stay in ``registry.py`` — they are the
registry's own identity and planning concepts, not its file format.

``registry.py`` RE-EXPORTS every public name below, so
``from kicadstamp.registry import load_registry_entries`` (gui/mixed_selection,
kicadstamp/explode, kicadstamp/internode_cell_copper, kicadstamp/absent_copper_prune,
placement/services/board_items_resolver, and a test that monkeypatches
``kicadstamp.registry.load_registry_entries``) keeps working unchanged. The import
direction is one-way: registry → registry_io, never back.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from .exceptions import ValidationError
from .i18n import _
from .persistence import (
    REGISTRY_SCHEMA_VERSION,
    REGISTRY_SCHEMA_VERSION_FORMAT3,
    check_schema_version,
)
from .utils.paths import registry_paths_for_config

logger = logging.getLogger(__name__)

__all__ = [
    "RegistryEntry",
    "TrackRegistryEntry",
    "load_registry",
    "load_registry_entries",
    "load_track_registry",
    "save_registry",
    "save_track_registry",
]


def _registry_schema_versions() -> tuple[int, ...]:
    """Registry schema versions this build READS, by the format gate.

    Format 2 (the product today): only schema 1 — byte-identical behaviour.

    Format 3: ONLY schema 3 (the uuid-keyed registry, spoke keys detached, that
    the on-disk lift writes — kicadstamp/config/registry_upgrade.py). Schema 1
    must NOT be read under the gate — see :func:`_refuse_unlifted_registry`
    (Н3): a name-keyed registry against a uuid-keyed plan is the mix Р-У5.7
    forbids, and reading it deletes the profile's copper. Schema 2 (Д2) is
    refused too: it is uuid-keyed, but it still carries the SPOKE keys
    (``pad:<pad>|…``), which no longer appear in any plan — reconcile would prune
    them and delete the copper. This used to accept {1, 2} on the argument that a
    failed lift (read-only registry directory) must stay readable; that traded
    a loud fatal for silent copper deletion, the wrong way around."""
    from .config.format_version import current_format
    if current_format() >= 3:
        return (REGISTRY_SCHEMA_VERSION_FORMAT3,)
    return (REGISTRY_SCHEMA_VERSION,)


def _refuse_unlifted_registry(raw: dict, path: str) -> None:
    """Н3 (У5.4 rework) + Д2: under the format-3 gate, a registry that has NOT
    been lifted to the CURRENT schema is a FATAL — checked BEFORE the lenient
    entry parse, and therefore before ``reconcile`` and before any deletion.

    Why a fatal, not a lenient read. Under the gate the plan carries UUID keys
    (Р-У5.1/Р-У5.7). A schema-1 registry carries NAME keys, so ``reconcile``
    sees every planned command as "create" and every stored entry as "prune":
    it would delete and recreate the profile's whole copper. Р-У5.7 says the
    name/uuid mix is worse than a fatal, and this is exactly that mix.

    When can the lift have failed to run? The lift runs on EVERY profile open
    (``load_config`` → ``upgrade_registries_on_disk``); reaching here means it
    did not, e.g. a registry directory that is not writable, a failed write, or
    a config whose explicit ``registry_path`` points somewhere the lift never
    looked. The message says what to do: reopen the profile (the lift runs on
    open) or make the registry directory writable.

    A file with NO ``schema_version`` is the pre-2026-08-25 legacy form —
    schema 1 by convention (``check_schema_version``) — and is refused too.

    Д2 (plan_2026_10_08_remove_spokes) widens this from "not uuid-keyed" to
    "older than the current schema": a schema-2 registry (uuid-keyed, spoke keys
    still attached) is refused as well, because reading it would let reconcile
    prune the detached-elsewhere spoke copper and delete it.

    The message names ``chains:`` FIRST, because that is the EXPECTED reason as of
    Д2's fix-up: while the config still carries a non-empty ``chains:`` the lift
    deliberately leaves the registry alone (registry_upgrade rule 7), so the file
    stays at schema 2 on purpose. The second sentence covers the other reason (the
    lift could not write) — the reader cannot tell the two apart: it sees only the
    file, and carrying the Config down here for a message would point the registry
    at the config package for no functional gain.

    A NON-integer ``schema_version`` is left to ``check_schema_version`` (a
    plain ``ValueError``), keeping its message and behaviour unchanged.
    """
    from .config.format_version import current_format
    if current_format() < 3:
        return
    version = raw.get("schema_version")
    older = (isinstance(version, int) and not isinstance(version, bool)
             and version < REGISTRY_SCHEMA_VERSION_FORMAT3)
    if version is None or older:
        raise ValidationError(_(
            "The copper registry {path} was not lifted to the current schema — "
            "apply stopped, the board was not touched. This is expected while the "
            "profile still carries a non-empty chains: (the lift is skipped then, "
            "so the spoke copper keys stay attached): remove the chains: section "
            "and reopen the profile. If the profile has no chains:, the lift could "
            "not write the file — reopen the profile, and check that the registry "
            "directory is writable"
        ).format(path=str(path)))


def _registry_schema_version_for_write() -> int:
    """The schema a freshly written registry gets: 3 under the format-3 gate, 1
    in format 2 (so the product's registry files stay byte-identical today)."""
    from .config.format_version import current_format
    return (REGISTRY_SCHEMA_VERSION_FORMAT3 if current_format() >= 3
            else REGISTRY_SCHEMA_VERSION)


@dataclass
class RegistryEntry:
    uuid: str
    x_mm: float
    y_mm: float
    net: str
    drill_mm: float
    diameter_mm: float


@dataclass
class TrackRegistryEntry:
    uuid: str
    start_x_mm: float
    start_y_mm: float
    end_x_mm: float
    end_y_mm: float
    width_mm: float
    net: str
    layer: str


def load_registry(path: str) -> dict[str, RegistryEntry]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(_("Failed to read registry {path}: {type}: {e} — "
                         "treating registry as empty (all vias will be created anew)")
                       .format(path=path, type=type(e).__name__, e=e))
        return {}
    if isinstance(raw, dict):
        # Under the format-3 gate a name-keyed (schema 1) registry is a FATAL
        # before anything else (Н3) — reading it would delete the copper.
        _refuse_unlifted_registry(raw, path)
        # A future schema_version fails loudly (before the lenient entry parse
        # below) — mis-parsing a newer format could recreate duplicate copper.
        check_schema_version(raw.get("schema_version"), _registry_schema_versions(),
                             path, "registry")
        raw = {k: v for k, v in raw.items() if k != "schema_version"}
    else:
        raw = {}
    try:
        return {k: RegistryEntry(**v) for k, v in raw.items()}
    except Exception as e:
        logger.warning(_("Failed to read registry {path}: {type}: {e} — "
                         "treating registry as empty (all vias will be created anew)")
                       .format(path=path, type=type(e).__name__, e=e))
        return {}


def save_registry(path: str, entries: dict[str, RegistryEntry]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"schema_version": _registry_schema_version_for_write(),
            **{k: asdict(v) for k, v in entries.items()}}
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_track_registry(path: str) -> dict[str, TrackRegistryEntry]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(_("Failed to read track registry {path}: {type}: {e} — "
                         "treating registry as empty (all tracks will be created anew)")
                       .format(path=path, type=type(e).__name__, e=e))
        return {}
    if isinstance(raw, dict):
        _refuse_unlifted_registry(raw, path)
        check_schema_version(raw.get("schema_version"), _registry_schema_versions(),
                             path, "track registry")
        raw = {k: v for k, v in raw.items() if k != "schema_version"}
    else:
        raw = {}
    try:
        return {k: TrackRegistryEntry(**v) for k, v in raw.items()}
    except Exception as e:
        logger.warning(_("Failed to read track registry {path}: {type}: {e} — "
                         "treating registry as empty (all tracks will be created anew)")
                       .format(path=path, type=type(e).__name__, e=e))
        return {}


def load_registry_entries(config_path, cfg=None) -> tuple[dict, dict, dict]:
    """(via_entries, track_entries, owner) for a config's TWO registry files.

    ``owner`` is {copper uuid -> registry key} over both files. This is the ONE
    reader the mixed-read bridge (gui/mixed_selection.py) and "Select cell"
    (gui/select_cell.py) both call — Н5-3, "one function on every door", so the
    two can never disagree about which copper belongs to which record. The two
    paths come from the product's ONE decision (registry_paths_for_config) with
    the config's explicit registry_path:/track_registry_path: values."""
    via_path, trk_path = registry_paths_for_config(
        str(config_path), getattr(cfg, "registry_path", None),
        getattr(cfg, "track_registry_path", None))
    via_entries = load_registry(via_path)
    track_entries = load_track_registry(trk_path)
    owner: dict[str, str] = {}
    for key, entry in via_entries.items():
        uuid = getattr(entry, "uuid", None)
        if uuid:
            owner[uuid] = key
    for key, entry in track_entries.items():
        uuid = getattr(entry, "uuid", None)
        if uuid:
            owner[uuid] = key
    return via_entries, track_entries, owner


def save_track_registry(path: str, entries: dict[str, TrackRegistryEntry]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"schema_version": _registry_schema_version_for_write(),
            **{k: asdict(v) for k, v in entries.items()}}
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
