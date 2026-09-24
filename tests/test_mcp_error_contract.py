# tests/test_mcp_error_contract.py
"""Cells Э1-Э6 of plan_2026_09_25_mcp_error_contract - the MCP error contract.

The defect these cells pin (measured cold, no live KiCad): the reconnect tuple
in ``mcp_server/connection.py`` was built from the BUILT-IN ``ConnectionError``
while production raises kipy's own, and kipy's ``ApiError`` is neither
``PlacerError`` nor ``ValueError`` - so neither ``_reconnectable_errors()`` nor
``_tool_error()`` ever caught a real IPC failure. Every tool answered from a
CLOSED KiCad with mcp's "unexpected crash" instead of a sentence.

Why these cells go through the real server (``build_server`` + ``call_tool``)
rather than only the wrapper: the client-visible contract is made by TWO layers.
The SDK's ``Tool.run`` reports a deliberate ``ToolError`` as
``ToolError("Error executing tool <name>: <our text>")``, and ANY other
exception as ``UnexpectedToolError``, whose message is only
``Error executing tool <name>`` and which is logged WITH a traceback. So "the
cell is red" means ``UnexpectedToolError``, and "green" means a plain
``ToolError`` carrying our text - the difference the whole entry exists for.

Numbering belongs to the PLAN (rule 37): the cell number lives in the docstring
next to the plan name, never in the test function's name - several plans in this
project own an "Э1..". The pre-existing cell
``tests/test_mcp_connection.py::test_reconnects_once_after_connection_error``
(built-in ``ConnectionError``) is deliberately NOT touched (rule 33): it pins
the other class, and Э1 here pins the kipy one - both classes must be caught.

Expected colour on the base commit, before the production fix, named in the plan
so that an unexpected colour is a finding rather than a reason to bend the cell
(rule 39):

  * RED   - Э1 (kipy ConnectionError row), Э2 (both rows), Э3, Э5 (second row);
  * GREEN - Э4, Э6 (hygiene cells, held by mutations m5 and m7).

Every cell here is built on fakes: no live KiCad, no IPC socket (plan §9).
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from kicadstamp.cli_common import api_error_message
from kipy.errors import ApiError, ApiStatusCode
from kipy.errors import ConnectionError as KipyConnectionError

from mcp_server.connection import ConnectionManager
from mcp_server.server import build_server

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolate_gui_settings(tmp_path, monkeypatch):
    """Point the raw-write gate at a throwaway gui_state.json - the same
    discipline as tests/test_mcp_server.py, so no cell reads (or writes) the
    developer's real one."""
    from gui import settings as gui_settings

    monkeypatch.setattr(gui_settings, "SETTINGS_PATH", tmp_path / "gui_state.json")


class _FakeAdapter:
    """Minimal adapter stand-in with ONE injectable failure.

    ``fail_on`` names the method that raises ``error`` (None = healthy). The
    names are the ones the production callers use, so a cell can put the failure
    exactly where production puts it:

      * ``refresh_board``  - the seam's own board read. On the FIRST call it
        happens inside ``_ensure_open``, i.e. OUTSIDE ``execute``'s try; on every
        later call it is INSIDE it. This is §3.3's real path: with no PCB
        document open ``kipy.kicad.get_board()`` raises ``ApiError`` ("Expected
        to be able to retrieve at least one board"), so the adapter's
        ``if self._board is None: raise BoardNotFoundError`` branch is dead code
        and the promise in ``_ensure_open``'s docstring is unreachable;
      * ``get_footprints`` - the handler's own read, i.e. an ``ApiError`` from a
        busy KiCad or a kipy ``ConnectionError`` from a link that died mid-call,
        both INSIDE the try;
      * ``ping``           - the body the seam-only connection cells call.
    """

    def __init__(self, *, name="fake.kicad_pcb", version="10.0.6", footprints=(),
                 fail_on=None, error=None):
        self._name = name
        self._version = version
        self._footprints = list(footprints)
        self._fail_on = fail_on
        self._error = error
        self.refresh_count = 0
        self.close_count = 0

    def _raise_if_failing(self, method):
        if self._fail_on == method:
            raise self._error

    def refresh_board(self):
        self.refresh_count += 1
        self._raise_if_failing("refresh_board")

    def get_board_filename(self):
        self._raise_if_failing("get_board_filename")
        return self._name

    def get_version(self):
        self._raise_if_failing("get_version")
        return self._version

    def get_footprints(self):
        self._raise_if_failing("get_footprints")
        return list(self._footprints)

    def ping(self):
        self._raise_if_failing("ping")
        return "pong"

    def close(self):
        self.close_count += 1


def _run_tool(server, name, arguments=None):
    """Call a tool the way an MCP client does, SDK wrapper included.

    Exceptions are expected to escape: that is the point of the cells below.
    """
    asyncio.run(server.call_tool(name, arguments or {}))


def _clean_subprocess(code: str) -> str:
    """Run ``code`` in a fresh interpreter and return its stdout, stripped.

    A subprocess is the point of Э5/Э6: importing kipy in THIS process (by
    test_mcp_server/test_mcp_handlers, or by the Э5 cells themselves) would mask
    exactly what is being measured.
    """
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


# --- Э1: the reconnect tuple must hold the classes production raises ---------

@pytest.mark.parametrize(
    "error_cls",
    [ConnectionError, KipyConnectionError],
    ids=["builtin-ConnectionError", "kipy-ConnectionError"],
)
def test_execute_reconnects_once_after_a_connection_error(error_cls):
    """Cell Э1 of plan_2026_09_25_mcp_error_contract: ``execute`` reconnects when
    the call failed with a connection-level error.

    Both classes are rows of this ONE table (rule 35), because the fix must catch
    both and a row naming two branches is a promise that both were checked: kipy
    raises its own ``ConnectionError`` on every IPC break, while the BUILT-IN one
    is raised by Python itself on OS-level socket errors - losing it would be a
    regression in the other direction. The built-in row is the pre-existing
    cell's own class, restated here; that cell stays in
    tests/test_mcp_connection.py untouched (rule 33).
    """
    created = []

    def factory(timeout_ms):
        # Only the FIRST adapter is flaky: a long-lived link drops once, then the
        # reconnect works - the historical cell's shape.
        fail_on = "ping" if not created else None
        adapter = _FakeAdapter(fail_on=fail_on, error=error_cls("link dropped"))
        created.append(adapter)
        return adapter

    manager = ConnectionManager(timeout_ms=12345, adapter_factory=factory)
    assert manager.execute(lambda a: a.ping()) == "pong"
    assert len(created) == 2
    assert created[0].close_count == 1  # the dead adapter was torn down once


# --- Э2: an ApiError is a sentence, not a crash -----------------------------

@pytest.mark.parametrize("fail_on", ["refresh_board", "get_footprints"])
def test_api_error_reaches_the_client_as_a_clear_tool_error(fail_on):
    """Cell Э2 of plan_2026_09_25_mcp_error_contract: an ``ApiError`` (kipy's
    "we talked to KiCad and it refused") arrives as a deliberate ``ToolError``
    carrying ``api_error_message``'s text - not as the SDK's crash wrapper.

    Two rows, because production raises it in two places and one medicine must
    cover both (rule 35): the seam's refresh (no PCB document open, §3.3) and the
    handler's read (a busy KiCad). Both were "unexpected crash" before the fix.
    """
    error = ApiError("KiCad returned error", code=ApiStatusCode.AS_TIMEOUT)
    server = build_server(adapter_factory=lambda ms: _FakeAdapter(fail_on=fail_on, error=error))

    with pytest.raises(ToolError) as excinfo:
        _run_tool(server, "kicadstamp_list_footprints")

    # UnexpectedToolError IS a ToolError, so the type alone would be green on the
    # base commit: the crash wrapper is what the client actually saw, and it is
    # logged with a traceback.
    assert not isinstance(excinfo.value, UnexpectedToolError)
    assert api_error_message(error) in str(excinfo.value)


def test_as_busy_tool_error_carries_the_unfinished_tool_explanation():
    """Cell Э2 of plan_2026_09_25_mcp_error_contract: the ``AS_BUSY`` half.

    Without this row the fix passes formally while losing the most valuable thing
    it carries: the "an unfinished tool is running in the GUI - finish it" sentence
    (the most common real-world cause, and the one easiest to misread as a hang).
    A generic "KiCad returned API error" would still be words instead of a crash -
    and still be a worse answer than the one the project already wrote.
    """
    error = ApiError("KiCad is busy and cannot respond to API requests right now",
                     code=ApiStatusCode.AS_BUSY)
    server = build_server(adapter_factory=lambda ms: _FakeAdapter(
        fail_on="get_footprints", error=error))

    with pytest.raises(ToolError) as excinfo:
        _run_tool(server, "kicadstamp_list_footprints")

    assert not isinstance(excinfo.value, UnexpectedToolError)
    text = str(excinfo.value)
    assert api_error_message(error) in text
    assert "unfinished" in text      # the long explanation, not the one-liner
    assert "not modified" in text    # ... and its verdict


# --- Э3: KiCad closed is a sentence too -------------------------------------

def test_kicad_unavailable_at_the_first_call_is_a_clear_error():
    """Cell Э3 of plan_2026_09_25_mcp_error_contract: KiCad is not running AT ALL
    and the FIRST call fails - the adapter cannot even be built (``_ensure_open``
    -> factory -> kipy's client raises inside ``kipy.KiCad()``).

    This is the most frequent real scenario ("KiCad was closed"), and the whole
    entry grew out of it.
    """
    attempts = []

    def factory(timeout_ms):
        attempts.append(timeout_ms)
        raise KipyConnectionError("Failed to connect to KiCad: Connection refused")

    server = build_server(adapter_factory=factory)

    with pytest.raises(ToolError) as excinfo:
        _run_tool(server, "kicadstamp_list_footprints")

    assert not isinstance(excinfo.value, UnexpectedToolError)
    text = str(excinfo.value)
    assert "KiCad" in text
    # kipy's own words survive as the DETAIL, not as the whole answer: it names
    # what failed but not what to do about it.
    assert "Failed to connect to KiCad: Connection refused" in text
    assert len(attempts) == 1  # nothing was retried: there was nothing to reconnect to


def test_kicad_unavailable_during_the_reconnect_is_a_clear_error():
    """Cell Э3 of plan_2026_09_25_mcp_error_contract: the half §6.3 calls easy to
    miss, and the reason fixing the tuple alone is worthless.

    The link dies mid-call, the seam tears the adapter down, and the RECONNECT is
    what fails (KiCad was closed in between). Fixing only the tuple moves the
    crash one step down this path instead of removing it: the second failure is
    raised from ``_ensure_open()`` INSIDE the ``except`` handler, so it leaves
    ``execute`` no matter what the tuple holds.
    """
    created = []
    attempts = []

    def factory(timeout_ms):
        attempts.append(timeout_ms)
        if len(attempts) == 1:
            adapter = _FakeAdapter(
                fail_on="get_footprints",
                error=KipyConnectionError("Error receiving reply from KiCad: link dropped"))
            created.append(adapter)
            return adapter
        raise KipyConnectionError("Failed to connect to KiCad: Connection refused")

    server = build_server(adapter_factory=factory)

    with pytest.raises(ToolError) as excinfo:
        _run_tool(server, "kicadstamp_list_footprints")

    assert not isinstance(excinfo.value, UnexpectedToolError)
    assert "Failed to connect to KiCad: Connection refused" in str(excinfo.value)
    assert len(attempts) == 2          # the reconnect WAS attempted (the fix's first half)
    assert created[0].close_count == 1  # and the dead adapter was torn down once


# --- Э4: the boundary did not move ------------------------------------------

def test_a_plain_runtime_error_still_reaches_the_client_as_a_crash():
    """Cell Э4 of plan_2026_09_25_mcp_error_contract: the ``_tool_error`` contract
    was NOT widened.

    A ``RuntimeError`` is a bug: it must stay distinguishable from a deliberate
    fatal, so the client keeps getting the SDK's crash wrapper (logged WITH a
    traceback, original text withheld - that is what makes a bug visible in the
    server log instead of being presented to the model as a user-facing sentence).
    Without this row, step 2 is one ``except Exception`` away from erasing the
    "bug / deliberate fatal" boundary the wrapper's own docstring is built on.
    """
    boom = RuntimeError("boom")
    server = build_server(adapter_factory=lambda ms: _FakeAdapter(
        fail_on="get_footprints", error=boom))

    with pytest.raises(UnexpectedToolError) as excinfo:
        _run_tool(server, "kicadstamp_list_footprints")

    assert excinfo.value.__cause__ is boom


# --- Э5/Э6: properties of the lazy import, measured on the property ----------

def test_importing_the_seam_does_not_pull_kipy():
    """Cell Э5 (first row) of plan_2026_09_25_mcp_error_contract: importing
    ``mcp_server.connection`` must not import kipy.

    Measured on the PROPERTY, in a process that never imported kipy: a naive
    top-level ``import kipy`` breaks it SILENTLY, because kipy is installed here
    and no other test in the suite would notice. What is protected is smaller
    than "MCP is optional" - kicad-python is a hard dependency (pyproject.toml) -
    and it is real: the price of the import chain, and the ability to drive the
    manager with a fake without kipy in the process at all.
    """
    code = "import sys; import mcp_server.connection; print('kipy' in sys.modules)"
    assert _clean_subprocess(code) == "False"


def test_the_reconnectable_tuple_holds_the_real_kipy_class():
    """Cell Э5 (second row) of plan_2026_09_25_mcp_error_contract: laziness WORKS
    - the kipy class really is in the tuple after the lazy path runs - and the
    BUILT-IN ``ConnectionError`` is still there beside it (§6.1: do not remove
    it).

    The probe deliberately does not import kipy before the call, and recognises
    the classes by ``__module__``/``__name__`` instead: a probe that imports kipy
    in order to look for kipy would answer its own question. The first row of the
    cell (``kipy`` absent at import time) is measured by the cell above; this is
    the "and it does arrive, lazily, when needed" half.
    """
    code = (
        "import sys\n"
        "import mcp_server.connection as seam\n"
        "errors = seam._reconnectable_errors()\n"
        "print('kipy' in sys.modules)\n"
        "print(','.join(sorted(t.__module__ + '.' + t.__name__ for t in errors)))\n"
    )
    imported, names = _clean_subprocess(code).splitlines()
    assert imported == "True"
    assert "kipy.errors.ConnectionError" in names
    assert "builtins.ConnectionError" in names


def test_the_lazy_import_happens_at_most_once():
    """Cell Э6 of plan_2026_09_25_mcp_error_contract: the tuple's lazy imports are
    cached - counted as IMPORTS, not as tuple identity.

    On the base commit ``_reconnectable_errors()`` returns a new tuple on every
    call while caching only the class (measured: ``f() is f()`` -> False), so a
    cell on identity would be red by its own wording rather than by the defect,
    and would force a pointless change to production code. Counting is the
    property that matters: the second call must pay nothing.
    """
    code = (
        "import builtins\n"
        "import mcp_server.connection as seam\n"
        "seen = []\n"
        "real = builtins.__import__\n"
        "def counting(name, *args, **kwargs):\n"
        "    if name.startswith('kipy') or name.startswith('kicadstamp.exceptions'):\n"
        "        seen.append(name)\n"
        "    return real(name, *args, **kwargs)\n"
        "builtins.__import__ = counting\n"
        "seam._reconnectable_errors()\n"
        "first = len(seen)\n"
        "seam._reconnectable_errors()\n"
        "print(first)\n"
        "print(len(seen) - first)\n"
    )
    first, second = (int(line) for line in _clean_subprocess(code).splitlines())
    assert first >= 1   # the first call does the lazy imports
    assert second == 0  # the cached second call does none
