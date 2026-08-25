import pytest
from kicadstamp.domain.geometry import Vector2
from kicadstamp.utils.units import MM
from kicadstamp.domain.geometry import BoardLayer

@pytest.mark.integration
def test_move_component(adapter, test_component_ref):
    """Перемещаем компонент на 1 мм по X и возвращаем обратно."""
    fp = adapter.get_footprint(test_component_ref)
    assert fp is not None

    original_pos = fp.position
    new_pos = Vector2.from_xy(int(original_pos.x + 1 * MM), int(original_pos.y))

    # Перемещаем
    commit = adapter.begin_commit()
    try:
        fp.position = new_pos
        adapter.update_items([fp])
        adapter.push_commit(commit, "test: move component")
    except Exception:
        adapter.drop_commit(commit)
        raise

    # Проверяем, что позиция изменилась
    fp_after = adapter.get_footprint(test_component_ref)
    assert fp_after.position.x == new_pos.x

    # Возвращаем обратно
    commit2 = adapter.begin_commit()
    try:
        fp_after.position = original_pos
        adapter.update_items([fp_after])
        adapter.push_commit(commit2, "test: restore component")
    except Exception:
        adapter.drop_commit(commit2)
        raise

    fp_final = adapter.get_footprint(test_component_ref)
    assert fp_final.position.x == original_pos.x


@pytest.mark.integration
def test_flip_component(adapter, test_component_ref):
    """Переворачиваем компонент на другую сторону и возвращаем обратно."""
    fp = adapter.get_footprint(test_component_ref)
    assert fp is not None

    original_layer = fp.layer
    target_layer = BoardLayer.BL_B_Cu if original_layer == BoardLayer.BL_F_Cu else BoardLayer.BL_F_Cu

    # Флип через адаптер
    adapter.flip_selected([fp])
    # Перечитываем плату
    adapter.refresh_board()
    fp_after = adapter.get_footprint(test_component_ref)

    assert fp_after.layer == target_layer

    # Возвращаем обратно
    adapter.flip_selected([fp_after])
    adapter.refresh_board()
    fp_final = adapter.get_footprint(test_component_ref)
    assert fp_final.layer == original_layer
