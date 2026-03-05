# MML Freight Forwarding Module — Sprint Spec

## Context

This is the `mml_freight_forwarding` module for Odoo 19. It integrates with the DSV freight forwarding API to manage freight tenders, bookings, and shipment tracking. This module is a dependency of the `mml_roq_forecast` module (see separate project spec) but can be built and deployed independently.

The ROQ module's consolidation engine produces **shipment groups** — planned groupings of multiple supplier POs shipping from the same FOB port. This freight module receives those shipment groups and converts them into freight tenders via the DSV API.

**If this module is not yet deployed**, the ROQ module still functions — shipment groups exist as internal planning records and freight is arranged manually. This module activates the automation layer.

- **Target Odoo version:** 19 (self-hosted)
- **Module technical name:** `mml_freight_forwarding`
- **Builder:** Claude Code + Jono
- **API partner:** DSV (existing integration work in progress)

---

## Sprint Goal

Deliver a working freight module that can:

1. Receive a shipment group from the ROQ module (or be triggered manually)
2. Generate and submit a freight tender request to the DSV API
3. Receive and store the freight quote/booking response
4. Track shipment status (ETD, ETA, vessel, container number)
5. Feed actual delivery dates back to the ROQ module for lead time accuracy tracking

---

## Architecture

```
┌──────────────────────────────────┐
│  mml_roq_forecast                │
│  (Shipment Group)                │
│  status: confirmed ──────────────┼──► Trigger freight tender
└──────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────┐
│  mml_freight_forwarding          │
│                                  │
│  ┌─────────────┐                 │
│  │ Tender       │ ◄── Build request from shipment group
│  │ Request      │ ──► Submit to DSV API
│  └─────┬───────┘                 │
│        │                         │
│        ▼                         │
│  ┌─────────────┐                 │
│  │ Freight      │ ◄── DSV quote response
│  │ Quote        │                │
│  └─────┬───────┘                 │
│        │ (user accepts)          │
│        ▼                         │
│  ┌─────────────┐                 │
│  │ Booking      │ ◄── DSV booking confirmation
│  │ Confirmation │ ──► Update shipment group status
│  └─────┬───────┘                 │
│        │                         │
│        ▼                         │
│  ┌─────────────┐                 │
│  │ Shipment     │ ◄── DSV tracking updates
│  │ Tracking     │ ──► Feed actual dates back to ROQ
│  └─────────────┘                 │
└──────────────────────────────────┘
```

---

## Data Model

### freight.tender

Freight tender request — one per shipment group or manually created.

| Field | Type | Description |
|---|---|---|
| `id` | Integer | Auto |
| `name` | Char | Auto-generated reference (e.g., `FT-2026-0042`) |
| `shipment_group_id` | Many2one → `roq.shipment.group` | Link to ROQ consolidation group (nullable for manual tenders) |
| `origin_port` | Char | FOB port (populated from shipment group or manual entry) |
| `destination_port` | Char | NZ destination port |
| `destination_warehouse_ids` | Many2many → `stock.warehouse` | Which warehouses receive this shipment |
| `container_type` | Selection | 20' / 40' / 40'HQ / LCL |
| `total_cbm` | Float | Total cubic metres |
| `total_weight_kg` | Float | Total weight (if available) |
| `target_ship_date` | Date | Requested ETD |
| `target_delivery_date` | Date | Requested ETA / delivery |
| `supplier_count` | Integer | Number of suppliers in this shipment |
| `po_ids` | Many2many → `purchase.order` | Linked purchase orders |
| `cargo_description` | Text | Free-text cargo description for tender |
| `special_requirements` | Text | Temperature control, hazmat, etc. |
| `status` | Selection | `draft` / `submitted` / `quoted` / `accepted` / `booked` / `cancelled` |
| `dsv_reference` | Char | DSV tender/quote reference number |
| `submitted_at` | Datetime | When tender was submitted to DSV |
| `notes` | Text | Internal notes |

### freight.quote

Quote received from DSV (or other forwarder). One tender can receive multiple quotes if DSV offers options.

| Field | Type | Description |
|---|---|---|
| `id` | Integer | Auto |
| `tender_id` | Many2one → `freight.tender` | Parent tender |
| `dsv_quote_reference` | Char | DSV quote ID |
| `carrier` | Char | Shipping line |
| `vessel_name` | Char | Vessel (if known at quote stage) |
| `etd` | Date | Estimated time of departure |
| `eta` | Date | Estimated time of arrival |
| `transit_days` | Integer | Transit time |
| `ocean_freight_cost` | Float | Ocean freight charge |
| `local_charges_origin` | Float | Origin port charges |
| `local_charges_dest` | Float | Destination port charges |
| `total_cost` | Float | Total quoted cost |
| `currency` | Char | Quote currency (default NZD) |
| `cost_per_cbm` | Float | Computed: total_cost / total_cbm |
| `valid_until` | Date | Quote expiry |
| `is_accepted` | Boolean | Whether this quote was accepted |
| `notes` | Text | |

### freight.booking

Confirmed booking — created when a quote is accepted.

| Field | Type | Description |
|---|---|---|
| `id` | Integer | Auto |
| `name` | Char | Booking reference (e.g., `FB-2026-0042`) |
| `quote_id` | Many2one → `freight.quote` | Accepted quote |
| `tender_id` | Many2one → `freight.tender` | Parent tender (denormalised for convenience) |
| `shipment_group_id` | Many2one → `roq.shipment.group` | Link back to ROQ |
| `dsv_booking_reference` | Char | DSV booking number |
| `carrier` | Char | Shipping line |
| `vessel_name` | Char | Vessel |
| `voyage_number` | Char | Voyage |
| `container_number` | Char | Container ID (once assigned) |
| `bl_number` | Char | Bill of lading number |
| `etd` | Date | Confirmed ETD |
| `eta` | Date | Confirmed ETA |
| `atd` | Date | Actual time of departure (from tracking) |
| `ata` | Date | Actual time of arrival (from tracking) |
| `delivered_date` | Date | Actual delivery to warehouse |
| `status` | Selection | `confirmed` / `departed` / `in_transit` / `arrived` / `delivered` / `cancelled` |
| `customs_status` | Selection | `pending` / `cleared` / `held` |
| `total_cost_actual` | Float | Final invoiced cost (may differ from quote) |
| `po_ids` | Many2many → `purchase.order` | Linked POs |
| `notes` | Text | |

### freight.tracking.event

Timeline of tracking events for a booking. Populated from DSV tracking API or manual entry.

| Field | Type | Description |
|---|---|---|
| `id` | Integer | Auto |
| `booking_id` | Many2one → `freight.booking` | Parent booking |
| `event_date` | Datetime | When the event occurred |
| `event_type` | Selection | `gate_in` / `loaded` / `departed` / `transhipment` / `arrived` / `customs_cleared` / `delivered` / `other` |
| `location` | Char | Port or location name |
| `description` | Text | Event description |
| `dsv_event_code` | Char | Raw DSV event code |
| `source` | Selection | `api` / `manual` | How the event was recorded |

---

## DSV API Integration

### Authentication

- Confirm DSV API auth method (API key, OAuth, basic auth) before implementation.
- Store credentials in Odoo system parameters (`ir.config_parameter`), not in code.
- All API calls go through a single service class (`DsvApiService`) for consistent error handling and logging.

### Endpoints Required

| Action | DSV Endpoint | When |
|---|---|---|
| Submit tender / request quote | TBD (confirm with DSV) | User clicks "Submit Tender" on freight.tender |
| Retrieve quotes | TBD | Poll or webhook after submission |
| Accept quote / book | TBD | User clicks "Accept" on freight.quote |
| Get tracking updates | TBD | Scheduled cron job (daily or more frequent) |

**To confirm:** DSV API endpoint URLs, authentication method, request/response schemas. The module should be built with a clean API adapter pattern so that if DSV's endpoints change or a second forwarder is added later, only the adapter needs updating.

### API Adapter Pattern

```python
class FreightForwarderAdapter:
    """Base class for freight forwarder API integrations."""

    def submit_tender(self, tender_data: dict) -> dict:
        raise NotImplementedError

    def get_quotes(self, tender_reference: str) -> list[dict]:
        raise NotImplementedError

    def accept_quote(self, quote_reference: str) -> dict:
        raise NotImplementedError

    def get_tracking(self, booking_reference: str) -> list[dict]:
        raise NotImplementedError


class DsvAdapter(FreightForwarderAdapter):
    """DSV-specific implementation."""

    def __init__(self, api_key, base_url):
        self.api_key = api_key
        self.base_url = base_url

    def submit_tender(self, tender_data):
        # DSV-specific request formatting and submission
        ...
```

This pattern allows adding Mainfreight, Kuehne+Nagel, or any other forwarder later without changing the core module logic.

### Request Building (Tender → DSV)

The tender request must include sufficient detail for DSV to quote:

```python
tender_request = {
    "origin_port": tender.origin_port,          # e.g., "Shenzhen, CN"
    "destination_port": tender.destination_port, # e.g., "Auckland, NZ"
    "cargo": {
        "container_type": tender.container_type,    # "20GP" / "40GP" / "40HQ" / "LCL"
        "total_cbm": tender.total_cbm,
        "total_weight_kg": tender.total_weight_kg,
        "description": tender.cargo_description,
        "special_requirements": tender.special_requirements,
    },
    "schedule": {
        "target_etd": tender.target_ship_date.isoformat(),
        "flexibility_days": 7,  # how much schedule flexibility we allow
    },
    "reference": tender.name,
    "supplier_count": tender.supplier_count,
    "consolidation": tender.supplier_count > 1,  # flag that this is a consolidated shipment
    "purchase_order_references": [po.name for po in tender.po_ids],
}
```

**Consolidation flag:** When `supplier_count > 1`, DSV needs to know this is a consolidated shipment from multiple factories. They may need to arrange pickup from multiple locations within the port area, or the cargo may arrive at the CFS (Container Freight Station) from different suppliers for stuffing into a single container.

### Response Handling

Quotes from DSV are parsed and stored as `freight.quote` records. The mapping from DSV's response schema to our model should be in the adapter, keeping the core model clean.

### Tracking Cron

A scheduled action (`ir.cron`) runs daily (configurable) to:

1. Query DSV tracking API for all `freight.booking` records in status `confirmed` / `departed` / `in_transit`.
2. Create `freight.tracking.event` records for any new events.
3. Update booking status based on events:
   - `gate_in` or `loaded` → `departed`
   - `departed` → `in_transit`
   - `arrived` → `arrived`
   - `customs_cleared` + `delivered` → `delivered`
4. When a booking reaches `delivered`:
   - Set `delivered_date`
   - Update the linked `roq.shipment.group` status to `delivered`
   - **Feed actual lead time back to ROQ:** compute `actual_lead_time = delivered_date - po_date` and store for lead time accuracy analysis.

---

## Lead Time Feedback Loop

This is the critical integration point with the ROQ module. Actual delivery dates allow the forecasting system to:

1. **Track lead time accuracy per supplier:** Compare assumed lead time vs actual, build a distribution of lead time variability (σ_LT).
2. **Future enhancement:** Incorporate lead time variability into safety stock: `SS = Z × √(LT × σ²_demand + D² × σ²_LT)`.
3. **Alert on lead time drift:** If a supplier's actual lead times are consistently longer than assumed, surface a warning recommending a lead time parameter update.

### Implementation

On booking delivery, compute and store:

```python
actual_lead_time_days = (booking.delivered_date - booking.tender_id.po_ids[0].date_order).days
assumed_lead_time_days = supplier.supplier_lead_time_days or system_default

lead_time_variance = actual_lead_time_days - assumed_lead_time_days
```

Store per-booking on `freight.booking`:
- `actual_lead_time_days` (Integer)
- `lead_time_variance_days` (Integer, positive = late, negative = early)

Aggregate per supplier (computed fields on `res.partner`):
- `avg_lead_time_actual` (Float) — rolling average of actual lead times
- `lead_time_std_dev` (Float) — standard deviation of actual lead times
- `lead_time_on_time_pct` (Float) — % of deliveries within ±7 days of assumed lead time

---

## UI Views

### Freight Tender (List + Form)

**List view:** All tenders, filterable by status, FOB port, date range. Colour-coded by status.

**Form view:**
- Header: reference, status, FOB port → destination port, dates
- Tabs:
  - **Cargo:** container type, CBM, weight, description, PO links
  - **Quotes:** embedded list of received quotes with accept button
  - **Booking:** booking details (once accepted), tracking timeline
  - **Notes**

**Action buttons on form:**
- `Submit Tender` (draft → submitted) — calls DSV API
- `Accept Quote` (on quote line) — creates booking
- `Cancel` — cancels tender/booking with DSV
- `Refresh Tracking` — manual trigger of tracking API call

### Booking Dashboard (Kanban)

Kanban columns: `confirmed` → `departed` → `in_transit` → `arrived` → `delivered`

Cards show:
- Booking reference
- Carrier + vessel
- FOB port → destination
- ETD / ETA
- Days in transit
- Linked PO count
- OOS risk flag (any linked SKU projected OOS before ETA)

### Tracking Timeline (Embedded in Booking Form)

Vertical timeline of tracking events, most recent first. Each event shows:
- Date/time
- Event type (icon)
- Location
- Description

### Supplier Lead Time Report

Per-supplier analysis:
- Assumed vs actual lead time distribution
- On-time delivery percentage
- Trend (improving / deteriorating)
- Recommendation: adjust lead time parameter if consistently drifting

---

## Cron Jobs

| Job | Frequency | Description |
|---|---|---|
| `Tracking Update` | Daily (configurable) | Poll DSV for tracking events on active bookings |
| `Quote Expiry Check` | Daily | Flag quotes expiring within 48 hours |
| `Lead Time Alert` | Weekly | Check for suppliers where actual LT deviates > 14 days from assumed |

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| DSV API timeout | Retry 3x with exponential backoff. Log failure. Leave tender in `submitted` status. Alert user. |
| DSV returns error | Parse error response, store in tender notes. Leave status unchanged. Alert user. |
| DSV API unavailable (extended) | Tender remains in `submitted`. Cron retries daily. User can manually fall back. |
| Quote expired before acceptance | Mark quote as expired. User can resubmit tender for fresh quotes. |
| Booking cancelled by DSV | Update status to `cancelled`. Alert user. Flag any linked POs that need re-tendering. |
| Tracking event out of sequence | Store event anyway (data from DSV is authoritative). Log warning for review. |

---

## Testing

### Unit Tests

- Tender creation from shipment group (verify all fields mapped correctly)
- Request building (verify DSV payload format)
- Response parsing (verify quotes parsed correctly from DSV response)
- Status transitions (verify valid transitions, reject invalid ones)
- Lead time calculation (verify actual vs assumed computation)

### Integration Tests (with DSV sandbox)

- Submit a test tender → receive quote → accept → verify booking created
- Tracking poll → verify events stored and status updated
- End-to-end: shipment group → tender → quote → booking → tracking → delivered → lead time feedback

### Mock Mode

For development without DSV API access:

- `DsvMockAdapter` that returns canned responses with configurable delays
- Toggle via system parameter: `freight.dsv.use_mock = True`
- Mock responses should include realistic data (port names, carrier names, transit times) so UI development isn't blocked

---

## Sprint Deliverables

| # | Deliverable | Priority |
|---|---|---|
| 1 | Data models (`freight.tender`, `freight.quote`, `freight.booking`, `freight.tracking.event`) | P1 |
| 2 | API adapter pattern + DSV adapter (with mock mode) | P1 |
| 3 | Tender form view + submit action | P1 |
| 4 | Quote display + accept action | P1 |
| 5 | Booking form with tracking timeline | P1 |
| 6 | Tracking cron job | P2 |
| 7 | Lead time feedback computation | P2 |
| 8 | Booking dashboard (Kanban) | P2 |
| 9 | Supplier lead time report | P3 |
| 10 | Quote expiry alerting | P3 |

---

## Open Questions

1. **DSV API credentials:** Where are these currently stored? What auth method?
2. **DSV API endpoints:** Exact URLs and schemas for tender submission, quote retrieval, booking, tracking.
3. **DSV consolidation handling:** Does DSV have a specific API field or process for consolidated shipments (multiple pickup points within a port)?
4. **Existing DSV module state:** What's already built? Any existing models or API calls we should integrate with rather than rebuild?
5. **Shipment group model:** Does the ROQ module's `roq.shipment.group` already exist, or should this module define the interface and the ROQ module conform to it?
6. **Multi-carrier future:** Any plans to add Mainfreight or other forwarders for domestic / Tasman routes? If so, the adapter pattern is the right investment now.
