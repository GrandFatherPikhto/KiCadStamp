# kicadstamp/cli_extract.py
"""
Library core of the `extract` / `clone-extract` commands.

Pure logic only — deliberately free of CLI idioms (no sys.exit / input /
print / argparse.Namespace). Callers that need exit codes or interactive
prompts live in the thin CLI wrappers (kicadstamp/cli.py and the entry
point kicadstamp_cli.py), which translate the raised PlacerError /
ValidationError into process exit codes.
"""

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from kicadstamp.config.includes import resolve_includes
from kicadstamp.config.sexp_format import dict_to_sexp, sexp_to_dict
from kicadstamp.utils.yaml_loader import safe_load
from kicadstamp.exceptions import PlacerError, check_unknown_keys
from kicadstamp.kicad.interfaces import IBoardAdapter
from kicadstamp.template_extraction import extract_template_from_selection, render_uncertain_comments
from kicadstamp.i18n import _

logger = logging.getLogger(__name__)


# extract_profiles: / clone_profiles: known keys — see load_profile's
# known_keys param. Field names read from the profile dict: cmd_extract's
# --profile branch (name/output/params/net_template/net_template_role/
# origin_by_via_net/origin_by_component_role/origin_by_component_pad/
# raw_selection) and clone-extract's --profile branch (net/pcb/channel/output).
#
# Public names (no underscore prefix): cli.py and the tests consume these
# across the module boundary, and the project rule is that private names do
# not cross package/module boundaries (same cleanup as config_writer's
# read_data/write_data). The old underscore-prefixed spellings were the
# original public contract here; they are dropped since only in-repo callers
# used them and all have been updated.
EXTRACT_PROFILE_KNOWN_KEYS = {
    'name', 'output', 'params', 'net_template', 'net_template_role', 'rule_nets',
    'origin_by_via_net', 'origin_by_component_role', 'origin_by_component_pad',
    'raw_selection',
}
CLONE_EXTRACT_PROFILE_KNOWN_KEYS = {'net', 'pcb', 'channel', 'output'}


def load_profile(profiles_path: str, top_key: str, profile_name: str,
                  root_defaults: list[str] | None = None,
                  known_keys: set | None = None) -> dict[str, Any]:
    """
    Common loader for named CLI profiles (for extract and clone-extract).
    top_key is different for each command (extract_profiles / clone_profiles).

    root_defaults — field names that, if set at the ROOT of the file (sibling
    to top_key, e.g. output: next to extract_profiles:) and not already set
    on the selected profile itself, are merged in as a fallback.  For fields
    that are almost always the same across every profile in the file (e.g.
    every extract_profiles entry for one board writes into the same cells
    file) — set it once at the root instead of repeating it per profile; a
    profile that genuinely needs a different value still overrides it as
    before, just by setting the field directly.  Empty/None (default) — no
    change from the old behaviour, used as-is by clone-extract.

    known_keys — if given, fatal on any key in the selected profile outside
    this set (see check_unknown_keys) — same protection clone_placements/
    rules already have.  A typo'd or wrong-separator key (e.g.
    'origin-by-via-net' instead of 'origin_by_via_net') was previously
    silently ignored: found live on boards/3ch-awg-tia, the origin quietly
    fell back to the selection bbox instead of the intended via.

    Raises PlacerError if the profiles file does not exist or does not contain
    the named profile — the CLI layer maps that to exit code 1.
    """
    p = Path(profiles_path)
    if not p.exists():
        raise PlacerError(_("[error] profiles file {path!r} not found").format(path=profiles_path))
    with open(p, "r", encoding="utf-8") as f:
        if p.suffix.lower() == ".sexp":
            data = sexp_to_dict(f.read()) or {}
        else:
            data = safe_load(f) or {}
    data = resolve_includes(str(p), data)
    profiles = data.get(top_key, {})
    if profile_name not in profiles:
        available = list(profiles.keys())
        raise PlacerError(_("[error] profile {name!r} not found in {top_key!r} of file {path!r}. "
                            "Available: {avail}")
                          .format(name=profile_name, top_key=top_key, path=profiles_path, avail=available))
    prof = dict(profiles[profile_name])
    for field in (root_defaults or []):
        if field not in prof and field in data:
            prof[field] = data[field]
    if known_keys is not None:
        check_unknown_keys(prof, known_keys,
                           _("unknown fields in {top_key} {name!r} of {path!r}")
                           .format(top_key=top_key, name=profile_name, path=profiles_path))
    return prof


def extract_template(adapter: IBoardAdapter, *, name: str, output: str,
                     params: dict[str, Any] | None = None,
                     net_template_map: dict[str, str] | None = None,
                     net_template_role: dict[str, str] | None = None,
                     rule_nets: set[str] | None = None,
                     origin_via_net: str | None = None,
                     origin_component_role: str | None = None,
                     origin_component_pad: str | None = None,
                     raw_selection: bool = False) -> dict[str, Any]:
    """Extract a spoke cell template from the current board selection and
    merge-write it, wrapped under a 'cells:' key, into `output` (YAML/JSON/
    s-expr by file suffix), preserving any existing entries (including any
    OTHER top-level key the
    file already owns, e.g. extract_profiles: if `output` is also the
    Extractor file — cells_file:/cell_files: were folded into include: on
    2026-08-02, so the on-disk shape here now matches an inline cells:
    block, and this file can be include:'d directly).

    Library core of the `extract` command — no CLI idioms: raises PlacerError
    on invalid arguments, never touches stdin/stdout/process exit codes.
    `adapter` must already be connected and refreshed (the CLI wrapper does
    that). Returns the written template dict.
    """
    if origin_component_pad and not origin_component_role:
        raise PlacerError(_("[error] --origin-by-component-pad without --origin-by-component-role — "
                            "you can only refine a pad for a role that you first specify"))

    annotations: list[tuple[str, str, str]] = []
    template_dict = extract_template_from_selection(
        adapter, name, params=params or {}, net_template_map=net_template_map or {},
        origin_via_net=origin_via_net,
        origin_component_role=origin_component_role,
        origin_component_pad=origin_component_pad,
        net_template_role=net_template_role or {},
        rule_nets=rule_nets or set(),
        annotations=annotations,
        raw_selection=raw_selection,
    )

    output_path = Path(output)
    is_json = output_path.suffix.lower() == '.json'
    is_sexp = output_path.suffix.lower() == '.sexp'
    existing = {}
    if output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            if is_json:
                existing = json.load(f) or {}
            elif is_sexp:
                existing = sexp_to_dict(f.read()) or {}
            else:
                existing = safe_load(f) or {}
    existing_cells = existing.setdefault('cells', {})
    if name in existing_cells:
        logger.warning(_("Template {name!r} already exists in {output} — will be overwritten")
                       .format(name=name, output=output_path))

    existing_cells.update(template_dict)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        if is_json:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        elif is_sexp:
            # render_uncertain_comments is a YAML-text post-processor (it
            # splices "# field: hint" comment lines into yaml.dump output) —
            # it has no s-expr equivalent yet, so .sexp output simply does
            # not carry the uncertainty annotations (2026-08-28; porting the
            # renderer to s-expr syntax is a separate, larger task, out of
            # scope for the .sexp output fix).
            f.write(dict_to_sexp(existing))
        else:
            text = yaml.dump(existing, allow_unicode=True, sort_keys=False, default_flow_style=False)
            if annotations:
                text = render_uncertain_comments(text, name, annotations, indent=2)
            f.write(text)

    logger.info(_("✅ Template {name!r} written to {output}").format(name=name, output=output_path))
    return template_dict
