# kicadstamp/utils/safe_write.py
"""Write helpers that must never be able to lose data.

Two helpers, both factored out so there is exactly ONE implementation of each:

* :func:`backup_file` — a timestamped copy of the whole file next to itself,
  taken BEFORE a write. Moved here verbatim from
  `gui/docks/entity_delete.py` (task В.1.3, plan_2026_09_11_tree_instances_and_
  converter_safety): the GUI delete/rename flow, the tree save path, the tree
  converter and `flatten` all follow the same "snapshot the file before you
  overwrite it" convention, so a `kicadstamp/` module (the CLI side) importing
  it from `gui/` was a layering inversion. `gui/docks/entity_delete.py` now
  re-exports it, so every existing importer keeps working unchanged.

* :func:`write_text_atomic` — write a string to a file atomically: a temporary
  file in the SAME directory, then `os.replace` onto the target. A plain
  `open(path, "w")` TRUNCATES the target before the new content is even
  computed, so any serialization error left a 0-byte config on disk (found live
  in the tree converter: `open(..., "w")` around `dict_to_sexp(converted)`,
  task В.1). With the content serialized up front and the replace done last,
  the target either keeps its old bytes or receives the complete new ones —
  never a half-written file.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

# Monotonic per-process counter appended to the timestamp — datetime.now()'s
# microsecond resolution can COLLIDE when two backups are created within the
# same microsecond (found live 2026-08-13 via the flaky
# test_backup_file_two_calls_produce_two_distinct_files): a colliding name
# would silently OVERWRITE the first backup instead of giving it its own
# recovery point, the very failure this timestamp scheme exists to prevent.
_backup_seq = 0


def backup_file(path: Path) -> Path:
    """Timestamped copy of `path` next to itself, e.g.
    'cells.yaml.bak.20260805_161500_123456.1' — never overwrites an earlier
    backup, see module docstring. Microsecond resolution (not just
    seconds) — a cascade delete can back up several files within the same
    second, and each must still get its own recovery point. A monotonic
    counter suffix guarantees uniqueness even when two calls land within the
    same microsecond. Returns the backup's path (used in the caller's
    summary message)."""
    global _backup_seq
    _backup_seq += 1
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = path.with_name(f"{path.name}.bak.{stamp}.{_backup_seq}")
    shutil.copy2(path, backup_path)
    return backup_path


def write_text_atomic(path: Path, text: str) -> None:
    """Write `text` to `path` atomically (task В.1.4, plan §В.1.2 step 4).

    The content goes to a temporary file in the SAME directory first (same
    filesystem, so `os.replace` is atomic), then `os.replace` swaps it onto
    the target. A crash, a full disk or a serialization error can therefore
    never leave a partially written config: the target keeps its previous
    bytes until the complete new content is ready.

    `newline=""` on purpose: no platform newline translation, so the output is
    byte-identical across Windows and Linux (the project's established
    discipline — see `config_rename.write_profile_files`). A `.bak` is the
    caller's responsibility (see :func:`backup_file`).

    On any failure the temporary file is removed and the target is untouched.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
