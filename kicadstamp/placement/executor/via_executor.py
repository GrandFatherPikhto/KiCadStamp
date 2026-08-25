# kicadstamp/placement/executor/via_executor.py
import logging

from kicadstamp.kicad.adapter import KiCadBoardAdapter
from ...config import Config
from ..commands import ViaCommand
from ...registry import PlacementRegistry
from ...utils.units import MM
from ...i18n import _

logger = logging.getLogger(__name__)

class ViaExecutor:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config, batch_size: int = 10):
        self.adapter = adapter
        self.cfg = config
        self.batch_size = batch_size

    def execute_vias(self, vias: list[ViaCommand], registry: PlacementRegistry | None = None) -> tuple[list[str], list[dict]]:
        failed_via_owners = []
        created_via_log = []

        via_batches = [vias[i:i+self.batch_size] for i in range(0, len(vias), self.batch_size)]
        logger.info(_("Creating vias in {count} batches").format(count=len(via_batches)))
        for idx, batch in enumerate(via_batches, 1):
            # (cmd, uuid) pairs, recorded into the registry ONLY after the
            # commit actually succeeded (P0-3, 2026-08-25: record_created used
            # to run inside work(), i.e. before push_commit — a crash between
            # the JSON write and the board commit then left the registry lying).
            pending = []

            def work(batch=batch):
                new_vias = []
                cmd_for_via = []
                for cmd in batch:
                    net = self.adapter.get_net_by_name(cmd.net_name)
                    if net is None:
                        logger.warning(_("  net {net} not found for via for {owner}")
                                       .format(net=cmd.net_name, owner=cmd.owner_ref))
                        continue
                    via = self.adapter.create_via(cmd.position, net, cmd.drill_mm, cmd.diameter_mm)
                    new_vias.append(via)
                    cmd_for_via.append(cmd)
                if new_vias:
                    created = self.adapter.create_items(new_vias)
                    for cmd, v in zip(cmd_for_via, created):
                        uuid_str = str(v.id.value)
                        created_via_log.append({
                            'uuid': uuid_str,
                            'x_mm': v.position.x / MM,
                            'y_mm': v.position.y / MM,
                            'diameter_mm': v.diameter / MM,
                            'drill_mm': v.drill_diameter / MM,
                            'net_name': v.net.name,
                            'owner_ref': cmd.owner_ref
                        })
                        pending.append((cmd, uuid_str))
                    logger.debug(_("  created {count} vias").format(count=len(created)))
            ok = self.adapter.commit_with_retry(_("Via batch {idx}/{total}").format(idx=idx, total=len(via_batches)), work)
            if ok and registry is not None:
                for cmd, uuid_str in pending:
                    registry.record_created(cmd, uuid_str)
            if not ok:
                failed_via_owners.extend(cmd.owner_ref for cmd in batch)
                logger.error(_("  via batch {idx} failed").format(idx=idx))
            else:
                logger.info(_("  via batch {idx} completed ({count} items)").format(idx=idx, count=len(batch)))

        return failed_via_owners, created_via_log