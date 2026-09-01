#!/usr/bin/env python3
# tools/convert_rules_to_chains.py
"""One-time migration: legacy `rules:` section key -> `chains:` (2026-09-01,
Rule -> Chain rename, plan_2026_09_01_rules_to_chains.md).

The loader already READS the legacy `rules:` key (config/aliases.py's
normalize_section_aliases maps it on read), so old profiles keep loading
without this tool. This converter exists to CANONICALIZE existing profiles on
disk: for every config file in the include: graph it renames a top-level
`rules:` key to `chains:` (backing up the file first), so the on-disk format
matches the new name.

Design (same shape as tools/convert_placements.py):
  * reads each file RAW (un-normalized — sexp/json direct) so it can actually
    SEE a legacy `rules:` key that the normalizing readers would have already
    mapped to `chains:`;
  * backs up the file before rewriting (timestamped .bak, the same
    backup-the-write-target convention as entity_delete.backup_file /
    tools/sexp_config_convert.py);
  * idempotent: a file already using `chains:` is left untouched (no .bak, no
    write), so re-running after a partial conversion is a no-op for the done
    files;
  * supports .sexp and .json (the config formats after YAML removal).

Run on a COPY of a live profile:
    tools/convert_rules_to_chains.py profiles/3ch-awg-tia-v103/3ch-awg-tia.sexp
"""
import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root on path

from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict


def _read_raw(path: Path) -> dict:
    """Read a config file's RAW dict WITHOUT section-key aliases — the
    converter must be able to see a legacy `rules:` key that the normalizing
    readers would already have mapped to `chains:`. For .sexp this uses
    sexp_to_dict(apply_aliases=False) (see its docstring); for .json the raw
    json.load already keeps the original key."""
    with open(path, "r", encoding="utf-8") as f:
        if path.suffix.lower() == ".json":
            import json
            return json.load(f) or {}
        if path.suffix.lower() == ".sexp":
            return sexp_to_dict(f.read(), apply_aliases=False) or {}
        raise ValueError(f"unsupported config format: {path.suffix} (expected .sexp or .json)")


def _write_raw(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        if path.suffix.lower() == ".json":
            import json
            f.write(json.dumps(data, indent=2, ensure_ascii=False))
        elif path.suffix.lower() == ".sexp":
            f.write(dict_to_sexp(data))
        else:
            raise ValueError(f"unsupported config format: {path.suffix} (expected .sexp or .json)")


def convert_rules_to_chains(data: Dict[str, Any]) -> Dict[str, Any]:
    """Rename a top-level `rules:` key to `chains:` in a config dict (in
    place-style, returns the same dict). Fatal (never a silent drop) when the
    file has BOTH `rules:` and `chains:` — that is ambiguous, same rule as
    config/aliases.py's normalize_section_aliases."""
    if "rules" not in data:
        return data
    if "chains" in data:
        raise ValueError(
            f"both 'rules:' and 'chains:' present in the same config file — "
            f"ambiguous; keep one (move the rules: entries into chains: and "
            f"remove the old key) before running the converter")
    data["chains"] = data.pop("rules")
    return data


def convert_file(path: Path) -> Path | None:
    """Canonicalize ONE config file: `rules:` -> `chains:` if present.
    Returns the written .bak path when the file was changed, or None when it
    was already canonical (no write, no backup). Raises OSError/ValueError on
    a non-readable/unwritable/ambiguous file."""
    if not path.exists():
        raise FileNotFoundError(f"input file not found: {path}")
    data = _read_raw(path)
    before = dict(data)
    convert_rules_to_chains(data)
    if data == before:
        return None  # already canonical — idempotent, no .bak, no write
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path.with_name(f"{path.name}.bak.{stamp}")
    shutil.copy2(path, bak)
    _write_raw(path, data)
    return bak


def _graph_files(root: Path) -> List[Path]:
    """Every config file reachable from `root` via include: (root first, then
    each include, recursively) — the same include graph the loader walks."""
    from kicadstamp.config.includes import walk_include_tree

    out: List[Path] = []
    seen: set = set()

    def _walk(node) -> None:
        if node.path in seen:
            return
        seen.add(node.path)
        out.append(node.path)
        for child in node.children:
            _walk(child)

    _walk(walk_include_tree(str(root)))
    return out


def convert_profile(root: Path) -> Dict[str, Path]:
    """Canonicalize the whole include: graph rooted at `root`. Returns
    {changed_file: backup_file} for every file that was rewritten (empty dict
    when nothing had a legacy `rules:` key)."""
    changed: Dict[str, Path] = {}
    for path in _graph_files(root):
        if path.suffix.lower() not in (".sexp", ".json"):
            continue
        try:
            bak = convert_file(path)
        except FileNotFoundError:
            continue  # a dangling include: target — nothing to convert
        if bak is not None:
            changed[str(path)] = bak
    return changed


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate legacy rules: config key to chains: (2026-09-01 "
                    "Rule -> Chain rename) across a profile's include: graph")
    parser.add_argument("path", help="root config file to convert (.sexp or .json)")
    args = parser.parse_args(argv)
    root = Path(args.path)
    try:
        changed = convert_profile(root)
    except Exception as e:  # noqa: BLE001 — a CLI tool reports the error cleanly
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not changed:
        print(f"no legacy 'rules:' keys found under {root} — already canonical")
        return 0
    for path, bak in sorted(changed.items()):
        print(f"converted {path} (backup: {bak.name})")
    print(f"{len(changed)} file(s) converted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
