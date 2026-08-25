# kicadstamp/placement/executor/flip_manager.py
import logging
import time

from kicadstamp.kicad.adapter import KiCadBoardAdapter
from ..commands import MoveCommand
from .base import layer_to_str
from ...i18n import _

logger = logging.getLogger(__name__)

class FlipManager:
    def __init__(self, adapter: KiCadBoardAdapter, batch_size: int = 10):
        self.adapter = adapter
        self.batch_size = batch_size

    def flip_if_needed(self, moves: list[MoveCommand]) -> dict[str, object]:
        """Return ref->footprint dict after possibly flipping components."""
        all_fps = self.adapter.get_footprints()
        fp_by_ref = {fp.ref: fp for fp in all_fps}

        refs_to_flip = [m.ref for m in moves if self._needs_flip(m, fp_by_ref)]
        if refs_to_flip:
            logger.info(_("Flipping {count} components").format(count=len(refs_to_flip)))
            self._flip_in_batches(refs_to_flip, fp_by_ref)
            time.sleep(0.5)
            # Reload footprints after flip
            all_fps = self.adapter.get_footprints()
            fp_by_ref = {fp.ref: fp for fp in all_fps}
        return fp_by_ref

    def _needs_flip(self, cmd: MoveCommand, fp_by_ref: dict[str, object]) -> bool:
        fp = fp_by_ref.get(cmd.ref)
        if fp is None:
            return False
        return fp.layer != cmd.layer

    def _flip_in_batches(self, refs: list[str], fp_by_ref: dict[str, object]):
        for i in range(0, len(refs), self.batch_size):
            batch_refs = refs[i:i+self.batch_size]
            fps = [fp_by_ref[ref] for ref in batch_refs if ref in fp_by_ref]
            if fps:
                self.adapter.flip_selected(fps)
                logger.info(_("  flipped {count} items (batch {batch})")
                            .format(count=len(fps), batch=i//self.batch_size + 1))