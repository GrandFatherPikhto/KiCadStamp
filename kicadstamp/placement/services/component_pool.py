# kicadstamp/placement/services/component_pool.py
"""
component_pool.py — selects concrete components for cell roles by
(real net, custom Role field), not by explicit ref in config.

The pool is built once per rule net and shared among ALL spokes of that rule —
components are consumed in order, in deterministic (natural numeric: C5 < C10,
not lexicographic) order. If a role runs out of components — fatal ValidationError,
not silent shortage.
"""
import re
import logging


from ...cluster_matching import cluster_prefix_match
from ...kicad.adapter import KiCadBoardAdapter
from ...exceptions import ValidationError
from ...constants import ROLE_FIELD_NAME, CLUSTER_FIELD_NAME
from ...i18n import _

logger = logging.getLogger(__name__)


def _natural_sort_key(ref: str):
    """C5 < C10 — not like ordinary string sorting ('C10' < 'C5')."""
    parts = re.split(r'(\d+)', ref)
    return [int(p) if p.isdigit() else p for p in parts]


class ComponentPool:
    """
    Pool of components for one rule net, grouped by role.
    Built once; spokes of this net consume it in order via pop().
    """

    def __init__(self, adapter: KiCadBoardAdapter, net_name: str, roles: list[str],
                 cluster: str | None = None):
        self.adapter = adapter
        self.net_name = net_name
        self.cluster = cluster
        self._pools: dict[str, list[str]] = {role: [] for role in roles}
        self._build()

    def _build(self):
        for fp in self.adapter.get_footprints():
            role = self.adapter.get_field_value(fp, ROLE_FIELD_NAME)
            if role is None or role not in self._pools:
                continue
            # If cluster is specified, check by segment‑prefix (same principle
            # as anchor_cluster) — not exact equality.
            if self.cluster is not None:
                fp_cluster = self.adapter.get_field_value(fp, CLUSTER_FIELD_NAME)
                if fp_cluster is None or not cluster_prefix_match(fp_cluster, self.cluster):
                    continue
            pads = self.adapter.get_footprint_pads(fp)
            nets_on_fp = {p.net.name for p in pads if p.net and p.net.name}
            if self.net_name not in nets_on_fp:
                continue
            ref = fp.reference_field.text.value
            self._pools[role].append(ref)

        for role in self._pools:
            self._pools[role].sort(key=_natural_sort_key)
            cluster_suffix = _(" (cluster={cluster})").format(cluster=self.cluster) if self.cluster else ""
            logger.debug(_("Pool {net!r}/{role!r}{suffix}: {refs}")
                         .format(net=self.net_name, role=role, suffix=cluster_suffix,
                                 refs=self._pools[role]))

    def pop(self, role: str, spoke_pad: str) -> str:
        """
        Takes the next (in natural order) component with the given role.
        Fatal error if the pool for this role is already exhausted.
        """
        candidates = self._pools.get(role)
        if candidates is None:
            raise ValidationError(
                _("\nCell (pad {pad}) requires role {role!r}, "
                  "but the pool for net {net!r} does not know this role at all "
                  "(check the list of roles passed when building the pool).")
                .format(pad=spoke_pad, role=role, net=self.net_name)
            )
        if not candidates:
            raise ValidationError(
                _("\nNot enough components with role {role!r} on net {net!r} "
                  "for spoke on pad {pad} — pool exhausted. "
                  "Check the {field!r} field in the schematic: perhaps you forgot "
                  "to mark another component, or it is not physically on this net.")
                .format(role=role, net=self.net_name, pad=spoke_pad, field=ROLE_FIELD_NAME)
            )
        return candidates.pop(0)

    def remaining_count(self, role: str) -> int:
        return len(self._pools.get(role, []))
