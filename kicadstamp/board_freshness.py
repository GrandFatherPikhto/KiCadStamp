# kicadstamp/board_freshness.py
"""Freshness of the board behind a live adapter — the ONE seam for it.

The helper below rebuilds the board behind an adapter so the read that follows
is LIVE instead of whatever the adapter's cache happens to hold. It lived in
``gui/worker.py`` until the MCP server turned out to need the very same
medicine (plan_2026_09_24_mcp_stale_board §5): an MCP process that stays up for
hours served a board read from its connect-time cache, measured live with the
project's own tool, and the fix is the same one the GUI worker bodies already
used.

It lives HERE, and not in ``gui/worker.py``, because the two consumers have
nothing to share but the rule: the MCP server is an OPTIONAL dependency
(``docs/mcp.md``) and must not pull PyQt6 into its process, so importing
``gui.*`` from ``mcp_server/`` is forbidden by canon. ``gui/worker.py``
therefore RE-EXPORTS this name rather than defining it, which keeps its four
call sites and the mutation harness that matches their text byte for byte.

This module imports NOTHING — no kipy, no Qt, no project internals. It is a
rule about an adapter-shaped object, stated once.
"""
from typing import Any


def refresh_board_before_live_read(adapter: Any) -> bool:
    """Rebuild the board behind `adapter` so the read that follows is LIVE.

    The adapter caches the footprint list (`_footprints_cache`) and invalidates
    it ONLY in `refresh_board()`, so a capture that reads `get_footprints()`
    without refreshing first records whatever the cache happens to hold. Measured
    on the test board (Ш1 of plan_2026_09_22_imprint_stale_cache_sh2): the warm
    read returned the pre-move positions EXACTLY — max |Δ| = 0.0000 mm against
    the state at the last refresh, 1.8063 mm against the live board on 11 refs —
    and it cost ZERO board reads, which is why counting ADAPTER calls cannot see
    this defect (the count says "6 adapter calls" while the board was read once,
    in the other column).

    The price of the call is the other side of the same coin, and it is not
    free: `refresh_board()` is `get_board()` PLUS a forced cache miss on the next
    `get_footprints()`, i.e. a full board read — measured class 166/186 ms on the
    325-footprint board. It is paid once per capture, which is what "live read"
    means; a caller that cannot afford it must not be reading a live position at
    all.

    Call it at the TOP of a worker body (as the first statement of its `try`),
    never from the UI thread. The caller already owns the shared kipy REQ socket
    for the whole operation — `LongOpController.start()` set
    `connection.long_op_active = True` BEFORE the thread started — so there is
    nothing to refuse here. A `socket_busy(connection)` check in a worker body
    would therefore be wrong twice over: inside a worker it is ALWAYS True
    (plan_2026_09_22_imprint_stale_cache_sh2 §2.3), while the synchronous test
    hooks (`ImprintFormWidget._do_reread` / `_do_reread_apply`) call the same
    bodies WITHOUT `start_long_op`, where the flag is not raised — the check
    would read False in the tests and True in production, i.e. report the
    OPPOSITE of what ships. The "one owner of the socket" property is justified
    by this comment instead, which is what the plan asks for.

    TWO CONSUMERS, ONE REMEDY (added when it moved here, plan_2026_09_24_mcp_
    stale_board §5): the four GUI worker bodies above and the MCP server's call
    seam (`mcp_server.connection.ConnectionManager.execute`) call this function,
    and it is ONE remedy applied in two places, not two look-alike helpers. Each
    consumer's own call site keeps its own justification: in the GUI the
    "one owner of the socket" is `start_long_op`'s flag raised before the worker
    thread starts, while on the MCP seam the owner is the manager's serialising
    lock, which every tool call passes through (a kipy REQ socket is not
    thread-safe, so the lock IS the single-owner guarantee there).

    Tolerant on purpose, so a path with no live board keeps working unchanged:
      * `adapter=None` — a payload built without a board, which several existing
        GUI tests build — is a no-op, not a crash;
      * an adapter without `refresh_board` (the docks' smaller test doubles) is a
        no-op too: there is nothing to invalidate.
    Returns True when a refresh actually happened, so a caller or a test can
    assert that the seam RAN instead of inferring it from a later side effect.
    """
    refresh = getattr(adapter, "refresh_board", None)
    if not callable(refresh):
        return False
    refresh()
    return True
