# kicadstamp/check_report.py
"""The ONE machine-readable report of the four `tools/check_*.py` scripts — `--json PATH`.

Why it exists (Ч2б-1 of plan_2026_10_23_checks_tab_top_level, decision of Denis
26.10.2026): the Checks tab could show a check's TEXT and nothing else, so "errors in a
tree" would have meant parsing the very strings a human reads. Instead every script
writes the same JSON shape next to the text it already prints:

    python tools/check_placement.py <profile> --json /tmp/r.json

The TEXT on stdout does not change by one byte (so "Copy" and every existing cell keep
working), and the JSON is written in the SAME run — one board read, one config load.

Shape (`schema` 1):

    {
      "schema": 1,
      "script": "check_placement.py",
      "when": "2026-10-26 12:00:00",
      "profile": "…/config.sexp" | null,
      "board": "board.kicad_pcb" | null,
      "result": {"exit": 1, "problems": 3, "ok": 41, "skipped": 0, "not_checked": 0},
      "applied": null | true | false,          # merge_duplicate_mounts only
      "refusal": null | "…",                   # the reason a write/run was refused
      "notes": [{"kind": "SKIPPED"|"NOT_CHECKED"|"INFO", "text": "…"}],
      "sections": [{"id": "…", "title": "…", "problems": [PROBLEM, …]}],
      "changes": {"new": [{"key", "text"}], "gone": [...], "baseline": bool, "when": …}
    }

    PROBLEM = {"key": <stable across runs>, "kind": <one of KINDS>, "text": <exactly the
               line stdout showed>, "refs": [<refdes for the overlay>], "where": {...} |
               null, "to_node": {"tree", "node", "cell"} | null}

Three rules the cells pin (C1–C6):

  * `text` is the SAME string that went to `print` — one assembly, never a second copy;
  * `kind` comes from `KINDS`; a caller switches on it instead of parsing prose;
  * `key` is stable between runs (it is what NEW/GONE compares), and `refs`/`to_node`
    are filled only from what the script really knows — a guess would send the operator
    to the wrong node.

`sections` lists EVERY section the run performed, in print order, with `problems: []`
for a clean one: the GUI decides what to hide ("пустые разделы не показывать"), and its
summary still has the list of what was checked.

The NEW/GONE baseline lives in `~/.local/state/kicadstamp/check_<script>_<profile>.json`
(machine state, never in `profiles/`). `check_placement.py` used to keep its own
`placement_check_<profile>.json`; `changes()` still READS that file when the new one does
not exist yet, so the first run after the move does not report everything as NEW.
`merge_duplicate_mounts.py` has NO changes: a dry run describes what stands there now.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterable, Optional

#: Bumped when a field changes meaning. A reader that does not know the number must say
#: so instead of guessing (there is exactly one reader today: the Checks tab).
SCHEMA_VERSION = 1

#: Every `kind` a problem can carry. A closed vocabulary on purpose: the GUI switches on
#: it, so a new kind must be added HERE (and to the check that emits it).
#:
#: A `kind` describes a PROBLEM. "Nothing was checked" is not one (Д-2 of the Ч2б-1
#: acceptance): a footprint with no library copy is a `NOT_CHECKED` note, and the count
#: lands in `result.not_checked` — otherwise a "we could not look" row would read like a
#: "we found damage" row.
KINDS = frozenset({
    # check_placement.py
    "MISPLACED",       # a part outside the consensus frame of its own instance
    "PAIR",            # the same, but the frame is not trusted (fewer than 2 agreeing roles)
    "DUPLICATE",       # one role on two parts of the same instance
    "TURNED",          # a part on its slot, rotated against the cell
    "TAKEN",           # an empty slot of one instance occupied by another cluster's part
    # check_config_records.py
    "SHEET",           # the address names one part only because sheet narrowing is soft
    "UNTAGGED",        # the part exists but carries no Cluster field
    "ABSENT",          # the address names no part at all
    "AMBIGUOUS",       # the address names several parts
    "PINNED",          # a pinned `refs` entry whose part is wrong/absent
    "UNLISTED",        # a part of a recorded cluster that no cell of it lists
    "CELL_MISSING",    # an entity names a cell that is not in `cells:`
    # check_footprints_vs_library.py
    "MODELS",          # 3D models missing on the board vs the library copy
    "POLY",            # fp_poly point count differs from the library copy
    "FIELDS_LOST",     # Role/Cluster gone from the footprint
    "FIELD_RENAMED",   # KiLib_Generator carries a value that is not the library's
    # merge_duplicate_mounts.py
    "MERGE",           # two mount nodes of one tree with the same anchor (siblings)
    "STRAY",           # the same, under DIFFERENT parents — reported, never merged
    "REFUSED",         # the tool refused (a ref it would delete is still referenced)
})

#: `notes[]` kinds — things that are not problems but the operator must be able to see.
NOTE_KINDS = frozenset({"SKIPPED", "NOT_CHECKED", "INFO"})

#: `~/.local/state/kicadstamp` — machine state, never inside `profiles/`.
_STATE_TAIL = (".local", "state", "kicadstamp")


def pop_option(argv: list, name: str) -> Optional[str]:
    """Take `name VALUE` (or `name=VALUE`) OUT of `argv`; None when it is absent.

    The four scripts parse their own arguments by hand (or with argparse), and the option
    must not be mistaken for the profile/board argument — so it is removed here, once,
    instead of each script growing its own copy of that scan."""
    value = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == name:
            if index + 1 >= len(argv):
                raise SystemExit(f"{name} needs a path")
            value = argv[index + 1]
            del argv[index:index + 2]
            continue
        if arg.startswith(name + "="):
            value = arg.split("=", 1)[1]
            del argv[index]
            continue
        index += 1
    return value


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def problem(kind: str, text: str, *, key: Optional[str] = None, refs: Iterable[str] = (),
            where=None, to_node=None) -> dict:
    """One problem entry. `key` defaults to a digest-free `kind|text` — stable for the same
    finding, which is all NEW/GONE needs (and never a line number: Д-4's lesson)."""
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is not in check_report.KINDS")
    return {"key": key or f"{kind}|{text}", "kind": kind, "text": text,
            "refs": [str(r) for r in refs], "where": where, "to_node": to_node}


def note(kind: str, text: str) -> dict:
    if kind not in NOTE_KINDS:
        raise ValueError(f"{kind!r} is not in check_report.NOTE_KINDS")
    return {"kind": kind, "text": text}


def section(section_id: str, title: str, problems: Iterable[dict] = ()) -> dict:
    return {"id": section_id, "title": title, "problems": list(problems)}


def state_path(script: str, profile_name: str, *, home=None) -> Path:
    """`~/.local/state/kicadstamp/<script stem>_<profile>.json` — the script's own name
    already starts with `check_`, so no second prefix is added."""
    base = Path(home) if home is not None else Path.home()
    for part in _STATE_TAIL:
        base = base / part
    return base / f"{Path(script).stem}_{profile_name}.json"


def legacy_state_path(profile_name: str, *, home=None) -> Path:
    """The file `check_placement.py` kept before Ч2б-1 — read once, never written."""
    base = Path(home) if home is not None else Path.home()
    for part in _STATE_TAIL:
        base = base / part
    return base / f"placement_check_{profile_name}.json"


def _read_state(path: Path) -> Optional[dict]:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a broken baseline must not fail a check
        return None
    return data if isinstance(data, dict) else None


def changes(script: str, profile_name: str, problems: Iterable[dict], *,
            home=None) -> dict:
    """NEW/GONE against the previous run of `script` on `profile_name`, then remember this
    run. Placement reads its OLD file name when the new one does not exist yet."""
    entries = list(problems)
    current = {entry["key"]: entry["text"] for entry in entries}
    path = state_path(script, profile_name, home=home)
    previous = _read_state(path)
    if previous is None and Path(script).name == "check_placement.py":
        previous = _read_state(legacy_state_path(profile_name, home=home))
    if previous is None:
        result = {"new": [], "gone": [], "baseline": True, "when": None}
    else:
        old = previous.get("issues") or {}
        result = {
            "new": [{"key": key, "text": current[key]}
                    for key in current if key not in old],
            "gone": [{"key": key, "text": old[key]}
                     for key in old if key not in current],
            "baseline": False,
            "when": previous.get("when"),
        }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"when": now(), "issues": current},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:  # noqa: BLE001 — remembering the run must not fail the check
        pass
    return result


def report(script: str, *, result: dict, sections: Iterable[dict], changes_=None,
           profile=None, board=None, notes: Iterable[dict] = (),
           applied=None, refusal=None) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "script": Path(script).name,
        "when": now(),
        "profile": str(profile) if profile is not None else None,
        "board": board,
        "result": dict(result),
        "applied": applied,
        "refusal": refusal,
        "notes": list(notes),
        "sections": list(sections),
        "changes": changes_,
    }


def write(path, data: dict) -> None:
    """Write the report where `--json PATH` asked for it. UTF-8, one indent — the file is
    for a machine, but a human may open it."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                      encoding="utf-8")
