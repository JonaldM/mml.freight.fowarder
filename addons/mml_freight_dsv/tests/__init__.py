import odoo
_STUB = getattr(odoo, "_stubbed", False)

# Odoo-safe: TransactionCase subclasses, no module-level pytest/importlib exec
from . import test_dsv_auth
from . import test_dsv_mock_adapter
from . import test_cron_jobs
from . import test_dsv_quote_payload
from . import test_dsv_booking_payload
from . import test_dsv_generic_adapter
from . import test_dsv_cancel
from . import test_dsv_confirm_booking
from . import test_dsv_tracking
from . import test_dsv_webhook
from . import test_dsv_webhook_dispatch
from . import test_dsv_label
from . import test_dsv_documents
from . import test_dsv_invoice
from . import test_dsv_invoice_webhook
from . import test_dsv_webhook_dedup
from . import test_dsv_doc_upload

# Pure-Python: function-only tests, no TransactionCase subclass
if _STUB:
    from . import test_dsv_acl
