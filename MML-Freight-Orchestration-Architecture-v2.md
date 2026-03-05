# MML Freight Orchestration Layer — Architecture Specification v2

## Executive Summary

A two-layer abstraction for Odoo 19 that separates **inbound freight forwarding** (supplier → MML warehouse) from **outbound 3PL fulfilment** (MML warehouse → customer). The freight layer is triggered by Purchase Orders — tendering across multiple freight forwarders to get the best rate for inbound shipments, then handing off to Mainfreight for receiving.

```
INBOUND FLOW (this document — Layer 1 priority)
═══════════════════════════════════════════════

Supplier (overseas/domestic)
    │
    ▼
purchase.order confirmed
    │
    ▼
┌────────────────────────────────────────────────────┐
│  LAYER 1: Freight Forwarder Orchestrator           │
│  Module: mml_freight                               │
│                                                    │
│  freight.tender (fan out to carriers)              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│  │   DSV    │  │  Kuehne  │  │ Flexport │  ...    │
│  │ Adapter  │  │ +Nagel   │  │ Adapter  │         │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘         │
│       └──────────────┼──────────────┘              │
│                      ▼                             │
│  freight.tender.quote (compare → select cheapest)  │
│                      │                             │
│                      ▼                             │
│  freight.booking (book → track → docs)             │
└──────────────────────┬─────────────────────────────┘
                       │
                       ▼
┌────────────────────────────────────────────────────┐
│  LAYER 2: 3PL Orchestrator                         │
│  Module: mml_3pl                                   │
│                                                    │
│  tpl.inbound.notice → Mainfreight                  │
│  "Shipment arriving on [date], here's the ASN"     │
│                                                    │
│  Mainfreight receives → stock.picking validated     │
│  → stock lands in Odoo                             │
└────────────────────────────────────────────────────┘
                       │
                       ▼
              stock available in warehouse
                       │
                       ▼

OUTBOUND FLOW (Layer 2 — last mile, separate phase)
═══════════════════════════════════════════════════

sale.order confirmed
    │
    ▼
stock.picking (outgoing) → tpl.dispatch.order → Mainfreight
    │
    ▼
Mainfreight picks/packs/ships → customer
```

---

## The Inbound Freight Problem

MML imports ~400 SKUs across 5 brands from multiple international and domestic suppliers. Each PO represents a freight decision:

| Decision | Variables |
|----------|-----------|
| Which forwarder? | DSV, K+N, Flexport, others |
| Which mode? | Sea (LCL/FCL), Air, Road |
| Which service level? | Standard, Express |
| Who pays? | Incoterms (EXW = MML arranges, CIF = supplier arranges, FOB = MML from port) |
| Where to? | Mainfreight warehouse (Auckland/Christchurch) |

The goal: for every PO where MML controls the freight leg, automatically tender to multiple forwarders and book the cheapest option.

---

## Module Structure

```
addons/
├── mml_freight/                        # Layer 1: Freight Forwarder Orchestrator
│   ├── __manifest__.py
│   ├── models/
│   │   ├── freight_carrier.py              # Carrier registry + config
│   │   ├── freight_tender.py               # Tender request
│   │   ├── freight_tender_package.py       # Package lines
│   │   ├── freight_tender_quote.py         # Quote responses
│   │   ├── freight_booking.py              # Confirmed booking lifecycle
│   │   ├── freight_tracking_event.py       # Normalised tracking events
│   │   ├── freight_document.py             # Document registry
│   │   ├── freight_adapter_registry.py     # Adapter resolution
│   │   ├── purchase_order.py               # PO extensions (inherit)
│   │   └── product_template.py             # Product extensions (inherit)
│   ├── adapters/
│   │   └── base_adapter.py                 # Abstract adapter interface
│   ├── controllers/
│   │   └── webhook.py                      # Inbound webhooks
│   ├── wizards/
│   │   ├── freight_tender_wizard.py        # Create tender from PO
│   │   └── freight_manual_select_wizard.py # Manual quote selection
│   ├── data/
│   │   ├── ir_sequence.xml
│   │   ├── ir_cron.xml
│   │   └── freight_incoterms_rules.xml     # Incoterm → freight responsibility
│   ├── views/
│   │   ├── freight_tender_views.xml
│   │   ├── freight_booking_views.xml
│   │   ├── freight_carrier_views.xml
│   │   ├── purchase_order_views.xml        # Add freight buttons/fields to PO
│   │   └── freight_dashboard.xml           # Rate comparison dashboard
│   └── security/
│       └── ir.model.access.csv
│
├── mml_freight_dsv/                    # DSV Adapter
│   ├── __manifest__.py
│   ├── models/
│   │   └── freight_carrier_dsv.py          # DSV-specific config fields
│   ├── adapters/
│   │   ├── dsv_generic_adapter.py          # Air, Sea, Road EU, Rail
│   │   ├── dsv_xpress_adapter.py           # XPress (courier/express)
│   │   └── dsv_auth.py                     # OAuth token management
│   └── data/
│       └── dsv_package_types.xml
│
├── mml_freight_<future_carrier>/       # K+N, Flexport, etc.
│
├── mml_3pl/                            # Layer 2: 3PL Orchestrator
│   ├── models/
│   │   ├── tpl_provider.py
│   │   ├── tpl_inbound_notice.py           # ASN / inbound receipt notice
│   │   ├── tpl_dispatch_order.py           # Outbound dispatch (Phase 2)
│   │   └── tpl_dispatch_line.py
│   ├── adapters/
│   │   └── base_adapter.py
│   └── views/
│
└── mml_3pl_mainfreight/                # Mainfreight Adapter
    ├── adapters/
    │   └── mainfreight_adapter.py
    └── data/
```

---

## Layer 1: Core Models

### `purchase.order` — Extensions (inherit)

The PO is the entry point. Add freight context directly onto the PO form.

```python
class PurchaseOrderFreight(models.Model):
    _inherit = 'purchase.order'

    # Freight responsibility
    freight_responsibility = fields.Selection([
        ('buyer', 'Buyer Arranges (EXW/FCA/FOB)'),
        ('seller', 'Seller Arranges (CIF/DDP/DAP)'),
        ('na', 'N/A (Domestic/No Freight)'),
    ], string='Freight Responsibility', compute='_compute_freight_responsibility', store=True,
       readonly=False, help='Derived from Incoterm but can be overridden')

    freight_tender_id = fields.Many2one('freight.tender', string='Freight Tender', copy=False)
    freight_booking_id = fields.Many2one('freight.booking', related='freight_tender_id.booking_id',
                                         string='Freight Booking', store=True)
    freight_status = fields.Selection(related='freight_booking_id.state', string='Freight Status')
    freight_cost = fields.Monetary(related='freight_booking_id.booked_rate', string='Freight Cost')
    freight_carrier_name = fields.Char(related='freight_booking_id.carrier_id.name',
                                       string='Freight Carrier')
    freight_tracking_url = fields.Char(related='freight_booking_id.carrier_tracking_url')
    freight_eta = fields.Datetime(related='freight_booking_id.eta', string='Freight ETA')

    # Cargo details (for tender)
    cargo_ready_date = fields.Date('Cargo Ready Date',
        help='Date goods are ready for collection from supplier')
    required_delivery_date = fields.Date('Required at Warehouse',
        help='Date goods must arrive at Mainfreight warehouse')

    # Shipment profile (helps carrier selection)
    freight_mode_preference = fields.Selection([
        ('any', 'Any (Let System Decide)'),
        ('sea', 'Sea Only'),
        ('air', 'Air Only'),
        ('road', 'Road Only'),
    ], default='any')

    tender_count = fields.Integer(compute='_compute_tender_count')

    @api.depends('incoterm_id')
    def _compute_freight_responsibility(self):
        # Incoterms where buyer arranges freight
        buyer_arranges = {'EXW', 'FCA', 'FOB', 'FAS'}
        for po in self:
            if not po.incoterm_id:
                po.freight_responsibility = 'na'
            elif po.incoterm_id.code in buyer_arranges:
                po.freight_responsibility = 'buyer'
            else:
                po.freight_responsibility = 'seller'

    def action_create_freight_tender(self):
        """Create a freight tender from this PO."""
        self.ensure_one()
        if self.freight_responsibility != 'buyer':
            raise UserError('Freight is arranged by the seller for this Incoterm.')
        if self.freight_tender_id:
            raise UserError('A freight tender already exists for this PO.')

        tender = self.env['freight.tender'].create(
            self._prepare_freight_tender_vals()
        )
        self.freight_tender_id = tender
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'freight.tender',
            'res_id': tender.id,
            'view_mode': 'form',
        }

    def _prepare_freight_tender_vals(self):
        """Build tender values from PO data."""
        # Destination = Mainfreight warehouse linked to PO's picking type
        picking_type = self.picking_type_id
        dest_warehouse = picking_type.warehouse_id
        dest_partner = dest_warehouse.partner_id

        return {
            'purchase_order_id': self.id,
            'origin_partner_id': self.partner_id.id,          # Supplier
            'dest_partner_id': dest_partner.id,               # Mainfreight warehouse
            'origin_country_id': self.partner_id.country_id.id,
            'dest_country_id': dest_partner.country_id.id,
            'incoterm_id': self.incoterm_id.id,
            'requested_pickup_date': self.cargo_ready_date,
            'requested_delivery_date': self.required_delivery_date,
            'goods_value': self.amount_untaxed,
            'currency_id': self.currency_id.id,
            'freight_mode_preference': self.freight_mode_preference,
            'package_line_ids': [(0, 0, vals) for vals in self._prepare_package_lines()],
        }

    def _prepare_package_lines(self):
        """Aggregate PO lines into freight package lines."""
        packages = []
        for line in self.order_line.filtered(lambda l: l.product_id.type == 'product'):
            product = line.product_id
            packages.append({
                'product_id': product.id,
                'quantity': int(line.product_qty),
                'weight_kg': (product.weight or 0) * line.product_qty,
                'net_weight_kg': (product.weight or 0) * line.product_qty,
                'length_cm': product.x_freight_length or 0,
                'width_cm': product.x_freight_width or 0,
                'height_cm': product.x_freight_height or 0,
                'description': product.name,
                'hs_code': product.hs_code or '',
                'is_dangerous': product.x_dangerous_goods or False,
            })
        return packages
```

### `freight.tender` — Tender Request (PO-centric)

```python
class FreightTender(models.Model):
    _name = 'freight.tender'
    _description = 'Freight Tender'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'create_date desc'

    name = fields.Char(default=lambda self: self.env['ir.sequence'].next_by_code('freight.tender'))
    state = fields.Selection([
        ('draft', 'Draft'),
        ('requesting', 'Requesting Quotes'),
        ('quoted', 'Quotes Received'),
        ('partial', 'Partial Quotes'),       # Some carriers responded, others timed out
        ('selected', 'Carrier Selected'),
        ('booked', 'Booking Confirmed'),
        ('expired', 'Expired'),
        ('cancelled', 'Cancelled'),
    ], default='draft', tracking=True)

    # Source — PO is primary trigger
    purchase_order_id = fields.Many2one('purchase.order', string='Purchase Order',
                                        required=True, ondelete='restrict')
    company_id = fields.Many2one('res.company',
                                  default=lambda self: self.env.company)

    # Origin (supplier)
    origin_partner_id = fields.Many2one('res.partner', string='Ship From (Supplier)',
                                         required=True)
    origin_country_id = fields.Many2one('res.country', required=True)
    origin_port = fields.Char('Origin Port/Airport',
        help='e.g., Shanghai, Sydney. Used for sea/air routing')

    # Destination (Mainfreight warehouse)
    dest_partner_id = fields.Many2one('res.partner', string='Ship To (Warehouse)',
                                       required=True)
    dest_country_id = fields.Many2one('res.country', required=True)
    dest_port = fields.Char('Destination Port/Airport',
        help='e.g., Auckland, Tauranga')

    # Trade terms
    incoterm_id = fields.Many2one('account.incoterms')

    # Timing
    requested_pickup_date = fields.Date('Cargo Ready Date')
    requested_delivery_date = fields.Date('Required at Warehouse')
    tender_expiry = fields.Datetime('Quotes Valid Until',
        default=lambda self: fields.Datetime.add(fields.Datetime.now(), days=3))

    # Mode preference
    freight_mode_preference = fields.Selection([
        ('any', 'Any'),
        ('sea', 'Sea Only'),
        ('air', 'Air Only'),
        ('road', 'Road Only'),
    ], default='any')

    # Cargo summary
    total_weight_kg = fields.Float(compute='_compute_totals', store=True)
    total_volume_m3 = fields.Float(compute='_compute_totals', store=True)
    total_cbm = fields.Float('Total CBM', compute='_compute_totals', store=True)
    total_packages = fields.Integer(compute='_compute_totals', store=True)
    chargeable_weight_kg = fields.Float(compute='_compute_totals', store=True,
        help='Max of actual weight and volumetric weight')
    goods_value = fields.Monetary()
    currency_id = fields.Many2one('res.currency')
    contains_dg = fields.Boolean(compute='_compute_dg', store=True)
    requires_temp_control = fields.Boolean()

    # Package lines
    package_line_ids = fields.One2many('freight.tender.package', 'tender_id')

    # Quotes
    quote_line_ids = fields.One2many('freight.tender.quote', 'tender_id')
    quote_count = fields.Integer(compute='_compute_quote_count')
    cheapest_quote_id = fields.Many2one('freight.tender.quote',
        compute='_compute_cheapest', store=True)
    cheapest_rate_nzd = fields.Float(compute='_compute_cheapest', store=True)

    # Selection
    selected_quote_id = fields.Many2one('freight.tender.quote', string='Selected Quote',
                                         tracking=True)
    selection_mode = fields.Selection([
        ('cheapest', 'Cheapest Rate'),
        ('fastest', 'Fastest Transit'),
        ('best_value', 'Best Value (cost × reliability)'),
        ('manual', 'Manual Selection'),
    ], default='cheapest')
    selection_reason = fields.Text('Selection Reason',
        help='Auto-populated or manually entered reason for carrier choice')

    # Result
    booking_id = fields.Many2one('freight.booking', string='Booking', copy=False)

    # Cost analysis
    cost_per_kg = fields.Float(compute='_compute_cost_metrics', store=True)
    cost_per_cbm = fields.Float(compute='_compute_cost_metrics', store=True)
    freight_as_pct_of_goods = fields.Float('Freight % of Goods Value',
        compute='_compute_cost_metrics', store=True)

    @api.depends('package_line_ids')
    def _compute_totals(self):
        for tender in self:
            lines = tender.package_line_ids
            tender.total_weight_kg = sum(l.weight_kg for l in lines)
            tender.total_volume_m3 = sum(l.volume_m3 for l in lines)
            tender.total_cbm = tender.total_volume_m3
            tender.total_packages = sum(l.quantity for l in lines)
            # Volumetric weight: CBM × 1000 for sea, CBM × 167 for air
            vol_weight = tender.total_volume_m3 * 1000  # sea default
            tender.chargeable_weight_kg = max(tender.total_weight_kg, vol_weight)

    @api.depends('quote_line_ids.total_rate_nzd', 'quote_line_ids.state')
    def _compute_cheapest(self):
        for tender in self:
            valid = tender.quote_line_ids.filtered(lambda q: q.state == 'received')
            if valid:
                cheapest = min(valid, key=lambda q: q.total_rate_nzd)
                tender.cheapest_quote_id = cheapest
                tender.cheapest_rate_nzd = cheapest.total_rate_nzd
            else:
                tender.cheapest_quote_id = False
                tender.cheapest_rate_nzd = 0

    def action_request_quotes(self):
        """Fan out quote requests to eligible carriers."""
        self.ensure_one()
        self.state = 'requesting'
        eligible = self._get_eligible_carriers()

        if not eligible:
            raise UserError(
                'No eligible carriers found for this shipment. '
                'Check carrier configurations (countries, weight limits, DG capability).'
            )

        for carrier in eligible:
            adapter = self.env['freight.adapter.registry'].get_adapter(carrier)
            try:
                quote_data = adapter.request_quote(self)
                self.env['freight.tender.quote'].create({
                    'tender_id': self.id,
                    'carrier_id': carrier.id,
                    'state': quote_data.pop('state', 'received'),
                    **quote_data,
                })
            except Exception as e:
                self.env['freight.tender.quote'].create({
                    'tender_id': self.id,
                    'carrier_id': carrier.id,
                    'state': 'error',
                    'error_message': str(e),
                })

        received = self.quote_line_ids.filtered(lambda q: q.state == 'received')
        pending = self.quote_line_ids.filtered(lambda q: q.state == 'pending')
        if received and not pending:
            self.state = 'quoted'
        elif received and pending:
            self.state = 'partial'
        # If all errored/pending, stay in 'requesting' — cron will check for async

    def action_auto_select(self):
        """Auto-select best quote based on selection_mode."""
        self.ensure_one()
        valid = self.quote_line_ids.filtered(lambda q: q.state == 'received')
        if not valid:
            raise UserError('No valid quotes available.')

        if self.selection_mode == 'cheapest':
            best = min(valid, key=lambda q: q.total_rate_nzd)
            self.selection_reason = (
                f'Auto-selected cheapest: {best.carrier_id.name} '
                f'@ ${best.total_rate_nzd:.2f} NZD '
                f'({best.estimated_transit_days:.0f} days transit)'
            )
        elif self.selection_mode == 'fastest':
            best = min(valid, key=lambda q: q.estimated_transit_days or 999)
            self.selection_reason = (
                f'Auto-selected fastest: {best.carrier_id.name} '
                f'@ {best.estimated_transit_days:.0f} days '
                f'(${best.total_rate_nzd:.2f} NZD)'
            )
        elif self.selection_mode == 'best_value':
            best = min(valid, key=lambda q: self._value_score(q))
            self.selection_reason = (
                f'Auto-selected best value: {best.carrier_id.name} '
                f'(score: {self._value_score(best):.3f})'
            )
        else:
            return  # manual

        self.selected_quote_id = best
        self.state = 'selected'

    def action_book(self):
        """Confirm booking with selected carrier."""
        self.ensure_one()
        if not self.selected_quote_id:
            raise UserError('No quote selected.')

        adapter = self.env['freight.adapter.registry'].get_adapter(
            self.selected_quote_id.carrier_id
        )
        booking_data = adapter.create_booking(self, self.selected_quote_id)
        booking = self.env['freight.booking'].create({
            'tender_id': self.id,
            'carrier_id': self.selected_quote_id.carrier_id.id,
            'purchase_order_id': self.purchase_order_id.id,
            **booking_data,
        })
        self.booking_id = booking
        self.state = 'booked'

        # Notify 3PL of incoming shipment
        booking._create_inbound_notice()

    def _get_eligible_carriers(self):
        """Filter carriers by trade lane, weight, mode, DG."""
        carriers = self.env['freight.carrier'].search([
            ('auto_tender', '=', True),
            ('active', '=', True),
        ])
        eligible = self.env['freight.carrier']
        for c in carriers:
            # Country eligibility
            if c.origin_countries and self.origin_country_id not in c.origin_countries:
                continue
            if c.destination_countries and self.dest_country_id not in c.destination_countries:
                continue
            # Weight limit
            if c.max_weight_kg and self.total_weight_kg > c.max_weight_kg:
                continue
            # DG capability
            if self.contains_dg and not c.supports_dg:
                continue
            # Mode preference
            if self.freight_mode_preference != 'any':
                if c.transport_modes != self.freight_mode_preference:
                    continue
            eligible |= c
        return eligible

    @staticmethod
    def _value_score(quote):
        """Lower = better. Combines cost, speed, reliability."""
        cost = quote.total_rate_nzd or 999999
        days = quote.estimated_transit_days or 30
        reliability = (quote.carrier_id.reliability_score or 50) / 100
        return cost * (days / 10) * (1 / max(reliability, 0.1))
```

### `freight.tender.quote` — Quote Responses

```python
class FreightTenderQuote(models.Model):
    _name = 'freight.tender.quote'
    _description = 'Freight Quote'
    _order = 'total_rate_nzd asc'

    tender_id = fields.Many2one('freight.tender', ondelete='cascade', required=True)
    carrier_id = fields.Many2one('freight.carrier', required=True)
    state = fields.Selection([
        ('pending', 'Awaiting Response'),
        ('received', 'Quote Received'),
        ('expired', 'Expired'),
        ('error', 'Error'),
        ('declined', 'Carrier Declined'),
    ], default='pending')

    # Rate breakdown
    base_rate = fields.Monetary()
    fuel_surcharge = fields.Monetary()
    origin_charges = fields.Monetary()
    destination_charges = fields.Monetary()
    customs_charges = fields.Monetary()
    other_surcharges = fields.Monetary()
    total_rate = fields.Monetary(compute='_compute_total', store=True)
    total_rate_nzd = fields.Float('Total (NZD)', compute='_compute_nzd', store=True)
    currency_id = fields.Many2one('res.currency')
    rate_valid_until = fields.Datetime()

    # Service details
    service_name = fields.Char()                          # "Sea LCL Standard"
    transport_mode = fields.Selection([
        ('road', 'Road'), ('air', 'Air'), ('sea_lcl', 'Sea LCL'),
        ('sea_fcl', 'Sea FCL'), ('rail', 'Rail'), ('express', 'Express'),
    ])
    estimated_transit_days = fields.Float()
    estimated_pickup_date = fields.Date()
    estimated_delivery_date = fields.Date()
    carrier_quote_ref = fields.Char()

    # Comparison helpers
    is_cheapest = fields.Boolean(compute='_compute_ranking')
    is_fastest = fields.Boolean(compute='_compute_ranking')
    rank_by_cost = fields.Integer(compute='_compute_ranking')
    rank_by_speed = fields.Integer(compute='_compute_ranking')
    cost_vs_cheapest_pct = fields.Float('% vs Cheapest', compute='_compute_ranking')

    # Error
    error_message = fields.Text()
    raw_response = fields.Text()

    @api.depends('base_rate', 'fuel_surcharge', 'origin_charges',
                 'destination_charges', 'customs_charges', 'other_surcharges')
    def _compute_total(self):
        for q in self:
            q.total_rate = (
                (q.base_rate or 0) + (q.fuel_surcharge or 0) +
                (q.origin_charges or 0) + (q.destination_charges or 0) +
                (q.customs_charges or 0) + (q.other_surcharges or 0)
            )

    @api.depends('total_rate', 'currency_id')
    def _compute_nzd(self):
        nzd = self.env.ref('base.NZD')
        for q in self:
            if q.currency_id and q.currency_id != nzd and q.total_rate:
                q.total_rate_nzd = q.currency_id._convert(
                    q.total_rate, nzd, q.tender_id.company_id, fields.Date.today()
                )
            else:
                q.total_rate_nzd = q.total_rate or 0

    def _compute_ranking(self):
        """Rank quotes within their tender for comparison view."""
        for tender in self.mapped('tender_id'):
            valid = tender.quote_line_ids.filtered(lambda q: q.state == 'received')
            if not valid:
                for q in tender.quote_line_ids:
                    q.is_cheapest = q.is_fastest = False
                    q.rank_by_cost = q.rank_by_speed = 0
                    q.cost_vs_cheapest_pct = 0
                continue

            by_cost = sorted(valid, key=lambda q: q.total_rate_nzd)
            by_speed = sorted(valid, key=lambda q: q.estimated_transit_days or 999)
            cheapest_rate = by_cost[0].total_rate_nzd if by_cost else 1

            for i, q in enumerate(by_cost):
                q.rank_by_cost = i + 1
                q.is_cheapest = (i == 0)
                q.cost_vs_cheapest_pct = (
                    ((q.total_rate_nzd - cheapest_rate) / cheapest_rate * 100)
                    if cheapest_rate else 0
                )
            for i, q in enumerate(by_speed):
                q.rank_by_speed = i + 1
                q.is_fastest = (i == 0)
```

### `freight.booking` — Confirmed Booking

```python
class FreightBooking(models.Model):
    _name = 'freight.booking'
    _description = 'Freight Booking'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(default=lambda self: self.env['ir.sequence'].next_by_code('freight.booking'))
    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Booking Confirmed'),
        ('cargo_ready', 'Cargo Ready'),
        ('picked_up', 'Picked Up'),
        ('in_transit', 'In Transit'),
        ('arrived_port', 'Arrived at Destination Port'),
        ('customs', 'In Customs'),
        ('delivered', 'Delivered to Warehouse'),
        ('received', 'Received by 3PL'),
        ('cancelled', 'Cancelled'),
        ('error', 'Error'),
    ], default='draft', tracking=True)

    # Links
    tender_id = fields.Many2one('freight.tender')
    carrier_id = fields.Many2one('freight.carrier', required=True)
    purchase_order_id = fields.Many2one('purchase.order')
    inbound_notice_id = fields.Many2one('tpl.inbound.notice', string='3PL Inbound Notice')

    # Carrier references
    carrier_booking_id = fields.Char('Carrier Booking ID', tracking=True)
    carrier_shipment_id = fields.Char('Carrier Shipment ID', tracking=True)
    carrier_tracking_url = fields.Char('Tracking URL')

    # Financials
    booked_rate = fields.Monetary(tracking=True)
    actual_rate = fields.Monetary(help='Final invoiced amount from carrier')
    currency_id = fields.Many2one('res.currency')
    invoice_id = fields.Many2one('account.move', string='Freight Invoice')

    # Tracking
    tracking_event_ids = fields.One2many('freight.tracking.event', 'booking_id')
    current_status = fields.Char(compute='_compute_current_status', store=True)
    eta = fields.Datetime('ETA at Warehouse', tracking=True)
    actual_pickup_date = fields.Datetime(tracking=True)
    actual_delivery_date = fields.Datetime(tracking=True)

    # Transit metrics
    transit_days_quoted = fields.Float(related='tender_id.selected_quote_id.estimated_transit_days')
    transit_days_actual = fields.Float(compute='_compute_transit_actual', store=True)
    on_time = fields.Boolean(compute='_compute_on_time', store=True)

    # Documents
    document_ids = fields.One2many('freight.document', 'booking_id')
    label_attachment_id = fields.Many2one('ir.attachment', string='Shipping Label')
    pod_attachment_id = fields.Many2one('ir.attachment', string='Proof of Delivery')

    # Transport
    transport_mode = fields.Selection(related='tender_id.selected_quote_id.transport_mode',
                                       store=True)
    vessel_name = fields.Char()
    voyage_number = fields.Char()
    container_number = fields.Char()
    bill_of_lading = fields.Char()
    awb_number = fields.Char('Air Waybill')

    def _create_inbound_notice(self):
        """Notify 3PL that a shipment is inbound."""
        warehouse = self.purchase_order_id.picking_type_id.warehouse_id
        provider = self.env['tpl.provider'].get_provider_for_warehouse(warehouse)

        notice = self.env['tpl.inbound.notice'].create({
            'booking_id': self.id,
            'provider_id': provider.id,
            'purchase_order_id': self.purchase_order_id.id,
            'warehouse_id': warehouse.id,
            'carrier_name': self.carrier_id.name,
            'carrier_booking_ref': self.carrier_booking_id,
            'expected_arrival': self.eta,
            'line_ids': [(0, 0, {
                'product_id': line.product_id.id,
                'quantity': line.product_qty,
                'uom_id': line.product_uom.id,
            }) for line in self.purchase_order_id.order_line
               if line.product_id.type == 'product'],
        })
        self.inbound_notice_id = notice
```

---

## Layer 2: 3PL Models (Inbound Focus)

### `tpl.inbound.notice` — Advance Shipment Notice

This is the key bridge between freight and 3PL. It tells Mainfreight: "expect this shipment, these products, arriving approximately on this date via this carrier."

```python
class TplInboundNotice(models.Model):
    _name = 'tpl.inbound.notice'
    _description = '3PL Inbound Shipment Notice (ASN)'
    _inherit = ['mail.thread']

    name = fields.Char(default=lambda self: self.env['ir.sequence'].next_by_code('tpl.inbound'))
    state = fields.Selection([
        ('draft', 'Draft'),
        ('sent', 'Sent to 3PL'),
        ('acknowledged', 'Acknowledged'),
        ('arrived', 'Arrived at Warehouse'),
        ('receiving', 'Being Received'),
        ('received', 'Fully Received'),
        ('discrepancy', 'Received with Discrepancy'),
        ('cancelled', 'Cancelled'),
    ], default='draft', tracking=True)

    # Links
    booking_id = fields.Many2one('freight.booking')
    purchase_order_id = fields.Many2one('purchase.order')
    provider_id = fields.Many2one('tpl.provider', required=True)
    warehouse_id = fields.Many2one('stock.warehouse')
    picking_id = fields.Many2one('stock.picking', string='Receipt Picking',
        help='Odoo receipt picking created when goods arrive')

    # Carrier info (from freight layer)
    carrier_name = fields.Char()
    carrier_booking_ref = fields.Char()
    expected_arrival = fields.Datetime('Expected Arrival')
    actual_arrival = fields.Datetime()

    # Lines
    line_ids = fields.One2many('tpl.inbound.notice.line', 'notice_id')

    # 3PL reference
    tpl_reference = fields.Char('3PL ASN Reference')

    # Discrepancy tracking
    discrepancy_notes = fields.Text()

    def action_send_to_3pl(self):
        adapter = self.env['tpl.adapter.registry'].get_adapter(self.provider_id)
        result = adapter.send_inbound_notice(self)
        self.tpl_reference = result.get('tpl_reference')
        self.state = 'sent'

    def action_confirm_receipt(self):
        """Called when 3PL confirms full receipt. Validates the Odoo picking."""
        if self.picking_id and self.picking_id.state not in ('done', 'cancel'):
            self.picking_id.button_validate()
        self.state = 'received'
        # Update booking state
        if self.booking_id:
            self.booking_id.state = 'received'
            self.booking_id.actual_delivery_date = fields.Datetime.now()


class TplInboundNoticeLine(models.Model):
    _name = 'tpl.inbound.notice.line'
    _description = '3PL Inbound Notice Line'

    notice_id = fields.Many2one('tpl.inbound.notice', ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True)
    sku = fields.Char(related='product_id.default_code')
    quantity_expected = fields.Float('Expected Qty')
    quantity_received = fields.Float('Received Qty')
    quantity_damaged = fields.Float('Damaged Qty')
    uom_id = fields.Many2one('uom.uom')
    lot_id = fields.Many2one('stock.production.lot')
    discrepancy = fields.Float(compute='_compute_discrepancy', store=True)

    @api.depends('quantity_expected', 'quantity_received')
    def _compute_discrepancy(self):
        for line in self:
            line.discrepancy = (line.quantity_received or 0) - (line.quantity_expected or 0)
```

---

## Inbound Flow — Step by Step

```
1. PURCHASE ORDER CREATED
   ├── Supplier: Enduro (Australia)
   ├── Incoterm: FOB Melbourne
   ├── Products: 500x Dog Food 20kg, 200x Cat Food 5kg
   ├── Cargo Ready: 15 March
   └── Required at Mainfreight AKL: 30 March
                │
                ▼
2. USER CLICKS "Request Freight Tender" ON PO
   └── freight.tender created automatically with:
       ├── Origin: Enduro's address (Melbourne, AU)
       ├── Destination: Mainfreight Auckland warehouse
       ├── Package lines computed from PO lines
       └── Weight/volume/CBM calculated
                │
                ▼
3. TENDER FANS OUT QUOTES
   ├── DSV Generic (Sea LCL) → $2,400 NZD, 12 days
   ├── DSV Generic (Air) → $8,100 NZD, 3 days
   ├── K+N (Sea LCL) → $2,650 NZD, 14 days
   └── Flexport (Sea LCL) → $2,200 NZD, 13 days
                │
                ▼
4. AUTO-SELECT (cheapest mode)
   └── Winner: Flexport Sea LCL @ $2,200 NZD
       Selection reason logged for audit
                │
                ▼
5. BOOKING CONFIRMED
   ├── freight.booking created
   ├── Carrier booking ID: FLX-2026-12345
   ├── Tracking URL generated
   └── PO updated with freight status + cost
                │
                ▼
6. 3PL INBOUND NOTICE CREATED
   └── tpl.inbound.notice sent to Mainfreight:
       "Expect 500x Dog Food + 200x Cat Food
        arriving ~30 March via Flexport,
        booking ref FLX-2026-12345"
                │
                ▼
7. TRACKING EVENTS FLOW IN (via polling or webhook)
   ├── Picked up from supplier (Melbourne)
   ├── Departed port (Melbourne)
   ├── Arrived port (Auckland)
   ├── Cleared customs
   └── Delivered to Mainfreight AKL
                │
                ▼
8. MAINFREIGHT RECEIVES GOODS
   ├── 3PL confirms receipt (with any discrepancies)
   ├── stock.picking (receipt) validated in Odoo
   ├── Stock lands on hand
   └── freight.booking → state: 'received'
                │
                ▼
9. FREIGHT INVOICE RECONCILIATION
   ├── Carrier invoice arrives (via API or manual)
   ├── Matched against booked rate
   ├── account.move (vendor bill) created
   └── Optionally allocated as landed cost
```

---

## PO Form — UI Changes

```xml
<!-- Add to purchase.order form view -->
<group string="Freight" attrs="{'invisible': [('freight_responsibility', '=', 'seller')]}">
    <field name="freight_responsibility"/>
    <field name="cargo_ready_date"/>
    <field name="required_delivery_date"/>
    <field name="freight_mode_preference"/>
    <separator/>
    <field name="freight_carrier_name" readonly="1"/>
    <field name="freight_cost" readonly="1"/>
    <field name="freight_status" readonly="1"
           decoration-success="freight_status == 'delivered'"
           decoration-info="freight_status == 'in_transit'"
           decoration-warning="freight_status == 'customs'"/>
    <field name="freight_eta" readonly="1"/>
    <field name="freight_tracking_url" widget="url" readonly="1"/>
</group>

<!-- Smart button on PO form -->
<button name="action_create_freight_tender" type="object"
        string="Request Freight Tender"
        class="oe_highlight"
        attrs="{'invisible': ['|',
            ('freight_responsibility', '!=', 'buyer'),
            ('freight_tender_id', '!=', False)]}"/>
<button name="action_view_tender" type="object"
        class="oe_stat_button" icon="fa-ship"
        attrs="{'invisible': [('freight_tender_id', '=', False)]}">
    <field name="tender_count" widget="statinfo" string="Tender"/>
</button>
```

---

## Tender Comparison View

```xml
<!-- freight.tender.quote tree view — embedded in tender form -->
<tree decoration-success="is_cheapest" decoration-info="is_fastest"
      decoration-muted="state in ('error','expired','declined')">
    <field name="carrier_id"/>
    <field name="transport_mode"/>
    <field name="service_name"/>
    <field name="total_rate_nzd" string="Rate (NZD)" widget="monetary"/>
    <field name="cost_vs_cheapest_pct" string="vs Cheapest"
           widget="percentage" decoration-danger="cost_vs_cheapest_pct > 20"/>
    <field name="estimated_transit_days" string="Transit (days)"/>
    <field name="estimated_delivery_date"/>
    <field name="rank_by_cost" string="#Cost"/>
    <field name="rank_by_speed" string="#Speed"/>
    <field name="state" widget="badge"
           decoration-success="state == 'received'"
           decoration-danger="state == 'error'"/>
    <button name="action_select_quote" type="object" string="Select"
            class="oe_highlight"
            attrs="{'invisible': [('state', '!=', 'received')]}"/>
</tree>
```

---

## Incoterms → Freight Responsibility Mapping

| Incoterm | Freight Arranged By | MML Tenders? |
|----------|-------------------|--------------|
| EXW | Buyer (MML) | Yes — full origin to warehouse |
| FCA | Buyer (MML) | Yes — from named place |
| FOB | Buyer (MML) | Yes — from port of origin |
| FAS | Buyer (MML) | Yes — from alongside vessel |
| CFR | Seller | No — seller pays freight |
| CIF | Seller | No — seller pays freight + insurance |
| CPT | Seller | No — seller pays carriage |
| CIP | Seller | No — seller pays carriage + insurance |
| DAP | Seller | No — delivered at place |
| DPU | Seller | No — delivered at place unloaded |
| DDP | Seller | No — delivered duty paid |

When incoterm is EXW/FCA/FOB/FAS, the "Request Freight Tender" button appears on the PO. Otherwise it's hidden because the supplier handles freight.

---

## Landed Cost Integration

When the freight invoice arrives and is reconciled:

```python
def _create_landed_cost(self):
    """Create landed cost from confirmed freight invoice."""
    picking = self.purchase_order_id.picking_ids.filtered(
        lambda p: p.state == 'done' and p.picking_type_code == 'incoming'
    )
    if not picking:
        return

    self.env['stock.landed.cost'].create({
        'vendor_bill_id': self.invoice_id.id,
        'picking_ids': [(6, 0, picking.ids)],
        'cost_lines': [(0, 0, {
            'name': f'Freight: {self.carrier_id.name} ({self.carrier_booking_id})',
            'product_id': self.env.ref('mml_freight.product_freight_cost').id,
            'price_unit': self.actual_rate or self.booked_rate,
            'split_method': 'by_weight',  # or 'by_volume', 'by_quantity'
            'account_id': self.env.ref('mml_freight.account_freight_expense').id,
        })],
    })
```

---

## Implementation Priority (Revised)

| Phase | Scope | Effort |
|-------|-------|--------|
| **Phase 1** | `purchase.order` extensions, `freight.tender` + `freight.tender.quote` + `freight.tender.package` models, adapter interface, PO form UI, manual quote entry (no API yet) | 2 weeks |
| **Phase 2** | `freight.booking` + tracking events, DSV adapter (booking + tracking), `tpl.inbound.notice` (manual 3PL notification) | 3 weeks |
| **Phase 3** | DSV quote API integration, auto-tender from PO, selection algorithms, `ir.cron` jobs | 2 weeks |
| **Phase 4** | DSV labels/documents/webhooks, Mainfreight adapter, landed cost integration | 2 weeks |
| **Phase 5** | Second carrier adapter, analytics dashboard, reliability scoring | 2 weeks |

**Phase 1 is critical** — it validates the data model and UI without needing any API integration. The tender/quote models can be built and used to manually enter quotes from emails while the API adapters are being built. This means the workflow is immediately usable.

---

## References

- [DSV API Integration Guide](./DSV-API-Integration-Guide.md)
- [DSV-Odoo Model Integration Map](./DSV-Odoo-Model-Integration-Map.md)
- DSV Postman Collections: `./dsv-postman-collections/`
