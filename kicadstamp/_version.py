# kicadstamp/_version.py
"""Single source of truth for the project version.

Kept in its own module (not __init__.py) so setuptools can read it via
``[tool.setuptools.dynamic] version = {attr = "kicadstamp._version.__version__"}``
without importing kicadstamp (which would trigger i18n side effects).
"""
__version__ = "1.8.0"
