# kicadstamp/config/tree_instances.py
"""
config/tree_instances.py — `tree_instances:` expansion.

Pure dict work that runs AFTER resolve_includes() + expand_sheet_templates()
and BEFORE any per-entry loader (_load_entity/_load_tree) — see
techdocs/handoff/deepseek/plan_2026_09_02_tree_instances.md (P0, 2026-09-02
revision: dict-level expansion).

A `tree_instances:` declaration is a SHORT reference to a template tree:

    tree_instances:
      - template: dac_buf      # name of an existing trees: entry
        name: ch1_dac_buf      # the generated tree's name
        sheet: Channel_1       # substituted into the copies

expand_tree_instances() materializes, for each declaration, ONE full Tree dict
(deep copy of the template: nodes get `ref += __{instance.name}` recursively,
the role anchor's sheet becomes the instance's) plus one Entity dict per
template placement node (deep copy of the referenced Entity, renamed to the
new ref, sheet = instance's sheet), and appends them to `trees:`/`entities:`.

v1.1 (2026-09-02, plan_2026_09_02_tree_instances_net_trace.md, design §10
"Вариант Б"): a template node of kind=net_trace is materialized TOO — one
NetTrace copy per instance, appended to `net_traces:`. Unlike a placement
node (whose ref is an arbitrary name and is simply suffixed `__{instance}`), a
net_trace node's ref IS a board net path (`/{sheet}/{group}/{signal}`), which
must stay a real net name for net_trace_planner/KiCad — so instead of a suffix
the net's LEADING sheet segment is replaced (e.g. `/Channel_0/DAC/+3V3` ->
`/Channel_1/DAC/+3V3`), independently on the record's own `net`, on every
`tracks[].net` and every `vias[].net`. The old sheet is the TEMPLATE tree's
own role-anchor `sheet` (anchor.sheet — the single point of sheet
parameterization for the whole template, see Q2): a net whose leading segment
is not that sheet is NOT this template's copper and is a fatal, never silently
rewritten. The generated NetTrace's `anchor_sheet` is unconditionally
overwritten with the instance sheet (same pattern as Tree.anchor.anchor_sheet
and the Entity copy's sheet). Registry identity (`net_trace_anchor_id` is
built from the net) is therefore per-copy automatically, no extra code.

v1.2 (2026-09-03, plan tree_instances_cluster): a declaration may OPTIONALLY
carry `cluster:` — an override substituted into every generated Entity copy's
`cluster` AND (the template is role-anchored by a v1 fatal) the generated
tree's role-anchor `cluster`, exactly mirroring how `sheet` is substituted.
The axis is independent from `sheet` (same-sheet/different-cluster,
different-sheet/same-cluster and both-different are all expressible). When a
declaration has NO `cluster` (None) nothing changes: each generated copy keeps
the cluster of its template Entity (or lacks one), and the anchor keeps its
template cluster — 100% back-compatible with pre-v1.2 declarations. The
override is deliberately NOT applied to net_trace materialization: a
net_trace's `anchor_cluster` narrows an EXTERNAL anchor search (which
component the copper is anchored to) — a different concept from "which
physical group this Entity is", and conflating them would be a semantic bug
(see design_cell_template_reuse §3, "Явно ВНЕ рамок").

The materialized dicts then flow through the SAME _load_entity/_load_tree/
_load_net_trace path as hand-written entries — duplicate-name checks, rule 2
(shared seen_refs), the one-record-per-net net_traces dedup, unknown-key
checks and the layer/mirror cross-validation apply to them for free, with zero
duplicated validation logic (the reason this is dict-level and NOT
post-dataclass: see the plan revision).

The raw `tree_instances:` key is deliberately LEFT INTACT in the returned
dict — the loader parses it into cfg.tree_instances, the persistence source
and the GUI's read-only-instance index. Materialized trees/entities/net_traces
are never persisted as such (the GUI's TreesDock save path excludes them).

v1 template constraints (each is a hard fatal, never a silent skip):
  - the template tree's anchor must be `role`-based (origin/ref/point/auto
    anchors are not parameterized by sheet yet);
  - every template node must be kind=placement (or unset/auto) or kind=
    net_trace (chain/coordinate/clone/module nodes inside a template are not
    instantiated yet);
  - a placement node's ref must name an existing entities: entry; a net_trace
    node's ref must name an existing net_traces: entry (its net);
  - a net_trace node additionally requires the template's role anchor to carry
    a real sheet (the old sheet whose leading net segment is rewritten), and
    the net's leading segment must equal that sheet.

Q2 (revised 2026-09-02, second round): a referenced template Entity MAY carry
its OWN real `sheet` — it is REQUIRED for the template's own live
re-readability ("Reread current position": a live component is found by
Role+Sheet+Cluster, so a sheetless template Entity is ambiguous when the same
Role+Cluster exists on several sheets, exactly the AD_DAC-on-Channel_0/1/2
case). Expansion does NOT fatal on it and does NOT keep it: the generated
ENTITY COPY's sheet is unconditionally overwritten with the instance sheet
(the same pattern as the role-anchor sheet and as sheet_templates.py's
`gen['sheet'] = sheet`). The template itself (and the file on disk) is never
mutated — expansion works on copy.deepcopy only.
"""
import copy
import logging

from ..exceptions import ValidationError, format_fatal_error
from ..i18n import _

logger = logging.getLogger(__name__)


def _substitute_net_sheet(net: str, old_sheet: str, new_sheet: str,
                          template_name: str, node_ref: str) -> str:
    """Replace the leading sheet segment of a net path `/{sheet}/...`.

    Fatal (never silent) when the leading segment does not equal old_sheet —
    a net whose leading segment isn't this template's own sheet is not this
    template's copper and must not be rewritten; the same fatal guards a
    malformed (non-`/`-prefixed) net string."""
    parts = net.split('/')
    if len(parts) < 2 or parts[0] != '' or parts[1] != old_sheet:
        raise ValidationError(format_fatal_error(
            _("tree_instance: template {template!r} net {ref!r} is not a "
              "{old_sheet!r}-sheet net path").format(template=template_name,
                                                     ref=node_ref,
                                                     old_sheet=old_sheet),
            [_("a net_trace node's net must be a board net path "
               "'/{sheet}/{group}/{signal}' whose leading sheet segment equals "
               "the template tree's anchor sheet — only that copper belongs to "
               "this template and is rewritten per instance")]))
    parts[1] = new_sheet
    return '/'.join(parts)


def _expand_node(node: dict, instance_name: str, sheet: str,
                 entities_by_name: dict, net_traces_by_net: dict,
                 generated_entities: list, generated_net_traces: list,
                 template_name: str, old_sheet: str | None,
                 cluster: str | None = None) -> dict:
    """Deep-copy one template node dict into the instance shape.

    kind=placement (or unset/auto): the node's ref is suffixed with
    __{instance_name} (recursively through children), its matching template
    Entity is copied into generated_entities under the new ref with the
    instance sheet.

    kind=net_trace (v1.1): the node's ref is a board net; the matching
    net_traces: record (found by net) is copied into generated_net_traces with
    the leading sheet segment of its net (and of every track/via net) replaced
    by the instance sheet, its anchor_sheet unconditionally overwritten, and
    the node's own ref rewritten to the new net. Other node kinds
    (chain/coordinate/clone/module) stay a fatal.

    Q2 (revised 2026-09-02): the template Entity's OWN sheet is deliberately
    NOT a fatal and NOT copied — the template keeps it for its own live
    re-readability, the generated copy unconditionally gets the instance
    sheet (same overwrite pattern as the role-anchor sheet).

    cluster (2026-09-03, plan tree_instances_cluster): a declaration-level
    override applied ONLY in the placement branch — when not None it
    overwrites the generated Entity copy's `cluster`; when None the deep copy
    keeps the template Entity's own cluster unchanged (today's behaviour).
    The net_trace branch deliberately ignores it (a net_trace's
    anchor_cluster is a different concept — see the module docstring)."""
    orig_ref = node.get('ref')
    if orig_ref is None:
        raise ValidationError(format_fatal_error(
            _("tree_instance: template {template!r} has a node without a ref")
            .format(template=template_name),
            [_("every template node needs a ref naming an entities: entry "
               "(placement) or a net_traces: entry by its net (net_trace)")]))
    kind = node.get('kind')
    if kind == 'net_trace':
        if not old_sheet:
            raise ValidationError(format_fatal_error(
                _("tree_instance: template {template!r} net_trace node {ref!r} "
                  "needs the template's anchor sheet")
                .format(template=template_name, ref=orig_ref),
                [_("a net_trace node's net is rewritten by replacing its leading "
                   "sheet segment, so the template tree's role anchor must carry "
                   "a sheet (anchor.sheet) naming the template's own sheet")]))
        record = net_traces_by_net.get(orig_ref)
        if record is None:
            raise ValidationError(format_fatal_error(
                _("tree_instance: template {template!r} node {ref!r} has no "
                  "matching net_traces: record").format(template=template_name,
                                                        ref=orig_ref),
                [_("every net_trace node of a tree template must reference an "
                   "existing net_traces: entry by its net name")]))

        gen = copy.deepcopy(node)
        new_net = _substitute_net_sheet(orig_ref, old_sheet, sheet,
                                        template_name, orig_ref)
        gen['ref'] = new_net
        gen_nt = copy.deepcopy(record)
        gen_nt['net'] = new_net
        gen_nt['anchor_sheet'] = sheet
        for t in gen_nt.get('tracks') or []:
            if isinstance(t, dict) and t.get('net'):
                t['net'] = _substitute_net_sheet(t['net'], old_sheet, sheet,
                                                 template_name, orig_ref)
        for v in gen_nt.get('vias') or []:
            if isinstance(v, dict) and v.get('net'):
                v['net'] = _substitute_net_sheet(v['net'], old_sheet, sheet,
                                                 template_name, orig_ref)
        generated_net_traces.append(gen_nt)
        children = node.get('children') or []
        if children:
            gen['children'] = [_expand_node(c, instance_name, sheet,
                                            entities_by_name, net_traces_by_net,
                                            generated_entities,
                                            generated_net_traces,
                                            template_name, old_sheet)
                               for c in children]
        return gen

    if kind is not None and kind != 'placement':
        raise ValidationError(format_fatal_error(
            _("tree_instance: template {template!r} has unsupported node kind "
              "{kind!r} (ref {ref!r})").format(template=template_name, kind=kind,
                                               ref=orig_ref),
            [_("v1 tree templates support only kind=placement (Entity) and "
               "kind=net_trace nodes — chain/coordinate/clone/module nodes "
               "inside a template are not instantiated yet")]))
    entity = entities_by_name.get(orig_ref)
    if entity is None:
        raise ValidationError(format_fatal_error(
            _("tree_instance: template {template!r} node {ref!r} has no matching "
              "entities: record").format(template=template_name, ref=orig_ref),
            [_("a placement node of a tree template must reference an existing "
               "entities: entry by its name")]))

    new_ref = f"{orig_ref}__{instance_name}"
    gen = copy.deepcopy(node)
    gen['ref'] = new_ref
    children = node.get('children') or []
    if children:
        gen['children'] = [_expand_node(c, instance_name, sheet, entities_by_name,
                                        net_traces_by_net, generated_entities,
                                        generated_net_traces, template_name,
                                        old_sheet, cluster)
                           for c in children]
    ent = copy.deepcopy(entity)
    ent['name'] = new_ref
    ent['sheet'] = sheet
    if cluster is not None:
        # Only when the declaration overrides — otherwise the deep copy keeps
        # the template Entity's own cluster unchanged (back-compat).
        ent['cluster'] = cluster
    generated_entities.append(ent)
    return gen


def _expand_template(template: dict, template_name: str, instance_name: str,
                     sheet: str, entities_by_name: dict,
                     net_traces_by_net: dict,
                     cluster: str | None = None) -> tuple[dict, list, list]:
    """Materialize ONE instance from a template Tree dict: returns
    (tree dict, [entity dicts], [net_trace dicts]). The template dict is never
    mutated — deep copies only."""
    anchor = template.get('anchor')
    if not (isinstance(anchor, dict) and isinstance(anchor.get('role'), str)
            and anchor.get('role')):
        raise ValidationError(format_fatal_error(
            _("tree_instance: template {template!r} must be role-anchored (v1)")
            .format(template=template_name),
            [_("only an (anchor (role ...)) template is supported — the instance "
               "sheet substitutes the role anchor's sheet at load; origin/ref/"
               "point/auto anchors are not parameterized by sheet yet")]))

    old_sheet = anchor.get('sheet')
    gen = copy.deepcopy(template)
    gen['name'] = instance_name
    gen['anchor']['sheet'] = sheet
    if cluster is not None and isinstance(gen['anchor'].get('role'), str):
        # Cluster override lands on the role anchor too (the anchor is already
        # guaranteed role-based by the fatal above — the isinstance guard only
        # documents that cluster substitution is a role-anchor concept). Set
        # UNCONDITIONALLY when cluster is given, same pattern as sheet — even
        # if the template anchor carried no cluster of its own.
        gen['anchor']['cluster'] = cluster
    generated_entities: list = []
    generated_net_traces: list = []
    gen['nodes'] = [_expand_node(n, instance_name, sheet, entities_by_name,
                                 net_traces_by_net, generated_entities,
                                 generated_net_traces, template_name, old_sheet,
                                 cluster)
                    for n in (template.get('nodes') or [])]
    return gen, generated_entities, generated_net_traces


def expand_tree_instances(data: dict) -> dict:
    """Append one materialized Tree dict + its Entity/NetTrace dicts per
    tree_instances: declaration to a COPY of `data`'s 'trees'/'entities'/
    'net_traces' and return the copy. The input dict is never mutated and the
    raw 'tree_instances:' key survives untouched (the loader still parses it
    into cfg.tree_instances).

    Returns `data` unchanged when there are no tree_instances: declarations."""
    result = dict(data)
    instances = data.get('tree_instances')
    if not instances:
        return result
    if not isinstance(instances, list):
        raise ValidationError(format_fatal_error(
            _("'tree_instances' must be a list of template/name/sheet "
              "declarations"),
            [_("got {type} — expected a list of declarations, each with "
               "template:/name:/sheet: (e.g. template: dac_buf, "
               "name: ch1_dac_buf, sheet: Channel_1)")
             .format(type=type(instances).__name__)]))

    trees = list(data.get('trees') or [])
    entities = list(data.get('entities') or [])
    net_traces = list(data.get('net_traces') or [])
    entities_by_name: dict = {}
    for ent in entities:
        if isinstance(ent, dict) and isinstance(ent.get('name'), str):
            entities_by_name[ent['name']] = ent
    net_traces_by_net: dict = {}
    for nt in net_traces:
        if isinstance(nt, dict) and isinstance(nt.get('net'), str):
            net_traces_by_net[nt['net']] = nt
    trees_by_name: dict = {}
    for t in trees:
        if isinstance(t, dict) and isinstance(t.get('name'), str):
            trees_by_name[t['name']] = t

    for idx, inst in enumerate(instances):
        if not isinstance(inst, dict):
            raise ValidationError(format_fatal_error(
                _("tree_instances: entry #{idx} must be a mapping").format(idx=idx + 1),
                [_("each entry is a dict with template:/name:/sheet:")]))
        template_name = inst.get('template')
        instance_name = inst.get('name')
        sheet = inst.get('sheet')
        for field_label, value in (("template", template_name),
                                   ("name", instance_name),
                                   ("sheet", sheet)):
            if not isinstance(value, str) or not value:
                raise ValidationError(format_fatal_error(
                    _("tree_instances: entry #{idx} missing required {field}:")
                    .format(idx=idx + 1, field=field_label),
                    [_("every tree_instances: entry needs template:/name:/sheet: "
                       "(non-empty strings)")]))
        cluster = inst.get('cluster')
        if cluster is not None and (not isinstance(cluster, str) or not cluster):
            raise ValidationError(format_fatal_error(
                _("tree_instances: entry #{idx} has an empty cluster:")
                .format(idx=idx + 1),
                [_("cluster:, when present, must be a non-empty string — omit "
                   "the key entirely to inherit the template's own cluster "
                   "unchanged")]))
        template = trees_by_name.get(template_name)
        if template is None:
            raise ValidationError(format_fatal_error(
                _("tree_instance {name!r}: template tree {template!r} not found")
                .format(name=instance_name, template=template_name),
                [_("known trees: {names}").format(
                    names=", ".join(sorted(trees_by_name)) or _("(none)"))]))
        (generated_tree, generated_entities,
         generated_net_traces) = _expand_template(
            template, template_name, instance_name, sheet, entities_by_name,
            net_traces_by_net, cluster)
        trees.append(generated_tree)
        entities.extend(generated_entities)
        net_traces.extend(generated_net_traces)
        # Register generated names so a second instance of the SAME template
        # (or a later declaration) resolving by name sees a consistent index;
        # duplicates are still caught downstream by the trees/entities
        # duplicate-name checks.
        trees_by_name[instance_name] = generated_tree
        for ent in generated_entities:
            entities_by_name[ent['name']] = ent

    result['trees'] = trees
    result['entities'] = entities
    result['net_traces'] = net_traces
    logger.info(_("Expanded {count} tree_instances: declarations into "
                  "trees/entities/net_traces").format(count=len(instances)))
    return result
