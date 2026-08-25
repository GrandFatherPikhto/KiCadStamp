# kicadstamp/placement/executor/batch_executor.py
import logging

from kicadstamp.kicad.adapter import KiCadBoardAdapter
from ...config import Config
from ..commands import MoveCommand, ViaCommand, TrackCommand
from .move_executor import MoveExecutor
from .via_executor import ViaExecutor
from .track_executor import TrackExecutor
from .operation_logger import OperationLogger
from ...registry import PlacementRegistry, TrackRegistry
from ...constants import DEFAULT_BATCH_SIZE, DEFAULT_LOG_DIR
from ...i18n import _

logger = logging.getLogger(__name__)

class BatchExecutor:
    def __init__(self, adapter: KiCadBoardAdapter, config: Config,
                 batch_size: int = DEFAULT_BATCH_SIZE,
                 operation_log_dir: str | None = None):
        self.adapter = adapter
        self.cfg = config
        self.batch_size = batch_size
        self.move_executor = MoveExecutor(adapter, config, batch_size)
        self.via_executor = ViaExecutor(adapter, config, batch_size)
        self.track_executor = TrackExecutor(adapter, config, batch_size)
        # operation_log_dir is the RESOLVED absolute directory (P1-3: the
        # resolved value now lives on RuntimeContext, not Config); None falls
        # back to DEFAULT_LOG_DIR exactly as before.
        self.logger = OperationLogger(operation_log_dir or DEFAULT_LOG_DIR)
        self._pending_move_log = []
        self._pending_via_log = []

    def execute_moves(self, moves: list[MoveCommand],
                       check_collisions: bool = True,
                       collision_margin_mm: float = 0.2) -> list[str]:
        failed_refs, move_log = self.move_executor.execute_moves(moves, check_collisions, collision_margin_mm)
        # extend, not overwrite: cmd_apply's per-item loop calls execute_moves
        # once per dependency-order item, all before the single execute_tracks()
        # flush at the end (see dependency_order.py) — a plain assignment here
        # would lose every item's move log except the last.
        self._pending_move_log.extend(move_log)
        return failed_refs

    def execute_vias(self, vias: list[ViaCommand], registry: PlacementRegistry | None = None) -> list[str]:
        failed_via_owners, via_log = self.via_executor.execute_vias(vias, registry)
        self._pending_via_log = via_log
        return failed_via_owners

    def execute_tracks(self, tracks: list[TrackCommand], registry: TrackRegistry | None = None) -> list[str]:
        failed_track_owners, track_log = self.track_executor.execute_tracks(tracks, registry)
        if self._pending_move_log or self._pending_via_log or track_log:
            self.logger.write_operation_log(self._pending_move_log, self._pending_via_log, track_log)
        self._pending_move_log = []
        self._pending_via_log = []
        return failed_track_owners

    def execute(self, moves: list[MoveCommand], vias: list[ViaCommand],
                tracks: list[TrackCommand] | None = None,
                check_collisions: bool = True,
                collision_margin_mm: float = 0.2) -> tuple[list[str], list[str], list[str]]:
        failed_refs = self.execute_moves(moves, check_collisions, collision_margin_mm)
        failed_vias = self.execute_vias(vias)
        failed_tracks = self.execute_tracks(tracks or [])
        return failed_refs, failed_vias, failed_tracks