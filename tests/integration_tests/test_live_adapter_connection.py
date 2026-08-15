import pytest
from kicadstamp.exceptions import BoardNotFoundError


@pytest.mark.integration
def test_adapter_connection(adapter):
    """Проверяем, что адаптер подключается к KiCad."""
    assert adapter._board is not None


@pytest.mark.integration
def test_get_footprint(adapter, test_component_ref):
    """Проверяем поиск компонента."""
    fp = adapter.get_footprint(test_component_ref)
    assert fp is not None
    assert fp.reference_field.text.value == test_component_ref


@pytest.mark.integration
def test_get_net_by_name(adapter):
    """Проверяем поиск цепи."""
    net = adapter.get_net_by_name("GND")
    assert net is not None
    assert net.name == "GND"


@pytest.mark.integration
def test_get_vias(adapter):
    """Проверяем получение всех via."""
    vias = adapter.get_vias()
    assert isinstance(vias, list)