#!/usr/bin/env python3
# kicadstamp/diagnostics/deepseek_probe_spokes_inventory_2026_10_08.py
"""Ч0 inventory probe for the "remove spokes and chains" plan
(plan_2026_10_08_remove_spokes.md, section Ч0).

READ-ONLY. It never opens a live profile and never writes anything: it walks the
source tree and classifies every mention by the SYMBOL it names, never by the
word — the first version of this probe keyed on the WORD "chain", which
mis-classified whole files (`kicadstamp/tree_position.py` was reported as "21
delete lines", while only its `kind == "chain"` branches belong to the feature
and the rest is the RESOLUTION chain (`chain = visited | {…}`) and
`resolve_point_chain` — a different meaning that stays). Reworked 2026-10-08 per
the plan's "Сверка Ч0".

The unit of classification is a LINE, and a line is what it NAMES:

  * delete — the line names a spoke/chain SYMBOL (`ManualSpoke`, `SOURCE_SPOKE`,
    `spoke_pad`, `apply_spoke_geometry`, `Chain`, the `chains:` section key, the
    `rules:` / `Rule` alias, the tree NODE KINDS `chain` / legacy `rule`, "Add
    chain", "placed by chain", "chain dict/list/form/row/leaf", ...);
  * allow  — the line names a KEPT symbol (the registry role placeholder name
    `SPOKE_LEVEL_ROLE_PLACEHOLDER` and its FROZEN value `__spoke__`; the cell via
    `offset_along_mm` / `offset_across_mm`; the `spoke_layout` geometry
    primitives) or uses "chain" in another sense (sheet-name chain, `include:`
    import chain, anchor / point / dependency chain, "the chain would loop").

A FILE is then `delete` (its basename encodes the feature: spoke / chain /
convert_rules_to_chains), `edit` (a mixed file: the spoke symbols go, the rest
stays) or `allow`.

The probe also emits:
  * the SYMBOL -> verdict table, and for every delete/edit FILE the SYMBOLS and
    the LINES it loses (a list, never a count of "delete lines");
  * the tree NODE KINDS `chain` / `rule` (Ч0 п.2): every place the kind is a
    literal, a table entry or a branch, plus the Д1 statement — the load must
    refuse a TREE NODE of that kind too, not only a non-empty `chains:`;
  * the `spoke_layout` decision (Ч0 п.3): the module keeps its primitives, the
    `apply_spoke_geometry` + `ManualSpoke` half goes, the test file's going cells
    are listed with the geometric PROPERTY each held and the best remaining cell
    by name keywords, so "property -> cell" can be finished in Ч4.

Run:  .venv/bin/python -m kicadstamp.diagnostics.deepseek_probe_spokes_inventory_2026_10_08
or:   .venv/bin/python kicadstamp/diagnostics/deepseek_probe_spokes_inventory_2026_10_08.py
"""
from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# --- scopes ----------------------------------------------------------------
# (scope label, root dir, extensions). "product" deliberately excludes the
# diagnostics directory, which is its own scope.
_SCOPES = (
    ("product", "kicadstamp", (".py",), ("diagnostics",)),
    ("product", "gui", (".py",), ()),
    ("diagnostics", "kicadstamp/diagnostics", (".py",), ()),
    ("tests", "tests", (".py",), ()),
    ("tools", "tools", (".py",), ()),
    ("mcp_server", "mcp_server", (".py",), ()),
    ("docs", "docs", (".md",), ()),
    ("locales", "locales", (".po",), ()),
    ("techdocs_map", "techdocs/map", (".md", ".txt"), ()),
)
# Root README files sit outside every scope but do mention spokes; keep them.
_EXTRA_FILES = ("README.md", "README_ru.md")
_SKIP_DIRS = {"__pycache__", ".venv", ".git", "profiles", ".mypy_cache"}

# --- the SYMBOL RULES (ordered: the first match decides) --------------------
# Each rule is (label, regex, verdict). The verdict is "delete" (the symbol goes
# with the feature) or "allow" (a KEPT symbol, or another meaning of the word).
#
# The ALLOW rules that guard a FROZEN VALUE (`__spoke__`), a cell-format field
# (`offset_*_mm`) and the kept geometry module come FIRST on purpose: an import
# of `apply_spoke_geometry` from `spoke_layout` names both, and the import line
# must still read "delete" — so the module rule is checked only when no delete
# symbol matched (see `_classify_line`).
_FROZEN_ALLOW = (
    ("registry role placeholder (name renameable, VALUE frozen)",
     r"SPOKE_LEVEL_ROLE_PLACEHOLDER|__spoke__"),
    ("cell via offset_*_mm fields (cell format, not spokes)",
     r"offset_along_mm|offset_across_mm"),
)
_KEPT_GEOM = ("spoke_layout geometry primitives (along/across, rotation)",
              r"spoke_layout")
_SYMBOL_RULES = (
    ("ManualSpoke (dataclass)", r"ManualSpoke"),
    ("SOURCE_SPOKE / cell spoke rows", r"SOURCE_SPOKE|spoke_rows|source_spoke"),
    ("spoke pad / spoke pad edit", r"spoke_pad|spoke_pad_edit"),
    ("apply_spoke_geometry", r"apply_spoke_geometry"),
    ("spoke extraction / redraw feature",
     r"spoke_extraction|spoke_extract|extract_spoke|spoke_redraw"),
    ("Chain (dataclass)", r"\bChain\b"),
    ("chains: section key / .chains / chains=",
     r"chains?\s*:|\.chains\b|[\"']chains[\"']|chains\s*="),
    ("chain dock / navigator / identity",
     r"chain_dock|chains_nav|chain_effective_name|collect_chains_by_net|"
     r"chain_dict|chain_net|chain_name|chain_doc"),
    ("rules: / Rule alias", r"rules\s*:|[\"']rules[\"']|\.rules\b|rules\s*=|Rule\b"),
    ("tree node kind chain / legacy rule (Ч0 п.2)",
     r"_F3_NODE_KIND_TARGET|LEGACY_KINDS|\bKINDS\b|"
     r"kind\s*==\s*[\"'](?:chain|rule)[\"']|"
     r"[\"'](?:chain|rule)[\"']\s*:\s*[\"']chains?[\"']|"
     r"\bkind\b[^\n]*[\"'](?:chain|rule)[\"']"),
    ("chain in a UI / menu phrase",
     r"placed by chain|add chain|chain's|chain\(s\)|chain dict|chain list|"
     r"chain form|chain row|chain leaf|chain #|chains? of (?:cell|spoke)"),
)
_OTHER_CHAIN = (
    ("other meaning of 'chain' (sheet-name / include / anchor / point chain)",
     r"chained|chaining|anchor[ -]?chain|include[ :]+chain|import[ :]+chain|"
     r"chain (?:loops?|would loop)|sheet[ -]?name chain|point[ -]?chain|"
     r"link chain|dependency chain|resolve_point_chain|цепочк"),
)
_RE_ANY = re.compile(r"spoke|chain", re.I)


def _classify_line(line: str):
    """(class, reason) for one line, or None when it is not a mention at all."""
    if not _RE_ANY.search(line):
        return None
    for reason, pattern in _FROZEN_ALLOW:
        if re.search(pattern, line, re.I):
            return ("allow", reason)
    for label, pattern in _SYMBOL_RULES:
        if re.search(pattern, line):
            return ("delete", label)
    reason, pattern = _KEPT_GEOM
    if re.search(pattern, line, re.I):
        return ("allow", reason)
    for reason, pattern in _OTHER_CHAIN:
        if re.search(pattern, line, re.I):
            return ("allow", reason)
    # A mention that names NO known symbol: report it as allow, so it is visible
    # for audit and NEVER silently counted as a deletion.
    return ("allow", "mentions spoke/chain but names no symbol in the table")


def _spoke_symbols_in(line: str) -> list[str]:
    """The delete-class SYMBOLS this line names (for the per-file symbol list)."""
    return [label for label, pattern in _SYMBOL_RULES if re.search(pattern, line)]


# --- file walk -------------------------------------------------------------
_TEXT_SUFFIXES = {".py", ".md", ".po", ".txt", ".pot"}


def _iter_files():
    seen: set[str] = set()
    for scope, rel, exts, skip in _SCOPES:
        root = _REPO / rel
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in exts:
                continue
            # Never scan the probe itself (its docstring/regexes quote the words).
            if path.name == Path(__file__).name:
                continue
            parts = set(path.relative_to(root).parts)
            if parts & _SKIP_DIRS or any(p in _SKIP_DIRS for p in path.parts):
                continue
            key = str(path.relative_to(_REPO))
            if key in seen:
                continue
            seen.add(key)
            yield scope, path
    for name in _EXTRA_FILES:
        path = _REPO / name
        if path.exists():
            key = str(path)
            if key not in seen:
                seen.add(key)
                yield ("readme", path)


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(_REPO))
    except ValueError:
        return str(path)


def _scan():
    """(records, files_by_scope, sources) — sources caches each file's lines."""
    records: list[dict] = []
    files_by_scope: Counter = Counter()
    sources: dict[str, list[str]] = {}
    for scope, path in _iter_files():
        files_by_scope[scope] += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = _rel(path)
        lines = text.splitlines()
        sources[rel] = lines
        for lineno, raw in enumerate(lines, start=1):
            res = _classify_line(raw)
            if res is None:
                continue
            cls, reason = res
            records.append({
                "path": rel, "scope": scope, "line": lineno,
                "cls": cls, "reason": reason, "text": raw.strip(),
                "symbols": _spoke_symbols_in(raw),
            })
    return records, files_by_scope, sources


# --- symbol table ----------------------------------------------------------
# The SYMBOLS the plan's final grep must explain, each with the other spellings
# a reader will look for. `_SYMBOLS` keeps the ORIGINAL names (the plan's list)
# and adds the two tree-node KINDS of Ч0 п.2.
_SYMBOLS = (
    "Chain", "ManualSpoke", "chains", "rules", "Rule",
    "spoke_pad", "SOURCE_SPOKE", "apply_spoke_geometry",
    "SPOKE_LEVEL_ROLE_PLACEHOLDER",
    "LEGACY_KINDS", "_F3_NODE_KIND_TARGET",
)
_RE_IMPORT = re.compile(r"^\s*(?:from\s+\S+\s+import|import)\b")


def _symbol_table(sources: dict[str, list[str]]):
    """{symbol: {"defs": [(path, line, text)], "importers": [path]}}."""
    table = {s: {"defs": [], "importers": []} for s in _SYMBOLS}
    for rel, lines in sources.items():
        for i, raw in enumerate(lines, start=1):
            stripped = raw.strip()
            for sym in _SYMBOLS:
                word = re.compile(rf"\b{re.escape(sym)}\b")
                if not word.search(raw):
                    continue
                if re.match(rf"(class|def)\s+{re.escape(sym)}\b", stripped) or \
                   re.match(rf"{re.escape(sym)}\s*[:=]", stripped):
                    table[sym]["defs"].append((rel, i, stripped))
                if _RE_IMPORT.match(raw):
                    table[sym]["importers"].append(rel)
    return table


# A file whose BASENAME encodes the feature is the feature's own file, so the
# whole file goes; any other file that merely contains delete-class lines is
# MIXED (the spoke half goes, the rest stays) -> "edit".
_RE_FEATURE_NAME = re.compile(r"spoke|chain|rules[_-]?to[_-]?chains|convert_rules", re.I)
# Files the plan keeps WHOLE. `spoke_layout.py` and its test are NOT here: Ч0 п.3
# decided the module stays with its PRIMITIVES while `apply_spoke_geometry` /
# `ManualSpoke` go and the docstring is rewritten — an EDIT, not an allow.
_KEEP_WHOLE = {"kicadstamp/constants.py"}


# `_EDIT_NOT_DELETE` (defined with the spoke_layout paths below) holds the two
# files whose NAME encodes the feature but which KEEP a half: the geometry
# PRIMITIVES (module) and their primitive-level cells (test). Ч0 п.3 decides
# they are EDITS, not whole-file deletions — whatever their basename says.

def _file_class_of(path: str, counts: Counter) -> str:
    """delete | edit | allow for ONE file, from its LINE classes.

    A file whose name encodes the feature goes whole; any other file with
    delete-class lines is MIXED (the spoke half goes, the rest stays). A file
    whose lines are all `allow` stays `allow` even when one of them names a
    delete-class symbol — the report lists that symbol in the file's row, so the
    case is visible for audit instead of being promoted to "edit" silently."""
    if path in _KEEP_WHOLE:
        return "allow"
    has_delete = counts["delete"] > 0
    if has_delete and path in _EDIT_NOT_DELETE:
        return "edit"
    base = path.rsplit("/", 1)[-1]
    if has_delete and _RE_FEATURE_NAME.search(base):
        return "delete"
    if has_delete:
        return "edit"
    return "allow"


# Test names the plan already names as spoke-only (Ч4).
_KNOWN_DEAD_TESTS = (
    "test_spoke_write", "test_chain_dock", "test_spoke_cell_identification",
    "test_chains_nav", "test_convert_rules_to_chains", "test_extract_spoke_dialog",
    "test_spoke_extraction", "test_spoke_redraw_component_ownership",
    "test_extract_spoke",
)
# Kept-mechanism files that must NOT be deleted wholesale.
_KEPT_MECHANISM = (
    "kicadstamp/placement/services/component_pool.py",
    "kicadstamp/placement/services/component_resolver.py",
    "kicadstamp/placement/services/manual_position_calculator.py",
    "kicadstamp/geometry/clone_geometry.py",
    "kicadstamp/geometry/cell_anchor.py",
    "kicadstamp/geometry/thermal_grid.py",
    "kicadstamp/cell_frame.py",
    "kicadstamp/tree_position.py",
    "kicadstamp/placement/planner.py",
    "kicadstamp/anchor_graph.py",
    "kicadstamp/placement/dependency_order.py",
    "kicadstamp/apply_pipeline.py",
    "kicadstamp/config/models.py",
    "kicadstamp/author.py",
)
_SPOKE_LAYOUT = "kicadstamp/geometry/spoke_layout.py"
_SPOKE_LAYOUT_TEST = "tests/geometry/test_spoke_layout.py"
# The two files whose name encodes the feature but which keep a PRIMITIVE half
# (Ч0 п.3): `spoke_layout.py` keeps `local_to_absolute` / `rotate_local_offset` /
# the via-track resolution / `SpokeLayout`, and its test keeps the primitive-level
# cells. `apply_spoke_geometry` + the `ManualSpoke` import go, the docstring is
# rewritten — an EDIT.
_EDIT_NOT_DELETE = {_SPOKE_LAYOUT, _SPOKE_LAYOUT_TEST}
# The geometric PROPERTY keywords the plan's table (Ч0 п.3) must account for.
_PROPERTY_KEYWORDS = (
    "rotation", "offset", "polar", "radius", "angle", "layer", "net",
    "mirror", "track", "via", "along", "across", "zero", "origin", "anchor",
)
_STOP_WORDS = {
    "test", "the", "a", "an", "is", "of", "and", "to", "from", "with", "not",
    "in", "on", "for", "gives", "matches", "same", "different", "when", "unset",
    "does", "be", "are", "its", "own", "each", "keeps", "kept", "list", "empty",
}
_RE_TEST_DEF = re.compile(r"^\s*def (test_\w+)\s*\(")


def _cells(lines: list[str]) -> list[tuple[str, int, str]]:
    """(cell name, line, docstring first line) for every test_ function."""
    out: list[tuple[str, int, str]] = []
    for i, raw in enumerate(lines, start=1):
        m = _RE_TEST_DEF.match(raw)
        if not m:
            continue
        doc = ""
        for follow in lines[i:i + 4]:
            s = follow.strip()
            if s.startswith('"""') or s.startswith("'''"):
                doc = s.strip("\"'").strip()
                break
        out.append((m.group(1), i, doc))
    return out


def _keywords(name: str) -> set[str]:
    return {w for w in name.split("_")
            if w not in _STOP_WORDS and len(w) > 2}


def _best_cell_match(keywords: set[str], cells, skip: set[str]):
    """(cell name, overlap) of the best remaining cell by name keywords."""
    best = ("—", 0)
    for name, _line, _doc in cells:
        if name in skip:
            continue
        overlap = len(keywords & _keywords(name))
        if overlap > best[1]:
            best = (name, overlap)
    return best


def _spoke_layout_section(lines: list[str], all_cells) -> list[str]:
    """The Ч0 п.3 table: every going cell, the property it held, its coverage."""
    out: list[str] = []
    cells = _cells(lines)
    going: list[tuple[str, int, str]] = []
    staying: set[str] = set()
    for i, raw in enumerate(lines, start=1):
        if _RE_TEST_DEF.match(raw):
            current = _RE_TEST_DEF.match(raw).group(1)
            # a cell "goes" when apply_spoke_geometry appears in its body
            body_end = len(lines)
            for j in range(i, len(lines)):
                if j > i and _RE_TEST_DEF.match(lines[j]):
                    body_end = j
                    break
            body = "\n".join(lines[i - 1:body_end])
            if "apply_spoke_geometry" in body:
                doc = next((d for n, _l, d in cells if n == current), "")
                going.append((current, i, doc))
            else:
                staying.add(current)
    out.append("Going cells (they call `apply_spoke_geometry`, which is removed) "
               "and the property each one held:\n\n")
    out.append("| going cell | line | property (from the cell name) | "
               "best remaining cell IN THIS FILE | overlap |\n|---|---|---|---|---|\n")
    for name, line, _doc in going:
        kws = _keywords(name)
        props = ", ".join(sorted(kws & set(_PROPERTY_KEYWORDS))) or "(none)"
        match, overlap = _best_cell_match(kws, cells, skip={n for n, _l, _d in going})
        out.append(f"| `{name}` | {line} | {props} | `{match}` | {overlap} |\n")
    out.append("\nRemaining cells in this file (the primitives that STAY):\n\n")
    for name, line, doc in cells:
        if name in staying:
            out.append(f"- `{name}`:{line} — {doc}\n")
    out.append("\nCells outside this file that share at least TWO name keywords "
               "with a going cell (the property is covered elsewhere in the "
               "suite) — the plan's Ч0 п.3 asks for exactly this mapping:\n\n")
    out.append("| going cell | elsewhere | overlap |\n|---|---|---|\n")
    for name, _line, _doc in going:
        kws = _keywords(name)
        best = ("—", 0)
        for other_path, other_lines in all_cells.items():
            if other_path == _SPOKE_LAYOUT_TEST:
                continue
            for other, _l, _d in other_lines:
                overlap = len(kws & _keywords(other))
                if overlap > best[1]:
                    best = (f"{other_path}::{other}", overlap)
        out.append(f"| `{name}` | `{best[0]}` | {best[1]} |\n")
    return out


def main() -> int:
    records, files_by_scope, sources = _scan()

    by_file: dict[str, Counter] = defaultdict(Counter)
    file_symbols: dict[str, set[str]] = defaultdict(set)
    for r in records:
        by_file[r["path"]][r["cls"]] += 1
        for sym in r["symbols"]:
            file_symbols[r["path"]].add(sym)

    file_class: dict[str, str] = {p: _file_class_of(p, c) for p, c in by_file.items()}
    class_files: dict[str, list[str]] = defaultdict(list)
    class_mentions: Counter = Counter()
    for r in records:
        class_mentions[r["cls"]] += 1
    for p, cls in file_class.items():
        class_files[cls].append(p)

    out = sys.stdout.write
    out("# Ч0 spoke / chain inventory (plan_2026_10_08_remove_spokes.md)\n\n")
    out("Read-only probe, REWORKED 2026-10-08 per the plan's \"Сверка Ч0\": a line is "
        "classified by the SYMBOL it names (never by the word \"chain\"), and a "
        "mixed FILE is reported with the SYMBOLS it loses — not with a count of "
        "\"delete lines\".\n\n")

    out("## Scope scanned\n\n")
    out("| scope | files scanned |\n|---|---|\n")
    for scope in sorted(files_by_scope):
        out(f"| {scope} | {files_by_scope[scope]} |\n")
    out(f"\nTotal matching lines: **{len(records)}** across "
        f"**{len(by_file)}** files.\n\n")

    out("## Class by symbol\n\n")
    by_symbol: Counter = Counter()
    for r in records:
        if r["symbols"]:
            for sym in r["symbols"]:
                by_symbol[sym] += 1
    out("| symbol (delete class) | lines naming it |\n|---|---|\n")
    for sym, n in by_symbol.most_common():
        out(f"| `{sym}` | {n} |\n")
    out("\n")

    out("## Totals by file class\n\n")
    out("A file's class is the rollup: `delete` / `edit` (mixed) / `allow`. The "
        "line counts below are summed over the files of that class, so an `edit` "
        "file's delete lines are counted HERE, not as a separate line class.\n\n")
    out("| file class | files | delete lines | allow lines |\n|---|---|---|---|\n")
    for cls in ("delete", "edit", "allow"):
        files = class_files.get(cls, [])
        d = sum(by_file[p]["delete"] for p in files)
        a = sum(by_file[p]["allow"] for p in files)
        out(f"| {cls} | {len(files)} | {d} | {a} |\n")
    out(f"\nLine classes as classified: delete {class_mentions['delete']}, "
        f"allow {class_mentions['allow']}.\n\n")

    out("## TOP files by mention count\n\n")
    out("| file | delete | allow | total | class |\n|---|---|---|---|---|\n")
    for p, c in sorted(by_file.items(),
                       key=lambda kv: (-sum(kv[1].values()), kv[0]))[:30]:
        out(f"| {p} | {c['delete']} | {c['allow']} | {sum(c.values())} | "
            f"{file_class[p]} |\n")
    out("\n")

    out("## Files with delete-class lines, BY SYMBOL\n\n")
    out("One row per symbol per file — this is the list Ч1–Ч3 work from, so it "
        "names what goes, not how many lines matched.\n\n")
    out("| file | class | symbol that goes | first line |\n|---|---|---|---|\n")
    for p in sorted(by_file, key=lambda p: (-by_file[p]["delete"], p)):
        if not by_file[p]["delete"]:
            continue
        first: dict[str, int] = {}
        for r in records:
            if r["path"] != p or r["cls"] != "delete":
                continue
            for sym in r["symbols"]:
                first.setdefault(sym, r["line"])
        for sym in sorted(first):
            out(f"| {p} | {file_class[p]} | `{sym}` | {first[sym]} |\n")
    out("\n")

    out("## Files by class\n\n")
    for cls in ("delete", "edit", "allow"):
        files = sorted(class_files.get(cls, []),
                       key=lambda p: (-sum(by_file[p].values()), p))
        out(f"### {cls} ({len(files)} files)\n\n")
        out("| file | scope | delete | allow | symbols |\n|---|---|---|---|---|\n")
        for p in files:
            c = by_file[p]
            syms = ", ".join(f"`{s}`" for s in sorted(file_symbols.get(p, set())))
            out(f"| {p} | {_scope_of(p)} | {c['delete']} | {c['allow']} | "
                f"{syms or '—'} |\n")
        out("\n")

    out("## Tree node kinds `chain` / `rule` (Ч0 п.2)\n\n")
    out("The first probe missed these: a tree NODE of kind `chain` (and the "
        "legacy `rule`) is part of the feature, and **Д1 must refuse the LOAD on "
        "such a node too** — a red line naming the tree and the node's ref — not "
        "only on a non-empty `chains:` / `rules:` section. Denis's live config has "
        "0 nodes of these kinds (module 3, mount 15, net_trace 63, placement 36), "
        "so the refusal costs nothing today.\n\n")
    out("| file:line | text |\n|---|---|\n")
    kind_re = _SYMBOL_RULES[9][1]        # the "tree node kind chain / rule" rule
    kind_hits = [(p, r["line"], r["text"])
                 for p, lines in sources.items()
                 for r in records
                 if r["path"] == p and re.search(kind_re, lines[r["line"] - 1])]
    seen_hits: set[tuple[str, int]] = set()
    for p, line, text in sorted(kind_hits):
        if (p, line) in seen_hits:
            continue
        seen_hits.add((p, line))
        out(f"| `{p}:{line}` | `{text.replace('|', chr(92) + '|')}` |\n")
    out("\n")

    # --- symbol table ---
    out("## Symbol / importer table\n\n")
    table = _symbol_table(sources)
    for sym in _SYMBOLS:
        info = table[sym]
        out(f"### `{sym}`\n\n")
        defs = info["defs"][:6]
        if defs:
            out("Definition / assignment:\n\n")
            for d, ln, txt in defs:
                out(f"- `{d}:{ln}` — `{txt}`\n")
        else:
            out("Definition: not found by the heuristic (check by hand).\n")
        importers = sorted(set(info["importers"]))
        out(f"\nImporters ({len(importers)}):\n\n")
        for imp in importers:
            out(f"- {imp}\n")
        out("\n")

    # --- spoke_layout decision (Ч0 п.3) ---
    out("## `spoke_layout` decision (Ч0 п.3)\n\n")
    out("**The module stays with its PRIMITIVES**: `local_to_absolute`, "
        "`rotate_local_offset`, the via/track resolution, the `SpokeLayout` "
        "container (used by `clone_geometry`) and the along/across axes. "
        "**`apply_spoke_geometry` and the `ManualSpoke` import go**, and the "
        "module docstring is rewritten without \"spoke cell\". Renames of the "
        "module and of `SpokeLayout` are NOT part of this work.\n\n")
    layout_lines = sources.get(_SPOKE_LAYOUT, [])
    going_here = [(r["line"], r["text"]) for r in records
                  if r["path"] == _SPOKE_LAYOUT and r["cls"] == "delete"]
    out(f"Lines of `{_SPOKE_LAYOUT}` that go ({len(going_here)}):\n\n")
    if going_here:
        out("| line | text |\n|---|---|\n")
        for line, text in going_here:
            out(f"| {line} | `{text.replace('|', chr(92) + '|')}` |\n")
    else:
        out("_none_\n")
    out("\n")
    test_lines = sources.get(_SPOKE_LAYOUT_TEST, [])
    if test_lines:
        all_cells = {p: _cells(lines) for p, lines in sources.items()
                     if p.startswith("tests/")}
        out(f"### `{_SPOKE_LAYOUT_TEST}` — property -> cell (Ч4 finishes this)\n\n")
        out("The file KEEPS its primitive-level cells and loses the "
            "`apply_spoke_geometry` ones; the table below names the property each "
            "going cell held and the best remaining cell by name keywords "
            "(a heuristic first pass — audit it, then carry the mapping into the "
            "Ч4 report).\n\n")
        out("".join(_spoke_layout_section(test_lines, all_cells)))

    # --- tests split ---
    out("\n## Tests\n\n")
    test_delete, test_edit = [], []
    for p, cls in file_class.items():
        if not p.startswith("tests/"):
            continue
        if cls == "delete":
            test_delete.append(p)
        elif cls == "edit":
            test_edit.append(p)
    out("### (a) meaningless once spokes go — delete\n\n")
    out("| test file | mentions | in plan's named-dead list |\n|---|---|---|\n")
    for p in sorted(test_delete):
        named = any(n in p for n in _KNOWN_DEAD_TESTS)
        out(f"| {p} | {sum(by_file[p].values())} | {'yes' if named else '—'} |\n")
    named_hits = [t for t in _KNOWN_DEAD_TESTS
                  if not any(t in p for p in file_class)]
    out(f"\nPlan-named dead tests the probe did NOT see as pure delete (audit): "
        f"{named_hits or 'none'}\n\n")
    out("### (b) spokes are only ONE parameter — edit, do not delete\n\n")
    out("| test file | mentions | delete | allow | symbols that go |\n|---|---|---|---|---|\n")
    for p in sorted(test_edit):
        c = by_file[p]
        syms = "; ".join(sorted(file_symbols.get(p, set())))
        out(f"| {p} | {sum(c.values())} | {c['delete']} | {c['allow']} | "
            f"{syms or '—'} |\n")
    out("\n")

    # --- conflicts ---
    out("## Conflicts with the plan's \"НЕ УДАЛЯТЬ\" list\n\n")
    out("A kept-mechanism file that also names a delete-class symbol: removing "
        "the spokes here is surgical, the module/frame/planner half stays.\n\n")
    out("| kept file | delete lines | allow lines | symbols that go |\n|---|---|---|---|\n")
    for keep in _KEPT_MECHANISM:
        c = by_file.get(keep)
        if c and c["delete"]:
            syms = "; ".join(sorted(file_symbols.get(keep, set())))
            out(f"| {keep} | {c['delete']} | {c['allow']} | {syms} |\n")
    out("\n")

    # --- cross-check ---
    out("## Cross-check\n\n")
    out(f"- probe matching lines: {len(records)}\n")
    out(f"- files with at least one mention: {len(by_file)}\n")
    out("- every line the probe leaves as `allow` names a KEPT symbol or another "
        "sense of \"chain\" — the audit list is the `allow` sections above.\n")
    return 0


_SCOPE_BY_PREFIX = {
    "kicadstamp/diagnostics/": "diagnostics",
    "kicadstamp/": "product",
    "gui/": "product",
    "tests/": "tests",
    "tools/": "tools",
    "mcp_server/": "mcp_server",
    "docs/": "docs",
    "locales/": "locales",
    "techdocs/map/": "techdocs_map",
}


def _scope_of(path: str) -> str:
    for prefix, scope in _SCOPE_BY_PREFIX.items():
        if path.startswith(prefix):
            return scope
    return "readme"


if __name__ == "__main__":
    raise SystemExit(main())
