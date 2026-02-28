# MML Freight Orchestration — Sprint 1 Design

**Date:** 2026-02-28
**Status:** Approved
**Scope:** Full system build — all modules, complete test coverage, demo data. Harold validates at the end against DSV; no interim touchpoints required.

---

## Objective

Deliver a fully installable Odoo 19 freight orchestration system that:
- Lets MML tender inbound freight across multiple carriers from a Purchase Order
- Scaffolds DSV Generic + XPress adapters with OAuth auth (keys not required to install)
- Provides a mock adapter so the full tender → quote → select → book flow works in demo mode
- Wires confirmed freight bookings into the existing `stock_3pl_core` message queue (Mainfreight inward order)
- Is fully tested — Harold installs, validates the UI, supplies DSV API keys, done

---

## Module Structure

### This repo (`E:\ClaudeCode\projects\fowarder.intergration\addons\`)

```
addons/
├── mml_freight/              Core orchestrator — zero carrier knowledge
├── mml_freight_dsv/          DSV Generic + XPress adapters + auth scaffold
├── mml_freight_knplus/       K+N stub (correct interface, NotImplementedError)
└── mml_freight_demo/         Demo data — installs last, Harold's starting point
```

### Mainfreight repo (same Odoo addons path — already built)

```
E:\ClaudeCode\projects\mainfreight.3pl.intergration\addons\
├── stock_3pl_core/           3PL message queue + transport base
└── stock_3pl_mainfreight/    Mainfreight SFTP/REST adapter
```

### Dependency graph

```
mml_freight_demo
  └── mml_freight_dsv
  └── mml_freight_knplus
        └── mml_freight
              └── stock_3pl_core  (purchase, stock, account, delivery, mail)
```

`mml_freight_dsv` and `mml_freight_knplus` both depend only on `mml_freight`.
`mml_freight_demo` depends on both adapters (ensures all carriers registered before demo data loads).

---

## Odoo Module Dependencies (from field docs)

| `mml_freight` depends | Source |
|---|---|
| `purchase` | `purchase.order` — primary trigger |
| `stock` | `stock.picking`, `stock.location`, `stock.warehouse` |
| `account` | `account.incoterms`, `account.move` |
| `delivery` | `delivery.carrier` — base for `freight.carrier` |
| `stock_3pl_core` | `3pl.message` queue for inward order handoff |
| `mail` | Chatter on tender + booking |

---

## Data Models

### `mml_freight`

#### `freight.carrier` — extends `delivery.carrier`
| Field | Type | Notes |
|---|---|---|
| `delivery_type` | Selection | Add `dsv_generic`, `dsv_xpress`, `knplus` |
| `auto_tender` | Boolean | Include in automatic tender fan-out |
| `origin_country_ids` | Many2many → `res.country` | Eligible origin countries (empty = all) |
| `dest_country_ids` | Many2many → `res.country` | Eligible destination countries |
| `max_weight_kg` | Float | 0 = no limit |
| `supports_dg` | Boolean | Dangerous goods capable |
| `transport_modes` | Selection | road / air / sea_lcl / sea_fcl / rail / express / any |
| `reliability_score` | Float | 0–100, used in best_value scoring |

#### `freight.tender`
| Field | Type | Notes |
|---|---|---|
| `name` | Char | Sequence: FT/YYYY/NNNNN |
| `state` | Selection | draft → requesting → quoted/partial → selected → booked → expired/cancelled |
| `purchase_order_id` | Many2one → `purchase.order` | Required, ondelete=restrict |
| `company_id` | Many2one → `res.company` | |
| `origin_partner_id` | Many2one → `res.partner` | Supplier (ship from) |
| `origin_country_id` | Many2one → `res.country` | |
| `origin_port` | Char | For sea/air routing |
| `dest_partner_id` | Many2one → `res.partner` | Mainfreight warehouse |
| `dest_country_id` | Many2one → `res.country` | |
| `dest_port` | Char | |
| `incoterm_id` | Many2one → `account.incoterms` | Copied from PO |
| `requested_pickup_date` | Date | Cargo ready date |
| `requested_delivery_date` | Date | Required at warehouse |
| `tender_expiry` | Datetime | Default: now + 3 days |
| `freight_mode_preference` | Selection | any/sea/air/road |
| `total_weight_kg` | Float | Computed from package lines |
| `total_volume_m3` | Float | Computed |
| `total_cbm` | Float | Computed |
| `total_packages` | Integer | Computed |
| `chargeable_weight_kg` | Float | max(actual, volumetric) |
| `goods_value` | Monetary | From PO amount_untaxed |
| `currency_id` | Many2one → `res.currency` | |
| `contains_dg` | Boolean | Computed from package lines |
| `package_line_ids` | One2many → `freight.tender.package` | |
| `quote_line_ids` | One2many → `freight.tender.quote` | |
| `cheapest_quote_id` | Many2one → `freight.tender.quote` | Computed |
| `selected_quote_id` | Many2one → `freight.tender.quote` | User selects |
| `selection_mode` | Selection | cheapest/fastest/best_value/manual |
| `selection_reason` | Text | Auto-populated or manual |
| `booking_id` | Many2one → `freight.booking` | |

#### `freight.tender.package`
| Field | Type | Notes |
|---|---|---|
| `tender_id` | Many2one → `freight.tender` | ondelete=cascade |
| `product_id` | Many2one → `product.product` | |
| `description` | Char | From product.name |
| `quantity` | Integer | |
| `weight_kg` | Float | unit weight × qty |
| `net_weight_kg` | Float | |
| `length_cm` | Float | From `x_freight_length` |
| `width_cm` | Float | |
| `height_cm` | Float | |
| `volume_m3` | Float | Computed: l×w×h / 1,000,000 |
| `hs_code` | Char | From `product.hs_code` |
| `is_dangerous` | Boolean | From `x_dangerous_goods` |

#### `freight.tender.quote`
| Field | Type | Notes |
|---|---|---|
| `tender_id` | Many2one → `freight.tender` | ondelete=cascade |
| `carrier_id` | Many2one → `freight.carrier` | |
| `state` | Selection | pending/received/expired/error/declined |
| `base_rate` | Monetary | |
| `fuel_surcharge` | Monetary | |
| `origin_charges` | Monetary | |
| `destination_charges` | Monetary | |
| `customs_charges` | Monetary | |
| `other_surcharges` | Monetary | |
| `total_rate` | Monetary | Computed sum |
| `total_rate_nzd` | Float | Converted to NZD for ranking |
| `currency_id` | Many2one → `res.currency` | |
| `rate_valid_until` | Datetime | |
| `service_name` | Char | e.g. "Sea LCL Standard" |
| `transport_mode` | Selection | road/air/sea_lcl/sea_fcl/rail/express |
| `estimated_transit_days` | Float | |
| `estimated_pickup_date` | Date | |
| `estimated_delivery_date` | Date | |
| `carrier_quote_ref` | Char | Carrier's reference |
| `is_cheapest` | Boolean | Computed |
| `is_fastest` | Boolean | Computed |
| `rank_by_cost` | Integer | Computed |
| `rank_by_speed` | Integer | Computed |
| `cost_vs_cheapest_pct` | Float | Computed |
| `error_message` | Text | |
| `raw_response` | Text | |

#### `freight.booking`
| Field | Type | Notes |
|---|---|---|
| `name` | Char | Sequence: FB/YYYY/NNNNN |
| `state` | Selection | draft → confirmed → cargo_ready → picked_up → in_transit → arrived_port → customs → delivered → received/cancelled/error |
| `tender_id` | Many2one → `freight.tender` | |
| `carrier_id` | Many2one → `freight.carrier` | Required |
| `purchase_order_id` | Many2one → `purchase.order` | |
| `tpl_message_id` | Many2one → `3pl.message` | Set when inward order queued |
| `carrier_booking_id` | Char | Carrier's booking reference |
| `carrier_shipment_id` | Char | Carrier's shipment ID |
| `carrier_tracking_url` | Char | |
| `booked_rate` | Monetary | |
| `actual_rate` | Monetary | Final invoiced |
| `currency_id` | Many2one → `res.currency` | |
| `invoice_id` | Many2one → `account.move` | Freight vendor bill |
| `tracking_event_ids` | One2many → `freight.tracking.event` | |
| `current_status` | Char | Computed from latest event |
| `eta` | Datetime | |
| `actual_pickup_date` | Datetime | |
| `actual_delivery_date` | Datetime | |
| `transport_mode` | Selection | Related from quote |
| `vessel_name` | Char | |
| `voyage_number` | Char | |
| `container_number` | Char | |
| `bill_of_lading` | Char | |
| `awb_number` | Char | Air waybill |
| `document_ids` | One2many → `freight.document` | |
| `label_attachment_id` | Many2one → `ir.attachment` | |
| `pod_attachment_id` | Many2one → `ir.attachment` | |

#### `freight.tracking.event`
| Field | Type | Notes |
|---|---|---|
| `booking_id` | Many2one → `freight.booking` | ondelete=cascade |
| `event_date` | Datetime | |
| `status` | Char | Normalised status string |
| `location` | Char | |
| `description` | Char | |
| `raw_payload` | Text | Original carrier response |

#### `freight.document`
| Field | Type | Notes |
|---|---|---|
| `booking_id` | Many2one → `freight.booking` | ondelete=cascade |
| `doc_type` | Selection | label/pod/invoice/customs/other |
| `attachment_id` | Many2one → `ir.attachment` | |
| `carrier_doc_ref` | Char | |

### `purchase.order` inherit (in `mml_freight`)
| Field | Type | Notes |
|---|---|---|
| `freight_responsibility` | Selection | buyer/seller/na — computed from `incoterm_id`, store=True, readonly=False |
| `freight_tender_id` | Many2one → `freight.tender` | |
| `freight_booking_id` | Many2one → `freight.booking` | Related via tender |
| `freight_status` | Selection | Related from booking.state |
| `freight_cost` | Monetary | Related from booking.booked_rate |
| `freight_carrier_name` | Char | Related from booking.carrier_id.name |
| `freight_tracking_url` | Char | Related from booking |
| `freight_eta` | Datetime | Related from booking |
| `cargo_ready_date` | Date | User sets this |
| `required_delivery_date` | Date | User sets this |
| `freight_mode_preference` | Selection | any/sea/air/road |
| `tender_count` | Integer | Computed smart button |

### `product.template` inherit (in `mml_freight`)
| Field | Type | Notes |
|---|---|---|
| `x_freight_length` | Float | cm — matches integration map `x_length` |
| `x_freight_width` | Float | cm |
| `x_freight_height` | Float | cm |
| `x_dangerous_goods` | Boolean | DG flag |

---

## DSV Adapter Design (`mml_freight_dsv`)

### New fields on `delivery.carrier` (via `freight_carrier_dsv.py`)
| Field | Maps to integration map field |
|---|---|
| `x_dsv_product_name` | Selection: road/air/sea/rail |
| `x_dsv_subscription_key` | DSV-Subscription-Key header |
| `x_dsv_client_id` | OAuth client_id |
| `x_dsv_client_secret` | OAuth client_secret (encrypted) |
| `x_dsv_mdm` | MDM account number |
| `x_dsv_environment` | demo/production → base URL |
| `x_dsv_service_auth` | XPress DSV-Service-Auth header |
| `x_dsv_pat` | XPress x-pat header |
| `x_dsv_access_token` | Cached OAuth token (write=False in UI) |
| `x_dsv_token_expiry` | Datetime — lazy refresh trigger |

### `dsv_auth.py` — Token Manager
- `get_token(carrier)` → returns valid token, refreshing if within 2 min of expiry
- `_refresh_token(carrier)` → POST to DSV OAuth endpoint, stores token + expiry on carrier
- `DsvAuthError` — raised on 401/403, caught by adapter, surfaced as UserError
- When `x_dsv_environment = 'demo'` → returns `'DEMO_TOKEN'` without HTTP call

### `dsv_mock_adapter.py` — activated when `x_dsv_environment = 'demo'`
`request_quote()` returns:
```python
[
  {'service_name': 'DSV Road Standard', 'transport_mode': 'road',
   'total_rate': 1800.00, 'currency': 'NZD', 'transit_days': 5,
   'carrier_quote_ref': 'MOCK-ROAD-001'},
  {'service_name': 'DSV Air Express', 'transport_mode': 'air',
   'total_rate': 6200.00, 'currency': 'NZD', 'transit_days': 2,
   'carrier_quote_ref': 'MOCK-AIR-001'},
]
```
`create_booking()` returns mock booking ref `DSV-MOCK-BK-NNNN`.
`get_tracking()` returns a 3-event chain: Picked Up → In Transit → Arrived at Port.

---

## 3PL Inward Order Handoff

When `freight.booking.action_book()` is called and booking is confirmed:

```python
def _queue_3pl_inward_order(self):
    """Queue an inward order notice via stock_3pl_core message queue."""
    if 'stock_3pl_core' not in self.env.registry._init_modules:
        return  # graceful no-op if 3PL module not installed
    po = self.purchase_order_id
    warehouse = po.picking_type_id.warehouse_id
    connector = self.env['3pl.connector'].search([
        ('warehouse_id', '=', warehouse.id),
        ('active', '=', True),
    ], limit=1)
    if not connector:
        return  # no 3PL connector for this warehouse — log and skip
    msg = self.env['3pl.message'].create({
        'connector_id': connector.id,
        'direction': 'outbound',
        'document_type': 'inward_order',
        'action': 'create',
        'ref_model': 'purchase.order',
        'ref_id': po.id,
    })
    self.tpl_message_id = msg
```

`stock_3pl_mainfreight`'s inward order builder (already built, currently inactive) picks this up on its next cron run and pushes INWH/INWL XML to Mainfreight SFTP.

---

## Incoterm → Freight Responsibility

| Incoterm codes | `freight_responsibility` | Tender button shown? |
|---|---|---|
| EXW, FCA, FOB, FAS | `buyer` | Yes |
| CFR, CIF, CPT, CIP, DAP, DPU, DDP | `seller` | No |
| (no incoterm set) | `na` | No |

---

## Views & UI

### PO form additions
- **Smart button**: "Freight Tender" (ship icon, shows tender count) — visible when tender exists
- **Freight group**: visible when `freight_responsibility = buyer`. Fields: responsibility, cargo_ready_date, required_delivery_date, freight_mode_preference, separator, then readonly: carrier, cost, status (badge coloured), ETA, tracking URL (widget=url)
- **Action button**: "Request Freight Tender" (highlight) — visible when responsibility=buyer and no tender yet

### New menu items (under Inventory or Purchase)
- Freight Tenders (list + form)
- Freight Bookings (list + form)
- Freight Carriers (list + form, extends delivery.carrier form)

### Tender form
- Header: name, state (statusbar), PO link, incoterm, mode preference
- Origin/Destination group
- Package lines (editable tree)
- Totals: weight, CBM, chargeable weight, goods value
- **Quotes tree** (embedded): carrier, mode, service, rate (NZD), % vs cheapest, transit days, ETA, state badge — cheapest row green, fastest row blue, errors greyed
- Action buttons: "Request Quotes", "Auto-Select Best", "Book Selected"

### Booking form
- Header: name, state (statusbar), carrier, booking ref, PO link
- Tracking: current status, ETA, tracking URL
- Transport details: mode, vessel/container/AWB
- Financials: booked rate, actual rate, freight invoice
- Tracking events (readonly tree): date, status, location, description
- Documents (readonly tree): type, attachment

---

## Test Strategy

All tests use `TransactionCase`. No real HTTP calls — DSV HTTP is mocked with `unittest.mock.patch`.

| Test file | What it covers |
|---|---|
| `test_freight_responsibility.py` | All 11 incoterms → correct buyer/seller/na |
| `test_package_aggregation.py` | PO lines → correct weight/vol/CBM, DG flag propagation |
| `test_carrier_eligibility.py` | Country, weight, DG, mode filters — eligible/excluded cases |
| `test_quote_ranking.py` | Cheapest/fastest/best_value ranking, NZD conversion |
| `test_auto_select.py` | Correct winner per selection_mode, reason logged |
| `test_tender_lifecycle.py` | draft→requesting→quoted→selected→booked state machine |
| `test_3pl_handoff.py` | booking confirm → `3pl.message` created with correct fields |
| `test_dsv_auth.py` | Token cached within expiry; refreshed when stale; demo env skips HTTP |
| `test_dsv_mock_adapter.py` | Mock returns correct quote structure; booking returns mock ref |
| `test_cron_jobs.py` | Token refresh cron + tracking sync cron registered and callable |
| `test_po_form_fields.py` | PO inherit fields readable/writable; compute triggers correctly |
| `test_demo_install.py` | Demo module installs cleanly; demo PO has freight_responsibility=buyer |

---

## Demo Data (`mml_freight_demo`)

| Record | Details |
|---|---|
| Carrier: DSV Road NZ | `delivery_type=dsv_generic`, `x_dsv_environment=demo`, `auto_tender=True`, NZ+AU origin, transport_mode=road |
| Carrier: K+N Sea LCL | `delivery_type=knplus`, `auto_tender=True`, sea mode, global lanes |
| Supplier: Enduro Pet (AU) | `res.partner`, Melbourne AU address, `supplier_rank=1` |
| Product: Dog Food 20kg | `default_code=EPC-DOG-20`, weight=20, x_freight_length=40, width=30, height=25, `x_dangerous_goods=False` |
| Product: Cat Food 5kg | `default_code=EPC-CAT-5`, weight=5, dims set |
| Product: Bird Seed 10kg | `default_code=EPC-BIRD-10`, weight=10, dims set |
| PO 1 (ready to tender) | Enduro Pet, FOB incoterm, 100×Dog Food + 50×Cat Food, cargo_ready=+15d, required=+30d. `freight_responsibility=buyer`, no tender yet |
| PO 2 (pre-quoted) | Same supplier, state=`quoted`, mock quotes pre-loaded from both carriers — shows comparison table immediately |

---

## Cron Jobs (defined in `mml_freight/data/ir_cron.xml`)

| Job | Model | Method | Interval |
|---|---|---|---|
| DSV Token Refresh | `freight.carrier` | `cron_refresh_dsv_tokens` | Every 8 min |
| Freight Tracking Sync | `freight.booking` | `cron_sync_tracking` | Every 30 min |

---

## File Layout (complete)

```
addons/
├── mml_freight/
│   ├── __manifest__.py
│   ├── __init__.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── freight_carrier.py
│   │   ├── freight_tender.py
│   │   ├── freight_tender_package.py
│   │   ├── freight_tender_quote.py
│   │   ├── freight_booking.py
│   │   ├── freight_tracking_event.py
│   │   ├── freight_document.py
│   │   ├── freight_adapter_registry.py
│   │   ├── purchase_order.py
│   │   └── product_template.py
│   ├── adapters/
│   │   └── base_adapter.py
│   ├── controllers/
│   │   └── webhook.py
│   ├── wizards/
│   │   ├── __init__.py
│   │   ├── freight_tender_wizard.py
│   │   └── freight_manual_select_wizard.py
│   ├── views/
│   │   ├── freight_carrier_views.xml
│   │   ├── freight_tender_views.xml
│   │   ├── freight_booking_views.xml
│   │   ├── purchase_order_views.xml
│   │   └── menu.xml
│   ├── data/
│   │   ├── ir_sequence.xml
│   │   └── ir_cron.xml
│   ├── security/
│   │   └── ir.model.access.csv
│   └── tests/
│       ├── __init__.py
│       ├── test_freight_responsibility.py
│       ├── test_package_aggregation.py
│       ├── test_carrier_eligibility.py
│       ├── test_quote_ranking.py
│       ├── test_auto_select.py
│       ├── test_tender_lifecycle.py
│       ├── test_3pl_handoff.py
│       └── test_po_form_fields.py
│
├── mml_freight_dsv/
│   ├── __manifest__.py
│   ├── __init__.py
│   ├── models/
│   │   ├── __init__.py
│   │   └── freight_carrier_dsv.py
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── dsv_auth.py
│   │   ├── dsv_generic_adapter.py
│   │   ├── dsv_xpress_adapter.py
│   │   └── dsv_mock_adapter.py
│   ├── controllers/
│   │   └── dsv_webhook.py
│   ├── views/
│   │   └── freight_carrier_dsv_views.xml
│   ├── data/
│   │   └── dsv_package_types.xml
│   ├── security/
│   │   └── ir.model.access.csv
│   └── tests/
│       ├── __init__.py
│       ├── test_dsv_auth.py
│       ├── test_dsv_mock_adapter.py
│       └── test_cron_jobs.py
│
├── mml_freight_knplus/
│   ├── __manifest__.py
│   ├── __init__.py
│   ├── adapters/
│   │   ├── __init__.py
│   │   └── knplus_adapter.py
│   └── models/
│       ├── __init__.py
│       └── freight_carrier_knplus.py
│
└── mml_freight_demo/
    ├── __manifest__.py
    ├── __init__.py
    ├── data/
    │   ├── demo_carriers.xml
    │   ├── demo_partners.xml
    │   ├── demo_products.xml
    │   └── demo_purchase_orders.xml
    └── tests/
        ├── __init__.py
        └── test_demo_install.py
```
