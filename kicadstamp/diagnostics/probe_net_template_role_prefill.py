#!/usr/bin/env python3
"""probe_net_template_role_prefill.py — empirically verify the §3 hypothesis of
plan_2026_08_29_extract_net_template_role_prefill.md: for every role the GUI's
net-template-role tab flags as AMBIGUOUS (classifying list built from the
_net_auto_roles preview classification), does the GUI's deterministic default
(classifying[0]) equal the BACKEND's deterministic default (mapped[0], first
net on the role's pads present in net_template_map)?

The two filters differ textually:
  - GUI:  nets on the role's pads that classify by role (lemma2/pad, not
          fallback) and are not Rule-net checked  (gui/docks/extract.py
          _update_net_template_role_rows, _classify_selection_nets);
  - Backend: nets on the role's pads present in net_template_map
          (kicadstamp/template_extraction.py:422 / :400).

If they ever diverge, prefilling the GUI combo with classifying[0] would LIE to
the user about what extract_template_from_selection() would really write
without --net-template-role — this probe is the guard that makes the divergence
visible instead of silently shipping a wrong default.

Read-only: connects to the live board, reads fields/pads/nets, writes nothing.

Run:
    python -m kicadstamp.diagnostics.probe_net_template_role_prefill \
        [--profile profiles/3ch-awg-tia-v103-test/3ch-awg-tia.sexp] [--cluster fpga_supp]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kicadstamp.config.sexp_format import sexp_to_dict  # noqa: E402
from kicadstamp.constants import CLUSTER_FIELD_NAME, ROLE_FIELD_NAME  # noqa: E402
from kicadstamp.kicad.adapter import KiCadBoardAdapter  # noqa: E402
from kicadstamp.net_resolution import RULE_NETS  # noqa: E402
from kicadstamp.template_extraction import (  # noqa: E402
    _selection_role_nets as selection_role_nets,
    _suggest_net_from_role as suggest_net_from_role,
    extract_template_from_selection,
)

DEFAULT_PROFILE = str(Path(__file__).resolve().parents[2]
                      / "profiles" / "3ch-awg-tia-v103-test" / "3ch-awg-tia.sexp")
# The board's Cluster field can differ from the extract_profiles entry name
# (on this project: profile key 'fpga_supp', board Cluster 'FPGA' — the same
# "profile_key != cell name" split already documented in gui/docks/extract.py
# _find_profile_key_for_cell). Params come from the profile key, footprints
# from the board Cluster.
DEFAULT_PROFILE_KEY = "fpga_supp"
DEFAULT_CLUSTER = "FPGA"


def _load_extract_profile_params(profile_path, profile_key):
    """Return {role/alias_key: net_literal} from the extract_profiles entry —
    the same params the GUI pulls into the alias edits when the profile is
    applied (gui/docks/extract.py _apply_profile_entry)."""
    data = sexp_to_dict(Path(profile_path).read_text(encoding="utf-8")) or {}
    for key, entry in (data.get("extract_profiles") or {}).items():
        if key == profile_key or entry.get("name") == profile_key:
            return dict(entry.get("params") or {})
    return {}


def _cluster_fps(adapter, cluster):
    out = []
    for fp in adapter.get_footprints():
        if adapter.get_field_value(fp, CLUSTER_FIELD_NAME) == cluster:
            out.append(fp)
    return out


def _pad_nets(adapter, fp):
    return sorted({p.net_name for p in adapter.get_footprint_pads(fp) if p.net_name})


def _build_net_template_map(params):
    """Same derivation as extract_template_from_selection() lines 242-251:
    each params VALUE (a net literal) becomes a net_template_map key."""
    net_template_map: dict[str, str] = {}
    for key, value in params.items():
        if value not in net_template_map:
            net_template_map[value] = f"{{{key}}}"
    return net_template_map


def main():
    profile = DEFAULT_PROFILE
    profile_key = DEFAULT_PROFILE_KEY
    cluster = DEFAULT_CLUSTER
    args = sys.argv[1:]
    if "--profile" in args:
        profile = args[args.index("--profile") + 1]
    if "--profile-key" in args:
        profile_key = args[args.index("--profile-key") + 1]
    if "--cluster" in args:
        cluster = args[args.index("--cluster") + 1]

    params = _load_extract_profile_params(profile, profile_key)
    print(f"profile params ({profile_key}): {params}")

    adapter = KiCadBoardAdapter()
    adapter.refresh_board()
    print("connected to live board\n")

    fps = _cluster_fps(adapter, cluster)
    if not fps:
        print(f"no footprints with Cluster={cluster!r} found")
        return
    print(f"cluster {cluster!r}: {len(fps)} footprints\n")

    # ---- GUI path: _classify_selection_nets + _update_net_template_role_rows
    role_nets = selection_role_nets(adapter, fps)
    nets = sorted({n for fp in fps for n in _pad_nets(adapter, fp)})
    auto_roles = {}
    for net in nets:
        if net in RULE_NETS:
            auto_roles[net] = ("fallback", None)
            continue
        role, pad = suggest_net_from_role(role_nets, net, set(), None, None)
        if role is None:
            auto_roles[net] = ("fallback", None)
        elif pad is None:
            auto_roles[net] = ("lemma2", role)
        else:
            auto_roles[net] = ("pad", role)

    # ---- Backend path: net_template_map membership (template_extraction :422)
    net_template_map = _build_net_template_map(params)

    print("role                     classifying[...]                       mapped[...]   classifying[0]==mapped[0]?")
    print("-" * 100)
    mismatches = []
    roles_seen = set()
    for fp in fps:
        role = adapter.get_field_value(fp, ROLE_FIELD_NAME)
        if not role or role in roles_seen:
            continue
        roles_seen.add(role)
        fp_nets = _pad_nets(adapter, fp)
        classifying = sorted(
            n for n in fp_nets
            if auto_roles.get(n) and auto_roles[n][0] != "fallback")
        mapped = [n for n in fp_nets if n in net_template_map]

        gui_default = classifying[0] if classifying else "(none)"
        backend_default = mapped[0] if mapped else "(none)"
        match = "OK" if (classifying and mapped and gui_default == backend_default) else "MISMATCH"
        if match == "MISMATCH":
            mismatches.append((role, gui_default, backend_default))
        print(f"{role:30} {str(classifying):35} {str(mapped):30} {match}")

    # ---- THE decisive check (plan §3): run the REAL extractor with the SAME
    # input but WITHOUT net_template_role and compare what it ACTUALLY writes
    # to net_template against the prefill the GUI would now show (mapped[0]).
    print("\n=== real extract_template_from_selection WITHOUT net_template_role ===")
    try:
        result = extract_template_from_selection(
            adapter, "probe_prefill", params=params, items=fps)
        cell = result["probe_prefill"]
        written = {}
        for comp in cell.get("components", []):
            role = comp.get("role")
            written[role] = comp.get("net_template")
        for role in sorted(written):
            mapped = [n for n in sorted(_pad_nets(adapter,
                       next(fp for fp in fps if adapter.get_field_value(fp, ROLE_FIELD_NAME) == role)))
                      if n in net_template_map]
            prefill = mapped[0] if mapped else "(none)"
            actual = written[role] or "(empty)"
            print(f"{role:30} prefill(mapped[0])={prefill!r:35} actual net_template={actual!r}")
    except Exception as exc:  # extractor may fatal on a bare-footprint selection
        print(f"  real extract skipped: {type(exc).__name__}: {exc}")

    print("-" * 100)
    if mismatches:
        print(f"\nMISMATCH: {len(mismatches)} role(s) where classifying[0] != mapped[0]:")
        for role, gui, be in mismatches:
            print(f"  {role}: GUI-classification[0]={gui!r}  backend-designated={be!r}")
        print("\n=> the GUI prefill must use mapped[0] (backend's net_template_map membership), "
              "NOT classifying[0] — the plan's §3 concern is confirmed on live data.")
    else:
        print("\nall roles: classifying[0] == mapped[0] — either list would match the backend.")


if __name__ == "__main__":
    main()
