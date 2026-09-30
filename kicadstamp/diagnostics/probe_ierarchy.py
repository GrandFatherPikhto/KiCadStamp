#!/usr/bin/env python3
"""
probe_ierarchy.py — matches board footprints' sheet_path against a sheet map
built from the live schematic.

Input:
    None (reads the live board and schematic).

Expected:
    Walks the schematic sheet tree, builds {full UUID path -> human-readable
    path}, then prints each footprint with its matched path (or reports a
    mismatch) — verifying that schematic sheet UUIDs line up with board
    sheet_path UUIDs.

Live KiCad:
    Yes — requires a running KiCad with the board and schematic open.

Run:
    python -m kicadstamp.diagnostics.probe_ierarchy
"""
import kipy

kc = kipy.KiCad()
board = kc.get_board()
schematic = kc.get_schematic()

# 1. Собираем карту путей из схемы: full_uuid_path -> human_readable_name
sheet_map = {}

def walk_sheets(sheet, current_path_str="", current_name_path=""):
    """Рекурсивно обходит листы схемы и собирает их пути"""
    # UUID текущего листа (у корня он может быть пустым)
    sheet_uuid = sheet.uuid if hasattr(sheet, 'uuid') else ""
    
    # Формируем цепочку UUID (аналог sp.path на плате)
    if current_path_str:
        new_path = f"{current_path_str}/{sheet_uuid}" if sheet_uuid else current_path_str
    else:
        new_path = sheet_uuid
        
    # Формируем читаемый путь (например, "Root/Channel_1")
    sheet_name = sheet.name if hasattr(sheet, 'name') else "Root"
    new_name_path = f"{current_name_path}/{sheet_name}" if current_name_path else sheet_name
    
    if new_path:
        sheet_map[new_path] = new_name_path
        
    # Идем вглубь по вложенным листам
    for sub_sheet in sheet.get_sheets():
        walk_sheets(sub_sheet, new_path, new_name_path)

# Запускаем обход с корня схемы
root_sheet = schematic.get_root_sheet()
walk_sheets(root_sheet)

# 2. Выводим футпринты, сопоставляя их sheet_path с нашей картой
footprints = board.get_footprints()
print(f"Всего футпринтов: {len(footprints)}\n")

for fp in footprints:
    ref = fp.reference_field.text.value
    sp = fp.sheet_path
    
    # sp.path — это список (tuple/list) из UUID. Превращаем в строку для маппинга
    # Исключаем UUID самого компонента (последний в цепочке), нам нужен именно путь листа
    sheet_uuid_chain = "/".join(sp.path[:-1]) if len(sp.path) > 1 else ""
    
    # Ищем имя листа в нашей карте
    human_path = sheet_map.get(sheet_uuid_chain, "Root (или не найдено)")
    
    print(f"{ref:10s}  Лист={human_path:<30s}  UUID-чейн={sp.path}")
