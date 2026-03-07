# conftest.py — mml.fowarder.intergration
#
# Self-contained: installs Odoo stubs AND wires freight addons.
# Must be self-contained because pytest.ini sets rootdir to this directory,
# so the parent mml.odoo.apps/conftest.py is outside rootdir and never loaded.
#
#   1. Odoo stubs (odoo.models, odoo.fields, odoo.api, odoo.tests, etc.)
#   2. sys.path wiring so addons/ is importable as top-level packages
#   3. odoo.addons.mml_freight* package registration
#   4. psycopg2 stub (not installed in the test-only Python env)
#   5. Auto-mark TransactionCase tests as odoo_integration

import sys
import types
import pathlib
import pytest

_HERE = pathlib.Path(__file__).parent
_ADDONS = _HERE / 'addons'


# ---------------------------------------------------------------------------
# 0. Odoo stubs — must run before any addon import
# ---------------------------------------------------------------------------
def _install_odoo_stubs():
    """Install minimal odoo.* stubs so pure-Python tests import without Odoo."""
    if 'odoo' in sys.modules and hasattr(sys.modules['odoo'], '_stubbed'):
        return

    odoo_fields = types.ModuleType('odoo.fields')

    class _BaseField:
        def __init__(self, *args, **kwargs):
            self._kwargs = kwargs
            self.default = kwargs.get('default')
            self.string = args[0] if args else kwargs.get('string', '')

        def __set_name__(self, owner, name):
            self._attr_name = name
            if '_fields_meta' not in owner.__dict__:
                owner._fields_meta = {}
            owner._fields_meta[name] = self

    class Selection(_BaseField):
        def __init__(self, selection=None, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.selection = selection or []

    class Datetime(_BaseField):
        @classmethod
        def now(cls):
            import datetime
            return datetime.datetime.utcnow()

    for _n in ('Boolean', 'Char', 'Date', 'Float', 'Integer', 'Text',
               'Html', 'Binary', 'Json', 'Many2one', 'One2many', 'Many2many'):
        setattr(odoo_fields, _n, type(_n, (_BaseField,), {}))
    odoo_fields.Selection = Selection
    odoo_fields.Datetime = Datetime

    odoo_models = types.ModuleType('odoo.models')

    class Model:
        _inherit = None
        _name = None
        _fields_meta = {}
        def write(self, vals): pass
        def ensure_one(self): pass
        def search(self, domain, **kwargs): return []
        def sudo(self): return self
        def create(self, vals): pass

    class AbstractModel(Model): pass
    class TransientModel(Model): pass
    odoo_models.Model = Model
    odoo_models.AbstractModel = AbstractModel
    odoo_models.TransientModel = TransientModel

    odoo_api = types.ModuleType('odoo.api')
    odoo_api.model = lambda f: f
    odoo_api.depends = lambda *args: (lambda f: f)
    odoo_api.constrains = lambda *args: (lambda f: f)
    odoo_api.onchange = lambda *args: (lambda f: f)
    odoo_api.model_create_multi = lambda f: f

    odoo_exceptions = types.ModuleType('odoo.exceptions')
    class ValidationError(Exception): pass
    class UserError(Exception): pass
    odoo_exceptions.ValidationError = ValidationError
    odoo_exceptions.UserError = UserError

    import unittest
    odoo_tests = types.ModuleType('odoo.tests')
    class TransactionCase(unittest.TestCase):
        """Stub: self.env NOT available without Odoo."""
    class HttpCase(TransactionCase):
        """Stub: HTTP test case requiring Odoo."""
    def tagged(*args):
        def decorator(cls): return cls
        return decorator
    odoo_tests.TransactionCase = TransactionCase
    odoo_tests.HttpCase = HttpCase
    odoo_tests.tagged = tagged
    odoo_tests_common = types.ModuleType('odoo.tests.common')
    odoo_tests_common.TransactionCase = TransactionCase
    odoo_tests_common.HttpCase = HttpCase

    odoo_http = types.ModuleType('odoo.http')
    odoo_http.Controller = type('Controller', (), {})
    odoo_http.route = lambda *a, **kw: (lambda f: f)
    odoo_http.request = None

    odoo = types.ModuleType('odoo')
    odoo._stubbed = True
    odoo._ = lambda s: s
    odoo.models = odoo_models
    odoo.fields = odoo_fields
    odoo.api = odoo_api
    odoo.exceptions = odoo_exceptions
    odoo.tests = odoo_tests
    odoo.http = odoo_http

    sys.modules['odoo'] = odoo
    sys.modules['odoo.models'] = odoo_models
    sys.modules['odoo.fields'] = odoo_fields
    sys.modules['odoo.api'] = odoo_api
    sys.modules['odoo.exceptions'] = odoo_exceptions
    sys.modules['odoo.tests'] = odoo_tests
    sys.modules['odoo.tests.common'] = odoo_tests_common
    sys.modules['odoo.http'] = odoo_http

    odoo_addons = types.ModuleType('odoo.addons')
    sys.modules['odoo.addons'] = odoo_addons
    odoo.addons = odoo_addons


_install_odoo_stubs()


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
    """Wire mml_freight, mml_freight_dsv, mml_freight_knplus, and mml_freight_mainfreight into the odoo.addons namespace."""

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

    # ------------------------------------------------------------------
    # mml_freight_knplus
    # ------------------------------------------------------------------
    mfk = _ADDONS / 'mml_freight_knplus'
    _register_addon_package('mml_freight_knplus', mfk)
    for sub in ('adapters', 'models', 'controllers', 'tests'):
        _register_subpackage(f'odoo.addons.mml_freight_knplus.{sub}', mfk / sub)

    # ------------------------------------------------------------------
    # mml_freight_mainfreight
    # ------------------------------------------------------------------
    mfm = _ADDONS / 'mml_freight_mainfreight'
    _register_addon_package('mml_freight_mainfreight', mfm)
    for sub in ('adapters', 'models', 'controllers', 'tests'):
        _register_subpackage(f'odoo.addons.mml_freight_mainfreight.{sub}', mfm / sub)


_wire_freight_addons()


def pytest_collection_modifyitems(config, items):
    """Auto-mark TransactionCase-based tests as odoo_integration."""
    from odoo.tests import TransactionCase
    for item in items:
        if isinstance(item, pytest.Class):
            continue
        cls = getattr(item, 'cls', None)
        if cls is not None and issubclass(cls, TransactionCase):
            item.add_marker(pytest.mark.odoo_integration)
