#!/usr/bin/env python3
# kicadstamp/diagnostics/deepseek_probe_spokes_inventory_2026_10_08.py
"""Ч0 inventory probe for the "remove spokes and chains" plan
(plan_2026_10_08_remove_spokes.md, section Ч0).

READ-ONLY. This probe never opens a live profile and never writes anything:
it walks the source tree, finds every line that mentions a spoke / chain, and
classifies each such LINE into one of three classes:

  * delete  — the mention really is a spoke / chain-of-spokes (``chains:``,
              ``Chain``, ``ManualSpoke``, ``spoke_pad``, ``SOURCE_SPOKE``,
              "Add Spoke" / "Extract Spoke" / "placed by chain", ...);
  * edit    — the file is MIXED: the spoke half goes, the rest stays (a file
              that has both ``delete`` and ``allow`` lines rolls up to edit);
  * allow   — either a member of the plan's "НЕ УДАЛЯТЬ" list (the
              ``SPOKE_LEVEL_ROLE_PLACEHOLDER`` name and its value ``__spoke__``
              in registry keys, ``geometry/spoke_layout.py``, ``ComponentPool``
              / ``component_resolver`` / ``manual_position_calculator``,
              ``thermal_via_arrays:``, the via ``offset_*`` fields) or the word
              "chain" used in ANOTHER sense (sheet-name chain, ``include:``
              import chain, anchor / point / tree chain).

The word "chain" is heavily overloaded in this codebase (chained points,
chained includes, anchor chains, "the chain would loop"), so the probe NEVER
deletes a bare "chain" word: a "chain" line is only a spoke-chain when it
carries an explicit spoke-chain marker (``chains:``, a quoted ``"chains"``,
``.chains``, ``chain_dock``, ``chain_effective_name``, "placed by chain", the
``Chain`` symbol, the ``rules:`` / ``Rule`` alias, ...). Everything else is
reported as ``allow`` with reason "other meaning".

The classification is a heuristic first pass and it is DELIBERATELY noisy: the
report prints the matching line text next to every class so a human (Claude,
before Ч1) can audit and overrule. Nothing here is a fix — Ч0 only counts.

The probe also emits:
  * a SYMBOL / importer table (``Chain``, ``ManualSpoke``, ``chains``,
    the ``rules`` alias, ``Rule``, ``spoke_pad``, ``SOURCE_SPOKE``,
    ``apply_spoke_geometry``, ``SPOKE_LEVEL_ROLE_PLACEHOLDER``) — where each
    symbol is defined and who imports it;
  * the TESTS split: files that become meaningless once spokes go (delete)
    versus files where spokes are just ONE parameter among others (edit);
  * the CONFLICTS with "НЕ УДАЛЯТЬ": a kept-mechanism file that also carries
    delete-class spoke code, where removal must be surgical.

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

# --- detection regexes -----------------------------------------------------
_RE_SPOKE = re.compile(r"spoke", re.I)
_RE_CHAIN = re.compile(r"chain", re.I)  # any chain-family token, then refined

# A "chain" line really IS a spoke-chain only when one of these markers is
# present. Kept narrow on purpose (see the module docstring).
_RE_CHAIN_SPOKE = re.compile(
    r"chains?\s*:|[\"']chains?[\"']|\.chains\b|chains\s*=|"
    r"chain_dock|chains_nav|chain_effective_name|collect_chains_by_net|"
    r"placed by chain|add chain|chain's|chain\(s\)|chain\s+#|"
    r"chain dict|chain list|chain form|chain row|chain leaf|chain_dict|"
    r"chain_net|chain_name|upsert_list_entry|net's chains|"
    r"chains? of (cell|spoke)",
    re.I,
)
# The "chain" word used in a NON-spoke sense — kept to the three senses the
# plan names (sheet-name chain, include:/import chain, anchor chain) plus the
# verb forms "chained/chaining". Everything else defaults to "spoke-chain",
# because the plan's own final grep for the product is `spoke|ManualSpoke|\bchains?\b`.
_RE_CHAIN_OTHER = re.compile(
    r"chained|chaining|anchor[ -]?chain|include[ :]+chain|import[ :]+chain|"
    r"chain (?:loops?|would loop)|sheet[ -]?name chain|point[ -]?chain|"
    r"entity[ -]?anchor|tree[ -]?anchor|link chain|dependency chain|"
    r"цепочк[аиу] (якор|имён|имен|импорт|лист)",
    re.I,
)
_RE_CHAIN_SYM = re.compile(r"\bChain\b")          # the dataclass symbol
_RE_RULE_ALIAS = re.compile(r"rules\s*:|[\"']rules[\"']|\.rules\b|rules\s*=|Rule\b")

# Spoke-only symbols that must go with the feature.
_RE_SPOKE_ONLY = re.compile(
    r"ManualSpoke|SOURCE_SPOKE|spoke_pad|apply_spoke_geometry|"
    r"spoke_extraction|spoke_extract|extract_spoke|spoke_redraw|spoke_pad_edit",
)
# Kept-mechanism symbols (from "НЕ УДАЛЯТЬ"): a line that mixes these with a
# spoke-only symbol is exactly the kind of surgical edit the plan warns about.
_RE_KEPT_SYM = re.compile(
    r"spoke_layout|ComponentPool|component_pool|component_resolver|"
    r"manual_position_calculator|cell_frame|tree_position|\bplanner\b|"
    r"thermal_via|clone_geometry|cell_anchor|anchor_graph",
)

# "НЕ УДАЛЯТЬ" allow patterns.
_RE_PLACEHOLDER = re.compile(r"SPOKE_LEVEL_ROLE_PLACEHOLDER|__spoke__")
_RE_VIA_OFFSET = re.compile(r"offset_along_mm|offset_across_mm")
_RE_GEOM_MODULE = re.compile(r"spoke_layout")

# Files the plan explicitly keeps WHOLE (geometry primitives, placeholder value).
# NOTE: commented out on purpose for spoke_layout.py — the plan keeps the module
# but it still imports ManualSpoke, so it is a CONFLICT, not a clean allow. The
# probe lets the line rules decide and flags the conflict instead.
_WHOLE_FILE_ALLOW = set()

# Text files scanned inside techdocs/map (skip images/binaries).
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


def _classify_line(line: str) -> tuple[str, str] | None:
    """Return (class, reason) for one line, or None when it is not a mention.

    Classes: "delete" / "allow" (an "allow" line inside a file that also has
    "delete" lines makes the FILE "edit"; a file of only "allow" lines is
    "allow")."""
    has_spoke = bool(_RE_SPOKE.search(line))
    has_chain = bool(_RE_CHAIN.search(line))
    if not (has_spoke or has_chain):
        return None

    # 2. registry role placeholder (name is renameable, VALUE is frozen).
    if _RE_PLACEHOLDER.search(line):
        return ("allow", "registry role placeholder (name renameable, value frozen)")

    # 3. cell via offset fields — part of the cell format, not spokes.
    if _RE_VIA_OFFSET.search(line):
        return ("allow", "cell via offset_*_mm fields (format, not spokes)")

    spoke_only = bool(_RE_SPOKE_ONLY.search(line))
    kept_sym = bool(_RE_KEPT_SYM.search(line))

    if has_spoke:
        # 4. geometry module kept whole — but only when nothing spoke-only is
        #    on the same line; mixing is a surgical edit (conflict).
        if _RE_GEOM_MODULE.search(line) and not spoke_only:
            return ("allow", "kept geometry module spoke_layout (rotation/axes)")
        if spoke_only:
            if kept_sym:
                return ("edit", "spoke-only symbol on a kept-mechanism line (surgical)")
            return ("delete", "spoke-only symbol")
        if "spoke" in line.lower():
            return ("delete", "bare 'spoke' mention")

    # 5. chain handling. A bare "chain" defaults to the spoke-chain meaning
    #    (that is what the plan's final product grep looks for); only the three
    #    explicit other senses flip it to allow.
    if has_chain:
        if _RE_CHAIN_SYM.search(line) or _RE_RULE_ALIAS.search(line):
            return ("delete", "Chain symbol / rules alias")
        if _RE_CHAIN_SPOKE.search(line):
            return ("delete", "spoke-chain marker")
        if _RE_CHAIN_OTHER.search(line):
            return ("allow", "other meaning of 'chain' (sheet-name/include/anchor chain)")
        return ("delete", "bare 'chain'/'chains' — matches the spoke feature grep")
    return None


def _scan():
    """Return (records, files_by_scope, scannable)."""
    records: list[dict] = []
    files_by_scope: Counter = Counter()
    for scope, path in _iter_files():
        files_by_scope[scope] += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = _rel(path)
        for lineno, raw in enumerate(text.splitlines(), start=1):
            res = _classify_line(raw)
            if res is None:
                continue
            cls, reason = res
            records.append({
                "path": rel, "scope": scope, "line": lineno,
                "cls": cls, "reason": reason, "text": raw.strip(),
            })
    return records, files_by_scope


# --- symbol table ----------------------------------------------------------
_SYMBOLS = (
    "Chain", "ManualSpoke", "chains", "rules", "Rule",
    "spoke_pad", "SOURCE_SPOKE", "apply_spoke_geometry",
    "SPOKE_LEVEL_ROLE_PLACEHOLDER",
)
_RE_IMPORT = re.compile(r"^\s*(?:from\s+\S+\s+import|import)\b")


def _symbol_table():
    """{symbol: {"defs": [(path, line, text)], "importers": [...]}}"""
    table = {s: {"defs": [], "importers": []} for s in _SYMBOLS}
    for _scope, path in _iter_files():
        rel = _rel(path)
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, raw in enumerate(lines, start=1):
            stripped = raw.strip()
            for sym in _SYMBOLS:
                word = re.compile(rf"\b{re.escape(sym)}\b")
                if not word.search(raw):
                    continue
                if re.match(rf"(class|def)\s+{re.escape(sym)}\b", stripped) or \
                   re.match(rf"{re.escape(sym)}\s*[:=]", stripped):
                    table[sym]["defs"].append((rel, i, stripped))
                if _RE_IMPORT.match(raw) and word.search(raw):
                    table[sym]["importers"].append(rel)
    return table


# A file whose BASENAME encodes the feature is the feature's own file, so the
# whole file goes; any other file that merely contains delete-class lines is
# MIXED (the spoke half goes, the rest stays) -> "edit".
_RE_FEATURE_NAME = re.compile(r"spoke|chain|rules[_-]?to[_-]?chains|convert_rules", re.I)
# The plan keeps these WHOLE ("НЕ УДАЛЯТЬ"): the geometry primitives, the
# regression test for them, and the registry placeholder constant. NOTE that
# spoke_layout.py still imports ManualSpoke — that is reported as a CONFLICT
# rather than hidden behind this override.
_KEEP_WHOLE = {
    "kicadstamp/geometry/spoke_layout.py",
    "tests/geometry/test_spoke_layout.py",
    "kicadstamp/constants.py",
}


def _file_class_of(path: str, counts: Counter) -> str:
    has_delete = counts["delete"] > 0
    if path in _KEEP_WHOLE:
        return "allow"
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
    "kicadstamp/geometry/spoke_layout.py",
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
    "tests/geometry/test_spoke_layout.py",
)


def main() -> int:
    records, files_by_scope = _scan()

    by_file: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        by_file[r["path"]][r["cls"]] += 1

    file_class: dict[str, str] = {
        p: _file_class_of(p, c) for p, c in by_file.items()
    }
    class_files: dict[str, list[str]] = defaultdict(list)
    class_mentions: Counter = Counter()
    for r in records:
        class_mentions[r["cls"]] += 1
    for p, cls in file_class.items():
        class_files[cls].append(p)

    out = sys.stdout.write
    out("# Ч0 spoke / chain inventory (plan_2026_10_08_remove_spokes.md)\n\n")
    out("Read-only probe. Unit = one matching source LINE. FILE class: a file whose "
        "name encodes the feature (spoke / chain) is `delete`; any other file with "
        "delete-class lines is `edit` (mixed — the spoke half goes, the rest stays); "
        "a file with only allow lines is `allow`.\n\n")

    out("## Scope scanned\n\n")
    out("| scope | files scanned |\n|---|---|\n")
    for scope in sorted(files_by_scope):
        out(f"| {scope} | {files_by_scope[scope]} |\n")
    out(f"\nTotal matching lines: **{len(records)}** across "
        f"**{len(by_file)}** files.\n\n")

    out("## Totals by class\n\n")
    out("| class | files | mentions (lines) |\n|---|---|---|\n")
    for cls in ("delete", "edit", "allow"):
        out(f"| {cls} | {len(class_files.get(cls, []))} | {class_mentions[cls]} |\n")
    out("\n")

    out("## TOP files by mention count\n\n")
    out("| file | delete | edit | allow | total | class |\n|---|---|---|---|---|---|\n")
    for p, c in sorted(by_file.items(),
                       key=lambda kv: (-sum(kv[1].values()), kv[0]))[:30]:
        tot = sum(c.values())
        out(f"| {p} | {c['delete']} | {c['edit']} | {c['allow']} | {tot} | "
            f"{file_class[p]} |\n")
    out("\n")

    out("## Files by class\n\n")
    for cls in ("delete", "edit", "allow"):
        files = sorted(class_files.get(cls, []),
                       key=lambda p: (-sum(by_file[p].values()), p))
        out(f"### {cls} ({len(files)} files)\n\n")
        out("| file | scope | delete | allow |\n|---|---|---|---|\n")
        for p in files:
            c = by_file[p]
            out(f"| {p} | {_scope_of(p)} | {c['delete']} | {c['allow']} |\n")
        out("\n")

    # --- symbol table ---
    out("## Symbol / importer table\n\n")
    table = _symbol_table()
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

    # --- tests split ---
    out("## Tests\n\n")
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
    out("| test file | mentions | delete | allow |\n|---|---|---|---|\n")
    for p in sorted(test_edit):
        c = by_file[p]
        out(f"| {p} | {sum(c.values())} | {c['delete']} | {c['allow']} |\n")
    out("\n")

    # --- conflicts ---
    out("## Conflicts with the plan's \"НЕ УДАЛЯТЬ\" list\n\n")
    out("A kept-mechanism file that also carries delete-class spoke code: removing "
        "the spokes here is surgical, the module/frame/planner half stays.\n\n")
    out("| kept file | delete lines | allow lines | note |\n|---|---|---|---|\n")
    for keep in _KEPT_MECHANISM:
        c = by_file.get(keep)
        if c and c["delete"]:
            out(f"| {keep} | {c['delete']} | {c['allow']} | keep mechanism, drop spoke branch |\n")
    out("\n")
    out("Lines that mix a kept-mechanism symbol with a spoke-only symbol:\n\n")
    mixed = [r for r in records if r["cls"] == "edit"]
    if mixed:
        out("| file:line | text |\n|---|---|\n")
        for r in sorted(mixed, key=lambda r: (r["path"], r["line"]))[:40]:
            txt = r["text"].replace("|", "\\|")
            out(f"| `{r['path']}:{r['line']}` | `{txt}` |\n")
    else:
        out("_none_\n")
    out("\n")

    # --- cross-check ---
    out("## Cross-check\n\n")
    out(f"- probe matching lines: {len(records)}\n")
    out(f"- files with at least one mention: {len(by_file)}\n")
    out("- compare against `grep -riE \"spoke|chains?\"` per scope; any gap means "
        "a file the walker did not reach.\n")

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
