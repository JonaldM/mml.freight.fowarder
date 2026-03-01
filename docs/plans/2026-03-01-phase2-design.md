# Phase 2 — DSV Generic Live Integration Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:writing-plans to create the implementation plan from this design.

**Goal:** Replace the DSV Generic mock adapter with a fully live integration covering quote, booking, tracking, and Mainfreight inward order handoff — end-to-end from PO to warehouse receipt notice.

**Architecture:** Odoo-embedded adapter pattern. All DSV API calls originate from `mml_freight_dsv`. The `freight.tender` / `freight.booking` / `freight.tracking.event` models remain carrier-agnostic. The `3pl.message` queue in `stock_3pl_core` handles Mainfreight handoff.

**Tech Stack:** Odoo 19, Python 3.12, DSV Generic API (OAuth2 + Subscription Key), `requests`, `stock_3pl_core` message queue, Mainfreight SFTP via `stock_3pl_mainfreight`.

**Scope:** DSV Generic (Road/Air/Sea/Rail) only. DSV XPress deprioritised. Primary use case: LCL/FCL sea freight NZ imports, occasional air.

---

## 1. Data Flow

```
purchase.order
    ↓ action_request_freight_tender()          [existing]
freight.tender
    ↓ auto-populate package_line_ids           [NEW]
      1 PO line → 1 tender package line
      dims from product master (x_freight_*)
      warn if dims missing, do not block
    ↓ action_request_quotes()                  [existing trigger]
    ↓ DsvGenericAdapter.request_quote()        [NEW — live]
      → LCL/FCL mode selected by CBM threshold
      → grey zone: two parallel requests
      → parse response → freight.tender.quote records
      → auto-select recommendation (existing algorithm)
      → all quotes visible, freight team can override
    ↓ action_book()                            [existing trigger]
    ↓ DsvGenericAdapter.create_booking()       [NEW — live]
      → autobook=False → DSV draft created
      → carrier_booking_id / carrier_shipment_id stored
freight.booking  [state=draft]
    ↓ "Confirm with DSV" button                [NEW]
    ↓ DsvGenericAdapter.confirm_booking()      [NEW]
      → DSV releases shipment, live ref returned
      → vessel / voyage / container / ETA stored
      → state → confirmed
      → _build_inward_order_payload() called
        → 3pl.message payload_xml populated
        → message state: draft → queued
        → stock_3pl_core cron → Mainfreight SFTP
    ↓ cron_sync_tracking() every 30 min        [existing trigger]
    ↓ DsvGenericAdapter.get_tracking()         [NEW — live]
      → freight.tracking.event records created
      → booking.state auto-advanced
      → _check_inward_order_updates() called
        → ETA drift > 24h → 3pl.message action='update' queued
        → vessel TBA → known → 3pl.message action='update' queued
    ↓ DSV webhook (real-time)                  [NEW — implement stub]
      → same tracking + update logic as cron
```

---

## 2. Package Auto-Population

**Trigger:** when `action_request_freight_tender()` creates a `freight.tender` from a PO.

**Mapping:** one `purchase.order.line` → one `freight.tender.package` line.

| PO line field | Package field | Source |
|---|---|---|
| `product_id.name` | `description` | product.name |
| `product_qty` | `quantity` | PO line qty |
| `product_id.x_freight_weight` | `weight_kg` | product master |
| `product_id.x_freight_length` | `length_cm` | product master |
| `product_id.x_freight_width` | `width_cm` | product master |
| `product_id.x_freight_height` | `height_cm` | product master |
| `product_id.x_dangerous_goods` | `is_dangerous` | product master |
| `product_id.hs_code` | `hs_code` | product master |
| computed | `volume_m3` | L×W×H/1,000,000 × qty |

**Missing dims:** if `weight_kg` or any dimension is zero, post a warning on the tender chatter: _"Product [name] is missing freight dimensions — package line populated with zeros, please update before requesting quotes."_ Do not block quote request.

---

## 3. LCL / FCL Mode Selection

**CBM thresholds** (configurable fields on `delivery.carrier`):

| `total_cbm` | Mode(s) requested | DSV productType(s) |
|---|---|---|
| < 15 | LCL only | `SEA_LCL` |
| 15 – 25 (grey zone) | LCL + FCL 20ft | `SEA_LCL`, `SEA_FCL_20` |
| 25 – 40 (grey zone) | FCL 20ft + FCL 40ft | `SEA_FCL_20`, `SEA_FCL_40` |
| > 40 | FCL 40ft only | `SEA_FCL_40` |

Grey zone triggers two parallel DSV quote requests. All returned quotes land on the same tender and are ranked together by the auto-select algorithm.

**Carrier fields added:**
```python
x_dsv_lcl_fcl_threshold = fields.Float('LCL→FCL Threshold (CBM)', default=15)
x_dsv_fcl20_fcl40_threshold = fields.Float('FCL20→FCL40 Threshold (CBM)', default=25)
x_dsv_fcl40_upper = fields.Float('FCL40 Upper Threshold (CBM)', default=40)
```

**Override:** `freight_mode_preference` on the tender (any/sea/air/road) can be set manually before requesting quotes. If set to a specific mode, auto-selection is bypassed and only that mode is requested.

**Air:** if `freight_mode_preference == 'air'`, request DSV `AIR_EXPRESS`. No CBM thresholds apply.

---

## 4. DSV Quote API

**Endpoint:** `POST /qs/quote/v1/quotes`
**Auth:** OAuth2 Bearer + `Ocp-Apim-Subscription-Key` header (existing `dsv_auth.get_token()`)

**Request payload built from `freight.tender`:**
```json
{
  "from": {
    "country": "origin_country_id.code",
    "city": "origin_partner_id.city",
    "zipCode": "origin_partner_id.zip",
    "addressLine1": "origin_partner_id.street"
  },
  "to": {
    "country": "dest_country_id.code",
    "city": "dest_partner_id.city",
    "zipCode": "dest_partner_id.zip",
    "addressLine1": "dest_partner_id.street"
  },
  "pickupDate": "requested_pickup_date",
  "incoterms": "incoterm_id.code",
  "productType": "SEA_LCL",
  "mdmNumber": "x_dsv_mdm",
  "packages": [
    {
      "quantity": "line.quantity",
      "description": "line.description",
      "grossWeight": "line.weight_kg",
      "length": "line.length_cm",
      "width": "line.width_cm",
      "height": "line.height_cm",
      "volume": "line.volume_m3",
      "dangerousGoods": "line.is_dangerous",
      "harmonizedCode": "line.hs_code"
    }
  ],
  "unitsOfMeasurement": {
    "weight": "KG",
    "dimension": "CM",
    "volume": "M3"
  }
}
```

**Response mapping** → `freight.tender.quote`:

| DSV field | Quote field |
|---|---|
| `serviceCode` | `carrier_quote_ref` |
| `serviceName` | `service_name` |
| `productType` | `transport_mode` |
| `totalCharge.amount` | `total_rate` |
| `totalCharge.currency` | `currency_id` |
| `transitDays` | `estimated_transit_days` |
| full response JSON | `raw_response` |

**Error handling:**
- `401/403` → token refresh + one retry (existing `dsv_auth` pattern)
- `400/422` → `quote.state = 'error'`, store `error_message`, tender continues
- `429/500` → same as 400/422, log and continue

---

## 5. DSV Booking API

### 5a. Create draft booking

**Endpoint:** `POST /booking/v2/bookings`
**Payload:** built from `freight.tender` + selected `freight.tender.quote`

Key fields:
```json
{
  "autobook": false,
  "productType": "quote.transport_mode",
  "mdmNumber": "carrier.x_dsv_mdm",
  "quoteId": "quote.carrier_quote_ref",
  "pickupDate": "tender.requested_pickup_date",
  "incoterms": "tender.incoterm_id.code",
  "shipper": { from tender.origin_partner_id },
  "consignee": { from tender.dest_partner_id },
  "packages": [ same as quote payload ],
  "goodsDescription": "concatenated from package descriptions",
  "customerReference": "tender.purchase_order_id.name"
}
```

**Response:**
```
carrier_booking_id  = response.bookingId
carrier_shipment_id = response.shipmentId
```

**Error handling:**
- Any error → `UserError` raised to freight team, `freight.booking` record NOT created, tender stays in `selected` state for retry.

### 5b. Confirm with DSV

**New method:** `action_confirm_with_dsv()` on `freight.booking`
**New button:** on booking form, visible when `state == 'draft'` and `carrier_booking_id` set

**Endpoint:** `POST /booking/v2/bookings/{carrier_booking_id}/confirm`

**On success:**
- Store vessel, voyage, container, ETA from response
- `state → confirmed`
- Call `_build_inward_order_payload()` → populate `tpl_message_id.payload_xml` → advance to `queued`
- Post chatter: _"Booking confirmed with DSV. Inward order notice queued to Mainfreight."_

**Error handling:**
- `UserError` on failure, state unchanged, freight team retries.

### 5c. Cancel draft booking

**New method:** `cancel_booking(booking)` on `FreightAdapterBase` (default no-op) and `DsvGenericAdapter`

**Endpoint:** `DELETE /booking/v2/bookings/{carrier_booking_id}`

**Triggered by:**
1. `freight.booking.action_cancel()` — if `carrier_booking_id` set and `state == 'draft'`
2. `action_book()` on tender — if a prior draft booking exists, cancel it before creating a new one

**On 404:** DSV draft already gone — treat as success, log info.

**On confirmed booking cancel attempt:**
- Do NOT call DSV API
- Post chatter warning: _"This booking is already confirmed with DSV. Contact DSV directly to cancel — cancellation fees may apply."_
- Odoo booking still moves to `cancelled`

---

## 6. Tracking Sync

### 6a. Vessel model extension

`freight.booking` gets two vessel tiers:

```python
feeder_vessel_name   = fields.Char('Feeder Vessel')    # first leg
feeder_voyage_number = fields.Char('Feeder Voyage No.')
# vessel_name / voyage_number already exist = last mile (NZ arrival)
```

Inward order to Mainfreight uses only `vessel_name` / `voyage_number` (last mile).

### 6b. Polling cron (30-min, existing)

**Endpoint:** `GET /tracking/v1/shipments/{carrier_shipment_id}/events`

**DSV event → Odoo state mapping:**

| DSV eventType | booking.state |
|---|---|
| `BOOKING_CONFIRMED` | `confirmed` |
| `CARGO_RECEIVED` | `cargo_ready` |
| `DEPARTURE` | `in_transit` |
| `ARRIVED_POD` | `arrived_port` |
| `CUSTOMS_CLEARED` | `customs` |
| `DELIVERED` | `delivered` |

**ETA:** write `response.estimatedDelivery` to `booking.eta` on every poll.

**`_check_inward_order_updates(prev_eta, prev_vessel)`** called after each sync:
```python
eta_drifted = abs((self.eta - prev_eta).total_seconds()) > 86400  # 24h threshold
vessel_now_known = not prev_vessel and bool(self.vessel_name)
if eta_drifted or vessel_now_known:
    self._queue_inward_order_update()
```

**Tracking errors:** log and skip, non-fatal, next cron retries.

### 6c. Webhook (real-time)

**Implements** the `_handle_dsv_tracking_webhook()` stub in `freight_booking.py`.

**Security (already in place):** HMAC-SHA256 validated by `dsv_webhook.py` before any ORM access.

**On `TRACKING_UPDATE` event:**
1. Find booking by `carrier_shipment_id` in payload
2. Verify `booking.carrier_id == carrier` (anti-spoofing)
3. Sanitise all string fields (max length 255, strip control chars)
4. Create `freight.tracking.event`
5. Auto-advance `booking.state`
6. Call `_check_inward_order_updates()`

---

## 7. Inward Order Payload Builder

**Location:** `stock_3pl_mainfreight/document/inward_order.py` (consistent with existing builders)

**Stage 1 — Create** (on `action_confirm_with_dsv()`):

```xml
<InwardOrder action="CREATE">
  <OrderRef>{po.name}</OrderRef>
  <BookingRef>{booking.carrier_booking_id}</BookingRef>
  <Supplier>
    <Name>{po.partner_id.name}</Name>
    <Address>{po.partner_id.street}, {po.partner_id.city}</Address>
    <Country>{po.partner_id.country_id.code}</Country>
  </Supplier>
  <Consignee>
    <Name>{warehouse.partner_id.name}</Name>
    <Address>{warehouse.partner_id.street}, {warehouse.partner_id.city}</Address>
    <Country>{warehouse.partner_id.country_id.code}</Country>
    <WarehouseCode>{connector.warehouse_code}</WarehouseCode>
  </Consignee>
  <ExpectedArrival>{booking.eta}</ExpectedArrival>
  <Transport>
    <Mode>{booking.transport_mode}</Mode>
    <Vessel>{booking.vessel_name or 'TBA'}</Vessel>
    <VoyageNo>{booking.voyage_number or 'TBA'}</VoyageNo>
    <ContainerNo>{booking.container_number}</ContainerNo>
  </Transport>
  <Lines>
    <Line>
      <ProductCode>{po_line.product_id.default_code}</ProductCode>
      <Description>{po_line.product_id.name}</Description>
      <Quantity>{po_line.product_qty}</Quantity>
      <UOM>{po_line.product_uom.name}</UOM>
      <WeightKg>{po_line.product_id.x_freight_weight * po_line.product_qty}</WeightKg>
    </Line>
  </Lines>
</InwardOrder>
```

**Stage 2 — Update** (triggered by `_queue_inward_order_update()`):

Same structure with `action="UPDATE"` and `OrderRef` as the correlation key. Only updated fields need to change (ETA, Vessel, VoyageNo).

**XML schema** validated against `docs/Mainfreight Warehousing Integration Specification.pdf` during implementation.

---

## 8. Error Handling Summary

| Operation | Fatal? | Odoo behaviour | DSV state |
|---|---|---|---|
| Quote 400/422 | No | Quote marked error, tender continues | No DSV record |
| Quote 401/403 | No | Token refresh + retry, then error | No DSV record |
| Quote 500 | No | Quote marked error | No DSV record |
| Booking any error | Yes | UserError, no booking record | No DSV record |
| Confirm any error | Yes | UserError, booking stays draft | DSV draft unchanged |
| Cancel 404 | No | Treat as success, log info | Already gone |
| Cancel confirmed | Never called | Chatter warning, Odoo → cancelled | DSV untouched |
| Tracking any error | No | Log, skip, retry next cron | Unchanged |
| Inward order fail | No | stock_3pl_core retry → dead letter | N/A |

---

## 9. New Fields Summary

### `delivery.carrier` (in `mml_freight_dsv`)
```python
x_dsv_lcl_fcl_threshold    Float  default=15   # CBM where LCL → FCL grey zone starts
x_dsv_fcl20_fcl40_threshold Float  default=25   # CBM where FCL20 → FCL40 grey zone starts
x_dsv_fcl40_upper           Float  default=40   # CBM above which FCL40 only
```

### `freight.booking` (in `mml_freight`)
```python
feeder_vessel_name    Char   # first-leg vessel
feeder_voyage_number  Char   # first-leg voyage
```

---

## 10. Test Files

| File | Covers |
|---|---|
| `test_dsv_quote_payload.py` | Payload builder: all fields, LCL/FCL threshold logic, grey-zone dual request, missing dims warning |
| `test_dsv_booking_payload.py` | Booking request payload, autobook=False, cancel on forwarder switch |
| `test_dsv_confirm_booking.py` | Confirm action, state transition, inward order trigger |
| `test_dsv_cancel.py` | Cancel draft (success + 404 tolerance), confirmed booking warning |
| `test_dsv_tracking.py` | Event mapping, state auto-advance, ETA drift detection, vessel TBA→known |
| `test_dsv_webhook.py` | Webhook handler: valid event, booking ownership check, string sanitisation |
| `test_inward_order_builder.py` | XML payload: create + update actions, TBA vessel, field mapping |
| `test_dsv_generic_adapter.py` | Full adapter happy path (mocked HTTP): quote → book → confirm → track |

All HTTP calls mocked — no live DSV credentials required.

---

## 11. Out of Scope (Phase 2)

- DSV XPress adapter
- DSV label / POD document download
- DSV invoice API
- Landed cost allocation
- K+N / Mondiale / other carrier adapters
- Operations dashboard (Phase N)
- Auto-tender on PO confirm (Phase 3)
