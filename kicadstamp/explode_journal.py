# kicadstamp/explode_journal.py
"""The "Разнос" JOURNAL and EXECUTION (2026-10-05, plan
``plan_2026_10_05_explode_r1_core.md`` §3, design
``design_2026_10_05_explode_cluster.md`` РЗ5/РЗ6).

The journal holds ABSOLUTE original positions of everything the plan moves. It is
written to a LOCAL user directory (never ``profiles/`` — Syncthing carries those
between machines) BEFORE the first shift, atomically (a temp file + ``os.replace``).
One journal per board, keyed by a hash of the board's absolute path.

`explode` shifts the plan's items in ONE KiCad transaction and then re-reads the
board: every moved item must sit exactly on its target (0 nm) and NO non-moved
copper may have changed position. A failure keeps the journal (nothing is lost).
`restore` puts every item back on its RECORDED absolute position (never an
inverse shift) and deletes the journal only when everything is back.

The write path follows the live probe (``diagnostics/claude_probe_explode_*``):
footprints are moved through their DTO (``update_items`` syncs those), tracks and
vias through the LIVE kipy object (``unwrap``), because ``update_items`` only
syncs Footprint DTOs.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from kipy.geometry import Vector2 as _KV

from . import __version__
from .domain.board import Footprint, Track, Via, unwrap
from .domain.geometry import Vector2
from .explode import ExplodeError, ExplodePlan, Pose
from .i18n import _

__all__ = [
    "board_identity",
    "journal_dir",
    "journal_path",
    "journal_status",
    "load_journal",
    "explode",
    "restore",
]


# ── where the journal lives ─────────────────────────────────────────────────

def journal_dir() -> Path:
    """The local state directory for the journal (plan §3): Linux
    ``$XDG_STATE_HOME/kicadstamp/explode`` (or ``~/.local/state/...``), Windows
    ``%LOCALAPPDATA%\\kicadstamp\\explode``. NEVER ``profiles/`` or
    ``.history/`` — Syncthing carries those between machines, and a journal from
    one board must never surface on another."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "kicadstamp" / "explode"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "kicadstamp" / "explode"


def board_identity(adapter) -> str:
    """The live board's absolute path (project dir + board filename), or its
    bare filename, or ``"(unknown board)"``. Reads only the identity seam and
    never raises on a fake that omits it."""
    fname = _safe(adapter, "get_board_filename")
    if not fname:
        return "(unknown board)"
    proj = _safe(adapter, "get_board_project")
    if proj and len(proj) == 2 and proj[1]:
        return str(Path(proj[1]) / str(fname))
    return str(fname)


def _safe(adapter, name):
    try:
        fn = getattr(adapter, name, None)
        return fn() if callable(fn) else None
    except Exception:  # noqa: BLE001 — an optional identity read, never fatal
        return None


def journal_path(adapter, journal_dir_override=None) -> Path:
    """This board's one journal path: ``<dir>/<sha1(abs board path)>.json``."""
    directory = Path(journal_dir_override) if journal_dir_override else journal_dir()
    digest = hashlib.sha1(board_identity(adapter).encode("utf-8")).hexdigest()[:20]
    return directory / f"{digest}.json"


def load_journal(path) -> Optional[dict]:
    """The journal at ``path``, or None when it is absent or unreadable."""
    try:
        p = Path(path)
        if not p.is_file():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a missing/corrupt journal reads as none
        return None


def journal_status(adapter, journal_dir_override=None) -> Optional[dict]:
    """The journal for the CURRENT board, or None (the Р2 status bar's read)."""
    return load_journal(journal_path(adapter, journal_dir_override))


# ── read/write helpers ──────────────────────────────────────────────────────

def _live_items(adapter) -> dict:
    """{uuid: live DTO} over footprints, tracks and vias (one read each)."""
    out = {}
    for item in (list(adapter.get_footprints()) + list(adapter.get_tracks())
                 + list(adapter.get_vias())):
        uuid = getattr(item, "uuid", None)
        if uuid is not None:
            out[str(uuid)] = item
    return out


def _copper_poses(adapter) -> dict:
    """{uuid: Pose} of every track and via — the "did anything else move?" snap."""
    out = {}
    for item in list(adapter.get_tracks()) + list(adapter.get_vias()):
        uuid = getattr(item, "uuid", None)
        if uuid is not None:
            out[str(uuid)] = Pose.of(item)
    return out


def _set_pose(item, pose: Pose) -> None:
    """Put ONE live item on an absolute pose, through the write path the probe
    proved: a footprint via its DTO (update_items syncs those), a via/track via
    the live kipy object (``unwrap``), for update_items does not sync those."""
    if isinstance(item, Footprint):
        item.position = Vector2.from_xy(pose.x, pose.y)
        item.angle_deg = pose.angle
        return
    kipy_item = unwrap(item)
    if isinstance(item, Via):
        kipy_item.position = _KV.from_xy(pose.x, pose.y)
    else:
        kipy_item.start = _KV.from_xy(pose.sx, pose.sy)
        kipy_item.end = _KV.from_xy(pose.ex, pose.ey)


def _pose_diff(pose: Pose, target: Pose) -> int:
    """The worst nanometre deviation between a live pose and a target, or a big
    sentinel when the KIND differs (a uuid that changed kind is not "on target")."""
    if pose.kind != target.kind:
        return 1 << 40
    if pose.kind == "footprint":
        vals = ((pose.x, target.x), (pose.y, target.y))
    elif pose.kind == "via":
        vals = ((pose.x, target.x), (pose.y, target.y))
    else:
        vals = ((pose.sx, target.sx), (pose.sy, target.sy),
                (pose.ex, target.ex), (pose.ey, target.ey))
    return max(abs(int(a) - int(b)) for a, b in vals)


def _commit(adapter, pairs, description: str) -> None:
    """ONE KiCad transaction: set every (item, pose) then push. On a failure the
    commit is DROPPED — the caller removes the journal."""
    commit = adapter.begin_commit()
    try:
        for item, pose in pairs:
            _set_pose(item, pose)
        adapter.update_items([item for item, _pose in pairs])
    except Exception:
        adapter.drop_commit(commit)
        raise
    adapter.push_commit(commit, description)


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, path)


# ── explode ─────────────────────────────────────────────────────────────────

def explode(adapter, plan: ExplodePlan, journal_dir_override=None) -> list:
    """Shift `plan`'s items in one transaction; journal written FIRST.

    Refuses while a journal already exists for this board ("restore first").
    Raises ExplodeError after the fact if the verification finds anything off —
    the journal is then KEPT."""
    path = journal_path(adapter, journal_dir_override)
    existing = load_journal(path)
    if existing is not None:
        raise ExplodeError(_(
            "clusters are already exploded ({when}) — restore them first")
            .format(when=existing.get("time", "?")))
    live = _live_items(adapter)
    pairs = []
    items: dict = {}
    for move in plan.moves:
        item = live.get(str(move.uuid))
        if item is None:
            continue
        items[str(move.uuid)] = {"kind": move.kind, "ref": move.ref,
                                 "before": move.before.to_dict(),
                                 "after": move.after.to_dict()}
        pairs.append((item, Pose.from_dict(items[str(move.uuid)]["after"])))
    if not pairs:
        raise ExplodeError(_("nothing to move — the plan is empty"))

    copper_before = _copper_poses(adapter)
    journal = {
        "version": __version__,
        "board": board_identity(adapter),
        "config": getattr(plan, "config_path", "") or "",
        "cell": plan.cell_name,
        "cluster": plan.cluster,
        "sheet": plan.sheet,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "margin_mm": getattr(plan, "margin_mm", None),
        "gap_mm": getattr(plan, "gap_mm", None),
        "items": items,
    }
    _write_atomic(path, journal)
    try:
        _commit(adapter, pairs, _("KiCadStamp: explode clusters"))
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise

    adapter.refresh_board()
    return _verify(adapter, journal, copper_before, "moved")


def _verify(adapter, journal: dict, copper_before, which: str) -> list:
    """Re-read the board and check: every moved item on its target (0 nm), its
    uuid present, and NO non-moved copper changed. Returns the report lines."""
    live = _live_items(adapter)
    worst = 0
    missing = []
    for uuid, rec in journal["items"].items():
        item = live.get(uuid)
        if item is None:
            missing.append(uuid)
            continue
        target = rec["after"] if which == "moved" else rec["before"]
        worst = max(worst, _pose_diff(Pose.of(item), Pose.from_dict(target)))
    dragged = []
    if copper_before is not None:
        for uuid, pose in copper_before.items():
            if uuid in journal["items"]:
                continue
            item = live.get(uuid)
            if item is None:
                dragged.append((uuid, "GONE"))
            elif _pose_diff(Pose.of(item), pose) > 0:
                dragged.append((uuid, _pose_diff(Pose.of(item), pose)))
    lines = [_("{which}: worst deviation {nm} nm over {n} item(s); "
               "missing uuids: {missing}").format(
                   which=which, nm=worst, n=len(journal["items"]),
                   missing=len(missing))]
    if copper_before is not None:
        lines.append(_("non-moved copper that changed position: {n}").format(
            n=len(dragged)))
    if worst > 0 or missing or dragged:
        raise ExplodeError(_(
            "the board did not land where the journal says ({which}): "
            "{worst} nm, {missing} missing uuid(s), {dragged} dragged copper "
            "item(s) — the journal was kept").format(
                which=which, worst=worst, missing=len(missing),
                dragged=len(dragged)))
    return lines


# ── restore ─────────────────────────────────────────────────────────────────

def restore(adapter, journal_file) -> list:
    """Put every journal item back on its RECORDED absolute pose (never an
    inverse shift), in one transaction. Deletes the journal only when every item
    is present and back; otherwise keeps it and reports what is off."""
    journal = load_journal(journal_file)
    if journal is None:
        raise ExplodeError(_("no journal to restore: {path}").format(
            path=str(journal_file)))
    live = _live_items(adapter)
    pairs = []
    hand_moved = []
    gone = []
    for uuid, rec in journal["items"].items():
        item = live.get(uuid)
        if item is None:
            gone.append(rec.get("ref") or uuid)
            continue
        if _pose_diff(Pose.of(item), Pose.from_dict(rec["after"])) > 0:
            hand_moved.append(rec.get("ref") or uuid)
        pairs.append((item, Pose.from_dict(rec["before"])))
    if pairs:
        _commit(adapter, pairs, _("KiCadStamp: restore clusters"))
        adapter.refresh_board()

    live_after = _live_items(adapter)
    worst = 0
    for uuid, rec in journal["items"].items():
        item = live_after.get(uuid)
        if item is None:
            continue
        worst = max(worst, _pose_diff(Pose.of(item), Pose.from_dict(rec["before"])))
    lines = [_("restored {n} item(s); worst deviation {nm} nm").format(
        n=len(pairs), nm=worst)]
    if hand_moved:
        lines.append(_("moved by hand after the explode (restored anyway): "
                       "{names}").format(names=", ".join(hand_moved[:20])))
    if gone:
        lines.append(_("no longer on the board (left as they are): {names}")
                     .format(names=", ".join(gone[:20])))
    if not gone and worst == 0:
        try:
            Path(journal_file).unlink()
        except OSError:
            pass
        lines.append(_("journal removed — everything is back"))
    else:
        lines.append(_("journal KEPT: {n} item(s) are not back").format(
            n=len(gone) + (1 if worst else 0)))
    if worst > 0:
        raise ExplodeError(_(
            "restore is off by {nm} nm — the journal was kept").format(nm=worst))
    return lines
