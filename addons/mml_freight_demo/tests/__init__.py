import odoo
_STUB = getattr(odoo, "_stubbed", False)

# Odoo-safe: TransactionCase subclasses, no module-level pytest/importlib exec
from . import test_demo_install
