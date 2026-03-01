# Carrier Contract Commitment Awareness — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `freight.carrier.contract` model to `mml_freight` so freight forwarder commitments (volume + rate) are tracked, utilization computed from confirmed bookings, and a new `contract_aware` selection mode on tenders surfaces opportunity cost vs market rates.

**Architecture:** New `freight.carrier.contract` model holds one contract record per carrier-period. `freight.booking` gets `contract_id`/`unit_quantity` so confirmed bookings deduct from commitment. `freight.tender.quote` gains computed contract and opportunity-cost fields. `freight.tender.action_auto_select` gains a `contract_aware` branch. All lives in `mml_freight` — no new module.

**Tech Stack:** Odoo 19, Python, ORM computed fields (`@api.depends`), `TransactionCase` tests, XML views.

**Design doc:** `docs/plans/2026-03-02-carrier-contract-commitment-design.md`

---

## Task 1: `freight.carrier.contract` model — core fields

**Files:**
- Create: `addons/mml_freight/models/freight_carrier_contract.py`
- Modify: `addons/mml_freight/models/__init__.py`
- Create: `addons/mml_freight/tests/test_carrier_contract.py`

### Step 1: Write the failing test

```python
# addons/mml_freight/tests/test_carrier_contract.py
from odoo.tests.common import TransactionCase
from odoo import fields

class TestCarrierContract(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        prod = cls.env['product.product'].search([], limit=1)
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Test',
            'product_id': prod.id,
            'delivery_type': 'fixed',
        })
        cls.nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or cls.env.company.currency_id

    def _make_contract(self, **kwargs):
        today = fields.Date.today()
        defaults = {
            'name': 'DSV 2026 FCL',
            'carrier_id': self.carrier.id,
            'date_start': today,
            'date_end': today.replace(year=today.year + 1),
            'commitment_unit': 'teu',
            'committed_quantity': 20.0,
            'contracted_rate': 2500.0,
            'contracted_rate_currency_id': self.nzd.id,
        }
        defaults.update(kwargs)
        return self.env['freight.carrier.contract'].create(defaults)

    def test_create_contract(self):
        c = self._make_contract()
        self.assertEqual(c.name, 'DSV 2026 FCL')
        self.assertEqual(c.committed_quantity, 20.0)
        self.assertEqual(c.commitment_unit, 'teu')

    def test_is_active_true(self):
        c = self._make_contract()
        self.assertTrue(c.is_active)

    def test_is_active_false_future(self):
        today = fields.Date.today()
        c = self._make_contract(
            date_start=today.replace(year=today.year + 1),
            date_end=today.replace(year=today.year + 2),
        )
        self.assertFalse(c.is_active)

    def test_is_active_false_expired(self):
        today = fields.Date.today()
        c = self._make_contract(
            date_start=today.replace(year=today.year - 2),
            date_end=today.replace(year=today.year - 1),
        )
        self.assertFalse(c.is_active)
```

### Step 2: Run test to verify it fails

```bash
cd addons && python -m pytest mml_freight/tests/test_carrier_contract.py -v 2>&1 | head -30
```
Expected: ImportError or AttributeError — `freight.carrier.contract` does not exist yet.

### Step 3: Create the model

```python
# addons/mml_freight/models/freight_carrier_contract.py
from odoo import models, fields, api

COMMITMENT_UNITS = [
    ('teu', 'TEU (containers)'),
    ('weight_kg', 'Weight (kg)'),
    ('shipment_count', 'Shipments'),
]


class FreightCarrierContract(models.Model):
    _name = 'freight.carrier.contract'
    _description = 'Freight Carrier Contract'
    _inherit = ['mail.thread']
    _order = 'date_start desc'

    name = fields.Char('Contract Name', required=True)
    carrier_id = fields.Many2one(
        'delivery.carrier', string='Carrier', required=True, ondelete='restrict', index=True,
    )
    date_start = fields.Date('Start Date', required=True)
    date_end = fields.Date('End Date', required=True)
    commitment_unit = fields.Selection(COMMITMENT_UNITS, string='Commitment Unit', required=True, default='teu')
    committed_quantity = fields.Float('Committed Quantity', required=True, digits=(10, 2))
    contracted_rate = fields.Monetary('Contracted Rate (per unit)', currency_field='contracted_rate_currency_id')
    contracted_rate_currency_id = fields.Many2one('res.currency', string='Rate Currency', required=True)
    notes = fields.Text('Notes')

    is_active = fields.Boolean(
        'Active', compute='_compute_is_active', store=True,
        help='True when today falls within the contract period.',
    )

    @api.depends('date_start', 'date_end')
    def _compute_is_active(self):
        today = fields.Date.today()
        for c in self:
            c.is_active = bool(c.date_start and c.date_end and c.date_start <= today <= c.date_end)
```

### Step 4: Register in `__init__.py`

Add `from . import freight_carrier_contract` after the existing imports in `addons/mml_freight/models/__init__.py`.

### Step 5: Run tests

```bash
cd addons && python -m pytest mml_freight/tests/test_carrier_contract.py -v
```
Expected: 4 tests PASS.

### Step 6: Commit

```bash
git add addons/mml_freight/models/freight_carrier_contract.py \
        addons/mml_freight/models/__init__.py \
        addons/mml_freight/tests/test_carrier_contract.py
git commit -m "feat(mml_freight): freight.carrier.contract model with is_active"
```

---

## Task 2: `freight.booking` unit tracking fields

**Files:**
- Modify: `addons/mml_freight/models/freight_booking.py`
- Create: `addons/mml_freight/tests/test_booking_unit_tracking.py`

### Step 1: Write failing tests

```python
# addons/mml_freight/tests/test_booking_unit_tracking.py
from odoo.tests.common import TransactionCase
from odoo import fields

class TestBookingUnitTracking(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        prod = cls.env['product.product'].search([], limit=1)
        cls.nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or cls.env.company.currency_id
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Track',
            'product_id': prod.id,
            'delivery_type': 'fixed',
        })
        today = fields.Date.today()
        cls.contract = cls.env['freight.carrier.contract'].create({
            'name': 'DSV Track Contract',
            'carrier_id': cls.carrier.id,
            'date_start': today,
            'date_end': today.replace(year=today.year + 1),
            'commitment_unit': 'teu',
            'committed_quantity': 20.0,
            'contracted_rate': 2500.0,
            'contracted_rate_currency_id': cls.nzd.id,
        })
        partner = cls.env['res.partner'].create({'name': 'BT Supplier'})
        cls.po = cls.env['purchase.order'].create({'partner_id': partner.id})

    def _make_booking(self, **kwargs):
        defaults = {
            'carrier_id': self.carrier.id,
            'currency_id': self.nzd.id,
            'transport_mode': 'sea_fcl',
        }
        defaults.update(kwargs)
        return self.env['freight.booking'].create(defaults)

    def test_booking_has_unit_fields(self):
        b = self._make_booking()
        self.assertIsNotNone(b.unit_quantity)
        self.assertIsNotNone(b.unit_type)
        # contract_id is optional — None by default
        self.assertFalse(b.contract_id)

    def test_unit_type_default_sea_fcl(self):
        b = self._make_booking(transport_mode='sea_fcl')
        self.assertEqual(b.unit_type, 'teu')

    def test_unit_type_air(self):
        b = self._make_booking(transport_mode='air')
        self.assertEqual(b.unit_type, 'weight_kg')

    def test_unit_type_road(self):
        b = self._make_booking(transport_mode='road')
        self.assertEqual(b.unit_type, 'shipment_count')

    def test_contract_id_linkable(self):
        b = self._make_booking(contract_id=self.contract.id, unit_quantity=2.0)
        self.assertEqual(b.contract_id, self.contract)
        self.assertEqual(b.unit_quantity, 2.0)
```

### Step 2: Run to verify failure

```bash
cd addons && python -m pytest mml_freight/tests/test_booking_unit_tracking.py -v 2>&1 | head -20
```
Expected: AttributeError — `unit_quantity`, `unit_type`, `contract_id` do not exist on `freight.booking`.

### Step 3: Add fields to `freight_booking.py`

In `addons/mml_freight/models/freight_booking.py`, add these fields to the `FreightBooking` class after the `transport_mode` field:

```python
    # Contract commitment tracking
    contract_id = fields.Many2one(
        'freight.carrier.contract',
        string='Carrier Contract',
        ondelete='set null',
        index=True,
        help='Contract this booking counts against. Set at booking time when contract_aware tender selection is used.',
    )
    unit_quantity = fields.Float(
        'Contract Units',
        digits=(10, 3),
        help='Quantity consumed against the contract (TEU, kg, or shipments).',
    )
    unit_type = fields.Selection(
        [('teu', 'TEU'), ('weight_kg', 'Weight (kg)'), ('shipment_count', 'Shipments')],
        string='Unit Type',
        compute='_compute_unit_type',
        store=True,
        help='Mirrors the contract commitment_unit for the active transport mode.',
    )

    @api.depends('transport_mode')
    def _compute_unit_type(self):
        mode_map = {
            'sea_fcl': 'teu',
            'sea_lcl': 'weight_kg',
            'air': 'weight_kg',
            'road': 'shipment_count',
            'rail': 'shipment_count',
            'express': 'shipment_count',
        }
        for b in self:
            b.unit_type = mode_map.get(b.transport_mode or '', 'shipment_count')
```

### Step 4: Run tests

```bash
cd addons && python -m pytest mml_freight/tests/test_booking_unit_tracking.py -v
```
Expected: 5 tests PASS.

### Step 5: Commit

```bash
git add addons/mml_freight/models/freight_booking.py \
        addons/mml_freight/tests/test_booking_unit_tracking.py
git commit -m "feat(mml_freight): contract tracking fields on freight.booking"
```

---

## Task 3: Contract utilization computed fields

**Files:**
- Modify: `addons/mml_freight/models/freight_carrier_contract.py`
- Modify: `addons/mml_freight/tests/test_carrier_contract.py`

### Step 1: Add failing utilization tests to `test_carrier_contract.py`

Append these test methods to `TestCarrierContract`:

```python
    def _make_booking_for_contract(self, contract, unit_quantity=1.0, state='confirmed'):
        """Helper: create a freight.booking linked to a contract in a given state."""
        prod = self.env['product.product'].search([], limit=1)
        carrier = contract.carrier_id
        nzd = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or self.env.company.currency_id
        b = self.env['freight.booking'].create({
            'carrier_id': carrier.id,
            'currency_id': nzd.id,
            'transport_mode': 'sea_fcl',
            'contract_id': contract.id,
            'unit_quantity': unit_quantity,
        })
        b.write({'state': state})
        return b

    def test_utilized_zero_when_no_bookings(self):
        c = self._make_contract()
        self.assertAlmostEqual(c.utilized_quantity, 0.0)
        self.assertAlmostEqual(c.remaining_quantity, 20.0)
        self.assertAlmostEqual(c.utilization_pct, 0.0)

    def test_utilized_counts_confirmed_booking(self):
        c = self._make_contract()
        self._make_booking_for_contract(c, unit_quantity=5.0, state='confirmed')
        c.invalidate_recordset()
        self.assertAlmostEqual(c.utilized_quantity, 5.0)
        self.assertAlmostEqual(c.remaining_quantity, 15.0)
        self.assertAlmostEqual(c.utilization_pct, 25.0)

    def test_utilized_counts_delivered_booking(self):
        c = self._make_contract()
        self._make_booking_for_contract(c, unit_quantity=3.0, state='delivered')
        c.invalidate_recordset()
        self.assertAlmostEqual(c.utilized_quantity, 3.0)

    def test_cancelled_booking_not_counted(self):
        c = self._make_contract()
        self._make_booking_for_contract(c, unit_quantity=10.0, state='cancelled')
        c.invalidate_recordset()
        self.assertAlmostEqual(c.utilized_quantity, 0.0)

    def test_draft_booking_not_counted(self):
        c = self._make_contract()
        self._make_booking_for_contract(c, unit_quantity=10.0, state='draft')
        c.invalidate_recordset()
        self.assertAlmostEqual(c.utilized_quantity, 0.0)

    def test_utilization_pct_full(self):
        c = self._make_contract()
        self._make_booking_for_contract(c, unit_quantity=20.0, state='confirmed')
        c.invalidate_recordset()
        self.assertAlmostEqual(c.utilization_pct, 100.0)
```

### Step 2: Run to verify failure

```bash
cd addons && python -m pytest mml_freight/tests/test_carrier_contract.py::TestCarrierContract::test_utilized_zero_when_no_bookings -v
```
Expected: AttributeError — `utilized_quantity` does not exist.

### Step 3: Add utilization fields to `freight_carrier_contract.py`

Add these fields and method to `FreightCarrierContract` after the `is_active` field:

```python
    utilized_quantity = fields.Float(
        'Utilized', compute='_compute_utilization', store=False, digits=(10, 2),
        help='Sum of unit_quantity across confirmed/in-transit/delivered bookings in this contract period.',
    )
    remaining_quantity = fields.Float(
        'Remaining', compute='_compute_utilization', store=False, digits=(10, 2),
    )
    utilization_pct = fields.Float(
        'Utilization %', compute='_compute_utilization', store=False, digits=(5, 1),
    )

    ACTIVE_BOOKING_STATES = ['confirmed', 'cargo_ready', 'picked_up', 'in_transit',
                              'arrived_port', 'customs', 'delivered', 'received']

    def _compute_utilization(self):
        for contract in self:
            if not contract.id:
                contract.utilized_quantity = 0.0
                contract.remaining_quantity = contract.committed_quantity
                contract.utilization_pct = 0.0
                continue
            bookings = self.env['freight.booking'].search([
                ('contract_id', '=', contract.id),
                ('state', 'in', self.ACTIVE_BOOKING_STATES),
            ])
            utilized = sum(bookings.mapped('unit_quantity'))
            committed = contract.committed_quantity or 1.0
            contract.utilized_quantity = utilized
            contract.remaining_quantity = contract.committed_quantity - utilized
            contract.utilization_pct = utilized / committed * 100
```

### Step 4: Run all contract tests

```bash
cd addons && python -m pytest mml_freight/tests/test_carrier_contract.py -v
```
Expected: All tests PASS (4 from task 1 + 6 new = 10 total).

### Step 5: Commit

```bash
git add addons/mml_freight/models/freight_carrier_contract.py \
        addons/mml_freight/tests/test_carrier_contract.py
git commit -m "feat(mml_freight): utilization computed fields on freight.carrier.contract"
```

---

## Task 4: Quote contract fields + opportunity cost

**Files:**
- Modify: `addons/mml_freight/models/freight_tender_quote.py`
- Create: `addons/mml_freight/tests/test_contract_opportunity_cost.py`

### Step 1: Write failing tests

```python
# addons/mml_freight/tests/test_contract_opportunity_cost.py
from odoo.tests.common import TransactionCase
from odoo import fields

class TestContractOpportunityCost(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        prod = cls.env['product.product'].search([], limit=1)
        cls.nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or cls.env.company.currency_id
        cls.carrier_dsv = cls.env['delivery.carrier'].create({
            'name': 'DSV OC',
            'product_id': prod.id,
            'delivery_type': 'fixed',
        })
        cls.carrier_kn = cls.env['delivery.carrier'].create({
            'name': 'KN OC',
            'product_id': prod.id,
            'delivery_type': 'fixed',
        })
        today = fields.Date.today()
        cls.contract = cls.env['freight.carrier.contract'].create({
            'name': 'DSV OC Contract',
            'carrier_id': cls.carrier_dsv.id,
            'date_start': today,
            'date_end': today.replace(year=today.year + 1),
            'commitment_unit': 'teu',
            'committed_quantity': 20.0,
            'contracted_rate': 3000.0,      # $3000/TEU contracted
            'contracted_rate_currency_id': cls.nzd.id,
        })
        partner = cls.env['res.partner'].create({'name': 'OC Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': partner.id})
        cls.tender = cls.env['freight.tender'].create({
            'po_ids': [(4, po.id)],
            'company_id': cls.env.company.id,
            'currency_id': cls.nzd.id,
            'state': 'quoted',
            'freight_mode_preference': 'sea',
        })

    def _make_quote(self, carrier, base_rate, transit_days=14, mode='sea_fcl'):
        return self.env['freight.tender.quote'].create({
            'tender_id': self.tender.id,
            'carrier_id': carrier.id,
            'state': 'received',
            'currency_id': self.nzd.id,
            'transport_mode': mode,
            'base_rate': base_rate,
            'estimated_transit_days': transit_days,
        })

    def test_is_contract_carrier_true_for_dsv(self):
        q = self._make_quote(self.carrier_dsv, base_rate=2800.0)
        self.assertTrue(q.is_contract_carrier)

    def test_is_contract_carrier_false_for_kn(self):
        q = self._make_quote(self.carrier_kn, base_rate=2600.0)
        self.assertFalse(q.is_contract_carrier)

    def test_contract_id_resolved(self):
        q = self._make_quote(self.carrier_dsv, base_rate=2800.0)
        self.assertEqual(q.contract_id, self.contract)

    def test_contracted_rate_total_nzd(self):
        # Contracted rate $3000/TEU, 1 TEU default → $3000 total
        q = self._make_quote(self.carrier_dsv, base_rate=2800.0)
        self.assertAlmostEqual(q.contracted_rate_total_nzd, 3000.0, places=0)

    def test_opportunity_cost_positive_when_contract_above_market(self):
        # Contract rate $3000, market quote $2800 → OC = +$200 (contract costs more)
        q = self._make_quote(self.carrier_dsv, base_rate=2800.0)
        self.assertGreater(q.opportunity_cost_nzd, 0)

    def test_opportunity_cost_negative_when_contract_below_market(self):
        # Contract rate $3000, market quote $3500 → OC = -$500 (contract saves money)
        q = self._make_quote(self.carrier_dsv, base_rate=3500.0)
        self.assertLess(q.opportunity_cost_nzd, 0)

    def test_opportunity_cost_zero_for_non_contract_carrier(self):
        q = self._make_quote(self.carrier_kn, base_rate=2600.0)
        self.assertAlmostEqual(q.opportunity_cost_nzd, 0.0)
```

### Step 2: Run to verify failure

```bash
cd addons && python -m pytest mml_freight/tests/test_contract_opportunity_cost.py -v 2>&1 | head -20
```
Expected: AttributeError — `is_contract_carrier` etc. do not exist.

### Step 3: Add fields to `freight_tender_quote.py`

Add these imports at the top of `freight_tender_quote.py` (after existing imports):
```python
# (no new imports needed — fields module already imported)
```

Add these fields and methods to `FreightTenderQuote` after the `is_selected` computed field block:

```python
    # --- Contract awareness ---
    contract_id = fields.Many2one(
        'freight.carrier.contract',
        string='Active Contract',
        compute='_compute_contract_fields',
        store=False,
        help='Active contract for this carrier (if any).',
    )
    is_contract_carrier = fields.Boolean(
        'Contract Carrier',
        compute='_compute_contract_fields',
        store=False,
        help='True when this carrier has an active contract with remaining commitment.',
    )
    contract_remaining_qty = fields.Float(
        'Contract Remaining',
        compute='_compute_contract_fields',
        store=False,
        digits=(10, 2),
    )
    contracted_rate_total_nzd = fields.Float(
        'Contracted Rate Total (NZD)',
        compute='_compute_contract_fields',
        store=False,
        digits=(10, 2),
        help='What this shipment would cost at the contracted rate, converted to NZD.',
    )
    opportunity_cost_nzd = fields.Float(
        'Opportunity Cost (NZD)',
        compute='_compute_contract_fields',
        store=False,
        digits=(10, 2),
        help='Contracted rate total minus market quote total (NZD). '
             'Positive = contract costs more than market. Negative = contract saves money.',
    )

    def _get_estimated_unit_quantity(self):
        """Estimate unit quantity for this quote based on transport mode and tender cargo."""
        self.ensure_one()
        mode = self.transport_mode or self.tender_id.freight_mode_preference or 'sea'
        if mode in ('sea_fcl',):
            # Use tender package count as TEU proxy (ops can override on booking)
            return max(1.0, float(self.tender_id.total_packages or 1))
        elif mode in ('air', 'sea_lcl'):
            return max(1.0, self.tender_id.chargeable_weight_kg or 1.0)
        else:
            return 1.0

    @api.depends('carrier_id', 'transport_mode', 'total_rate_nzd',
                 'tender_id.total_packages', 'tender_id.chargeable_weight_kg')
    def _compute_contract_fields(self):
        today = fields.Date.today()
        for q in self:
            if not q.carrier_id:
                q.contract_id = False
                q.is_contract_carrier = False
                q.contract_remaining_qty = 0.0
                q.contracted_rate_total_nzd = 0.0
                q.opportunity_cost_nzd = 0.0
                continue

            contract = self.env['freight.carrier.contract'].search([
                ('carrier_id', '=', q.carrier_id.id),
                ('date_start', '<=', today),
                ('date_end', '>=', today),
            ], limit=1)

            if not contract:
                q.contract_id = False
                q.is_contract_carrier = False
                q.contract_remaining_qty = 0.0
                q.contracted_rate_total_nzd = 0.0
                q.opportunity_cost_nzd = 0.0
                continue

            remaining = contract.remaining_quantity
            q.contract_id = contract
            q.is_contract_carrier = remaining > 0
            q.contract_remaining_qty = remaining

            # Estimate contracted cost for this shipment
            unit_qty = q._get_estimated_unit_quantity()
            nzd = self.env.ref('base.NZD', raise_if_not_found=False)
            rate_nzd = contract.contracted_rate
            if nzd and contract.contracted_rate_currency_id != nzd:
                rate_nzd = contract.contracted_rate_currency_id._convert(
                    contract.contracted_rate, nzd,
                    q.tender_id.company_id or self.env.company,
                    today,
                )
            contracted_total = rate_nzd * unit_qty
            q.contracted_rate_total_nzd = contracted_total
            # Positive = contract costs more than market (opportunity cost to MML)
            q.opportunity_cost_nzd = contracted_total - (q.total_rate_nzd or 0.0)
```

### Step 4: Run tests

```bash
cd addons && python -m pytest mml_freight/tests/test_contract_opportunity_cost.py -v
```
Expected: All 7 tests PASS.

### Step 5: Commit

```bash
git add addons/mml_freight/models/freight_tender_quote.py \
        addons/mml_freight/tests/test_contract_opportunity_cost.py
git commit -m "feat(mml_freight): contract fields + opportunity cost on freight.tender.quote"
```

---

## Task 5: `contract_aware` selection mode on `freight.tender`

**Files:**
- Modify: `addons/mml_freight/models/freight_tender.py`
- Create: `addons/mml_freight/tests/test_contract_aware_selection.py`

### Step 1: Write failing tests

```python
# addons/mml_freight/tests/test_contract_aware_selection.py
from odoo.tests.common import TransactionCase
from odoo import fields

class TestContractAwareSelection(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        prod = cls.env['product.product'].search([], limit=1)
        cls.nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or cls.env.company.currency_id
        cls.carrier_dsv = cls.env['delivery.carrier'].create({
            'name': 'DSV CA',
            'product_id': prod.id,
            'delivery_type': 'fixed',
        })
        cls.carrier_kn = cls.env['delivery.carrier'].create({
            'name': 'KN CA',
            'product_id': prod.id,
            'delivery_type': 'fixed',
        })
        today = fields.Date.today()
        cls.contract = cls.env['freight.carrier.contract'].create({
            'name': 'DSV CA Contract',
            'carrier_id': cls.carrier_dsv.id,
            'date_start': today,
            'date_end': today.replace(year=today.year + 1),
            'commitment_unit': 'teu',
            'committed_quantity': 20.0,
            'contracted_rate': 3000.0,
            'contracted_rate_currency_id': cls.nzd.id,
        })
        partner = cls.env['res.partner'].create({'name': 'CA Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': partner.id})
        cls.tender = cls.env['freight.tender'].create({
            'po_ids': [(4, po.id)],
            'company_id': cls.env.company.id,
            'currency_id': cls.nzd.id,
            'state': 'quoted',
            'selection_mode': 'contract_aware',
        })
        cls.q_dsv = cls.env['freight.tender.quote'].create({
            'tender_id': cls.tender.id,
            'carrier_id': cls.carrier_dsv.id,
            'state': 'received',
            'currency_id': cls.nzd.id,
            'transport_mode': 'sea_fcl',
            'base_rate': 3200.0,   # DSV is more expensive on market
            'estimated_transit_days': 14,
        })
        cls.q_kn = cls.env['freight.tender.quote'].create({
            'tender_id': cls.tender.id,
            'carrier_id': cls.carrier_kn.id,
            'state': 'received',
            'currency_id': cls.nzd.id,
            'transport_mode': 'sea_fcl',
            'base_rate': 2800.0,   # K+N is cheapest on market
            'estimated_transit_days': 16,
        })

    def _reset_tender(self):
        self.tender.write({'state': 'quoted', 'selected_quote_id': False,
                           'has_opportunity_cost_alert': False, 'opportunity_cost_nzd': 0.0})

    def test_contract_aware_selects_dsv(self):
        """Contract carrier (DSV) should be selected even though K+N is cheaper on market."""
        self._reset_tender()
        self.tender.action_auto_select()
        self.assertEqual(self.tender.selected_quote_id, self.q_dsv)

    def test_contract_aware_sets_opportunity_cost_alert(self):
        """When contract carrier costs more than market, alert flag should be set."""
        self._reset_tender()
        self.tender.action_auto_select()
        self.assertTrue(self.tender.has_opportunity_cost_alert)
        self.assertGreater(self.tender.opportunity_cost_nzd, 0)

    def test_contract_aware_no_alert_when_contract_cheaper(self):
        """No alert when contract rate is below market."""
        self._reset_tender()
        # Make DSV cheap on market (below contracted rate)
        self.q_dsv.write({'base_rate': 2500.0})
        self.tender.action_auto_select()
        self.assertFalse(self.tender.has_opportunity_cost_alert)
        self.q_dsv.write({'base_rate': 3200.0})  # restore

    def test_contract_aware_falls_back_to_cheapest_when_no_commitment(self):
        """When contract is exhausted, fall back to cheapest market quote."""
        self._reset_tender()
        # Use up all 20 TEU
        self.env['freight.booking'].create({
            'carrier_id': self.carrier_dsv.id,
            'currency_id': self.nzd.id,
            'transport_mode': 'sea_fcl',
            'contract_id': self.contract.id,
            'unit_quantity': 20.0,
            'state': 'confirmed',
        })
        self.contract.invalidate_recordset()
        self.tender.action_auto_select()
        # Should fall back to K+N (cheapest)
        self.assertEqual(self.tender.selected_quote_id, self.q_kn)
        self.assertFalse(self.tender.has_opportunity_cost_alert)

    def test_new_fields_on_tender(self):
        """Tender must have has_opportunity_cost_alert and opportunity_cost_nzd fields."""
        self.assertIsNotNone(self.tender.has_opportunity_cost_alert)
        self.assertIsNotNone(self.tender.opportunity_cost_nzd)
```

### Step 2: Run to verify failure

```bash
cd addons && python -m pytest mml_freight/tests/test_contract_aware_selection.py -v 2>&1 | head -20
```
Expected: `contract_aware` not in SELECTION_MODES, AttributeError on `has_opportunity_cost_alert`.

### Step 3: Modify `freight_tender.py`

**3a.** Add `contract_aware` to `SELECTION_MODES`:
```python
SELECTION_MODES = [
    ('cheapest', 'Cheapest'),
    ('fastest', 'Fastest'),
    ('best_value', 'Best Value'),
    ('contract_aware', 'Contract Aware'),
    ('manual', 'Manual'),
]
```

**3b.** Add two new fields to `FreightTender` after `selection_reason`:
```python
    has_opportunity_cost_alert = fields.Boolean(
        'Opportunity Cost Alert',
        default=False,
        help='Set when contract_aware selected a carrier whose market rate is below the contracted rate.',
    )
    opportunity_cost_nzd = fields.Float(
        'Opportunity Cost (NZD)',
        digits=(10, 2),
        help='Contracted rate total minus cheapest market rate (NZD) for the selected tender.',
    )
```

**3c.** Add the `contract_aware` branch in `action_auto_select`. Insert before the final `else` (manual) branch:

```python
        elif mode == 'contract_aware':
            today = fields.Date.today()
            # Find contract candidates: received quotes where carrier has active contract + remaining qty
            contract_candidates = received.filtered(lambda q: q.is_contract_carrier)

            if not contract_candidates:
                # No active contract with remaining commitment — fall back to cheapest
                winner = received.sorted('total_rate_nzd')[0]
                reason = (
                    f'Contract-aware: no contract commitment remaining — '
                    f'selected cheapest market rate ({winner.total_rate_nzd:.2f} NZD, {winner.carrier_id.name})'
                )
                self.write({
                    'selected_quote_id': winner.id,
                    'state': 'selected',
                    'selection_reason': reason,
                    'has_opportunity_cost_alert': False,
                    'opportunity_cost_nzd': 0.0,
                })
                self.message_post(body=reason)
                return True

            # Select contract candidate with lowest contracted rate total
            winner = contract_candidates.sorted('contracted_rate_total_nzd')[0]
            contract = winner.contract_id

            # Compute opportunity cost vs cheapest market quote
            cheapest_market = received.sorted('total_rate_nzd')[0]
            oc = winner.opportunity_cost_nzd  # positive = contract costs more

            has_alert = oc > 0
            if has_alert:
                reason = (
                    f'Contract-aware: {winner.carrier_id.name} selected (contract commitment). '
                    f'Opportunity cost vs cheapest market: +{oc:.2f} NZD. '
                    f'Contract utilisation: {contract.utilized_quantity:.1f} of '
                    f'{contract.committed_quantity:.1f} {contract.commitment_unit}. '
                    f'Review if deviation from contract is warranted.'
                )
            else:
                reason = (
                    f'Contract-aware: {winner.carrier_id.name} selected. '
                    f'Contract rate beats market by {abs(oc):.2f} NZD. '
                    f'Contract utilisation: {contract.utilized_quantity:.1f} of '
                    f'{contract.committed_quantity:.1f} {contract.commitment_unit}.'
                )

            self.write({
                'selected_quote_id': winner.id,
                'state': 'selected',
                'selection_reason': reason,
                'has_opportunity_cost_alert': has_alert,
                'opportunity_cost_nzd': oc,
            })
            self.message_post(body=reason)
            return True
```

### Step 4: Run tests

```bash
cd addons && python -m pytest mml_freight/tests/test_contract_aware_selection.py -v
```
Expected: All 5 tests PASS.

### Step 5: Run the full existing test suite to check for regressions

```bash
cd addons && python -m pytest mml_freight/tests/ -v 2>&1 | tail -20
```
Expected: All existing tests still PASS.

### Step 6: Commit

```bash
git add addons/mml_freight/models/freight_tender.py \
        addons/mml_freight/tests/test_contract_aware_selection.py
git commit -m "feat(mml_freight): contract_aware selection mode on freight.tender"
```

---

## Task 6: Weekly cron — commitment pace alert

**Files:**
- Modify: `addons/mml_freight/models/freight_carrier_contract.py`
- Modify: `addons/mml_freight/data/ir_cron.xml`
- Create: `addons/mml_freight/tests/test_contract_cron.py`

### Step 1: Write failing test

```python
# addons/mml_freight/tests/test_contract_cron.py
from odoo.tests.common import TransactionCase
from odoo import fields
from unittest.mock import patch

class TestContractCron(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        prod = cls.env['product.product'].search([], limit=1)
        cls.nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or cls.env.company.currency_id
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Cron',
            'product_id': prod.id,
            'delivery_type': 'fixed',
        })

    def _make_contract(self, days_remaining=60, utilization_pct=30):
        today = fields.Date.today()
        import datetime
        end = today + datetime.timedelta(days=days_remaining)
        committed = 20.0
        utilized = committed * utilization_pct / 100
        c = self.env['freight.carrier.contract'].create({
            'name': f'DSV Cron {days_remaining}d',
            'carrier_id': self.carrier.id,
            'date_start': today,
            'date_end': end,
            'commitment_unit': 'teu',
            'committed_quantity': committed,
            'contracted_rate': 2500.0,
            'contracted_rate_currency_id': self.nzd.id,
        })
        if utilized > 0:
            self.env['freight.booking'].create({
                'carrier_id': self.carrier.id,
                'currency_id': self.nzd.id,
                'transport_mode': 'sea_fcl',
                'contract_id': c.id,
                'unit_quantity': utilized,
                'state': 'confirmed',
            })
        return c

    def test_cron_posts_alert_for_underutilized_near_expiry(self):
        """Contract with <50% util and <90 days remaining should get a chatter alert."""
        c = self._make_contract(days_remaining=60, utilization_pct=30)
        initial_msg_count = len(c.message_ids)
        self.env['freight.carrier.contract'].cron_contract_pace_alert()
        c.invalidate_recordset()
        self.assertGreater(len(c.message_ids), initial_msg_count)

    def test_cron_no_alert_when_well_utilized(self):
        """Contract with >50% util should NOT get an alert."""
        c = self._make_contract(days_remaining=60, utilization_pct=60)
        initial_msg_count = len(c.message_ids)
        self.env['freight.carrier.contract'].cron_contract_pace_alert()
        c.invalidate_recordset()
        self.assertEqual(len(c.message_ids), initial_msg_count)

    def test_cron_no_alert_when_plenty_of_time(self):
        """Contract with >90 days remaining should NOT get an alert even if underutilized."""
        c = self._make_contract(days_remaining=120, utilization_pct=10)
        initial_msg_count = len(c.message_ids)
        self.env['freight.carrier.contract'].cron_contract_pace_alert()
        c.invalidate_recordset()
        self.assertEqual(len(c.message_ids), initial_msg_count)
```

### Step 2: Run to verify failure

```bash
cd addons && python -m pytest mml_freight/tests/test_contract_cron.py -v 2>&1 | head -15
```
Expected: AttributeError — `cron_contract_pace_alert` does not exist.

### Step 3: Add cron method to `freight_carrier_contract.py`

Add this import at the top: `import logging` and `_logger = logging.getLogger(__name__)`.

Add this method to `FreightCarrierContract`:

```python
    @api.model
    def cron_contract_pace_alert(self):
        """Weekly cron: warn on active contracts with low utilization and <90 days remaining.

        Threshold: utilization < 50% AND days_remaining < 90.
        Posts a chatter note on the contract record.
        """
        import datetime
        today = fields.Date.today()
        threshold_date = today + datetime.timedelta(days=90)

        at_risk = self.search([
            ('date_start', '<=', today),
            ('date_end', '>=', today),
            ('date_end', '<=', threshold_date),
        ])

        for contract in at_risk:
            if contract.utilization_pct >= 50.0:
                continue
            days_remaining = (contract.date_end - today).days
            msg = (
                f'Commitment pace alert: {contract.utilization_pct:.1f}% utilised '
                f'({contract.utilized_quantity:.1f} of {contract.committed_quantity:.1f} '
                f'{contract.commitment_unit}), {days_remaining} days remaining. '
                f'At current pace you may fall short of committed volume.'
            )
            contract.message_post(body=msg)
            _logger.info('Contract pace alert posted for contract %s (%s)', contract.name, contract.id)
```

### Step 4: Add cron record to `ir_cron.xml`

Append inside `<odoo>` in `addons/mml_freight/data/ir_cron.xml`:

```xml
    <record id="cron_contract_pace_alert" model="ir.cron">
        <field name="name">Freight: Contract Commitment Pace Alert</field>
        <field name="model_id" ref="model_freight_carrier_contract"/>
        <field name="state">code</field>
        <field name="code">model.cron_contract_pace_alert()</field>
        <field name="interval_number">1</field>
        <field name="interval_type">weeks</field>
        <field name="numbercall">-1</field>
        <field name="doall" eval="False"/>
        <field name="active">True</field>
    </record>
```

### Step 5: Run tests

```bash
cd addons && python -m pytest mml_freight/tests/test_contract_cron.py -v
```
Expected: All 3 tests PASS.

### Step 6: Commit

```bash
git add addons/mml_freight/models/freight_carrier_contract.py \
        addons/mml_freight/data/ir_cron.xml \
        addons/mml_freight/tests/test_contract_cron.py
git commit -m "feat(mml_freight): weekly cron commitment pace alert"
```

---

## Task 7: Security, manifest, and views

**Files:**
- Modify: `addons/mml_freight/security/ir.model.access.csv`
- Create: `addons/mml_freight/views/freight_carrier_contract_views.xml`
- Modify: `addons/mml_freight/views/freight_carrier_views.xml`
- Modify: `addons/mml_freight/views/freight_tender_views.xml`
- Modify: `addons/mml_freight/views/menu.xml`
- Modify: `addons/mml_freight/__manifest__.py`

### Step 1: Add security rows to `ir.model.access.csv`

Append to `addons/mml_freight/security/ir.model.access.csv`:

```csv
access_freight_carrier_contract_user,freight.carrier.contract user,model_freight_carrier_contract,stock.group_stock_user,1,0,0,0
access_freight_carrier_contract_manager,freight.carrier.contract manager,model_freight_carrier_contract,stock.group_stock_manager,1,1,1,1
```

### Step 2: Create `freight_carrier_contract_views.xml`

Create `addons/mml_freight/views/freight_carrier_contract_views.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>

    <!-- LIST VIEW -->
    <record id="view_freight_carrier_contract_list" model="ir.ui.view">
        <field name="name">freight.carrier.contract.list</field>
        <field name="model">freight.carrier.contract</field>
        <field name="arch" type="xml">
            <list string="Carrier Contracts"
                  decoration-success="utilization_pct &lt; 80"
                  decoration-warning="utilization_pct &gt;= 80 and utilization_pct &lt;= 100"
                  decoration-danger="utilization_pct &gt; 100">
                <field name="name"/>
                <field name="carrier_id"/>
                <field name="date_start"/>
                <field name="date_end"/>
                <field name="commitment_unit"/>
                <field name="committed_quantity" string="Committed"/>
                <field name="utilized_quantity" string="Utilized"/>
                <field name="remaining_quantity" string="Remaining"/>
                <field name="utilization_pct" string="Util %" widget="progressbar"/>
                <field name="is_active" widget="boolean_toggle" optional="show"/>
            </list>
        </field>
    </record>

    <!-- FORM VIEW -->
    <record id="view_freight_carrier_contract_form" model="ir.ui.view">
        <field name="name">freight.carrier.contract.form</field>
        <field name="model">freight.carrier.contract</field>
        <field name="arch" type="xml">
            <form string="Carrier Contract">
                <header>
                    <field name="is_active" widget="boolean_toggle" readonly="1"/>
                </header>
                <sheet>
                    <div class="oe_title">
                        <h1><field name="name" placeholder="e.g. DSV 2026 FCL Agreement"/></h1>
                    </div>
                    <group>
                        <group string="Carrier">
                            <field name="carrier_id"/>
                            <field name="date_start"/>
                            <field name="date_end"/>
                        </group>
                        <group string="Commitment Terms">
                            <field name="commitment_unit"/>
                            <field name="committed_quantity"/>
                            <field name="contracted_rate"/>
                            <field name="contracted_rate_currency_id"/>
                        </group>
                    </group>
                    <group string="Utilization">
                        <field name="utilized_quantity" readonly="1"/>
                        <field name="remaining_quantity" readonly="1"/>
                        <field name="utilization_pct" string="Utilization %" readonly="1" widget="progressbar"/>
                    </group>
                    <group string="Notes">
                        <field name="notes" nolabel="1"/>
                    </group>
                </sheet>
                <div class="oe_chatter">
                    <field name="message_follower_ids"/>
                    <field name="message_ids"/>
                </div>
            </form>
        </field>
    </record>

    <!-- ACTION -->
    <record id="action_freight_carrier_contract" model="ir.actions.act_window">
        <field name="name">Carrier Contracts</field>
        <field name="res_model">freight.carrier.contract</field>
        <field name="view_mode">list,form</field>
    </record>

</odoo>
```

### Step 3: Add Contracts tab to carrier form view

In `addons/mml_freight/views/freight_carrier_views.xml`, add a notebook/page for contracts. Change the `<xpath expr="//sheet" position="inside">` to also inject a notebook after the groups. Replace the existing view with:

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_freight_carrier_form" model="ir.ui.view">
        <field name="name">delivery.carrier.form.freight</field>
        <field name="model">delivery.carrier</field>
        <field name="inherit_id" ref="delivery.view_delivery_carrier_form"/>
        <field name="arch" type="xml">
            <xpath expr="//sheet" position="inside">
                <group string="Freight Orchestration" name="freight_config">
                    <field name="auto_tender"/>
                    <field name="transport_modes"/>
                    <field name="max_weight_kg"/>
                    <field name="supports_dg"/>
                    <field name="reliability_score"/>
                    <field name="x_webhook_secret" password="True" groups="stock.group_stock_manager"/>
                </group>
                <group string="Eligible Lanes" name="freight_lanes">
                    <field name="origin_country_ids" widget="many2many_tags"/>
                    <field name="dest_country_ids" widget="many2many_tags"/>
                </group>
                <notebook>
                    <page string="Contracts" name="contracts">
                        <field name="freight_contract_ids" context="{'default_carrier_id': active_id}">
                            <list editable="bottom">
                                <field name="name"/>
                                <field name="date_start"/>
                                <field name="date_end"/>
                                <field name="commitment_unit"/>
                                <field name="committed_quantity"/>
                                <field name="contracted_rate"/>
                                <field name="contracted_rate_currency_id"/>
                                <field name="utilized_quantity" readonly="1"/>
                                <field name="remaining_quantity" readonly="1"/>
                                <field name="is_active" readonly="1"/>
                            </list>
                        </field>
                    </page>
                </notebook>
            </xpath>
        </field>
    </record>
    <record id="action_freight_carrier" model="ir.actions.act_window">
        <field name="name">Freight Carriers</field>
        <field name="res_model">delivery.carrier</field>
        <field name="view_mode">list,form</field>
        <field name="domain">[('auto_tender', '=', True)]</field>
    </record>
</odoo>
```

Also add `freight_contract_ids` as a One2many on `FreightCarrier` in `freight_carrier.py`:

```python
    freight_contract_ids = fields.One2many(
        'freight.carrier.contract', 'carrier_id', string='Contracts',
    )
```

### Step 4: Add opportunity cost banner + columns to tender form

In `addons/mml_freight/views/freight_tender_views.xml`, locate the quote tab's `<list>` inside the `quote_line_ids` field and add after the existing columns:

```xml
<field name="is_contract_carrier" optional="show"/>
<field name="contract_remaining_qty" string="Contract Rem." optional="show"/>
<field name="opportunity_cost_nzd" string="Opp. Cost (NZD)" optional="show"
       decoration-danger="opportunity_cost_nzd &gt; 0"
       decoration-success="opportunity_cost_nzd &lt; 0"/>
```

Also add an alert banner in the tender form `<sheet>` before `<div class="oe_title">`:

```xml
<div class="alert alert-warning" role="alert"
     invisible="not has_opportunity_cost_alert">
    Opportunity cost alert: contract carrier selected above market rate.
    See quotes tab for detail.
</div>
```

And add `has_opportunity_cost_alert` and `opportunity_cost_nzd` as invisible fields somewhere in the form (so the `invisible` binding works):

```xml
<field name="has_opportunity_cost_alert" invisible="1"/>
<field name="opportunity_cost_nzd" invisible="1"/>
```

### Step 5: Add Contracts menu item to `menu.xml`

In `addons/mml_freight/views/menu.xml`, add after the Freight Carriers item:

```xml
    <menuitem id="menu_freight_contracts" name="Carrier Contracts"
              parent="menu_freight_root" action="action_freight_carrier_contract" sequence="45"
              groups="stock.group_stock_manager"/>
```

### Step 6: Update `__manifest__.py`

Add `'views/freight_carrier_contract_views.xml'` to the `data` list. Full updated data list:

```python
    'data': [
        'security/ir.model.access.csv',
        'data/ir_sequence.xml',
        'data/ir_cron.xml',
        'views/freight_carrier_contract_views.xml',
        'views/freight_carrier_views.xml',
        'views/freight_tender_views.xml',
        'views/freight_booking_views.xml',
        'views/purchase_order_views.xml',
        'views/menu.xml',
    ],
```

### Step 7: Run full test suite

```bash
cd addons && python -m pytest mml_freight/tests/ -v 2>&1 | tail -30
```
Expected: All tests PASS. No regressions.

### Step 8: Commit

```bash
git add addons/mml_freight/security/ir.model.access.csv \
        addons/mml_freight/views/freight_carrier_contract_views.xml \
        addons/mml_freight/views/freight_carrier_views.xml \
        addons/mml_freight/views/freight_tender_views.xml \
        addons/mml_freight/views/menu.xml \
        addons/mml_freight/models/freight_carrier.py \
        addons/mml_freight/__manifest__.py
git commit -m "feat(mml_freight): carrier contract views, security, manifest + carrier form Contracts tab"
```

---

## Summary

| Task | What it delivers |
|------|-----------------|
| 1 | `freight.carrier.contract` model with `is_active` |
| 2 | `freight.booking` contract tracking fields (`contract_id`, `unit_quantity`, `unit_type`) |
| 3 | `freight.carrier.contract` utilization computed fields |
| 4 | Quote-level `contract_id`, `is_contract_carrier`, `opportunity_cost_nzd` |
| 5 | `contract_aware` selection mode on tender + alert fields |
| 6 | Weekly cron: commitment pace alert |
| 7 | Security, views, manifest — everything wired for Odoo install |
