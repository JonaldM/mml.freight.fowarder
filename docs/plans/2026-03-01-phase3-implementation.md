# Phase 3 — Auto-Tender, Webhook Dispatch, Expiry Cron & KPIs

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Complete the tender automation loop: auto-tender on PO confirmation, webhook live dispatch, quote expiry cron, and booking KPI fields.

**Architecture:** Phase 2 built the DSV live adapter and all manual-trigger flows. Phase 3 closes four remaining gaps: (1) PO confirmation triggers quote fan-out automatically; (2) the webhook controller currently validates HMAC but never dispatches — adding `handle_webhook()` to the adapter interface bridges that; (3) a new cron expires stale quotes/tenders; (4) `transit_days_actual` and `on_time` computed fields enable performance tracking.

**Tech Stack:** Odoo 19, Python 3.12, `unittest.mock` for adapter patching, existing `FreightAdapterBase` + `FreightAdapterRegistry` pattern.

**Repo root:** `E:\ClaudeCode\projects\mml.odoo.apps\fowarder.intergration` (referred to as `./`)

---

## Task 1: Auto-Tender on PO Confirmation

**Files:**
- Modify: `addons/mml_freight/models/purchase_order.py`
- Test: `addons/mml_freight/tests/test_auto_tender.py`
- Modify: `addons/mml_freight/tests/__init__.py`

### Context

`purchase.order.button_confirm()` is the standard Odoo method called when a user clicks
**Confirm Order**. We override it: if `freight_responsibility == 'buyer'` and no tender
exists, we call `action_request_freight_tender()` then `tender.action_request_quotes()`.
Failures must NOT block PO confirmation — wrap in try/except and post to chatter.

`action_request_freight_tender()` already exists in `purchase_order.py` and creates the
`freight.tender` record + populates package lines, then returns a window action.
We don't use the return value here.

### Step 1: Write failing test

Create `addons/mml_freight/tests/test_auto_tender.py`:

```python
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase


class TestAutoTender(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supplier = cls.env['res.partner'].create({'name': 'Auto Tender Supplier'})
        incoterm_exw = cls.env['account.incoterms'].search([('code', '=', 'EXW')], limit=1)
        if not incoterm_exw:
            incoterm_exw = cls.env['account.incoterms'].create({'code': 'EXW', 'name': 'EXW'})
        cls.incoterm_exw = incoterm_exw

        incoterm_cif = cls.env['account.incoterms'].search([('code', '=', 'CIF')], limit=1)
        if not incoterm_cif:
            incoterm_cif = cls.env['account.incoterms'].create({'code': 'CIF', 'name': 'CIF'})
        cls.incoterm_cif = incoterm_cif

        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Auto Tender Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'auto_tender': True,
            'delivery_type': 'fixed',
        })

    def _make_po(self, incoterm=None):
        return self.env['purchase.order'].create({
            'partner_id': self.supplier.id,
            'incoterm_id': (incoterm or self.incoterm_exw).id,
        })

    def test_confirm_buyer_incoterm_creates_tender(self):
        """PO confirmation with buyer incoterm auto-creates a freight tender."""
        po = self._make_po(self.incoterm_exw)
        mock_adapter = MagicMock()
        mock_adapter.request_quote.return_value = []
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=mock_adapter,
        ):
            po.button_confirm()
        self.assertTrue(po.freight_tender_id, 'Freight tender should be auto-created on PO confirm')

    def test_confirm_seller_incoterm_no_tender(self):
        """PO with seller incoterm (CIF) should NOT create a freight tender on confirm."""
        po = self._make_po(self.incoterm_cif)
        po.button_confirm()
        self.assertFalse(po.freight_tender_id, 'Seller incoterm should not trigger auto-tender')

    def test_confirm_existing_tender_not_duplicated(self):
        """If a tender already exists, button_confirm must not create a second one."""
        po = self._make_po(self.incoterm_exw)
        existing = self.env['freight.tender'].create({
            'purchase_order_id': po.id,
            'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
        })
        po.freight_tender_id = existing
        mock_adapter = MagicMock()
        mock_adapter.request_quote.return_value = []
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=mock_adapter,
        ):
            po.button_confirm()
        tenders = self.env['freight.tender'].search([('purchase_order_id', '=', po.id)])
        self.assertEqual(len(tenders), 1, 'Must not create duplicate tender')

    def test_confirm_still_succeeds_when_quote_request_errors(self):
        """PO confirm must succeed even if action_request_quotes raises."""
        po = self._make_po(self.incoterm_exw)
        mock_adapter = MagicMock()
        mock_adapter.request_quote.side_effect = Exception('DSV down')
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=mock_adapter,
        ):
            po.button_confirm()   # must not raise
        self.assertEqual(po.state, 'purchase', 'PO must be confirmed despite quote failure')

    def test_confirm_no_incoterm_no_tender(self):
        """PO with no incoterm → freight_responsibility=na → no tender."""
        po = self.env['purchase.order'].create({'partner_id': self.supplier.id})
        po.button_confirm()
        self.assertFalse(po.freight_tender_id)
```

### Step 2: Register test

In `addons/mml_freight/tests/__init__.py`, add:
```python
from . import test_auto_tender
```

### Step 3: Run to verify failure

```
python odoo-bin -d <db> --test-enable -i mml_freight --test-tags TestAutoTender --stop-after-init
```
Expected: `AssertionError: Freight tender should be auto-created on PO confirm`

### Step 4: Implement in `purchase_order.py`

Add these two methods to `PurchaseOrder` class (after `action_request_freight_tender`):

```python
def button_confirm(self):
    """Override: auto-create freight tender when buyer controls the freight leg."""
    result = super().button_confirm()
    for po in self.filtered(lambda p: p.freight_responsibility == 'buyer'
                                      and not p.freight_tender_id):
        po._auto_create_freight_tender()
    return result

def _auto_create_freight_tender(self):
    """Create a freight tender and fan out quote requests. Errors post to chatter."""
    self.ensure_one()
    try:
        self.action_request_freight_tender()   # creates tender + populates packages
        tender = self.freight_tender_id
        if tender:
            tender.action_request_quotes()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(
            'Auto-tender failed for PO %s: %s', self.name, e,
        )
        self.message_post(
            body=(
                f'⚠️ Auto freight tender failed: {e}. '
                f'Please create a tender manually from the Freight tab.'
            ),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
```

### Step 5: Run to verify tests pass

```
python odoo-bin -d <db> --test-enable -i mml_freight --test-tags TestAutoTender --stop-after-init
```
Expected: 5 tests PASS

### Step 6: Commit

```bash
git add addons/mml_freight/models/purchase_order.py \
        addons/mml_freight/tests/test_auto_tender.py \
        addons/mml_freight/tests/__init__.py
git commit -m "feat: auto-create freight tender on PO confirmation when buyer controls freight"
```

---

## Task 2: Webhook Dispatch via Adapter

**Files:**
- Modify: `addons/mml_freight/adapters/base_adapter.py`
- Modify: `addons/mml_freight_dsv/adapters/dsv_mock_adapter.py`
- Modify: `addons/mml_freight/controllers/webhook.py`
- Test: `addons/mml_freight_dsv/tests/test_dsv_webhook_dispatch.py`
- Modify: `addons/mml_freight_dsv/tests/__init__.py`

### Context

The webhook controller (`controllers/webhook.py`) validates the HMAC-SHA256 signature
then returns `{'status': 'ok'}` without doing anything. Dispatch is missing.

Architecture: the controller is generic (lives in `mml_freight`, not DSV-specific).
We add `handle_webhook(body)` to `FreightAdapterBase` (no-op by default) and implement
it in `DsvMockAdapter` by delegating to `freight.booking._handle_dsv_tracking_webhook`.
The controller then calls `adapter.handle_webhook(body)`.

The JSON body is already parsed by Odoo (`type='json'` route) — the controller receives
it as `request.jsonrequest`.

### Step 1: Write failing test

Create `addons/mml_freight_dsv/tests/test_dsv_webhook_dispatch.py`:

```python
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase


class TestDsvWebhookDispatch(TransactionCase):
    """Tests that DsvMockAdapter.handle_webhook() delegates to freight.booking handler."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Dispatch Test',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'dsv_generic',
            'x_dsv_environment': 'demo',
        })

    def _adapter(self):
        from odoo.addons.mml_freight_dsv.adapters.dsv_mock_adapter import DsvMockAdapter
        return DsvMockAdapter(self.carrier, self.env)

    def test_handle_webhook_calls_booking_handler(self):
        """DsvMockAdapter.handle_webhook() must call freight.booking._handle_dsv_tracking_webhook."""
        body = {'shipmentId': 'TEST-SH-001', 'events': []}
        with patch.object(
            type(self.env['freight.booking']),
            '_handle_dsv_tracking_webhook',
        ) as mock_handler:
            self._adapter().handle_webhook(body)
        mock_handler.assert_called_once()
        call_args = mock_handler.call_args
        self.assertEqual(call_args.args[0].id, self.carrier.id)
        self.assertEqual(call_args.args[1], body)

    def test_handle_webhook_base_noop(self):
        """FreightAdapterBase.handle_webhook() is a no-op — must not raise."""
        from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase
        # Use a concrete subclass that doesn't override handle_webhook
        class MinimalAdapter(FreightAdapterBase):
            def request_quote(self, t): return []
            def create_booking(self, t, q): return {}
            def get_tracking(self, b): return []

        adapter = MinimalAdapter(self.carrier, self.env)
        adapter.handle_webhook({'anything': True})  # must not raise
```

### Step 2: Register test

In `addons/mml_freight_dsv/tests/__init__.py`, add:
```python
from . import test_dsv_webhook_dispatch
```

### Step 3: Run to verify failure

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestDsvWebhookDispatch --stop-after-init
```
Expected: `AttributeError: handle_webhook` (method doesn't exist yet)

### Step 4: Add `handle_webhook` to `FreightAdapterBase`

In `addons/mml_freight/adapters/base_adapter.py`, add after `cancel_booking`:

```python
def handle_webhook(self, body):
    """Process an inbound webhook payload from the carrier.

    Default is a no-op. Override in carrier-specific adapters that support webhooks.

    Args:
        body: parsed JSON payload (dict)
    """
    pass
```

### Step 5: Implement `handle_webhook` in `DsvMockAdapter`

In `addons/mml_freight_dsv/adapters/dsv_mock_adapter.py`, add to `DsvMockAdapter`:

```python
def handle_webhook(self, body):
    """Dispatch DSV tracking webhook to freight.booking handler."""
    self.env['freight.booking']._handle_dsv_tracking_webhook(self.carrier, body)
```

### Step 6: Update `controllers/webhook.py` to dispatch

Replace the return statement in `freight_webhook` so the body is parsed and dispatched.
The full controller becomes:

```python
import hmac
import hashlib
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

_SIGNATURE_HEADER = 'X-Freight-Signature'


def _validate_webhook_signature(carrier, body_bytes):
    """Validate HMAC-SHA256 signature. Returns False if secret not configured or sig invalid."""
    secret = carrier.sudo().x_webhook_secret
    if not secret:
        _logger.warning(
            'Webhook rejected for carrier %s: x_webhook_secret not configured', carrier.id
        )
        return False
    sig_header = request.httprequest.headers.get(_SIGNATURE_HEADER, '')
    if not sig_header.startswith('sha256='):
        return False
    received_hex = sig_header[7:]
    expected_hex = hmac.new(
        secret.encode('utf-8'), body_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_hex, received_hex)


class FreightWebhookController(http.Controller):

    @http.route('/freight/webhook/<int:carrier_id>', type='json', auth='none', csrf=False)
    def freight_webhook(self, carrier_id, **kwargs):
        """Generic webhook entry point — dispatches to carrier adapter.

        Security: HMAC-SHA256 signature is validated before any ORM access.
        """
        body_bytes = request.httprequest.get_data()

        carrier = request.env['delivery.carrier'].browse(carrier_id)
        try:
            exists = carrier.exists()
        except Exception:
            exists = False

        if not exists or not _validate_webhook_signature(carrier, body_bytes):
            return {'status': 'ok'}

        _logger.info('Freight webhook validated for carrier %s', carrier_id)

        body = request.jsonrequest or {}
        registry = request.env['freight.adapter.registry'].sudo()
        adapter = registry.get_adapter(carrier.sudo())
        if adapter:
            try:
                adapter.handle_webhook(body)
            except Exception as e:
                _logger.error(
                    'Webhook dispatch error for carrier %s: %s', carrier_id, e,
                )

        return {'status': 'ok'}
```

**Note:** `hmac.new` is actually `hmac.new` — Python's `hmac` module uses `hmac.new()`, not
`hashlib.hmac`. Check: the original code uses `hmac.new(...)` — this is correct Python `hmac`
module usage (`import hmac; hmac.new(key, msg, digestmod)`).

### Step 7: Run tests

```
python odoo-bin -d <db> --test-enable -i mml_freight,mml_freight_dsv --test-tags TestDsvWebhookDispatch --stop-after-init
```
Expected: 2 tests PASS

### Step 8: Commit

```bash
git add addons/mml_freight/adapters/base_adapter.py \
        addons/mml_freight_dsv/adapters/dsv_mock_adapter.py \
        addons/mml_freight/controllers/webhook.py \
        addons/mml_freight_dsv/tests/test_dsv_webhook_dispatch.py \
        addons/mml_freight_dsv/tests/__init__.py
git commit -m "feat: webhook dispatch — handle_webhook() on adapter base, DSV delegate, controller dispatch"
```

---

## Task 3: Quote & Tender Expiry Cron

**Files:**
- Modify: `addons/mml_freight/models/freight_tender.py`
- Modify: `addons/mml_freight/data/ir_cron.xml`
- Test: `addons/mml_freight/tests/test_tender_expiry.py`
- Modify: `addons/mml_freight/tests/__init__.py`

### Context

Two expiry scenarios:
1. **Quote expiry** — `freight.tender.quote.rate_valid_until` has passed: mark that quote
   as `'expired'`. This fires even if the tender itself hasn't expired.
2. **Tender expiry** — `freight.tender.tender_expiry` has passed and the tender is still
   in an open state (`requesting`/`quoted`/`partial`): mark all pending quotes as
   `'expired'` and move the tender to `'expired'` state.

The cron runs every hour. It finds open tenders past their expiry datetime.

### Step 1: Write failing test

Create `addons/mml_freight/tests/test_tender_expiry.py`:

```python
from odoo.tests.common import TransactionCase
from odoo import fields
import datetime


class TestTenderExpiry(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        supplier = cls.env['res.partner'].create({'name': 'Expiry Test Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        cls.po = po
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Expiry Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
        })
        cls.nzd = (
            cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1)
            or cls.env.company.currency_id
        )

    def _make_tender(self, state='quoted', expiry_offset_hours=-1):
        expiry = fields.Datetime.now() + datetime.timedelta(hours=expiry_offset_hours)
        tender = self.env['freight.tender'].create({
            'purchase_order_id': self.po.id,
            'company_id':        self.env.company.id,
            'currency_id':       self.nzd.id,
            'state':             state,
            'tender_expiry':     expiry,
        })
        return tender

    def _add_quote(self, tender, state='pending', rate_valid_until=None):
        return self.env['freight.tender.quote'].create({
            'tender_id':       tender.id,
            'carrier_id':      self.carrier.id,
            'state':           state,
            'currency_id':     self.nzd.id,
            'rate_valid_until': rate_valid_until,
        })

    def test_expired_tender_marked_expired(self):
        """cron_expire_tenders() moves past-expiry open tenders to 'expired' state."""
        tender = self._make_tender(state='quoted', expiry_offset_hours=-2)
        self.env['freight.tender'].cron_expire_tenders()
        self.assertEqual(tender.state, 'expired')

    def test_pending_quotes_on_expired_tender_become_expired(self):
        """Pending quotes on an expired tender get state='expired'."""
        tender = self._make_tender(state='requesting', expiry_offset_hours=-1)
        q = self._add_quote(tender, state='pending')
        self.env['freight.tender'].cron_expire_tenders()
        self.assertEqual(q.state, 'expired')

    def test_received_quotes_not_touched_on_tender_expiry(self):
        """Already-received quotes on an expired tender keep state='received'."""
        tender = self._make_tender(state='quoted', expiry_offset_hours=-1)
        q = self._add_quote(tender, state='received')
        self.env['freight.tender'].cron_expire_tenders()
        self.assertEqual(q.state, 'received')

    def test_future_tender_not_expired(self):
        """Tender with future expiry stays in its current state."""
        tender = self._make_tender(state='quoted', expiry_offset_hours=+24)
        self.env['freight.tender'].cron_expire_tenders()
        self.assertEqual(tender.state, 'quoted')

    def test_booked_tender_not_expired(self):
        """Booked tenders must not be expired even if expiry has passed."""
        tender = self._make_tender(state='booked', expiry_offset_hours=-1)
        self.env['freight.tender'].cron_expire_tenders()
        self.assertEqual(tender.state, 'booked')

    def test_individual_quote_expiry(self):
        """Quotes past their rate_valid_until are expired even if tender is still open."""
        tender = self._make_tender(state='quoted', expiry_offset_hours=+24)  # tender still valid
        past = fields.Datetime.now() - datetime.timedelta(hours=2)
        q = self._add_quote(tender, state='received', rate_valid_until=past)
        self.env['freight.tender'].cron_expire_tenders()
        self.assertEqual(q.state, 'expired')

    def test_future_quote_not_expired(self):
        """Quote with future rate_valid_until stays received."""
        tender = self._make_tender(state='quoted', expiry_offset_hours=+24)
        future = fields.Datetime.now() + datetime.timedelta(hours=48)
        q = self._add_quote(tender, state='received', rate_valid_until=future)
        self.env['freight.tender'].cron_expire_tenders()
        self.assertEqual(q.state, 'received')
```

### Step 2: Register test

In `addons/mml_freight/tests/__init__.py`, add:
```python
from . import test_tender_expiry
```

### Step 3: Run to verify failure

```
python odoo-bin -d <db> --test-enable -i mml_freight --test-tags TestTenderExpiry --stop-after-init
```
Expected: `AttributeError: 'freight.tender' object has no attribute 'cron_expire_tenders'`

### Step 4: Implement `cron_expire_tenders` in `freight_tender.py`

Add this method to `FreightTender` class (after `action_cancel`):

```python
@api.model
def cron_expire_tenders(self):
    """Hourly cron: expire overdue quotes and tenders.

    Two passes:
    1. Expire individual quotes where rate_valid_until < now().
    2. Expire full tenders where tender_expiry < now() and still open.
    """
    now = fields.Datetime.now()

    # Pass 1: individual quote expiry regardless of tender state
    stale_quotes = self.env['freight.tender.quote'].search([
        ('state', 'in', ('pending', 'received')),
        ('rate_valid_until', '<', now),
        ('rate_valid_until', '!=', False),
    ])
    if stale_quotes:
        stale_quotes.write({'state': 'expired'})
        _logger.info('Freight cron: expired %d stale quotes', len(stale_quotes))

    # Pass 2: tender-level expiry
    open_states = ['requesting', 'quoted', 'partial']
    overdue = self.search([
        ('state', 'in', open_states),
        ('tender_expiry', '<', now),
        ('tender_expiry', '!=', False),
    ])
    for tender in overdue:
        # Expire remaining pending quotes
        pending = tender.quote_line_ids.filtered(
            lambda q: q.state in ('pending',)
        )
        if pending:
            pending.write({'state': 'expired'})
        tender.write({'state': 'expired'})
        _logger.info('Freight cron: tender %s expired', tender.name)
```

Make sure `_logger` is already imported at the top of the file. Check — it should be there
from existing code. If not, add `import logging` and `_logger = logging.getLogger(__name__)`.

### Step 5: Add cron to `ir_cron.xml`

In `addons/mml_freight/data/ir_cron.xml`, add inside `<odoo>` after the existing crons:

```xml
<record id="cron_freight_tender_expiry" model="ir.cron">
    <field name="name">Freight: Expire Overdue Quotes and Tenders</field>
    <field name="model_id" ref="model_freight_tender"/>
    <field name="state">code</field>
    <field name="code">model.cron_expire_tenders()</field>
    <field name="interval_number">1</field>
    <field name="interval_type">hours</field>
    <field name="numbercall">-1</field>
    <field name="active">True</field>
</record>
```

### Step 6: Run tests

```
python odoo-bin -d <db> --test-enable -i mml_freight --test-tags TestTenderExpiry --stop-after-init
```
Expected: 7 tests PASS

### Step 7: Commit

```bash
git add addons/mml_freight/models/freight_tender.py \
        addons/mml_freight/data/ir_cron.xml \
        addons/mml_freight/tests/test_tender_expiry.py \
        addons/mml_freight/tests/__init__.py
git commit -m "feat: cron_expire_tenders() — expire stale quotes and overdue tenders hourly"
```

---

## Task 4: Booking KPI Fields — `transit_days_actual` + `on_time`

**Files:**
- Modify: `addons/mml_freight/models/freight_booking.py`
- Modify: `addons/mml_freight/views/freight_booking_views.xml`
- Test: `addons/mml_freight/tests/test_booking_kpis.py`
- Modify: `addons/mml_freight/tests/__init__.py`

### Context

Two computed fields on `freight.booking`:

- **`transit_days_actual`** — `float` — days between `actual_pickup_date` and
  `actual_delivery_date`. 0.0 when either is unset.
- **`on_time`** — `bool` — `True` when `actual_delivery_date <= eta` (if ETA set),
  else `actual_delivery_date <= tender_id.requested_delivery_date`.
  `False` when delivery date not yet set.

Both stored — they appear in list views and can be used in future dashboards.

### Step 1: Write failing test

Create `addons/mml_freight/tests/test_booking_kpis.py`:

```python
import datetime
from odoo.tests.common import TransactionCase
from odoo import fields


class TestBookingKPIs(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supplier = cls.env['res.partner'].create({'name': 'KPI Supplier'})
        cls.po = cls.env['purchase.order'].create({'partner_id': cls.supplier.id})
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'KPI Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
        })
        cls.nzd = (
            cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1)
            or cls.env.company.currency_id
        )
        cls.base_dt = datetime.datetime(2026, 6, 1, 0, 0, 0)

    def _make_booking(self, pickup=None, delivery=None, eta=None):
        vals = {
            'carrier_id':       self.carrier.id,
            'currency_id':      self.nzd.id,
            'purchase_order_id': self.po.id,
        }
        if pickup:
            vals['actual_pickup_date'] = pickup
        if delivery:
            vals['actual_delivery_date'] = delivery
        if eta:
            vals['eta'] = eta
        return self.env['freight.booking'].create(vals)

    def test_transit_days_computed_from_dates(self):
        pickup   = self.base_dt
        delivery = pickup + datetime.timedelta(days=14)
        booking  = self._make_booking(pickup=pickup, delivery=delivery)
        self.assertAlmostEqual(booking.transit_days_actual, 14.0, places=1)

    def test_transit_days_zero_when_no_pickup(self):
        booking = self._make_booking(delivery=self.base_dt)
        self.assertEqual(booking.transit_days_actual, 0.0)

    def test_transit_days_zero_when_no_delivery(self):
        booking = self._make_booking(pickup=self.base_dt)
        self.assertEqual(booking.transit_days_actual, 0.0)

    def test_on_time_true_when_delivery_before_eta(self):
        eta      = self.base_dt + datetime.timedelta(days=15)
        delivery = self.base_dt + datetime.timedelta(days=14)
        booking  = self._make_booking(delivery=delivery, eta=eta)
        self.assertTrue(booking.on_time)

    def test_on_time_false_when_delivery_after_eta(self):
        eta      = self.base_dt + datetime.timedelta(days=10)
        delivery = self.base_dt + datetime.timedelta(days=12)
        booking  = self._make_booking(delivery=delivery, eta=eta)
        self.assertFalse(booking.on_time)

    def test_on_time_false_when_no_delivery(self):
        booking = self._make_booking(eta=self.base_dt + datetime.timedelta(days=10))
        self.assertFalse(booking.on_time)

    def test_on_time_uses_requested_delivery_when_no_eta(self):
        """Falls back to tender.requested_delivery_date when booking.eta is unset."""
        requested = (self.base_dt + datetime.timedelta(days=20)).date()
        tender = self.env['freight.tender'].create({
            'purchase_order_id':     self.po.id,
            'company_id':            self.env.company.id,
            'currency_id':           self.nzd.id,
            'requested_delivery_date': requested,
        })
        delivery = self.base_dt + datetime.timedelta(days=18)
        booking  = self.env['freight.booking'].create({
            'carrier_id':       self.carrier.id,
            'currency_id':      self.nzd.id,
            'purchase_order_id': self.po.id,
            'tender_id':        tender.id,
            'actual_delivery_date': delivery,
        })
        self.assertTrue(booking.on_time)
```

### Step 2: Register test

In `addons/mml_freight/tests/__init__.py`, add:
```python
from . import test_booking_kpis
```

### Step 3: Run to verify failure

```
python odoo-bin -d <db> --test-enable -i mml_freight --test-tags TestBookingKPIs --stop-after-init
```
Expected: `AttributeError` (fields don't exist yet)

### Step 4: Add fields to `freight_booking.py`

Find the `FreightBooking` class fields section (around the `eta`, `actual_pickup_date`,
`actual_delivery_date` fields). Add after `actual_delivery_date`:

```python
transit_days_actual = fields.Float(
    'Actual Transit Days',
    compute='_compute_transit_kpis',
    store=True,
    digits=(6, 1),
    help='Days between actual pickup and actual delivery.',
)
on_time = fields.Boolean(
    'On Time',
    compute='_compute_transit_kpis',
    store=True,
    help='True when actual delivery <= ETA (or requested delivery date if no ETA).',
)
```

Then add the compute method (anywhere after the field definitions, before `cron_sync_tracking`):

```python
@api.depends('actual_pickup_date', 'actual_delivery_date', 'eta',
             'tender_id.requested_delivery_date')
def _compute_transit_kpis(self):
    for booking in self:
        pickup   = booking.actual_pickup_date
        delivery = booking.actual_delivery_date

        if pickup and delivery:
            delta = delivery - pickup
            booking.transit_days_actual = delta.total_seconds() / 86400
        else:
            booking.transit_days_actual = 0.0

        if not delivery:
            booking.on_time = False
        elif booking.eta:
            booking.on_time = delivery <= booking.eta
        else:
            req = booking.tender_id.requested_delivery_date
            if req:
                # Compare datetime to date: convert req to datetime at midnight
                req_dt = fields.Datetime.from_string(str(req) + ' 23:59:59')
                booking.on_time = delivery <= req_dt
            else:
                booking.on_time = False
```

### Step 5: Add to booking form view

In `addons/mml_freight/views/freight_booking_views.xml`, inside the booking form's
`<group string="Tracking">` group (which already has `eta`, `actual_pickup_date`,
`actual_delivery_date`), add after `actual_delivery_date`:

```xml
<field name="transit_days_actual" readonly="1"/>
<field name="on_time" widget="boolean_toggle" readonly="1"/>
```

Also add `transit_days_actual` and `on_time` to the list view as optional columns.
Find `<field name="actual_delivery_date"` in the list view and add after it:

```xml
<field name="transit_days_actual" string="Transit Days" optional="hide"/>
<field name="on_time" widget="boolean" optional="hide"/>
```

### Step 6: Run tests

```
python odoo-bin -d <db> --test-enable -i mml_freight --test-tags TestBookingKPIs --stop-after-init
```
Expected: 7 tests PASS

### Step 7: Commit

```bash
git add addons/mml_freight/models/freight_booking.py \
        addons/mml_freight/views/freight_booking_views.xml \
        addons/mml_freight/tests/test_booking_kpis.py \
        addons/mml_freight/tests/__init__.py
git commit -m "feat: transit_days_actual and on_time KPI fields on freight.booking"
```

---

## Task 5: Security — Add `ir.model.access.csv` Entries for New Tests

**Files:**
- Review: `addons/mml_freight/security/ir.model.access.csv`

### Context

This is a verification step only. Run all new tests together to catch any model access
errors (`AccessError`) that indicate missing ACL rows for `freight.tender.quote` or other
models used in the new tests.

### Step 1: Run all Phase 3 tests together

```
python odoo-bin -d <db> --test-enable -i mml_freight,mml_freight_dsv \
  --test-tags TestAutoTender,TestDsvWebhookDispatch,TestTenderExpiry,TestBookingKPIs \
  --stop-after-init
```
Expected: All pass. If any `AccessError` — read the error, find the missing model, add
a row to `addons/mml_freight/security/ir.model.access.csv`.

Standard access row format:
```
id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink
```
Example (allow all stock users to read freight.tender.quote):
```
access_freight_tender_quote_user,freight.tender.quote user,model_freight_tender_quote,stock.group_stock_user,1,1,1,0
```

### Step 2: Commit if any ACL rows were added

```bash
git add addons/mml_freight/security/ir.model.access.csv
git commit -m "fix: add missing ir.model.access rows for Phase 3 models"
```

If no changes needed, skip the commit.

---

## Summary

| Task | Feature | Models / Files |
|------|---------|----------------|
| 1 | Auto-tender on PO confirm | `purchase_order.py` + `test_auto_tender.py` |
| 2 | Webhook dispatch via adapter | `base_adapter.py` + `dsv_mock_adapter.py` + `webhook.py` + `test_dsv_webhook_dispatch.py` |
| 3 | Quote & tender expiry cron | `freight_tender.py` + `ir_cron.xml` + `test_tender_expiry.py` |
| 4 | Booking KPI fields | `freight_booking.py` + `freight_booking_views.xml` + `test_booking_kpis.py` |
| 5 | ACL verification | `ir.model.access.csv` (if needed) |

Phase 4 will cover: DSV label/document fetching, POD attachment, and landed cost integration (`stock.landed.cost`).
