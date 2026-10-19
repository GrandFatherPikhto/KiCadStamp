# kicadstamp/diagnostics/probe_micro_track_matching_2026_10_19.py
"""READ-ONLY recon for plan_2026_10_15_micro_track_redraw_duplicates (Ч0-2/Ч0-3).

The fact (Denis + Claude, 15.10): ONE ``net_traces`` record holds three segments
around (77.98, 98.44) mm, all shorter than the 10 µm position tolerance, and
"Дубли меди" reported them as ONE group of 3 copies and deleted two of them —
i.e. the cleanup removed REAL copper of the record. Two separate mechanisms are
suspected, and this probe measures both on THAT fact, without a board:

  1. WRONG-DIRECTION MISS (``track_matches``) — with a 10 µm tolerance and a
     segment shorter than it, one micro segment matches ANOTHER micro segment,
     so the positional pre-check can believe the copper is already on the board
     and silently not draw it.
  2. FALSE DUPLICATE GROUP (``board_dedupe``) — grouping compares the ends with
     the first member within the same 10 µm and ignores width and length, so
     three DIFFERENT micro segments land in one group and "clear" deletes two.

Then it evaluates Denis's three conditions for the dedupe fix:
  * width joins the group key;
  * a segment shorter than 2 × POSITION_TOLERANCE_MM never enters a group
    (reported on its own line, never deleted);
  * the fact above must yield 0 groups.
Control rows keep the rule honest: two REAL duplicates of a normal segment must
still be found, and two copies of a sub-threshold segment are deliberately NOT
grouped (that is the decision, not an accident).

Usage:

    python -m kicadstamp.diagnostics.probe_micro_track_matching_2026_10_19

Exit code is 0 — this is a measuring probe, not a gate.
"""
from __future__ import annotations

import math

from kicadstamp.board_dedupe import find_track_duplicate_groups
from kicadstamp.constants import POSITION_TOLERANCE_MM
from kicadstamp.domain.board import Track
from kicadstamp.domain.geometry import BoardLayer, Vector2
from kicadstamp.placement.commands import TrackCommand
from kicadstamp.registry import track_matches
from kicadstamp.utils.units import MM

#: 2 × POSITION_TOLERANCE_MM in board units (nanometres) — Denis's threshold.
SHORT_NM = int(round(2 * POSITION_TOLERANCE_MM * MM))

NET = "+3V3_STM32"
LAYER = BoardLayer.BL_B_Cu
WIDTH = 0.65


def _mm(x_mm: float, y_mm: float) -> Vector2:
    return Vector2(int(round(x_mm * MM)), int(round(y_mm * MM)))


def _track(uuid: str, sx: float, sy: float, ex: float, ey: float,
           *, width: float = WIDTH, net: str = NET) -> Track:
    return Track(uuid=uuid, start=_mm(sx, sy), end=_mm(ex, ey), net_name=net,
                 width_mm=width, layer=LAYER)


def _cmd(sx: float, sy: float, ex: float, ey: float,
         *, width: float = WIDTH, net: str = NET) -> TrackCommand:
    return TrackCommand(start=_mm(sx, sy), end=_mm(ex, ey), width_mm=width,
                        net_name=net, layer=LAYER, owner_ref="probe")


def _length_um(track: Track) -> float:
    return math.hypot(track.end.x - track.start.x,
                      track.end.y - track.start.y) / 1000.0


def _fact() -> list[Track]:
    """The three segments of the 15.10 fact (registry geometry, exact nm)."""
    return [
        _track("A", 77.9784, 98.4459, 77.9742, 98.4416),
        _track("B", 77.9742, 98.4416, 77.9827, 98.4416),
        _track("C", 77.9784, 98.4459, 77.9843, 98.4400),
    ]


def _group_tracks_denis(tracks: list[Track]) -> tuple[list[list[Track]], list[Track]]:
    """The proposed rule, implemented locally so the numbers come from code.

    * the group key IS (net, layer, width) — a width difference is not a dupe;
    * a segment shorter than SHORT_NM never joins a group (returned separately);
    * the rest keeps today's greedy comparison (ends within the tolerance of the
      group's first member, unordered pair).
    """
    groups: list[list[Track]] = []
    shorts: list[Track] = []
    for track in tracks:
        if math.hypot(track.end.x - track.start.x, track.end.y - track.start.y) < SHORT_NM:
            shorts.append(track)
            continue
        asserted_key = (track.net_name, track.layer, round(track.width_mm, 6))
        for group in groups:
            head = group[0]
            if (head.net_name, head.layer, round(head.width_mm, 6)) != asserted_key:
                continue
            if _ends_close(track, head):
                group.append(track)
                break
        else:
            groups.append([track])
    return [g for g in groups if len(g) > 1], shorts


def _ends_close(a: Track, b: Track) -> bool:
    tol = int(round(POSITION_TOLERANCE_MM * MM))
    a_s, a_e = sorted([(a.start.x, a.start.y), (a.end.x, a.end.y)])
    b_s, b_e = sorted([(b.start.x, b.start.y), (b.end.x, b.end.y)])
    return (abs(a_s[0] - b_s[0]) <= tol and abs(a_s[1] - b_s[1]) <= tol
            and abs(a_e[0] - b_e[0]) <= tol and abs(a_e[1] - b_e[1]) <= tol)


def main() -> int:
    fact = _fact()

    print("== Ч0-2/Ч0-3: the 15.10 fact, three micro segments of ONE record ==")
    print(f"tolerance = {POSITION_TOLERANCE_MM} mm; Denis's short threshold = "
          f"{SHORT_NM} nm ({SHORT_NM / 1000:.0f} µm)\n")
    for t in fact:
        print(f"  {t.uuid}: ({t.start.x / MM:.4f},{t.start.y / MM:.4f}) -> "
              f"({t.end.x / MM:.4f},{t.end.y / MM:.4f})  len={_length_um(t):.1f} µm  "
              f"w={t.width_mm}  net={t.net_name}")

    print("\n-- 1. WRONG-DIRECTION MISS: does track_matches accept one for another? --")
    for live in fact:
        for cmd_track in fact:
            if live is cmd_track:
                continue
            cmd = _cmd(*[v / MM for v in (cmd_track.start.x, cmd_track.start.y,
                                          cmd_track.end.x, cmd_track.end.y)])
            verdict = track_matches(live, cmd)
            print(f"  live {live.uuid} vs planned {cmd_track.uuid}: "
                  f"{'MATCHES (would be skipped)' if verdict else 'no match'}")
    matched_pairs = sum(
        1 for live in fact for cmd_track in fact
        if live is not cmd_track and track_matches(
            live, _cmd(*[v / MM for v in (cmd_track.start.x, cmd_track.start.y,
                                          cmd_track.end.x, cmd_track.end.y)]))
    )
    print(f"  => cross-matches: {matched_pairs} of {len(fact) * (len(fact) - 1)} "
          "ordered pairs")

    print("\n-- 2. FALSE GROUP: what 'Дубли меди' finds TODAY --")
    groups_today = find_track_duplicate_groups(fact)
    to_delete = 0
    for i, group in enumerate(groups_today, 1):
        ids = [t.uuid for t in group]
        to_delete += len(group) - 1
        print(f"  group {i}: {ids}  keep={ids[0]}  delete={ids[1:]}")
    print(f"  => groups={len(groups_today)}, 'clear' would delete {to_delete} "
          "segment(s) of real copper")
    if groups_today and to_delete == 2 and len(groups_today) == 1:
        print("  REPRODUCED: exactly the 15.10 deletion (2 of 3 removed)")

    print("\n-- 3. DENIS'S RULE: width in the key + short segments never grouped --")
    groups_new, shorts = _group_tracks_denis(fact)
    print(f"  groups={len(groups_new)}  deleted={sum(len(g) - 1 for g in groups_new)}")
    print(f"  reported separately (not grouped, not deleted): "
          f"{[(t.uuid, round(_length_um(t), 1)) for t in shorts]}")
    assert not groups_new, "the fact must yield 0 groups under Denis's rule"

    print("\n-- 4. CONTROL ROWS: the rule must not break real dedupe --")
    real_dupes = [
        _track("n1", 10.0, 10.0, 12.0, 10.0),
        _track("n2", 10.0, 10.0, 12.0, 10.0),
    ]
    print(f"  two identical 2 mm segments -> today "
          f"{len(find_track_duplicate_groups(real_dupes))} group(s), with the rule "
          f"{len(_group_tracks_denis(real_dupes)[0])} group(s)")

    width_pair = [
        _track("w1", 20.0, 20.0, 22.0, 20.0, width=0.25),
        _track("w2", 20.0, 20.0, 22.0, 20.0, width=0.65),
    ]
    print(f"  same ends, DIFFERENT width -> today "
          f"{len(find_track_duplicate_groups(width_pair))} group(s), with the rule "
          f"{len(_group_tracks_denis(width_pair)[0])} group(s)")

    short_pair = [
        _track("s1", 30.0, 30.0, 30.008, 30.0),
        _track("s2", 30.0, 30.0, 30.008, 30.0),
    ]
    print(f"  two identical 8 µm segments -> today "
          f"{len(find_track_duplicate_groups(short_pair))} group(s), with the rule "
          f"{len(_group_tracks_denis(short_pair)[0])} group(s) + "
          f"{len(_group_tracks_denis(short_pair)[1])} short line(s)")

    print("\n-- 5. Boundary: a segment is 'short' strictly below 20 µm --")
    for length_um in (19.0, 20.0, 21.0):
        pair = [_track("b1", 40.0, 40.0, 40.0 + length_um / 1000.0, 40.0),
                _track("b2", 40.0, 40.0, 40.0 + length_um / 1000.0, 40.0)]
        groups_b, shorts_b = _group_tracks_denis(pair)
        print(f"  {length_um:>4.1f} µm: groups={len(groups_b)} shorts={len(shorts_b)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
