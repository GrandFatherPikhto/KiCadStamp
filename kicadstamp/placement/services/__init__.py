# kicadstamp/placement/services/__init__.py
"""
Services for position calculation, angle correction, relaxation and via planning.
"""

from .via_planner import ViaPlanner

# Only ViaPlanner lives in this package today. PositionCalculator /
# PowerPinOrienter / SpacingRelaxer are leftovers from the old monolithic
# services module and have no importable implementation — leaving them in
# __all__ made `from kicadstamp.placement.services import PositionCalculator`
# an ImportError. The real calculator today is ClonePositionCalculator in its
# own module.
__all__ = ["ViaPlanner"]