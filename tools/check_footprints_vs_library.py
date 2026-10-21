"""tools/check_footprints_vs_library.py — read-only: are the board's footprints still what the library has?

Catches two known API damages on a SAVED board file:
  * 3D models gone  — kicad-python's FootprintInstance.orientation setter drops them
    (techdocs/bugreport/2026_10_15_footprint_update/1_…);
  * polygon closing point gone — IPC UpdateItems drops the repeated last point of a
    footprint fp_poly, DRC then says lib_footprint_mismatch (…/3_…).

For every footprint it opens its library copy (found through the project's and the user's
fp-lib-table, else <lib-dir>/<lib>.pretty) and compares the number of (model …) entries and
the point count of every fp_poly, in order. Nothing is written; KiCad is not needed. Save the
board first — the file is what is read.

usage: .venv/bin/python tools/check_footprints_vs_library.py <board.kicad_pcb>
       [--lib-dir /usr/share/kicad/footprints] [--all]   (--all: print clean footprints too)
"""
import sys
from pathlib import Path


def _parse(text: str):
    """Minimal s-expression reader: nested lists of str atoms (quotes stripped)."""
    stack, cur, i, n = [], [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "(":
            stack.append(cur)
            cur = []
            i += 1
        elif c == ")":
            done, cur = cur, stack.pop()
            cur.append(done)
            i += 1
        elif c == '"':
            j = i + 1
            while text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            cur.append(text[i + 1:j])
            i = j + 1
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            cur.append(text[i:j])
            i = j
    return cur[0]


def _children(node, tag):
    return [c for c in node if isinstance(c, list) and c and c[0] == tag]


def _signature(fp):
    """(model count, [points per fp_poly in order])."""
    models = len(_children(fp, "model"))
    polys = []
    for poly in _children(fp, "fp_poly"):
        pts = _children(poly, "pts")
        polys.append(len(_children(pts[0], "xy")) if pts else 0)
    return models, polys


def _ref(fp):
    for prop in _children(fp, "property"):
        if len(prop) > 2 and prop[1] == "Reference":
            return prop[2]
    for text in _children(fp, "fp_text"):
        if len(text) > 2 and text[1] == "reference":
            return text[2]
    return "?"


def _expand(uri: str, env: dict) -> str:
    for key, value in env.items():
        uri = uri.replace("${" + key + "}", value)
    return uri


def _lib_dirs(board_path: Path, default_dir: Path) -> dict:
    """{library nickname: .pretty dir} from the project's and the user's fp-lib-table,
    with ${KIPRJMOD} and the user's path variables (kicad_common.json) expanded;
    a nickname not in any table falls back to <default_dir>/<nick>.pretty."""
    import json
    cfg = Path.home() / ".config" / "kicad" / "10.0"
    env = {"KIPRJMOD": str(board_path.parent.resolve()),
           "KICAD10_FOOTPRINT_DIR": str(default_dir)}
    try:
        env.update(json.loads((cfg / "kicad_common.json").read_text(encoding="utf-8"))
                   .get("environment", {}).get("vars", {}) or {})
    except (OSError, ValueError):
        pass
    dirs = {}
    for table in (cfg / "fp-lib-table", board_path.parent / "fp-lib-table"):   # project wins
        if not table.is_file():
            continue
        for lib in _children(_parse(table.read_text(encoding="utf-8")), "lib"):
            fields = {c[0]: c[1] for c in lib if isinstance(c, list) and len(c) > 1}
            if fields.get("type") == "KiCad" and "name" in fields and "uri" in fields:
                dirs[fields["name"]] = Path(_expand(fields["uri"], env))
    return dirs


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    board_path = Path(argv[0])
    lib_dir = Path(argv[argv.index("--lib-dir") + 1]) if "--lib-dir" in argv else Path("/usr/share/kicad/footprints")
    show_all = "--all" in argv
    libs = _lib_dirs(board_path, lib_dir)
    board = _parse(board_path.read_text(encoding="utf-8"))
    fps = _children(board, "footprint")
    print(f"board {board_path.name}: {len(fps)} footprint(s); library {lib_dir}")
    bad = missing = 0
    cache = {}
    for fp in fps:
        lib_id = fp[1]
        ref = _ref(fp)
        lib, _, name = lib_id.partition(":")
        mod = libs.get(lib, lib_dir / f"{lib}.pretty") / f"{name}.kicad_mod"
        if not mod.is_file():
            print(f"  {ref:6} {lib_id}: library copy not found — not checked")
            missing += 1
            continue
        if mod not in cache:
            cache[mod] = _signature(_parse(mod.read_text(encoding="utf-8")))
        want = cache[mod]
        got = _signature(fp)
        problems = []
        if got[0] < want[0]:
            problems.append(f"3D models {got[0]} of {want[0]}")
        if got[1] != want[1]:
            problems.append(f"fp_poly points {got[1]} vs library {want[1]}")
        if problems:
            bad += 1
            print(f"  {ref:6} {lib_id}: " + "; ".join(problems))
        elif show_all:
            print(f"  {ref:6} {lib_id}: ok (models {got[0]}, fp_poly {got[1]})")
    print(f"result: {len(fps) - bad - missing} ok, {bad} damaged, {missing} not checked")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
