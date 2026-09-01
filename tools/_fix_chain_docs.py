#!/usr/bin/env python3
"""One-off 2026-09-01 (plan rules_to_chains): terminology pass over the user
docs. Replaces the renamed identifiers ONLY — never the legitimate "Rule Area"
(kicad.md) or "non-rule net"/"rule net" phrases. Safe token replacements:

  rules:      -> chains:      (config section key)
  rule_effective_name -> chain_effective_name
  Rule        -> Chain        (dataclass / CLI identity, when not "Rule Area")
  rule (lowercase) -> chain   (prose, when not part of "Rule Area"/"rule net")
  правил/правила/правило (RU prose) -> цепей/цепи/цепь
"""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent

# (en, ru) doc pairs; each entry is a filename.
TARGETS = [
    "docs/config.md", "docs/config_ru.md",
    "docs/gui.md", "docs/gui_ru.md",
    "docs/commands.md", "docs/commands_ru.md",
    "docs/README.md", "docs/README_ru.md",
    "docs/python.md", "docs/python_ru.md",
    "docs/uplevel_modules.md", "docs/uplevel_modules_ru.md",
    "docs/architect.md", "docs/architect_ru.md",
    "docs/placement.md", "docs/placement_ru.md",
    "docs/tests.md", "docs/tests_ru.md",
    "docs/diagram.md", "docs/diagram_ru.md",
    "README.md", "README_ru.md",
]


def _replace(text: str) -> str:
    # Config key + identity renames (exact, order matters: longer first).
    text = text.replace("rules:", "chains:")
    text = text.replace("`rules`", "`chains`")
    text = text.replace("rule_effective_name", "chain_effective_name")
    # The dataclass / class name — but NOT "Rule Area" (kicad zone), and NOT
    # the "Rule net (null)" GUI checkbox label (still a real UI string).
    text = text.replace("Rule Area", "\x00AREA\x00")
    text = re.sub(r"(?<![\w./-])Rule(?!\w)", "Chain", text)
    text = text.replace("\x00AREA\x00", "Rule Area")
    # Prose "rule"/"rules" as the concept — skip "rule net", "non-rule",
    # "rule_nets", "rule_area", "rules:" (already done), "rules (no ...)".
    text = re.sub(r"(?<![\w./-])rules(?![\w:])", "chains", text)
    text = re.sub(r"(?<![\w./-])rule(?![\w:])", "chain", text)
    # Russian prose.
    text = text.replace("правила", "цепи")
    text = text.replace("правил", "цепей")
    text = text.replace("правило", "цепь")
    return text


def main() -> None:
    for name in TARGETS:
        path = ROOT / name
        if not path.exists():
            continue
        original = path.read_text(encoding="utf-8")
        updated = _replace(original)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            print(f"updated {name}")
        else:
            print(f"unchanged {name}")


if __name__ == "__main__":
    main()
