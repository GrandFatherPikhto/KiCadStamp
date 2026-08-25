# test_track_ops.py
"""
Проба: работает ли создание/чтение/удаление PCB_TRACK через kipy IPC.

Это НЕ тест готовой фичи (её ещё нет) — это тот самый эмпирический шаг
"а работает ли вообще API", который должен идти ПЕРЕД проектированием
формата шаблона для дорожек (см. обсуждение в чате). Намеренно не трогает
adapter.py — adapter пока не умеет треки вообще, здесь щупаем kipy напрямую,
через уже существующие create_items/begin_commit/push_commit (они уже
приняты как generic — не специфичны для Via).

Если этот тест падает или зависает — значит, идея с шаблонными дорожками
упирается в ограничение самого API, и обсуждать формат смысла нет, пока
это не снимется (апдейт KiCad/kipy).
"""
import pytest
from kipy.geometry import Vector2
from kipy.board_types import Track, BoardLayer
from kicadstamp.utils.units import MM


@pytest.mark.integration
def test_create_read_remove_track(adapter):
    """Создаём прямой трек на GND между двумя произвольными точками, читаем его обратно, удаляем."""
    net = adapter.get_net_by_name("GND")
    assert net is not None, "GND должна существовать на тестовой плате"

    start = Vector2.from_xy(int(50 * MM), int(50 * MM))
    end = Vector2.from_xy(int(55 * MM), int(50 * MM))
    width_mm = 0.25

    track = Track()
    track.start = start
    track.end = end
    track.width = int(width_mm * MM)
    track.layer = BoardLayer.BL_F_Cu
    track.net = net

    commit = adapter.begin_commit()
    try:
        created = adapter.create_items([track])
        adapter.push_commit(commit, "test: create track")
    except Exception:
        adapter.drop_commit(commit)
        raise

    assert len(created) == 1, "create_items должен вернуть ровно один созданный объект"
    track_id = created[0].uuid

    # --- Читаем обратно ---
    tracks_after = adapter._board.get_tracks()
    found = next((t for t in tracks_after if str(t.id.value) == track_id), None)
    assert found is not None, "созданный трек не находится через board.get_tracks()"

    # Позиция/ширина/слой/цепь должны совпасть с тем, что мы задали
    assert found.start.x == start.x and found.start.y == start.y, \
        f"начало трека разошлось: задано {start}, получено {found.start}"
    assert found.end.x == end.x and found.end.y == end.y, \
        f"конец трека разошёлся: задано {end}, получено {found.end}"
    assert abs(found.width - int(width_mm * MM)) < 1000, \
        f"ширина трека разошлась: задано {width_mm} мм, получено {found.width / MM} мм"
    assert found.net is not None and found.net.name == "GND", \
        f"цепь трека разошлась: ожидали GND, получили {found.net.name if found.net else None}"

    # --- Удаляем ---
    adapter.remove_by_id(track_id)
    commit2 = adapter.begin_commit()
    try:
        adapter.push_commit(commit2, "test: remove track")
    except Exception:
        adapter.drop_commit(commit2)
        raise

    tracks_final = adapter._board.get_tracks()
    assert not any(str(t.id.value) == track_id for t in tracks_final), \
        "трек не удалился после remove_by_id + push_commit"