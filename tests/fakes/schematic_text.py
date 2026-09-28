# tests/fakes/schematic_text.py
"""Synthetic .kicad_sch text builders shared by tests/test_schematic_*.py
and tests/gui/test_schema_model.py, test_fieldstool_window.py — small but
structurally faithful to real KiCad output (verified against
test_boards/3CH-AWG-TIA/op_amp.kicad_sch and 3CH-AWG-TIA.kicad_sch), so
the regex/balanced-paren parsing in kicadstamp/schematic_blocks.py and
kicadstamp/schematic_discovery.py exercises the real shapes, not a
simplified one. Whole files must stay valid S-expression syntax (balanced
parens) since kicadstamp/schematic_editing.py's write self-verify calls
sexpdata.load() on them.

Ф1.7 of plan_2026_09_27_repo_and_tests_transformation: this module moved out of
the tests/ root (it used to be tests/fieldstool_fixtures.py) and was RENAMED — the
old name promised pytest fixtures and there is not one in the file: it builds
schematic TEXT. It sits in tests/fakes/ because twelve test files across several
domains share it, and that package keeps working when Ф2 moves those files into
domain subpackages.
"""
from typing import Iterable, Optional


def symbol_block(refs: Iterable[str], role: Optional[str] = None,
                  cluster: Optional[str] = None, x: float = 100.0, y: float = 50.0,
                  symbol_uuid: Optional[str] = None,
                  instance_paths: Optional[list] = None) -> str:
    """instance_paths: optional list of (refdes, path_string) pairs overriding
    the default '/dummy/<ref>' paths — lets tests model real multi-element
    instance chains (root/sheet-instance/symbol-uuid)."""
    fields = ""
    if role is not None:
        fields += (f'\t\t(property "Role" "{role}"\n'
                   f'\t\t\t(at {x} {y} 0)\n\t\t\t(hide yes)\n\t\t\t(show_name no)\n'
                   f'\t\t\t(do_not_autoplace no)\n'
                   f'\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n')
    if cluster is not None:
        fields += (f'\t\t(property "Cluster" "{cluster}"\n'
                   f'\t\t\t(at {x} {y} 0)\n\t\t\t(hide yes)\n\t\t\t(show_name no)\n'
                   f'\t\t\t(do_not_autoplace no)\n'
                   f'\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n')
    path_pairs = instance_paths or [(ref, f"/dummy/{ref}") for ref in refs]
    instances = "".join(
        f'\t\t\t\t(path "{p}"\n\t\t\t\t\t(reference "{r}")\n\t\t\t\t\t(unit 1)\n\t\t\t\t)\n'
        for r, p in path_pairs
    )
    uuid_line = f'\t\t(uuid "{symbol_uuid}")\n' if symbol_uuid else ''
    return (
        '\t(symbol\n'
        f'\t\t(lib_id "Device:R")\n'
        f'\t\t(at {x} {y} 0)\n'
        '\t\t(unit 1)\n'
        f'{uuid_line}'
        f'{fields}'
        '\t\t(pin "1"\n\t\t\t(uuid "00000000-0000-0000-0000-000000000001")\n\t\t)\n'
        '\t\t(instances\n'
        '\t\t\t(project "TEST"\n'
        f'{instances}'
        '\t\t\t)\n'
        '\t\t)\n'
        '\t)\n'
    )


def sheet_block(sheetfile: str, sheetname: str = "Sheet") -> str:
    return (
        '\t(sheet\n'
        '\t\t(at 100.0 50.0)\n'
        '\t\t(size 20.0 10.0)\n'
        f'\t\t(property "Sheetname" "{sheetname}"\n'
        '\t\t\t(at 100.0 49.0 0)\n\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n'
        f'\t\t(property "Sheetfile" "{sheetfile}"\n'
        '\t\t\t(at 100.0 61.0 0)\n\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n'
        '\t)\n'
    )


def sch_file(*blocks: str) -> str:
    body = "".join(blocks)
    return (
        '(kicad_sch\n'
        '\t(version 20231120)\n'
        '\t(generator "eeschema")\n'
        f'{body}'
        ')\n'
    )
