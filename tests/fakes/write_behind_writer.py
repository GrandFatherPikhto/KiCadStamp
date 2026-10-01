# tests/fakes/write_behind_writer.py
"""Writing a config file the way a HAND EDIT does — the filesystem directly —
while keeping the readers' caches honest.

Why this helper exists (W2 of the Ф3.1 разбор; measured again in Ф3.6,
01.10.2026). Several rigs write a config with a bare `write_text` and then
re-read it through the product's readers. Those readers are cached by
`(path, mtime_ns)`, and `file_cache`'s own docstring says plainly why mtime
alone is not enough for OUR OWN writes: two writes microseconds apart can land
on ONE tick, and then the second read is a false HIT on the content of the
first. Windows makes that the rule rather than the exception — measured
01.10.2026: `st_mtime_ns` granularity there is ~0.5 ms, and 344/500 (C:) and
409/500 (repo disk D:) back-to-back write pairs landed on the SAME tick.

The product closes that gap the only way it can: `write_config_file`
invalidates the caches itself right after the physical write. A rig that writes
the file by hand must do the same, or it measures the cache instead of the code
— which is exactly how `test_chain_saved_refreshes_config_tree_chains` failed
in CI #655 / Ф3.6: the tree was rebuilt from the PRE-write content.

So this is the rig-side twin of the writer's tail and must stay EQUAL to it.
The three calls mirror `write_config_file` (kicadstamp/config_writer.py):
`invalidate_path` + `invalidate_graph_path` + `invalidate_probe`.
"""
from pathlib import Path

from kicadstamp.config.format_version import invalidate_probe
from kicadstamp.utils.file_cache import invalidate_graph_path, invalidate_path


def write_behind_the_writer(path, text: str) -> None:
    """Write TEXT to PATH physically, then run the invalidations
    ``write_config_file`` runs after its own write — so a reader that follows
    cannot answer from the pre-write cache.

    This is NOT a way to write config in the product: the product has exactly
    one write chokepoint, and a rig whose file must carry a format number can
    simply call ``write_config_file``. Use this only where the POINT is to hand
    the readers bytes that did not come from the writer."""
    target = Path(path)
    target.write_text(text, encoding="utf-8")
    invalidate_path(target)
    invalidate_graph_path(target)
    invalidate_probe(target)
