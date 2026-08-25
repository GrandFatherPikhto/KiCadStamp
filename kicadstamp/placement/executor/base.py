# kicadstamp/placement/executor/base.py
"""layer_to_str moved to kicadstamp/utils/layers.py (single source of truth).
This module remains as a thin re-export so the executor's existing importers
(move_executor.py, flip_manager.py) and tests keep working unchanged."""
from ...utils.layers import layer_to_str
