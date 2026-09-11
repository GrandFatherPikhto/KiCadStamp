# kicadstamp/utils/yaml_loader.py
"""YAML read helper: ``safe_load`` = ``yaml.load`` with ``CSafeLoader``.

STATUS (2026-09-12): legacy, kept on purpose — NOT the project's config
reader any more. Until the config format moved to s-expr (2026-08-28,
core_yaml_removal) this module was the single chokepoint for every YAML read
in the project; the runtime config graph now reads ``.sexp``/``.json`` through
``kicadstamp/config/sexp_format.py`` and imports nothing from here. YAML was
deliberately NOT dropped as a capability, so this module is preserved as the
ready-made entry point should YAML ever be needed again. Its only remaining
consumers today are the one-off transition tool ``tools/sexp_config_convert.py``
and the tests for it.

PyYAML's ``yaml.safe_load`` binds the pure-Python ``SafeLoader`` even when
libyaml is installed; ``CSafeLoader`` is the same loader implemented in C and
is several times faster. This module exposes a drop-in ``safe_load`` that uses
``CSafeLoader`` when available and falls back to ``SafeLoader`` otherwise, so
every YAML reader pays for one YAML parse, at C speed.

Measured 2026-08-25 on profiles/3ch-awg-tia/3ch-awg-tia.yaml (5652 lines):
``yaml.safe_load`` 0.486s per 3 parses vs ``CSafeLoader`` 0.073s per 3 parses
(~6.7x). The GUI startup used to parse the root YAML once, so this was worth
~0.5s of MainWindow() construction on that project.
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
