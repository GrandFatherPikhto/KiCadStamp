# gui/docks/rules.py
"""
BACKWARD-COMPAT SHIM (2026-09-01, plan rules_to_chains).

This module was the old RuleDock (a DetailDock page with a spokes TABLE). The
Chain form now lives in gui/docks/chain.py (ChainDock — chain/pad modes, hosted
in the non-modal ChainDialog, gui/docks/chain_dialog.py), and the pads are
leaves in the Config tree, not a table.

This file exists ONLY so that existing importers/tests referencing the old
module path (`from gui.docks.rules import RuleDock`) keep working during the
transition. It re-exports the new module's public names verbatim:
  - RuleDock == ChainDock (the chain/pad-mode form);
  - BulkSetCellDialog (unchanged);
  - _chain_identity / _rule_identity.
New code should import from gui.docks.chain directly. This shim is scheduled
to be deleted once the migration (tests/docs) is complete.
"""
from .chain import (BulkSetCellDialog, ChainDock, _chain_identity,
                    _rule_identity)

RuleDock = ChainDock

__all__ = ["RuleDock", "BulkSetCellDialog", "_chain_identity", "_rule_identity"]
