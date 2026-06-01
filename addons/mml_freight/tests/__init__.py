import odoo
_STUB = getattr(odoo, "_stubbed", False)

# Odoo-safe: TransactionCase subclasses, no module-level pytest/importlib exec
from . import test_freight_responsibility
from . import test_package_aggregation
from . import test_carrier_eligibility
from . import test_quote_ranking
from . import test_auto_select
from . import test_tender_lifecycle
from . import test_3pl_handoff
from . import test_po_form_fields
from . import test_tender_package_population
from . import test_auto_tender
from . import test_tender_expiry
from . import test_booking_kpis
from . import test_fetch_label
from . import test_fetch_documents
from . import test_fetch_documents_idempotency
from . import test_fetch_invoice
from . import test_landed_cost
from . import test_dedup_constraints
from . import test_action_book_guard
from . import test_action_guards
from . import test_invoice_webhook_idempotency
from . import test_cron_sync_guard
from . import test_carrier_contract
from . import test_booking_unit_tracking
from . import test_contract_opportunity_cost
from . import test_contract_aware_selection
from . import test_contract_cron
from . import test_freight_document_model
from . import test_consolidated_pos
from . import test_freight_service

# Pure-Python: module-level pytest import or module-scope importlib exec
if _STUB:
    from . import test_booking_computed_fields
    from . import test_freight_cost_product
    from . import test_document_triggers
    from . import test_po_attachment
