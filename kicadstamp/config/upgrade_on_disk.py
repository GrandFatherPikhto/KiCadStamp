# kicadstamp/config/upgrade_on_disk.py
"""Lift the config files of one include: graph to CURRENT_FORMAT, on disk.

`plan_2026_09_24_config_format_version.md` §Т4 plus «Уточнения к Т4» (which wins
wherever the two disagree). `upgrade_graph_on_disk(root)` is called from
`load_config` BEFORE `cached_graph_result`, so it runs outside the cached
computation — once per (root, mtime) change, never inside a cached body.

The rules, each of them pinned by a cell in
`tests/test_config_format_upgrade_on_disk.py`:

1. EVERY step is applied by REBUILDING the text — `dict_to_sexp(upgrade_data(...))`
   (У1) — the identity 1 -> 2 included. There is no «just insert the number»
   shortcut and no per-step branch: formats will change, sometimes unpredictably,
   and such a branch is exactly how a later step breaks in a way nobody expects.
   The accepted price, said out loud: the first lift NORMALIZES the text of live
   files — section aliases become canonical (`rules` -> `chains`), default-valued
   fields are dropped, the indentation becomes the writer's. The MEANING does not
   change, and the `.bak` keeps the previous bytes.
2. The check before the write compares MEANING, not raw dicts (У2):
   `_strip_defaults` on both sides, the way `tools/sexp_config_convert.py`
   self-verifies. A raw compare would refuse for ever — the rebuild drops
   default-valued fields, so the two dicts never match and every open would log an
   ERROR without ever lifting the file.
3. ONE file newer than this build, anywhere in the graph, stops the WHOLE sweep
   before the first write: the pre-pass calls `read_version`, which refuses such a
   file. Nothing in the graph is written.
4. A copy or a write that fails leaves THAT file exactly as it was, logs the
   reason, and the load continues from the lifted in-memory content (Т2) — the
   next open tries again.
5. A file already at CURRENT is not touched at all: no write, no `.bak`, `mtime`
   unchanged.
6. Nothing is written while the GUI working set holds unsaved changes (У3): that
   file's new content is not on disk yet, and the Save lifts it itself through the
   one writer (`ConfigWorkingSet.flush` -> `write_config_file`).

GUI and MCP lifting one profile at the same time give at worst two `.bak` files
and two identical writes — the content is deterministic, and the two `os.replace`
calls of one atomic write do not interleave into a torn file.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..config_writer import serialize_config, write_config_file
from ..exceptions import ValidationError
from ..i18n import _
from ..utils.safe_write import backup_file
from .format_version import (
    current_format,
    parse_raw_file,
    parse_raw_text,
    read_version,
    upgrade_data,
)

logger = logging.getLogger(__name__)


def _graph_files(root: str | Path) -> list[Path]:
    """Every physical file of the include: graph, root first, each file ONCE.

    A diamond (one file reached from two branches) is ONE file and must be lifted
    once. `walk_include_tree` deliberately does not dedupe — it is built for
    display — so the dedupe lives here."""
    from .includes import walk_include_tree

    files: list[Path] = []
    seen: set[str] = set()

    def visit(node) -> None:
        resolved = str(node.path.resolve())
        if resolved not in seen:
            seen.add(resolved)
            files.append(Path(resolved))
        for child in node.children:
            visit(child)

    visit(walk_include_tree(str(root)))
    return files


def upgrade_graph_on_disk(root: str | Path) -> list[Path]:
    """Lift every config file reachable from `root` whose on-disk number is older
    than CURRENT_FORMAT. Returns the files actually written — empty when the whole
    graph was already current — which is what the WARNING lines and the tests
    report.

    Raises whatever `read_version` raises (a file NEWER than this build, a
    malformed number) from the PRE-PASS, before a single byte is written: that is
    what the pre-pass is for. Backup/write failures are NOT raised — they are
    logged and that file is left alone, so one unwritable file cannot stop a
    profile from loading."""
    from ..config_working_set import WORKING_SET  # lazy — keeps this import-free of the GUI path

    if WORKING_SET.is_dirty():
        # У3: the staged content is not on disk yet, so lifting the disk file
        # would write a state the user has not saved. That file keeps its older
        # number until the Save, which lifts it through the one writer.
        logger.info(_("format upgrade on disk skipped: the working set holds unsaved changes"))
        return []

    files = _graph_files(root)

    # Pre-pass: decide, and refuse a newer file, BEFORE the first write. read_version
    # parses a file only while its (path, mtime_ns) probe is cold; the warm path is
    # one os.stat per file (plan §10.4).
    outdated: list[Path] = []
    for path in files:
        if not path.exists():
            continue
        if read_version(path) < current_format():
            outdated.append(path)
    if not outdated:
        return []

    lifted: list[Path] = []
    for path in outdated:
        try:
            data, version = parse_raw_file(path)
            content = upgrade_data(data, version, str(path), at_parse_time=True)
            text = serialize_config(path, content)
            # У2: compare by MEANING. _strip_defaults on both sides, exactly as
            # tools/sexp_config_convert.py verifies its own output — the rebuild
            # drops default-valued fields, so raw dicts would never match and the
            # file would be refused on every open, for ever.
            parsed_back = parse_raw_text(text, path.suffix.lower(), str(path))[0]
            if _strip_defaults(parsed_back) != _strip_defaults(content):
                logger.error(_(
                    "config file {path}: the format upgrade is NOT written — the text it would write does not read back as the lifted content; the file is left as it is").format(path=path))
                continue
            # Order matters (Д5): the text is serialized and verified first, the
            # copy is taken second, the write last — so a target the serializer
            # refuses never leaves a stray copy behind. The copy is taken HERE, by
            # the same backup_file() the writer uses, because the WARNING below
            # has to name it; write_config_file is told backup=False, and it still
            # refuses a file that became newer and still writes atomically.
            backup = backup_file(path)
            # The text that was just verified IS the text written — not a second
            # serialization of the same data. Otherwise the check would gate a
            # value nobody writes, and a lift implemented by INSERTING the number
            # into the old text (mutant M3 of the Т4 acceptance) would pass it
            # while the file kept its legacy aliases.
            write_config_file(path, content, backup=False, serialized_text=text)
        except (OSError, ValidationError) as e:
            logger.error(_(
                "config file {path}: the format upgrade failed ({error}) — the file is left as it is; the next open will try again").format(path=path, error=e))
            continue
        logger.warning(_(
            "config file {path}: format {old} is outdated, lifted to {new}. The previous version is saved: {backup}").format(path=path, old=version, new=current_format(), backup=backup))
        lifted.append(path)
    return lifted


def _strip_defaults(data: dict) -> dict:
    """`sexp_format._strip_defaults`, imported lazily: that module imports the
    format-number module at ITS module level, and this file is imported from
    `load_config` on the hot path."""
    from .sexp_format import _strip_defaults as strip

    return strip(data)
