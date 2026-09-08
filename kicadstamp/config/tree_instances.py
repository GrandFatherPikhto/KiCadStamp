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

v1.3 (2026-09-07, plan tree_instances_params_override): a declaration may
OPTIONALLY carry `params:` — a mapping MERGED into every generated Entity
copy's OWN `params` dict (per-key: a key mentioned here overrides the template
Entity's value, a key not mentioned keeps the template's — the same
"override wins, rest inherited" semantics as the v1.2 `cluster` override,
just per-key instead of whole-field). The values are what
net_resolution.resolve_net substitutes into {placeholder}s of a component's
net_template, so this is the code half of making a net_template parametrized
by e.g. `/{channel_sheet}/DAC/+3V3_AVDD` resolve PER-INSTANCE (template
Entity params: {channel_sheet: Channel_0} + declaration params:
{channel_sheet: Channel_1} -> the generated Channel-1 copy's role resolves to
`/Channel_1/DAC/+3V3_AVDD`). When a declaration has NO `params` (None) nothing
changes: the deep copy keeps the template Entity's own params verbatim — 100%
back-compatible with pre-v1.3 declarations. Deliberately NOT applied to
net_trace materialization (same reason as `cluster` in v1.2 — a net_trace's
nets are rewritten by leading-sheet substitution, not {placeholder}-resolved).

v1.2.1 (2026-09-08, plan tree_instances_cluster_composite_guard): the v1.2
per-copy `cluster:` override now carries a COMPOSITE-guard. A template whose
placement nodes carry genuinely DIFFERENT non-empty cluster values (e.g.
ch0_dac_buf: one DAC_BUF main entity + three PIF_AVDD/PIF_CLKVDD/PIF_DVDD
sub-blocks) is composite — a blanket override would erase the very per-node
distinction the Cluster step of role_narrowing's cascade depends on
(confirmed live: ch1_dac_buf, done_2026_09_08_role_narrowing_live_probe.md).
The rule is deterministic and structural (never guesses the author's intent):
the set of distinct non-empty clusters the template's OWN nodes would generate
is computed BEFORE any override; a HOMOGENEOUS template (0 or 1 distinct
value) keeps the unconditional per-copy override exactly as v1.2 (back-compat);
a COMPOSITE one (>1) skips the per-copy override entirely — each copy keeps
its own template cluster. The generated tree's role-anchor cluster
substitution (the EXTERNAL anchor_cluster narrowing) is a separate concept and
is NOT affected by this guard (set unconditionally whenever `cluster:` is
given, see _expand_template).

v1.4 (2026-09-08, plan tree_instances_auto_root_template_support): an
auto-anchored template — NO (anchor ...) at all (TreeAnchor.is_auto, trees.py:
the anchor is derived from the tree's OWN single top-level placement node)
with EXACTLY ONE top-level placement node — is now also a valid tree_instances
template, alongside the role-anchored one (the old "must be role-anchored"
fatal is relaxed). For an auto template the anchor IS that root node (the
whole point of is_auto), so there is no separate anchor.sheet/anchor.cluster
substitution to make: the instance sheet lands on every generated copy — root
included — through the SAME unconditional _expand_node `ent['sheet'] = sheet`
path, and the v1.2.1 composite-guard walks template nodes generically (the
root is not special-cased), so cluster behaves identically for both shapes.
The only genuinely new work: (a) the entry gate admits the shape — checked
HERE with the same EXACTLY-ONE rule auto-anchor resolution itself requires
(_template_root_entity_ref), and (b) `old_sheet` for net_trace
leading-segment rewriting, which for an auto template comes from the root
Entity's OWN sheet (there is no anchor.sheet). The generated instance tree
stays auto-anchored (deep copy of a template with no 'anchor' key) — exactly
right, since a generated clone needs the identical self-resolving root-node
anchor its template has, especially when it too gets embedded as a module.

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
  - the template tree must be role-anchored ((anchor (role ...))) OR — v1.4 —
    auto-anchored (no (anchor ...) at all, with exactly ONE top-level placement
    node, the same shape auto-anchor resolution itself requires);
    origin/ref/point anchors are not parameterized by sheet;
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


def _template_generated_clusters(nodes: list, entities_by_name: dict) -> set[str]:
    """The set of distinct non-empty `cluster` values the given template
    placement nodes (recursively through children) would generate BEFORE any
    declaration-level cluster: override — used to detect whether a template is
    homogeneous (<=1 distinct value, override safe to apply everywhere, today's
    behaviour) or composite (>1 distinct value — e.g. ch0_dac_buf's DAC_BUF main
    entity + PIF_AVDD/PIF_CLKVDD/PIF_DVDD sub-blocks — where a blanket override
    would erase the very distinction role_narrowing.py's Cluster step depends
    on, confirmed live 2026-09-08, done_2026_09_08_role_narrowing_live_probe.md).

    net_trace nodes are skipped (their ref names a net, not an entity, and the
    override never applies to them anyway — see the module docstring's v1.2
    note); a placement node whose ref has no entities: entry is skipped too (it
    would be a load-time fatal elsewhere, here it must not crash the probe)."""
    clusters: set[str] = set()
    for node in nodes:
        if node.get('kind') == 'net_trace':
            continue
        ref = node.get('ref')
        entity = entities_by_name.get(ref) if ref is not None else None
        if entity is not None:
            c = entity.get('cluster')
            if c:
                clusters.add(c)
        children = node.get('children') or []
        if children:
            clusters |= _template_generated_clusters(children, entities_by_name)
    return clusters


def _template_root_entity_ref(template: dict) -> str | None:
    """Mirror of tree_position._root_entity_ref for a RAW (pre-parse) template
    dict: the ref of the template's OWN single top-level placement node, or
    None when there is no such canonical root (0 or 2+ top-level nodes, or the
    sole node isn't kind=placement/unset) — the same shape auto-anchor
    resolution (_root_entity_ref/_auto_anchor_base) requires at materialization
    time, checked HERE too so an auto template that could never resolve its own
    anchor fails at EXPANSION time with a clear message, not later during a live
    redraw of the generated instance."""
    nodes = template.get('nodes') or []
    if len(nodes) != 1:
        return None
    top = nodes[0]
    kind = top.get('kind')
    if kind is not None and kind != 'placement':
        return None
    return top.get('ref')


def _expand_node(node: dict, instance_name: str, sheet: str,
                 entities_by_name: dict, net_traces_by_net: dict,
                 generated_entities: list, generated_net_traces: list,
                 template_name: str, old_sheet: str | None,
                 cluster: str | None = None,
                 params: dict[str, str] | None = None) -> dict:
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
    anchor_cluster is a different concept — see the module docstring).

    params (2026-09-07, plan tree_instances_params_override): a
    declaration-level mapping MERGED into the generated Entity copy's OWN
    `params` (per-key override of the template Entity's params — net_template
    {placeholder} substitution values). Applied ONLY in the placement branch;
    the net_trace branch deliberately ignores it (see the module docstring)."""
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
                                        old_sheet, cluster, params)
                           for c in children]
    ent = copy.deepcopy(entity)
    ent['name'] = new_ref
    ent['sheet'] = sheet
    if cluster is not None:
        # Only when the declaration overrides — otherwise the deep copy keeps
        # the template Entity's own cluster unchanged (back-compat).
        ent['cluster'] = cluster
    if params is not None:
        # Per-key MERGE into the generated copy's OWN params — keys not named
        # in the declaration keep the template Entity's value (back-compat);
        # this is what makes a {channel_sheet}-style net_template placeholder
        # resolve per-instance (net_resolution.resolve_net reads Entity.params).
        merged_params = dict(ent.get('params') or {})
        merged_params.update(params)
        ent['params'] = merged_params
    generated_entities.append(ent)
    return gen


def _expand_template(template: dict, template_name: str, instance_name: str,
                     sheet: str, entities_by_name: dict,
                     net_traces_by_net: dict,
                     cluster: str | None = None,
                     params: dict[str, str] | None = None) -> tuple[dict, list, list]:
    """Materialize ONE instance from a template Tree dict: returns
    (tree dict, [entity dicts], [net_trace dicts]). The template dict is never
    mutated — deep copies only.

    The template may be role-anchored (an (anchor (role ...)) at the top) OR —
    v1.4 (plan tree_instances_auto_root_template_support) — auto-anchored (no
    (anchor ...) at all, with exactly ONE top-level placement node,
    TreeAnchor.is_auto; the shape is checked at the entry gate via
    _template_root_entity_ref, mirroring auto-anchor resolution's own rule). For
    a ROLE template the instance sheet is written into the deep-copied anchor
    (gen['anchor']['sheet']) and `old_sheet` (the net_trace leading-segment
    rewrite's source) is anchor.sheet. For an AUTO template there is no 'anchor'
    key to mutate — the generated tree stays auto-anchored and `old_sheet` is
    the root Entity record's own sheet; the instance sheet/cluster reach the
    copies through the SAME per-node mechanism as any other node (_expand_node),
    because for an auto template the anchor IS its root node.

    cluster (2026-09-03, plan tree_instances_cluster): when a declaration
    carries `cluster:`, it lands on the generated ROLE anchor (only when the
    template is role-anchored — an auto template has no anchor to carry it) AND,
    via _expand_node, every generated Entity copy. The per-copy half got a
    COMPOSITE-guard (v1.2.1, 2026-09-08, plan
    tree_instances_cluster_composite_guard): a template whose placement nodes
    would generate >1 distinct non-empty cluster values is composite, and a
    blanket per-copy override there would erase exactly the per-node distinction
    role_narrowing's Cluster step depends on — so for a composite template the
    per-copy override is skipped (effective_node_cluster becomes None) and each
    copy keeps its own template cluster, while a role anchor is STILL overridden
    (the two substitutions are separate concepts — external-anchor narrowing vs
    each copy's own identity). A homogeneous template (<=1 distinct value) keeps
    the unconditional per-copy override exactly as before (back-compat)."""
    anchor = template.get('anchor')
    is_role_anchor = (isinstance(anchor, dict)
                      and isinstance(anchor.get('role'), str)
                      and bool(anchor.get('role')))
    # v1.4 (plan tree_instances_auto_root_template_support): the entry gate now
    # admits TWO template shapes — a role-anchored one AND an auto-anchored one
    # (no (anchor ...) at all = TreeAnchor.is_auto). An explicit NON-role anchor
    # (origin/ref/point) is neither and stays a fatal (not parameterized by
    # sheet). For an auto template the anchor IS its own single top-level
    # placement node (the whole point of is_auto) — so the gate mirrors
    # auto-anchor resolution's EXACTLY-ONE rule (tree_position._root_entity_ref
    # / entity_placement._auto_anchor_base) at EXPANSION time: an auto template
    # that could never resolve its own anchor fails HERE with a clear message,
    # not later during a live redraw of the generated instance.
    root_ref = None if is_role_anchor else _template_root_entity_ref(template)
    if not is_role_anchor and (anchor or root_ref is None):
        raise ValidationError(format_fatal_error(
            _("tree_instance: template {template!r} must be role-anchored OR "
              "auto-anchored with exactly one top-level placement node (v1.4)")
            .format(template=template_name),
            [_("either an (anchor (role ...)) template, or NO (anchor ...) at "
               "all with exactly one top-level placement node (the same shape "
               "auto-anchor resolution itself requires) — the instance sheet "
               "substitutes the role anchor's sheet, or (for an auto template) "
               "the root Entity's own sheet; origin/ref/point anchors are still "
               "not parameterized by sheet")]))

    if is_role_anchor:
        old_sheet = anchor.get('sheet')
    else:
        # Auto template: the old sheet for net_trace leading-segment rewriting
        # comes from the root node's OWN Entity record (there is no anchor.sheet
        # to read). The missing-record fatal deliberately duplicates
        # _expand_node's "no matching entities:" wording — identical cause,
        # identical message (and _expand_node still repeats the same check for
        # non-root nodes further down, as always — not removed).
        root_entity = entities_by_name.get(root_ref)
        if root_entity is None:
            raise ValidationError(format_fatal_error(
                _("tree_instance: template {template!r} node {ref!r} has no "
                  "matching entities: record").format(template=template_name,
                                                       ref=root_ref),
                [_("the auto-anchored template's single top-level node must "
                   "reference an existing entities: entry by its name")]))
        old_sheet = root_entity.get('sheet')
    gen = copy.deepcopy(template)
    gen['name'] = instance_name
    if is_role_anchor:
        gen['anchor']['sheet'] = sheet
        if cluster is not None and isinstance(gen['anchor'].get('role'), str):
            # Cluster override lands on the role anchor too (we are in the role
            # branch, so the isinstance guard only documents that cluster
            # substitution is a role-anchor concept). Set UNCONDITIONALLY when
            # cluster is given, same pattern as sheet — even if the template
            # anchor carried no cluster of its own.
            gen['anchor']['cluster'] = cluster
    # else (auto-anchored template): there is no 'anchor' key to mutate — the
    # generated tree stays auto-anchored too (gen has no 'anchor' key, same as
    # the template), which is exactly right: the generated instance's own root
    # Entity copy (its sheet already substituted unconditionally by
    # _expand_node, its cluster by the same composite-guard rule as every other
    # node) already carries everything a NEW auto-anchor resolution needs.

    # 2026-09-08 (plan tree_instances_cluster_composite_guard): the per-node
    # Entity cluster override below is a DIFFERENT concept from the anchor's
    # own cluster just set above (external-anchor narrowing vs each copy's OWN
    # internal identity — role_narrowing.py's anchor_cluster/cluster split).
    # Unconditional substitution is only correct for a HOMOGENEOUS template; a
    # COMPOSITE one (multiple nodes with genuinely different template clusters)
    # would have the override erase real per-node distinctions that
    # role_narrowing's Cluster step depends on — confirmed live
    # (done_2026_09_08_role_narrowing_live_probe.md). Deterministic, structural
    # detection — never guesses the declaration author's intent: a homogeneous
    # template keeps today's unconditional-override behaviour exactly (0
    # distinct or 1 distinct value), a composite one skips the per-node
    # override entirely (each copy keeps its own template cluster).
    effective_node_cluster = cluster
    if cluster is not None:
        generated_clusters = _template_generated_clusters(
            template.get('nodes') or [], entities_by_name)
        if len(generated_clusters) > 1:
            logger.info(_("tree_instance {name!r}: template {template!r} is "
                          "composite ({count} distinct clusters among its "
                          "nodes: {clusters}) — cluster override {cluster!r} is "
                          "NOT applied to individual Entity copies (each keeps "
                          "its own template cluster); the tree's own role-anchor "
                          "cluster is still overridden as requested")
                        .format(name=instance_name, template=template_name,
                                count=len(generated_clusters),
                                clusters=", ".join(sorted(generated_clusters)),
                                cluster=cluster))
            effective_node_cluster = None

    generated_entities: list = []
    generated_net_traces: list = []
    gen['nodes'] = [_expand_node(n, instance_name, sheet, entities_by_name,
                                 net_traces_by_net, generated_entities,
                                 generated_net_traces, template_name, old_sheet,
                                 effective_node_cluster, params)
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
        # v1.3 (plan tree_instances_params_override): OPTIONAL per-instance
        # params merge. Validation duplicated here on purpose — this function
        # runs on the raw dict BEFORE the _load_tree_instance loader (see the
        # module docstring), so it cannot rely on entries.py having already
        # validated the declaration (same duplication discipline as `cluster`
        # just above). Only the "is it a mapping" guard — no per-value checks,
        # same level as Entity.params elsewhere.
        params = inst.get('params')
        if params is not None and not isinstance(params, dict):
            raise ValidationError(format_fatal_error(
                _("tree_instances: entry #{idx} has a non-mapping params:")
                .format(idx=idx + 1),
                [_("params:, when present, must be a mapping of string keys to "
                   "string values — omit the key entirely to inherit the "
                   "template Entity's own params unchanged")]))
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
            net_traces_by_net, cluster, params)
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
