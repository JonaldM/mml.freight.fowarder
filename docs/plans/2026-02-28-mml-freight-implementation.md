# MML Freight Orchestration — Sprint 1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build four Odoo 19 modules (mml_freight, mml_freight_dsv, mml_freight_knplus, mml_freight_demo) delivering a full freight tender → quote → select → book → 3PL handoff flow with mock DSV adapter requiring only API keys to go live.

**Architecture:** mml_freight is the carrier-agnostic core (tender/quote/booking models + abstract adapter interface). mml_freight_dsv registers DSV Generic and XPress adapters with OAuth auth scaffold and a mock adapter that returns hardcoded quotes when x_dsv_environment=demo. freight.booking.action_book() queues a 3pl.message via stock_3pl_core for Mainfreight inward order handoff.

**Tech Stack:** Odoo 19, Python 3.12, odoo.tests.TransactionCase, unittest.mock.patch for HTTP, python -m py_compile for syntax checks between tasks.

**Working directory:** `E:\ClaudeCode\projects\fowarder.intergration`
**Addons target:** `E:\ClaudeCode\projects\fowarder.intergration\addons\`
**3PL dependency:** `E:\ClaudeCode\projects\mainfreight.3pl.intergration\addons\stock_3pl_core\` (already built — do not modify)

---

## Task 1: Scaffold mml_freight module skeleton

**Files:**
- Create: `addons/mml_freight/__manifest__.py`
- Create: `addons/mml_freight/__init__.py`
- Create: `addons/mml_freight/models/__init__.py`
- Create: `addons/mml_freight/adapters/__init__.py`
- Create: `addons/mml_freight/adapters/base_adapter.py`
- Create: `addons/mml_freight/controllers/__init__.py`
- Create: `addons/mml_freight/controllers/webhook.py`
- Create: `addons/mml_freight/wizards/__init__.py`
- Create: `addons/mml_freight/views/.gitkeep`
- Create: `addons/mml_freight/data/.gitkeep`
- Create: `addons/mml_freight/security/.gitkeep`
- Create: `addons/mml_freight/tests/__init__.py`
- Create: `addons/mml_freight/static/src/.gitkeep`

**Step 1: Create addons/ directory**
```bash
mkdir -p addons/mml_freight/{models,adapters,controllers,wizards,views,data,security,tests}
mkdir -p addons/mml_freight/static/src
```

**Step 2: Write `addons/mml_freight/__manifest__.py`**
```python
{
    'name': 'MML Freight Orchestration',
    'version': '19.0.1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Freight tender, quote, booking and tracking for inbound POs',
    'author': 'MML',
    'license': 'OPL-1',
    'depends': [
        'mail',
        'stock',
        'account',
        'purchase',
        'delivery',
        'stock_3pl_core',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_sequence.xml',
        'data/ir_cron.xml',
        'views/freight_carrier_views.xml',
        'views/freight_tender_views.xml',
        'views/freight_booking_views.xml',
        'views/purchase_order_views.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
}
```

**Step 3: Write `addons/mml_freight/__init__.py`**
```python
from . import models
from . import adapters
from . import controllers
from . import wizards
```

**Step 4: Write `addons/mml_freight/models/__init__.py`**
```python
from . import freight_carrier
from . import freight_tender
from . import freight_tender_package
from . import freight_tender_quote
from . import freight_booking
from . import freight_tracking_event
from . import freight_document
from . import freight_adapter_registry
from . import purchase_order
from . import product_template
```

**Step 5: Write `addons/mml_freight/adapters/__init__.py`**
```python
from .base_adapter import FreightAdapterBase
```

**Step 6: Write `addons/mml_freight/adapters/base_adapter.py`**
```python
from abc import ABC, abstractmethod


class FreightAdapterBase(ABC):
    """Abstract base for all freight carrier adapters.

    Each adapter is instantiated with the delivery.carrier record and
    the freight.tender record. Methods raise NotImplementedError by default
    so stub adapters (K+N) work without implementing every method.
    """

    def __init__(self, carrier, env):
        self.carrier = carrier
        self.env = env

    @abstractmethod
    def request_quote(self, tender):
        """Return list of quote dicts for the given tender.

        Each dict must contain:
            service_name (str), transport_mode (str), total_rate (float),
            currency (str ISO-4217), transit_days (float),
            carrier_quote_ref (str), rate_valid_until (str ISO-8601 or None),
            base_rate (float), fuel_surcharge (float),
            origin_charges (float), destination_charges (float),
            customs_charges (float), other_surcharges (float),
            estimated_pickup_date (str ISO-8601 or None),
            estimated_delivery_date (str ISO-8601 or None)
        """
        raise NotImplementedError

    @abstractmethod
    def create_booking(self, tender, selected_quote):
        """Confirm a booking. Return booking reference dict:
            carrier_booking_id (str), carrier_shipment_id (str or None),
            carrier_tracking_url (str or None)
        """
        raise NotImplementedError

    @abstractmethod
    def get_tracking(self, booking):
        """Return list of tracking event dicts:
            event_date (str ISO-8601), status (str), location (str),
            description (str), raw_payload (str)
        """
        raise NotImplementedError

    def get_label(self, booking):
        """Return label bytes or None. Optional — adapters may not support labels."""
        return None
```

**Step 7: Write `addons/mml_freight/controllers/__init__.py`**
```python
from . import webhook
```

**Step 8: Write `addons/mml_freight/controllers/webhook.py`**
```python
from odoo import http
from odoo.http import request
import logging

_logger = logging.getLogger(__name__)


class FreightWebhookController(http.Controller):

    @http.route('/freight/webhook/<int:carrier_id>', type='json', auth='none', csrf=False)
    def freight_webhook(self, carrier_id, **kwargs):
        """Generic webhook entry point — dispatches to carrier adapter."""
        carrier = request.env['delivery.carrier'].sudo().browse(carrier_id)
        if not carrier.exists():
            return {'error': 'carrier_not_found'}
        _logger.info('Freight webhook received for carrier %s', carrier_id)
        return {'status': 'received'}
```

**Step 9: Write `addons/mml_freight/wizards/__init__.py`**
```python
```

**Step 10: Syntax-check all new files**
```bash
python -m py_compile addons/mml_freight/__manifest__.py
python -m py_compile addons/mml_freight/__init__.py
python -m py_compile addons/mml_freight/models/__init__.py
python -m py_compile addons/mml_freight/adapters/__init__.py
python -m py_compile addons/mml_freight/adapters/base_adapter.py
python -m py_compile addons/mml_freight/controllers/__init__.py
python -m py_compile addons/mml_freight/controllers/webhook.py
```
Expected: no output (no errors).

**Step 11: Commit**
```bash
git add addons/mml_freight/
git commit -m "feat: scaffold mml_freight module skeleton"
```

---

## Task 2: freight.carrier model

**Files:**
- Create: `addons/mml_freight/models/freight_carrier.py`

**Step 1: Write `addons/mml_freight/models/freight_carrier.py`**
```python
from odoo import models, fields

TRANSPORT_MODES = [
    ('any', 'Any'),
    ('road', 'Road'),
    ('air', 'Air'),
    ('sea_lcl', 'Sea LCL'),
    ('sea_fcl', 'Sea FCL'),
    ('rail', 'Rail'),
    ('express', 'Express'),
]


class FreightCarrier(models.Model):
    _inherit = 'delivery.carrier'

    auto_tender = fields.Boolean(
        'Include in Auto-Tender',
        default=False,
        help='Include this carrier in automatic tender fan-out from POs.',
    )
    origin_country_ids = fields.Many2many(
        'res.country',
        'freight_carrier_origin_country_rel',
        'carrier_id', 'country_id',
        string='Eligible Origin Countries',
        help='Leave empty to allow all origins.',
    )
    dest_country_ids = fields.Many2many(
        'res.country',
        'freight_carrier_dest_country_rel',
        'carrier_id', 'country_id',
        string='Eligible Destination Countries',
        help='Leave empty to allow all destinations.',
    )
    max_weight_kg = fields.Float(
        'Max Weight (kg)',
        default=0.0,
        help='0 = no limit.',
    )
    supports_dg = fields.Boolean('Dangerous Goods Capable', default=False)
    transport_modes = fields.Selection(
        TRANSPORT_MODES,
        string='Transport Mode',
        default='any',
    )
    reliability_score = fields.Float(
        'Reliability Score',
        default=50.0,
        help='0–100. Used in best_value auto-selection scoring.',
    )

    def is_eligible(self, origin_country, dest_country, weight_kg, has_dg, mode_preference):
        """Return True if this carrier is eligible for the given shipment parameters.

        Args:
            origin_country: res.country record or None
            dest_country: res.country record or None
            weight_kg: float total chargeable weight
            has_dg: bool — shipment contains dangerous goods
            mode_preference: str selection value ('any', 'road', 'air', etc.)
        """
        self.ensure_one()
        if has_dg and not self.supports_dg:
            return False
        if self.max_weight_kg > 0 and weight_kg > self.max_weight_kg:
            return False
        if origin_country and self.origin_country_ids and origin_country not in self.origin_country_ids:
            return False
        if dest_country and self.dest_country_ids and dest_country not in self.dest_country_ids:
            return False
        if mode_preference != 'any' and self.transport_modes != 'any' and self.transport_modes != mode_preference:
            return False
        return True
```

**Step 2: Syntax-check**
```bash
python -m py_compile addons/mml_freight/models/freight_carrier.py
```
Expected: no output.

**Step 3: Commit**
```bash
git add addons/mml_freight/models/freight_carrier.py
git commit -m "feat: add freight.carrier eligibility fields and is_eligible method"
```

---

## Task 3: product.template inherit

**Files:**
- Create: `addons/mml_freight/models/product_template.py`

**Step 1: Write `addons/mml_freight/models/product_template.py`**
```python
from odoo import models, fields


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    x_freight_length = fields.Float('Length (cm)', default=0.0)
    x_freight_width = fields.Float('Width (cm)', default=0.0)
    x_freight_height = fields.Float('Height (cm)', default=0.0)
    x_dangerous_goods = fields.Boolean('Dangerous Goods', default=False)
```

**Step 2: Syntax-check**
```bash
python -m py_compile addons/mml_freight/models/product_template.py
```

**Step 3: Commit**
```bash
git add addons/mml_freight/models/product_template.py
git commit -m "feat: add freight dimension and DG fields to product.template"
```

---

## Task 4: freight.tender.package model

**Files:**
- Create: `addons/mml_freight/models/freight_tender_package.py`

**Step 1: Write `addons/mml_freight/models/freight_tender_package.py`**
```python
from odoo import models, fields, api


class FreightTenderPackage(models.Model):
    _name = 'freight.tender.package'
    _description = 'Freight Tender — Package Line'
    _order = 'id'

    tender_id = fields.Many2one(
        'freight.tender', required=True, ondelete='cascade', index=True,
    )
    product_id = fields.Many2one('product.product', string='Product')
    description = fields.Char('Description')
    quantity = fields.Integer('Qty', default=1)
    weight_kg = fields.Float('Gross Weight (kg)')
    net_weight_kg = fields.Float('Net Weight (kg)')
    length_cm = fields.Float('Length (cm)')
    width_cm = fields.Float('Width (cm)')
    height_cm = fields.Float('Height (cm)')
    volume_m3 = fields.Float(
        'Volume (m³)', compute='_compute_volume', store=True, digits=(10, 6),
    )
    hs_code = fields.Char('HS Code')
    is_dangerous = fields.Boolean('Dangerous Goods', default=False)

    @api.depends('length_cm', 'width_cm', 'height_cm', 'quantity')
    def _compute_volume(self):
        for line in self:
            if line.length_cm and line.width_cm and line.height_cm:
                line.volume_m3 = (
                    line.length_cm * line.width_cm * line.height_cm
                    / 1_000_000.0
                    * line.quantity
                )
            else:
                line.volume_m3 = 0.0

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            tmpl = self.product_id.product_tmpl_id
            self.description = self.product_id.name
            self.length_cm = tmpl.x_freight_length
            self.width_cm = tmpl.x_freight_width
            self.height_cm = tmpl.x_freight_height
            self.is_dangerous = tmpl.x_dangerous_goods
            self.hs_code = getattr(self.product_id, 'hs_code', False) or ''
```

**Step 2: Syntax-check**
```bash
python -m py_compile addons/mml_freight/models/freight_tender_package.py
```

**Step 3: Commit**
```bash
git add addons/mml_freight/models/freight_tender_package.py
git commit -m "feat: add freight.tender.package model with volume compute"
```

---

## Task 5: freight.tender.quote model

**Files:**
- Create: `addons/mml_freight/models/freight_tender_quote.py`

**Step 1: Write `addons/mml_freight/models/freight_tender_quote.py`**
```python
from odoo import models, fields, api

QUOTE_STATES = [
    ('pending', 'Pending'),
    ('received', 'Received'),
    ('expired', 'Expired'),
    ('error', 'Error'),
    ('declined', 'Declined'),
]

TRANSPORT_MODES = [
    ('road', 'Road'),
    ('air', 'Air'),
    ('sea_lcl', 'Sea LCL'),
    ('sea_fcl', 'Sea FCL'),
    ('rail', 'Rail'),
    ('express', 'Express'),
]


class FreightTenderQuote(models.Model):
    _name = 'freight.tender.quote'
    _description = 'Freight Tender — Carrier Quote'
    _order = 'total_rate_nzd asc, estimated_transit_days asc'

    tender_id = fields.Many2one(
        'freight.tender', required=True, ondelete='cascade', index=True,
    )
    carrier_id = fields.Many2one('delivery.carrier', string='Carrier', required=True)
    state = fields.Selection(QUOTE_STATES, default='pending', required=True)
    service_name = fields.Char('Service')
    transport_mode = fields.Selection(TRANSPORT_MODES)

    currency_id = fields.Many2one('res.currency', required=True)
    base_rate = fields.Monetary('Base Rate', currency_field='currency_id')
    fuel_surcharge = fields.Monetary('Fuel Surcharge', currency_field='currency_id')
    origin_charges = fields.Monetary('Origin Charges', currency_field='currency_id')
    destination_charges = fields.Monetary('Destination Charges', currency_field='currency_id')
    customs_charges = fields.Monetary('Customs Charges', currency_field='currency_id')
    other_surcharges = fields.Monetary('Other Surcharges', currency_field='currency_id')
    total_rate = fields.Monetary(
        'Total Rate', compute='_compute_total_rate', store=True, currency_field='currency_id',
    )
    total_rate_nzd = fields.Float(
        'Total Rate (NZD)', compute='_compute_total_rate_nzd', store=True, digits=(10, 2),
    )

    rate_valid_until = fields.Datetime('Rate Valid Until')
    estimated_transit_days = fields.Float('Transit Days')
    estimated_pickup_date = fields.Date('Est. Pickup')
    estimated_delivery_date = fields.Date('Est. Delivery')
    carrier_quote_ref = fields.Char('Carrier Quote Ref')
    error_message = fields.Text('Error')
    raw_response = fields.Text('Raw Response')

    is_cheapest = fields.Boolean(compute='_compute_rankings', store=True)
    is_fastest = fields.Boolean(compute='_compute_rankings', store=True)
    rank_by_cost = fields.Integer(compute='_compute_rankings', store=True)
    rank_by_speed = fields.Integer(compute='_compute_rankings', store=True)
    cost_vs_cheapest_pct = fields.Float(
        '% vs Cheapest', compute='_compute_rankings', store=True, digits=(5, 1),
    )

    @api.depends('base_rate', 'fuel_surcharge', 'origin_charges',
                 'destination_charges', 'customs_charges', 'other_surcharges')
    def _compute_total_rate(self):
        for q in self:
            q.total_rate = (
                q.base_rate + q.fuel_surcharge + q.origin_charges
                + q.destination_charges + q.customs_charges + q.other_surcharges
            )

    @api.depends('total_rate', 'currency_id')
    def _compute_total_rate_nzd(self):
        nzd = self.env.ref('base.NZD', raise_if_not_found=False)
        for q in self:
            if not q.currency_id or not q.total_rate:
                q.total_rate_nzd = 0.0
                continue
            if nzd and q.currency_id != nzd:
                q.total_rate_nzd = q.currency_id._convert(
                    q.total_rate, nzd, q.tender_id.company_id, fields.Date.today(),
                )
            else:
                q.total_rate_nzd = q.total_rate

    @api.depends('tender_id.quote_line_ids.total_rate_nzd',
                 'tender_id.quote_line_ids.estimated_transit_days',
                 'tender_id.quote_line_ids.state')
    def _compute_rankings(self):
        for q in self:
            received = q.tender_id.quote_line_ids.filtered(
                lambda x: x.state == 'received'
            )
            if not received:
                q.is_cheapest = False
                q.is_fastest = False
                q.rank_by_cost = 0
                q.rank_by_speed = 0
                q.cost_vs_cheapest_pct = 0.0
                continue

            sorted_cost = received.sorted('total_rate_nzd')
            sorted_speed = received.filtered(
                lambda x: x.estimated_transit_days > 0
            ).sorted('estimated_transit_days')

            cheapest_rate = sorted_cost[0].total_rate_nzd if sorted_cost else 0

            q.rank_by_cost = list(sorted_cost.ids).index(q.id) + 1 if q.id in sorted_cost.ids else 0
            q.is_cheapest = sorted_cost and sorted_cost[0].id == q.id
            q.is_fastest = bool(sorted_speed and sorted_speed[0].id == q.id)
            if sorted_speed:
                speed_ids = list(sorted_speed.ids)
                q.rank_by_speed = speed_ids.index(q.id) + 1 if q.id in speed_ids else 0
            else:
                q.rank_by_speed = 0

            if cheapest_rate and q.total_rate_nzd:
                q.cost_vs_cheapest_pct = (
                    (q.total_rate_nzd - cheapest_rate) / cheapest_rate * 100
                )
            else:
                q.cost_vs_cheapest_pct = 0.0
```

**Step 2: Syntax-check**
```bash
python -m py_compile addons/mml_freight/models/freight_tender_quote.py
```

**Step 3: Commit**
```bash
git add addons/mml_freight/models/freight_tender_quote.py
git commit -m "feat: add freight.tender.quote model with ranking computes"
```

---

## Task 6: freight.tracking.event and freight.document models

**Files:**
- Create: `addons/mml_freight/models/freight_tracking_event.py`
- Create: `addons/mml_freight/models/freight_document.py`

**Step 1: Write `addons/mml_freight/models/freight_tracking_event.py`**
```python
from odoo import models, fields


class FreightTrackingEvent(models.Model):
    _name = 'freight.tracking.event'
    _description = 'Freight Booking — Tracking Event'
    _order = 'event_date desc'

    booking_id = fields.Many2one(
        'freight.booking', required=True, ondelete='cascade', index=True,
    )
    event_date = fields.Datetime('Event Date', required=True)
    status = fields.Char('Status', required=True)
    location = fields.Char('Location')
    description = fields.Char('Description')
    raw_payload = fields.Text('Raw Payload')
```

**Step 2: Write `addons/mml_freight/models/freight_document.py`**
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

    booking_id = fields.Many2one(
        'freight.booking', required=True, ondelete='cascade', index=True,
    )
    doc_type = fields.Selection(DOC_TYPES, string='Type', required=True, default='other')
    attachment_id = fields.Many2one('ir.attachment', string='Attachment', ondelete='set null')
    carrier_doc_ref = fields.Char('Carrier Doc Ref')
```

**Step 3: Syntax-check**
```bash
python -m py_compile addons/mml_freight/models/freight_tracking_event.py
python -m py_compile addons/mml_freight/models/freight_document.py
```

**Step 4: Commit**
```bash
git add addons/mml_freight/models/freight_tracking_event.py addons/mml_freight/models/freight_document.py
git commit -m "feat: add freight.tracking.event and freight.document models"
```

---

## Task 7: freight.booking model

**Files:**
- Create: `addons/mml_freight/models/freight_booking.py`

**Step 1: Write `addons/mml_freight/models/freight_booking.py`**
```python
from odoo import models, fields, api
import logging

_logger = logging.getLogger(__name__)

BOOKING_STATES = [
    ('draft', 'Draft'),
    ('confirmed', 'Confirmed'),
    ('cargo_ready', 'Cargo Ready'),
    ('picked_up', 'Picked Up'),
    ('in_transit', 'In Transit'),
    ('arrived_port', 'Arrived at Port'),
    ('customs', 'Customs Clearance'),
    ('delivered', 'Delivered'),
    ('received', 'Received at Warehouse'),
    ('cancelled', 'Cancelled'),
    ('error', 'Error'),
]

TRANSPORT_MODES = [
    ('road', 'Road'),
    ('air', 'Air'),
    ('sea_lcl', 'Sea LCL'),
    ('sea_fcl', 'Sea FCL'),
    ('rail', 'Rail'),
    ('express', 'Express'),
]


class FreightBooking(models.Model):
    _name = 'freight.booking'
    _description = 'Freight Booking'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name desc'

    name = fields.Char('Reference', readonly=True, default='New', copy=False)
    state = fields.Selection(
        BOOKING_STATES, default='draft', required=True, tracking=True,
    )
    tender_id = fields.Many2one('freight.tender', ondelete='restrict', index=True)
    carrier_id = fields.Many2one('delivery.carrier', required=True, ondelete='restrict')
    purchase_order_id = fields.Many2one('purchase.order', ondelete='restrict', index=True)

    # 3PL handoff
    tpl_message_id = fields.Many2one(
        '3pl.message', string='3PL Message', ondelete='set null', readonly=True,
    )

    # Carrier references
    carrier_booking_id = fields.Char('Carrier Booking Ref', tracking=True)
    carrier_shipment_id = fields.Char('Carrier Shipment ID')
    carrier_tracking_url = fields.Char('Tracking URL')

    # Financials
    currency_id = fields.Many2one('res.currency', required=True)
    booked_rate = fields.Monetary('Booked Rate', currency_field='currency_id')
    actual_rate = fields.Monetary('Actual Rate', currency_field='currency_id')
    invoice_id = fields.Many2one('account.move', string='Freight Invoice', ondelete='set null')

    # Tracking
    current_status = fields.Char(
        'Current Status', compute='_compute_current_status', store=True,
    )
    eta = fields.Datetime('ETA')
    actual_pickup_date = fields.Datetime('Actual Pickup')
    actual_delivery_date = fields.Datetime('Actual Delivery')

    # Transport details
    transport_mode = fields.Selection(TRANSPORT_MODES)
    vessel_name = fields.Char('Vessel')
    voyage_number = fields.Char('Voyage No.')
    container_number = fields.Char('Container No.')
    bill_of_lading = fields.Char('Bill of Lading')
    awb_number = fields.Char('AWB No.')

    # Relations
    tracking_event_ids = fields.One2many(
        'freight.tracking.event', 'booking_id', string='Tracking Events',
    )
    document_ids = fields.One2many('freight.document', 'booking_id', string='Documents')
    label_attachment_id = fields.Many2one(
        'ir.attachment', string='Label', ondelete='set null',
    )
    pod_attachment_id = fields.Many2one(
        'ir.attachment', string='POD', ondelete='set null',
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('freight.booking') or 'New'
        return super().create(vals_list)

    @api.depends('tracking_event_ids.status', 'tracking_event_ids.event_date')
    def _compute_current_status(self):
        for b in self:
            latest = b.tracking_event_ids.sorted('event_date', reverse=True)
            b.current_status = latest[0].status if latest else ''

    def action_confirm(self):
        self.write({'state': 'confirmed'})
        self._queue_3pl_inward_order()
        return True

    def action_cancel(self):
        self.write({'state': 'cancelled'})
        return True

    def _queue_3pl_inward_order(self):
        """Queue an inward order notice via stock_3pl_core message queue.

        Graceful no-op if stock_3pl_core is not installed or no connector
        is configured for the purchase order's warehouse.
        """
        if 'stock_3pl_core' not in self.env.registry._init_modules:
            _logger.info(
                'freight.booking %s: stock_3pl_core not installed — skipping 3PL handoff',
                self.name,
            )
            return
        po = self.purchase_order_id
        if not po:
            return
        warehouse = po.picking_type_id.warehouse_id if po.picking_type_id else False
        if not warehouse:
            return
        connector = self.env['3pl.connector'].search([
            ('warehouse_id', '=', warehouse.id),
            ('active', '=', True),
        ], limit=1)
        if not connector:
            _logger.info(
                'freight.booking %s: no active 3PL connector for warehouse %s — skipping',
                self.name, warehouse.name,
            )
            return
        msg = self.env['3pl.message'].create({
            'connector_id': connector.id,
            'direction': 'outbound',
            'document_type': 'inward_order',
            'action': 'create',
            'ref_model': 'purchase.order',
            'ref_id': po.id,
        })
        self.tpl_message_id = msg
        _logger.info(
            'freight.booking %s: queued 3pl.message %s for PO %s',
            self.name, msg.id, po.name,
        )

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

    def _sync_tracking(self):
        """Sync tracking events from carrier adapter."""
        adapter = self.env['freight.adapter.registry'].get_adapter(self.carrier_id)
        if not adapter:
            return
        events = adapter.get_tracking(self)
        for evt in events:
            # Only create if not already present (deduplication by date+status)
            existing = self.tracking_event_ids.filtered(
                lambda e: e.status == evt.get('status') and str(e.event_date) == evt.get('event_date', '')
            )
            if not existing:
                self.env['freight.tracking.event'].create({
                    'booking_id': self.id,
                    'event_date': evt['event_date'],
                    'status': evt['status'],
                    'location': evt.get('location', ''),
                    'description': evt.get('description', ''),
                    'raw_payload': evt.get('raw_payload', ''),
                })
```

**Step 2: Syntax-check**
```bash
python -m py_compile addons/mml_freight/models/freight_booking.py
```

**Step 3: Commit**
```bash
git add addons/mml_freight/models/freight_booking.py
git commit -m "feat: add freight.booking model with 3PL handoff and tracking sync"
```

---

## Task 8: freight.adapter.registry model

**Files:**
- Create: `addons/mml_freight/models/freight_adapter_registry.py`

**Step 1: Write `addons/mml_freight/models/freight_adapter_registry.py`**
```python
from odoo import models, api
import logging

_logger = logging.getLogger(__name__)

# Module-level registry: delivery_type -> adapter class
_ADAPTER_REGISTRY = {}


def register_adapter(delivery_type):
    """Decorator: register a FreightAdapterBase subclass for a delivery_type.

    Usage in adapter modules:
        @register_adapter('dsv_generic')
        class DsvGenericAdapter(FreightAdapterBase):
            ...
    """
    def decorator(cls):
        _ADAPTER_REGISTRY[delivery_type] = cls
        _logger.info('Freight adapter registered: %s -> %s', delivery_type, cls.__name__)
        return cls
    return decorator


class FreightAdapterRegistry(models.AbstractModel):
    _name = 'freight.adapter.registry'
    _description = 'Freight Adapter Registry'

    @api.model
    def get_adapter(self, carrier):
        """Return an instantiated adapter for the given delivery.carrier record.

        Returns None if no adapter is registered for the carrier's delivery_type.
        """
        delivery_type = carrier.delivery_type
        cls = _ADAPTER_REGISTRY.get(delivery_type)
        if not cls:
            _logger.warning('No freight adapter registered for delivery_type: %s', delivery_type)
            return None
        return cls(carrier, self.env)

    @api.model
    def get_eligible_carriers(self, tender):
        """Return freight.carrier records eligible for the given tender."""
        all_carriers = self.env['delivery.carrier'].search([
            ('active', '=', True),
            ('auto_tender', '=', True),
        ])
        eligible = self.env['delivery.carrier']
        for carrier in all_carriers:
            if carrier.is_eligible(
                tender.origin_country_id,
                tender.dest_country_id,
                tender.chargeable_weight_kg,
                tender.contains_dg,
                tender.freight_mode_preference or 'any',
            ):
                eligible |= carrier
        return eligible
```

**Step 2: Syntax-check**
```bash
python -m py_compile addons/mml_freight/models/freight_adapter_registry.py
```

**Step 3: Commit**
```bash
git add addons/mml_freight/models/freight_adapter_registry.py
git commit -m "feat: add freight adapter registry with register_adapter decorator"
```

---

## Task 9: freight.tender model

**Files:**
- Create: `addons/mml_freight/models/freight_tender.py`

**Step 1: Write `addons/mml_freight/models/freight_tender.py`**
```python
from odoo import models, fields, api
from odoo.exceptions import UserError
import logging
from datetime import timedelta

_logger = logging.getLogger(__name__)

TENDER_STATES = [
    ('draft', 'Draft'),
    ('requesting', 'Requesting Quotes'),
    ('quoted', 'Quoted'),
    ('partial', 'Partial Quotes'),
    ('selected', 'Quote Selected'),
    ('booked', 'Booked'),
    ('expired', 'Expired'),
    ('cancelled', 'Cancelled'),
]

SELECTION_MODES = [
    ('cheapest', 'Cheapest'),
    ('fastest', 'Fastest'),
    ('best_value', 'Best Value'),
    ('manual', 'Manual'),
]

MODE_PREFERENCES = [
    ('any', 'Any'),
    ('sea', 'Sea'),
    ('air', 'Air'),
    ('road', 'Road'),
]


class FreightTender(models.Model):
    _name = 'freight.tender'
    _description = 'Freight Tender'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name desc'

    name = fields.Char('Reference', readonly=True, default='New', copy=False)
    state = fields.Selection(TENDER_STATES, default='draft', required=True, tracking=True)
    purchase_order_id = fields.Many2one(
        'purchase.order', required=True, ondelete='restrict', index=True,
    )
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company,
    )

    # Origin
    origin_partner_id = fields.Many2one('res.partner', string='Ship From (Supplier)')
    origin_country_id = fields.Many2one('res.country', string='Origin Country')
    origin_port = fields.Char('Origin Port')

    # Destination
    dest_partner_id = fields.Many2one('res.partner', string='Ship To (Warehouse)')
    dest_country_id = fields.Many2one('res.country', string='Destination Country')
    dest_port = fields.Char('Destination Port')

    # Cargo details
    incoterm_id = fields.Many2one('account.incoterms', string='Incoterm')
    requested_pickup_date = fields.Date('Cargo Ready Date')
    requested_delivery_date = fields.Date('Required at Warehouse')
    tender_expiry = fields.Datetime('Tender Expiry')
    freight_mode_preference = fields.Selection(MODE_PREFERENCES, default='any')

    # Computed aggregates
    total_weight_kg = fields.Float(
        'Total Weight (kg)', compute='_compute_totals', store=True,
    )
    total_volume_m3 = fields.Float(
        'Total Volume (m³)', compute='_compute_totals', store=True, digits=(10, 4),
    )
    total_cbm = fields.Float(
        'Total CBM', compute='_compute_totals', store=True, digits=(10, 4),
    )
    total_packages = fields.Integer(
        'Total Packages', compute='_compute_totals', store=True,
    )
    chargeable_weight_kg = fields.Float(
        'Chargeable Weight (kg)', compute='_compute_totals', store=True,
    )
    contains_dg = fields.Boolean(
        'Contains DG', compute='_compute_totals', store=True,
    )

    # Goods value
    goods_value = fields.Monetary('Goods Value', currency_field='currency_id')
    currency_id = fields.Many2one('res.currency')

    # Lines
    package_line_ids = fields.One2many(
        'freight.tender.package', 'tender_id', string='Package Lines',
    )
    quote_line_ids = fields.One2many(
        'freight.tender.quote', 'tender_id', string='Quotes',
    )

    # Selection
    cheapest_quote_id = fields.Many2one(
        'freight.tender.quote', compute='_compute_cheapest_quote', store=True,
    )
    selected_quote_id = fields.Many2one(
        'freight.tender.quote', string='Selected Quote', ondelete='set null',
    )
    selection_mode = fields.Selection(SELECTION_MODES, default='manual')
    selection_reason = fields.Text('Selection Reason')

    # Booking
    booking_id = fields.Many2one('freight.booking', string='Booking', ondelete='set null')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('freight.tender') or 'New'
            if not vals.get('tender_expiry'):
                vals['tender_expiry'] = fields.Datetime.now() + timedelta(days=3)
        return super().create(vals_list)

    @api.depends('package_line_ids.weight_kg', 'package_line_ids.volume_m3',
                 'package_line_ids.quantity', 'package_line_ids.is_dangerous')
    def _compute_totals(self):
        for t in self:
            lines = t.package_line_ids
            total_weight = sum(lines.mapped('weight_kg'))
            total_vol = sum(lines.mapped('volume_m3'))
            total_qty = sum(lines.mapped('quantity'))
            # Volumetric weight: CBM × 333 (standard sea freight factor)
            volumetric_weight = total_vol * 333
            t.total_weight_kg = total_weight
            t.total_volume_m3 = total_vol
            t.total_cbm = total_vol  # alias
            t.total_packages = total_qty
            t.chargeable_weight_kg = max(total_weight, volumetric_weight)
            t.contains_dg = any(lines.mapped('is_dangerous'))

    @api.depends('quote_line_ids.total_rate_nzd', 'quote_line_ids.state')
    def _compute_cheapest_quote(self):
        for t in self:
            received = t.quote_line_ids.filtered(lambda q: q.state == 'received')
            if received:
                t.cheapest_quote_id = received.sorted('total_rate_nzd')[0]
            else:
                t.cheapest_quote_id = False

    def action_request_quotes(self):
        """Fan out quote requests to all eligible carriers."""
        self.ensure_one()
        if self.state not in ('draft', 'partial'):
            raise UserError('Can only request quotes from Draft or Partial Quotes state.')
        self.write({'state': 'requesting'})
        registry = self.env['freight.adapter.registry']
        carriers = registry.get_eligible_carriers(self)
        if not carriers:
            raise UserError('No eligible carriers found for this tender. Check carrier configuration.')
        for carrier in carriers:
            # Create pending quote line
            quote = self.env['freight.tender.quote'].create({
                'tender_id': self.id,
                'carrier_id': carrier.id,
                'state': 'pending',
                'currency_id': self.currency_id.id or self.env.company.currency_id.id,
            })
            # Request quote from adapter
            adapter = registry.get_adapter(carrier)
            if not adapter:
                quote.write({'state': 'error', 'error_message': 'No adapter registered for this carrier.'})
                continue
            try:
                results = adapter.request_quote(self)
                if results:
                    # Use first result for this quote line; multiple services → multiple lines
                    for i, result in enumerate(results):
                        target_quote = quote if i == 0 else self.env['freight.tender.quote'].create({
                            'tender_id': self.id,
                            'carrier_id': carrier.id,
                            'state': 'pending',
                            'currency_id': self.currency_id.id or self.env.company.currency_id.id,
                        })
                        curr = self.env['res.currency'].search(
                            [('name', '=', result.get('currency', 'NZD'))], limit=1,
                        )
                        target_quote.write({
                            'state': 'received',
                            'service_name': result.get('service_name', ''),
                            'transport_mode': result.get('transport_mode', 'road'),
                            'currency_id': curr.id if curr else target_quote.currency_id.id,
                            'base_rate': result.get('base_rate', result.get('total_rate', 0)),
                            'fuel_surcharge': result.get('fuel_surcharge', 0),
                            'origin_charges': result.get('origin_charges', 0),
                            'destination_charges': result.get('destination_charges', 0),
                            'customs_charges': result.get('customs_charges', 0),
                            'other_surcharges': result.get('other_surcharges', 0),
                            'estimated_transit_days': result.get('transit_days', 0),
                            'carrier_quote_ref': result.get('carrier_quote_ref', ''),
                            'raw_response': str(result),
                        })
            except Exception as e:
                _logger.error('Quote request failed for carrier %s: %s', carrier.name, e)
                quote.write({'state': 'error', 'error_message': str(e)[:500]})

        received = self.quote_line_ids.filtered(lambda q: q.state == 'received')
        pending_or_error = self.quote_line_ids.filtered(lambda q: q.state in ('pending', 'error'))
        if received and not pending_or_error:
            self.state = 'quoted'
        elif received:
            self.state = 'partial'
        else:
            self.state = 'partial'
        return True

    def action_auto_select(self):
        """Auto-select best quote based on selection_mode."""
        self.ensure_one()
        received = self.quote_line_ids.filtered(lambda q: q.state == 'received')
        if not received:
            raise UserError('No received quotes to select from.')

        mode = self.selection_mode or 'cheapest'
        if mode == 'cheapest':
            winner = received.sorted('total_rate_nzd')[0]
            reason = f'Auto-selected: cheapest rate ({winner.total_rate_nzd:.2f} NZD)'
        elif mode == 'fastest':
            with_days = received.filtered(lambda q: q.estimated_transit_days > 0)
            if not with_days:
                raise UserError('No quotes with transit days for fastest selection.')
            winner = with_days.sorted('estimated_transit_days')[0]
            reason = f'Auto-selected: fastest transit ({winner.estimated_transit_days:.1f} days)'
        elif mode == 'best_value':
            # Score = 0.6 * cost_rank + 0.4 * reliability — lower is better
            def best_value_score(q):
                cost_score = q.rank_by_cost or 99
                reliability = q.carrier_id.reliability_score or 50
                return cost_score * 0.6 + (100 - reliability) * 0.4 / 10
            winner = received.sorted(best_value_score)[0]
            reason = f'Auto-selected: best value (cost rank {winner.rank_by_cost}, reliability {winner.carrier_id.reliability_score:.0f})'
        else:
            raise UserError('Manual selection mode: select a quote manually.')

        self.write({
            'selected_quote_id': winner.id,
            'state': 'selected',
            'selection_reason': reason,
        })
        self.message_post(body=reason)
        return True

    def action_book(self):
        """Confirm booking with selected carrier."""
        self.ensure_one()
        if not self.selected_quote_id:
            raise UserError('Select a quote before booking.')
        if self.state != 'selected':
            raise UserError('Tender must be in Selected state to book.')

        registry = self.env['freight.adapter.registry']
        adapter = registry.get_adapter(self.selected_quote_id.carrier_id)
        if not adapter:
            raise UserError('No adapter available for selected carrier.')

        result = adapter.create_booking(self, self.selected_quote_id)

        booking = self.env['freight.booking'].create({
            'tender_id': self.id,
            'carrier_id': self.selected_quote_id.carrier_id.id,
            'purchase_order_id': self.purchase_order_id.id,
            'currency_id': self.selected_quote_id.currency_id.id,
            'booked_rate': self.selected_quote_id.total_rate,
            'transport_mode': self.selected_quote_id.transport_mode,
            'carrier_booking_id': result.get('carrier_booking_id', ''),
            'carrier_shipment_id': result.get('carrier_shipment_id', ''),
            'carrier_tracking_url': result.get('carrier_tracking_url', ''),
            'state': 'draft',
        })
        booking.action_confirm()

        self.write({
            'booking_id': booking.id,
            'state': 'booked',
        })
        if self.purchase_order_id:
            self.purchase_order_id.freight_tender_id = self
        return True

    def action_cancel(self):
        self.write({'state': 'cancelled'})
        return True
```

**Step 2: Syntax-check**
```bash
python -m py_compile addons/mml_freight/models/freight_tender.py
```

**Step 3: Commit**
```bash
git add addons/mml_freight/models/freight_tender.py
git commit -m "feat: add freight.tender model with request_quotes, auto_select, action_book"
```

---

## Task 10: purchase.order inherit

**Files:**
- Create: `addons/mml_freight/models/purchase_order.py`

**Step 1: Write `addons/mml_freight/models/purchase_order.py`**
```python
from odoo import models, fields, api

INCOTERMS_BUYER = {'EXW', 'FCA', 'FOB', 'FAS'}
INCOTERMS_SELLER = {'CFR', 'CIF', 'CPT', 'CIP', 'DAP', 'DPU', 'DDP'}

FREIGHT_RESPONSIBILITY = [
    ('buyer', 'Buyer (MML arranges)'),
    ('seller', 'Seller (Supplier arranges)'),
    ('na', 'Not Applicable'),
]

MODE_PREFERENCES = [
    ('any', 'Any'),
    ('sea', 'Sea'),
    ('air', 'Air'),
    ('road', 'Road'),
]


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    freight_responsibility = fields.Selection(
        FREIGHT_RESPONSIBILITY,
        string='Freight Responsibility',
        compute='_compute_freight_responsibility',
        store=True,
        readonly=False,
    )
    freight_tender_id = fields.Many2one(
        'freight.tender', string='Freight Tender', ondelete='set null',
    )
    freight_booking_id = fields.Many2one(
        'freight.booking',
        related='freight_tender_id.booking_id',
        string='Freight Booking',
        readonly=True,
    )
    freight_status = fields.Selection(
        related='freight_tender_id.booking_id.state',
        string='Freight Status',
        readonly=True,
    )
    freight_cost = fields.Monetary(
        related='freight_tender_id.booking_id.booked_rate',
        string='Freight Cost',
        readonly=True,
        currency_field='currency_id',
    )
    freight_carrier_name = fields.Char(
        related='freight_tender_id.booking_id.carrier_id.name',
        string='Freight Carrier',
        readonly=True,
    )
    freight_tracking_url = fields.Char(
        related='freight_tender_id.booking_id.carrier_tracking_url',
        string='Tracking URL',
        readonly=True,
    )
    freight_eta = fields.Datetime(
        related='freight_tender_id.booking_id.eta',
        string='ETA',
        readonly=True,
    )
    cargo_ready_date = fields.Date('Cargo Ready Date')
    required_delivery_date = fields.Date('Required at Warehouse')
    freight_mode_preference = fields.Selection(MODE_PREFERENCES, default='any')
    tender_count = fields.Integer(compute='_compute_tender_count')

    @api.depends('incoterm_id', 'incoterm_id.code')
    def _compute_freight_responsibility(self):
        for po in self:
            code = po.incoterm_id.code if po.incoterm_id else False
            if not code:
                po.freight_responsibility = 'na'
            elif code in INCOTERMS_BUYER:
                po.freight_responsibility = 'buyer'
            elif code in INCOTERMS_SELLER:
                po.freight_responsibility = 'seller'
            else:
                po.freight_responsibility = 'na'

    def _compute_tender_count(self):
        for po in self:
            po.tender_count = self.env['freight.tender'].search_count([
                ('purchase_order_id', '=', po.id),
            ])

    def action_view_freight_tenders(self):
        self.ensure_one()
        return {
            'name': 'Freight Tenders',
            'type': 'ir.actions.act_window',
            'res_model': 'freight.tender',
            'view_mode': 'list,form',
            'domain': [('purchase_order_id', '=', self.id)],
            'context': {
                'default_purchase_order_id': self.id,
            },
        }

    def action_request_freight_tender(self):
        """Open a new freight tender linked to this PO."""
        self.ensure_one()
        tender = self.env['freight.tender'].create({
            'purchase_order_id': self.id,
            'company_id': self.company_id.id,
            'origin_partner_id': self.partner_id.id,
            'origin_country_id': self.partner_id.country_id.id if self.partner_id.country_id else False,
            'incoterm_id': self.incoterm_id.id if self.incoterm_id else False,
            'requested_pickup_date': self.cargo_ready_date,
            'requested_delivery_date': self.required_delivery_date,
            'goods_value': self.amount_untaxed,
            'currency_id': self.currency_id.id,
            'freight_mode_preference': self.freight_mode_preference or 'any',
        })
        self.freight_tender_id = tender
        return {
            'name': 'Freight Tender',
            'type': 'ir.actions.act_window',
            'res_model': 'freight.tender',
            'res_id': tender.id,
            'view_mode': 'form',
        }
```

**Step 2: Syntax-check**
```bash
python -m py_compile addons/mml_freight/models/purchase_order.py
```

**Step 3: Commit**
```bash
git add addons/mml_freight/models/purchase_order.py
git commit -m "feat: extend purchase.order with freight responsibility, tender link, and smart button"
```

---

## Task 11: security/ir.model.access.csv and data XML

**Files:**
- Create: `addons/mml_freight/security/ir.model.access.csv`
- Create: `addons/mml_freight/data/ir_sequence.xml`
- Create: `addons/mml_freight/data/ir_cron.xml`

**Step 1: Write `addons/mml_freight/security/ir.model.access.csv`**
```csv
id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink
access_freight_carrier_user,freight.carrier user,delivery.model_delivery_carrier,stock.group_stock_user,1,0,0,0
access_freight_carrier_manager,freight.carrier manager,delivery.model_delivery_carrier,stock.group_stock_manager,1,1,1,1
access_freight_tender_user,freight.tender user,model_freight_tender,stock.group_stock_user,1,1,1,0
access_freight_tender_manager,freight.tender manager,model_freight_tender,stock.group_stock_manager,1,1,1,1
access_freight_tender_package_user,freight.tender.package user,model_freight_tender_package,stock.group_stock_user,1,1,1,0
access_freight_tender_package_manager,freight.tender.package manager,model_freight_tender_package,stock.group_stock_manager,1,1,1,1
access_freight_tender_quote_user,freight.tender.quote user,model_freight_tender_quote,stock.group_stock_user,1,1,1,0
access_freight_tender_quote_manager,freight.tender.quote manager,model_freight_tender_quote,stock.group_stock_manager,1,1,1,1
access_freight_booking_user,freight.booking user,model_freight_booking,stock.group_stock_user,1,0,0,0
access_freight_booking_manager,freight.booking manager,model_freight_booking,stock.group_stock_manager,1,1,1,1
access_freight_tracking_event_user,freight.tracking.event user,model_freight_tracking_event,stock.group_stock_user,1,0,0,0
access_freight_tracking_event_manager,freight.tracking.event manager,model_freight_tracking_event,stock.group_stock_manager,1,1,1,1
access_freight_document_user,freight.document user,model_freight_document,stock.group_stock_user,1,0,0,0
access_freight_document_manager,freight.document manager,model_freight_document,stock.group_stock_manager,1,1,1,1
access_freight_adapter_registry,freight.adapter.registry all,model_freight_adapter_registry,base.group_user,1,0,0,0
```

**Step 2: Write `addons/mml_freight/data/ir_sequence.xml`**
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="seq_freight_tender" model="ir.sequence">
        <field name="name">Freight Tender</field>
        <field name="code">freight.tender</field>
        <field name="prefix">FT/%(year)s/</field>
        <field name="padding">5</field>
        <field name="company_id" eval="False"/>
    </record>
    <record id="seq_freight_booking" model="ir.sequence">
        <field name="name">Freight Booking</field>
        <field name="code">freight.booking</field>
        <field name="prefix">FB/%(year)s/</field>
        <field name="padding">5</field>
        <field name="company_id" eval="False"/>
    </record>
</odoo>
```

**Step 3: Write `addons/mml_freight/data/ir_cron.xml`**
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="cron_freight_tracking_sync" model="ir.cron">
        <field name="name">Freight: Sync Tracking Events</field>
        <field name="model_id" ref="model_freight_booking"/>
        <field name="state">code</field>
        <field name="code">model.cron_sync_tracking()</field>
        <field name="interval_number">30</field>
        <field name="interval_type">minutes</field>
        <field name="numbercall">-1</field>
        <field name="active">True</field>
    </record>
    <record id="cron_dsv_token_refresh" model="ir.cron">
        <field name="name">Freight DSV: Refresh OAuth Tokens</field>
        <field name="model_id" ref="delivery.model_delivery_carrier"/>
        <field name="state">code</field>
        <field name="code">model.cron_refresh_dsv_tokens()</field>
        <field name="interval_number">8</field>
        <field name="interval_type">minutes</field>
        <field name="numbercall">-1</field>
        <field name="active">True</field>
    </record>
</odoo>
```

**Step 4: Commit**
```bash
git add addons/mml_freight/security/ addons/mml_freight/data/
git commit -m "feat: add mml_freight security ACL, sequences, and cron jobs"
```

---

## Task 12: mml_freight views

**Files:**
- Create: `addons/mml_freight/views/freight_carrier_views.xml`
- Create: `addons/mml_freight/views/freight_tender_views.xml`
- Create: `addons/mml_freight/views/freight_booking_views.xml`
- Create: `addons/mml_freight/views/purchase_order_views.xml`
- Create: `addons/mml_freight/views/menu.xml`

**Step 1: Write `addons/mml_freight/views/freight_carrier_views.xml`**
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
                </group>
                <group string="Eligible Lanes" name="freight_lanes">
                    <field name="origin_country_ids" widget="many2many_tags"/>
                    <field name="dest_country_ids" widget="many2many_tags"/>
                </group>
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

**Step 2: Write `addons/mml_freight/views/freight_tender_views.xml`**
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_freight_tender_list" model="ir.ui.view">
        <field name="name">freight.tender.list</field>
        <field name="model">freight.tender</field>
        <field name="arch" type="xml">
            <list string="Freight Tenders" decoration-info="state == 'draft'" decoration-success="state == 'booked'">
                <field name="name"/>
                <field name="purchase_order_id"/>
                <field name="origin_partner_id"/>
                <field name="incoterm_id"/>
                <field name="chargeable_weight_kg"/>
                <field name="state" widget="badge"/>
            </list>
        </field>
    </record>
    <record id="view_freight_tender_form" model="ir.ui.view">
        <field name="name">freight.tender.form</field>
        <field name="model">freight.tender</field>
        <field name="arch" type="xml">
            <form string="Freight Tender">
                <header>
                    <button name="action_request_quotes" string="Request Quotes" type="object" class="btn-primary" invisible="state not in ('draft', 'partial')"/>
                    <button name="action_auto_select" string="Auto-Select Best" type="object" invisible="state not in ('quoted', 'partial')"/>
                    <button name="action_book" string="Book Selected" type="object" class="btn-primary" invisible="state != 'selected'"/>
                    <button name="action_cancel" string="Cancel" type="object" invisible="state in ('booked', 'cancelled')"/>
                    <field name="state" widget="statusbar" statusbar_visible="draft,requesting,quoted,selected,booked"/>
                </header>
                <sheet>
                    <div class="oe_title"><h1><field name="name"/></h1></div>
                    <group>
                        <group string="Shipment">
                            <field name="purchase_order_id"/>
                            <field name="incoterm_id"/>
                            <field name="freight_mode_preference"/>
                            <field name="selection_mode"/>
                            <field name="tender_expiry"/>
                        </group>
                        <group string="Cargo Summary">
                            <field name="total_weight_kg"/>
                            <field name="total_cbm"/>
                            <field name="chargeable_weight_kg"/>
                            <field name="total_packages"/>
                            <field name="contains_dg" widget="boolean_toggle"/>
                            <field name="goods_value"/>
                            <field name="currency_id" invisible="1"/>
                        </group>
                    </group>
                    <group>
                        <group string="Origin">
                            <field name="origin_partner_id"/>
                            <field name="origin_country_id"/>
                            <field name="origin_port"/>
                            <field name="requested_pickup_date"/>
                        </group>
                        <group string="Destination">
                            <field name="dest_partner_id"/>
                            <field name="dest_country_id"/>
                            <field name="dest_port"/>
                            <field name="requested_delivery_date"/>
                        </group>
                    </group>
                    <notebook>
                        <page string="Packages">
                            <field name="package_line_ids">
                                <list editable="bottom">
                                    <field name="product_id"/>
                                    <field name="description"/>
                                    <field name="quantity"/>
                                    <field name="weight_kg"/>
                                    <field name="length_cm"/>
                                    <field name="width_cm"/>
                                    <field name="height_cm"/>
                                    <field name="volume_m3" readonly="1"/>
                                    <field name="hs_code"/>
                                    <field name="is_dangerous"/>
                                </list>
                            </field>
                        </page>
                        <page string="Quotes">
                            <field name="quote_line_ids">
                                <list decoration-success="is_cheapest == True" decoration-info="is_fastest == True" decoration-muted="state == 'error'">
                                    <field name="carrier_id"/>
                                    <field name="service_name"/>
                                    <field name="transport_mode"/>
                                    <field name="total_rate_nzd"/>
                                    <field name="cost_vs_cheapest_pct" string="% vs Cheapest"/>
                                    <field name="estimated_transit_days"/>
                                    <field name="state" widget="badge"/>
                                    <field name="is_cheapest" invisible="1"/>
                                    <field name="is_fastest" invisible="1"/>
                                </list>
                            </field>
                            <group>
                                <field name="selected_quote_id"/>
                                <field name="selection_reason"/>
                                <field name="cheapest_quote_id" readonly="1"/>
                            </group>
                        </page>
                    </notebook>
                </sheet>
                <chatter/>
            </form>
        </field>
    </record>
    <record id="action_freight_tender" model="ir.actions.act_window">
        <field name="name">Freight Tenders</field>
        <field name="res_model">freight.tender</field>
        <field name="view_mode">list,form</field>
    </record>
</odoo>
```

**Step 3: Write `addons/mml_freight/views/freight_booking_views.xml`**
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_freight_booking_list" model="ir.ui.view">
        <field name="name">freight.booking.list</field>
        <field name="model">freight.booking</field>
        <field name="arch" type="xml">
            <list string="Freight Bookings" decoration-success="state == 'delivered'" decoration-warning="state == 'in_transit'">
                <field name="name"/>
                <field name="carrier_id"/>
                <field name="purchase_order_id"/>
                <field name="carrier_booking_id"/>
                <field name="transport_mode"/>
                <field name="booked_rate"/>
                <field name="currency_id" invisible="1"/>
                <field name="eta"/>
                <field name="current_status"/>
                <field name="state" widget="badge"/>
            </list>
        </field>
    </record>
    <record id="view_freight_booking_form" model="ir.ui.view">
        <field name="name">freight.booking.form</field>
        <field name="model">freight.booking</field>
        <field name="arch" type="xml">
            <form string="Freight Booking">
                <header>
                    <button name="action_cancel" string="Cancel" type="object" invisible="state in ('delivered','received','cancelled')"/>
                    <field name="state" widget="statusbar" statusbar_visible="draft,confirmed,picked_up,in_transit,arrived_port,delivered,received"/>
                </header>
                <sheet>
                    <div class="oe_title"><h1><field name="name"/></h1></div>
                    <group>
                        <group string="Booking">
                            <field name="carrier_id"/>
                            <field name="purchase_order_id"/>
                            <field name="tender_id"/>
                            <field name="carrier_booking_id"/>
                            <field name="carrier_tracking_url" widget="url"/>
                        </group>
                        <group string="Tracking">
                            <field name="current_status"/>
                            <field name="eta"/>
                            <field name="tpl_message_id" readonly="1"/>
                        </group>
                    </group>
                    <group>
                        <group string="Transport">
                            <field name="transport_mode"/>
                            <field name="vessel_name"/>
                            <field name="container_number"/>
                            <field name="bill_of_lading"/>
                            <field name="awb_number"/>
                        </group>
                        <group string="Financials">
                            <field name="booked_rate"/>
                            <field name="actual_rate"/>
                            <field name="currency_id" invisible="1"/>
                            <field name="invoice_id"/>
                        </group>
                    </group>
                    <notebook>
                        <page string="Tracking Events">
                            <field name="tracking_event_ids" readonly="1">
                                <list><field name="event_date"/><field name="status"/><field name="location"/><field name="description"/></list>
                            </field>
                        </page>
                        <page string="Documents">
                            <field name="document_ids">
                                <list><field name="doc_type"/><field name="attachment_id"/><field name="carrier_doc_ref"/></list>
                            </field>
                        </page>
                    </notebook>
                </sheet>
                <chatter/>
            </form>
        </field>
    </record>
    <record id="action_freight_booking" model="ir.actions.act_window">
        <field name="name">Freight Bookings</field>
        <field name="res_model">freight.booking</field>
        <field name="view_mode">list,form</field>
    </record>
</odoo>
```

**Step 4: Write `addons/mml_freight/views/purchase_order_views.xml`**
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_purchase_order_form_freight" model="ir.ui.view">
        <field name="name">purchase.order.form.freight</field>
        <field name="model">purchase.order</field>
        <field name="inherit_id" ref="purchase.purchase_order_form"/>
        <field name="arch" type="xml">
            <xpath expr="//div[@name='button_box']" position="inside">
                <button name="action_view_freight_tenders" type="object"
                        class="oe_stat_button" icon="fa-truck" invisible="tender_count == 0">
                    <field name="tender_count" widget="statinfo" string="Freight Tender"/>
                </button>
            </xpath>
            <xpath expr="//page[@name='purchase_delivery_bill']" position="after">
                <page string="Freight" name="freight_page" invisible="freight_responsibility == 'na'">
                    <group>
                        <group string="Responsibility">
                            <field name="freight_responsibility" readonly="1"/>
                            <field name="cargo_ready_date"/>
                            <field name="required_delivery_date"/>
                            <field name="freight_mode_preference"/>
                        </group>
                        <group string="Booking Status" invisible="not freight_booking_id">
                            <field name="freight_carrier_name" readonly="1"/>
                            <field name="freight_cost" readonly="1"/>
                            <field name="freight_status" widget="badge" readonly="1"/>
                            <field name="freight_eta" readonly="1"/>
                            <field name="freight_tracking_url" widget="url" readonly="1"/>
                        </group>
                    </group>
                    <button name="action_request_freight_tender" string="Request Freight Tender"
                            type="object" class="btn-primary"
                            invisible="freight_responsibility != 'buyer' or freight_tender_id != False"/>
                </page>
            </xpath>
        </field>
    </record>
</odoo>
```

**Step 5: Write `addons/mml_freight/views/menu.xml`**
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <menuitem id="menu_freight_root" name="Freight" sequence="50"
              parent="stock.menu_stock_root" groups="stock.group_stock_user"/>
    <menuitem id="menu_freight_tenders" name="Freight Tenders"
              parent="menu_freight_root" action="action_freight_tender" sequence="10"/>
    <menuitem id="menu_freight_bookings" name="Freight Bookings"
              parent="menu_freight_root" action="action_freight_booking" sequence="20"/>
    <menuitem id="menu_freight_carriers" name="Freight Carriers"
              parent="menu_freight_root" action="action_freight_carrier" sequence="30"/>
</odoo>
```

**Step 6: Commit**
```bash
git add addons/mml_freight/views/
git commit -m "feat: add mml_freight views — tender, booking, PO freight tab, menus"
```

---

## Task 13: mml_freight test files

**Files:**
- Create: `addons/mml_freight/tests/test_freight_responsibility.py`
- Create: `addons/mml_freight/tests/test_package_aggregation.py`
- Create: `addons/mml_freight/tests/test_carrier_eligibility.py`
- Create: `addons/mml_freight/tests/test_quote_ranking.py`
- Create: `addons/mml_freight/tests/test_auto_select.py`
- Create: `addons/mml_freight/tests/test_tender_lifecycle.py`
- Create: `addons/mml_freight/tests/test_3pl_handoff.py`
- Create: `addons/mml_freight/tests/test_po_form_fields.py`

**Step 1: Write `addons/mml_freight/tests/test_freight_responsibility.py`**
```python
from odoo.tests.common import TransactionCase

class TestFreightResponsibility(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Resp Supplier', 'supplier_rank': 1})
        cls.product = cls.env['product.product'].create({'name': 'Test Prod', 'type': 'product'})

    def _make_po(self, code):
        inc = self.env['account.incoterms'].search([('code', '=', code)], limit=1)
        if not inc:
            inc = self.env['account.incoterms'].create({'name': code, 'code': code})
        return self.env['purchase.order'].create({
            'partner_id': self.partner.id, 'incoterm_id': inc.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_qty': 1, 'price_unit': 10, 'name': 'x'})],
        })

    def test_exw_buyer(self): self.assertEqual(self._make_po('EXW').freight_responsibility, 'buyer')
    def test_fca_buyer(self): self.assertEqual(self._make_po('FCA').freight_responsibility, 'buyer')
    def test_fob_buyer(self): self.assertEqual(self._make_po('FOB').freight_responsibility, 'buyer')
    def test_fas_buyer(self): self.assertEqual(self._make_po('FAS').freight_responsibility, 'buyer')
    def test_cfr_seller(self): self.assertEqual(self._make_po('CFR').freight_responsibility, 'seller')
    def test_cif_seller(self): self.assertEqual(self._make_po('CIF').freight_responsibility, 'seller')
    def test_cpt_seller(self): self.assertEqual(self._make_po('CPT').freight_responsibility, 'seller')
    def test_cip_seller(self): self.assertEqual(self._make_po('CIP').freight_responsibility, 'seller')
    def test_dap_seller(self): self.assertEqual(self._make_po('DAP').freight_responsibility, 'seller')
    def test_dpu_seller(self): self.assertEqual(self._make_po('DPU').freight_responsibility, 'seller')
    def test_ddp_seller(self): self.assertEqual(self._make_po('DDP').freight_responsibility, 'seller')
    def test_no_incoterm_na(self):
        po = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_qty': 1, 'price_unit': 10, 'name': 'x'})],
        })
        self.assertEqual(po.freight_responsibility, 'na')
```

**Step 2: Write `addons/mml_freight/tests/test_package_aggregation.py`**
```python
from odoo.tests.common import TransactionCase

class TestPackageAggregation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Agg Supplier'})
        inc = cls.env['account.incoterms'].search([('code', '=', 'FOB')], limit=1)
        if not inc:
            inc = cls.env['account.incoterms'].create({'name': 'FOB', 'code': 'FOB'})
        cls.po = cls.env['purchase.order'].create({'partner_id': cls.partner.id, 'incoterm_id': inc.id})

    def _tender(self):
        return self.env['freight.tender'].create({
            'purchase_order_id': self.po.id, 'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
        })

    def _pkg(self, t, qty, w, l, wi, h, dg=False):
        return self.env['freight.tender.package'].create({
            'tender_id': t.id, 'quantity': qty, 'weight_kg': w,
            'length_cm': l, 'width_cm': wi, 'height_cm': h, 'is_dangerous': dg,
        })

    def test_weight_sum(self):
        t = self._tender()
        self._pkg(t, 1, 20.0, 40, 30, 25)
        self._pkg(t, 2, 10.0, 30, 20, 20)
        self.assertAlmostEqual(t.total_weight_kg, 30.0)

    def test_volume_per_line(self):
        t = self._tender()
        pkg = self._pkg(t, 2, 5.0, 100, 50, 50)
        self.assertAlmostEqual(pkg.volume_m3, 0.5, places=4)

    def test_chargeable_uses_volumetric(self):
        t = self._tender()
        self._pkg(t, 1, 10.0, 100, 100, 100)
        self.assertAlmostEqual(t.chargeable_weight_kg, 333.0, places=1)

    def test_dg_flag_propagates(self):
        t = self._tender()
        self._pkg(t, 1, 2.0, 10, 10, 10, dg=True)
        self.assertTrue(t.contains_dg)
```

**Step 3: Write `addons/mml_freight/tests/test_carrier_eligibility.py`**
```python
from odoo.tests.common import TransactionCase

class TestCarrierEligibility(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.nz = cls.env['res.country'].search([('code', '=', 'NZ')], limit=1)
        cls.au = cls.env['res.country'].search([('code', '=', 'AU')], limit=1)
        cls.prod = cls.env['product.product'].search([], limit=1)
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Elig Test', 'product_id': cls.prod.id, 'delivery_type': 'fixed',
            'auto_tender': True, 'transport_modes': 'road', 'max_weight_kg': 500.0,
            'supports_dg': False,
            'origin_country_ids': [(6, 0, [cls.au.id])],
            'dest_country_ids': [(6, 0, [cls.nz.id])],
        })

    def test_all_match(self): self.assertTrue(self.carrier.is_eligible(self.au, self.nz, 100, False, 'road'))
    def test_dg_excluded(self): self.assertFalse(self.carrier.is_eligible(self.au, self.nz, 100, True, 'road'))
    def test_overweight(self): self.assertFalse(self.carrier.is_eligible(self.au, self.nz, 600, False, 'road'))
    def test_wrong_origin(self):
        cn = self.env['res.country'].search([('code', '=', 'CN')], limit=1)
        self.assertFalse(self.carrier.is_eligible(cn, self.nz, 100, False, 'road'))
    def test_wrong_mode(self): self.assertFalse(self.carrier.is_eligible(self.au, self.nz, 100, False, 'air'))
    def test_any_mode_carrier(self):
        c = self.env['delivery.carrier'].create({'name': 'Any', 'product_id': self.prod.id, 'delivery_type': 'fixed', 'transport_modes': 'any'})
        self.assertTrue(c.is_eligible(self.au, self.nz, 100, False, 'air'))
```

**Step 4: Write `addons/mml_freight/tests/test_quote_ranking.py`**
```python
from odoo.tests.common import TransactionCase

class TestQuoteRanking(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        p = cls.env['res.partner'].create({'name': 'Rank S'})
        po = cls.env['purchase.order'].create({'partner_id': p.id})
        nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or cls.env.company.currency_id
        cls.tender = cls.env['freight.tender'].create({'purchase_order_id': po.id, 'company_id': cls.env.company.id, 'currency_id': nzd.id})
        prod = cls.env['product.product'].search([], limit=1)
        c1 = cls.env['delivery.carrier'].create({'name': 'C1', 'product_id': prod.id, 'delivery_type': 'fixed'})
        c2 = cls.env['delivery.carrier'].create({'name': 'C2', 'product_id': prod.id, 'delivery_type': 'fixed'})
        cls.q_cheap = cls.env['freight.tender.quote'].create({'tender_id': cls.tender.id, 'carrier_id': c1.id, 'state': 'received', 'currency_id': nzd.id, 'base_rate': 1000.0, 'estimated_transit_days': 7})
        cls.q_fast = cls.env['freight.tender.quote'].create({'tender_id': cls.tender.id, 'carrier_id': c2.id, 'state': 'received', 'currency_id': nzd.id, 'base_rate': 2000.0, 'estimated_transit_days': 3})

    def test_cheapest_flag(self): self.assertTrue(self.q_cheap.is_cheapest); self.assertFalse(self.q_fast.is_cheapest)
    def test_fastest_flag(self): self.assertTrue(self.q_fast.is_fastest); self.assertFalse(self.q_cheap.is_fastest)
    def test_rank_by_cost(self): self.assertEqual(self.q_cheap.rank_by_cost, 1); self.assertEqual(self.q_fast.rank_by_cost, 2)
    def test_cost_vs_cheapest(self): self.assertAlmostEqual(self.q_fast.cost_vs_cheapest_pct, 100.0, places=1)
    def test_total_rate_sum(self):
        nzd = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or self.env.company.currency_id
        prod = self.env['product.product'].search([], limit=1)
        c = self.env['delivery.carrier'].create({'name': 'CR', 'product_id': prod.id, 'delivery_type': 'fixed'})
        q = self.env['freight.tender.quote'].create({'tender_id': self.tender.id, 'carrier_id': c.id, 'state': 'received', 'currency_id': nzd.id, 'base_rate': 500.0, 'fuel_surcharge': 50.0, 'origin_charges': 100.0, 'destination_charges': 75.0, 'customs_charges': 25.0, 'other_surcharges': 10.0})
        self.assertAlmostEqual(q.total_rate, 760.0)
```

**Step 5: Write `addons/mml_freight/tests/test_auto_select.py`**
```python
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError

class TestAutoSelect(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        p = cls.env['res.partner'].create({'name': 'AS Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': p.id})
        nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or cls.env.company.currency_id
        cls.tender = cls.env['freight.tender'].create({'purchase_order_id': po.id, 'company_id': cls.env.company.id, 'currency_id': nzd.id, 'state': 'quoted'})
        prod = cls.env['product.product'].search([], limit=1)
        c1 = cls.env['delivery.carrier'].create({'name': 'Slow', 'product_id': prod.id, 'delivery_type': 'fixed', 'reliability_score': 80.0})
        c2 = cls.env['delivery.carrier'].create({'name': 'Fast', 'product_id': prod.id, 'delivery_type': 'fixed', 'reliability_score': 90.0})
        cls.q_cheap = cls.env['freight.tender.quote'].create({'tender_id': cls.tender.id, 'carrier_id': c1.id, 'state': 'received', 'currency_id': nzd.id, 'base_rate': 1000.0, 'estimated_transit_days': 14})
        cls.q_fast = cls.env['freight.tender.quote'].create({'tender_id': cls.tender.id, 'carrier_id': c2.id, 'state': 'received', 'currency_id': nzd.id, 'base_rate': 3000.0, 'estimated_transit_days': 3})

    def test_cheapest(self):
        self.tender.selection_mode = 'cheapest'; self.tender.action_auto_select()
        self.assertEqual(self.tender.selected_quote_id, self.q_cheap); self.assertEqual(self.tender.state, 'selected')

    def test_fastest(self):
        self.tender.write({'state': 'quoted', 'selected_quote_id': False, 'selection_mode': 'fastest'})
        self.tender.action_auto_select()
        self.assertEqual(self.tender.selected_quote_id, self.q_fast)

    def test_manual_raises(self):
        self.tender.write({'state': 'quoted', 'selected_quote_id': False, 'selection_mode': 'manual'})
        with self.assertRaises(UserError): self.tender.action_auto_select()

    def test_reason_set(self):
        self.tender.write({'state': 'quoted', 'selected_quote_id': False, 'selection_mode': 'cheapest'})
        self.tender.action_auto_select()
        self.assertIn('cheapest', self.tender.selection_reason.lower())
```

**Step 6: Write `addons/mml_freight/tests/test_tender_lifecycle.py`**
```python
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError

class TestTenderLifecycle(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        p = cls.env['res.partner'].create({'name': 'LC Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': p.id})
        nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or cls.env.company.currency_id
        cls.tender = cls.env['freight.tender'].create({'purchase_order_id': po.id, 'company_id': cls.env.company.id, 'currency_id': nzd.id})

    def test_initial_state(self): self.assertEqual(self.tender.state, 'draft')
    def test_sequence_assigned(self): self.assertTrue(self.tender.name.startswith('FT/'))
    def test_cancel(self): self.tender.action_cancel(); self.assertEqual(self.tender.state, 'cancelled')
    def test_book_without_quote_raises(self):
        with self.assertRaises(UserError): self.tender.action_book()
    def test_auto_select_no_quotes_raises(self):
        t2 = self.env['freight.tender'].create({'purchase_order_id': self.tender.purchase_order_id.id, 'company_id': self.env.company.id, 'currency_id': self.env.company.currency_id.id, 'state': 'quoted', 'selection_mode': 'cheapest'})
        with self.assertRaises(UserError): t2.action_auto_select()
```

**Step 7: Write `addons/mml_freight/tests/test_3pl_handoff.py`**
```python
from odoo.tests.common import TransactionCase

class Test3plHandoff(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': '3PL Supplier'})
        cls.po = cls.env['purchase.order'].create({'partner_id': cls.partner.id})
        cls.carrier = cls.env['delivery.carrier'].create({'name': '3PL C', 'product_id': cls.env['product.product'].search([], limit=1).id, 'delivery_type': 'fixed'})

    def test_no_error_without_connector(self):
        b = self.env['freight.booking'].create({'carrier_id': self.carrier.id, 'purchase_order_id': self.po.id, 'currency_id': self.env.company.currency_id.id})
        b.action_confirm()
        self.assertEqual(b.state, 'confirmed')

    def test_3pl_message_created_when_connector_present(self):
        if '3pl.message' not in self.env:
            self.skipTest('stock_3pl_core not installed')
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        connector = self.env['3pl.connector'].search([('warehouse_id', '=', warehouse.id), ('active', '=', True)], limit=1)
        if not connector:
            self.skipTest('No active 3pl.connector')
        picking_type = self.env['stock.picking.type'].search([('warehouse_id', '=', warehouse.id)], limit=1)
        self.po.write({'picking_type_id': picking_type.id})
        b = self.env['freight.booking'].create({'carrier_id': self.carrier.id, 'purchase_order_id': self.po.id, 'currency_id': self.env.company.currency_id.id})
        b.action_confirm()
        self.assertTrue(b.tpl_message_id)
        self.assertEqual(b.tpl_message_id.document_type, 'inward_order')
        self.assertEqual(b.tpl_message_id.ref_id, self.po.id)
```

**Step 8: Write `addons/mml_freight/tests/test_po_form_fields.py`**
```python
from odoo.tests.common import TransactionCase

class TestPoFormFields(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'PO Fields S'})

    def _inc(self, code):
        i = self.env['account.incoterms'].search([('code', '=', code)], limit=1)
        if not i:
            i = self.env['account.incoterms'].create({'name': code, 'code': code})
        return i

    def test_responsibility_recomputes(self):
        po = self.env['purchase.order'].create({'partner_id': self.partner.id, 'incoterm_id': self._inc('FOB').id})
        self.assertEqual(po.freight_responsibility, 'buyer')
        po.incoterm_id = self._inc('DDP')
        self.assertEqual(po.freight_responsibility, 'seller')

    def test_cargo_date_writable(self):
        po = self.env['purchase.order'].create({'partner_id': self.partner.id})
        po.cargo_ready_date = '2026-04-01'
        self.assertEqual(str(po.cargo_ready_date), '2026-04-01')

    def test_tender_count_zero(self):
        po = self.env['purchase.order'].create({'partner_id': self.partner.id})
        self.assertEqual(po.tender_count, 0)

    def test_tender_count_increments(self):
        po = self.env['purchase.order'].create({'partner_id': self.partner.id, 'incoterm_id': self._inc('FOB').id})
        self.env['freight.tender'].create({'purchase_order_id': po.id, 'company_id': self.env.company.id, 'currency_id': self.env.company.currency_id.id})
        self.assertEqual(po.tender_count, 1)

    def test_action_creates_tender(self):
        po = self.env['purchase.order'].create({'partner_id': self.partner.id, 'incoterm_id': self._inc('EXW').id})
        po.action_request_freight_tender()
        self.assertTrue(po.freight_tender_id)
        self.assertEqual(po.freight_tender_id.purchase_order_id, po)
```

**Step 9: Syntax-check**
```bash
for f in addons/mml_freight/tests/test_*.py; do python -m py_compile "$f" && echo "OK: $f"; done
```
Expected: `OK: <each file>`

**Step 10: Commit**
```bash
git add addons/mml_freight/tests/
git commit -m "test: add all mml_freight tests"
```

---

## Task 14: mml_freight_dsv — full module

**Files:** All files under `addons/mml_freight_dsv/`

**Step 1: Create directories**
```bash
mkdir -p addons/mml_freight_dsv/{models,adapters,controllers,views,security,tests}
```

**Step 2: Write `addons/mml_freight_dsv/__manifest__.py`**
```python
{
    'name': 'MML Freight — DSV Adapter',
    'version': '19.0.1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'DSV Generic and XPress carrier adapters for MML freight orchestration',
    'author': 'MML',
    'license': 'OPL-1',
    'depends': ['mml_freight'],
    'data': [
        'security/ir.model.access.csv',
        'views/freight_carrier_dsv_views.xml',
    ],
    'installable': True,
    'auto_install': False,
}
```

**Step 3: Write `addons/mml_freight_dsv/__init__.py`**
```python
from . import models
from . import adapters
from . import controllers
```

**Step 4: Write `addons/mml_freight_dsv/models/__init__.py`**
```python
from . import freight_carrier_dsv
```

**Step 5: Write `addons/mml_freight_dsv/adapters/__init__.py`**
```python
from .dsv_mock_adapter import DsvMockAdapter
from .dsv_generic_adapter import DsvGenericAdapter
from .dsv_xpress_adapter import DsvXpressAdapter
```

**Step 6: Write `addons/mml_freight_dsv/controllers/__init__.py`**
```python
from . import dsv_webhook
```

**Step 7: Write `addons/mml_freight_dsv/tests/__init__.py`**
```python
from . import test_dsv_auth
from . import test_dsv_mock_adapter
from . import test_cron_jobs
```

**Step 8: Write `addons/mml_freight_dsv/models/freight_carrier_dsv.py`**
```python
from odoo import models, fields

class FreightCarrierDsv(models.Model):
    _inherit = 'delivery.carrier'

    x_dsv_product_name = fields.Selection([('road','Road'),('air','Air'),('sea','Sea'),('rail','Rail')], string='DSV Product')
    x_dsv_subscription_key = fields.Char('DSV Subscription Key', groups='stock.group_stock_manager', password=True)
    x_dsv_client_id = fields.Char('OAuth Client ID', groups='stock.group_stock_manager')
    x_dsv_client_secret = fields.Char('OAuth Client Secret', groups='stock.group_stock_manager', password=True)
    x_dsv_mdm = fields.Char('DSV MDM Account')
    x_dsv_environment = fields.Selection([('demo','Demo (Mock)'),('production','Production')], default='demo')
    x_dsv_service_auth = fields.Char('XPress DSV-Service-Auth', groups='stock.group_stock_manager', password=True)
    x_dsv_pat = fields.Char('XPress PAT', groups='stock.group_stock_manager', password=True)
    x_dsv_access_token = fields.Char('DSV Access Token (cached)', groups='stock.group_stock_manager', copy=False)
    x_dsv_token_expiry = fields.Datetime('DSV Token Expiry', copy=False)

    def cron_refresh_dsv_tokens(self):
        """Cron: proactively refresh DSV OAuth tokens expiring within 10 minutes."""
        from datetime import timedelta
        import logging
        soon = fields.Datetime.now() + timedelta(minutes=10)
        carriers = self.search([
            ('x_dsv_environment', '=', 'production'),
            ('x_dsv_client_id', '!=', False),
            ('x_dsv_token_expiry', '<', soon),
        ])
        from odoo.addons.mml_freight_dsv.adapters.dsv_auth import refresh_token
        for carrier in carriers:
            try:
                refresh_token(carrier)
            except Exception as e:
                logging.getLogger(__name__).error('DSV token refresh failed for %s: %s', carrier.name, e)
```

**Step 9: Write `addons/mml_freight_dsv/adapters/dsv_auth.py`**
```python
import requests
import logging
from datetime import timedelta
from odoo import fields

_logger = logging.getLogger(__name__)
DSV_OAUTH_URL = 'https://api.dsv.com/oauth2/token'
REFRESH_WINDOW_SECONDS = 120


class DsvAuthError(Exception):
    pass


def get_token(carrier):
    """Return valid DSV access token. Demo mode returns DEMO_TOKEN without HTTP."""
    if carrier.x_dsv_environment == 'demo':
        return 'DEMO_TOKEN'
    now = fields.Datetime.now()
    if (carrier.x_dsv_access_token and carrier.x_dsv_token_expiry
            and carrier.x_dsv_token_expiry > now + timedelta(seconds=REFRESH_WINDOW_SECONDS)):
        return carrier.x_dsv_access_token
    return refresh_token(carrier)


def refresh_token(carrier):
    """POST to DSV OAuth and store token + expiry on carrier record."""
    if not carrier.x_dsv_client_id or not carrier.x_dsv_client_secret:
        raise DsvAuthError(f'DSV carrier "{carrier.name}" missing OAuth credentials.')
    try:
        resp = requests.post(DSV_OAUTH_URL, data={
            'grant_type': 'client_credentials',
            'client_id': carrier.x_dsv_client_id,
            'client_secret': carrier.x_dsv_client_secret,
            'scope': 'freight',
        }, timeout=10)
    except requests.RequestException as e:
        raise DsvAuthError(f'DSV OAuth request failed: {e}') from e
    if resp.status_code in (401, 403):
        raise DsvAuthError(f'DSV OAuth rejected credentials (HTTP {resp.status_code}).')
    if not resp.ok:
        raise DsvAuthError(f'DSV OAuth HTTP {resp.status_code}.')
    data = resp.json()
    token = data.get('access_token')
    if not token:
        raise DsvAuthError('DSV OAuth response missing access_token.')
    expiry = fields.Datetime.now() + timedelta(seconds=data.get('expires_in', 3600))
    carrier.sudo().write({'x_dsv_access_token': token, 'x_dsv_token_expiry': expiry})
    _logger.info('DSV token refreshed for %s', carrier.name)
    return token
```

**Step 10: Write `addons/mml_freight_dsv/adapters/dsv_mock_adapter.py`**
```python
import itertools
import datetime
from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase
from odoo.addons.mml_freight.models.freight_adapter_registry import register_adapter

_counter = itertools.count(1)


@register_adapter('dsv_generic')
@register_adapter('dsv_xpress')
class DsvMockAdapter(FreightAdapterBase):
    """Active when x_dsv_environment == 'demo'. No HTTP calls."""

    def _demo(self):
        return getattr(self.carrier, 'x_dsv_environment', 'demo') == 'demo'

    def request_quote(self, tender):
        if not self._demo():
            raise NotImplementedError('Set x_dsv_environment=demo for mock quotes.')
        return [
            {'service_name': 'DSV Road Standard', 'transport_mode': 'road', 'base_rate': 1800.00,
             'fuel_surcharge': 0, 'origin_charges': 0, 'destination_charges': 0,
             'customs_charges': 0, 'other_surcharges': 0, 'total_rate': 1800.00,
             'currency': 'NZD', 'transit_days': 5, 'carrier_quote_ref': 'MOCK-ROAD-001',
             'rate_valid_until': None, 'estimated_pickup_date': None, 'estimated_delivery_date': None},
            {'service_name': 'DSV Air Express', 'transport_mode': 'air', 'base_rate': 6200.00,
             'fuel_surcharge': 0, 'origin_charges': 0, 'destination_charges': 0,
             'customs_charges': 0, 'other_surcharges': 0, 'total_rate': 6200.00,
             'currency': 'NZD', 'transit_days': 2, 'carrier_quote_ref': 'MOCK-AIR-001',
             'rate_valid_until': None, 'estimated_pickup_date': None, 'estimated_delivery_date': None},
        ]

    def create_booking(self, tender, selected_quote):
        if not self._demo():
            raise NotImplementedError('Set x_dsv_environment=demo for mock booking.')
        return {'carrier_booking_id': f'DSV-MOCK-BK-{next(_counter):04d}', 'carrier_shipment_id': None, 'carrier_tracking_url': None}

    def get_tracking(self, booking):
        if not self._demo():
            raise NotImplementedError('Set x_dsv_environment=demo for mock tracking.')
        now = datetime.datetime.utcnow()
        fmt = lambda d: d.isoformat()
        return [
            {'event_date': fmt(now - datetime.timedelta(days=3)), 'status': 'Picked Up', 'location': 'Shanghai CN', 'description': 'Picked up.', 'raw_payload': '{}'},
            {'event_date': fmt(now - datetime.timedelta(days=2)), 'status': 'In Transit', 'location': 'DSV Hub', 'description': 'In transit to Auckland.', 'raw_payload': '{}'},
            {'event_date': fmt(now - datetime.timedelta(hours=12)), 'status': 'Arrived at Port', 'location': 'Auckland NZ', 'description': 'Arrived at port.', 'raw_payload': '{}'},
        ]
```

**Step 11: Write `addons/mml_freight_dsv/adapters/dsv_generic_adapter.py`**
```python
from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase

class DsvGenericAdapter(FreightAdapterBase):
    """Live DSV Generic adapter scaffold — requires API keys."""
    def request_quote(self, tender): raise NotImplementedError('Use x_dsv_environment=demo.')
    def create_booking(self, tender, quote): raise NotImplementedError
    def get_tracking(self, booking): raise NotImplementedError
```

**Step 12: Write `addons/mml_freight_dsv/adapters/dsv_xpress_adapter.py`**
```python
from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase

class DsvXpressAdapter(FreightAdapterBase):
    """Live DSV XPress adapter scaffold — requires XPress credentials."""
    def request_quote(self, tender): raise NotImplementedError('Use x_dsv_environment=demo.')
    def create_booking(self, tender, quote): raise NotImplementedError
    def get_tracking(self, booking): raise NotImplementedError
```

**Step 13: Write `addons/mml_freight_dsv/controllers/dsv_webhook.py`**
```python
from odoo import http
from odoo.http import request
import json, logging
_logger = logging.getLogger(__name__)

class DsvWebhookController(http.Controller):
    @http.route('/dsv/webhook/<int:carrier_id>', type='json', auth='none', csrf=False, methods=['POST'])
    def dsv_webhook(self, carrier_id, **kwargs):
        carrier = request.env['delivery.carrier'].sudo().browse(carrier_id)
        if not carrier.exists():
            return {'error': 'carrier_not_found'}
        body = request.get_json_data()
        _logger.info('DSV webhook carrier %s: %s', carrier.name, json.dumps(body)[:200])
        event_type = body.get('eventType', '')
        if event_type == 'TRACKING_UPDATE':
            request.env['freight.booking'].sudo()._handle_dsv_tracking_webhook(carrier, body)
        else:
            _logger.warning('DSV unhandled event %s', event_type)
        return {'status': 'ok'}
```

**Step 14: Write `addons/mml_freight_dsv/views/freight_carrier_dsv_views.xml`**
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_delivery_carrier_dsv_type" model="ir.ui.view">
        <field name="name">delivery.carrier.form.dsv.type</field>
        <field name="model">delivery.carrier</field>
        <field name="inherit_id" ref="delivery.view_delivery_carrier_form"/>
        <field name="arch" type="xml">
            <field name="delivery_type" position="attributes">
                <attribute name="selection_add">[('dsv_generic', 'DSV Generic'), ('dsv_xpress', 'DSV XPress')]</attribute>
            </field>
        </field>
    </record>
    <record id="view_freight_carrier_dsv_form" model="ir.ui.view">
        <field name="name">delivery.carrier.form.dsv</field>
        <field name="model">delivery.carrier</field>
        <field name="inherit_id" ref="mml_freight.view_freight_carrier_form"/>
        <field name="arch" type="xml">
            <xpath expr="//group[@name='freight_config']" position="after">
                <group string="DSV Configuration" name="dsv_config" invisible="delivery_type not in ('dsv_generic', 'dsv_xpress')">
                    <field name="x_dsv_environment"/>
                    <field name="x_dsv_mdm"/>
                    <field name="x_dsv_product_name" invisible="delivery_type != 'dsv_generic'"/>
                    <field name="x_dsv_subscription_key" password="True" invisible="delivery_type != 'dsv_generic'"/>
                    <field name="x_dsv_client_id" invisible="delivery_type != 'dsv_generic'"/>
                    <field name="x_dsv_client_secret" password="True" invisible="delivery_type != 'dsv_generic'"/>
                    <field name="x_dsv_service_auth" password="True" invisible="delivery_type != 'dsv_xpress'"/>
                    <field name="x_dsv_pat" password="True" invisible="delivery_type != 'dsv_xpress'"/>
                    <field name="x_dsv_token_expiry" readonly="1"/>
                </group>
            </xpath>
        </field>
    </record>
</odoo>
```

**Step 15: Write `addons/mml_freight_dsv/security/ir.model.access.csv`**
```csv
id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink
```

**Step 16: Write `addons/mml_freight_dsv/tests/test_dsv_auth.py`**
```python
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_auth import get_token, DsvAuthError
from odoo import fields
from datetime import timedelta

class TestDsvAuth(TransactionCase):
    def setUp(self):
        super().setUp()
        self.carrier = self.env['delivery.carrier'].create({
            'name': 'DSV Auth Test', 'product_id': self.env['product.product'].search([], limit=1).id,
            'delivery_type': 'dsv_generic', 'x_dsv_environment': 'demo',
        })

    def test_demo_no_http(self):
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.requests.post') as m:
            token = get_token(self.carrier)
        self.assertEqual(token, 'DEMO_TOKEN')
        m.assert_not_called()

    def test_cached_token_not_expired(self):
        self.carrier.write({'x_dsv_environment': 'production', 'x_dsv_client_id': 'id', 'x_dsv_client_secret': 'sec',
            'x_dsv_access_token': 'CACHED', 'x_dsv_token_expiry': fields.Datetime.now() + timedelta(hours=1)})
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.requests.post') as m:
            token = get_token(self.carrier)
        self.assertEqual(token, 'CACHED')
        m.assert_not_called()

    def test_near_expiry_refreshes(self):
        self.carrier.write({'x_dsv_environment': 'production', 'x_dsv_client_id': 'id', 'x_dsv_client_secret': 'sec',
            'x_dsv_access_token': 'OLD', 'x_dsv_token_expiry': fields.Datetime.now() + timedelta(seconds=60)})
        mock_resp = MagicMock(ok=True, status_code=200)
        mock_resp.json.return_value = {'access_token': 'NEW', 'expires_in': 3600}
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.requests.post', return_value=mock_resp):
            token = get_token(self.carrier)
        self.assertEqual(token, 'NEW')

    def test_401_raises(self):
        self.carrier.write({'x_dsv_environment': 'production', 'x_dsv_client_id': 'bad', 'x_dsv_client_secret': 'bad',
            'x_dsv_access_token': False, 'x_dsv_token_expiry': False})
        mock_resp = MagicMock(ok=False, status_code=401)
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.requests.post', return_value=mock_resp):
            with self.assertRaises(DsvAuthError): get_token(self.carrier)

    def test_missing_creds_raises(self):
        self.carrier.write({'x_dsv_environment': 'production', 'x_dsv_client_id': False, 'x_dsv_client_secret': False})
        with self.assertRaises(DsvAuthError): get_token(self.carrier)
```

**Step 17: Write `addons/mml_freight_dsv/tests/test_dsv_mock_adapter.py`**
```python
from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_mock_adapter import DsvMockAdapter

class TestDsvMockAdapter(TransactionCase):
    def setUp(self):
        super().setUp()
        self.carrier = self.env['delivery.carrier'].create({
            'name': 'Mock Test', 'product_id': self.env['product.product'].search([], limit=1).id,
            'delivery_type': 'dsv_generic', 'x_dsv_environment': 'demo',
        })
        self.adapter = DsvMockAdapter(self.carrier, self.env)

    def _tender(self):
        p = self.env['res.partner'].create({'name': 'Mock S'})
        po = self.env['purchase.order'].create({'partner_id': p.id})
        return self.env['freight.tender'].create({'purchase_order_id': po.id, 'company_id': self.env.company.id, 'currency_id': self.env.company.currency_id.id})

    def test_two_quotes(self): self.assertEqual(len(self.adapter.request_quote(self._tender())), 2)
    def test_road_quote(self):
        q = next(x for x in self.adapter.request_quote(self._tender()) if x['transport_mode'] == 'road')
        self.assertEqual(q['service_name'], 'DSV Road Standard')
        self.assertAlmostEqual(q['total_rate'], 1800.0)
        self.assertEqual(q['transit_days'], 5)
    def test_air_quote(self):
        q = next(x for x in self.adapter.request_quote(self._tender()) if x['transport_mode'] == 'air')
        self.assertAlmostEqual(q['total_rate'], 6200.0)
    def test_mock_booking_ref(self):
        t = self._tender()
        nzd = self.env['res.currency'].search([('name','=','NZD')], limit=1) or self.env.company.currency_id
        q = self.env['freight.tender.quote'].create({'tender_id': t.id, 'carrier_id': self.carrier.id, 'state': 'received', 'currency_id': nzd.id, 'base_rate': 1800.0})
        r = self.adapter.create_booking(t, q)
        self.assertTrue(r['carrier_booking_id'].startswith('DSV-MOCK-BK-'))
    def test_tracking_events(self):
        b = self.env['freight.booking'].create({'carrier_id': self.carrier.id, 'currency_id': self.env.company.currency_id.id})
        events = self.adapter.get_tracking(b)
        self.assertEqual(len(events), 3)
        self.assertIn('Picked Up', [e['status'] for e in events])
    def test_live_raises(self):
        self.carrier.x_dsv_environment = 'production'
        with self.assertRaises(NotImplementedError): self.adapter.request_quote(self._tender())
```

**Step 18: Write `addons/mml_freight_dsv/tests/test_cron_jobs.py`**
```python
from odoo.tests.common import TransactionCase

class TestCronJobs(TransactionCase):
    def test_tracking_cron(self): self.env['freight.booking'].cron_sync_tracking()
    def test_token_cron(self): self.env['delivery.carrier'].cron_refresh_dsv_tokens()
    def test_cron_records_installed(self):
        c1 = self.env.ref('mml_freight.cron_freight_tracking_sync', raise_if_not_found=False)
        c2 = self.env.ref('mml_freight.cron_dsv_token_refresh', raise_if_not_found=False)
        self.assertTrue(c1, 'Tracking cron missing')
        self.assertTrue(c2, 'Token cron missing')
```

**Step 19: Syntax-check**
```bash
for f in addons/mml_freight_dsv/**/*.py; do python -m py_compile "$f" && echo "OK: $f"; done
```

**Step 20: Commit**
```bash
git add addons/mml_freight_dsv/
git commit -m "feat: complete mml_freight_dsv — DSV fields, auth, mock adapter, stubs, webhook, views, tests"
```

---

## Task 15: mml_freight_knplus and mml_freight_demo

**Step 1: Create mml_freight_knplus directories**
```bash
mkdir -p addons/mml_freight_knplus/{models,adapters,views}
```

**Step 2: Write all mml_freight_knplus files**

`addons/mml_freight_knplus/__manifest__.py`:
```python
{
    'name': 'MML Freight — K+N Adapter',
    'version': '19.0.1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Kuehne+Nagel carrier adapter stub',
    'author': 'MML',
    'license': 'OPL-1',
    'depends': ['mml_freight'],
    'data': ['views/freight_carrier_knplus_views.xml'],
    'installable': True,
    'auto_install': False,
}
```

`addons/mml_freight_knplus/__init__.py`:
```python
from . import models
from . import adapters
```

`addons/mml_freight_knplus/models/__init__.py`:
```python
from . import freight_carrier_knplus
```

`addons/mml_freight_knplus/models/freight_carrier_knplus.py`:
```python
from odoo import models, fields
class FreightCarrierKnplus(models.Model):
    _inherit = 'delivery.carrier'
    x_knplus_client_id = fields.Char('K+N Client ID', groups='stock.group_stock_manager')
    x_knplus_environment = fields.Selection([('demo','Demo'),('production','Production')], default='demo')
```

`addons/mml_freight_knplus/adapters/__init__.py`:
```python
from .knplus_adapter import KnplusAdapter
```

`addons/mml_freight_knplus/adapters/knplus_adapter.py`:
```python
from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase
from odoo.addons.mml_freight.models.freight_adapter_registry import register_adapter

@register_adapter('knplus')
class KnplusAdapter(FreightAdapterBase):
    """K+N stub — correct interface, NotImplementedError on all methods."""
    def request_quote(self, tender): raise NotImplementedError('K+N quote not implemented. Disable auto_tender on K+N carriers.')
    def create_booking(self, tender, quote): raise NotImplementedError('K+N booking not implemented.')
    def get_tracking(self, booking): raise NotImplementedError('K+N tracking not implemented.')
```

`addons/mml_freight_knplus/views/freight_carrier_knplus_views.xml`:
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_delivery_carrier_knplus_type" model="ir.ui.view">
        <field name="name">delivery.carrier.form.knplus.type</field>
        <field name="model">delivery.carrier</field>
        <field name="inherit_id" ref="delivery.view_delivery_carrier_form"/>
        <field name="arch" type="xml">
            <field name="delivery_type" position="attributes">
                <attribute name="selection_add">[('knplus', 'K+N (Kuehne+Nagel)')]</attribute>
            </field>
        </field>
    </record>
</odoo>
```

**Step 3: Syntax-check knplus**
```bash
for f in addons/mml_freight_knplus/**/*.py; do python -m py_compile "$f" && echo "OK: $f"; done
```

**Step 4: Commit knplus**
```bash
git add addons/mml_freight_knplus/
git commit -m "feat: scaffold mml_freight_knplus with K+N stub adapter"
```

**Step 5: Create mml_freight_demo directories**
```bash
mkdir -p addons/mml_freight_demo/{data,tests}
```

**Step 6: Write all mml_freight_demo files**

`addons/mml_freight_demo/__manifest__.py`:
```python
{
    'name': 'MML Freight — Demo Data',
    'version': '19.0.1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Demo data for MML Freight Orchestration',
    'author': 'MML',
    'license': 'OPL-1',
    'depends': ['mml_freight_dsv', 'mml_freight_knplus'],
    'data': ['data/demo_carriers.xml', 'data/demo_partners.xml', 'data/demo_products.xml', 'data/demo_purchase_orders.xml'],
    'installable': True,
    'auto_install': False,
}
```

`addons/mml_freight_demo/__init__.py`:
```python
from . import tests
```

`addons/mml_freight_demo/data/demo_carriers.xml`:
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="carrier_dsv_road_nz" model="delivery.carrier">
        <field name="name">DSV Road NZ</field>
        <field name="delivery_type">dsv_generic</field>
        <field name="auto_tender">True</field>
        <field name="transport_modes">road</field>
        <field name="x_dsv_environment">demo</field>
        <field name="x_dsv_product_name">road</field>
        <field name="reliability_score">78.0</field>
        <field name="product_id" ref="delivery.product_product_delivery"/>
    </record>
    <record id="carrier_knplus_sea_lcl" model="delivery.carrier">
        <field name="name">K+N Sea LCL Global</field>
        <field name="delivery_type">knplus</field>
        <field name="auto_tender">False</field>
        <field name="transport_modes">sea_lcl</field>
        <field name="reliability_score">82.0</field>
        <field name="product_id" ref="delivery.product_product_delivery"/>
    </record>
</odoo>
```

`addons/mml_freight_demo/data/demo_partners.xml`:
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="partner_enduro_pet_au" model="res.partner">
        <field name="name">Enduro Pet Pty Ltd</field>
        <field name="street">12 Warehouse Drive</field>
        <field name="city">Melbourne</field>
        <field name="zip">3000</field>
        <field name="country_id" ref="base.au"/>
        <field name="supplier_rank">1</field>
        <field name="company_type">company</field>
    </record>
</odoo>
```

`addons/mml_freight_demo/data/demo_products.xml`:
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="product_dog_food_20kg" model="product.template">
        <field name="name">Dog Food 20kg</field>
        <field name="default_code">EPC-DOG-20</field>
        <field name="type">product</field>
        <field name="weight">20.0</field>
        <field name="x_freight_length">40.0</field>
        <field name="x_freight_width">30.0</field>
        <field name="x_freight_height">25.0</field>
        <field name="x_dangerous_goods">False</field>
    </record>
    <record id="product_cat_food_5kg" model="product.template">
        <field name="name">Cat Food 5kg</field>
        <field name="default_code">EPC-CAT-5</field>
        <field name="type">product</field>
        <field name="weight">5.0</field>
        <field name="x_freight_length">25.0</field>
        <field name="x_freight_width">20.0</field>
        <field name="x_freight_height">15.0</field>
        <field name="x_dangerous_goods">False</field>
    </record>
    <record id="product_bird_seed_10kg" model="product.template">
        <field name="name">Bird Seed 10kg</field>
        <field name="default_code">EPC-BIRD-10</field>
        <field name="type">product</field>
        <field name="weight">10.0</field>
        <field name="x_freight_length">30.0</field>
        <field name="x_freight_width">25.0</field>
        <field name="x_freight_height">20.0</field>
        <field name="x_dangerous_goods">False</field>
    </record>
</odoo>
```

`addons/mml_freight_demo/data/demo_purchase_orders.xml`:
```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo noupdate="1">
    <record id="po_enduro_ready_to_tender" model="purchase.order">
        <field name="name">PO/DEMO/001</field>
        <field name="partner_id" ref="partner_enduro_pet_au"/>
        <field name="incoterm_id" ref="account.incoterm_fob"/>
        <field name="cargo_ready_date" eval="(datetime.date.today() + relativedelta(days=15)).strftime('%Y-%m-%d')"/>
        <field name="required_delivery_date" eval="(datetime.date.today() + relativedelta(days=30)).strftime('%Y-%m-%d')"/>
        <field name="freight_mode_preference">sea</field>
        <field name="order_line" eval="[
            (0,0,{'product_id':ref('product_dog_food_20kg'),'product_qty':100,'price_unit':15.00,'name':'Dog Food 20kg'}),
            (0,0,{'product_id':ref('product_cat_food_5kg'),'product_qty':50,'price_unit':8.00,'name':'Cat Food 5kg'}),
        ]"/>
    </record>
</odoo>
```

`addons/mml_freight_demo/tests/__init__.py`:
```python
from . import test_demo_install
```

`addons/mml_freight_demo/tests/test_demo_install.py`:
```python
from odoo.tests.common import TransactionCase

class TestDemoInstall(TransactionCase):
    def test_dsv_carrier(self):
        c = self.env.ref('mml_freight_demo.carrier_dsv_road_nz', raise_if_not_found=False)
        self.assertIsNotNone(c); self.assertEqual(c.delivery_type, 'dsv_generic'); self.assertTrue(c.auto_tender)

    def test_knplus_carrier(self):
        c = self.env.ref('mml_freight_demo.carrier_knplus_sea_lcl', raise_if_not_found=False)
        self.assertIsNotNone(c); self.assertEqual(c.delivery_type, 'knplus')

    def test_enduro_partner(self):
        p = self.env.ref('mml_freight_demo.partner_enduro_pet_au', raise_if_not_found=False)
        self.assertIsNotNone(p); self.assertGreater(p.supplier_rank, 0)

    def test_products_have_dims(self):
        d = self.env.ref('mml_freight_demo.product_dog_food_20kg', raise_if_not_found=False)
        self.assertIsNotNone(d); self.assertGreater(d.x_freight_length, 0)

    def test_demo_po_buyer_responsibility(self):
        po = self.env.ref('mml_freight_demo.po_enduro_ready_to_tender', raise_if_not_found=False)
        self.assertIsNotNone(po); self.assertEqual(po.freight_responsibility, 'buyer')

    def test_demo_po_cargo_date(self):
        po = self.env.ref('mml_freight_demo.po_enduro_ready_to_tender', raise_if_not_found=False)
        self.assertTrue(po.cargo_ready_date)
```

**Step 7: Syntax-check demo**
```bash
python -m py_compile addons/mml_freight_demo/__manifest__.py
python -m py_compile addons/mml_freight_demo/__init__.py
python -m py_compile addons/mml_freight_demo/tests/test_demo_install.py
```

**Step 8: Commit demo**
```bash
git add addons/mml_freight_demo/
git commit -m "feat: add mml_freight_demo with carriers, supplier, products, demo PO and install tests"
```

---

## Final Verification

```bash
find addons/ -name "*.py" | xargs python -m py_compile && echo "ALL FILES COMPILE OK"
```
Expected: `ALL FILES COMPILE OK`

**Run Odoo tests** (requires Odoo 19 instance with all modules installed):
```bash
odoo-bin --test-enable --stop-after-init -d freight_test \
  -i mml_freight,mml_freight_dsv,mml_freight_knplus,mml_freight_demo \
  --test-tags=mml_freight,mml_freight_dsv,mml_freight_demo
```
Expected: All tests PASS, no errors.

