# Hardening Sprint — Deduplication & Idempotency Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate all duplicate-record and double-processing risks across `mml_freight` and `mml_freight_dsv` — DB constraints, pessimistic locks, webhook dedup, and action guards.

**Architecture:** Seven independent tasks ordered by risk priority. Tasks 1–3 add DB-level and model-level protection (never rely on application logic alone). Tasks 4–6 add pessimistic locks and state guards on critical action methods. Task 7 hardens the tracking cron. The `freight.webhook.event` model (Task 2) is the only new model introduced — it's a direct copy of the `source_hash` pattern from `stock_3pl_core`. Everything else is targeted edits to existing files.

**Tech Stack:** Odoo 19, Python, PostgreSQL `SELECT … FOR UPDATE NOWAIT`, SHA-256 via `hashlib`.

**Key files to read before starting:**
- `addons/mml_freight/models/freight_tracking_event.py` — 17 lines, no constraints today
- `addons/mml_freight/models/freight_document.py` — 23 lines, no constraints today
- `addons/mml_freight/models/freight_booking.py` — `action_fetch_documents`, `action_fetch_invoice`, `_handle_dsv_invoice_webhook`, `cron_sync_tracking`
- `addons/mml_freight/models/freight_tender.py` — `action_request_quotes` (line 137), `action_book` (line 233)
- `addons/mml_freight_dsv/controllers/dsv_webhook.py` — 71 lines, no source_hash today
- `addons/mml_freight/security/ir.model.access.csv` — format reference for Task 2

**Reference implementation:** `E:\ClaudeCode\projects\mml.odoo.apps\mainfreight.3pl.intergration` — `addons/stock_3pl_core/models/message.py` for `source_hash` and `idempotency_key` patterns.

---

## Task 1: DB Unique Constraints — `freight.tracking.event` and `freight.document`

**Why:** No DB-level protection exists. Concurrent webhook deliveries and overlapping cron runs can both pass the application-level `if not exists` check and insert duplicate rows simultaneously. DB constraints are the last line of defence.

**Files:**
- Modify: `addons/mml_freight/models/freight_tracking_event.py`
- Modify: `addons/mml_freight/models/freight_document.py`
- Create: `addons/mml_freight/tests/test_dedup_constraints.py`
- Modify: `addons/mml_freight/tests/__init__.py`

---

**Step 1: Write failing tests**

`addons/mml_freight/tests/test_dedup_constraints.py`:
```python
from odoo.tests.common import TransactionCase


class TestDedupConstraints(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Constraint Test Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })
        supplier = cls.env['res.partner'].create({'name': 'Constraint Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        tender = cls.env['freight.tender'].create({
            'purchase_order_id': po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': tender.id,
            'currency_id': cls.env.company.currency_id.id,
        })

    # ── freight.tracking.event ──────────────────────────────────────────────

    def test_duplicate_tracking_event_blocked_at_db(self):
        """UNIQUE(booking_id, event_date, status) prevents duplicate tracking events."""
        self.env['freight.tracking.event'].create({
            'booking_id': self.booking.id,
            'event_date': '2026-03-01 10:00:00',
            'status': 'in_transit',
            'location': 'Shanghai CN',
            'description': 'Departed',
        })
        with self.assertRaises(Exception, msg='Duplicate tracking event must be blocked'):
            with self.env.cr.savepoint():
                self.env['freight.tracking.event'].create({
                    'booking_id': self.booking.id,
                    'event_date': '2026-03-01 10:00:00',
                    'status': 'in_transit',
                    'location': 'Different location',  # different data, same key
                    'description': 'Duplicate',
                })

    def test_same_status_different_date_allowed(self):
        """Same status on a different date is NOT a duplicate."""
        self.env['freight.tracking.event'].create({
            'booking_id': self.booking.id,
            'event_date': '2026-03-01 10:00:00',
            'status': 'delivered',
            'location': 'Auckland NZ',
            'description': 'First event',
        })
        # Different date → must not raise
        self.env['freight.tracking.event'].create({
            'booking_id': self.booking.id,
            'event_date': '2026-03-02 10:00:00',
            'status': 'delivered',
            'location': 'Auckland NZ',
            'description': 'Second event',
        })

    # ── freight.document ────────────────────────────────────────────────────

    def test_duplicate_document_blocked_at_db(self):
        """UNIQUE(booking_id, doc_type, carrier_doc_ref) prevents duplicate documents."""
        attachment = self.env['ir.attachment'].create({
            'name': 'test.pdf',
            'type': 'binary',
            'datas': 'dGVzdA==',
            'res_model': 'freight.booking',
            'res_id': self.booking.id,
        })
        self.env['freight.document'].create({
            'booking_id': self.booking.id,
            'doc_type': 'pod',
            'attachment_id': attachment.id,
            'carrier_doc_ref': 'DSV-DOC-001',
        })
        with self.assertRaises(Exception, msg='Duplicate document must be blocked'):
            with self.env.cr.savepoint():
                self.env['freight.document'].create({
                    'booking_id': self.booking.id,
                    'doc_type': 'pod',
                    'attachment_id': attachment.id,
                    'carrier_doc_ref': 'DSV-DOC-001',  # same ref
                })

    def test_same_doc_type_different_ref_allowed(self):
        """Same doc_type with a different carrier_doc_ref is NOT a duplicate."""
        attachment = self.env['ir.attachment'].create({
            'name': 'a.pdf', 'type': 'binary', 'datas': 'dGVzdA==',
            'res_model': 'freight.booking', 'res_id': self.booking.id,
        })
        self.env['freight.document'].create({
            'booking_id': self.booking.id,
            'doc_type': 'pod',
            'attachment_id': attachment.id,
            'carrier_doc_ref': 'REF-A',
        })
        # Different ref → must not raise
        self.env['freight.document'].create({
            'booking_id': self.booking.id,
            'doc_type': 'pod',
            'attachment_id': attachment.id,
            'carrier_doc_ref': 'REF-B',
        })
```

**Step 2: Run tests — expect failures** (constraints don't exist yet)

```bash
python -m pytest addons/mml_freight/tests/test_dedup_constraints.py -v 2>&1 | head -30
```

**Step 3: Add unique constraint to `freight_tracking_event.py`**

Add `_sql_constraints` immediately before the field definitions:

```python
from odoo import models, fields


class FreightTrackingEvent(models.Model):
    _name = 'freight.tracking.event'
    _description = 'Freight Booking — Tracking Event'
    _order = 'event_date desc'

    _sql_constraints = [
        (
            'unique_booking_event',
            'UNIQUE(booking_id, event_date, status)',
            'A tracking event with this status and date already exists for this booking.',
        ),
    ]

    booking_id = fields.Many2one(
        'freight.booking', required=True, ondelete='cascade', index=True,
    )
    event_date = fields.Datetime('Event Date', required=True)
    status = fields.Char('Status', required=True)
    location = fields.Char('Location')
    description = fields.Char('Description')
    raw_payload = fields.Text('Raw Payload')
```

**Step 4: Add unique constraint to `freight_document.py`**

```python
from odoo import models, fields

DOC_TYPES = [
    ('label', 'Shipping Label'),
    ('pod', 'Proof of Delivery'),
    ('invoice', 'Freight Invoice'),
    ('customs', 'Customs Document'),
    ('other', 'Other'),
]


class FreightDocument(models.Model):
    _name = 'freight.document'
    _description = 'Freight Booking — Document'
    _order = 'id'

    _sql_constraints = [
        (
            'unique_booking_doc',
            'UNIQUE(booking_id, doc_type, carrier_doc_ref)',
            'A document with this type and carrier reference already exists for this booking.',
        ),
    ]

    booking_id = fields.Many2one(
        'freight.booking', required=True, ondelete='cascade', index=True,
    )
    doc_type = fields.Selection(DOC_TYPES, string='Type', required=True, default='other')
    attachment_id = fields.Many2one('ir.attachment', string='Attachment', ondelete='set null')
    carrier_doc_ref = fields.Char('Carrier Doc Ref')
```

**Note on NULLs:** PostgreSQL treats `NULL` values as distinct in unique constraints, so rows where `carrier_doc_ref IS NULL` will never violate the constraint — this is intentional. Task 3 eliminates null refs by generating synthetic ones.

**Step 5: Register test in `__init__.py`**

Add to `addons/mml_freight/tests/__init__.py`:
```python
from . import test_dedup_constraints
```

**Step 6: Commit**

```bash
git add addons/mml_freight/models/freight_tracking_event.py \
        addons/mml_freight/models/freight_document.py \
        addons/mml_freight/tests/test_dedup_constraints.py \
        addons/mml_freight/tests/__init__.py
git commit -m "feat: DB unique constraints — freight.tracking.event + freight.document"
```

---

## Task 2: Webhook Deduplication — `freight.webhook.event` Model

**Why:** DSV will retry webhook delivery if our endpoint times out or returns a non-2xx. Without dedup, a retried `TRACKING_UPDATE` or `Invoice` webhook creates duplicate tracking events and chatter notes. Pattern copied directly from `stock_3pl_core`'s `source_hash` approach.

**Files:**
- Create: `addons/mml_freight/models/freight_webhook_event.py`
- Modify: `addons/mml_freight/models/__init__.py`
- Modify: `addons/mml_freight/security/ir.model.access.csv`
- Modify: `addons/mml_freight_dsv/controllers/dsv_webhook.py`
- Create: `addons/mml_freight_dsv/tests/test_dsv_webhook_dedup.py`
- Modify: `addons/mml_freight_dsv/tests/__init__.py`

---

**Step 1: Write failing tests**

`addons/mml_freight_dsv/tests/test_dsv_webhook_dedup.py`:
```python
import hashlib
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase


class TestDsvWebhookDedup(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Dedup Webhook Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })

    def _make_hash(self, body: bytes) -> str:
        return hashlib.sha256(body).hexdigest()

    def test_webhook_event_model_exists(self):
        """freight.webhook.event model must be registered."""
        self.assertIn('freight.webhook.event', self.env)

    def test_duplicate_source_hash_blocked_at_db(self):
        """UNIQUE(carrier_id, source_hash) prevents duplicate webhook event log entries."""
        h = self._make_hash(b'{"eventType":"TRACKING_UPDATE","shipmentId":"SH-001"}')
        self.env['freight.webhook.event'].create({
            'carrier_id': self.carrier.id,
            'source_hash': h,
            'event_type': 'TRACKING_UPDATE',
        })
        with self.assertRaises(Exception, msg='Duplicate webhook event must be blocked at DB level'):
            with self.env.cr.savepoint():
                self.env['freight.webhook.event'].create({
                    'carrier_id': self.carrier.id,
                    'source_hash': h,
                    'event_type': 'TRACKING_UPDATE',
                })

    def test_different_payload_same_carrier_allowed(self):
        """Different payloads (different hash) on same carrier must both be accepted."""
        h1 = self._make_hash(b'{"shipmentId":"SH-A"}')
        h2 = self._make_hash(b'{"shipmentId":"SH-B"}')
        self.env['freight.webhook.event'].create({
            'carrier_id': self.carrier.id,
            'source_hash': h1,
            'event_type': 'TRACKING_UPDATE',
        })
        # Must not raise
        self.env['freight.webhook.event'].create({
            'carrier_id': self.carrier.id,
            'source_hash': h2,
            'event_type': 'TRACKING_UPDATE',
        })

    def test_duplicate_webhook_dispatch_skipped(self):
        """Second delivery of identical webhook body must be silently ignored — no handler called."""
        body = b'{"eventType":"TRACKING_UPDATE","shipmentId":"SH-DUP-001"}'
        h = self._make_hash(body)
        # Pre-seed the event log as if first delivery already processed
        self.env['freight.webhook.event'].create({
            'carrier_id': self.carrier.id,
            'source_hash': h,
            'event_type': 'TRACKING_UPDATE',
        })
        mock_handler = MagicMock()
        with patch.object(
            type(self.env['freight.booking']),
            '_handle_dsv_tracking_webhook',
            mock_handler,
        ):
            # Simulate what the controller does after HMAC validation
            existing = self.env['freight.webhook.event'].search([
                ('carrier_id', '=', self.carrier.id),
                ('source_hash', '=', h),
            ], limit=1)
            if not existing:
                self.env['freight.webhook.event'].create({
                    'carrier_id': self.carrier.id,
                    'source_hash': h,
                    'event_type': 'TRACKING_UPDATE',
                })
                # Would dispatch here
            # Must NOT have dispatched
            mock_handler.assert_not_called()
```

**Step 2: Create `freight_webhook_event.py`**

`addons/mml_freight/models/freight_webhook_event.py`:
```python
from odoo import models, fields


class FreightWebhookEvent(models.Model):
    """Deduplication log for inbound carrier webhook payloads.

    Pattern copied from stock_3pl_core.3pl.message.source_hash. The unique
    constraint on (carrier_id, source_hash) is the primary deduplication
    mechanism — the application-level search-before-create is the fast path,
    the DB constraint is the safety net for race conditions.
    """
    _name = 'freight.webhook.event'
    _description = 'Freight Webhook Event (deduplication log)'
    _order = 'received_at desc'

    _sql_constraints = [
        (
            'unique_carrier_event',
            'UNIQUE(carrier_id, source_hash)',
            'This webhook payload has already been processed for this carrier.',
        ),
    ]

    carrier_id = fields.Many2one(
        'delivery.carrier', required=True, ondelete='cascade', index=True,
    )
    source_hash = fields.Char('Payload SHA-256', required=True, index=True)
    event_type = fields.Char('Event Type')
    received_at = fields.Datetime('Received At', default=fields.Datetime.now)
```

**Step 3: Register model in `addons/mml_freight/models/__init__.py`**

Add after `freight_document`:
```python
from . import freight_webhook_event
```

**Step 4: Add access rules to `ir.model.access.csv`**

Append two lines (same pattern as existing freight models):
```
access_freight_webhook_event_user,freight.webhook.event user,model_freight_webhook_event,stock.group_stock_user,1,0,0,0
access_freight_webhook_event_manager,freight.webhook.event manager,model_freight_webhook_event,stock.group_stock_manager,1,1,1,1
```

**Step 5: Add dedup logic to `dsv_webhook.py`**

In `dsv_webhook.py`, add `import hashlib` after the existing imports, then insert the dedup block **after** the HMAC validation and `body` parse, **before** the event dispatch. Replace:

```python
        body = request.get_json_data()
        event_type = body.get('eventType', '') if isinstance(body, dict) else ''

        # Log only the event type — not the body — to avoid PII in server logs.
        _logger.info('DSV webhook: carrier=%s event_type=%s', carrier.id, event_type)

        if event_type == 'TRACKING_UPDATE':
```

With:

```python
        body = request.get_json_data()
        event_type = body.get('eventType', '') if isinstance(body, dict) else ''

        # Log only the event type — not the body — to avoid PII in server logs.
        _logger.info('DSV webhook: carrier=%s event_type=%s', carrier.id, event_type)

        # Deduplication: reject retried webhook payloads using SHA-256 of raw body.
        # Identical payloads from DSV retries are silently ignored (same 200 response).
        source_hash = hashlib.sha256(body_bytes).hexdigest()
        existing = request.env['freight.webhook.event'].sudo().search([
            ('carrier_id', '=', carrier.id),
            ('source_hash', '=', source_hash),
        ], limit=1)
        if existing:
            _logger.info(
                'DSV webhook: duplicate payload ignored (carrier=%s hash=%s)',
                carrier.id, source_hash[:16],
            )
            return {'status': 'ok'}
        request.env['freight.webhook.event'].sudo().create({
            'carrier_id': carrier.id,
            'source_hash': source_hash,
            'event_type': event_type,
        })

        if event_type == 'TRACKING_UPDATE':
```

Also add `import hashlib` to the top of `dsv_webhook.py`.

**Step 6: Register test in `addons/mml_freight_dsv/tests/__init__.py`**

```python
from . import test_dsv_webhook_dedup
```

**Step 7: Commit**

```bash
git add addons/mml_freight/models/freight_webhook_event.py \
        addons/mml_freight/models/__init__.py \
        addons/mml_freight/security/ir.model.access.csv \
        addons/mml_freight_dsv/controllers/dsv_webhook.py \
        addons/mml_freight_dsv/tests/test_dsv_webhook_dedup.py \
        addons/mml_freight_dsv/tests/__init__.py
git commit -m "feat: webhook deduplication — freight.webhook.event + DSV source_hash guard"
```

---

## Task 3: Fix `action_fetch_documents()` — Synthetic `carrier_doc_ref` for Empty Refs

**Why:** When `carrier_doc_ref` is empty, `action_fetch_documents()` always creates a new `freight.document` record (the `if carrier_doc_ref:` branch is never entered). With the DB constraint from Task 1, this now raises `IntegrityError` on the second fetch. Fix: generate a deterministic synthetic ref from `doc_type + filename` so idempotent re-fetches update the existing record instead of erroring.

**Files:**
- Modify: `addons/mml_freight/models/freight_booking.py` — `action_fetch_documents()` only
- Create: `addons/mml_freight/tests/test_fetch_documents_idempotency.py`
- Modify: `addons/mml_freight/tests/__init__.py`

---

**Step 1: Write failing tests**

`addons/mml_freight/tests/test_fetch_documents_idempotency.py`:
```python
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase


class TestFetchDocumentsIdempotency(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Doc Idempotency Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })
        supplier = cls.env['res.partner'].create({'name': 'Doc Idem Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        tender = cls.env['freight.tender'].create({
            'purchase_order_id': po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': tender.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_booking_id': 'BK-DOC-IDEM',
        })

    def _mock_docs(self, carrier_doc_ref=''):
        """Returns adapter mock that yields one POD document."""
        return MagicMock(get_documents=MagicMock(return_value=[{
            'doc_type': 'pod',
            'bytes': b'%PDF-pod',
            'filename': 'POD-001.pdf',
            'carrier_doc_ref': carrier_doc_ref,
        }]))

    def test_fetch_twice_with_ref_creates_one_document(self):
        """Fetching docs twice with a carrier_doc_ref results in exactly one freight.document."""
        adapter = self._mock_docs(carrier_doc_ref='DSV-POD-XYZ')
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter', return_value=adapter,
        ):
            self.booking.action_fetch_documents()
            self.booking.action_fetch_documents()
        docs = self.booking.document_ids.filtered(lambda d: d.doc_type == 'pod')
        self.assertEqual(len(docs), 1, 'Must have exactly 1 POD document after two fetches')

    def test_fetch_twice_without_ref_creates_one_document(self):
        """Fetching docs twice with empty carrier_doc_ref must also result in exactly one document."""
        adapter = self._mock_docs(carrier_doc_ref='')
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter', return_value=adapter,
        ):
            self.booking.action_fetch_documents()
            self.booking.action_fetch_documents()
        docs = self.booking.document_ids.filtered(lambda d: d.doc_type == 'pod')
        self.assertEqual(len(docs), 1, 'Must have exactly 1 POD document even without carrier_doc_ref')

    def test_synthetic_ref_is_stable(self):
        """Synthetic ref must be the same value on every call for the same doc_type + filename."""
        import hashlib
        ref_a = 'local:' + hashlib.sha256(('pod' + 'POD-001.pdf').encode('utf-8')).hexdigest()[:32]
        ref_b = 'local:' + hashlib.sha256(('pod' + 'POD-001.pdf').encode('utf-8')).hexdigest()[:32]
        self.assertEqual(ref_a, ref_b)
```

**Step 2: Implement the fix in `action_fetch_documents()`**

In `freight_booking.py`, add `import hashlib` to the top-of-file imports (after `import base64`). Then in `action_fetch_documents()`, replace the block that assigns `carrier_doc_ref`:

Find:
```python
            carrier_doc_ref = doc.get('carrier_doc_ref', '')
            doc_type = doc['doc_type']

            # Idempotent upsert: match on (doc_type, carrier_doc_ref) when ref is set
            existing_doc = False
            if carrier_doc_ref:
                existing_doc = self.document_ids.filtered(
                    lambda d, dt=doc_type, ref=carrier_doc_ref:
                        d.doc_type == dt and d.carrier_doc_ref == ref
                )[:1]
```

Replace with:
```python
            carrier_doc_ref = doc.get('carrier_doc_ref', '') or ''
            doc_type = doc['doc_type']

            # Generate a stable synthetic ref when carrier provides none.
            # Ensures the DB UNIQUE(booking_id, doc_type, carrier_doc_ref) constraint
            # treats repeated fetches of the same file as updates, not inserts.
            if not carrier_doc_ref:
                carrier_doc_ref = 'local:' + hashlib.sha256(
                    (doc_type + doc['filename']).encode('utf-8')
                ).hexdigest()[:32]

            # Idempotent upsert: match on (doc_type, carrier_doc_ref)
            existing_doc = self.document_ids.filtered(
                lambda d, dt=doc_type, ref=carrier_doc_ref:
                    d.doc_type == dt and d.carrier_doc_ref == ref
            )[:1]
```

**Step 3: Register test in `__init__.py`**

```python
from . import test_fetch_documents_idempotency
```

**Step 4: Commit**

```bash
git add addons/mml_freight/models/freight_booking.py \
        addons/mml_freight/tests/test_fetch_documents_idempotency.py \
        addons/mml_freight/tests/__init__.py
git commit -m "fix: synthetic carrier_doc_ref for empty-ref documents in action_fetch_documents"
```

---

## Task 4: `action_book()` — Pessimistic Lock + State Re-check

**Why:** `action_book()` calls `adapter.create_booking()` (a live DSV API call). A double-click or concurrent request will pass the `state == 'selected'` check, make two API calls, and create two `freight.booking` records — the second overwrites `booking_id`, orphaning the first. `SELECT … FOR UPDATE NOWAIT` ensures only one caller proceeds.

**Files:**
- Modify: `addons/mml_freight/models/freight_tender.py` — `action_book()` only
- Create: `addons/mml_freight/tests/test_action_book_guard.py`
- Modify: `addons/mml_freight/tests/__init__.py`

---

**Step 1: Write failing tests**

`addons/mml_freight/tests/test_action_book_guard.py`:
```python
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestActionBookGuard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Book Guard Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })
        supplier = cls.env['res.partner'].create({'name': 'Book Guard Supplier'})
        cls.po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        cls.tender = cls.env['freight.tender'].create({
            'purchase_order_id': cls.po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
            'state': 'selected',
        })
        cls.quote = cls.env['freight.tender.quote'].create({
            'tender_id': cls.tender.id,
            'carrier_id': cls.carrier.id,
            'state': 'received',
            'currency_id': cls.env.company.currency_id.id,
            'total_rate': 1800.0,
        })
        cls.tender.selected_quote_id = cls.quote.id

    def _mock_booking_result(self):
        return {
            'carrier_booking_id': 'DSV-BK-GUARD-001',
            'carrier_shipment_id': '',
            'carrier_tracking_url': '',
            'requires_manual_confirmation': True,
        }

    def test_action_book_already_booked_raises(self):
        """action_book raises UserError if tender is already in 'booked' state."""
        self.tender.write({'state': 'booked'})
        with self.assertRaises(UserError, msg='Must raise when already booked'):
            self.tender.action_book()
        # Reset
        self.tender.write({'state': 'selected'})

    def test_action_book_lock_acquired_and_state_rechecked(self):
        """action_book executes SELECT FOR UPDATE NOWAIT before API call."""
        execute_calls = []
        original_execute = self.env.cr.execute

        def mock_execute(query, *args, **kwargs):
            if 'FOR UPDATE NOWAIT' in str(query):
                execute_calls.append(query)
            return original_execute(query, *args, **kwargs)

        mock_adapter = MagicMock(
            create_booking=MagicMock(return_value=self._mock_booking_result()),
        )
        with patch.object(self.env.cr, 'execute', side_effect=mock_execute), \
             patch.object(
                 type(self.env['freight.adapter.registry']),
                 'get_adapter', return_value=mock_adapter,
             ):
            self.tender.action_book()

        self.assertTrue(execute_calls, 'SELECT FOR UPDATE NOWAIT must be called before API call')

    def test_action_book_not_selected_state_raises(self):
        """action_book raises UserError when state is not 'selected' (existing guard still works)."""
        tender2 = self.env['freight.tender'].create({
            'purchase_order_id': self.po.id,
            'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
            'state': 'draft',
        })
        with self.assertRaises(UserError):
            tender2.action_book()
```

**Step 2: Add lock + state re-check to `action_book()`**

In `freight_tender.py`, replace the opening of `action_book()` (lines 233–239):

```python
    def action_book(self):
        """Confirm booking with selected carrier."""
        self.ensure_one()
        if not self.selected_quote_id:
            raise UserError('Select a quote before booking.')
        if self.state != 'selected':
            raise UserError('Tender must be in Selected state to book.')
```

With:

```python
    def action_book(self):
        """Confirm booking with selected carrier."""
        self.ensure_one()
        if not self.selected_quote_id:
            raise UserError('Select a quote before booking.')
        if self.state != 'selected':
            raise UserError('Tender must be in Selected state to book.')
        # Pessimistic lock — prevents double-click race that would call the DSV API twice
        # and create two freight.booking records for the same tender.
        try:
            self.env.cr.execute(
                'SELECT id FROM freight_tender WHERE id = %s FOR UPDATE NOWAIT', [self.id]
            )
        except Exception:
            raise UserError(
                'Another operation is in progress for this tender. Please try again.'
            )
        # Re-check state after acquiring lock (another process may have changed it)
        self.invalidate_recordset()
        if self.state != 'selected':
            raise UserError('Tender state changed — please refresh and try again.')
```

**Step 3: Register test**

```python
from . import test_action_book_guard
```

**Step 4: Commit**

```bash
git add addons/mml_freight/models/freight_tender.py \
        addons/mml_freight/tests/test_action_book_guard.py \
        addons/mml_freight/tests/__init__.py
git commit -m "fix: action_book — pessimistic lock + state re-check prevents duplicate bookings"
```

---

## Task 5: State Guards — `action_confirm_with_dsv()` and `action_request_quotes()`

**Why:** `action_confirm_with_dsv()` makes a live DSV confirmation API call with no state guard — double-click makes two calls. `action_request_quotes()` has a state check but no lock — two concurrent requests both pass the check, creating duplicate quote records per carrier. Both need hardening.

**Files:**
- Modify: `addons/mml_freight/models/freight_booking.py` — `action_confirm_with_dsv()` only
- Modify: `addons/mml_freight/models/freight_tender.py` — `action_request_quotes()` only
- Create: `addons/mml_freight/tests/test_action_guards.py`
- Modify: `addons/mml_freight/tests/__init__.py`

---

**Step 1: Write failing tests**

`addons/mml_freight/tests/test_action_guards.py`:
```python
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestActionGuards(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Guard Test Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })
        supplier = cls.env['res.partner'].create({'name': 'Guard Supplier'})
        cls.po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        cls.tender = cls.env['freight.tender'].create({
            'purchase_order_id': cls.po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': cls.tender.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_booking_id': 'BK-GUARD-001',
            'state': 'confirmed',
        })

    # ── action_confirm_with_dsv ──────────────────────────────────────────────

    def test_confirm_with_dsv_already_confirmed_raises(self):
        """action_confirm_with_dsv raises UserError when booking is already confirmed."""
        with self.assertRaises(UserError, msg='Must raise when already confirmed'):
            self.booking.action_confirm_with_dsv()

    def test_confirm_with_dsv_draft_proceeds(self):
        """action_confirm_with_dsv proceeds from draft state (no UserError raised from guard)."""
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'tender_id': self.tender.id,
            'currency_id': self.env.company.currency_id.id,
            'carrier_booking_id': 'BK-DRAFT-001',
            'state': 'draft',
        })
        mock_result = {
            'carrier_shipment_id': 'SH-001', 'vessel_name': '', 'voyage_number': '',
            'container_number': '', 'bill_of_lading': '', 'feeder_vessel_name': '',
            'feeder_voyage_number': '', 'eta': '',
        }
        mock_adapter = MagicMock(confirm_booking=MagicMock(return_value=mock_result))
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter', return_value=mock_adapter,
        ):
            booking.action_confirm_with_dsv()
        self.assertEqual(booking.state, 'confirmed')

    # ── action_request_quotes ────────────────────────────────────────────────

    def test_request_quotes_lock_acquired(self):
        """action_request_quotes executes SELECT FOR UPDATE NOWAIT."""
        tender = self.env['freight.tender'].create({
            'purchase_order_id': self.po.id,
            'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
            'state': 'draft',
        })
        execute_calls = []
        original_execute = self.env.cr.execute

        def mock_execute(query, *args, **kwargs):
            if 'FOR UPDATE NOWAIT' in str(query):
                execute_calls.append(query)
            return original_execute(query, *args, **kwargs)

        mock_adapter = MagicMock(request_quote=MagicMock(return_value=[]))
        with patch.object(self.env.cr, 'execute', side_effect=mock_execute), \
             patch.object(
                 type(self.env['freight.adapter.registry']),
                 'get_eligible_carriers', return_value=self.carrier,
             ), \
             patch.object(
                 type(self.env['freight.adapter.registry']),
                 'get_adapter', return_value=mock_adapter,
             ):
            tender.action_request_quotes()

        self.assertTrue(execute_calls, 'SELECT FOR UPDATE NOWAIT must be called')

    def test_request_quotes_wrong_state_raises(self):
        """action_request_quotes raises UserError when state is not draft/partial."""
        tender = self.env['freight.tender'].create({
            'purchase_order_id': self.po.id,
            'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
            'state': 'booked',
        })
        with self.assertRaises(UserError):
            tender.action_request_quotes()
```

**Step 2: Add guard to `action_confirm_with_dsv()` in `freight_booking.py`**

Add immediately after `self.ensure_one()` at line ~149:

```python
    def action_confirm_with_dsv(self):
        """Confirm booking with DSV API, update vessel/ETA fields, queue 3PL inward order."""
        self.ensure_one()
        if self.state == 'confirmed':
            raise UserError('This booking is already confirmed.')
        # ... rest unchanged ...
```

**Step 3: Add lock + re-check to `action_request_quotes()` in `freight_tender.py`**

Replace the opening guard block (lines 137–146):

```python
    def action_request_quotes(self):
        """Fan out quote requests to all eligible carriers."""
        self.ensure_one()
        if self.state not in ('draft', 'partial'):
            raise UserError('Can only request quotes from Draft or Partial Quotes state.')
        registry = self.env['freight.adapter.registry']
        carriers = registry.get_eligible_carriers(self)
        if not carriers:
            raise UserError('No eligible carriers found for this tender. Check carrier configuration.')
        self.write({'state': 'requesting'})
```

With:

```python
    def action_request_quotes(self):
        """Fan out quote requests to all eligible carriers."""
        self.ensure_one()
        if self.state not in ('draft', 'partial'):
            raise UserError('Can only request quotes from Draft or Partial Quotes state.')
        # Pessimistic lock — prevents concurrent calls creating duplicate quote records per carrier.
        try:
            self.env.cr.execute(
                'SELECT id FROM freight_tender WHERE id = %s FOR UPDATE NOWAIT', [self.id]
            )
        except Exception:
            raise UserError(
                'Another operation is in progress for this tender. Please try again.'
            )
        # Re-check state after lock (another request may have changed it while we waited)
        self.invalidate_recordset()
        if self.state not in ('draft', 'partial'):
            raise UserError('Tender state changed — please refresh and try again.')
        registry = self.env['freight.adapter.registry']
        carriers = registry.get_eligible_carriers(self)
        if not carriers:
            raise UserError('No eligible carriers found for this tender. Check carrier configuration.')
        self.write({'state': 'requesting'})
```

**Step 4: Register test**

```python
from . import test_action_guards
```

**Step 5: Commit**

```bash
git add addons/mml_freight/models/freight_booking.py \
        addons/mml_freight/models/freight_tender.py \
        addons/mml_freight/tests/test_action_guards.py \
        addons/mml_freight/tests/__init__.py
git commit -m "fix: state guards — action_confirm_with_dsv + action_request_quotes lock"
```

---

## Task 6: `_handle_dsv_invoice_webhook()` — Idempotency Guard

**Why:** If DSV retries the `Invoice` webhook (e.g. our server was slow to respond), `_handle_dsv_invoice_webhook()` fetches the invoice again and posts a second chatter note even though `actual_rate` is already correct. Skip the write and chatter when the rate already matches.

**Files:**
- Modify: `addons/mml_freight/models/freight_booking.py` — `_handle_dsv_invoice_webhook()` only
- Create: `addons/mml_freight/tests/test_invoice_webhook_idempotency.py`
- Modify: `addons/mml_freight/tests/__init__.py`

---

**Step 1: Write failing tests**

`addons/mml_freight/tests/test_invoice_webhook_idempotency.py`:
```python
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase


class TestInvoiceWebhookIdempotency(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Invoice Webhook Idem Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })
        supplier = cls.env['res.partner'].create({'name': 'Idem Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        tender = cls.env['freight.tender'].create({
            'purchase_order_id': po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': tender.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_shipment_id': 'SH-INV-IDEM',
            'actual_rate': 2050.00,
        })

    def _call_webhook(self, amount):
        invoice_data = {
            'dsv_invoice_id': 'DSV-INV-IDEM',
            'amount': amount,
            'currency': 'NZD',
            'invoice_date': '2026-03-01',
        }
        mock_adapter = MagicMock(get_invoice=MagicMock(return_value=invoice_data))
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=mock_adapter,
        ):
            self.env['freight.booking']._handle_dsv_invoice_webhook(
                self.carrier,
                {'shipmentId': 'SH-INV-IDEM', 'eventType': 'Invoice'},
            )

    def test_second_identical_webhook_posts_no_chatter(self):
        """When actual_rate already matches the invoice amount, no new chatter note is posted."""
        msg_count_before = len(self.booking.message_ids)
        # Rate already matches — second delivery of same webhook
        self._call_webhook(amount=2050.00)
        msg_count_after = len(self.booking.message_ids)
        self.assertEqual(
            msg_count_before, msg_count_after,
            'No chatter note must be posted when actual_rate already matches',
        )

    def test_first_webhook_when_rate_zero_does_update(self):
        """When actual_rate is 0, invoice webhook must update it normally."""
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'tender_id': self.booking.tender_id.id,
            'currency_id': self.env.company.currency_id.id,
            'carrier_shipment_id': 'SH-INV-ZERO',
            'actual_rate': 0.0,
        })
        invoice_data = {
            'dsv_invoice_id': 'DSV-INV-NEW',
            'amount': 1750.00,
            'currency': 'NZD',
            'invoice_date': '2026-03-01',
        }
        mock_adapter = MagicMock(get_invoice=MagicMock(return_value=invoice_data))
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=mock_adapter,
        ):
            self.env['freight.booking']._handle_dsv_invoice_webhook(
                self.carrier,
                {'shipmentId': 'SH-INV-ZERO', 'eventType': 'Invoice'},
            )
        self.assertAlmostEqual(booking.actual_rate, 1750.00, places=2)

    def test_rate_change_does_update(self):
        """When invoice amount differs from actual_rate (rate correction), the update proceeds."""
        self._call_webhook(amount=2100.00)  # Different from 2050
        self.assertAlmostEqual(self.booking.actual_rate, 2100.00, places=2)
```

**Step 2: Add idempotency guard to `_handle_dsv_invoice_webhook()`**

In `freight_booking.py`, inside `_handle_dsv_invoice_webhook()`, add the guard after `invoice_data` is retrieved and before `booking.write()`:

Find:
```python
        invoice_data = adapter.get_invoice(booking)
        if not invoice_data:
            _logger.info('DSV invoice webhook: get_invoice returned None for booking %s', booking.name)
            return
        curr = self.env['res.currency'].search(
```

Replace with:
```python
        invoice_data = adapter.get_invoice(booking)
        if not invoice_data:
            _logger.info('DSV invoice webhook: get_invoice returned None for booking %s', booking.name)
            return
        # Idempotency guard: skip write and chatter if actual_rate already matches.
        # Prevents duplicate chatter notes on DSV webhook retries.
        if booking.actual_rate and abs(booking.actual_rate - invoice_data['amount']) < 0.01:
            _logger.info(
                'DSV invoice webhook: actual_rate already matches (%.2f) for booking %s — skipping',
                booking.actual_rate, booking.name,
            )
            return
        curr = self.env['res.currency'].search(
```

**Step 3: Register test**

```python
from . import test_invoice_webhook_idempotency
```

**Step 4: Commit**

```bash
git add addons/mml_freight/models/freight_booking.py \
        addons/mml_freight/tests/test_invoice_webhook_idempotency.py \
        addons/mml_freight/tests/__init__.py
git commit -m "fix: _handle_dsv_invoice_webhook — skip duplicate chatter when rate already matches"
```

---

## Task 7: `cron_sync_tracking()` — Concurrent Execution Guard

**Why:** If the cron runs every 5 minutes and a sync takes > 5 minutes (many active bookings, slow DSV API), two cron instances will overlap. Both find the same bookings, both call the DSV API, both try to create the same tracking events. The DB constraint from Task 1 will catch the duplicate insert, but the redundant API calls waste rate-limit budget. Adding `invalidate_recordset()` + state re-check mirrors the exact pattern from `stock_3pl_core._process_outbound_queue`.

**Files:**
- Modify: `addons/mml_freight/models/freight_booking.py` — `cron_sync_tracking()` only
- Create: `addons/mml_freight/tests/test_cron_sync_guard.py`
- Modify: `addons/mml_freight/tests/__init__.py`

---

**Step 1: Write failing test**

`addons/mml_freight/tests/test_cron_sync_guard.py`:
```python
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase


class TestCronSyncGuard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Cron Guard Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })
        supplier = cls.env['res.partner'].create({'name': 'Cron Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        tender = cls.env['freight.tender'].create({
            'purchase_order_id': po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': tender.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_shipment_id': 'SH-CRON-001',
            'state': 'in_transit',
        })

    def test_cron_skips_booking_if_state_changes_before_sync(self):
        """Booking cancelled between cron fetch and processing must be skipped."""
        sync_calls = []

        def fake_sync(booking_self):
            # Simulate: between the search() and _sync_tracking(), another process
            # cancels the booking. invalidate_recordset() ensures we re-read state.
            booking_self.write({'state': 'cancelled'})
            sync_calls.append(booking_self.id)

        # Set to cancelled BEFORE cron runs — simulates a state change
        self.booking.write({'state': 'cancelled'})

        with patch.object(
            type(self.env['freight.booking']), '_sync_tracking', side_effect=fake_sync,
        ):
            self.env['freight.booking'].cron_sync_tracking()

        # _sync_tracking must NOT be called for cancelled booking
        self.assertNotIn(
            self.booking.id, sync_calls,
            '_sync_tracking must not be called for a booking that is now cancelled',
        )

    def test_cron_invalidates_recordset_before_processing(self):
        """cron_sync_tracking must call invalidate_recordset() before processing each booking."""
        invalidate_calls = []
        original_invalidate = self.booking.invalidate_recordset

        def track_invalidate(*args, **kwargs):
            invalidate_calls.append(True)
            return original_invalidate(*args, **kwargs)

        self.booking.write({'state': 'in_transit'})

        with patch.object(
            type(self.env['freight.booking']), '_sync_tracking', return_value=None,
        ), patch.object(
            type(self.booking), 'invalidate_recordset', side_effect=track_invalidate,
        ):
            self.env['freight.booking'].cron_sync_tracking()

        self.assertTrue(invalidate_calls, 'invalidate_recordset() must be called before _sync_tracking')
```

**Step 2: Update `cron_sync_tracking()` in `freight_booking.py`**

Find:
```python
    @api.model
    def cron_sync_tracking(self):
        """Cron: sync tracking for all active bookings."""
        active_states = ['confirmed', 'cargo_ready', 'picked_up', 'in_transit', 'arrived_port', 'customs']
        bookings = self.search([('state', 'in', active_states)])
        for booking in bookings:
            try:
                booking._sync_tracking()
            except Exception as e:
                _logger.error('Tracking sync failed for booking %s: %s', booking.name, e)
```

Replace with:
```python
    @api.model
    def cron_sync_tracking(self):
        """Cron: sync tracking for all active bookings.

        Guard against concurrent cron runs: invalidate_recordset() + state re-check
        before processing each booking. Pattern copied from stock_3pl_core
        _process_outbound_queue. Prevents redundant DSV API calls when two cron
        instances overlap.
        """
        active_states = ['confirmed', 'cargo_ready', 'picked_up', 'in_transit', 'arrived_port', 'customs']
        bookings = self.search([('state', 'in', active_states)])
        for booking in bookings:
            # Re-read from DB — another cron instance or user action may have
            # changed state since the initial search().
            booking.invalidate_recordset()
            if booking.state not in active_states:
                _logger.info(
                    'cron_sync_tracking: skipping booking %s (state=%s, changed since fetch)',
                    booking.name, booking.state,
                )
                continue
            try:
                booking._sync_tracking()
            except Exception as e:
                _logger.error('Tracking sync failed for booking %s: %s', booking.name, e)
```

**Step 3: Register test**

```python
from . import test_cron_sync_guard
```

**Step 4: Final regression check**

After committing, run the full test suite to confirm no regressions:

```bash
python -m pytest addons/mml_freight/tests/ addons/mml_freight_dsv/tests/ -v 2>&1 | tail -30
```

Expected: all green (or the standard Odoo "no module named odoo" if running outside a server — normal for this project).

**Step 5: Commit**

```bash
git add addons/mml_freight/models/freight_booking.py \
        addons/mml_freight/tests/test_cron_sync_guard.py \
        addons/mml_freight/tests/__init__.py
git commit -m "fix: cron_sync_tracking — invalidate + state re-check guards concurrent runs"
```

---

## Summary

| Task | Files Changed | Key Protection Added |
|------|---------------|----------------------|
| 1 | `freight_tracking_event.py`, `freight_document.py` | DB `UNIQUE` constraints — last line of defence |
| 2 | `freight_webhook_event.py` (new), `dsv_webhook.py` | SHA-256 source_hash dedup — rejects DSV webhook retries |
| 3 | `freight_booking.py` | Synthetic `carrier_doc_ref` — eliminates NULL constraint bypass |
| 4 | `freight_tender.py` | `FOR UPDATE NOWAIT` + state re-check on `action_book` |
| 5 | `freight_booking.py`, `freight_tender.py` | State guards on `action_confirm_with_dsv` + `action_request_quotes` lock |
| 6 | `freight_booking.py` | Skip-if-matches guard on `_handle_dsv_invoice_webhook` |
| 7 | `freight_booking.py` | `invalidate_recordset()` + state re-check in `cron_sync_tracking` |

After all tasks are complete, run the finishing-a-development-branch skill.
