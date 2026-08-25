# kicadstamp/utils/yaml_loader.py
"""Single chokepoint for reading YAML in this project.

PyYAML's ``yaml.safe_load`` binds the pure-Python ``SafeLoader`` even when
libyaml is installed; ``CSafeLoader`` is the same loader implemented in C and
is several times faster. This module exposes a drop-in ``safe_load`` that uses
``CSafeLoader`` when available and falls back to ``SafeLoader`` otherwise, so
every config reader in the project pays for one YAML parse, at C speed.

Measured 2026-08-25 on profiles/3ch-awg-tia/3ch-awg-tia.yaml (5652 lines):
``yaml.safe_load`` 0.486s per 3 parses vs ``CSafeLoader`` 0.073s per 3 parses
(~6.7x). The GUI startup parses the root YAML once, so this is worth ~0.5s of
MainWindow() construction on that project.
"""
import yaml

try:
    _SAFE_LOADER = yaml.CSafeLoader  # type: ignore[attr-defined]
except AttributeError:  # PyYAML compiled without libyaml
    _SAFE_LOADER = yaml.SafeLoader


def safe_load(stream):
    """Drop-in replacement for ``yaml.safe_load``: ``yaml.load(stream,
    Loader=CSafeLoader-or-SafeLoader)``. Returns the same Python objects; the
    stream may be a file object or a str/bytes YAML document."""
    return yaml.load(stream, Loader=_SAFE_LOADER)
