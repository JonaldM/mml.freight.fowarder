# DSV API Integration Guide

## Overview

DSV provides a suite of REST APIs for programmatic access to their freight and logistics services. The platform covers Air, Sea, Road (EU), Rail, and XPress transport modes with APIs for booking, tracking, quoting, label printing, document management, invoicing, and real-time webhooks.

**Developer Portal:** [https://developer.dsv.com](https://developer.dsv.com)  
**Support:** developer.support@dsv.com

---

## API Product Catalogue

DSV's APIs are organised into two main product families plus shared services:

### Generic APIs (Air, Sea, Road EU, Rail)

| API | Purpose | Transport Modes |
|-----|---------|-----------------|
| Booking | Submit and validate transport bookings | Air, Sea, Road (EU), Rail |
| Draft Booking | Multi-package staged booking workflow | Air, Sea, Road (EU), Rail |
| Quote | Request freight quotations | Air, Sea, Road (EU), Rail |
| Label Print | Download PDF package labels | Air, Sea, Road, Rail |
| Tracking | Shipment status and event history | Air, Sea, Road (EU), Rail, XPress |
| Document Download | Retrieve shipment documents (POD, invoices, customs, etc.) | Air, Sea, Road (EU), Rail |
| Document Upload | Attach documents to shipments | Air, Sea, Road (EU), Rail |
| Visibility | Live IoT device tracking data | Air, Sea, Rail, XPress |
| Invoice | Retrieve invoice data and PDFs | Air, Sea, Rail |
| Webhook | Push notifications for tracking, invoice, visibility events | Air, Sea, Road (EU), Rail |

### XPress APIs

| API | Purpose |
|-----|---------|
| Rate & Service | Request quotes based on agreed tariff rates and list available services |
| Booking | Submit draft bookings, upload documents, confirm & get labels, cancel |
| Tracking | Shipment details and events by shipment ID, AWB, or carrier tracking ID |

---

## Authentication

DSV uses different authentication schemes depending on the API family.

### Generic APIs: OAuth 2.0 + Subscription Key

OAuth 2.0 is **mandatory** for all Generic APIs. Every request requires two headers:

| Header | Value | Source |
|--------|-------|--------|
| `DSV-Subscription-Key` | API subscription key | Developer Portal → Profile page |
| `Authorization` | `Bearer {access_token}` | OAuth 2.0 token endpoint |

#### OAuth 2.0 Token Flow

**Prerequisites:**
- myDSV portal credentials (username & password)
- Subscription to both the target API product *and* the "DSV Access Token" API product

**Token Endpoints:**

| Environment | URL |
|-------------|-----|
| Demo | `https://api.dsv.com/my-demo/oauth/v1/token` |
| Production | `https://api.dsv.com/my/oauth/v1/token` |

**Step 1 — Obtain Access Token:**

```http
POST /my-demo/oauth/v1/token HTTP/1.1
Host: api.dsv.com
Content-Type: application/x-www-form-urlencoded
DSV-Subscription-Key: {your_subscription_key}

client_id={myDSV_username}&client_secret={myDSV_password}&grant_type=client_credentials
```

Response returns:
- `access_token` — valid for **10 minutes**
- `refresh_token` — valid for **30 days**

**Step 2 — Use Access Token:**

```http
GET /my-demo/tracking/v2/shipments HTTP/1.1
Host: api.dsv.com
DSV-Subscription-Key: {your_subscription_key}
Authorization: Bearer {access_token}
```

**Step 3 — Refresh Token (before access token expires):**

```http
POST /my-demo/oauth/v1/token HTTP/1.1
Host: api.dsv.com
Content-Type: application/x-www-form-urlencoded
DSV-Subscription-Key: {your_subscription_key}

grant_type=refresh_token&refresh_token={your_refresh_token}
```

Returns a new access token and refresh token pair.

> **Note:** When the refresh token expires after 30 days, full re-authentication with username/password is required.

#### Token Management Best Practices

- Store tokens securely — never expose in URLs, logs, or client-side code
- Implement automated token refresh before the 10-minute expiry window
- Handle `401 Unauthorized` responses by triggering a token refresh
- Rotate tokens regularly as a security measure
- Always use HTTPS

### XPress APIs: Triple-Header Authentication

XPress APIs require **three** authentication headers on every request:

| Header | Value | Source |
|--------|-------|--------|
| `DSV-Subscription-Key` | API subscription key | Developer Portal → Profile page |
| `DSV-Service-Auth` | Service authorization key | XPress portal → Profile → API Profile (per-product) |
| `x-pat` | Personal Access Token | XPress portal → Profile → API Profile (shared across products) |

Each header has separate keys for demo and production environments. Two keys (primary/secondary) are allocated per product — use either one.

**XPress Portal URLs:**
- Demo: `https://demo.xpress.dsv.com`
- Production: `https://xpress.dsv.com`

---

## API Details

### Booking API (Generic)

Two core operations:

- **Validate Booking** — dry-run validation without submitting to DSV. Uses the same validation rules as live bookings. Useful for pre-submission checks.
- **Submit Booking** — creates a real transport booking. Controlled by the `autobook` flag:
  - `autobook: true` → booking sent directly to DSV for processing
  - `autobook: false` → booking appears as draft in myDSV portal, requires manual confirmation by an operator
- **Print Labels** — download labels using Booking ID and SSCC ID at any point in the shipment lifecycle

**Critical:** For Booking Party and Freight Payer addresses, use the MDM test account number (provided in subscription confirmation email) with all other address fields left empty:

```json
{
  "parties": {
    "freightPayer": {
      "address": { "mdm": "1234567890" }
    },
    "bookingParty": {
      "address": { "mdm": "1234567890" }
    }
  }
}
```

### Draft Booking API (Generic)

A staged workflow for building bookings incrementally with multiple packages:

1. **Create Draft Booking** — initialise with multiple packages (1 colli per package line)
2. **Add Packages** — append additional packages using the Draft Booking ID
3. **Print Labels** — available at any stage via Booking ID + SSCC ID
4. **Submit Draft** — finalise and convert to a standard DSV booking

> Limitations: Update and removal of individual packages are not supported. Each package line is restricted to 1 colli.

### Quote API (Generic)

Full quotation lifecycle:

- **Submit Quote** — provide shipment details (routing, packages, etc.)
- **Filter Quotes** — search by predefined criteria
- **Get Quote** — retrieve by `quoteRequestId` with current status
- **Get Attachment** — download quote attachments
- **Cancel Quote** — only available in Draft or AwaitingForOptions status

### Tracking API (Generic)

Four lookup methods:

| Method | Key | Returns |
|--------|-----|---------|
| Shipment List | Period, mode, status filters | List with IDs, references, addresses, dates, status |
| By Booking ID | myDSV booking ID (e.g., `40257145990000123456`) | Full detail with events |
| By Shipment ID | DSV shipment ID (e.g., `SCPH1234567`) | Full detail with events |
| By Customer Reference | Your reference (e.g., `PO123456578`) | Full detail with events |

### Label Print API (Generic)

Single operation: download PDF package labels by myDSV booking ID. Works for bookings created via API or via the myDSV portal.

### Document Download API (Generic)

- List documents by Booking ID or Shipment ID
- Download specific documents via URL returned in the listing

Available document types: POD, Commercial Invoice, Goods Documents, Customs Declaration, Dangerous Goods, Packing List, House Bill of Lading. Optional POD-only filter available.

### Document Upload API (Generic)

Upload by Booking ID or Shipment ID. Mandatory `document_type` parameter:

| Code | Document Type |
|------|--------------|
| `CUS` | Customs declaration |
| `GDS` | Other goods document |
| `HAZ` | Dangerous goods document |
| `INV` | Commercial invoice |
| `PKL` | Packing list |

Constraints: max 3MB file size, antimalware scanning applied, uploads are permanent (no delete).

### Invoice API (Generic)

- **Invoice List** — paginated, filterable by country, transport mode, MDM, date ranges
- **Invoice by ID** — single invoice by DSV Invoice ID
- **Invoice by Customer Ref** — lookup by your reference
- **Invoice by Shipment ID** — lookup by DSV shipment ID
- **Invoice PDF** — download invoice PDF by DSV Invoice ID

Default filter is past 30 days. Pagination is included in response with page navigation links.

### Visibility API (Generic)

Retrieves IoT device data readings by DSV Shipment ID. Requires DSV-purchased tracking devices attached to the shipment. Provides real-time transit condition monitoring.

### Webhook (Generic)

**Tracking Webhooks** — push notifications for shipment events:

| Event | Road | Air | Sea | Rail |
|-------|------|-----|-----|------|
| Booking summary | ✓ | ✓ | ✓ | ✓ |
| Estimated pickup | ✓ | ✓ | ✓ | ✓ |
| Estimated delivery | ✓ | ✓ | ✓ | ✓ |
| Est. arrival changed | | ✓ | ✓ | ✓ |
| Est. departure changed | | ✓ | ✓ | ✓ |
| Actual arrival changed | | ✓ | ✓ | ✓ |
| Actual departure changed | | ✓ | ✓ | ✓ |
| Picked up | ✓ | | | |
| Delivered | ✓ | | | |
| Supplier booking | ✓ | ✓ | ✓ | ✓ |
| POD Available | ✓ | | | |

**Invoice/Visibility Webhooks** — subscribe/unsubscribe to events:

- Event types: `Invoice`, `ShipmentDeviceReading`, `ShipmentDeliveryReceipt`
- Requires OAuth token in Authorization header
- Configurable `PushUrl`, optional custom headers

### XPress Booking API

Two booking paths depending on destination:

**Simple Booking** (domestic / intra-Europe) — 2 API calls:
1. Submit Draft Booking
2. Confirm Draft & Download Labels

**Complex Booking** (outside Europe) — 3 API calls:
1. Submit Draft Booking
2. Upload Mandatory Documents (trade-lane dependent: packing lists, DG certs, commercial invoices, export declarations)
3. Confirm Draft & Download Labels

Labels available in ZPL or PDF format. Cancellation available within the carrier-specific cancellation window.

> **Certification Required:** XPress Booking API requires passing certification tests before production access is granted. Tests are executed via the API Validation Portal at `https://api.validation.dsv.com` using the `x-cert-id` header (e.g., `TC1`, `TC2`, etc.).

### XPress Rate & Service API

Request quotes based on agreed tariff rates. Returns available services matching the provided criteria. Does not create bookings.

### XPress Tracking API

Four lookup methods: by XPress Shipment ID, by AWB number, or by Carrier Tracking ID. Returns shipment details, events (pickup, departure, arrival, delivery), ETA, cargo details, and estimated/actual charges.

---

## Environments

| Environment | Generic Portal | XPress Portal | Purpose |
|-------------|---------------|---------------|---------|
| Demo | `https://demo.mydsv.com` | `https://demo.xpress.dsv.com` | Testing & development |
| Production | `https://mydsv.com` | `https://xpress.dsv.com` | Live operations |
| API Validation | — | `https://api.validation.dsv.com` | XPress Booking certification |

---

## Onboarding Workflow

1. **Register** on the [DSV Developer Portal](https://developer.dsv.com/signup)
2. **Subscribe** to the required API products (+ "DSV Access Token" for Generic APIs)
3. **Wait for approval** — DSV will email credentials for demo portals and your test MDM account number
4. **Develop & test** against demo environment using Postman collections or your own client
5. **XPress only:** Pass certification tests via the API Validation Portal
6. **Request go-live** — DSV provides production credentials and keys
7. **Switch** endpoints and authentication keys to production

---

## Common Errors

| Code | Meaning | Resolution |
|------|---------|------------|
| `401 Unauthorized` | Expired or invalid access token | Refresh the token |
| `403 Forbidden` | Invalid credentials or insufficient permissions | Verify credentials and subscription status |
| Invalid Token Format | Malformed Bearer header | Ensure format is `Bearer {token}` with no extra whitespace |
| Expired Refresh Token | Refresh token past 30-day validity | Re-authenticate with username/password |

---

## Postman Collections

DSV provides ready-made Postman collections for all endpoints:

**Generic APIs:**
- [Booking](https://developer.dsv.com/content/DSV%20API%20(Booking%20v2%20-Token).zip)
- [Quote](https://developer.dsv.com/content/DSV%20API%20(Quote%20v1).zip)
- [Labels](https://developer.dsv.com/content/DSV%20API%20(Label%20print%20v1%20-%20Token).zip)
- [Tracking](https://developer.dsv.com/content/DSV%20API%20(Tracking%20v2%20-%20Token).zip)
- [Download](https://developer.dsv.com/content/DSV%20API%20(Download%20v1%20-Token).zip)
- [Upload](https://developer.dsv.com/content/DSV%20API%20(Upload%20v1%20-Token).zip)
- [Invoice](https://developer.dsv.com/content/DSV%20API%20(Invoice%20v1%20-%20Token).zip)
- [Visibility](https://developer.dsv.com/content/DSV%20API%20(Visibility%20v1%20-Token).zip)

**XPress APIs:**
- [Rate & Service](https://developer.dsv.com/content/DSVXPress_Rates_Services_sample.postman_collections.zip)
- [Booking & Labels](https://developer.dsv.com/content/DSVXPress_Booking_samples.postman_collections.zip)
- [Tracking](https://developer.dsv.com/content/DSVXPress%20-%20Tracking.postman_collections.zip)

---

---

## Complete Endpoint Reference (from Postman Collections)

### Base URLs

| Family | Environment | Base URL |
|--------|-------------|----------|
| Generic | Demo | `https://api.dsv.com/my-demo/` |
| Generic | Production | `https://api.dsv.com/my/` |
| Generic (Quote) | Demo | `https://api.dsv.com/qs-demo/` |
| XPress | Demo/Prod | `https://api.dsv.com/xp/` |
| OAuth | Demo | `https://api.dsv.com/my-demo/oauth/v1/token` |
| OAuth | Production | `https://api.dsv.com/my/oauth/v1/token` |

### Generic API Endpoints

```
POST   /oauth/v1/token                                          # Get/refresh access token
POST   /booking/v2/bookings                                     # Submit or validate booking
POST   /booking/v2/bookings                                     # Draft booking (same endpoint, different flow)
POST   /tracking/v2/shipments/list                               # Shipment list (filtered)
GET    /tracking/v2/shipments/tmsId/:tmsId                       # Track by DSV Shipment ID
GET    /tracking/v2/shipments/bookingId/:bookingId               # Track by Booking ID
GET    /tracking/v2/shipments/reference/:reference               # Track by Customer Reference
GET    /printing/v1/labels/:bookingId?printFormat=Portrait1Label  # Download labels (PDF)
GET    /download/v1/shipments/bookingId/:bookingId/documents     # List docs by Booking ID
GET    /download/v1/shipments/tmsId/:tmsId/documents             # List docs by Shipment ID
GET    /download/v1/shipments/reference/:reference/documents     # List docs by Customer Ref
POST   /invoice/v1/invoices                                      # Invoice list (filtered)
GET    /invoice/v1/invoices/:invoiceId                           # Invoice by ID
GET    /invoice/v1/invoices/referenceNumbers/:referenceNumber    # Invoice by Customer Ref
GET    /invoice/v1/invoices/shipments/:shipmentId                # Invoice by Shipment ID
GET    /invoice/v1/invoices/pdf/:invoiceId                       # Download invoice PDF
POST   /invoice/v1/invoices/subscribe                            # Webhook: subscribe invoice
POST   /invoice/v1/invoices/unsubscribe                          # Webhook: unsubscribe invoice
POST   /visibility/v1/shipments/deviceReadings                   # IoT device readings
POST   /visibility/v1/shipments/subscribe                        # Webhook: subscribe visibility
POST   /visibility/v1/shipments/unsubscribe/:subscriptionEvent   # Webhook: unsubscribe visibility
POST   /webhook/v1/subscriptions/subscribe                       # Webhook: subscribe (quotes/tracking)
POST   /webhook/v1/subscriptions/unsubscribe/:subscriptionEvent  # Webhook: unsubscribe
GET    /webhook/v1/subscriptions                                 # List active subscriptions
```

### Quote API Endpoints (base: `qs-demo` / `qs`)

```
POST   /quote/v1/quotes                                          # Submit quote request
POST   /quote/v1/quotes/filter                                   # Filter quote requests
GET    /quote/v1/quotes/:quoteRequestId                          # Get quote details
GET    /quote/v1/quotes/:quoteRequestId/attachments/:attachmentId # Get quote attachment
POST   /quote/v1/quotes/:quoteRequestId/cancel                   # Cancel quote
```

### XPress API Endpoints (base: `xp`)

```
POST   /booking/v2/bookings                                      # Create draft booking
POST   /booking/v2/bookings/uploadDocument/:shipmentId           # Upload documents
GET    /booking/v2/bookings/labels/:shipmentId?labelFormat=PDF   # Confirm & get labels
DELETE /booking/v2/bookings/cancel/:shipmentId                   # Cancel booking
POST   /comparator/v2/compare                                    # Rate & service quote
GET    /tracking/v2/shipments/shipmentId/:shipmentID             # Track by Shipment ID
GET    /tracking/v2/shipments/carrierTrackingNumber/:num         # Track by Carrier Number
GET    /tracking/v2/shipments/awb/:awb                           # Track by AWB
GET    /tracking/v2/shipments/shipmentDetails/:shipmentId        # Full shipment details
```

---

## Sample Request Payloads

### Generic Booking — Road (minimal)

```json
{
  "autobook": true,
  "product": {
    "name": "Road",
    "dropOff": false
  },
  "services": {
    "insurance": {
      "amount": { "value": 100, "currency": "DKK" },
      "category": "STD"
    }
  },
  "incoterms": {
    "code": "EXW",
    "location": "TestCityPickup1"
  },
  "pickupTime": {
    "date": "2021-03-15",
    "start": "08:05:00",
    "end": "10:15:00"
  },
  "deliveryTime": {
    "date": "2021-03-19",
    "start": "13:05:00",
    "end": "14:15:00"
  },
  "pickupInstructions": ["Test Pickup instruction"],
  "deliveryInstructions": ["Test Delivery instruction"],
  "parties": {
    "sender": {
      "address": {
        "companyName": "Test-Sender1",
        "addressId": "Test1",
        "addressLine1": "Test address 1",
        "addressLine2": "Test address 1.2",
        "addressLine3": null,
        "city": "TestCity1",
        "countryCode": "DK",
        "state": null,
        "zipCode": "0000",
        "instructions": null,
        "mdm": null
      },
      "contact": {
        "name": "Test Name",
        "email": "testemail@testemail.com",
        "telephone": "+4512345678"
      }
    },
    "receiver": {
      "address": {
        "companyName": "Test-Receiver1",
        "addressId": "Test2",
        "addressLine1": "Test address 1",
        "city": "TestCity2",
        "countryCode": "US",
        "state": "NY",
        "zipCode": "000000",
        "mdm": null
      },
      "contact": {
        "name": "Test name 1",
        "email": "testemail1@emailtest.com",
        "telephone": "+11234567890"
      }
    },
    "delivery": {
      "address": {
        "companyName": "Test-Delivery1",
        "addressLine1": "Test address delivery 1",
        "city": "TestCityDelivery1",
        "countryCode": "US",
        "state": "NY",
        "zipCode": "00000",
        "mdm": null
      },
      "contact": {
        "name": "TestName",
        "email": "testemail3@emailtest.com",
        "telephone": "+112345678901"
      }
    },
    "pickup": {
      "address": {
        "companyName": "Test-Pickup1",
        "addressLine1": "Test address pickup 1",
        "city": "TestCityPickup1",
        "countryCode": "DK",
        "zipCode": "0000",
        "mdm": null
      },
      "contact": {
        "name": "TestName",
        "email": "testemail2@emailtest.com",
        "telephone": "+451234567890"
      }
    },
    "freightPayer": {
      "address": { "mdm": "***YOUR_MDM***" }
    },
    "bookingParty": {
      "address": { "mdm": "***YOUR_MDM***" }
    }
  },
  "packages": [
    {
      "quantity": 1,
      "packageType": "BAG",
      "totalWeight": 1500,
      "netWeight": 1000,
      "length": 60,
      "height": 80,
      "width": 70,
      "stackable": "STACKABLE",
      "totalVolume": 0.336,
      "palletSpace": null,
      "loadingMeters": 2,
      "description": "Test goods 1",
      "shippingMarks": "Test shipping marks 1"
    }
  ],
  "references": [
    { "value": "TestReference1", "type": "INVOICING_REFERENCE" },
    { "value": "TestReference2", "type": "ORDER_NUMBER" },
    { "value": "TestReference3", "type": "CONSIGNEE_REFERENCE" },
    { "value": "TestReference4", "type": "SHIPPER_REFERENCE" },
    { "value": "TestReference5", "type": "OTHER" }
  ],
  "units": {
    "dimension": "CM",
    "weight": "KG",
    "volume": "M3",
    "loadingSpace": "LM",
    "temperature": "C"
  }
}
```

**Additional fields for Air bookings:** `services.serviceLevel`, `services.airlineExpress`, `detailedGoodsDescription`, `packages[].harmonizedCode`, party `eori` and `approvedShipper` fields, `notify` party.

**Additional fields for Sea bookings:** `product.containerType` (for FCL), `cargoType` ("LCL"/"FCL").

### Generic Tracking — Shipment List Filter

```json
{
  "statuses": ["IN_PROGRESS"],
  "transports": ["ROAD"]
}
```

### Generic Invoice — Filter Request

```json
{
  "countryCode": "",
  "transportMode": "",
  "mdm": "",
  "invoiceStartDateFilter": "2023-10-17",
  "invoiceEndDateFilter": "",
  "invoiceDueDateStartFilter": "",
  "invoiceDueDateEndFilter": "",
  "pageSize": "10"
}
```

### Generic Quote — Submit

```json
{
  "requestedBy": {
    "userId": "test.email@test.com",
    "name": "Test name",
    "firstName": "Test First Name",
    "lastName": "Test Surname",
    "email": "test.email@test.com",
    "language": "en",
    "phone": "+48123456789"
  },
  "requestDate": "2026-05-28",
  "readyForBookDate": "2026-12-06T11:39:28.190Z",
  "bookingParty": {
    "mdm": "YOUR_MDM",
    "mainMdm": "YOUR_MDM",
    "name": "Test Name",
    "address1": "Test Address",
    "country": "GB",
    "zipCode": "00000",
    "city": "City"
  },
  "pickupType": "DSV",
  "from": {
    "name": "Test Pickup",
    "address1": "Test Pickup Address",
    "country": "GB",
    "zipCode": "00000",
    "city": "City"
  },
  "to": {
    "country": "BE",
    "zipCode": "000",
    "city": "City of Delivery"
  },
  "pickupDate": "2026-06-12",
  "deliveryDate": "2026-06-15",
  "cargoType": "LCL",
  "packages": [
    {
      "goodsDescription": "package1",
      "packageType": "PLL",
      "quantity": 11,
      "length": 120,
      "width": 80,
      "height": 100,
      "totalWeight": 10,
      "totalVolume": 10.56,
      "stackable": "NotStackable",
      "loadMeters": 1.9
    }
  ],
  "unitsOfMeasurement": {
    "dimension": "CM",
    "weight": "KG",
    "volume": "M3",
    "temperature": "C"
  },
  "totalWeight": 10,
  "source": "Public"
}
```

### Generic Visibility — Device Readings Filter

```json
{
  "shipmentId": "",
  "measuredAtStartDateTime": "2023-01-01",
  "measuredAtEndDateTime": "2023-09-22",
  "measuredAtPreDefinedRange": ""
}
```

### Webhook Subscribe — Invoice (Basic Auth)

```json
{
  "pushUrl": "https://your-endpoint.com/webhook/dsv-invoice",
  "authenticationData": {
    "type": "basic",
    "username": "webhook-user",
    "password": "webhook-password"
  },
  "headers": [
    { "name": "X-Custom-Header", "value": "value" }
  ]
}
```

### Webhook Subscribe — Quote/Tracking (OAuth)

```json
{
  "subscriptionEvent": "QuoteSubmitted",
  "pushUrl": "https://your-endpoint.com/webhook/dsv-quotes",
  "authenticationData": {
    "type": "oauth",
    "OAuth": {
      "tokenGenerationUrl": "https://your-auth-server.com/token",
      "accessTokenRequest": {
        "headers": [
          { "name": "Header1", "value": "Value1" }
        ],
        "body": {
          "formUrlEncodedValues": [
            { "name": "client_id", "Value": "your_client_id" },
            { "name": "client_secret", "Value": "your_client_secret" },
            { "name": "grant_type", "Value": "client_credentials" }
          ]
        }
      }
    }
  },
  "headers": [
    { "name": "X-Custom-Header", "value": "value" }
  ]
}
```

### XPress Booking — Draft (Export Minimal)

```json
{
  "dsvAccount": 6406789123,
  "pickup": {
    "collectDateFrom": "2020-07-31T08:49:56.244",
    "collectDateTo": "2020-07-31T20:49:56.244",
    "address": {
      "companyName": "DSV A/S",
      "addressLine1": "Hovedgaden 630",
      "zipCode": "2640",
      "city": "Hedehusene",
      "countryCode": "DK",
      "contactName": "Sender contact",
      "contactPhoneNumber": "+45 123456789",
      "contactEmail": "contact.email@sender.com"
    }
  },
  "delivery": {
    "companyName": "delivery company name",
    "addressLine1": "delivery address line 1",
    "zipCode": "08001",
    "city": "Barcelona",
    "countryCode": "ES",
    "contactName": "contact name",
    "contactPhoneNumber": "+35 123456789",
    "contactEmail": "contact.email@receiver.es"
  },
  "serviceOptions": {},
  "packages": [
    {
      "length": 0,
      "width": 0,
      "height": 0,
      "grossWeight": 6.5,
      "stackableTimes": 2
    }
  ],
  "commodities": [
    {
      "originCountryCode": "DK",
      "goodsDescription": "Commodity goods description",
      "goodsValue": {
        "currencyCode": "EUR",
        "monetaryValue": 250
      }
    }
  ]
}
```

### XPress Rate & Service Quote

```json
{
  "dsvAccount": 6406789123,
  "pickupCountryCode": "DK",
  "pickupCity": "Copenhagen",
  "deliveryCountryCode": "FR",
  "deliveryCity": "Paris",
  "serviceOptions": {
    "packageType": "PARCELS",
    "timeOption": "9AM",
    "saturdayDelivery": "false",
    "insurance": {
      "currencyCode": "EUR",
      "monetaryValue": 250
    }
  },
  "dimensionUnit": "CM",
  "weightUnit": "KG",
  "residentialDelivery": false,
  "ddp": false,
  "specialContent": "LITHIUM",
  "packages": [
    {
      "length": 20,
      "width": 20,
      "height": 20,
      "grossWeight": 6.5
    }
  ]
}
```

---

## Known Enums (from Postman Samples)

| Field | Values |
|-------|--------|
| `product.name` | `Road`, `Air`, `Sea`, `Rail` |
| `stackable` | `STACKABLE`, `NO` |
| `packageType` (Generic) | `BAG`, `CAS`, `PLL`, and others |
| `packageType` (XPress) | `PARCELS`, and others |
| `reference.type` | `INVOICING_REFERENCE`, `ORDER_NUMBER`, `CONSIGNEE_REFERENCE`, `SHIPPER_REFERENCE`, `OTHER` |
| `cargoType` | `LCL`, `FCL` |
| `incoterms.code` | `EXW`, and other standard Incoterms |
| `units.dimension` | `CM` |
| `units.weight` | `KG` |
| `units.volume` | `M3` |
| `statuses` (tracking) | `IN_PROGRESS`, and others |
| `transports` (tracking) | `ROAD`, `AIR`, `SEA`, `RAIL` |
| `document_type` (upload) | `CUS`, `GDS`, `HAZ`, `INV`, `PKL` |
| `subscriptionEvent` | `QuoteSubmitted`, `Invoice`, `ShipmentDeviceReading`, `ShipmentDeliveryReceipt` |
| `labelFormat` (XPress) | `PDF`, `ZPL` |
| `printFormat` (Generic) | `Portrait1Label` |
| `specialContent` (XPress) | `LITHIUM`, `DRY_ICE`, `DGR`, `ADR`, `ADR_LQ` |

---

## Notes for Implementation

1. **Upload API collection was auth-walled** — couldn't download the Postman collection. The endpoint is `POST /upload/v1/...` but exact paths need confirming from the portal once subscribed.
2. **No response schemas in Postman** — DSV's Postman collections include request samples only, no example responses. Response schemas will need to be captured from the demo sandbox during development.
3. **Generic vs XPress booking schemas differ significantly** — Generic uses `parties.sender/receiver/pickup/delivery` with nested `address/contact`, while XPress uses flat `pickup.address` and `delivery` objects. The mapping layer needs to handle both.
4. **MDM is critical** — the Master Data Management account number is the key identifier linking your account to DSV. It's required in `freightPayer` and `bookingParty` for Generic, and as `dsvAccount` for XPress.
5. **Token refresh automation** — access tokens expire in 10 minutes, refresh tokens in 30 days. Any production integration needs a token manager that proactively refreshes before expiry.

---

## References

- [Developer Portal Home](https://developer.dsv.com/)
- [Generic API Guide (Air, Sea, Road, Rail)](https://developer.dsv.com/guide-mydsv)
- [XPress API Guide](https://developer.dsv.com/guide-xpress)
- [OAuth 2.0 Guide](https://developer.dsv.com/oauth-guide)
- [Webhook Guide](https://developer.dsv.com/webhook-guide)
- [API FAQ](https://developer.dsv.com/api_faq)
- [Common Errors](https://developer.dsv.com/common-errors)
- [API Catalogue](https://developer.dsv.com/apicatalogue)
