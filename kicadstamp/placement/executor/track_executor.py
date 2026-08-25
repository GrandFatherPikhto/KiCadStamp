# kicadstamp/placement/executor/track_executor.py
import logging

from kicadstamp.kicad.adapter import KiCadBoardAdapter
from ...config import Config
from ..commands import TrackCommand
from ...registry import TrackRegistry
from ...utils.units import MM
from ...i18n import _

logger = logging.getLogger(__name__)

class TrackExecutor:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config, batch_size: int = 10):
        self.adapter = adapter
        self.cfg = config
        self.batch_size = batch_size

    def execute_tracks(self, tracks: list[TrackCommand],
                       registry: TrackRegistry | None = None) -> tuple[list[str], list[dict]]:
        failed_track_owners = []
        created_track_log = []

        track_batches = [tracks[i:i+self.batch_size] for i in range(0, len(tracks), self.batch_size)]
        logger.info(_("Creating tracks in {count} batches").format(count=len(track_batches)))
        for idx, batch in enumerate(track_batches, 1):
            # (cmd, uuid) pairs, recorded into the registry ONLY after the
            # commit actually succeeded (P0-3, 2026-08-25: record_created used
            # to run inside work(), i.e. before push_commit — a crash between
            # the JSON write and the board commit then left the registry lying).
            pending = []

            def work(batch=batch):
                new_tracks = []
                cmd_for_track = []
                for cmd in batch:
                    net = self.adapter.get_net_by_name(cmd.net_name)
                    if net is None:
                        logger.warning(_("  net {net} not found for track for {owner}")
                                       .format(net=cmd.net_name, owner=cmd.owner_ref))
                        continue
                    track = self.adapter.create_track(cmd.start, cmd.end, cmd.width_mm, net, cmd.layer)
                    new_tracks.append(track)
                    cmd_for_track.append(cmd)
                if new_tracks:
                    created = self.adapter.create_items(new_tracks)
                    for cmd, t in zip(cmd_for_track, created):
                        uuid_str = t.uuid
                        created_track_log.append({
                            'uuid': uuid_str,
                            'start_x_mm': t.start.x / MM,
                            'start_y_mm': t.start.y / MM,
                            'end_x_mm': t.end.x / MM,
                            'end_y_mm': t.end.y / MM,
                            'width_mm': t.width_mm,
                            'net_name': t.net_name,
                            'owner_ref': cmd.owner_ref
                        })
                        pending.append((cmd, uuid_str))
                    logger.debug(_("  created {count} tracks").format(count=len(created)))
            ok = self.adapter.commit_with_retry(_("Track batch {idx}/{total}").format(idx=idx, total=len(track_batches)), work)
            if ok and registry is not None:
                for cmd, uuid_str in pending:
                    registry.record_created(cmd, uuid_str)
            if not ok:
                failed_track_owners.extend(cmd.owner_ref for cmd in batch)
                logger.error(_("  track batch {idx} failed").format(idx=idx))
            else:
                logger.info(_("  track batch {idx} completed ({count} items)").format(idx=idx, count=len(batch)))

        return failed_track_owners, created_track_log