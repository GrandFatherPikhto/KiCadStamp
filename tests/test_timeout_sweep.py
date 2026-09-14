# tests/test_timeout_sweep.py
"""One answer to "how long do we wait for KiCad" — watchdogs (Э5 of
plan_2026_09_13_timeout_sweep).

``DEFAULT_TIMEOUT_MS`` (kicadstamp/constants.py) is the single default, picked
from live measurements on 2026-09-13; the GUI's Settings knob writes the
effective value into ``BoardConnection.timeout_ms``. Before the sweep THIRTEEN
places carried a number of their own, so ``apply`` waited 5 s while ``extract``
waited 20, and the docks' worker adapters ignored the Settings knob entirely.

Three guards live here:

  1. the literal is GONE from the shipping tree (``kicadstamp/`` minus
     ``diagnostics/``, ``gui/``, ``mcp_server/``);
  2. every default EQUALS the constant — never a hard-coded 5000: a test that
     must be edited whenever the default moves is a test that will one day be
     edited without thinking;
  3. an EXPLICIT timeout still wins — the counter-guard against "swept so hard
     that nothing is configurable any more".

``kicadstamp/diagnostics/`` used to be excluded from the scan wholesale. Since
Х6 (plan_2026_09_13_three_unsentinelled_guards) it is scanned like everything
else, with FIVE named exceptions — the probes that keep a long timeout ON
PURPOSE, each with a comment saying why (Э4). The sixth, ``get_pad_bbox.py``,
only ever had the old global default there (and then a comment claiming it was
deliberate), so it now uses ``DEFAULT_TIMEOUT_MS``.

Mutation checks run by hand when this landed (the hand-off report has the
output): putting one literal back in gui/docks/trees_dock.py turns guard 1 red;
restoring extract's literal default turns guard 2 red; dropping the explicit
argument path turns guard 3 red.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from kicadstamp.constants import DEFAULT_TIMEOUT_MS

REPO_ROOT = Path(__file__).resolve().parent.parent

# The shipping packages the sweep covers (Э5.1).
_SCANNED_PACKAGES = ("kicadstamp", "gui", "mcp_server")
# The literal in every shape it used to be written in (20000 / 20_000).
_LITERAL = re.compile(r"\b20_?000\b")

# Diagnostics probes that keep a 20 s literal ON PURPOSE (Э4): each runs a HEAVY
# batch read, where a ceiling sized for the GUI would fake timeouts into the
# very numbers the probe exists to report. They are excluded BY NAME, each with
# its reason — anything else under kicadstamp/diagnostics/ is scanned like the
# shipping tree. Х6 (plan_2026_09_13_three_unsentinelled_guards) closed the
# blanket exclusion that used to hide get_pad_bbox.py, whose 20 s was simply the
# old default. `test_the_diagnostic_exception_list_has_no_stale_entries` keeps
# this list from rotting.
_DIAGNOSTIC_LITERAL_EXCEPTIONS = {
    "probe_placement_cost.py":
        "times a full placement Phase 1 on a grown board",
    "diagnostic_charset.py":
        "scans every field of every footprint on the whole board",
    "probe_inter_cluster_nets_gnd.py":
        "reads the whole selection's copper plus the board's item lists",
    "probe_schematic_ipc_api.py":
        "calls undocumented SCH-layer IPC methods of unknown latency",
    "probe_field_map_unit_cost.py":
        "sweeps every footprint --iter times to build medians",
}


def _diagnostics_dir() -> Path:
    return REPO_ROOT / "kicadstamp" / "diagnostics"


def _scanned_sources():
    for package in _SCANNED_PACKAGES:
        for path in sorted((REPO_ROOT / package).rglob("*.py")):
            if (path.parent == _diagnostics_dir()
                    and path.name in _DIAGNOSTIC_LITERAL_EXCEPTIONS):
                continue
            yield path


class TestNoStrayLiterals:
    def test_the_shipping_tree_owns_one_default_only(self):
        offenders = []
        for path in _scanned_sources():
            for number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1):
                if _LITERAL.search(line):
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}")
        assert not offenders, (
            "A 20 s IPC timeout literal is back. The default lives in "
            "kicadstamp/constants.py (DEFAULT_TIMEOUT_MS): use the constant, or "
            "carry the connection's own value into the worker payload (Э3):\n"
            + "\n".join(offenders))

    def test_the_scan_actually_walks_the_tree(self):
        """Antidote to a vacuous pass: a moved/renamed package would otherwise
        make the scan above match nothing and stay green forever."""
        sources = list(_scanned_sources())
        assert len(sources) > 100
        assert any(p.name == "cli_main.py" for p in sources)

    def test_get_pad_bbox_is_scanned_like_the_shipping_tree(self):
        """Х6 (plan_2026_09_13_three_unsentinelled_guards): the probe whose
        20 s was only the old global default stopped hiding behind the blanket
        diagnostics exclusion — it is scanned now, so putting a literal back
        there turns the guard above red. The five named exceptions must still
        be skipped, or their long budget would be scanned as a mistake."""
        scanned = {p.name for p in _scanned_sources()}
        assert "get_pad_bbox.py" in scanned
        assert "probe_placement_cost.py" not in scanned

    def test_the_diagnostic_exception_list_has_no_stale_entries(self):
        """Anti-rot for the named exceptions: a probe that was renamed away, or
        that no longer carries the literal, must not keep pre-approving a
        future 20 s literal silently — and every entry must state a reason."""
        for name, reason in _DIAGNOSTIC_LITERAL_EXCEPTIONS.items():
            assert reason.strip(), f"exception without a reason: {name}"
            path = _diagnostics_dir() / name
            assert path.exists(), f"exception names a missing probe: {name}"
            literal_lines = [
                line for line in path.read_text(encoding="utf-8").splitlines()
                if _LITERAL.search(line)]
            assert literal_lines, (
                f"{name} no longer carries a 20 s literal — drop its exception "
                "from _DIAGNOSTIC_LITERAL_EXCEPTIONS so the file is scanned "
                "again")


def _capture_cli_namespace(monkeypatch, argv):
    """Run the REAL entry point (kicadstamp.cli_main.main) with *argv* and hand
    back the argparse Namespace its dispatch received — no parser is rebuilt
    here, so what is asserted is what a user actually gets."""
    import kicadstamp.cli_main as cli_main

    seen = {}

    def _recorder(args):
        seen["args"] = args

    monkeypatch.setattr(cli_main, "cmd_extract", _recorder)
    monkeypatch.setattr(cli_main, "cmd_extract_net", _recorder)
    monkeypatch.setattr(sys, "argv", ["kicadstamp_cli.py", *argv])

    assert cli_main.main() == 0
    return seen["args"]


class TestCliDefaultsFollowTheConstant:
    def test_extract(self, monkeypatch):
        args = _capture_cli_namespace(monkeypatch, ["extract", "--output", "cell.sexp"])
        assert args.timeout_ms == DEFAULT_TIMEOUT_MS

    def test_extract_net(self, monkeypatch):
        args = _capture_cli_namespace(
            monkeypatch, ["extract-net", "--net", "N", "--anchor-role", "R",
                          "--output", "nt.sexp"])
        assert args.timeout_ms == DEFAULT_TIMEOUT_MS

    def test_apply(self, monkeypatch):
        """`apply` already used the constant BEFORE the sweep — pinned so the
        sweep cannot regress it while fixing its neighbours."""
        import kicadstamp.cli_main as cli_main

        seen = {}
        monkeypatch.setattr(cli_main, "cmd_apply",
                            lambda args: seen.update(args=args))
        monkeypatch.setattr(sys, "argv",
                            ["kicadstamp_cli.py", "apply", "root.sexp"])

        assert cli_main.main() == 0
        assert seen["args"].timeout_ms == DEFAULT_TIMEOUT_MS

    def test_an_explicit_flag_still_wins(self, monkeypatch):
        args = _capture_cli_namespace(
            monkeypatch, ["extract", "--timeout-ms", "1234", "--output", "c.sexp"])
        assert args.timeout_ms == 1234


def _manager_adapter_timeout(manager_cls, **kwargs):
    """The timeout ConnectionManager really hands to its adapter factory."""
    seen = []

    class _FakeAdapter:
        def __init__(self, timeout_ms):
            self.timeout_ms = timeout_ms

        def get_board_filename(self):
            return "board.kicad_pcb"

        def refresh_board(self):
            pass

    manager = manager_cls(
        adapter_factory=lambda timeout_ms: (
            seen.append(timeout_ms) or _FakeAdapter(timeout_ms)),
        **kwargs)
    manager.execute(lambda adapter: adapter.timeout_ms)
    return seen[-1]


class TestLibraryDefaultsFollowTheConstant:
    def test_apply_pipeline_keyword_default(self):
        import inspect

        from kicadstamp.apply_pipeline import ApplyPipeline

        default = inspect.signature(ApplyPipeline.__init__).parameters[
            "timeout_ms"].default
        assert default == DEFAULT_TIMEOUT_MS

    def test_run_options_field_default(self):
        from kicadstamp.apply_pipeline import RunOptions

        assert RunOptions.__dataclass_fields__["timeout_ms"].default == \
            DEFAULT_TIMEOUT_MS

    def test_connection_manager_default(self):
        from mcp_server.connection import ConnectionManager

        assert _manager_adapter_timeout(ConnectionManager) == DEFAULT_TIMEOUT_MS


class TestExplicitTimeoutStillWins:
    """The counter-guard (Э5.3): a number passed in by hand must survive the
    sweep, or "one default" turns into "no choice"."""

    def test_apply_pipeline_constructor(self):
        from kicadstamp.apply_pipeline import ApplyPipeline

        assert ApplyPipeline("root.sexp", timeout_ms=1234).timeout_ms == 1234

    def test_run_options_constructor(self):
        from kicadstamp.apply_pipeline import RunOptions

        assert RunOptions(config_path="root.sexp", timeout_ms=1234).timeout_ms == 1234

    def test_connection_manager_constructor(self):
        from mcp_server.connection import ConnectionManager

        assert _manager_adapter_timeout(ConnectionManager, timeout_ms=1234) == 1234


class TestWorkerTimeoutResolution:
    """``gui.connection.worker_timeout_ms`` — the ONE place a worker's own
    adapter gets its number from (Э3). The caller side hands it a live
    connection, the worker side the payload it was given."""

    def test_reads_the_live_connection(self):
        from gui.connection import worker_timeout_ms

        class _Connection:
            timeout_ms = 12_345

        assert worker_timeout_ms(_Connection()) == 12_345

    def test_reads_the_value_carried_in_a_payload(self):
        from gui.connection import worker_timeout_ms

        assert worker_timeout_ms({"timeout_ms": 12_345}) == 12_345

    @pytest.mark.parametrize("source", [
        None,
        {},
        {"timeout_ms": None},
        {"timeout_ms": 0},
        {"timeout_ms": -1},
        {"timeout_ms": True},
        {"timeout_ms": "5000"},
        # A test double without the attribute, or a caller predating Э3.
        type("_NoAttributeAtAll", (), {})(),
    ])
    def test_anything_unusable_falls_back_to_the_constant(self, source):
        from gui.connection import worker_timeout_ms

        assert worker_timeout_ms(source) == DEFAULT_TIMEOUT_MS
