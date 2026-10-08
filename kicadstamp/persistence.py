# kicadstamp/persistence.py
"""
On-disk persistence format versioning (registry JSON + operation logs).

The ``schema_version`` field lets a future incompatible format change be
detected loudly instead of being silently mis-parsed (and potentially
recreating duplicate copper or corrupting the board). Readers accept a MISSING
field as version 1 — backward compatibility with every file written before
2026-08-25 — and refuse a version newer than this build supports.

These diagnostics are deliberately plain-English literals (not ``_()``): they
fire only on a genuinely incompatible, forward-looking file, and keeping them
out of the gettext catalog avoids dragging a rare error path through the
babel extract/compile cycle (same precedent as the plain ``f"Could not open
log_file..."`` warning in gui/dock_hub.py).
"""

REGISTRY_SCHEMA_VERSION = 1
# The registry schema a format-3 (UUID-keyed) profile carries. Kept here next to
# the format-2 value so the registry reader/writer and the on-disk lift
# (kicadstamp/config/registry_upgrade.py) agree on ONE source of truth. Do NOT
# raise REGISTRY_SCHEMA_VERSION itself: in format 2 (CURRENT_FORMAT = 2, the
# product today) the registry files must stay byte-identical, schema 1 included.
#
# 3 (Д2, plan_2026_10_08_remove_spokes): the schema in which the SPOKE copper
# keys (``pad:<pad>|…``) are DETACHED — dropped from the registry so the board
# copper they named is left unowned instead of being pruned away on the next
# apply. Schema 2 (У5.4) was already UUID-keyed but still carried those keys, so
# it is lifted to 3 by the detach alone.
REGISTRY_SCHEMA_VERSION_FORMAT3 = 3
OPERATION_LOG_SCHEMA_VERSION = 1


def check_schema_version(version, expected, path, kind: str) -> None:
    """Refuse a persisted file whose ``schema_version`` is not one this build
    supports.

    ``version`` — the raw ``schema_version`` value read from the file (``None``
    when the field is absent, i.e. a legacy pre-2026-08-25 file — accepted).
    ``expected`` — a single supported version (the usual case, and what every
    caller but the format-3 registry passes), or an iterable of supported
    versions: the registry reader under the format-3 gate accepts only the
    lifted schema 3; anything older is refused by ``_refuse_unlifted_registry``
    before this is reached. ``path``/``kind`` — used only to build the error
    message.

    Raises :class:`ValueError` on a version this build does not understand:
    silently proceeding would mis-parse entries and corrupt the board, so a
    future format change must fail loudly until a migration is written.
    """
    if version is None:
        return
    supported = tuple(expected) if isinstance(expected, (tuple, list, set)) else (expected,)
    if version not in supported:
        shown = (supported[0] if len(supported) == 1
                 else " or ".join(str(v) for v in supported))
        raise ValueError(
            "{kind} {path!r} has schema_version {version}, but this build only "
            "supports schema_version {expected} — the on-disk format changed. "
            "Regenerate or migrate the file before running.".format(
                kind=kind, path=str(path), version=version, expected=shown,
            )
        )
