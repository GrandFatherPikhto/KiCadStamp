#!/usr/bin/env python3
# tools/project_map.py
"""Generate `techdocs/map/index.md` — a code-derived pointer to the project.

Standard library ONLY (`ast`, `pathlib`, `subprocess`, `tokenize` is not needed):
no Qt, no KiCad, no import of project modules. The source is PARSED (`ast`), never
imported, so the map builds even where Qt/KiCad are absent. Output is DETERMINISTIC
(sorted everywhere): a second run over unchanged code is byte-identical.

What it writes, per the project-map plan (plan_2026_10_05_project_map.md, Part 1):

  1. a header with the SHA and date of the commit it was built from and whether the
     working tree was dirty;
  2. per package: every module — path, total lines, CODE lines (docstrings,
     comments and blanks excluded), the module docstring's first line, and a
     `⚠ >800` mark (rule Д8);
  3. the module's public API: top-level classes/functions without a leading `_`,
     PLUS every name listed in `__all__` (so an explicitly exported private counts);
  4. internal import edges: which project modules it imports (INCLUDING imports
     inside functions) and which import it;
  5. `tests/repo/` guards: each file's docstring first line and its test names;
  6. `kicadstamp/diagnostics/` as a one-line counter (excluded from the detail).

Usage:
    .venv/bin/python tools/project_map.py [OUTPUT] [--root ROOT]

OUTPUT defaults to techdocs/map/index.md; ROOT defaults to the repository root
(the directory above tools/). ROOT exists for the tests, which build a temporary
tree (e.g. to plant a module with a syntax error).
"""
from __future__ import annotations

import argparse
import ast
import subprocess
from pathlib import Path

# Package directories listed in the map, in this exact order (deterministic).
PACKAGES = [
    "kicadstamp",
    "kicadstamp/config",
    "kicadstamp/placement",
    "kicadstamp/geometry",
    "kicadstamp/kicad",
    "gui",
    "gui/docks",
    "mcp_server",
    "tools",
]
# Excluded from the detail; reported as a counter.
DIAGNOSTICS_DIR = "kicadstamp/diagnostics"
# Rule Д8 of the map.
BIG_FILE_LINES = 800
# Internal top-level packages: an absolute import is "internal" iff it starts
# with one of these.
INTERNAL_TOP = ("kicadstamp", "gui", "mcp_server", "tools")


# ── git header ──────────────────────────────────────────────────────────────

def _git(root: Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=root, capture_output=True,
                             text=True, timeout=10)
    except Exception:  # noqa: BLE001 — a non-repo root is a valid input
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


# ── line counting ───────────────────────────────────────────────────────────

def _docstring_line_ranges(tree: ast.AST) -> set[int]:
    """Physical lines covered by module/class/function docstrings."""
    covered: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            end = getattr(first, "end_lineno", first.lineno)
            covered.update(range(first.lineno, end + 1))
    return covered


def _code_line_count(source: str, tree: ast.AST | None) -> int:
    """Non-blank, non-comment lines NOT inside a docstring. Falls back to a plain
    count when the source does not parse (a broken module is still listed)."""
    lines = source.splitlines()
    covered = _docstring_line_ranges(tree) if tree is not None else set()
    count = 0
    for number, line in enumerate(lines, 1):
        if number in covered:
            continue
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            continue
        count += 1
    return count


def _first_doc_line(node: ast.AST) -> str:
    doc = ast.get_docstring(node) if isinstance(
        node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) else None
    if not doc:
        return ""
    for line in doc.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


# ── module model ────────────────────────────────────────────────────────────

class Module:
    def __init__(self, root: Path, path: Path):
        self.root = root
        self.path = path
        self.rel = path.relative_to(root).as_posix()
        self.key = self.rel[:-3].replace("/", ".") if self.rel.endswith(".py") else self.rel
        self.source = ""
        self.tree: ast.AST | None = None
        self.parse_error = ""
        self.total_lines = 0
        self.code_lines = 0
        self.doc_line = ""
        self.api: list[tuple[str, int, str]] = []
        self.imports: set[str] = set()
        self._load()

    def _load(self) -> None:
        try:
            self.source = self.path.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            self.parse_error = f"{type(e).__name__}: {e}"
            return
        self.total_lines = len(self.source.splitlines())
        try:
            self.tree = ast.parse(self.source, filename=self.rel)
        except SyntaxError as e:
            self.parse_error = f"{type(e).__name__}: line {e.lineno}: {e.msg}"
            self.code_lines = _code_line_count(self.source, None)
            return
        self.code_lines = _code_line_count(self.source, self.tree)
        self.doc_line = _first_doc_line(self.tree)
        self._collect_api()
        self._collect_imports()

    def _collect_api(self) -> None:
        exported: set[str] = set()
        for node in getattr(self.tree, "body", []):
            if (isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "__all__"
                            for t in node.targets)):
                try:
                    for elt in node.value.elts:  # type: ignore[attr-defined]
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            exported.add(elt.value)
                except AttributeError:
                    pass
        entries: list[tuple[str, int, str]] = []
        defined: set[str] = set()
        for node in getattr(self.tree, "body", []):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
                name = node.name
                if name.startswith("_") and name not in exported:
                    continue
                entries.append((name, node.lineno, _first_doc_line(node)))
        # Names in __all__ that are NOT top-level defs (e.g. re-exported) count
        # too, with the line of the __all__ entry they came from.
        present = {name for name, _l, _d in entries}
        for node in getattr(self.tree, "body", []):
            if (isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "__all__"
                            for t in node.targets)):
                for elt in getattr(node.value, "elts", []):
                    if (isinstance(elt, ast.Constant)
                            and isinstance(elt.value, str)
                            and elt.value not in present
                            and elt.value not in defined):
                        entries.append((elt.value, getattr(elt, "lineno", node.lineno),
                                        ""))
        self.api = sorted(entries, key=lambda t: (t[1], t[0]))

    def _collect_imports(self) -> None:
        for node in ast.walk(self.tree):  # type: ignore[arg-type]
            if isinstance(node, ast.ImportFrom):
                for target in self._resolve_from_all(node):
                    self.imports.add(target)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in INTERNAL_TOP:
                        self.imports.add(alias.name)

    def _resolve_from_all(self, node: ast.ImportFrom) -> list[str]:
        """Every internal module a `from ... import ...` names (a bare
        `from . import b` names the submodule `b`, not the package)."""
        if node.level:
            parts = self.key.split(".")
            # level 1 -> the module's own package; each extra level goes up one.
            base = parts[:max(0, len(parts) - 1 - (node.level - 1))]
            prefix = ".".join(base)
            if node.module:
                return [f"{prefix}.{node.module}"]
            return [f"{prefix}.{a.name}" for a in node.names if a.name != "*"]
        if not node.module or node.module.split(".")[0] not in INTERNAL_TOP:
            return []
        return [node.module]


# ── rendering ───────────────────────────────────────────────────────────────

def _module_files(root: Path, package: str) -> list[Path]:
    directory = root / package
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.py") if p.name != "__init__.py"
                  or p.stat().st_size > 0)


def build_map(root: Path) -> str:
    modules: list[Module] = []
    for package in PACKAGES:
        for path in _module_files(root, package):
            modules.append(Module(root, path))
    known_keys = {m.key for m in modules}

    # Keep only edges whose TARGET is a known module (internal, resolved).
    for m in modules:
        m.imports = {t for t in m.imports if t in known_keys}
    reverse: dict[str, set[str]] = {m.key: set() for m in modules}
    for m in modules:
        for target in m.imports:
            reverse.setdefault(target, set()).add(m.key)

    out: list[str] = []
    out.append("# Project map — index")
    out.append("")
    out.append("Generated by `.venv/bin/python tools/project_map.py` "
               "(Windows: `.venv\\Scripts\\python.exe tools\\project_map.py`).")
    out.append("")
    out.append(f"- SHA: {_git(root, 'rev-parse', 'HEAD') or 'unknown'}")
    out.append(f"- commit date: {_git(root, 'log', '-1', '--format=%cs') or 'unknown'}")
    dirty = bool(_git(root, "status", "--porcelain"))
    out.append(f"- working tree dirty: {'yes' if dirty else 'no'}")
    out.append("")

    # ── modules per package ────────────────────────────────────────────────
    out.append("## Modules")
    out.append("")
    for package in PACKAGES:
        in_pkg = [m for m in modules if m.rel.startswith(package + "/")
                  and m.rel.count("/") == package.count("/") + 1]
        if not in_pkg:
            continue
        out.append(f"### {package}/")
        out.append("")
        out.append("| module | lines | code | first docstring line |")
        out.append("|---|---:|---:|---|")
        for m in sorted(in_pkg, key=lambda x: x.rel):
            flag = " ⚠ >800" if m.total_lines > BIG_FILE_LINES else ""
            doc = m.doc_line or "(без docstring)"
            if m.parse_error:
                doc = f"не разобран: {m.parse_error}"
            out.append(f"| {m.rel} | {m.total_lines}{flag} | {m.code_lines} | {doc} |")
        out.append("")

    # ── public API ─────────────────────────────────────────────────────────
    out.append("## Public API")
    out.append("")
    for m in sorted(modules, key=lambda x: x.rel):
        if m.parse_error:
            continue
        out.append(f"### {m.rel}")
        out.append("")
        if not m.api:
            out.append("(public API пуст)")
        for name, lineno, doc in m.api:
            out.append(f"- `{name}` — {lineno} — {doc or '(без docstring)'}")
        out.append("")

    # ── import edges ───────────────────────────────────────────────────────
    out.append("## Imports (internal)")
    out.append("")
    for m in sorted(modules, key=lambda x: x.rel):
        out.append(f"### {m.rel}")
        out.append("")
        incoming = sorted(reverse.get(m.key, ()))
        out.append("- imports: " + (", ".join(sorted(m.imports)) or "—"))
        out.append("- imported by: " + (", ".join(incoming) or "—"))
        out.append("")

    # ── tests/repo guards ──────────────────────────────────────────────────
    out.append("## Guards (tests/repo/)")
    out.append("")
    for path in sorted((root / "tests/repo").glob("test_*.py")):
        mod = Module(root, path)
        out.append(f"### {mod.rel}")
        out.append("")
        out.append(mod.doc_line or "(без docstring)")
        out.append("")
        for name, lineno, _doc in mod.api:
            out.append(f"- `{name}` — {lineno}")
        out.append("")

    # ── diagnostics counter ────────────────────────────────────────────────
    diag = sorted((root / DIAGNOSTICS_DIR).glob("*.py")) \
        if (root / DIAGNOSTICS_DIR).is_dir() else []
    out.append("## diagnostics")
    out.append("")
    out.append(f"`{DIAGNOSTICS_DIR}/`: {len(diag)} module(s) — excluded from the detail.")
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(prog="project_map.py")
    parser.add_argument("output", nargs="?", default=None,
                        help="output markdown (default: techdocs/map/index.md)")
    parser.add_argument("--root", default=None,
                        help="source root (default: the repository root)")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else repo_root
    output = (Path(args.output) if args.output else root / "techdocs/map/index.md")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_map(root), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
