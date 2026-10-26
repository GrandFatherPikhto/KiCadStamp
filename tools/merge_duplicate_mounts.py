"""tools/merge_duplicate_mounts.py — merge mount nodes of ONE tree that share the same anchor.

A mount node is addressed by its anchor (Role, Cluster, sheet, pad) plus its own xy/polar, rotation and
shift. Two mount nodes of the same tree with all of that equal are one point of reference written twice:
the tree then shows two names for one pin and a redraw by name picks one of them (22.10, v103: pad 107
of IC1 carried `fpga_107_vcca` and `mnt_fpga_vcca_107`). This tool finds such pairs and, with --write,
moves the children of every duplicate under ONE kept node and deletes the duplicates.

Rules (Denis, 22.10):
  * a different offset (xy/polar), rotation or shift means a DIFFERENT mount — never merged;
  * only inside one tree — the same anchor in two trees is not a duplicate;
  * only siblings are merged; a duplicate under another parent is reported and left alone.

The kept node is the first one in file order, or the one named by --keep. The tool refuses to write when
the ref of a node it would delete is still mentioned anywhere else in the config. Before writing the
config is backed up next to itself (the same backup the Trees dock makes); after writing it is loaded
again from a temporary copy, exactly as tools/check_placement.py loads it.

The board is not needed and not touched.

usage: .venv/bin/python tools/merge_duplicate_mounts.py <profile dir | profile .sexp> [--keep REF ...] [--write]
exit code: 0 nothing to merge / merged, 1 duplicates found (dry run) or refused, 2 bad arguments
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# `tools/` itself too — see the same note in check_config_records.py (a cell imports this
# module, and the sibling `check_placement` is then not on sys.path).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_placement import _config_name, _load_copy          # noqa: E402
from kicadstamp import check_report                            # noqa: E402
from kicadstamp.config_working_set import graph_root           # noqa: E402
from kicadstamp.config_writer import read_data, write_data    # noqa: E402
from kicadstamp.utils.safe_write import backup_file           # noqa: E402


def _mount_key(node: dict):
    """Everything that decides where a mount node stands; None for any other node."""
    if node.get("kind") != "mount" or not node.get("anchor"):
        return None
    return json.dumps([node["anchor"], node.get("xy"), node.get("polar"),
                       node.get("rotation", 0.0), node.get("shift")], sort_keys=True)


def _label(node: dict) -> str:
    a = node["anchor"]
    where = f"{a.get('role')} / {a.get('cluster') or '-'} / {a.get('sheet') or '-'}"
    pad = f", pad {a['pad']}" if a.get("pad") is not None else ""
    return f"{where}{pad}, xy {node.get('xy') or node.get('polar') or [0, 0]}"


def _scan(tree: dict, keep: set):
    """(merges, strays): merges = [(siblings, kept, [duplicates])], strays = groups under different parents."""
    by_key: dict = {}
    merges = []

    def walk(siblings: list, parent: str):
        groups: dict = {}
        for node in siblings:
            key = _mount_key(node)
            if key is not None:
                groups.setdefault(key, []).append(node)
                by_key.setdefault(key, set()).add(parent)
            walk(node.setdefault("children", []), node["ref"])
        for nodes in groups.values():
            if len(nodes) > 1:
                kept = next((n for n in nodes if n["ref"] in keep), nodes[0])
                merges.append((siblings, kept, [n for n in nodes if n is not kept]))

    walk(tree.setdefault("nodes", []), tree.get("name"))
    strays = [key for key, parents in by_key.items() if len(parents) > 1]
    return merges, strays


def _prune_empty_children(nodes: list):
    for node in nodes:
        if node.get("children") == []:
            del node["children"]
        else:
            _prune_empty_children(node.get("children", []))


def _write_json(json_path, *, exit_code, found, applied, refusal, cfg_path, notes=()):
    """Write the machine report ONCE, at the END — the decision of Denis (26.10.2026): a
    dry run describes what WOULD happen (`applied: false`), a successful `--write` says
    `applied: true`, and a refusal says `false` plus the reason. No NEW/GONE here: this
    tool is about what stands in the config right now."""
    if not json_path:
        return
    every = [entry for entries in found.values() for entry in entries]
    check_report.write(json_path, check_report.report(
        "merge_duplicate_mounts.py",
        result={"exit": exit_code, "problems": len(every), "ok": None,
                "skipped": 0, "not_checked": 0},
        sections=[check_report.section("groups", "Groups", found["groups"]),
                  check_report.section("strays", "Strays", found["strays"])],
        changes_=None, profile=cfg_path, board=None, notes=list(notes),
        applied=applied, refusal=refusal))


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("target", help="profile directory or its .sexp")
    ap.add_argument("--keep", action="append", default=[], metavar="REF",
                    help="keep this node of its group instead of the first one (repeatable)")
    ap.add_argument("--write", action="store_true", help="write the merge (default: dry run)")
    ap.add_argument("--json", metavar="PATH",
                    help="also write a machine-readable report to PATH (same run)")
    args = ap.parse_args(argv)
    json_path = args.json

    target = Path(args.target).resolve()
    if target.is_file():
        cfg_path = target
    elif target.is_dir():
        cfg_path = target / _config_name(target)
    else:
        print(f"no such profile: {target}")
        return 2
    data = read_data(cfg_path)
    trees = data.get("trees") or []
    keep = set(args.keep)

    found = {"groups": [], "strays": []}
    notes = []
    removed, total = [], 0
    for tree in trees:
        merges, strays = _scan(tree, keep)
        for siblings, kept, dups in merges:
            total += 1
            line = f"[tree {tree.get('name')}] {_label(kept)}"
            print(line)
            print(f"   keep    {kept['ref']} ({len(kept['children'])} child node(s))")
            refs = [kept["ref"]] + [dup["ref"] for dup in dups]
            for dup in dups:
                moved = [c["ref"] for c in dup["children"]]
                print(f"   merge   {dup['ref']} -> its children move under {kept['ref']}: {', '.join(moved) or 'none'}")
                for field in ("name", "group"):
                    if dup.get(field) and dup.get(field) != kept.get(field):
                        note_line = (f"   NOTE    {dup['ref']} has {field} {dup[field]!r}, {kept['ref']} has "
                                     f"{kept.get(field)!r} — the kept node's value stays")
                        print(note_line)
                        notes.append(check_report.note("INFO", note_line))
                kept["children"].extend(dup["children"])
                siblings.remove(dup)
                removed.append(dup["ref"])
            found["groups"].append(check_report.problem(
                "MERGE", line, key=f"MERGE|{tree.get('name')}|{kept['ref']}", refs=refs,
                where={"tree": tree.get("name"), "anchor": _label(kept)},
                to_node={"tree": tree.get("name"), "node": kept["ref"], "cell": None}))
        for key in strays:
            line = (f"[tree {tree.get('name')}] same anchor under DIFFERENT parents — "
                    f"not merged, do it by hand: {key}")
            print(line)
            found["strays"].append(check_report.problem(
                "STRAY", line, key=f"STRAY|{tree.get('name')}|{key}",
                where={"tree": tree.get("name"), "anchor": key}))
        _prune_empty_children(tree.get("nodes", []))

    if not total:
        print("no duplicate mount nodes")
        _write_json(json_path, exit_code=0, found=found, applied=False, refusal=None,
                    cfg_path=cfg_path, notes=notes)
        return 0
    rest = json.dumps(data, ensure_ascii=False)
    still = [ref for ref in removed if f'"{ref}"' in rest]
    if still:
        line = f"\nREFUSED: the config still mentions {', '.join(still)} — rename those references first"
        print(line)
        found["groups"].append(check_report.problem(
            "REFUSED", line.strip(), key="REFUSED|" + "|".join(sorted(still)), refs=still))
        _write_json(json_path, exit_code=1, found=found, applied=False, refusal=line.strip(),
                    cfg_path=cfg_path, notes=notes)
        return 1
    if not args.write:
        print(f"\n{total} group(s) to merge — dry run, nothing written; add --write to merge")
        _write_json(json_path, exit_code=1, found=found, applied=False, refusal=None,
                    cfg_path=cfg_path, notes=notes)
        return 1

    backup = backup_file(cfg_path)
    with graph_root(cfg_path):      # a format-3 write resolves reference UUIDs against the profile
        write_data(cfg_path, data)
    print(f"\nwritten: {cfg_path}\nbackup:  {backup}")
    tmp, _cfg_file, _cfg, _ctx = _load_copy(cfg_path.parent, cfg_path.name)
    shutil.rmtree(tmp, ignore_errors=True)
    print("reloaded from a copy: OK. If KiCadStamp is open on this profile, reload it before saving there —"
          " its own Save would write the old trees back.")
    _write_json(json_path, exit_code=0, found=found, applied=True, refusal=None,
                cfg_path=cfg_path, notes=notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
