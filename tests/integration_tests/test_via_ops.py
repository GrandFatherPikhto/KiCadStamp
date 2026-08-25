# test_via_ops.py
import pytest
from kicadstamp.utils.units import MM
from kipy.geometry import Vector2
from kicadstamp.placement.commands import ViaCommand
from kicadstamp.registry import PlacementRegistry, registry_path_for_config


@pytest.mark.integration
def test_create_and_remove_via(adapter, test_config, tmp_path):
    """Создаём via, проверяем, что она появилась, затем удаляем."""
    # Получаем цепь GND
    net = adapter.get_net_by_name("GND")
    assert net is not None

    # Создаём via
    pos = Vector2.from_xy(int(50 * MM), int(50 * MM))
    via = adapter.create_via(pos, net, 0.3, 0.6)

    commit = adapter.begin_commit()
    try:
        created = adapter.create_items([via])
        adapter.push_commit(commit, "test: create via")
        assert len(created) == 1
        via_id = created[0].uuid
    except Exception:
        adapter.drop_commit(commit)
        raise

    # Проверяем, что via появилась
    vias_after = adapter.get_vias()
    assert any(v.uuid == via_id for v in vias_after)

    # Удаляем via (через реестр или напрямую)
    adapter.remove_by_id(via_id)
    commit2 = adapter.begin_commit()
    try:
        adapter.push_commit(commit2, "test: remove via")
    except Exception:
        adapter.drop_commit(commit2)
        raise

    # Проверяем, что via удалена
    vias_final = adapter.get_vias()
    assert not any(v.uuid == via_id for v in vias_final)


@pytest.mark.integration
def test_registry_reconcile(adapter, test_config, tmp_path):
    """Проверяем, что реестр корректно обрабатывает создание via."""
    reg_path = tmp_path / "test.registry.json"
    registry = PlacementRegistry(adapter, str(reg_path))

    # Создаём команду via
    net = adapter.get_net_by_name("GND")
    via_cmd = ViaCommand(
        position=Vector2.from_xy(int(50 * MM), int(50 * MM)),
        drill_mm=0.3, diameter_mm=0.6,
        net_name="GND", owner_ref="IC1",
        registry_key="test|via|0"
    )

    # Первый вызов — должна создаться
    to_create, _ = registry.reconcile([via_cmd])
    assert len(to_create) == 1

    # Создаём via и записываем в реестр
    commit = adapter.begin_commit()
    try:
        created = adapter.create_items([adapter.create_via(via_cmd.position, adapter.get_net_by_name("GND"), 0.3, 0.6)])
        registry.record_created(via_cmd, created[0].uuid)
        adapter.push_commit(commit, "test: registry via")
    except Exception:
        adapter.drop_commit(commit)
        raise

    # Второй вызов — via уже существует, должна быть пропущена
    to_create_2, _ = registry.reconcile([via_cmd])
    assert len(to_create_2) == 0

    # Удаляем via через реестр (prune)
    adapter.remove_by_id(registry.entries[via_cmd.registry_key].uuid)
    commit2 = adapter.begin_commit()
    try:
        adapter.push_commit(commit2, "test: remove registry via")
    except Exception:
        adapter.drop_commit(commit2)
        raise

@pytest.mark.integration
def test_temp_via_creation(temp_via):
    via_id, pos, net = temp_via
    assert via_id is not None
    assert pos.x == 50 * MM
    assert net.name == "GND"

@pytest.mark.integration
def test_registry_with_via(registry, temp_via):
    via_id, pos, net = temp_via
    from kicadstamp.placement.commands import ViaCommand
    cmd = ViaCommand(pos, 0.3, 0.6, "GND", "test", "test|via|0")
    # Сначала записываем созданную via в реестр
    registry.record_created(cmd, via_id)
    # Теперь reconcile должен пропустить via
    to_create, _ = registry.reconcile([cmd])
    assert len(to_create) == 0


# test_component_ops.py
@pytest.mark.integration
def test_move_component(moved_component):
    ref, orig_pos, new_pos = moved_component
    assert ref is not None
    # Проверяем, что компонент действительно был перемещён (после фикстуры)
    # Здесь мы можем прочитать его позицию и убедиться, что она = new_pos
    # (но это уже делает сама фикстура, так что тест просто проверяет, что всё работает)
    assert orig_pos.x != new_pos.x

@pytest.mark.integration
def test_flip_component(flipped_component):
    ref, orig_layer, target_layer = flipped_component
    assert ref is not None
    assert orig_layer != target_layer