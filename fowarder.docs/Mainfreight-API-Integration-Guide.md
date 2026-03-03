# Mainfreight API Integration Guide

## Overview

Mainfreight provides a suite of REST APIs across three product divisions: Transport (domestic road freight), Warehousing (3PL operations), and Tracking (cross-divisional shipment visibility). The Warehousing and Tracking APIs are the primary integration targets for MML — they cover inbound ASN creation, outbound dispatch, stock on hand queries, and shipment tracking across all Mainfreight divisions including Air & Ocean.

**Developer Portal:** [https://developer.mainfreight.com](https://developer.mainfreight.com)  
**Registration:** [Developer Registration Form](https://developer.mainfreight.com/global/en/global-home/registration.aspx)  
**API Terms:** [API Terms of Use](https://developer.mainfreight.com/global/en/global-home/terms-and-conditions.aspx)

---

## API Product Catalogue

### Scope Clarification for MML

| API | Scope | MML Use Case |
|-----|-------|-------------|
| **Warehousing API** | 3PL operations — order dispatch, inward receipt, stock queries. Global (NZ, AU, Americas, EU). | **Primary.** Inward Create = ASN for inbound POs. Order Create = outbound dispatch from SO. Stock on Hand = inventory sync. |
| **Tracking API** | Cross-divisional tracking — Transport, Warehousing, **Air & Ocean**. Global. | **Primary.** Track inbound international freight (A&O housebills, container numbers, master bills) and domestic consignments. |
| **Transport API** | **Domestic road freight only.** Rate quoting (NZ only), shipment create/delete, labels. NZ, AU, Americas, EU. | **Secondary.** Domestic last-mile deliveries only. Not for international freight forwarding. |
| **Subscription API** | Push webhooks — order confirmation, inward confirmation, tracking updates. | **Primary.** Event-driven updates instead of polling. |

> **Important:** Mainfreight's Transport API does NOT cover international Air & Ocean freight forwarding. There is no public API for booking international sea/air freight through Mainfreight A&O. International A&O shipments must be booked via email/phone/Mainchain portal, but CAN be tracked programmatically via the Tracking API.

---

## Authentication

All Mainfreight APIs use a simple API key model. No OAuth flow required.

### API Key Authentication

| Header | Value | Source |
|--------|-------|--------|
| `Authorization` | `Secret {your_api_key}` | Developer Portal registration |
| `Content-Type` | `application/json` or `application/xml` | Per-request |
| `Accept` | `application/json` or `application/xml` | Optional (defaults to JSON) |

**Example:**

```http
GET /tracking/2.0/references?referenceType=InternationalHousebill&referenceValue=MFAO123456 HTTP/1.1
Host: api.mainfreight.com
Authorization: Secret wdnOId93-VXZECxvVRPQEJZNxPB5XZ...
Content-Type: application/json
```

### Environments

| Environment | Base URL |
|-------------|----------|
| **Production** | `https://api.mainfreight.com` |
| **UAT** | `https://apitest.mainfreight.com` |

### Security

- TLS 1.2 only (enforced server-side)
- API key is issued per-registration, scoped to specific API products
- Separate keys for Transport, Warehousing, Tracking, Subscription APIs

---

## API Details

### Warehousing API (v1.1) — 3PL Operations

**Base Path:** `/warehousing/1.1/Customers/`

**Required Query Parameter:** `region={RegionCode}` on all endpoints.

| Region Code | Coverage |
|-------------|----------|
| `NZ` | New Zealand |
| `AU` | Australia |
| `AM` | Americas |
| `EU` | Europe |

#### Inward Create — ASN / Inbound Receipt Notice

**Endpoint:** `POST /warehousing/1.1/Customers/Inward?region={region}`

**MML Use Case:** When a freight booking confirms delivery to Mainfreight warehouse, create an inward order so Mainfreight knows what to expect and can receive against it. Maps to `tpl.inbound.notice` in the orchestration architecture.

**Request Body:**

```json
{
  "Warehouse": "AKL",
  "CustomerCode": "MMLCONS",
  "InwardType": "Purchase Order",
  "References": [
    {
      "ReferenceType": "PurchaseOrder",
      "ReferenceValue": "PO-2026-00145"
    },
    {
      "ReferenceType": "CarrierBookingRef",
      "ReferenceValue": "FLX-2026-12345"
    }
  ],
  "ExpectedDate": "2026-03-30T00:00:00",
  "Carrier": "Flexport",
  "Notes": "500x Dog Food 20kg + 200x Cat Food 5kg. Booking ref FLX-2026-12345.",
  "Lines": [
    {
      "ProductCode": "ENDURO-DOG-20KG",
      "Quantity": 500,
      "UnitOfMeasure": "EA",
      "LotNumber": "",
      "ExpiryDate": ""
    },
    {
      "ProductCode": "ENDURO-CAT-5KG",
      "Quantity": 200,
      "UnitOfMeasure": "EA",
      "LotNumber": "",
      "ExpiryDate": ""
    }
  ]
}
```

**Key Fields:**

| Field | Description | Required |
|-------|-------------|----------|
| `Warehouse` | Mainfreight warehouse code (e.g., `AKL`, `CHC`) | Yes |
| `CustomerCode` | MML's Mainfreight customer code | Yes |
| `InwardType` | Type of inward (`Purchase Order`, `Return`, `Transfer`) | Yes |
| `References[]` | Array of reference type/value pairs | No |
| `ExpectedDate` | Expected arrival date (ISO 8601) | No |
| `Carrier` | Carrier name (free text) | No |
| `Lines[].ProductCode` | SKU code (must exist in Mainfreight system) | Yes |
| `Lines[].Quantity` | Expected quantity | Yes |
| `Lines[].UnitOfMeasure` | UoM code | Yes |
| `Lines[].LotNumber` | Lot/batch number | No |
| `Lines[].ExpiryDate` | Expiry date for lot tracking | No |

#### Inward Update

**Endpoint:** `PUT /warehousing/1.1/Customers/Inward?region={region}`

Updates an existing inward order (e.g., revised quantities, changed ETA).

#### Inward Delete

**Endpoint:** `DELETE /warehousing/1.1/Customers/Inward?region={region}`

Cancels an inward order before receipt.

#### Order Create — Outbound Dispatch

**Endpoint:** `POST /warehousing/1.1/Customers/Order?region={region}`

**MML Use Case:** When an SO is confirmed in Odoo, create an outbound order in Mainfreight for pick/pack/ship. Maps to `tpl.dispatch.order` in the orchestration architecture.

**Request Body:**

```json
{
  "Warehouse": "AKL",
  "CustomerCode": "MMLCONS",
  "OrderType": "Sales Order",
  "References": [
    {
      "ReferenceType": "SalesOrder",
      "ReferenceValue": "SO-2026-00789"
    }
  ],
  "RequiredDate": "2026-03-15T00:00:00",
  "DeliveryAddress": {
    "Name": "Briscoes Distribution Centre",
    "Address1": "123 Logistics Drive",
    "Address2": "",
    "Suburb": "East Tamaki",
    "City": "Auckland",
    "PostCode": "2013",
    "Country": "NZ",
    "ContactName": "Receiving Dock",
    "ContactPhone": "+64 9 555 1234",
    "ContactEmail": "receiving@briscoes.co.nz"
  },
  "DeliveryInstructions": "Dock 3. Call 30min before arrival.",
  "Lines": [
    {
      "ProductCode": "VOL-ESPRESSO-01",
      "Quantity": 48,
      "UnitOfMeasure": "EA"
    },
    {
      "ProductCode": "VOL-LUNGO-02",
      "Quantity": 24,
      "UnitOfMeasure": "EA"
    }
  ]
}
```

#### Order Update / Order Delete

**Endpoints:**
- `PUT /warehousing/1.1/Customers/Order?region={region}`
- `DELETE /warehousing/1.1/Customers/Order?region={region}`

Same pattern as Inward — update quantities/addresses or cancel before picking begins.

#### Stock on Hand

**Endpoint:** `GET /warehousing/1.1/Customers/StockOnHand?region={region}`

**MML Use Case:** Periodic inventory sync — pull current stock levels from Mainfreight into Odoo `stock.quant` to keep on-hand quantities accurate.

**Query Parameters:**

| Parameter | Description |
|-----------|-------------|
| `CustomerCode` | MML's Mainfreight customer code |
| `Warehouse` | Warehouse code (optional — omit for all warehouses) |
| `ProductCode` | Specific SKU (optional — omit for all products) |

**Response includes:** ProductCode, Description, AvailableQuantity, OnHandQuantity, AllocatedQuantity, DamagedQuantity, UnitOfMeasure, LotNumber, ExpiryDate.

---

### Tracking API (v2.0) — Cross-Divisional Visibility

**Base Path:** `/tracking/2.0/`

This is the API that covers international Air & Ocean shipments — even though you can't book A&O via API, you CAN track them.

#### References — Current Status

**Endpoint:** `GET /tracking/2.0/references`

Returns current status, tracking URL, signed-by name, and proof of delivery link.

**Query Parameters:**

| Parameter | Description | Required |
|-----------|-------------|----------|
| `referenceType` | Type of reference to search | Yes |
| `referenceValue` | Reference number | Yes |

**Available Reference Types:**

| Division | Reference Types |
|----------|----------------|
| **Warehousing** | `InboundWarehouseOrder`, `OutboundWarehouseOrder` |
| **Transport (domestic)** | `ConsignmentNumber`, `DomesticHousebill`, `EuropeanShipment` |
| **Air & Ocean** | `ContainerNumber`, `DeclarationNumber`, `InternationalHousebill`, `MasterBillNumber`, `OrderReference`, `OrderNumber`, `ShipmentNumber` |

**Example — Track international shipment by housebill:**

```http
GET /tracking/2.0/references?referenceType=InternationalHousebill&referenceValue=MFAO2026123456 HTTP/1.1
Host: api.mainfreight.com
Authorization: Secret {api_key}
Accept: application/json
```

#### References/Events — Full Event Timeline

**Endpoint:** `GET /tracking/2.0/references/events`

Same parameters as above, but returns full event history timeline. Useful for building tracking event log in `freight.tracking.event`.

**Event Response Codes:** [Full code list available](https://developer.mainfreight.com/global/en/global-home/tracking-api/code-list.aspx) on the developer portal. Covers pickup, in-transit milestones, customs, port arrivals, delivery, POD.

---

### Transport API (v1.0) — Domestic Road Freight

**Base Path:** `/transport/1.0/customer/`

> **Scope limitation:** This API is for domestic road freight movements only. The Rate API is currently NZ-only. Shipment Create covers NZ, AU, Americas, and EU domestic road movements.

#### Rate — Get Freight Quote

**Endpoint:** `POST /transport/1.0/customer/rate?region={region}`

**Currently NZ only.** Returns estimated freight cost for domestic road transport between two NZ locations.

#### Shipment Create

**Endpoint:** `POST /transport/1.0/customer/shipment?region={region}`

Creates a domestic road freight shipment.

**Request Body (simplified):**

```json
{
  "account": {
    "code": "MMLCONS"
  },
  "housebillNumber": "MML-DOM-2026-001",
  "serviceLevel": {
    "code": "STANDARD"
  },
  "transportMode": "ROAD",
  "freightTerms": "SENDER",
  "routingType": "DIRECT",
  "origin": {
    "sender": {
      "name": "Mainfreight Warehouse Auckland",
      "address": {
        "address1": "100 Freight Lane",
        "suburb": "Penrose",
        "city": "Auckland",
        "postCode": "1061",
        "countryCode": "NZ"
      },
      "contact": {
        "name": "MML Dispatch",
        "phone": "+64 9 555 0000"
      }
    },
    "pickupTime": {
      "toDateTime": "2026-03-15T17:00:00"
    }
  },
  "destination": {
    "receiver": {
      "name": "Harvey Norman Hamilton",
      "address": {
        "address1": "456 Retail Road",
        "suburb": "Te Rapa",
        "city": "Hamilton",
        "postCode": "3200",
        "countryCode": "NZ"
      },
      "contact": {
        "name": "Receiving",
        "phone": "+64 7 555 1234"
      }
    },
    "deliveryTime": {
      "toDateTime": "2026-03-17T17:00:00"
    }
  },
  "items": [
    {
      "quantity": 10,
      "description": "Volere Coffee Machines",
      "weight": 150,
      "volume": 2.4,
      "itemType": "GENERAL",
      "packType": "CARTON"
    }
  ]
}
```

**Key Reference Codes:**

| Field | Available Codes |
|-------|----------------|
| `serviceLevel.code` | `STANDARD`, `EXPRESS`, `ECONOMY` (region-dependent) |
| `transportMode` | `ROAD` |
| `freightTerms` | `SENDER`, `RECEIVER`, `OTHER` |
| `routingType` | `DIRECT`, `ECONOMY`, `CHEAPEST` |
| `items[].packType` | `CARTON`, `PALLET`, `SKID`, `CRATE`, `SATCHEL`, etc. |

#### Shipment Label

**Endpoint:** `GET /transport/1.0/customer/shipment/document`

Downloads PDF shipping labels for created shipments.

#### Shipment Delete

**Endpoint:** `DELETE /transport/1.0/customer/shipment?region={region}`

Cancels a shipment before pickup.

---

### Subscription API — Webhooks

**Base Path:** `/subscription/`

Push-based event notifications — removes the need to poll for updates.

#### Available Webhook Types

| Webhook | Trigger | Payload Contains |
|---------|---------|-----------------|
| **Order Confirmation** | Mainfreight confirms outbound order dispatched | Order reference, consignment number, carrier, tracking URL, line-level pick confirmation |
| **Inward Confirmation** | Mainfreight confirms inbound goods received | Inward reference, received quantities per line, discrepancies, lot numbers |
| **Tracking Update** | Status change on any shipment (transport, warehouse, A&O) | Reference, event code, event description, timestamp, location |

#### Webhook Configuration

Webhooks are configured via the developer portal registration, not via API call. You provide:
- **Endpoint URL** — your Odoo controller URL (e.g., `https://odoo.mml.co.nz/mainfreight/webhook`)
- **Message type** — which webhook events to subscribe to

#### Webhook Delivery

- Webhooks require HTTP 200 response to confirm successful delivery
- Non-200 responses trigger retry logic (exponential backoff)
- All webhooks include a standard envelope with metadata + event-specific content

**Webhook Payload Structure:**

```json
{
  "messageType": "InwardConfirmation",
  "messageId": "abc-123-def-456",
  "timestamp": "2026-03-30T14:22:00Z",
  "content": {
    // Event-specific payload
  }
}
```

---

## Mainfreight-Specific Concepts

### Warehouse Codes

Each Mainfreight warehouse has a short code. MML will primarily use:

| Code | Location | Division |
|------|----------|----------|
| `AKL` | Auckland | Warehousing |
| `CHC` | Christchurch | Warehousing |

Confirm exact codes with your Mainfreight account manager — these may be site-specific (e.g., `AKLSTH` for Auckland South).

### Customer Code

Your Mainfreight customer code (e.g., `MMLCONS`) is required on all Warehousing API calls. This is the account identifier linking API requests to your contract and pricing.

### Product Master

Mainfreight maintains their own product master in Mainchain (their WMS). Your SKU codes in the API must match what's set up in Mainchain. New SKUs need to be registered with Mainfreight before they can be received or dispatched via API.

### Mainchain Portal

Mainchain is Mainfreight's customer-facing WMS portal. It provides:
- Manual order/inward entry (fallback when API is down)
- Inventory reporting and analytics
- Document management
- Shipment tracking dashboard

The API mirrors Mainchain functionality — anything you can do in Mainchain, you can do via API.

---

## Mapping to MML Orchestration Architecture

| Architecture Component | Mainfreight API | Endpoint |
|----------------------|----------------|----------|
| `tpl.inbound.notice` → send to 3PL | Warehousing Inward Create | `POST /warehousing/1.1/Customers/Inward` |
| `tpl.inbound.notice` → update | Warehousing Inward Update | `PUT /warehousing/1.1/Customers/Inward` |
| `tpl.inbound.notice` → cancel | Warehousing Inward Delete | `DELETE /warehousing/1.1/Customers/Inward` |
| `tpl.dispatch.order` → send to 3PL | Warehousing Order Create | `POST /warehousing/1.1/Customers/Order` |
| `tpl.dispatch.order` → update | Warehousing Order Update | `PUT /warehousing/1.1/Customers/Order` |
| `tpl.dispatch.order` → cancel | Warehousing Order Delete | `DELETE /warehousing/1.1/Customers/Order` |
| Inventory sync → `stock.quant` | Warehousing Stock on Hand | `GET /warehousing/1.1/Customers/StockOnHand` |
| `freight.tracking.event` (A&O) | Tracking References/Events | `GET /tracking/2.0/references/events` |
| `freight.tracking.event` (domestic) | Tracking References/Events | `GET /tracking/2.0/references/events` |
| Inward receipt confirmation | Webhook: Inward Confirmation | Push to `/mainfreight/webhook` |
| Outbound dispatch confirmation | Webhook: Order Confirmation | Push to `/mainfreight/webhook` |
| Tracking status push | Webhook: Tracking Update | Push to `/mainfreight/webhook` |
| Domestic freight rate | Transport Rate | `POST /transport/1.0/customer/rate` (NZ only) |
| Domestic shipment booking | Transport Shipment Create | `POST /transport/1.0/customer/shipment` |
| Domestic shipping labels | Transport Shipment Label | `GET /transport/1.0/customer/shipment/document` |

---

## Scheduled Jobs (ir.cron)

| Job | Purpose | Interval |
|-----|---------|----------|
| Stock on Hand sync | Pull inventory levels into Odoo `stock.quant` | Every 2 hours |
| Tracking poll (A&O) | Poll tracking for active international bookings | Every 30 minutes |
| Tracking poll (domestic) | Poll tracking for active domestic shipments | Every 30 minutes |
| Inward receipt check | Check for unacknowledged inward confirmations (fallback if webhook fails) | Every 15 minutes |

> With webhooks enabled, polling becomes a fallback safety net rather than the primary update mechanism.

---

## Notes for Implementation

1. **No international freight booking API exists** — Mainfreight A&O bookings must be made via email, phone, or Mainchain portal. The freight orchestration module will handle Mainfreight A&O as a manual-quote carrier in `freight.tender.quote`, with tracking automated via the Tracking API once a booking reference is obtained.

2. **Warehousing API is the highest-ROI integration** — it automates the physical receiving and dispatching that happens on every single PO and SO regardless of which freight carrier brought the goods in. Build this first.

3. **Product master sync is a prerequisite** — before the Warehousing API can work, all ~400 MML SKUs must be registered in Mainfreight's Mainchain system with matching product codes. Confirm with your Mainfreight rep that the SKU list is current.

4. **Region parameter is mandatory** — every Warehousing API call requires `?region=NZ` (or `AU`, `AM`, `EU`). Hardcode `NZ` for MML's primary operations.

5. **Webhook endpoint security** — Mainfreight doesn't document webhook authentication (no HMAC signature or shared secret in the public docs). Clarify with your Mainfreight rep whether webhooks include any verification mechanism, or implement IP whitelisting as a fallback.

6. **UAT environment available** — `apitest.mainfreight.com` provides a sandbox for development. Register separately for UAT API keys.

7. **The Tracking API covers ALL divisions** — a single tracking query with `InternationalHousebill` or `ContainerNumber` reference type gives you A&O visibility. This is the bridge between the freight forwarding layer (where Mainfreight A&O may have been the carrier) and the 3PL layer.

---

## References

- [Developer Portal Home](https://developer.mainfreight.com/global/en/global-home/getting-started.aspx)
- [Transport API](https://developer.mainfreight.com/global/en/global-home/transport-api.aspx)
- [Warehousing API](https://developer.mainfreight.com/global/en/global-home/warehousing-api.aspx)
- [Tracking API](https://developer.mainfreight.com/global/en/global-home/tracking-api.aspx)
- [Subscription API (Webhooks)](https://developer.mainfreight.com/global/en/global-home/subscription-api.aspx)
- [Tracking Event Code List](https://developer.mainfreight.com/global/en/global-home/tracking-api/code-list.aspx)
- [Shipment Reference List](https://developer.mainfreight.com/global/en/global-home/transport-api/shipment-reference-list.aspx)
- [Warehousing Inward Reference Types](https://developer.mainfreight.com/global/en/global-home/warehousing-api/inward-reference-types.aspx)
- [Warehousing Order Reference Types](https://developer.mainfreight.com/global/en/global-home/warehousing-api/order-reference-types.aspx)
- [Webhook Setup](https://developer.mainfreight.com/global/en/global-home/subscription-api/webhooks.aspx)
