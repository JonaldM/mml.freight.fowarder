# conftest.py — fowarder.intergration
#
# Extends the root-level conftest.py stubs with:
#   1. sys.path wiring so addons/ is importable as top-level packages
#   2. odoo.addons.mml_freight* package registration (mirrors what
#      mainfreight.3pl.intergration/conftest.py does for stock_3pl_*)
#   3. psycopg2 stub (not installed in the test-only Python env)
#
# The root conftest.py (mml.odoo.apps/conftest.py) runs first and installs
# the core odoo.* stubs (odoo.models, odoo.fields, odoo.api, etc.).
# This file runs after and adds the freight-specific wiring on top.

import sys
import types
import pathlib

_HERE = pathlib.Path(__file__).parent
_ADDONS = _HERE / 'addons'

# ---------------------------------------------------------------------------
# 1. Add addons/ to sys.path so "import mml_freight" works as a direct import
# ---------------------------------------------------------------------------
if str(_ADDONS) not in sys.path:
    sys.path.insert(0, str(_ADDONS))


def _stub_missing_module(name):
    """Register an empty stub module under *name* if it isn't already present."""
    if name not in sys.modules:
        stub = types.ModuleType(name)
        sys.modules[name] = stub
    return sys.modules[name]


def _register_addon_package(addon_name, addon_path):
    """Register *addon_path* as both a top-level module and as
    odoo.addons.<addon_name> so that either import form resolves to the
    real package on disk.
    """
    full_name = f'odoo.addons.{addon_name}'
    if full_name in sys.modules:
        return sys.modules[full_name]

    # Create a module entry with __path__ pointing at the real directory so
    # Python can resolve relative imports inside the package.
    pkg = types.ModuleType(full_name)
    pkg.__path__ = [str(addon_path)]
    pkg.__package__ = full_name
    sys.modules[full_name] = pkg

    # Also make the short name importable (needed for intra-addon relative imports)
    if addon_name not in sys.modules:
        sys.modules[addon_name] = pkg

    # Attach to the odoo.addons namespace object
    odoo_addons = sys.modules.get('odoo.addons')
    if odoo_addons is not None:
        setattr(odoo_addons, addon_name, pkg)

    return pkg


def _register_subpackage(full_name, real_path):
    """Register a sub-package (e.g. odoo.addons.mml_freight.models)."""
    if full_name in sys.modules:
        return sys.modules[full_name]
    pkg = types.ModuleType(full_name)
    pkg.__path__ = [str(real_path)]
    pkg.__package__ = full_name
    sys.modules[full_name] = pkg
    return pkg


def _wire_freight_addons():
    """Wire mml_freight and mml_freight_dsv into the odoo.addons namespace."""

    # ------------------------------------------------------------------
    # psycopg2 stub — freight_booking.py imports it at module level but
    # only uses it inside methods that require a live DB connection.
    # ------------------------------------------------------------------
    _stub_missing_module('psycopg2')

    # ------------------------------------------------------------------
    # mml_freight
    # ------------------------------------------------------------------
    mf = _ADDONS / 'mml_freight'
    _register_addon_package('mml_freight', mf)
    for sub in ('adapters', 'models', 'services', 'controllers', 'wizards'):
        _register_subpackage(f'odoo.addons.mml_freight.{sub}', mf / sub)

    # ------------------------------------------------------------------
    # mml_freight_dsv
    # ------------------------------------------------------------------
    mfd = _ADDONS / 'mml_freight_dsv'
    _register_addon_package('mml_freight_dsv', mfd)
    for sub in ('adapters', 'models', 'controllers', 'wizards', 'tests'):
        _register_subpackage(f'odoo.addons.mml_freight_dsv.{sub}', mfd / sub)


_wire_freight_addons()
