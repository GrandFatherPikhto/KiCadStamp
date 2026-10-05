# kicadstamp/diagnostics/deepseek_mutations_project_map_2026_10_05.py
"""Demon's acceptance rows for tools/project_map.py
(plan_2026_10_05_project_map.md, Part 1).

Guards live in tests/repo/test_project_map.py. Run on the shared machinery of
deepseek_mutations_refresh_mixed_2026_10_05.py (count == 1 or НЕДЕЙСТВИТЕЛЬНА,
_drop_pyc, PYTHONDONTWRITEBYTECODE, -n auto, ПРОМАХ on zero reds, a control that
MUST survive).

    .venv/bin/python kicadstamp/diagnostics/deepseek_mutations_project_map_2026_10_05.py
"""
from kicadstamp.diagnostics import deepseek_mutations_refresh_mixed_2026_10_05 as rig

G = ["test_project_map.py"]
MAP = "tools/project_map.py"

ROWS = [
    ("PM1 output not sorted", MAP,
     "        for m in sorted(in_pkg, key=lambda x: x.rel):",
     "        for m in sorted(in_pkg, key=lambda x: x.rel, reverse=True):  # MUTATION",
     "die", G, ()),
    ("PM2 imports inside functions not collected", MAP,
     "        for node in ast.walk(self.tree):  # type: ignore[arg-type]",
     "        for node in self.tree.body:  # MUTATION",
     "die", G, ()),
    ("PM3 __all__ ignored", MAP,
     "                if name.startswith(\"_\") and name not in exported:",
     "                if name.startswith(\"_\"):  # MUTATION",
     "die", G, ()),
    ("PM4 800 threshold broken", MAP,
     "            flag = \" ⚠ >800\" if m.total_lines > BIG_FILE_LINES else \"\"",
     "            flag = \" ⚠ >800\" if m.total_lines > 8000 else \"\"  # MUTATION",
     "die", G, ()),
    ("PM5 parse exception crashes the generator", MAP,
     "        except SyntaxError as e:\n            self.parse_error = f\"{type(e).__name__}: line {e.lineno}: {e.msg}\"",
     "        except SyntaxError as e:\n            raise  # MUTATION",
     "die", G, ()),
    ("PM6 reverse edges not built", MAP,
     "        incoming = sorted(reverse.get(m.key, ()))",
     "        incoming = []  # MUTATION",
     "die", G, ()),
    ("K7 cosmetic comment (control)", MAP,
     "def build_map(root: Path) -> str:",
     "def build_map(root: Path) -> str:  # control",
     "survive", G, ()),
]

if __name__ == "__main__":
    rig.MUTATIONS = ROWS
    rig.main()
