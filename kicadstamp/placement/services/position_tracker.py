# kicadstamp/placement/services/position_tracker.py

import logging


from ...domain.geometry import BoardLayer
from ...domain.geometry import Angle

from ...kicad.adapter import KiCadBoardAdapter
from ..commands import MoveCommand, PlacedComponentInfo
from ...constants import POSITION_TOLERANCE_NM, ANGLE_TOLERANCE_DEG
from ...i18n import _

logger = logging.getLogger(__name__)


class PositionTracker:
    """Checks whether a component is already at its target position and
    converts ``PlacedComponentInfo`` lists into ``MoveCommand`` lists,
    skipping components that are already in place.

    Both :class:`ManualPositionCalculator` and
    :class:`ClonePositionCalculator` produce ``PlacedComponentInfo``
    results; this class provides the shared "is it already there?" logic
    that was previously inlined in ``planner.py`` (``_already_in_place`` /
    ``moves_from_placed``).
    """

    def __init__(self, adapter: KiCadBoardAdapter, target_layer: BoardLayer,
                 skip_existing: bool = False):
        self.adapter = adapter
        self.target_layer = target_layer
        self.skip_existing = skip_existing

    def already_in_place(self, ref: str, dest, angle_deg: float,
                         layer: BoardLayer) -> bool:
        """Return True if *ref* is already at (*dest*, *angle_deg*, *layer*)
        within tolerance."""
        fp = self.adapter.get_footprint(ref)
        if fp is None:
            return False
        if fp.layer != layer:
            return False
        if abs(fp.position.x - dest.x) > POSITION_TOLERANCE_NM:
            return False
        if abs(fp.position.y - dest.y) > POSITION_TOLERANCE_NM:
            return False
        angle_diff = abs((fp.angle_deg - angle_deg + 180) % 360 - 180)
        return angle_diff <= ANGLE_TOLERANCE_DEG

    def moves_from_placed(self,
                          placed: list[PlacedComponentInfo]
                          ) -> list[MoveCommand]:
        """Convert a list of placement results to move commands, skipping
        components whose current board position matches the target."""
        moves: list[MoveCommand] = []
        skipped = 0
        for info in placed:
            layer = info.layer if info.layer is not None else self.target_layer
            if (self.skip_existing
                    and self.already_in_place(info.ref, info.dest,
                                              info.angle_deg, layer)):
                skipped += 1
                logger.debug(
                    _("  {ref}: already in place, move skipped"
                      " (skip_existing_components)")
                    .format(ref=info.ref))
                continue
            moves.append(MoveCommand(
                ref=info.ref,
                position=info.dest,
                angle=Angle.from_degrees(info.angle_deg),
                layer=layer,
            ))
        if skipped:
            logger.info(
                _("Skipped {count} components already at target position")
                .format(count=skipped))
        return moves
