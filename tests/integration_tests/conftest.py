# tests/integration_tests/conftest.py

import pytest
from kipy.geometry import Vector2
from kipy.board_types import BoardLayer

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from kicadstamp.kicad.adapter import KiCadBoardAdapter
from kicadstamp.config import load_config
from kicadstamp.utils.units import MM
from kicadstamp.placement.commands import ViaCommand
from kicadstamp.registry import PlacementRegistry, registry_path_for_config

TEST_BOARD_PATH = Path("test_boards/10CL006YE144C8G.kicad_pcb")
TEST_CONFIG_PATH = Path("kicadstamp_templates_example.yaml")


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: marks tests that require a running KiCad instance and a PCB board.")


@pytest.fixture(scope="session")
def adapter():
    """One adapter for the entire test session."""
    adapter = KiCadBoardAdapter(timeout_ms=30000)
    adapter.refresh_board()
    return adapter


@pytest.fixture(scope="session")
def board(adapter):
    """Board from the adapter."""
    return adapter._board


@pytest.fixture(scope="session")
def test_config():
    """Loads the test config."""
    cfg, _ = load_config(str(TEST_CONFIG_PATH))
    return cfg


@pytest.fixture(scope="function")
def test_component_ref():
    """Refdes of a component for tests (must exist on the board)."""
    return "C5"


@pytest.fixture(scope="function")
def test_pad_number():
    """Pad number for tests."""
    return "17"


@pytest.fixture(scope="function")
def temp_via(adapter):
    """
    Creates a temporary via on GND and deletes it after the test.
    Returns UUID, position, and net.
    """
    net = adapter.get_net_by_name("GND")
    pos = Vector2.from_xy(int(50 * MM), int(50 * MM))
    via = adapter.create_via(pos, net, 0.3, 0.6)

    commit = adapter.begin_commit()
    try:
        created = adapter.create_items([via])
        adapter.push_commit(commit, "test: create temp via")
        via_id = created[0].uuid
    except Exception:
        adapter.drop_commit(commit)
        raise

    yield via_id, pos, net

    # Delete the via after the test
    adapter.remove_by_id(via_id)
    commit2 = adapter.begin_commit()
    try:
        adapter.push_commit(commit2, "test: remove temp via")
    except Exception:
        adapter.drop_commit(commit2)
        raise


@pytest.fixture(scope="function")
def moved_component(adapter, test_component_ref):
    """
    Moves a component 1 mm to the right and restores it after the test.
    Returns refdes, original position, and new position.
    """
    fp = adapter.get_footprint(test_component_ref)
    if fp is None:
        pytest.skip(f"Component {test_component_ref} not found on the board")

    original_pos = fp.position
    new_pos = Vector2.from_xy(int(original_pos.x + 1 * MM), int(original_pos.y))

    # Move
    commit = adapter.begin_commit()
    try:
        fp.position = new_pos
        adapter.update_items([fp])
        adapter.push_commit(commit, "test: move component")
    except Exception:
        adapter.drop_commit(commit)
        raise

    yield test_component_ref, original_pos, new_pos

    # Restore
    fp_after = adapter.get_footprint(test_component_ref)
    if fp_after is None:
        return
    commit2 = adapter.begin_commit()
    try:
        fp_after.position = original_pos
        adapter.update_items([fp_after])
        adapter.push_commit(commit2, "test: restore component")
    except Exception:
        adapter.drop_commit(commit2)
        raise


@pytest.fixture(scope="function")
def flipped_component(adapter, test_component_ref):
    """
    Flips a component to the other side and restores it after the test.
    Returns refdes, original layer, and target layer.
    """
    fp = adapter.get_footprint(test_component_ref)
    if fp is None:
        pytest.skip(f"Component {test_component_ref} not found on the board")

    original_layer = fp.layer
    target_layer = BoardLayer.BL_B_Cu if original_layer == BoardLayer.BL_F_Cu else BoardLayer.BL_F_Cu

    # Flip
    adapter.flip_selected([fp])
    adapter.refresh_board()

    yield test_component_ref, original_layer, target_layer

    # Restore
    fp_after = adapter.get_footprint(test_component_ref)
    if fp_after is None:
        return
    adapter.flip_selected([fp_after])
    adapter.refresh_board()


@pytest.fixture(scope="function")
def registry(adapter, tmp_path):
    """
    Creates a temporary placement registry in tmp_path and returns PlacementRegistry.
    The registry file is deleted together with tmp_path after the test.
    """
    reg_path = tmp_path / "test.registry.json"
    return PlacementRegistry(adapter, str(reg_path))


@pytest.fixture(scope="function")
def template_extraction(adapter):
    """
    Fixture for template extraction tests.
    Selects components and vias from the config and returns the result of extract_template_from_selection.
    """
    from kicadstamp.template_extraction import extract_template_from_selection
    def _extract(name):
        return extract_template_from_selection(adapter, name)
    return _extract