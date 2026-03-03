# Phase 2 — DSV Generic Live Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the DSV Generic mock adapter with a fully live integration covering package auto-population, quote, booking, confirm, tracking, and Mainfreight inward order handoff — end-to-end from PO to warehouse receipt notice.

**Architecture:** `DsvMockAdapter` (registered as `dsv_generic`) delegates to `DsvGenericAdapter` (unregistered, live-only logic) when `x_dsv_environment == 'production'`. All HTTP calls mocked in tests — no live DSV credentials required. Inward order payload built by `InwardOrderDocument` in `stock_3pl_mainfreight`, stored in `3pl.message.payload_xml`.

**Tech Stack:** Odoo 19, Python 3.12, DSV Generic API (`https://api.dsv.com`) OAuth2 + Subscription Key, `requests`, `lxml`, `stock_3pl_core` message queue.

**Repo roots:**
- Freight: `E:\ClaudeCode\projects\mml.odoo.apps\fowarder.intergration` (referred to as `./`)
- 3PL: `E:\ClaudeCode\projects\mml.odoo.apps\mainfreight.3pl.intergration` (referred to as `3pl/`)

---

## Task 1: Add `x_freight_weight` to `product.template`

**Files:**
- Modify: `addons/mml_freight/models/product_template.py`
- Modify: `addons/mml_freight/models/freight_tender_package.py`
- Test: `addons/mml_freight/tests/test_package_aggregation.py`

**Step 1: Write failing test**

Add to end of `addons/mml_freight/tests/test_package_aggregation.py`:

```python
def test_weight_field_on_product(self):
    product = self.env['product.template'].create({'name': 'WeightTest'})
    product.x_freight_weight = 5.5
    self.assertAlmostEqual(product.x_freight_weight, 5.5)

def test_onchange_sets_weight_from_product(self):
    product = self.env['product.product'].create({
        'name': 'OnchangeWeight',
        'x_freight_weight': 3.2,
    })
    line = self.env['freight.tender.package'].new({'product_id': product.id})
    line._onchange_product_id()
    self.assertAlmostEqual(line.weight_kg, 3.2)
```

**Step 2: Run to verify it fails**

```
python odoo-bin -d <db> --test-enable -i mml_freight --test-tags TestPackageAggregation.test_weight_field_on_product --stop-after-init
```
Expected: `AttributeError: 'product.template' object has no attribute 'x_freight_weight'`

**Step 3: Add field to product_template.py**

In `addons/mml_freight/models/product_template.py`, add after `x_dangerous_goods`:

```python
    x_freight_weight = fields.Float('Gross Weight (kg)', default=0.0)
```

**Step 4: Wire weight in freight_tender_package.py onchange**

In `addons/mml_freight/models/freight_tender_package.py`, inside `_onchange_product_id`, add after `self.is_dangerous = tmpl.x_dangerous_goods`:

```python
            self.weight_kg = tmpl.x_freight_weight
```

**Step 5: Run to verify tests pass**

```
python odoo-bin -d <db> --test-enable -i mml_freight --test-tags TestPackageAggregation.test_weight_field_on_product,TestPackageAggregation.test_onchange_sets_weight_from_product --stop-after-init
```
Expected: PASS

**Step 6: Commit**

```bash
git add addons/mml_freight/models/product_template.py \
        addons/mml_freight/models/freight_tender_package.py \
        addons/mml_freight/tests/test_package_aggregation.py
git commit -m "feat: add x_freight_weight to product.template, wire into package line onchange"
```

---

## Task 2: Package Auto-Population on Tender Creation

**Files:**
- Modify: `addons/mml_freight/models/purchase_order.py`
- Create: `addons/mml_freight/tests/test_tender_package_population.py`
- Modify: `addons/mml_freight/tests/__init__.py`

**Step 1: Create test file**

`addons/mml_freight/tests/test_tender_package_population.py`:

```python
from odoo.tests.common import TransactionCase


class TestTenderPackagePopulation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supplier = cls.env['res.partner'].create({'name': 'Pop Supplier'})
        cls.product = cls.env['product.product'].create({
            'name': 'Widget',
            'type': 'product',
            'x_freight_length': 30.0,
            'x_freight_width': 20.0,
            'x_freight_height': 10.0,
            'x_freight_weight': 2.5,
            'x_dangerous_goods': False,
        })

    def _make_po(self, qty=5):
        po = self.env['purchase.order'].create({'partner_id': self.supplier.id})
        self.env['purchase.order.line'].create({
            'order_id': po.id,
            'product_id': self.product.id,
            'product_qty': qty,
            'price_unit': 10.0,
        })
        return po

    def test_package_lines_created_from_po_lines(self):
        po = self._make_po(qty=5)
        po.action_request_freight_tender()
        tender = po.freight_tender_id
        self.assertEqual(len(tender.package_line_ids), 1)
        line = tender.package_line_ids[0]
        self.assertEqual(line.description, self.product.name)
        self.assertEqual(line.quantity, 5)
        self.assertAlmostEqual(line.weight_kg, 2.5)
        self.assertAlmostEqual(line.length_cm, 30.0)
        self.assertAlmostEqual(line.width_cm, 20.0)
        self.assertAlmostEqual(line.height_cm, 10.0)
        self.assertFalse(line.is_dangerous)

    def test_missing_dims_posts_chatter_warning(self):
        product_no_dims = self.env['product.product'].create({'name': 'NoDims'})
        po = self.env['purchase.order'].create({'partner_id': self.supplier.id})
        self.env['purchase.order.line'].create({
            'order_id': po.id,
            'product_id': product_no_dims.id,
            'product_qty': 3,
            'price_unit': 5.0,
        })
        po.action_request_freight_tender()
        tender = po.freight_tender_id
        msgs = tender.message_ids.filtered(
            lambda m: 'missing freight dimensions' in (m.body or '')
        )
        self.assertTrue(msgs, 'Expected missing-dims chatter warning')

    def test_volume_computed_from_dims(self):
        po = self._make_po(qty=2)
        po.action_request_freight_tender()
        line = po.freight_tender_id.package_line_ids[0]
        expected = 30.0 * 20.0 * 10.0 / 1_000_000.0 * 2
        self.assertAlmostEqual(line.volume_m3, expected, places=6)

    def test_dangerous_goods_flag_copied(self):
        dg_product = self.env['product.product'].create({
            'name': 'DGProd', 'x_dangerous_goods': True,
            'x_freight_weight': 1.0, 'x_freight_length': 10.0,
            'x_freight_width': 10.0, 'x_freight_height': 10.0,
        })
        po = self.env['purchase.order'].create({'partner_id': self.supplier.id})
        self.env['purchase.order.line'].create({
            'order_id': po.id, 'product_id': dg_product.id,
            'product_qty': 1, 'price_unit': 5.0,
        })
        po.action_request_freight_tender()
        line = po.freight_tender_id.package_line_ids[0]
        self.assertTrue(line.is_dangerous)
```

**Step 2: Register test in `__init__.py`**

In `addons/mml_freight/tests/__init__.py`, add:
```python
from . import test_tender_package_population
```

**Step 3: Run to verify tests fail**

```
python odoo-bin -d <db> --test-enable -i mml_freight --test-tags TestTenderPackagePopulation --stop-after-init
```
Expected: FAIL — no package lines created

**Step 4: Implement auto-population in `purchase_order.py`**

In `addons/mml_freight/models/purchase_order.py`, after `self.freight_tender_id = tender` in `action_request_freight_tender()`, add:

```python
        self._populate_tender_packages(tender)
```

Add helper method to `PurchaseOrder` class:

```python
    def _populate_tender_packages(self, tender):
        """Create freight.tender.package lines from PO lines."""
        warned = []
        for line in self.order_line:
            tmpl = line.product_id.product_tmpl_id
            weight = (tmpl.x_freight_weight if tmpl else 0.0) or 0.0
            length = (tmpl.x_freight_length if tmpl else 0.0) or 0.0
            width  = (tmpl.x_freight_width  if tmpl else 0.0) or 0.0
            height = (tmpl.x_freight_height if tmpl else 0.0) or 0.0
            if not (weight and length and width and height):
                warned.append(line.product_id.name or 'Unknown')
            self.env['freight.tender.package'].create({
                'tender_id':   tender.id,
                'product_id':  line.product_id.id,
                'description': line.product_id.name or '',
                'quantity':    int(line.product_qty),
                'weight_kg':   weight,
                'length_cm':   length,
                'width_cm':    width,
                'height_cm':   height,
                'is_dangerous': tmpl.x_dangerous_goods if tmpl else False,
                'hs_code':     getattr(line.product_id, 'hs_code', '') or '',
            })
        if warned:
            names = ', '.join(warned)
            tender.message_post(
                body=(
                    f'Product(s) missing freight dimensions — package lines populated with zeros, '
                    f'please update before requesting quotes: {names}'
                )
            )
```

**Step 5: Run to verify tests pass**

```
python odoo-bin -d <db> --test-enable -i mml_freight --test-tags TestTenderPackagePopulation --stop-after-init
```
Expected: 4 tests PASS

**Step 6: Commit**

```bash
git add addons/mml_freight/models/purchase_order.py \
        addons/mml_freight/tests/test_tender_package_population.py \
        addons/mml_freight/tests/__init__.py
git commit -m "feat: auto-populate tender package lines from PO lines on tender creation"
```

---

## Task 3: Add Model Fields — CBM Thresholds + Feeder Vessel

**Files:**
- Modify: `addons/mml_freight_dsv/models/freight_carrier_dsv.py`
- Modify: `addons/mml_freight/models/freight_booking.py`

**Step 1: Write failing tests**

Add to `addons/mml_freight_dsv/tests/test_dsv_mock_adapter.py`:

```python
def test_cbm_threshold_fields_exist(self):
    self.carrier.x_dsv_lcl_fcl_threshold = 15.0
    self.carrier.x_dsv_fcl20_fcl40_threshold = 25.0
    self.carrier.x_dsv_fcl40_upper = 40.0
    self.assertAlmostEqual(self.carrier.x_dsv_lcl_fcl_threshold, 15.0)

def test_feeder_vessel_fields_exist(self):
    b = self.env['freight.booking'].create({
        'carrier_id': self.carrier.id,
        'currency_id': self.env.company.currency_id.id,
    })
    b.feeder_vessel_name = 'MSC Flaminia'
    b.feeder_voyage_number = 'FV001'
    self.assertEqual(b.feeder_vessel_name, 'MSC Flaminia')
```

**Step 2: Run to verify they fail**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestDsvMockAdapter.test_cbm_threshold_fields_exist --stop-after-init
```
Expected: `AttributeError`

**Step 3: Add CBM threshold fields to `freight_carrier_dsv.py`**

After `x_dsv_token_expiry`, add:

```python
    x_dsv_lcl_fcl_threshold = fields.Float(
        'LCL→FCL Threshold (CBM)', default=15.0,
        help='Total CBM below which LCL is requested. Grey zone begins here.',
    )
    x_dsv_fcl20_fcl40_threshold = fields.Float(
        'FCL20→FCL40 Threshold (CBM)', default=25.0,
        help='Total CBM above which FCL 40ft is also quoted.',
    )
    x_dsv_fcl40_upper = fields.Float(
        'FCL40 Upper Threshold (CBM)', default=40.0,
        help='Total CBM above which only FCL 40ft is requested.',
    )
```

**Step 4: Add feeder vessel fields to `freight_booking.py`**

After `bill_of_lading` field, add:

```python
    feeder_vessel_name   = fields.Char('Feeder Vessel')
    feeder_voyage_number = fields.Char('Feeder Voyage No.')
```

**Step 5: Run to verify tests pass**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestDsvMockAdapter.test_cbm_threshold_fields_exist,TestDsvMockAdapter.test_feeder_vessel_fields_exist --stop-after-init
```
Expected: PASS

**Step 6: Commit**

```bash
git add addons/mml_freight_dsv/models/freight_carrier_dsv.py \
        addons/mml_freight/models/freight_booking.py
git commit -m "feat: CBM threshold fields on delivery.carrier, feeder vessel fields on freight.booking"
```

---

## Task 4: DSV Quote Payload Builder + LCL/FCL Mode Selection

**Files:**
- Create: `addons/mml_freight_dsv/adapters/dsv_quote_builder.py`
- Create: `addons/mml_freight_dsv/tests/test_dsv_quote_payload.py`
- Modify: `addons/mml_freight_dsv/tests/__init__.py`

**Step 1: Create test file**

`addons/mml_freight_dsv/tests/test_dsv_quote_payload.py`:

```python
from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_quote_builder import (
    get_product_types, build_quote_payload,
)


class TestDsvQuotePayload(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV QP Test',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'dsv_generic',
            'x_dsv_mdm': 'MDM123',
            'x_dsv_lcl_fcl_threshold': 15.0,
            'x_dsv_fcl20_fcl40_threshold': 25.0,
            'x_dsv_fcl40_upper': 40.0,
        })
        origin = cls.env['res.partner'].create({
            'name': 'SH Supplier',
            'country_id': cls.env.ref('base.cn').id,
            'city': 'Shanghai', 'zip': '200000', 'street': '1 Nanjing Rd',
        })
        dest = cls.env['res.partner'].create({
            'name': 'AKL WH',
            'country_id': cls.env.ref('base.nz').id,
            'city': 'Auckland', 'zip': '0600', 'street': '1 Freight Dr',
        })
        po = cls.env['purchase.order'].create({'partner_id': origin.id})
        cls.tender = cls.env['freight.tender'].create({
            'purchase_order_id': po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
            'origin_partner_id': origin.id,
            'origin_country_id': cls.env.ref('base.cn').id,
            'dest_partner_id': dest.id,
            'dest_country_id': cls.env.ref('base.nz').id,
            'requested_pickup_date': '2026-04-01',
            'incoterm_id': cls.env.ref('account.incoterm_FOB').id,
        })
        cls.env['freight.tender.package'].create({
            'tender_id': cls.tender.id,
            'description': 'Widget', 'quantity': 10,
            'weight_kg': 25.0, 'length_cm': 60.0,
            'width_cm': 40.0, 'height_cm': 30.0,
        })

    # --- Mode selection ---

    def test_small_cbm_is_lcl_only(self):
        self.assertEqual(get_product_types(self.carrier, 5.0, 'any'), ['SEA_LCL'])

    def test_grey_zone_lcl_to_fcl20(self):
        modes = get_product_types(self.carrier, 18.0, 'any')
        self.assertIn('SEA_LCL', modes)
        self.assertIn('SEA_FCL_20', modes)
        self.assertEqual(len(modes), 2)

    def test_grey_zone_fcl20_to_fcl40(self):
        modes = get_product_types(self.carrier, 30.0, 'any')
        self.assertIn('SEA_FCL_20', modes)
        self.assertIn('SEA_FCL_40', modes)
        self.assertEqual(len(modes), 2)

    def test_large_cbm_is_fcl40_only(self):
        self.assertEqual(get_product_types(self.carrier, 45.0, 'any'), ['SEA_FCL_40'])

    def test_air_preference_bypasses_cbm(self):
        self.assertEqual(get_product_types(self.carrier, 5.0, 'air'), ['AIR_EXPRESS'])

    def test_sea_preference_uses_cbm_thresholds(self):
        modes = get_product_types(self.carrier, 5.0, 'sea')
        self.assertEqual(modes, ['SEA_LCL'])

    # --- Payload structure ---

    def test_payload_required_top_level_keys(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        for key in ('from', 'to', 'packages', 'productType', 'mdmNumber', 'unitsOfMeasurement'):
            self.assertIn(key, p)

    def test_payload_product_type_set(self):
        p = build_quote_payload(self.tender, 'SEA_FCL_20', 'MDM123')
        self.assertEqual(p['productType'], 'SEA_FCL_20')

    def test_payload_origin_country_code(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        self.assertEqual(p['from']['country'], 'CN')

    def test_payload_dest_country_code(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        self.assertEqual(p['to']['country'], 'NZ')

    def test_payload_package_weight(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        self.assertAlmostEqual(p['packages'][0]['grossWeight'], 25.0)

    def test_payload_package_quantity(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        self.assertEqual(p['packages'][0]['quantity'], 10)

    def test_payload_units_of_measurement(self):
        uom = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')['unitsOfMeasurement']
        self.assertEqual(uom['weight'], 'KG')
        self.assertEqual(uom['dimension'], 'CM')
        self.assertEqual(uom['volume'], 'M3')

    def test_payload_incoterm_code(self):
        p = build_quote_payload(self.tender, 'SEA_LCL', 'MDM123')
        self.assertEqual(p['incoterms'], 'FOB')
```

**Step 2: Register test**

In `addons/mml_freight_dsv/tests/__init__.py`, add:
```python
from . import test_dsv_quote_payload
```

**Step 3: Run to verify tests fail**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestDsvQuotePayload --stop-after-init
```
Expected: `ImportError: cannot import name 'get_product_types'`

**Step 4: Create `dsv_quote_builder.py`**

`addons/mml_freight_dsv/adapters/dsv_quote_builder.py`:

```python
"""DSV Generic — quote request payload builder."""


def get_product_types(carrier, total_cbm, mode_preference):
    """Return list of DSV productType strings for the given tender.

    Grey zones return two types to trigger parallel requests.
    Specific mode_preference bypasses CBM thresholds.
    """
    if mode_preference == 'air':
        return ['AIR_EXPRESS']
    if mode_preference == 'road':
        return ['ROAD']

    # Sea or any: use CBM thresholds
    lcl_max   = getattr(carrier, 'x_dsv_lcl_fcl_threshold',      15.0) or 15.0
    fcl20_max = getattr(carrier, 'x_dsv_fcl20_fcl40_threshold',   25.0) or 25.0
    fcl40_top = getattr(carrier, 'x_dsv_fcl40_upper',             40.0) or 40.0

    if total_cbm < lcl_max:
        return ['SEA_LCL']
    elif total_cbm < fcl20_max:
        return ['SEA_LCL', 'SEA_FCL_20']
    elif total_cbm < fcl40_top:
        return ['SEA_FCL_20', 'SEA_FCL_40']
    else:
        return ['SEA_FCL_40']


def build_quote_payload(tender, product_type, mdm_number):
    """Build DSV POST /qs/quote/v1/quotes body dict from a freight.tender record."""
    origin = tender.origin_partner_id
    dest   = tender.dest_partner_id
    return {
        'from': {
            'country':      origin.country_id.code if origin.country_id else '',
            'city':         origin.city  or '',
            'zipCode':      origin.zip   or '',
            'addressLine1': origin.street or '',
        },
        'to': {
            'country':      dest.country_id.code if dest.country_id else '',
            'city':         dest.city  or '',
            'zipCode':      dest.zip   or '',
            'addressLine1': dest.street or '',
        },
        'pickupDate':  str(tender.requested_pickup_date) if tender.requested_pickup_date else '',
        'incoterms':   tender.incoterm_id.code if tender.incoterm_id else '',
        'productType': product_type,
        'mdmNumber':   mdm_number or '',
        'packages': [
            {
                'quantity':       line.quantity,
                'description':    line.description or '',
                'grossWeight':    line.weight_kg,
                'length':         line.length_cm,
                'width':          line.width_cm,
                'height':         line.height_cm,
                'volume':         line.volume_m3,
                'dangerousGoods': line.is_dangerous,
                'harmonizedCode': line.hs_code or '',
            }
            for line in tender.package_line_ids
        ],
        'unitsOfMeasurement': {'weight': 'KG', 'dimension': 'CM', 'volume': 'M3'},
    }
```

**Step 5: Run to verify tests pass**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestDsvQuotePayload --stop-after-init
```
Expected: 12 tests PASS

**Step 6: Commit**

```bash
git add addons/mml_freight_dsv/adapters/dsv_quote_builder.py \
        addons/mml_freight_dsv/tests/test_dsv_quote_payload.py \
        addons/mml_freight_dsv/tests/__init__.py
git commit -m "feat: DSV quote payload builder with LCL/FCL/grey-zone mode selection"
```

---

## Task 5: DSV Booking Payload Builder

**Files:**
- Create: `addons/mml_freight_dsv/adapters/dsv_booking_builder.py`
- Create: `addons/mml_freight_dsv/tests/test_dsv_booking_payload.py`
- Modify: `addons/mml_freight_dsv/tests/__init__.py`

**Step 1: Create test file**

`addons/mml_freight_dsv/tests/test_dsv_booking_payload.py`:

```python
from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_booking_builder import build_booking_payload


class TestDsvBookingPayload(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV BK Test',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'dsv_generic',
            'x_dsv_mdm': 'MDM002',
        })
        origin = cls.env['res.partner'].create({
            'name': 'SH Sup', 'country_id': cls.env.ref('base.cn').id,
            'city': 'Shanghai', 'zip': '200001', 'street': '1 Main',
        })
        dest = cls.env['res.partner'].create({
            'name': 'AKL WH', 'country_id': cls.env.ref('base.nz').id,
            'city': 'Auckland', 'zip': '0600', 'street': '2 Freight',
        })
        po = cls.env['purchase.order'].create({'partner_id': origin.id})
        cls.tender = cls.env['freight.tender'].create({
            'purchase_order_id': po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
            'origin_partner_id': origin.id,
            'dest_partner_id': dest.id,
            'requested_pickup_date': '2026-05-10',
            'incoterm_id': cls.env.ref('account.incoterm_FOB').id,
        })
        cls.env['freight.tender.package'].create({
            'tender_id': cls.tender.id,
            'description': 'Widget', 'quantity': 5,
            'weight_kg': 10.0, 'length_cm': 40.0,
            'width_cm': 30.0, 'height_cm': 20.0,
        })
        nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) \
              or cls.env.company.currency_id
        cls.quote = cls.env['freight.tender.quote'].create({
            'tender_id': cls.tender.id,
            'carrier_id': cls.carrier.id,
            'state': 'received',
            'currency_id': nzd.id,
            'carrier_quote_ref': 'QREF001',
            'transport_mode': 'sea_lcl',
        })

    def test_autobook_is_false(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertFalse(p['autobook'])

    def test_quote_id_set(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertEqual(p['quoteId'], 'QREF001')

    def test_customer_reference_is_po_name(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertEqual(p['customerReference'], self.tender.purchase_order_id.name)

    def test_mdm_number_from_carrier(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertEqual(p['mdmNumber'], 'MDM002')

    def test_packages_mapped(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertEqual(len(p['packages']), 1)
        self.assertEqual(p['packages'][0]['quantity'], 5)

    def test_goods_description_from_package_descriptions(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertIn('Widget', p['goodsDescription'])

    def test_shipper_country_code(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertEqual(p['shipper']['country'], 'CN')

    def test_consignee_country_code(self):
        p = build_booking_payload(self.tender, self.quote, self.carrier)
        self.assertEqual(p['consignee']['country'], 'NZ')
```

**Step 2: Register test**

```python
from . import test_dsv_booking_payload
```

**Step 3: Run to verify tests fail**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestDsvBookingPayload --stop-after-init
```
Expected: `ImportError`

**Step 4: Create `dsv_booking_builder.py`**

`addons/mml_freight_dsv/adapters/dsv_booking_builder.py`:

```python
"""DSV Generic — booking request payload builder."""


def _partner_dict(partner):
    return {
        'name':         partner.name or '',
        'addressLine1': partner.street or '',
        'city':         partner.city   or '',
        'zipCode':      partner.zip    or '',
        'country':      partner.country_id.code if partner.country_id else '',
    }


def build_booking_payload(tender, selected_quote, carrier):
    """Build DSV POST /booking/v2/bookings body dict."""
    descs = [l.description for l in tender.package_line_ids if l.description]
    goods_desc = ', '.join(descs) if descs else 'General Cargo'
    packages = [
        {
            'quantity':       line.quantity,
            'description':    line.description or '',
            'grossWeight':    line.weight_kg,
            'length':         line.length_cm,
            'width':          line.width_cm,
            'height':         line.height_cm,
            'volume':         line.volume_m3,
            'dangerousGoods': line.is_dangerous,
            'harmonizedCode': line.hs_code or '',
        }
        for line in tender.package_line_ids
    ]
    return {
        'autobook':          False,
        'productType':       (selected_quote.transport_mode or '').upper(),
        'mdmNumber':         carrier.x_dsv_mdm or '',
        'quoteId':           selected_quote.carrier_quote_ref or '',
        'pickupDate':        str(tender.requested_pickup_date) if tender.requested_pickup_date else '',
        'incoterms':         tender.incoterm_id.code if tender.incoterm_id else '',
        'shipper':           _partner_dict(tender.origin_partner_id),
        'consignee':         _partner_dict(tender.dest_partner_id),
        'packages':          packages,
        'goodsDescription':  goods_desc,
        'customerReference': tender.purchase_order_id.name if tender.purchase_order_id else '',
    }
```

**Step 5: Run to verify tests pass**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestDsvBookingPayload --stop-after-init
```
Expected: 8 tests PASS

**Step 6: Commit**

```bash
git add addons/mml_freight_dsv/adapters/dsv_booking_builder.py \
        addons/mml_freight_dsv/tests/test_dsv_booking_payload.py \
        addons/mml_freight_dsv/tests/__init__.py
git commit -m "feat: DSV booking payload builder"
```

---

## Task 6: `DsvGenericAdapter` — live `request_quote()`

**Files:**
- Modify: `addons/mml_freight_dsv/adapters/dsv_generic_adapter.py`
- Create: `addons/mml_freight_dsv/tests/test_dsv_generic_adapter.py`
- Modify: `addons/mml_freight_dsv/tests/__init__.py`

**Step 1: Create test file**

`addons/mml_freight_dsv/tests/test_dsv_generic_adapter.py`:

```python
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter


def _resp(status=200, data=None):
    m = MagicMock()
    m.status_code = status
    m.ok = status < 400
    m.text = str(data or '')
    m.json.return_value = data or {}
    return m


class TestDsvGenericAdapter(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Live',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'dsv_generic',
            'x_dsv_environment': 'production',
            'x_dsv_client_id': 'cid', 'x_dsv_client_secret': 'csec',
            'x_dsv_mdm': 'MDM001', 'x_dsv_subscription_key': 'SUB001',
            'x_dsv_lcl_fcl_threshold': 15.0,
            'x_dsv_fcl20_fcl40_threshold': 25.0,
            'x_dsv_fcl40_upper': 40.0,
        })
        origin = cls.env['res.partner'].create({
            'name': 'Sup', 'country_id': cls.env.ref('base.cn').id,
            'city': 'SH', 'zip': '200001', 'street': '1 Main',
        })
        dest = cls.env['res.partner'].create({
            'name': 'WH', 'country_id': cls.env.ref('base.nz').id,
            'city': 'AKL', 'zip': '0600', 'street': '2 Freight',
        })
        po = cls.env['purchase.order'].create({'partner_id': origin.id})
        cls.tender = cls.env['freight.tender'].create({
            'purchase_order_id': po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
            'origin_partner_id': origin.id,
            'origin_country_id': cls.env.ref('base.cn').id,
            'dest_partner_id': dest.id,
            'dest_country_id': cls.env.ref('base.nz').id,
            'requested_pickup_date': '2026-05-01',
        })
        cls.env['freight.tender.package'].create({
            'tender_id': cls.tender.id,
            'description': 'Widget', 'quantity': 5,
            'weight_kg': 10.0, 'length_cm': 40.0,
            'width_cm': 30.0, 'height_cm': 20.0,
        })

    def _adapter(self):
        return DsvGenericAdapter(self.carrier, self.env)

    # --- request_quote ---

    def test_request_quote_returns_list(self):
        dsv_data = {'quotes': [{
            'serviceCode': 'SVC001', 'serviceName': 'DSV Sea LCL',
            'productType': 'SEA_LCL',
            'totalCharge': {'amount': 2500.0, 'currency': 'NZD'},
            'transitDays': 25,
        }]}
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
            with patch('requests.post', return_value=_resp(200, dsv_data)):
                results = self._adapter().request_quote(self.tender)
        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['service_name'], 'DSV Sea LCL')
        self.assertAlmostEqual(results[0]['total_rate'], 2500.0)
        self.assertEqual(results[0]['transport_mode'], 'sea_lcl')

    def test_request_quote_multiple_quotes_in_response(self):
        dsv_data = {'quotes': [
            {'serviceCode': 'A', 'serviceName': 'LCL', 'productType': 'SEA_LCL',
             'totalCharge': {'amount': 1000.0, 'currency': 'NZD'}, 'transitDays': 30},
            {'serviceCode': 'B', 'serviceName': 'Air', 'productType': 'AIR_EXPRESS',
             'totalCharge': {'amount': 5000.0, 'currency': 'NZD'}, 'transitDays': 3},
        ]}
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
            with patch('requests.post', return_value=_resp(200, dsv_data)):
                results = self._adapter().request_quote(self.tender)
        self.assertEqual(len(results), 2)

    def test_request_quote_401_retries_once(self):
        """401 triggers token refresh and one retry; both fail → error dict returned."""
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
            with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.refresh_token', return_value='T2'):
                with patch('requests.post', return_value=_resp(401)):
                    results = self._adapter().request_quote(self.tender)
        self.assertTrue(all(r.get('_error') for r in results))

    def test_request_quote_500_returns_error_dict(self):
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
            with patch('requests.post', return_value=_resp(500)):
                results = self._adapter().request_quote(self.tender)
        self.assertTrue(all(r.get('_error') for r in results))

    def test_request_quote_network_error_returns_error_dict(self):
        import requests as _req
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
            with patch('requests.post', side_effect=_req.ConnectionError('timeout')):
                results = self._adapter().request_quote(self.tender)
        self.assertTrue(all(r.get('_error') for r in results))
```

**Step 2: Register test**

```python
from . import test_dsv_generic_adapter
```

**Step 3: Run to verify tests fail**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestDsvGenericAdapter.test_request_quote_returns_list --stop-after-init
```
Expected: `NotImplementedError`

**Step 4: Replace `dsv_generic_adapter.py` with live implementation scaffold**

`addons/mml_freight_dsv/adapters/dsv_generic_adapter.py`:

```python
import logging
import requests
from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase
from odoo.addons.mml_freight_dsv.adapters.dsv_auth import get_token, refresh_token, DsvAuthError
from odoo.addons.mml_freight_dsv.adapters.dsv_quote_builder import get_product_types, build_quote_payload

_logger = logging.getLogger(__name__)

DSV_BASE         = 'https://api.dsv.com'
DSV_QUOTE_URL    = f'{DSV_BASE}/qs/quote/v1/quotes'
DSV_BOOKING_URL  = f'{DSV_BASE}/booking/v2/bookings'
DSV_TRACKING_URL = f'{DSV_BASE}/tracking/v1/shipments/{{shipment_id}}/events'

_DSV_EVENT_STATE_MAP = {
    'BOOKING_CONFIRMED': 'confirmed',
    'CARGO_RECEIVED':    'cargo_ready',
    'DEPARTURE':         'in_transit',
    'ARRIVED_POD':       'arrived_port',
    'CUSTOMS_CLEARED':   'customs',
    'DELIVERED':         'delivered',
}

_DSV_PRODUCT_TYPE_TO_MODE = {
    'SEA_LCL':    'sea_lcl',
    'SEA_FCL_20': 'sea_fcl',
    'SEA_FCL_40': 'sea_fcl',
    'AIR_EXPRESS': 'air',
    'ROAD':        'road',
}


class DsvGenericAdapter(FreightAdapterBase):
    """Live DSV Generic adapter. Not directly registered — used via DsvMockAdapter delegation."""

    def _headers(self, token):
        return {
            'Authorization':            f'Bearer {token}',
            'Ocp-Apim-Subscription-Key': self.carrier.x_dsv_subscription_key or '',
            'Content-Type':              'application/json',
        }

    def _post_with_retry(self, url, payload, token):
        """POST to DSV. Retries once on 401 after token refresh."""
        resp = requests.post(url, json=payload, headers=self._headers(token), timeout=30)
        if resp.status_code in (401, 403):
            try:
                token = refresh_token(self.carrier)
            except DsvAuthError:
                return resp
            resp = requests.post(url, json=payload, headers=self._headers(token), timeout=30)
        return resp

    # ------------------------------------------------------------------
    # request_quote
    # ------------------------------------------------------------------

    def request_quote(self, tender):
        """Return list of quote dicts. Error conditions return dicts with _error=True."""
        token      = get_token(self.carrier)
        mdm        = self.carrier.x_dsv_mdm or ''
        total_cbm  = tender.total_cbm or 0.0
        mode_pref  = tender.freight_mode_preference or 'any'
        prod_types = get_product_types(self.carrier, total_cbm, mode_pref)

        results = []
        for product_type in prod_types:
            payload = build_quote_payload(tender, product_type, mdm)
            try:
                resp = self._post_with_retry(DSV_QUOTE_URL, payload, token)
            except Exception as e:
                _logger.error('DSV quote request failed (%s): %s', product_type, e)
                results.append({'_error': True, 'error_message': str(e)[:500]})
                continue

            if not resp.ok:
                _logger.warning('DSV quote HTTP %s for %s', resp.status_code, product_type)
                results.append({'_error': True, 'error_message': f'DSV HTTP {resp.status_code}'})
                continue

            for quote in (resp.json().get('quotes') or []):
                charge = quote.get('totalCharge') or {}
                mode   = _DSV_PRODUCT_TYPE_TO_MODE.get(
                    quote.get('productType', product_type), 'sea_lcl'
                )
                results.append({
                    'service_name':           quote.get('serviceName', ''),
                    'transport_mode':         mode,
                    'carrier_quote_ref':      quote.get('serviceCode', ''),
                    'total_rate':             float(charge.get('amount', 0)),
                    'base_rate':              float(charge.get('amount', 0)),
                    'fuel_surcharge':         0.0,
                    'origin_charges':         0.0,
                    'destination_charges':    0.0,
                    'customs_charges':        0.0,
                    'other_surcharges':       0.0,
                    'currency':               charge.get('currency', 'NZD'),
                    'transit_days':           float(quote.get('transitDays', 0)),
                    'rate_valid_until':        None,
                    'estimated_pickup_date':  None,
                    'estimated_delivery_date': None,
                    'raw_response':           str(resp.json()),
                })
        return results

    # ------------------------------------------------------------------
    # create_booking / cancel_booking — implemented in Task 7
    # ------------------------------------------------------------------

    def create_booking(self, tender, selected_quote):
        raise NotImplementedError('Implemented in Task 7')

    def cancel_booking(self, booking):
        raise NotImplementedError('Implemented in Task 7')

    # ------------------------------------------------------------------
    # confirm_booking — implemented in Task 9
    # ------------------------------------------------------------------

    def confirm_booking(self, booking):
        raise NotImplementedError('Implemented in Task 9')

    # ------------------------------------------------------------------
    # get_tracking — implemented in Task 11
    # ------------------------------------------------------------------

    def get_tracking(self, booking):
        raise NotImplementedError('Implemented in Task 11')
```

**Step 5: Run to verify tests pass**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestDsvGenericAdapter.test_request_quote_returns_list,TestDsvGenericAdapter.test_request_quote_401_retries_once,TestDsvGenericAdapter.test_request_quote_500_returns_error_dict,TestDsvGenericAdapter.test_request_quote_network_error_returns_error_dict --stop-after-init
```
Expected: 4 tests PASS

**Step 6: Confirm mock adapter tests still pass (no regression)**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestDsvMockAdapter --stop-after-init
```

**Step 7: Commit**

```bash
git add addons/mml_freight_dsv/adapters/dsv_generic_adapter.py \
        addons/mml_freight_dsv/tests/test_dsv_generic_adapter.py \
        addons/mml_freight_dsv/tests/__init__.py
git commit -m "feat: DsvGenericAdapter live request_quote() with retry and error handling"
```

---

## Task 7: `DsvGenericAdapter` — `create_booking()` + `cancel_booking()`

**Files:**
- Modify: `addons/mml_freight_dsv/adapters/dsv_generic_adapter.py`
- Modify: `addons/mml_freight/adapters/base_adapter.py`
- Create: `addons/mml_freight_dsv/tests/test_dsv_cancel.py`
- Modify: `addons/mml_freight_dsv/tests/__init__.py`
- Modify: `addons/mml_freight_dsv/tests/test_dsv_generic_adapter.py`

**Step 1: Add create_booking tests to `test_dsv_generic_adapter.py`**

```python
    # --- create_booking ---

    def _quote(self):
        nzd = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1) \
              or self.env.company.currency_id
        return self.env['freight.tender.quote'].create({
            'tender_id': self.tender.id, 'carrier_id': self.carrier.id,
            'state': 'received', 'currency_id': nzd.id,
            'carrier_quote_ref': 'QREF99', 'transport_mode': 'sea_lcl',
        })

    def test_create_booking_returns_refs(self):
        dsv_data = {'bookingId': 'DSVBK001', 'shipmentId': 'DSVSH001'}
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
            with patch('requests.post', return_value=_resp(200, dsv_data)):
                result = self._adapter().create_booking(self.tender, self._quote())
        self.assertEqual(result['carrier_booking_id'], 'DSVBK001')
        self.assertEqual(result['carrier_shipment_id'], 'DSVSH001')
        self.assertTrue(result['requires_manual_confirmation'])

    def test_create_booking_422_raises_user_error(self):
        from odoo.exceptions import UserError
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
            with patch('requests.post', return_value=_resp(422, {'error': 'invalid payload'})):
                with self.assertRaises(UserError):
                    self._adapter().create_booking(self.tender, self._quote())
```

**Step 2: Create `test_dsv_cancel.py`**

`addons/mml_freight_dsv/tests/test_dsv_cancel.py`:

```python
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter


def _resp(status=200):
    m = MagicMock()
    m.status_code = status
    m.ok = status < 400
    return m


class TestDsvCancel(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Cancel',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'dsv_generic',
            'x_dsv_environment': 'production',
            'x_dsv_client_id': 'id', 'x_dsv_client_secret': 'sec',
            'x_dsv_subscription_key': 'SUB001',
        })

    def _booking(self, bk_id, state='draft'):
        return self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': self.env.company.currency_id.id,
            'carrier_booking_id': bk_id,
            'state': state,
        })

    def _adapter(self):
        return DsvGenericAdapter(self.carrier, self.env)

    def test_cancel_draft_calls_delete_with_booking_id_in_url(self):
        b = self._booking('DSVBK_CANCEL')
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
            with patch('requests.delete', return_value=_resp(204)) as mock_del:
                self._adapter().cancel_booking(b)
        mock_del.assert_called_once()
        self.assertIn('DSVBK_CANCEL', mock_del.call_args[0][0])

    def test_cancel_404_does_not_raise(self):
        b = self._booking('DSVBK_GONE')
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
            with patch('requests.delete', return_value=_resp(404)):
                self._adapter().cancel_booking(b)  # must not raise

    def test_cancel_no_booking_id_is_noop(self):
        b = self._booking('')
        with patch('requests.delete') as mock_del:
            self._adapter().cancel_booking(b)
        mock_del.assert_not_called()

    def test_cancel_confirmed_booking_skips_api_and_posts_chatter(self):
        b = self._booking('DSVBK_CONF', state='confirmed')
        with patch('requests.delete') as mock_del:
            self._adapter().cancel_booking(b)
        mock_del.assert_not_called()
        msgs = b.message_ids.filtered(lambda m: 'Contact DSV directly' in (m.body or ''))
        self.assertTrue(msgs, 'Expected chatter warning for confirmed booking cancel')
```

**Step 3: Register test**

```python
from . import test_dsv_cancel
```

**Step 4: Run to verify tests fail**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestDsvCancel --stop-after-init
```
Expected: `NotImplementedError`

**Step 5: Add `cancel_booking` default to `base_adapter.py`**

In `addons/mml_freight/adapters/base_adapter.py`, add after `get_label`:

```python
    def cancel_booking(self, booking):
        """Cancel a booking with the carrier. Default is a no-op. Override where supported."""
        pass
```

**Step 6: Implement `create_booking()` and `cancel_booking()` in `dsv_generic_adapter.py`**

Replace the stubs:

```python
    def create_booking(self, tender, selected_quote):
        """Create DSV draft booking (autobook=False). Raises UserError on any API failure."""
        from odoo.exceptions import UserError
        from odoo.addons.mml_freight_dsv.adapters.dsv_booking_builder import build_booking_payload
        token   = get_token(self.carrier)
        payload = build_booking_payload(tender, selected_quote, self.carrier)
        try:
            resp = self._post_with_retry(DSV_BOOKING_URL, payload, token)
        except Exception as e:
            raise UserError(f'DSV booking API error: {e}') from e
        if not resp.ok:
            raise UserError(f'DSV booking failed (HTTP {resp.status_code}): {resp.text[:200]}')
        data = resp.json()
        return {
            'carrier_booking_id':          data.get('bookingId', ''),
            'carrier_shipment_id':         data.get('shipmentId', ''),
            'carrier_tracking_url':        data.get('trackingUrl', ''),
            'requires_manual_confirmation': True,
        }

    def cancel_booking(self, booking):
        """Cancel DSV draft booking via DELETE. Confirmed → warn only. 404 → treat as success."""
        if booking.state == 'confirmed':
            booking.message_post(
                body='This booking is already confirmed with DSV. '
                     'Contact DSV directly to cancel — cancellation fees may apply.'
            )
            return
        bk_id = booking.carrier_booking_id
        if not bk_id:
            return
        token = get_token(self.carrier)
        url   = f'{DSV_BOOKING_URL}/{bk_id}'
        try:
            resp = requests.delete(url, headers=self._headers(token), timeout=30)
        except Exception as e:
            _logger.warning('DSV cancel booking %s: request error %s', bk_id, e)
            return
        if resp.status_code == 404:
            _logger.info('DSV cancel %s: 404 (already gone) — treating as success', bk_id)
            return
        if not resp.ok:
            _logger.warning('DSV cancel booking %s: HTTP %s', bk_id, resp.status_code)
```

**Step 7: Run to verify all cancel and create_booking tests pass**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestDsvCancel,TestDsvGenericAdapter.test_create_booking_returns_refs,TestDsvGenericAdapter.test_create_booking_422_raises_user_error --stop-after-init
```
Expected: PASS

**Step 8: Commit**

```bash
git add addons/mml_freight/adapters/base_adapter.py \
        addons/mml_freight_dsv/adapters/dsv_generic_adapter.py \
        addons/mml_freight_dsv/tests/test_dsv_cancel.py \
        addons/mml_freight_dsv/tests/test_dsv_generic_adapter.py \
        addons/mml_freight_dsv/tests/__init__.py
git commit -m "feat: DsvGenericAdapter create_booking() and cancel_booking()"
```

---

## Task 8: `action_book()` — Skip Auto-Confirm for DSV

**Files:**
- Modify: `addons/mml_freight/models/freight_tender.py`
- Modify: `addons/mml_freight/tests/test_tender_lifecycle.py`

**Step 1: Add failing test**

Read `addons/mml_freight/tests/test_tender_lifecycle.py` to understand setup. Then add:

```python
def test_booking_stays_draft_when_requires_manual_confirmation(self):
    """When adapter returns requires_manual_confirmation=True, booking must not auto-confirm."""
    from unittest.mock import patch, MagicMock
    mock_adapter = MagicMock()
    mock_adapter.create_booking.return_value = {
        'carrier_booking_id':          'BK-DRAFT-TEST',
        'carrier_shipment_id':         'SH-001',
        'carrier_tracking_url':        '',
        'requires_manual_confirmation': True,
    }
    # self.tender must be in 'selected' state with self.carrier selected — adapt to fixture
    with patch.object(
        type(self.env['freight.adapter.registry']), 'get_adapter',
        return_value=mock_adapter,
    ):
        self.tender.action_book()
    self.assertEqual(self.tender.booking_id.state, 'draft',
                     'Booking must stay in draft when requires_manual_confirmation=True')
```

**Step 2: Run to verify it fails**

```
python odoo-bin -d <db> --test-enable -i mml_freight --test-tags TestTenderLifecycle.test_booking_stays_draft_when_requires_manual_confirmation --stop-after-init
```
Expected: FAIL (state is 'confirmed')

**Step 3: Modify `action_book()` in `freight_tender.py`**

Replace the existing `action_book()` body with:

```python
    def action_book(self):
        """Confirm booking with selected carrier."""
        self.ensure_one()
        if not self.selected_quote_id:
            raise UserError('Select a quote before booking.')
        if self.state != 'selected':
            raise UserError('Tender must be in Selected state to book.')

        registry = self.env['freight.adapter.registry']
        adapter  = registry.get_adapter(self.selected_quote_id.carrier_id)
        if not adapter:
            raise UserError('No adapter available for selected carrier.')

        # Cancel any existing draft booking before creating a new one
        if self.booking_id and self.booking_id.state == 'draft' and self.booking_id.carrier_booking_id:
            prior_adapter = registry.get_adapter(self.booking_id.carrier_id)
            if prior_adapter:
                prior_adapter.cancel_booking(self.booking_id)

        result = adapter.create_booking(self, self.selected_quote_id)

        booking = self.env['freight.booking'].create({
            'tender_id':             self.id,
            'carrier_id':            self.selected_quote_id.carrier_id.id,
            'purchase_order_id':     self.purchase_order_id.id,
            'currency_id':           self.selected_quote_id.currency_id.id,
            'booked_rate':           self.selected_quote_id.total_rate,
            'transport_mode':        self.selected_quote_id.transport_mode,
            'carrier_booking_id':    result.get('carrier_booking_id', ''),
            'carrier_shipment_id':   result.get('carrier_shipment_id', ''),
            'carrier_tracking_url':  result.get('carrier_tracking_url', ''),
            'state':                 'draft',
        })

        # Only auto-confirm if the adapter does not require manual confirmation
        if not result.get('requires_manual_confirmation'):
            booking.action_confirm()

        self.write({'booking_id': booking.id, 'state': 'booked'})
        if self.purchase_order_id:
            self.purchase_order_id.freight_tender_id = self
        return True
```

**Step 4: Run modified test and full lifecycle suite**

```
python odoo-bin -d <db> --test-enable -i mml_freight --test-tags TestTenderLifecycle --stop-after-init
```
Expected: all PASS

**Step 5: Commit**

```bash
git add addons/mml_freight/models/freight_tender.py \
        addons/mml_freight/tests/test_tender_lifecycle.py
git commit -m "feat: action_book() respects requires_manual_confirmation, cancels prior draft booking"
```

---

## Task 9: `DsvGenericAdapter` — `confirm_booking()`

**Files:**
- Modify: `addons/mml_freight_dsv/adapters/dsv_generic_adapter.py`
- Create: `addons/mml_freight_dsv/tests/test_dsv_confirm_booking.py`
- Modify: `addons/mml_freight_dsv/tests/__init__.py`

**Step 1: Create test file**

`addons/mml_freight_dsv/tests/test_dsv_confirm_booking.py`:

```python
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter


def _resp(status=200, data=None):
    m = MagicMock()
    m.status_code = status
    m.ok = status < 400
    m.text = ''
    m.json.return_value = data or {}
    return m


class TestDsvConfirmBookingAdapter(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Confirm',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'dsv_generic',
            'x_dsv_environment': 'production',
            'x_dsv_subscription_key': 'SUB001',
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_booking_id': 'DSVBK001',
            'state': 'draft',
        })

    def _adapter(self):
        return DsvGenericAdapter(self.carrier, self.env)

    def test_confirm_returns_vessel_and_eta(self):
        dsv_data = {
            'shipmentId': 'SH001',
            'vesselName': 'MSC Oscar',
            'voyageNumber': 'VOY42',
            'containerNumber': 'CONT001',
            'estimatedDelivery': '2026-06-15T00:00:00Z',
        }
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
            with patch('requests.post', return_value=_resp(200, dsv_data)):
                result = self._adapter().confirm_booking(self.booking)
        self.assertEqual(result['vessel_name'], 'MSC Oscar')
        self.assertEqual(result['voyage_number'], 'VOY42')
        self.assertEqual(result['container_number'], 'CONT001')
        self.assertIn('2026-06-15', result['eta'])

    def test_confirm_no_booking_id_raises(self):
        from odoo.exceptions import UserError
        empty = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': self.env.company.currency_id.id,
        })
        with self.assertRaises(UserError):
            self._adapter().confirm_booking(empty)

    def test_confirm_400_raises_user_error(self):
        from odoo.exceptions import UserError
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
            with patch('requests.post', return_value=_resp(400)):
                with self.assertRaises(UserError):
                    self._adapter().confirm_booking(self.booking)

    def test_confirm_feeder_vessel_mapped(self):
        dsv_data = {'shipmentId': 'SH002', 'feederVesselName': 'Feeder A',
                    'feederVoyageNumber': 'FV01', 'estimatedDelivery': ''}
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
            with patch('requests.post', return_value=_resp(200, dsv_data)):
                result = self._adapter().confirm_booking(self.booking)
        self.assertEqual(result['feeder_vessel_name'], 'Feeder A')
        self.assertEqual(result['feeder_voyage_number'], 'FV01')
```

**Step 2: Register test**

```python
from . import test_dsv_confirm_booking
```

**Step 3: Run to verify tests fail**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestDsvConfirmBookingAdapter --stop-after-init
```
Expected: `NotImplementedError`

**Step 4: Implement `confirm_booking()` in `dsv_generic_adapter.py`**

Replace the stub:

```python
    def confirm_booking(self, booking):
        """Confirm DSV draft booking. Returns vessel/ETA dict. Raises UserError on failure."""
        from odoo.exceptions import UserError
        bk_id = booking.carrier_booking_id
        if not bk_id:
            raise UserError('Cannot confirm booking: no carrier_booking_id set.')
        token = get_token(self.carrier)
        url   = f'{DSV_BOOKING_URL}/{bk_id}/confirm'
        try:
            resp = self._post_with_retry(url, {}, token)
        except Exception as e:
            raise UserError(f'DSV confirm booking error: {e}') from e
        if not resp.ok:
            raise UserError(
                f'DSV confirm booking failed (HTTP {resp.status_code}): {resp.text[:200]}'
            )
        data = resp.json()
        return {
            'carrier_shipment_id': data.get('shipmentId', booking.carrier_shipment_id or ''),
            'vessel_name':         data.get('vesselName', ''),
            'voyage_number':       data.get('voyageNumber', ''),
            'container_number':    data.get('containerNumber', ''),
            'bill_of_lading':      data.get('billOfLading', ''),
            'feeder_vessel_name':  data.get('feederVesselName', ''),
            'feeder_voyage_number': data.get('feederVoyageNumber', ''),
            'eta':                 data.get('estimatedDelivery', ''),
        }
```

**Step 5: Run to verify tests pass**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestDsvConfirmBookingAdapter --stop-after-init
```
Expected: 4 tests PASS

**Step 6: Commit**

```bash
git add addons/mml_freight_dsv/adapters/dsv_generic_adapter.py \
        addons/mml_freight_dsv/tests/test_dsv_confirm_booking.py \
        addons/mml_freight_dsv/tests/__init__.py
git commit -m "feat: DsvGenericAdapter confirm_booking()"
```

---

## Task 10: `freight.booking.action_confirm_with_dsv()` + `action_cancel()` integration

**Files:**
- Modify: `addons/mml_freight/models/freight_booking.py`
- Modify: `addons/mml_freight_dsv/tests/test_dsv_confirm_booking.py`

**Step 1: Add Odoo-model tests to `test_dsv_confirm_booking.py`**

Add new class at end of file:

```python
class TestBookingConfirmWithDsv(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Confirm Odoo',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'dsv_generic',
            'x_dsv_environment': 'production',
        })
        partner = cls.env['res.partner'].create({'name': 'BK Supplier'})
        cls.po   = cls.env['purchase.order'].create({'partner_id': partner.id})
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id':        cls.carrier.id,
            'currency_id':       cls.env.company.currency_id.id,
            'carrier_booking_id': 'DSVBK_CONF',
            'purchase_order_id': cls.po.id,
            'state':             'draft',
        })

    def _mock_confirm(self, result=None):
        from unittest.mock import MagicMock
        m = MagicMock()
        m.confirm_booking.return_value = result or {
            'carrier_shipment_id': 'SH99',
            'vessel_name': 'Ever Given',
            'voyage_number': 'VOY99',
            'container_number': 'CONT99',
            'bill_of_lading': '',
            'feeder_vessel_name': '',
            'feeder_voyage_number': '',
            'eta': '2026-07-01T00:00:00Z',
        }
        return m

    def test_action_confirm_with_dsv_state_becomes_confirmed(self):
        from unittest.mock import patch
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=self._mock_confirm(),
        ):
            self.booking.action_confirm_with_dsv()
        self.assertEqual(self.booking.state, 'confirmed')

    def test_action_confirm_with_dsv_stores_vessel(self):
        from unittest.mock import patch
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=self._mock_confirm(),
        ):
            self.booking.action_confirm_with_dsv()
        self.assertEqual(self.booking.vessel_name, 'Ever Given')
        self.assertEqual(self.booking.voyage_number, 'VOY99')

    def test_action_confirm_with_dsv_posts_chatter(self):
        from unittest.mock import patch
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=self._mock_confirm(),
        ):
            self.booking.action_confirm_with_dsv()
        msgs = self.booking.message_ids.filtered(
            lambda m: 'confirmed with DSV' in (m.body or '')
        )
        self.assertTrue(msgs)

    def test_action_cancel_calls_adapter_cancel(self):
        from unittest.mock import patch, MagicMock
        mock_adapter = MagicMock()
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=mock_adapter,
        ):
            self.booking.action_cancel()
        mock_adapter.cancel_booking.assert_called_once_with(self.booking)
        self.assertEqual(self.booking.state, 'cancelled')
```

**Step 2: Run to verify they fail**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestBookingConfirmWithDsv --stop-after-init
```
Expected: `AttributeError: 'freight.booking' has no method 'action_confirm_with_dsv'`

**Step 3: Add `action_confirm_with_dsv()` to `freight_booking.py`**

Add after `action_confirm()`:

```python
    def action_confirm_with_dsv(self):
        """Confirm booking with DSV API, update vessel/ETA fields, queue 3PL inward order."""
        self.ensure_one()
        from odoo.exceptions import UserError
        registry = self.env['freight.adapter.registry']
        adapter  = registry.get_adapter(self.carrier_id)
        if not adapter or not hasattr(adapter, 'confirm_booking'):
            raise UserError('No adapter with confirm_booking() for this carrier.')

        result = adapter.confirm_booking(self)   # raises UserError on failure

        # Parse ISO-8601 ETA string to datetime
        eta = False
        if result.get('eta'):
            try:
                import dateutil.parser
                eta = dateutil.parser.parse(result['eta']).replace(tzinfo=None)
            except Exception:
                pass

        self.write({
            'state':               'confirmed',
            'carrier_shipment_id': result.get('carrier_shipment_id') or self.carrier_shipment_id,
            'vessel_name':         result.get('vessel_name', ''),
            'voyage_number':       result.get('voyage_number', ''),
            'container_number':    result.get('container_number', ''),
            'bill_of_lading':      result.get('bill_of_lading', ''),
            'feeder_vessel_name':  result.get('feeder_vessel_name', ''),
            'feeder_voyage_number': result.get('feeder_voyage_number', ''),
            'eta':                 eta,
        })
        self._queue_3pl_inward_order()
        self._build_inward_order_payload()
        self.message_post(body='Booking confirmed with DSV. Inward order notice queued to Mainfreight.')
        return True
```

**Step 4: Replace `action_cancel()` to call adapter**

```python
    def action_cancel(self):
        registry = self.env['freight.adapter.registry']
        adapter  = registry.get_adapter(self.carrier_id)
        if adapter and self.carrier_booking_id:
            adapter.cancel_booking(self)
        self.write({'state': 'cancelled'})
        return True
```

**Step 5: Add `_build_inward_order_payload` stub**

```python
    def _build_inward_order_payload(self):
        """Populate tpl_message_id.payload_xml and advance to queued. Implemented in Task 16."""
        pass
```

**Step 6: Run to verify tests pass**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestBookingConfirmWithDsv --stop-after-init
```
Expected: 4 tests PASS

**Step 7: Run full booking + 3PL handoff suite (no regression)**

```
python odoo-bin -d <db> --test-enable -i mml_freight --test-tags Test3plHandoff --stop-after-init
```

**Step 8: Commit**

```bash
git add addons/mml_freight/models/freight_booking.py \
        addons/mml_freight_dsv/tests/test_dsv_confirm_booking.py
git commit -m "feat: freight.booking action_confirm_with_dsv(), action_cancel() calls adapter"
```

---

## Task 11: `DsvGenericAdapter` — `get_tracking()`

**Files:**
- Modify: `addons/mml_freight_dsv/adapters/dsv_generic_adapter.py`
- Create: `addons/mml_freight_dsv/tests/test_dsv_tracking.py`
- Modify: `addons/mml_freight_dsv/tests/__init__.py`

**Step 1: Create test file**

`addons/mml_freight_dsv/tests/test_dsv_tracking.py`:

```python
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter


def _resp(status=200, data=None):
    m = MagicMock()
    m.status_code = status
    m.ok = status < 400
    m.json.return_value = data or {}
    return m


class TestDsvTracking(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Track',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'dsv_generic',
            'x_dsv_environment': 'production',
            'x_dsv_subscription_key': 'SUB001',
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id':         cls.carrier.id,
            'currency_id':        cls.env.company.currency_id.id,
            'carrier_shipment_id': 'SH_TRACK_001',
            'state':              'confirmed',
        })

    def _adapter(self):
        return DsvGenericAdapter(self.carrier, self.env)

    def test_returns_events_list(self):
        dsv_data = {'events': [{'eventType': 'DEPARTURE', 'eventDate': '2026-05-10T08:00:00Z',
                                 'location': 'Shanghai CN', 'description': 'Departed.',
                                 'estimatedDelivery': '2026-06-15T00:00:00Z'}]}
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
            with patch('requests.get', return_value=_resp(200, dsv_data)):
                events = self._adapter().get_tracking(self.booking)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['status'], 'in_transit')
        self.assertEqual(events[0]['location'], 'Shanghai CN')
        self.assertEqual(events[0]['_new_eta'], '2026-06-15T00:00:00Z')

    def test_all_event_types_mapped(self):
        mapping = [
            ('BOOKING_CONFIRMED', 'confirmed'),
            ('CARGO_RECEIVED',    'cargo_ready'),
            ('DEPARTURE',         'in_transit'),
            ('ARRIVED_POD',       'arrived_port'),
            ('CUSTOMS_CLEARED',   'customs'),
            ('DELIVERED',         'delivered'),
        ]
        for dsv_type, expected in mapping:
            data = {'events': [{'eventType': dsv_type, 'eventDate': '2026-05-01T00:00:00Z'}]}
            with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
                with patch('requests.get', return_value=_resp(200, data)):
                    events = self._adapter().get_tracking(self.booking)
            self.assertEqual(events[0]['status'], expected, f'Failed for {dsv_type}')

    def test_error_returns_empty_list(self):
        """Tracking errors are non-fatal."""
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
            with patch('requests.get', return_value=_resp(500)):
                events = self._adapter().get_tracking(self.booking)
        self.assertEqual(events, [])

    def test_no_shipment_id_returns_empty(self):
        b = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': self.env.company.currency_id.id,
        })
        events = self._adapter().get_tracking(b)
        self.assertEqual(events, [])
```

**Step 2: Register test**

```python
from . import test_dsv_tracking
```

**Step 3: Run to verify tests fail**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestDsvTracking --stop-after-init
```
Expected: `NotImplementedError`

**Step 4: Implement `get_tracking()` in `dsv_generic_adapter.py`**

Replace the stub:

```python
    def get_tracking(self, booking):
        """Fetch tracking events from DSV. Returns empty list on any error (non-fatal)."""
        shipment_id = booking.carrier_shipment_id
        if not shipment_id:
            return []
        token = get_token(self.carrier)
        url   = DSV_TRACKING_URL.format(shipment_id=shipment_id)
        try:
            resp = requests.get(url, headers=self._headers(token), timeout=30)
        except Exception as e:
            _logger.warning('DSV tracking GET failed for %s: %s', booking.name, e)
            return []
        if not resp.ok:
            _logger.warning('DSV tracking HTTP %s for %s', resp.status_code, booking.name)
            return []
        events = []
        for raw in (resp.json().get('events') or []):
            event_type = raw.get('eventType', '')
            status     = _DSV_EVENT_STATE_MAP.get(event_type, event_type.lower())
            events.append({
                'event_date':  raw.get('eventDate', ''),
                'status':      status,
                'location':    raw.get('location', ''),
                'description': raw.get('description', ''),
                'raw_payload': str(raw),
                '_new_eta':    raw.get('estimatedDelivery', ''),
            })
        return events
```

**Step 5: Run to verify tests pass**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestDsvTracking --stop-after-init
```
Expected: PASS

**Step 6: Commit**

```bash
git add addons/mml_freight_dsv/adapters/dsv_generic_adapter.py \
        addons/mml_freight_dsv/tests/test_dsv_tracking.py \
        addons/mml_freight_dsv/tests/__init__.py
git commit -m "feat: DsvGenericAdapter get_tracking() with DSV event type mapping"
```

---

## Task 12: ETA Drift Detection + Tracking State Auto-Advance

**Files:**
- Modify: `addons/mml_freight/models/freight_booking.py`
- Modify: `addons/mml_freight_dsv/tests/test_dsv_tracking.py`

**Step 1: Add tests**

Add to `test_dsv_tracking.py`:

```python
class TestEtaDriftDetection(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'ETA Drift',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'dsv_generic',
        })
        from datetime import datetime
        cls.orig_eta = datetime(2026, 6, 15)
        partner = cls.env['res.partner'].create({'name': 'DS'})
        cls.po  = cls.env['purchase.order'].create({'partner_id': partner.id})
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id':          cls.carrier.id,
            'currency_id':         cls.env.company.currency_id.id,
            'carrier_shipment_id': 'SH_ETA',
            'purchase_order_id':   cls.po.id,
            'state':               'in_transit',
            'eta':                 cls.orig_eta,
            'vessel_name':         '',
        })

    def test_no_update_when_eta_change_under_24h(self):
        from datetime import timedelta
        from unittest.mock import patch
        self.booking.eta = self.orig_eta + timedelta(hours=2)
        with patch.object(self.booking, '_queue_inward_order_update') as m:
            self.booking._check_inward_order_updates(self.orig_eta, '')
        m.assert_not_called()

    def test_update_queued_when_eta_drifts_over_24h(self):
        from datetime import timedelta
        from unittest.mock import patch
        self.booking.eta = self.orig_eta + timedelta(hours=25)
        with patch.object(self.booking, '_queue_inward_order_update') as m:
            self.booking._check_inward_order_updates(self.orig_eta, '')
        m.assert_called_once()

    def test_update_queued_when_vessel_becomes_known(self):
        from unittest.mock import patch
        self.booking.vessel_name = 'MSC Oscar'
        with patch.object(self.booking, '_queue_inward_order_update') as m:
            self.booking._check_inward_order_updates(self.orig_eta, '')  # prev_vessel = ''
        m.assert_called_once()

    def test_no_update_when_vessel_was_already_known(self):
        from unittest.mock import patch
        self.booking.vessel_name = 'MSC Oscar'
        with patch.object(self.booking, '_queue_inward_order_update') as m:
            self.booking._check_inward_order_updates(self.orig_eta, 'MSC Oscar')
        m.assert_not_called()
```

**Step 2: Run to verify they fail**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestEtaDriftDetection --stop-after-init
```
Expected: `AttributeError`

**Step 3: Implement `_check_inward_order_updates` + `_queue_inward_order_update` + updated `_sync_tracking`**

In `freight_booking.py`, add at module level (after imports):

```python
# DSV eventType → booking state — used by both webhook handler and _sync_tracking
_DSV_BOOKING_STATE_MAP = {
    'BOOKING_CONFIRMED': 'confirmed',
    'CARGO_RECEIVED':    'cargo_ready',
    'DEPARTURE':         'in_transit',
    'ARRIVED_POD':       'arrived_port',
    'CUSTOMS_CLEARED':   'customs',
    'DELIVERED':         'delivered',
}
```

Add methods to `FreightBooking`:

```python
    def _check_inward_order_updates(self, prev_eta, prev_vessel):
        """Queue an inward order UPDATE if ETA drifted > 24h or vessel TBA→known."""
        eta_drifted = False
        if prev_eta and self.eta:
            eta_drifted = abs((self.eta - prev_eta).total_seconds()) > 86400
        vessel_now_known = not prev_vessel and bool(self.vessel_name)
        if eta_drifted or vessel_now_known:
            self._queue_inward_order_update()

    def _queue_inward_order_update(self):
        """Create a queued 3pl.message UPDATE for this booking's inward order."""
        if '3pl.connector' not in self.env:
            return
        po = self.purchase_order_id
        if not po:
            return
        warehouse = po.picking_type_id.warehouse_id if po.picking_type_id else False
        if not warehouse:
            return
        connector = self._resolve_3pl_connector(warehouse, po)
        if not connector:
            return
        msg = self.env['3pl.message'].create({
            'connector_id':  connector.id,
            'direction':     'outbound',
            'document_type': 'inward_order',
            'action':        'update',
            'ref_model':     'purchase.order',
            'ref_id':        po.id,
        })
        _logger.info('freight.booking %s: queued inward_order UPDATE %s', self.name, msg.id)
```

Replace `_sync_tracking` with:

```python
    def _sync_tracking(self):
        """Sync tracking events from carrier adapter; auto-advance state; detect ETA drift."""
        adapter = self.env['freight.adapter.registry'].get_adapter(self.carrier_id)
        if not adapter:
            return

        prev_eta    = self.eta
        prev_vessel = self.vessel_name or ''
        events      = adapter.get_tracking(self)

        latest_state = None
        latest_eta   = None
        state_order  = [s[0] for s in BOOKING_STATES]

        for evt in events:
            exists = self.tracking_event_ids.filtered(
                lambda e: e.status == evt.get('status')
                and str(e.event_date) == evt.get('event_date', '')
            )
            if not exists:
                self.env['freight.tracking.event'].create({
                    'booking_id':  self.id,
                    'event_date':  evt['event_date'],
                    'status':      evt['status'],
                    'location':    evt.get('location', ''),
                    'description': evt.get('description', ''),
                    'raw_payload': evt.get('raw_payload', ''),
                })
            if evt.get('status') in state_order:
                latest_state = evt['status']
            if evt.get('_new_eta'):
                latest_eta = evt['_new_eta']

        # Update ETA
        if latest_eta:
            try:
                import dateutil.parser
                self.eta = dateutil.parser.parse(latest_eta).replace(tzinfo=None)
            except Exception:
                pass

        # Auto-advance state (never go backwards)
        if latest_state and latest_state in state_order:
            cur_idx = state_order.index(self.state) if self.state in state_order else -1
            new_idx = state_order.index(latest_state)
            if new_idx > cur_idx:
                self.state = latest_state

        self._check_inward_order_updates(prev_eta, prev_vessel)
```

**Step 4: Run to verify tests pass**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestEtaDriftDetection --stop-after-init
```
Expected: 4 tests PASS

**Step 5: Commit**

```bash
git add addons/mml_freight/models/freight_booking.py \
        addons/mml_freight_dsv/tests/test_dsv_tracking.py
git commit -m "feat: ETA drift detection, booking state auto-advance, inward order update queuing"
```

---

## Task 13: Webhook Handler Implementation

**Files:**
- Modify: `addons/mml_freight/models/freight_booking.py`
- Create: `addons/mml_freight_dsv/tests/test_dsv_webhook.py`
- Modify: `addons/mml_freight_dsv/tests/__init__.py`

**Step 1: Create test file**

`addons/mml_freight_dsv/tests/test_dsv_webhook.py`:

```python
from odoo.tests.common import TransactionCase


class TestDsvWebhook(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Webhook',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'dsv_generic',
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id':          cls.carrier.id,
            'currency_id':         cls.env.company.currency_id.id,
            'carrier_shipment_id': 'SH_WH_001',
            'state':               'confirmed',
        })

    def _fire(self, body, carrier=None):
        self.env['freight.booking'].sudo()._handle_dsv_tracking_webhook(
            carrier or self.carrier, body
        )

    def test_valid_event_creates_tracking_record(self):
        body = {'shipmentId': 'SH_WH_001', 'events': [
            {'eventType': 'DEPARTURE', 'eventDate': '2026-05-10T08:00:00Z',
             'location': 'Shanghai CN', 'description': 'Departed.'},
        ]}
        self._fire(body)
        events = self.booking.tracking_event_ids.filtered(lambda e: e.status == 'in_transit')
        self.assertTrue(events)

    def test_unknown_shipment_id_silently_ignored(self):
        body = {'shipmentId': 'UNKNOWN_SH', 'events': [
            {'eventType': 'DEPARTURE', 'eventDate': '2026-05-10T08:00:00Z'},
        ]}
        self._fire(body)  # must not raise

    def test_carrier_mismatch_logs_warning_and_ignores(self):
        other = self.env['delivery.carrier'].create({
            'name': 'Other', 'product_id': self.carrier.product_id.id,
            'delivery_type': 'dsv_generic',
        })
        body = {'shipmentId': 'SH_WH_001', 'events': [
            {'eventType': 'DEPARTURE', 'eventDate': '2026-05-10T08:00:00Z'},
        ]}
        import logging
        with self.assertLogs('odoo.addons.mml_freight.models.freight_booking', level='WARNING'):
            self._fire(body, carrier=other)
        # No new tracking events created
        self.assertFalse(
            self.booking.tracking_event_ids.filtered(lambda e: e.status == 'in_transit')
        )

    def test_oversized_location_truncated(self):
        body = {'shipmentId': 'SH_WH_001', 'events': [
            {'eventType': 'DEPARTURE', 'eventDate': '2026-05-10T09:00:00Z',
             'location': 'A' * 400 + '\x00\x01', 'description': 'ok'},
        ]}
        self._fire(body)
        evt = self.booking.tracking_event_ids.filtered(
            lambda e: e.event_date and '2026-05-10' in str(e.event_date)
        )
        if evt:
            self.assertLessEqual(len(evt[-1].location), 255)
            self.assertNotIn('\x00', evt[-1].location)

    def test_empty_body_does_not_raise(self):
        self._fire({})
        self._fire({'shipmentId': ''})
```

**Step 2: Register test**

```python
from . import test_dsv_webhook
```

**Step 3: Run to verify tests fail**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestDsvWebhook --stop-after-init
```
Expected: `test_valid_event_creates_tracking_record` FAIL (stub does nothing)

**Step 4: Implement `_handle_dsv_tracking_webhook()` in `freight_booking.py`**

Replace the stub:

```python
    def _handle_dsv_tracking_webhook(self, carrier, body):
        """Handle DSV TRACKING_UPDATE webhook. Caller must have validated HMAC before calling."""
        import re

        def _sanitise(value, max_len=255):
            if not value:
                return ''
            return re.sub(r'[\x00-\x1f\x7f]', '', str(value))[:max_len]

        if not isinstance(body, dict):
            return
        shipment_id = body.get('shipmentId', '')
        if not shipment_id:
            return

        booking = self.search([
            ('carrier_shipment_id', '=', shipment_id),
            ('state', 'not in', ['cancelled', 'received']),
        ], limit=1)
        if not booking:
            _logger.info('DSV webhook: no active booking for shipmentId %s', shipment_id)
            return

        if booking.carrier_id.id != carrier.id:
            _logger.warning(
                'DSV webhook carrier mismatch: booking %s carrier=%s, webhook carrier=%s',
                booking.name, booking.carrier_id.id, carrier.id,
            )
            return

        prev_eta    = booking.eta
        prev_vessel = booking.vessel_name or ''
        state_order = [s[0] for s in BOOKING_STATES]

        for raw in (body.get('events') or []):
            event_type = raw.get('eventType', '')
            status     = _DSV_BOOKING_STATE_MAP.get(event_type, _sanitise(event_type.lower(), 64))
            event_date = _sanitise(raw.get('eventDate', ''), 50)
            location   = _sanitise(raw.get('location', ''))
            description = _sanitise(raw.get('description', ''))

            exists = booking.tracking_event_ids.filtered(
                lambda e: e.status == status and str(e.event_date) == event_date
            )
            if not exists:
                self.env['freight.tracking.event'].create({
                    'booking_id':  booking.id,
                    'event_date':  event_date,
                    'status':      status,
                    'location':    location,
                    'description': description,
                    'raw_payload': '{}',   # never log body — may contain PII
                })

            # Auto-advance state
            if status in state_order:
                cur_idx = state_order.index(booking.state) if booking.state in state_order else -1
                new_idx = state_order.index(status)
                if new_idx > cur_idx:
                    booking.state = status

        booking._check_inward_order_updates(prev_eta, prev_vessel)
```

**Step 5: Run to verify tests pass**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestDsvWebhook --stop-after-init
```
Expected: 5 tests PASS

**Step 6: Commit**

```bash
git add addons/mml_freight/models/freight_booking.py \
        addons/mml_freight_dsv/tests/test_dsv_webhook.py \
        addons/mml_freight_dsv/tests/__init__.py
git commit -m "feat: implement _handle_dsv_tracking_webhook() with carrier ownership check and sanitisation"
```

---

## Task 14: Inward Order Document Builder (in `stock_3pl_mainfreight`)

**Files (in `3pl/` repo):**
- Create: `3pl/addons/stock_3pl_mainfreight/document/inward_order.py`
- Modify: `3pl/addons/stock_3pl_mainfreight/document/__init__.py`
- Create: `3pl/addons/stock_3pl_mainfreight/tests/test_inward_order_builder.py`
- Modify: `3pl/addons/stock_3pl_mainfreight/tests/__init__.py`

**Step 1: Create test file**

`3pl/addons/stock_3pl_mainfreight/tests/test_inward_order_builder.py`:

```python
from lxml import etree
from odoo.tests.common import TransactionCase
from odoo.addons.stock_3pl_mainfreight.document.inward_order import InwardOrderDocument


class TestInwardOrderBuilder(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.warehouse = cls.env['stock.warehouse'].search([], limit=1)
        connector_vals = {
            'name': 'IO Test Connector',
            'warehouse_id': cls.warehouse.id,
            'warehouse_partner': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
        }
        if hasattr(cls.env['3pl.connector'], 'warehouse_code'):
            connector_vals['warehouse_code'] = 'AKL'
        cls.connector = cls.env['3pl.connector'].create(connector_vals)

        cls.supplier = cls.env['res.partner'].create({
            'name': 'CN Supplier', 'street': '1 Main', 'city': 'Shanghai',
            'country_id': cls.env.ref('base.cn').id,
        })
        cls.wh_partner = cls.env['res.partner'].create({
            'name': 'MF Auckland', 'street': '5 Mainfreight Dr',
            'city': 'Auckland', 'country_id': cls.env.ref('base.nz').id,
        })
        cls.warehouse.partner_id = cls.wh_partner

        cls.product = cls.env['product.product'].create({
            'name': 'Widget', 'default_code': 'WGT001', 'type': 'product',
            'x_freight_weight': 1.5,
        })
        po = cls.env['purchase.order'].create({'partner_id': cls.supplier.id})
        cls.env['purchase.order.line'].create({
            'order_id': po.id, 'product_id': cls.product.id,
            'product_qty': 100, 'price_unit': 5.0,
        })
        cls.po = po

        nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) \
              or cls.env.company.currency_id
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.env['delivery.carrier'].search([], limit=1).id,
            'currency_id': nzd.id,
            'carrier_booking_id': 'DSVBK_IO_001',
            'vessel_name': 'MSC Oscar',
            'voyage_number': 'VOY42',
            'container_number': 'CONT001',
            'transport_mode': 'sea_lcl',
            'purchase_order_id': po.id,
        })

    def _doc(self):
        return InwardOrderDocument(self.connector, self.env)

    def test_build_create_returns_xml_string(self):
        xml = self._doc().build_outbound(self.booking, action='create')
        self.assertIsInstance(xml, str)
        self.assertIn('<?xml', xml)

    def test_create_action_attribute(self):
        xml = self._doc().build_outbound(self.booking, action='create')
        root = etree.fromstring(xml.encode())
        self.assertEqual(root.get('action'), 'CREATE')

    def test_update_action_attribute(self):
        xml = self._doc().build_outbound(self.booking, action='update')
        root = etree.fromstring(xml.encode())
        self.assertEqual(root.get('action'), 'UPDATE')

    def test_order_ref_is_po_name(self):
        xml = self._doc().build_outbound(self.booking, action='create')
        root = etree.fromstring(xml.encode())
        self.assertEqual(root.findtext('OrderRef'), self.po.name)

    def test_booking_ref(self):
        xml = self._doc().build_outbound(self.booking, action='create')
        root = etree.fromstring(xml.encode())
        self.assertEqual(root.findtext('BookingRef'), 'DSVBK_IO_001')

    def test_vessel_name_in_transport(self):
        xml = self._doc().build_outbound(self.booking, action='create')
        root = etree.fromstring(xml.encode())
        transport = root.find('Transport')
        self.assertEqual(transport.findtext('Vessel'), 'MSC Oscar')

    def test_tba_vessel_when_empty(self):
        self.booking.vessel_name = ''
        xml = self._doc().build_outbound(self.booking, action='create')
        root = etree.fromstring(xml.encode())
        self.assertEqual(root.find('Transport').findtext('Vessel'), 'TBA')
        self.booking.vessel_name = 'MSC Oscar'  # restore

    def test_po_lines_in_xml(self):
        xml = self._doc().build_outbound(self.booking, action='create')
        root = etree.fromstring(xml.encode())
        lines = root.findall('.//Line')
        self.assertGreater(len(lines), 0)
        self.assertEqual(lines[0].findtext('ProductCode'), 'WGT001')

    def test_xml_is_valid(self):
        xml = self._doc().build_outbound(self.booking, action='create')
        # Must parse without error
        etree.fromstring(xml.encode())
```

**Step 2: Register test in `3pl/addons/stock_3pl_mainfreight/tests/__init__.py`**

```python
from . import test_inward_order_builder
```

**Step 3: Run to verify tests fail**

```
python odoo-bin -d <db> --test-enable -i stock_3pl_mainfreight --test-tags TestInwardOrderBuilder --stop-after-init
```
Expected: `ImportError`

**Step 4: Create `inward_order.py`**

`3pl/addons/stock_3pl_mainfreight/document/inward_order.py`:

```python
# addons/stock_3pl_mainfreight/document/inward_order.py
import logging
from lxml import etree
from odoo.addons.stock_3pl_core.models.document_base import AbstractDocument

_logger = logging.getLogger(__name__)


class InwardOrderDocument(AbstractDocument):
    document_type = 'inward_order'
    format = 'xml'

    def build_outbound(self, booking, action='create'):
        """Build Mainfreight InwardOrder XML for a freight.booking record.

        action: 'create' or 'update'
        """
        po        = booking.purchase_order_id
        warehouse = (po.picking_type_id.warehouse_id if po and po.picking_type_id else None)
        wh_partner = warehouse.partner_id if warehouse else None
        wh_code    = getattr(self.connector, 'warehouse_code', '') or ''

        root = etree.Element('InwardOrder', action=action.upper())

        self._add(root, 'OrderRef',   po.name if po else '', max_len=50)
        self._add(root, 'BookingRef', booking.carrier_booking_id or '', max_len=50)

        # Supplier
        sup_el = etree.SubElement(root, 'Supplier')
        supplier = po.partner_id if po else None
        if supplier:
            self._add(sup_el, 'Name',    supplier.name or '',  max_len=100)
            self._add(sup_el, 'Address', supplier.street or '', max_len=100)
            self._add(sup_el, 'Country',
                      supplier.country_id.code if supplier.country_id else '', max_len=3)

        # Consignee
        con_el = etree.SubElement(root, 'Consignee')
        if wh_partner:
            self._add(con_el, 'Name',    wh_partner.name or '',  max_len=100)
            self._add(con_el, 'Address', wh_partner.street or '', max_len=100)
            self._add(con_el, 'Country',
                      wh_partner.country_id.code if wh_partner.country_id else '', max_len=3)
        self._add(con_el, 'WarehouseCode', wh_code, max_len=10)

        # ETA
        if booking.eta:
            self._add(root, 'ExpectedArrival', booking.eta.strftime('%Y-%m-%d'))

        # Transport
        tr_el = etree.SubElement(root, 'Transport')
        self._add(tr_el, 'Mode',       booking.transport_mode or '',        max_len=20)
        self._add(tr_el, 'Vessel',     booking.vessel_name or 'TBA',        max_len=100)
        self._add(tr_el, 'VoyageNo',   booking.voyage_number or 'TBA',      max_len=50)
        self._add(tr_el, 'ContainerNo', booking.container_number or '',     max_len=50)

        # Lines
        lines_el = etree.SubElement(root, 'Lines')
        for line in (po.order_line if po else []):
            product = line.product_id
            line_el = etree.SubElement(lines_el, 'Line')
            self._add(line_el, 'ProductCode', product.default_code or '', max_len=40)
            self._add(line_el, 'Description', product.name or '',         max_len=100)
            self._add(line_el, 'Quantity',    str(round(line.product_qty)))
            self._add(line_el, 'UOM',         line.product_uom.name if line.product_uom else '')
            weight = getattr(product.product_tmpl_id, 'x_freight_weight', 0.0) or 0.0
            self._add(line_el, 'WeightKg', f'{weight * line.product_qty:.3f}')

        return etree.tostring(root, pretty_print=True, xml_declaration=True,
                              encoding='UTF-8').decode('utf-8')

    def _add(self, parent, tag, value, max_len=None):
        el = etree.SubElement(parent, tag)
        el.text = self.truncate(value, max_len) if max_len else str(value)

    def get_filename(self, booking):
        po_name = booking.purchase_order_id.name if booking.purchase_order_id else booking.name
        return f'inward_order_{po_name}.xml'

    def get_idempotency_key(self, booking):
        po_name = booking.purchase_order_id.name if booking.purchase_order_id else str(booking.id)
        return self.make_idempotency_key(self.connector.id, self.document_type, po_name)
```

**Step 5: Register in `3pl/addons/stock_3pl_mainfreight/document/__init__.py`**

Add:
```python
from . import inward_order
```

**Step 6: Run to verify tests pass**

```
python odoo-bin -d <db> --test-enable -i stock_3pl_mainfreight --test-tags TestInwardOrderBuilder --stop-after-init
```
Expected: 9 tests PASS

**Step 7: Commit (in the 3PL repo)**

```bash
cd E:\ClaudeCode\projects\mml.odoo.apps\mainfreight.3pl.intergration
git add addons/stock_3pl_mainfreight/document/inward_order.py \
        addons/stock_3pl_mainfreight/document/__init__.py \
        addons/stock_3pl_mainfreight/tests/test_inward_order_builder.py \
        addons/stock_3pl_mainfreight/tests/__init__.py
git commit -m "feat: InwardOrderDocument builder — CREATE + UPDATE XML for Mainfreight inward orders"
```

---

## Task 15: `_build_inward_order_payload()` — Wire Builder into Booking

**Files:**
- Modify: `addons/mml_freight/models/freight_booking.py`
- Modify: `addons/mml_freight/tests/test_3pl_handoff.py`

**Step 1: Add test**

Add to `addons/mml_freight/tests/test_3pl_handoff.py`:

```python
def test_build_inward_order_payload_populates_message(self):
    """action_confirm_with_dsv triggers _build_inward_order_payload → message state=queued."""
    if '3pl.message' not in self.env:
        self.skipTest('stock_3pl_core not installed')
    if 'inward_order' not in self.env['stock_3pl_mainfreight.inward_order_document'].__class__.__dict__ \
            if False else False:
        pass  # always proceed — skip condition adjusted below
    from unittest.mock import patch, MagicMock
    warehouse = self.env['stock.warehouse'].search([], limit=1)
    self._isolate_warehouse(warehouse)
    picking_type = self.env['stock.picking.type'].search(
        [('warehouse_id', '=', warehouse.id)], limit=1
    )
    connector = self._make_connector(warehouse)
    partner = self.env['res.partner'].create({'name': 'PL Sup'})
    po = self.env['purchase.order'].create({
        'partner_id': partner.id, 'picking_type_id': picking_type.id,
    })
    booking = self.env['freight.booking'].create({
        'carrier_id':          self.carrier.id,
        'currency_id':         self.env.company.currency_id.id,
        'carrier_booking_id':  'BK_PAYLOAD_001',
        'purchase_order_id':   po.id,
        'vessel_name':         'MSC Oscar',
        'voyage_number':       'VOY1',
        'container_number':    'CONT1',
        'state':               'draft',
    })
    mock_adapter = MagicMock()
    mock_adapter.confirm_booking.return_value = {
        'carrier_shipment_id': 'SH_PL_001', 'vessel_name': 'MSC Oscar',
        'voyage_number': 'VOY1', 'container_number': 'CONT1',
        'bill_of_lading': '', 'feeder_vessel_name': '', 'feeder_voyage_number': '', 'eta': '',
    }
    with patch.object(
        type(self.env['freight.adapter.registry']), 'get_adapter',
        return_value=mock_adapter,
    ):
        booking.action_confirm_with_dsv()

    msg = booking.tpl_message_id
    self.assertTrue(msg, '3pl.message should be created')
    self.assertEqual(msg.state, 'queued', 'Message should be queued after payload built')
    self.assertTrue(msg.payload_xml, 'payload_xml should be populated')
    self.assertIn('<InwardOrder', msg.payload_xml)
```

**Step 2: Run to verify it fails**

```
python odoo-bin -d <db> --test-enable -i mml_freight --test-tags Test3plHandoff.test_build_inward_order_payload_populates_message --stop-after-init
```
Expected: FAIL (stub returns None, message stays in draft)

**Step 3: Implement `_build_inward_order_payload()` in `freight_booking.py`**

Replace the stub:

```python
    def _build_inward_order_payload(self):
        """Build inward order XML and advance tpl_message_id to 'queued'."""
        if not self.tpl_message_id:
            return
        # Try to load InwardOrderDocument from stock_3pl_mainfreight
        try:
            from odoo.addons.stock_3pl_mainfreight.document.inward_order import InwardOrderDocument
        except ImportError:
            _logger.info(
                'freight.booking %s: stock_3pl_mainfreight not installed — skipping payload build',
                self.name,
            )
            return
        connector = self.tpl_message_id.connector_id
        if not connector:
            return
        try:
            doc = InwardOrderDocument(connector, self.env)
            xml = doc.build_outbound(self, action='create')
            self.tpl_message_id.write({'payload_xml': xml, 'state': 'queued'})
            _logger.info(
                'freight.booking %s: inward order payload built, message %s queued',
                self.name, self.tpl_message_id.id,
            )
        except Exception as e:
            _logger.error(
                'freight.booking %s: failed to build inward order payload: %s',
                self.name, e,
            )
```

**Step 4: Run to verify test passes**

```
python odoo-bin -d <db> --test-enable -i mml_freight,stock_3pl_mainfreight --test-tags Test3plHandoff.test_build_inward_order_payload_populates_message --stop-after-init
```
Expected: PASS

**Step 5: Commit**

```bash
git add addons/mml_freight/models/freight_booking.py \
        addons/mml_freight/tests/test_3pl_handoff.py
git commit -m "feat: _build_inward_order_payload() populates 3pl.message from InwardOrderDocument"
```

---

## Task 16: `DsvMockAdapter` Delegates to `DsvGenericAdapter` in Production

**Files:**
- Modify: `addons/mml_freight_dsv/adapters/dsv_mock_adapter.py`
- Modify: `addons/mml_freight_dsv/tests/test_dsv_mock_adapter.py`

**Step 1: Write test for delegation**

In `test_dsv_mock_adapter.py`, replace `test_live_raises` with:

```python
def test_production_delegates_request_quote_to_generic(self):
    """In production mode, DsvMockAdapter forwards to DsvGenericAdapter (mocked HTTP)."""
    from unittest.mock import patch
    self.carrier.x_dsv_environment = 'production'
    dsv_data = {'quotes': [{'serviceCode': 'X', 'serviceName': 'Sea', 'productType': 'SEA_LCL',
                             'totalCharge': {'amount': 1000.0, 'currency': 'NZD'}, 'transitDays': 20}]}
    with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
        with patch('requests.post', return_value=_build_mock_resp(200, dsv_data)):
            results = self.adapter.request_quote(self._tender())
    self.assertIsInstance(results, list)
    self.assertTrue(any(not r.get('_error') for r in results))
    self.carrier.x_dsv_environment = 'demo'  # restore

def test_demo_still_returns_mock_quotes(self):
    self.carrier.x_dsv_environment = 'demo'
    results = self.adapter.request_quote(self._tender())
    self.assertEqual(len(results), 2)
```

Add helper at top of the test class:

```python
import json
from unittest.mock import MagicMock

def _build_mock_resp(status, data):
    m = MagicMock()
    m.status_code = status
    m.ok = status < 400
    m.text = json.dumps(data)
    m.json.return_value = data
    return m
```

**Step 2: Run to verify `test_production_delegates_request_quote_to_generic` fails**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestDsvMockAdapter.test_production_delegates_request_quote_to_generic --stop-after-init
```
Expected: FAIL (raises `NotImplementedError`)

**Step 3: Modify `dsv_mock_adapter.py` to delegate in production**

`addons/mml_freight_dsv/adapters/dsv_mock_adapter.py`:

```python
import itertools
import datetime
from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase
from odoo.addons.mml_freight.models.freight_adapter_registry import register_adapter

_counter = itertools.count(1)


@register_adapter('dsv_generic')
@register_adapter('dsv_xpress')
class DsvMockAdapter(FreightAdapterBase):
    """Registered adapter for dsv_generic and dsv_xpress.

    demo mode  → returns hardcoded mock responses (no HTTP)
    production → delegates to DsvGenericAdapter (live HTTP)
    """

    def _demo(self):
        return getattr(self.carrier, 'x_dsv_environment', 'demo') == 'demo'

    def _live(self):
        """Return a DsvGenericAdapter instance for delegation in production mode."""
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter
        return DsvGenericAdapter(self.carrier, self.env)

    def request_quote(self, tender):
        if not self._demo():
            return self._live().request_quote(tender)
        return [
            {'service_name': 'DSV Road Standard', 'transport_mode': 'road',
             'base_rate': 1800.00, 'fuel_surcharge': 0, 'origin_charges': 0,
             'destination_charges': 0, 'customs_charges': 0, 'other_surcharges': 0,
             'total_rate': 1800.00, 'currency': 'NZD', 'transit_days': 5,
             'carrier_quote_ref': 'MOCK-ROAD-001', 'rate_valid_until': None,
             'estimated_pickup_date': None, 'estimated_delivery_date': None},
            {'service_name': 'DSV Air Express', 'transport_mode': 'air',
             'base_rate': 6200.00, 'fuel_surcharge': 0, 'origin_charges': 0,
             'destination_charges': 0, 'customs_charges': 0, 'other_surcharges': 0,
             'total_rate': 6200.00, 'currency': 'NZD', 'transit_days': 2,
             'carrier_quote_ref': 'MOCK-AIR-001', 'rate_valid_until': None,
             'estimated_pickup_date': None, 'estimated_delivery_date': None},
        ]

    def create_booking(self, tender, selected_quote):
        if not self._demo():
            return self._live().create_booking(tender, selected_quote)
        return {
            'carrier_booking_id': f'DSV-MOCK-BK-{next(_counter):04d}',
            'carrier_shipment_id': None,
            'carrier_tracking_url': None,
        }

    def get_tracking(self, booking):
        if not self._demo():
            return self._live().get_tracking(booking)
        now = datetime.datetime.utcnow()
        fmt = lambda d: d.isoformat()
        return [
            {'event_date': fmt(now - datetime.timedelta(days=3)), 'status': 'Picked Up',
             'location': 'Shanghai CN', 'description': 'Picked up.', 'raw_payload': '{}'},
            {'event_date': fmt(now - datetime.timedelta(days=2)), 'status': 'In Transit',
             'location': 'DSV Hub', 'description': 'In transit.', 'raw_payload': '{}'},
            {'event_date': fmt(now - datetime.timedelta(hours=12)), 'status': 'Arrived at Port',
             'location': 'Auckland NZ', 'description': 'Arrived.', 'raw_payload': '{}'},
        ]

    def cancel_booking(self, booking):
        if not self._demo():
            return self._live().cancel_booking(booking)
        # No-op in demo

    def confirm_booking(self, booking):
        if not self._demo():
            return self._live().confirm_booking(booking)
        # Demo confirm: return synthetic result
        return {
            'carrier_shipment_id': f'DSV-MOCK-SH-{next(_counter):04d}',
            'vessel_name': 'MOCK VESSEL',
            'voyage_number': 'MOCK-V001',
            'container_number': 'MOCK-CONT',
            'bill_of_lading': '',
            'feeder_vessel_name': '',
            'feeder_voyage_number': '',
            'eta': '',
        }
```

**Step 4: Run all mock adapter tests**

```
python odoo-bin -d <db> --test-enable -i mml_freight_dsv --test-tags TestDsvMockAdapter --stop-after-init
```
Expected: all PASS (including new delegation test, `test_live_raises` removed)

**Step 5: Commit**

```bash
git add addons/mml_freight_dsv/adapters/dsv_mock_adapter.py \
        addons/mml_freight_dsv/tests/test_dsv_mock_adapter.py
git commit -m "feat: DsvMockAdapter delegates to DsvGenericAdapter in production mode"
```

---

## Task 17: Views — Confirm Button, Feeder Vessel, CBM Thresholds

**Files:**
- Modify: `addons/mml_freight/views/freight_booking_views.xml`
- Modify: `addons/mml_freight_dsv/views/freight_carrier_dsv_views.xml`

**Step 1: Read current booking form view**

Read `addons/mml_freight/views/freight_booking_views.xml` to find the status bar and button area.

**Step 2: Add "Confirm with DSV" button to booking form**

In the booking form's `<header>` section, add after the state bar (or create one if missing):

```xml
<button name="action_confirm_with_dsv"
        string="Confirm with DSV"
        type="object"
        class="oe_highlight"
        attrs="{'invisible': [
            '|',
            ('state', '!=', 'draft'),
            ('carrier_booking_id', '=', False)
        ]}"/>
<button name="action_cancel"
        string="Cancel"
        type="object"
        attrs="{'invisible': [('state', 'in', ['cancelled', 'received'])]}"/>
```

**Step 3: Add feeder vessel fields to booking form**

In the transport/vessel group, add after `voyage_number`:

```xml
<field name="feeder_vessel_name"/>
<field name="feeder_voyage_number"/>
```

**Step 4: Read current DSV carrier view**

Read `addons/mml_freight_dsv/views/freight_carrier_dsv_views.xml` to find the DSV configuration group.

**Step 5: Add CBM threshold fields to carrier form**

In the DSV configuration group, add a new `<group>` for mode thresholds:

```xml
<group string="LCL/FCL Thresholds (CBM)">
    <field name="x_dsv_lcl_fcl_threshold"/>
    <field name="x_dsv_fcl20_fcl40_threshold"/>
    <field name="x_dsv_fcl40_upper"/>
</group>
```

**Step 6: Upgrade modules to apply view changes**

```
python odoo-bin -d <db> -u mml_freight,mml_freight_dsv --stop-after-init
```

**Step 7: Commit**

```bash
git add addons/mml_freight/views/freight_booking_views.xml \
        addons/mml_freight_dsv/views/freight_carrier_dsv_views.xml
git commit -m "feat: booking form Confirm with DSV button, feeder vessel fields, CBM threshold config"
```

---

## Final: Full Test Suite

Run everything together to confirm no regressions:

```
python odoo-bin -d <db> --test-enable \
  -i mml_freight,mml_freight_dsv,mml_freight_demo \
  --stop-after-init --log-level=test
```

And in the 3PL repo:

```
python odoo-bin -d <db> --test-enable \
  -i stock_3pl_core,stock_3pl_mainfreight \
  --stop-after-init --log-level=test
```

Expected: all tests PASS.

---

## Summary of Files Changed

| File | Action |
|------|--------|
| `addons/mml_freight/models/product_template.py` | Add `x_freight_weight` |
| `addons/mml_freight/models/freight_tender_package.py` | Wire `weight_kg` in onchange |
| `addons/mml_freight/models/purchase_order.py` | Add `_populate_tender_packages()` |
| `addons/mml_freight/models/freight_booking.py` | Add `feeder_vessel_*`, `action_confirm_with_dsv`, `action_cancel`, `_check_inward_order_updates`, `_queue_inward_order_update`, `_build_inward_order_payload`, `_sync_tracking` (updated), `_handle_dsv_tracking_webhook` (implemented) |
| `addons/mml_freight/models/freight_tender.py` | Update `action_book()` — conditional confirm + prior booking cancel |
| `addons/mml_freight/adapters/base_adapter.py` | Add `cancel_booking` default no-op |
| `addons/mml_freight/views/freight_booking_views.xml` | Confirm button, feeder vessel fields |
| `addons/mml_freight_dsv/models/freight_carrier_dsv.py` | Add CBM threshold fields |
| `addons/mml_freight_dsv/adapters/dsv_quote_builder.py` | **Create** |
| `addons/mml_freight_dsv/adapters/dsv_booking_builder.py` | **Create** |
| `addons/mml_freight_dsv/adapters/dsv_generic_adapter.py` | Full implementation |
| `addons/mml_freight_dsv/adapters/dsv_mock_adapter.py` | Delegation in production |
| `addons/mml_freight_dsv/views/freight_carrier_dsv_views.xml` | CBM threshold fields |
| `3pl/addons/stock_3pl_mainfreight/document/inward_order.py` | **Create** |
| `3pl/addons/stock_3pl_mainfreight/document/__init__.py` | Register `inward_order` |
| **8 new test files** across both repos | See design doc §10 |
