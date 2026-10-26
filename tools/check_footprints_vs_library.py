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

usage: .venv/bin/python tools/check_footprints_vs_library.py <board.kicad_pcb | profile dir | profile .sexp>
       [--lib-dir <footprints dir>] [--all]   (--all: print clean footprints too)

Which library directory (the order KiCad itself follows): --lib-dir, else $KICAD10_FOOTPRINT_DIR,
else that same variable in kicad_common.json (from KiCad's settings directory FOR THIS PLATFORM),
else the platform default of a stock KiCad install. None found -> exit 2 and one line naming every
place looked at — never "not checked" per footprint.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kicadstamp import check_report        # noqa: E402


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


def _lib_generator(fp):
    """The library footprint's own KiLib_Generator value, or None."""
    for prop in _children(fp, "property"):
        if len(prop) > 2 and prop[1] == "KiLib_Generator":
            return prop[2]
    return None


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


def _kicad_settings_dir() -> Path:
    """Where KiCad 10 keeps the USER's settings on this platform — the ONE home of that path,
    used by both the `kicad_common.json` read and the user's `fp-lib-table` read, so the two can
    never disagree about which directory is KiCad's.

    Linux `~/.config/kicad/10.0`, Windows `%APPDATA%\\kicad\\10.0`, macOS
    `~/Library/Preferences/kicad/10.0`. Before 22.10.2026 this was spelled `~/.config/...`
    everywhere: right on Linux, a directory KiCad never writes on the other two — so the
    KICAD10_FOOTPRINT_DIR lookup and the user's library table were both lost there, which is
    exactly the platform the search order was fixed FOR (acceptance finding Д-2).
    """
    if sys.platform.startswith("win"):
        # KiCad itself only ever uses %APPDATA%; a bare HOME is the fallback for a headless run.
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "kicad" / "10.0"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Preferences" / "kicad" / "10.0"
    return Path.home() / ".config" / "kicad" / "10.0"


def _kicad_common_json() -> Path:
    """The user's KiCad 10 settings file, inside `_kicad_settings_dir()` — the ONE home of
    that path, read both for the path variables of fp-lib-table and for KICAD10_FOOTPRINT_DIR."""
    return _kicad_settings_dir() / "kicad_common.json"


def _common_vars() -> dict:
    """The `environment.vars` block of kicad_common.json ({} when unreadable)."""
    import json
    try:
        data = json.loads(_kicad_common_json().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return (data.get("environment") or {}).get("vars", {}) or {}


def _platform_lib_dirs():
    """Where a STOCK KiCad install keeps its footprints — the last resort, used only when
    neither --lib-dir nor KICAD10_FOOTPRINT_DIR says where they are."""
    if sys.platform.startswith("win"):
        found = []
        for root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")):
            if root:
                # KiCad installs under a VERSIONED directory; newest first, so a machine
                # carrying 9.0 and 10.0 side by side is checked against the newer library set.
                found += sorted(Path(root).glob("KiCad/*/share/kicad/footprints"), reverse=True)
        return found
    if sys.platform == "darwin":
        return [Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints")]
    return [Path("/usr/share/kicad/footprints"), Path("/usr/local/share/kicad/footprints")]


def _resolve_lib_dir(argv, candidates=None):
    """(library directory, "") or (None, the sentence naming WHERE we looked).

    The order is the one KiCad itself follows:
      1. `--lib-dir`, when given — and it must exist, so a typo is refused instead of quietly
         checking against nothing;
      2. `KICAD10_FOOTPRINT_DIR` — the environment first, then the same variable inside
         kicad_common.json (where KiCad keeps it);
      3. the platform default of a stock KiCad install.

    Nothing found is a REFUSAL, not a report full of "not checked": on a machine with no
    reachable library the check can say nothing about the board at all (Денис, 22.10).
    """
    looked = []
    if "--lib-dir" in argv:
        given = Path(argv[argv.index("--lib-dir") + 1])
        if given.is_dir():
            return given, ""
        return None, (f"REFUSED: --lib-dir {given} is not a directory — the check needs KiCad's "
                      f"footprint libraries to compare against")
    looked.append("--lib-dir (not given)")
    env_value = os.environ.get("KICAD10_FOOTPRINT_DIR")
    if env_value:
        path = Path(env_value)
        if path.is_dir():
            return path, ""
        looked.append(f"$KICAD10_FOOTPRINT_DIR = {path} (no such directory)")
    else:
        looked.append("$KICAD10_FOOTPRINT_DIR (not set)")
    common = _kicad_common_json()
    config_value = _common_vars().get("KICAD10_FOOTPRINT_DIR")
    if config_value:
        path = Path(config_value)
        if path.is_dir():
            return path, ""
        looked.append(f"KICAD10_FOOTPRINT_DIR in {common} = {path} (no such directory)")
    else:
        looked.append(f"KICAD10_FOOTPRINT_DIR in {common} (not set)")
    for path in (candidates if candidates is not None else _platform_lib_dirs()):
        if path.is_dir():
            return path, ""
        looked.append(str(path))
    return None, ("REFUSED: no KiCad footprint library directory found — looked at: "
                  + "; ".join(looked) + ". Pass --lib-dir")


def _lib_dirs(board_path: Path, default_dir: Path) -> dict:
    """{library nickname: .pretty dir} from the project's and the user's fp-lib-table,
    with ${KIPRJMOD} and the user's path variables (kicad_common.json) expanded;
    a nickname not in any table falls back to <default_dir>/<nick>.pretty."""
    cfg = _kicad_settings_dir()
    env = {"KIPRJMOD": str(board_path.parent.resolve()),
           "KICAD10_FOOTPRINT_DIR": str(default_dir)}
    env.update(_common_vars())
    dirs = {}
    for table in (cfg / "fp-lib-table", board_path.parent / "fp-lib-table"):   # project wins
        if not table.is_file():
            continue
        for lib in _children(_parse(table.read_text(encoding="utf-8")), "lib"):
            fields = {c[0]: c[1] for c in lib if isinstance(c, list) and len(c) > 1}
            if fields.get("type") == "KiCad" and "name" in fields and "uri" in fields:
                dirs[fields["name"]] = Path(_expand(fields["uri"], env))
    return dirs


def _board_of(target: Path):
    """The .kicad_pcb to read: the argument itself, or — for a KiCadStamp profile (its dir or
    its .sexp) — the board next to the profile's root_sheet. None (with a reason) otherwise."""
    if target.is_dir():
        sexps = [f for f in (target / "config.sexp", target / f"{target.name}.sexp") if f.is_file()] \
            or sorted(target.glob("*.sexp"))
        if len(sexps) != 1:
            print(f"cannot tell the profile config in {target}: {[f.name for f in sexps]}")
            return None
        target = sexps[0]
    if not target.is_file():
        print(f"no such file: {target}")
        return None
    head = target.read_text(encoding="utf-8")[:4096]
    if head.lstrip().startswith("(kicad_pcb"):
        return target
    if head.lstrip().startswith("(kicadstamp-config"):
        import re
        m = re.search(r'\(root_sheet\s+"([^"]+)"', head)
        if not m:
            print(f"{target}: the profile has no root_sheet — pass the .kicad_pcb")
            return None
        board = (target.parent / m.group(1)).resolve().with_suffix(".kicad_pcb")
        if not board.is_file():
            print(f"{target}: its board {board} does not exist")
            return None
        print(f"profile {target.name} -> board {board}")
        return board
    print(f"{target} is neither a .kicad_pcb nor a KiCadStamp profile")
    return None


def main(argv):
    json_path = check_report.pop_option(argv, "--json")
    if not argv:
        print(__doc__)
        return 2
    board_path = _board_of(Path(argv[0]))
    if board_path is None:
        return 2
    lib_dir, refusal = _resolve_lib_dir(argv)
    if lib_dir is None:
        print(refusal)
        if json_path:
            check_report.write(json_path, check_report.report(
                "check_footprints_vs_library.py",
                result={"exit": 2, "problems": 0, "ok": 0, "skipped": 0, "not_checked": 0},
                sections=[], refusal=refusal))
        return 2
    show_all = "--all" in argv
    libs = _lib_dirs(board_path, lib_dir)
    board = _parse(board_path.read_text(encoding="utf-8"))
    fps = _children(board, "footprint")
    print(f"board {board_path.name}: {len(fps)} footprint(s); library {lib_dir}")
    bad = missing = 0
    found = {"footprints": [], "fields": []}
    notes = []
    cache = {}
    generators = {}   # ref -> the library's KiLib_Generator value
    for fp in fps:
        lib_id = fp[1]
        ref = _ref(fp)
        lib, _, name = lib_id.partition(":")
        mod = libs.get(lib, lib_dir / f"{lib}.pretty") / f"{name}.kicad_mod"
        if not mod.is_file():
            line = f"  {ref:6} {lib_id}: library copy not found — not checked"
            print(line)
            missing += 1
            found["footprints"].append(check_report.problem(
                "LIB_MISSING", line, key=f"LIB|{ref}|{lib_id}", refs=[ref],
                where={"ref": ref, "library": lib_id}))
            continue
        if mod not in cache:
            lib_fp = _parse(mod.read_text(encoding="utf-8"))
            cache[mod] = (_signature(lib_fp), _lib_generator(lib_fp))
        want, generators[ref] = cache[mod]
        got = _signature(fp)
        problems = []
        if got[0] < want[0]:
            problems.append(f"3D models {got[0]} of {want[0]}")
        if got[1] != want[1]:
            problems.append(f"fp_poly points {got[1]} vs library {want[1]}")
        if problems:
            bad += 1
            line = f"  {ref:6} {lib_id}: " + "; ".join(problems)
            print(line)
            found["footprints"].append(check_report.problem(
                "MODELS" if got[0] < want[0] else "POLY", line,
                key=f"DMG|{ref}|" + ("|".join(problems)), refs=[ref],
                where={"ref": ref, "library": lib_id}))
        elif show_all:
            line = f"  {ref:6} {lib_id}: ok (models {got[0]}, fp_poly {got[1]})"
            print(line)
            notes.append(check_report.note("INFO", line))
    # Bug 4: "Update Footprints from Library" renames a user field to the library's
    # KiLib_Generator (keeping our value). What matters to KiCadStamp is a Role/Cluster that is
    # GONE; a KiLib_Generator next to intact Role/Cluster is only the trace of such an update.
    props = {}
    for fp in fps:
        props[_ref(fp)] = {c[1]: (c[2] if len(c) > 2 else "") for c in _children(fp, "property") if len(c) > 1}
    with_rc = [r for r, p in props.items() if "Role" in p or "Cluster" in p]
    traced = [r for r, p in props.items() if "KiLib_Generator" in p]
    # A footprint without Role or Cluster is damage when its neighbours still have them, or when it
    # carries the library's KiLib_Generator (the update touched it) — that also catches the case
    # where "Remove text items if not in library footprint" deleted BOTH fields on EVERY footprint.
    lost = [r for r, p in props.items()
            if ("Role" not in p or "Cluster" not in p) and (with_rc or traced)]
    # A KiLib_Generator whose value is not the library's is one of OUR fields under the library's
    # name (Sim.Pins, a BOM field...) even when Role and Cluster survived.
    renamed = [r for r, p in props.items() if r not in lost and "KiLib_Generator" in p
               and r in generators and p["KiLib_Generator"] != generators[r]]
    if lost:
        print(f"FIELDS LOST: {len(lost)} footprint(s) lack Role or Cluster — KiCadStamp would read them "
              f"wrong; run F8 (update fields from schematic) BEFORE any placement:")
        for r in sorted(lost):
            p = props[r]
            line = (f"  {r:6} Role={p.get('Role', '—')!s:16} Cluster={p.get('Cluster', '—')!s:22} "
                    f"KiLib_Generator={p.get('KiLib_Generator', '—')}")
            print(line)
            found["fields"].append(check_report.problem(
                "FIELDS_LOST", line, key=f"LOST|{r}", refs=[r],
                where={"ref": r, "role": p.get("Role"), "cluster": p.get("Cluster")}))
        bad += len(lost)
    if renamed:
        print(f"FIELD RENAMED: {len(renamed)} footprint(s) carry KiLib_Generator with a value that is not the "
              f"library's — some other field of ours was renamed; run F8:")
        for r in sorted(renamed):
            line = (f"  {r:6} KiLib_Generator={props[r]['KiLib_Generator']!s:22} "
                    f"library has {generators[r]}")
            print(line)
            found["fields"].append(check_report.problem(
                "FIELD_RENAMED", line, key=f"RENAMED|{r}", refs=[r],
                where={"ref": r, "library": generators[r]}))
        bad += len(renamed)
    if not lost:
        line = (f"fields: ok ({len(with_rc)} footprint(s) with Role and Cluster)"
                + (f"; {len(traced)} also carry KiLib_Generator — a trace of Update Footprints from "
                   f"Library, harmless while Role/Cluster are intact" if traced else ""))
        print(line)
        notes.append(check_report.note("INFO", line))
    print(f"result: {len(fps) - bad - missing} ok, {bad} damaged, {missing} not checked")
    exit_code = 1 if bad else 0
    if json_path:
        every = [entry for entries in found.values() for entry in entries]
        check_report.write(json_path, check_report.report(
            "check_footprints_vs_library.py",
            result={"exit": exit_code, "problems": bad,
                    "ok": len(fps) - bad - missing, "skipped": 0, "not_checked": missing},
            sections=[check_report.section("footprints", "Footprints", found["footprints"]),
                      check_report.section("fields", "Fields", found["fields"])],
            changes_=check_report.changes("check_footprints_vs_library.py",
                                          board_path.stem, every),
            profile=None, board=board_path.name, notes=notes))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
