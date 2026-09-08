#!/usr/bin/env python3
"""
probe_role_narrowing_cascade.py — read-only live probe of the role-narrowing
cascade (role_narrowing._narrow_by_sheet_cluster_selection), printing the FULL
state on every call — including steps that did NOT narrow.

Why (plan_2026_09_08_role_narrowing_live_probe.md): the logger.info calls
INSIDE role_narrowing.py only fire when a step actually reduces the candidate
set, so a no-op Sheet/Cluster step is silent — and the three genuinely
different reasons ("no anchor at all" vs "set but zero matches" vs "set and
matched everything") are indistinguishable from logs. This probe re-derives
every step's per-candidate data and explains WHY a step did or did not narrow,
without touching role_narrowing.py itself (module-level wrap for the duration
of one dry-run apply, then restore).

Never mutates role_narrowing.py / clone_role_resolver.py: it only swaps the
module-level attribute for the duration of one dry-run run. Three module
bindings are wrapped (role_narrowing's own + the two modules that import the
function at module scope) so every resolution path goes through the trace.

Read-only: installs a dry-run ApplyPipeline (nothing is written to the board).

Usage (live board open in KiCad):
    python -m kicadstamp.diagnostics.probe_role_narrowing_cascade \
        --config profiles/3ch-awg-tia-v103/config.sexp \
        --only pif_avdd_channel_0__ch1_dac_buf,pif_clkvdd_channel_0__ch1_dac_buf,pif_dvdd_channel_0__ch1_dac_buf

Default --only is that exact pif_avdd/clkvdd/dvdd Channel-1 set (the live
ch1_dac_buf Redraw failure reproduced on 2026-09-08).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME
from kicadstamp.cluster_matching import cluster_prefix_match
from kicadstamp.exceptions import ValidationError
from kicadstamp.sheet_names import resolve_sheet_path_names
from kicadstamp.placement.services import role_narrowing
from kicadstamp.placement.services import clone_role_resolver
from kicadstamp.placement.services.role_narrowing import narrow_candidates_by_sheet

# The original implementations, saved before any wrapping so uninstall() can
# restore byte-for-byte (including the two importers' module-scope bindings).
_ORIGINALS = {
    role_narrowing: role_narrowing._narrow_by_sheet_cluster_selection,
    clone_role_resolver: clone_role_resolver._narrow_by_sheet_cluster_selection,
}


def _mm(fp) -> tuple[float, float]:
    return (fp.position.x / 1e6, fp.position.y / 1e6)


def _describe_set(fps, adapter, sheet_names) -> None:
    for fp in sorted(fps, key=lambda f: f.ref):
        cluster = adapter.get_field_value(fp, CLUSTER_FIELD_NAME)
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        names = resolve_sheet_path_names(fp, sheet_names) if sheet_names else []
        x, y = _mm(fp)
        print(f"        {fp.ref:8s} Role={role or '':20s} "
              f"Cluster={cluster or '':22s} sheet_path={names} "
              f"pos=({x:8.3f}, {y:8.3f}) mm")


def _traced(candidates, adapter, selected_refs, anchor_sheet, anchor_cluster,
            sheet_names, label, role_str):
    print("\n" + "=" * 100)
    print(f"NARROWING  label={label!r}  role={role_str!r}")
    print(f"  placement.sheet  = {anchor_sheet!r}")
    print(f"  placement.cluster= {anchor_cluster!r}")
    print(f"  incoming candidates ({len(candidates)}):")
    _describe_set(candidates, adapter, sheet_names)

    # ── Re-derive each step's outcome (mirrors role_narrowing exactly) ──────
    # 1. Sheet
    sheet_out = None
    if anchor_sheet and len(candidates) > 1:
        by_sheet = narrow_candidates_by_sheet(list(candidates), anchor_sheet,
                                              sheet_names)
        if 0 < len(by_sheet) < len(candidates):
            sheet_out = by_sheet
            print(f"  STEP sheet: narrowed {len(candidates)} -> "
                  f"{len(by_sheet)} (by anchor_sheet {anchor_sheet!r})")
        elif len(by_sheet) == 0:
            print(f"  STEP sheet: anchor_sheet {anchor_sheet!r} matched ZERO "
                  f"candidates — no-op (set kept as-is)")
        else:
            print(f"  STEP sheet: anchor_sheet {anchor_sheet!r} matched ALL "
                  f"{len(candidates)} candidates — nothing to narrow")
    else:
        reason = ("SKIPPED — placement.sheet is None/empty"
                  if not anchor_sheet else "SKIPPED — <=1 candidate")
        print(f"  STEP sheet: {reason}")

    # 2. Cluster (applies to the sheet-narrowed pool when sheet narrowed)
    cluster_out = None
    pool_after_sheet = sheet_out if sheet_out is not None else list(candidates)
    if anchor_cluster and len(pool_after_sheet) > 1:
        live = [(fp, adapter.get_field_value(fp, CLUSTER_FIELD_NAME) or '')
                for fp in pool_after_sheet]
        matched = [fp for fp, cl in live
                   if cluster_prefix_match(cl, anchor_cluster)]
        live_cluster_values = sorted({cl for _, cl in live})
        if not matched:
            print(f"  STEP cluster: anchor_cluster {anchor_cluster!r} matched "
                  f"ZERO of {len(pool_after_sheet)} — no-op (format/typo "
                  f"mismatch?)")
            print(f"        live Cluster fields among candidates: "
                  f"{live_cluster_values!r}")
        elif len(matched) < len(pool_after_sheet):
            cluster_out = matched
            print(f"  STEP cluster: narrowed {len(pool_after_sheet)} -> "
                  f"{len(matched)} (by anchor_cluster {anchor_cluster!r})")
        else:
            print(f"  STEP cluster: anchor_cluster {anchor_cluster!r} matched "
                  f"ALL {len(pool_after_sheet)} candidates — nothing to narrow "
                  f"(their live Cluster fields all read the same)")
            print(f"        live Cluster fields among candidates: "
                  f"{live_cluster_values!r}")
    else:
        reason = ("SKIPPED — placement.cluster is None/empty"
                  if not anchor_cluster
                  else "SKIPPED — <=1 candidate after sheet")
        print(f"  STEP cluster: {reason}")

    # 3. Selection (informational only — never mutates the narrowing)
    pool_before_selection = (cluster_out if cluster_out is not None
                             else pool_after_sheet)
    if selected_refs and len(pool_before_selection) > 1:
        sel = [fp for fp in pool_before_selection if fp.ref in selected_refs]
        if 0 < len(sel) < len(pool_before_selection):
            print(f"  STEP selection: narrowed {len(pool_before_selection)} -> "
                  f"{len(sel)} (by current board selection)")
        else:
            print(f"  STEP selection: current selection ({len(selected_refs)} "
                  f"ref(s)) matched {len(sel)}/{len(pool_before_selection)} "
                  f"— no narrowing")

    # ── Call the REAL cascade (authoritative result) ─────────────────────────
    result = _ORIGINALS[role_narrowing](
        candidates, adapter, selected_refs, anchor_sheet, anchor_cluster,
        sheet_names, label, role_str)

    print(f"  OUTGOING candidates ({len(result)}): "
          f"{sorted(fp.ref for fp in result)}")
    return result


def install():
    for mod, _ in _ORIGINALS.items():
        setattr(mod, "_narrow_by_sheet_cluster_selection", _traced)


def uninstall():
    for mod, original in _ORIGINALS.items():
        setattr(mod, "_narrow_by_sheet_cluster_selection", original)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config",
                        default="profiles/3ch-awg-tia-v103/config.sexp")
    parser.add_argument("--only",
                        default="pif_avdd_channel_0__ch1_dac_buf,"
                                "pif_clkvdd_channel_0__ch1_dac_buf,"
                                "pif_dvdd_channel_0__ch1_dac_buf",
                        help="comma-separated item names, same as apply's --only")
    args = parser.parse_args(argv)

    only = [s.strip() for s in args.only.split(",") if s.strip()]

    # Local import: only needed for the run itself (keeps the module cheap to
    # import for a future unit test that only checks the wrapper logic).
    from kicadstamp.apply_pipeline import ApplyPipeline

    install()
    pipeline = None
    try:
        pipeline = ApplyPipeline(args.config, only=only, dry_run=True)
        pipeline.run()
        print("\n[probe] dry-run completed without a role ambiguity fatal.")
        return 0
    except ValidationError as exc:
        # The pipeline raised the same ambiguity fatal Redraw hits live — the
        # trace above already shows every narrowing step and why it stopped.
        print("\n[probe] pipeline raised ValidationError (expected — the live "
              "ch1_dac_buf fatal):")
        print(str(exc))
        if pipeline is not None:
            order = getattr(pipeline, "items", None)
            if order:
                print("\n[probe] execution-order items that reached planning:")
                for it in order:
                    print(f"    {it.label!r}  kind={getattr(it, 'kind', '?')}")
        return 1
    finally:
        uninstall()


if __name__ == "__main__":
    sys.exit(main())
