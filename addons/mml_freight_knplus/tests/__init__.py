import odoo
_STUB = getattr(odoo, "_stubbed", False)

# Odoo-safe: TransactionCase subclasses, no module-level pytest/importlib exec
from . import test_kn_adapter
from . import test_kn_webhook

# Pure-Python: module-level pytest import or function-only tests
if _STUB:
    from . import test_pure_kn_gate
    from . import test_kn_webhook_sandbox
