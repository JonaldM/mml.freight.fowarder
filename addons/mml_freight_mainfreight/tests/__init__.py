import odoo
_STUB = getattr(odoo, "_stubbed", False)

# Odoo-safe: TransactionCase subclasses, no module-level pytest/importlib exec
from . import test_mf_tracking
from . import test_mf_webhook

# Pure-Python: module-level pytest import or function-only tests
if _STUB:
    from . import test_mf_documents
    from . import test_mf_webhook_auth_order
