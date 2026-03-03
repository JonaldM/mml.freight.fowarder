# DSV API ↔ Odoo Model Integration Map

## Overview

This document maps DSV API operations to the required Odoo models for MML Consumer Products' freight integration. Models are categorised by integration tier: **Core** (must connect), **Supporting** (referenced by core), and **Optional** (nice-to-have visibility).

---

## Core Models (Direct DSV Integration Points)

These models have direct read/write interaction with DSV API calls.

### 1. `sale.order` — Sales Order

**DSV Relevance:** Primary trigger for booking creation. Each SO (or group of SOs) generates a DSV shipment booking.

| Odoo Field | DSV Mapping | Direction |
|-----------|-------------|-----------|
| `name` | `references[].value` (type: `ORDER_NUMBER`) | Odoo → DSV |
| `partner_id` → address fields | `parties.receiver` / `parties.delivery` | Odoo → DSV |
| `partner_shipping_id` | `parties.delivery.address` | Odoo → DSV |
| `commitment_date` / `expected_date` | `deliveryTime.date` | Odoo → DSV |
| `warehouse_id` → address | `parties.pickup.address` (Mainfreight 3PL) | Odoo → DSV |
| `amount_total` | `services.insurance.amount.value` (if insured) | Odoo → DSV |
| `carrier_id` | Determines Generic vs XPress API path | Odoo → DSV |
| Custom field: `x_dsv_booking_id` | DSV Booking ID (returned from API) | DSV → Odoo |
| Custom field: `x_dsv_shipment_id` | DSV Shipment ID (from tracking) | DSV → Odoo |
| Custom field: `x_dsv_tracking_status` | Shipment status from tracking API | DSV → Odoo |

### 2. `sale.order.line` — Sales Order Line

**DSV Relevance:** Drives package construction — aggregating lines into DSV `packages[]` array.

| Odoo Field | DSV Mapping | Direction |
|-----------|-------------|-----------|
| `product_id` → weight/dimensions | `packages[].totalWeight`, `length`, `width`, `height` | Odoo → DSV |
| `product_uom_qty` | `packages[].quantity` | Odoo → DSV |
| `product_id.hs_code` | `packages[].harmonizedCode` (Air/Sea) | Odoo → DSV |
| `product_id.description_sale` | `packages[].description` | Odoo → DSV |

### 3. `stock.picking` — Transfer / Delivery Order

**DSV Relevance:** The actual shipment trigger. When a picking is confirmed/validated, this fires the DSV booking. Also receives tracking updates.

| Odoo Field | DSV Mapping | Direction |
|-----------|-------------|-----------|
| `name` | `references[].value` (type: `SHIPPER_REFERENCE`) | Odoo → DSV |
| `origin` (SO reference) | `references[].value` (type: `ORDER_NUMBER`) | Odoo → DSV |
| `scheduled_date` | `pickupTime.date` | Odoo → DSV |
| `partner_id` → address | `parties.receiver.address` | Odoo → DSV |
| `location_id` → warehouse address | `parties.pickup.address` | Odoo → DSV |
| `carrier_tracking_ref` | DSV Booking/Shipment ID | DSV → Odoo |
| `carrier_id` | Route to correct DSV API (Generic/XPress) | Odoo → DSV |
| Custom field: `x_dsv_label_url` | Label PDF download link | DSV → Odoo |
| Custom field: `x_dsv_pod_available` | POD webhook flag | DSV → Odoo |

### 4. `delivery.carrier` — Shipping Method

**DSV Relevance:** Configuration model that holds DSV API credentials and determines which DSV product/service is used.

| Odoo Field | DSV Mapping | Direction |
|-----------|-------------|-----------|
| `name` | Carrier display name (e.g., "DSV Road EU", "DSV XPress") | Config |
| `delivery_type` | Custom: `dsv_generic` or `dsv_xpress` | Config |
| Custom: `x_dsv_product_name` | `product.name` (Road/Air/Sea/Rail) | Config → DSV |
| Custom: `x_dsv_subscription_key` | `DSV-Subscription-Key` header | Config |
| Custom: `x_dsv_client_id` | OAuth `client_id` (myDSV username) | Config |
| Custom: `x_dsv_client_secret` | OAuth `client_secret` (myDSV password) | Config |
| Custom: `x_dsv_mdm` | MDM account number for `bookingParty` / `freightPayer` | Config |
| Custom: `x_dsv_environment` | `demo` / `production` → base URL switch | Config |
| Custom: `x_dsv_service_auth` | XPress `DSV-Service-Auth` header | Config |
| Custom: `x_dsv_pat` | XPress `x-pat` header | Config |

### 5. `stock.move` — Stock Move

**DSV Relevance:** Individual product movements within a picking. Used to build the `packages[]` array with accurate weights/dimensions.

| Odoo Field | DSV Mapping | Direction |
|-----------|-------------|-----------|
| `product_id` → `weight`, dimensions | `packages[].totalWeight/length/width/height` | Odoo → DSV |
| `product_qty` | `packages[].quantity` | Odoo → DSV |
| `picking_id` | Links to parent shipment | Internal |

### 6. `account.move` — Journal Entry / Invoice

**DSV Relevance:** DSV Invoice API returns freight invoices that need reconciliation against Odoo vendor bills. Also linked via `payment.transaction.invoice_ids`.

| Odoo Field | DSV Mapping | Direction |
|-----------|-------------|-----------|
| `ref` | DSV Invoice ID | DSV → Odoo |
| `amount_total` | Invoice amount from DSV | DSV → Odoo |
| `partner_id` | DSV as vendor | DSV → Odoo |
| `invoice_date` | DSV invoice date | DSV → Odoo |
| `currency_id` | Invoice currency | DSV → Odoo |
| Custom: `x_dsv_invoice_id` | DSV Invoice ID for PDF retrieval | DSV → Odoo |
| Custom: `x_dsv_shipment_id` | Cross-reference to shipment | DSV → Odoo |

---

## Supporting Models (Referenced by Core)

These models provide data consumed by the integration but aren't directly written to by DSV.

### 7. `res.partner` — Contact

**DSV Relevance:** All DSV address parties (sender, receiver, delivery, pickup, notify) are constructed from partner records.

| Odoo Field | DSV Mapping |
|-----------|-------------|
| `name` | `parties.*.address.companyName` |
| `street` | `parties.*.address.addressLine1` |
| `street2` | `parties.*.address.addressLine2` |
| `city` | `parties.*.address.city` |
| `zip` | `parties.*.address.zipCode` |
| `country_id.code` | `parties.*.address.countryCode` |
| `state_id.code` | `parties.*.address.state` |
| `email` | `parties.*.contact.email` |
| `phone` / `mobile` | `parties.*.contact.telephone` |
| `vat` | `parties.*.address.eori` (for Air/Sea international) |

### 8. `product.product` / `product.template` — Product

**DSV Relevance:** Product master data drives package construction.

| Odoo Field | DSV Mapping |
|-----------|-------------|
| `weight` | `packages[].totalWeight` (aggregated) |
| `volume` | `packages[].totalVolume` (aggregated) |
| Custom: `x_length` / `x_width` / `x_height` | `packages[].length/width/height` |
| `hs_code` (if using `product_harmonized_system`) | `packages[].harmonizedCode` |
| `default_code` | `packages[].shippingMarks` |
| `name` | `packages[].description` |
| Custom: `x_dangerous_goods` | Triggers DG fields in booking payload |

### 9. `product.packaging` — Product Packaging

**DSV Relevance:** Maps to DSV `packageType` codes.

| Odoo Field | DSV Mapping |
|-----------|-------------|
| `name` | Display name |
| `package_type_id` | Maps to DSV codes: `BAG`, `CAS`, `PLL`, `PARCELS`, etc. |
| `length` / `width` / `height` | Package dimensions |
| `max_weight` | Weight validation |

### 10. `stock.package.type` — Package Type

**DSV Relevance:** Defines package type codes that map to DSV's `packageType` enum.

| Custom Field | DSV Mapping |
|-------------|-------------|
| `x_dsv_package_code` | `packageType` value (`BAG`, `CAS`, `PLL`, etc.) |

### 11. `stock.location` — Inventory Location

**DSV Relevance:** Warehouse/3PL location addresses used as pickup party.

| Odoo Field | DSV Mapping |
|-----------|-------------|
| Linked partner address | `parties.pickup.address` (Mainfreight 3PL addresses) |

### 12. `stock.warehouse` — Warehouse

**DSV Relevance:** Determines pickup location for bookings.

| Odoo Field | DSV Mapping |
|-----------|-------------|
| `partner_id` → address | `parties.sender.address` / `parties.pickup.address` |

### 13. `res.company` — Company

**DSV Relevance:** MML company details for booking party.

| Odoo Field | DSV Mapping |
|-----------|-------------|
| Company address | `parties.bookingParty.address` (overridden by MDM) |
| `currency_id` | `services.insurance.amount.currency`, `units` |

### 14. `account.incoterms` — Incoterms

**DSV Relevance:** Direct mapping to DSV `incoterms.code`. Consumed via `purchase.order.incoterm_id` (inbound freight) — the incoterm determines whether MML arranges the freight leg (EXW/FCA/FOB/FAS → buyer arranges → triggers freight tender) or the supplier does (CIF/DDP/DAP etc. → no tender). The `code` value maps directly into the DSV booking payload.

| Odoo Field | DSV Mapping | Consumed Via |
|-----------|-------------|--------------|
| `code` | `incoterms.code` (EXW, FOB, CIF, etc.) | `purchase.order.incoterm_id` → `freight.tender.incoterm_id` → DSV booking |
| `name` | Display only | — |

### 15. `res.currency` — Currency

**DSV Relevance:** Used in insurance amounts, goods values, invoice reconciliation.

| Odoo Field | DSV Mapping |
|-----------|-------------|
| `name` | `services.insurance.amount.currency`, `goodsValue.currencyCode` |

---

## Optional Models (Enhanced Visibility)

### 16. `stock.landed.cost` — Landed Costs

**DSV Relevance:** DSV freight invoices can be automatically allocated as landed costs against incoming shipments.

| Integration | Purpose |
|------------|---------|
| DSV Invoice API → landed cost lines | Auto-create freight cost allocations |
| Link to `stock.picking` via shipment ID | Associate costs with correct receipt |

### 17. `purchase.order` — Purchase Order

**DSV Relevance:** For inbound freight (supplier → MML), the PO is the primary trigger for freight tendering. `incoterm_id` determines whether MML arranges the freight leg — if buyer-arranges (EXW/FCA/FOB/FAS), a freight tender is created and DSV may be booked. The PO reference is also passed as a customer reference in DSV tracking queries.

| Odoo Field | DSV Mapping |
|-----------|-------------|
| `name` | `references[].value` (type: `CONSIGNEE_REFERENCE`) for tracking lookup |
| `incoterm_id` → `code` | `incoterms.code` in DSV booking payload; also drives `freight_responsibility` computed field (`buyer` → tender triggered, `seller` → no DSV booking) |
| `partner_id` → address | `parties.sender.address` (supplier origin for inbound bookings) |
| `cargo_ready_date` (custom) | `pickupTime.date` for inbound booking |
| `picking_type_id` → `warehouse_id` → address | `parties.delivery.address` (Mainfreight destination) |

### 18. `ir.attachment` — Attachments

**DSV Relevance:** Store DSV labels, PODs, and downloaded documents against pickings/SOs.

| Integration | Purpose |
|------------|---------|
| DSV Label Print API → attachment on `stock.picking` | PDF label storage |
| DSV Document Download API → attachment on `stock.picking` | POD, customs docs |
| DSV Document Upload API ← attachment from `stock.picking` | Commercial invoices, packing lists |

### 19. `stock.picking.type` — Picking Type

**DSV Relevance:** Filter which picking types trigger DSV booking (outgoing only, specific warehouses).

### 20. `ir.cron` — Scheduled Actions

**DSV Relevance:** Automated jobs for the integration.

| Cron Job | Purpose | Suggested Interval |
|----------|---------|-------------------|
| Token refresh | Refresh OAuth token before 10min expiry | Every 8 minutes |
| Tracking sync | Poll tracking API for status updates | Every 30 minutes |
| Invoice sync | Pull new DSV invoices | Daily |
| POD download | Fetch PODs when webhook signals availability | Event-driven (webhook) or hourly |

### 21. `payment.transaction` — Payment Transaction

**DSV Relevance:** Indirect — links `sale_order_ids` to `invoice_ids`. The `payment.transaction` model bridges SO payments to invoices, which is relevant when reconciling DSV freight charges against customer-paid orders. Not directly integrated with DSV API but important for the financial flow.

Key fields from the provided reference:
- `sale_order_ids` (many2many → `sale.order`) — links to originating SOs
- `invoice_ids` (many2many → `account.move`) — links to related invoices
- `state` — transaction lifecycle (draft → pending → authorized → done)
- `amount` / `currency_id` — payment amounts

---

## Custom Fields Summary (New Fields Required)

| Model | Field | Type | Purpose |
|-------|-------|------|---------|
| `sale.order` | `x_dsv_booking_id` | Char | DSV Booking ID |
| `sale.order` | `x_dsv_shipment_id` | Char | DSV Shipment ID |
| `sale.order` | `x_dsv_tracking_status` | Selection | Current tracking status |
| `stock.picking` | `x_dsv_booking_id` | Char | DSV Booking ID |
| `stock.picking` | `x_dsv_label_url` | Char | Label PDF URL |
| `stock.picking` | `x_dsv_pod_available` | Boolean | POD ready flag |
| `account.move` | `x_dsv_invoice_id` | Char | DSV Invoice ID |
| `account.move` | `x_dsv_shipment_id` | Char | Cross-ref to shipment |
| `delivery.carrier` | `x_dsv_product_name` | Selection | Road/Air/Sea/Rail |
| `delivery.carrier` | `x_dsv_subscription_key` | Char | API subscription key |
| `delivery.carrier` | `x_dsv_client_id` | Char | OAuth client ID |
| `delivery.carrier` | `x_dsv_client_secret` | Char | OAuth client secret (encrypted) |
| `delivery.carrier` | `x_dsv_mdm` | Char | MDM account number |
| `delivery.carrier` | `x_dsv_environment` | Selection | demo/production |
| `delivery.carrier` | `x_dsv_service_auth` | Char | XPress auth key |
| `delivery.carrier` | `x_dsv_pat` | Char | XPress personal access token |
| `stock.package.type` | `x_dsv_package_code` | Char | DSV packageType enum value |
| `product.template` | `x_length` | Float | Package length (cm) |
| `product.template` | `x_width` | Float | Package width (cm) |
| `product.template` | `x_height` | Float | Package height (cm) |
| `product.template` | `x_dangerous_goods` | Boolean | DG flag |

---

## Integration Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Odoo 19                           │
│                                                     │
│  sale.order ──→ stock.picking ──→ DSV Booking API   │
│       │              │                │             │
│       │              │         ┌──────┘             │
│       │              │         ▼                    │
│       │              │    DSV Tracking API ──→      │
│       │              │    stock.picking (status)     │
│       │              │                              │
│       │              │    DSV Label API ──→          │
│       │              │    ir.attachment              │
│       │              │                              │
│       │         DSV Document APIs ←→                │
│       │         ir.attachment (POD, docs)            │
│       │                                             │
│  account.move ←── DSV Invoice API                   │
│       │                                             │
│  stock.landed.cost ←── (freight cost allocation)    │
│                                                     │
│  delivery.carrier ── (DSV API credentials & config) │
│                                                     │
│  ir.cron ── (token refresh, tracking sync, etc.)    │
│                                                     │
│  ┌─────────────────────────────────────┐            │
│  │  DSV Webhook Receiver (controller)  │            │
│  │  - Tracking events → stock.picking  │            │
│  │  - Invoice events → account.move    │            │
│  │  - POD available → trigger download │            │
│  └─────────────────────────────────────┘            │
└─────────────────────────────────────────────────────┘
```

---

## Model Count Summary

| Tier | Count | Models |
|------|-------|--------|
| Core (direct DSV R/W) | 6 | `sale.order`, `sale.order.line`, `stock.picking`, `delivery.carrier`, `stock.move`, `account.move` |
| Supporting (data source) | 9 | `res.partner`, `product.product`, `product.template`, `product.packaging`, `stock.package.type`, `stock.location`, `stock.warehouse`, `res.company`, `account.incoterms`, `res.currency` |
| Optional (enhanced) | 6 | `stock.landed.cost`, `purchase.order`, `ir.attachment`, `stock.picking.type`, `ir.cron`, `payment.transaction` |
| **Total** | **21** | |

---

## Notes for Harold

1. **Odoo 19 module structure:** Build as `delivery_dsv` module extending `delivery.carrier` with the custom `delivery_type` options. This is the standard pattern for carrier integrations.
2. **Token management:** Store tokens on `delivery.carrier` with `x_dsv_access_token` and `x_dsv_token_expiry`. Use `ir.cron` for refresh, but also implement lazy refresh on 401 responses.
3. **Webhook endpoint:** Register an Odoo HTTP controller at `/dsv/webhook/<carrier_id>` that validates the incoming auth and dispatches to the appropriate model update.
4. **Package aggregation logic:** The hardest mapping is `sale.order.line` → `packages[]`. Consider whether MML ships per-line or aggregates into cartons. This needs a business rule decision.
5. **Mainfreight 3PL coordination:** The pickup address will typically be the Mainfreight warehouse, not MML's office. Ensure `stock.warehouse.partner_id` points to the correct 3PL address.
6. **Environment switching:** Use `x_dsv_environment` on `delivery.carrier` to flip between demo/production base URLs without code changes.
