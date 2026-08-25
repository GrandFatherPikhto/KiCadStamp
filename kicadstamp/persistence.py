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
OPERATION_LOG_SCHEMA_VERSION = 1


def check_schema_version(version, expected: int, path, kind: str) -> None:
    """Refuse a persisted file whose ``schema_version`` is newer than ``expected``.

    ``version`` — the raw ``schema_version`` value read from the file (``None``
    when the field is absent, i.e. a legacy pre-2026-08-25 file — accepted).
    ``path``/``kind`` — used only to build the error message.

    Raises :class:`ValueError` on a version this build does not understand:
    silently proceeding would mis-parse entries and corrupt the board, so a
    future format change must fail loudly until a migration is written.
    """
    if version is None:
        return
    if version != expected:
        raise ValueError(
            "{kind} {path!r} has schema_version {version}, but this build only "
            "supports schema_version {expected} — the on-disk format changed. "
            "Regenerate or migrate the file before running.".format(
                kind=kind, path=str(path), version=version, expected=expected,
            )
        )
