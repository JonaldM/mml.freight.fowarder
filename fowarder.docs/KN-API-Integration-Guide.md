# Kuehne+Nagel API Integration Guide

## Overview

Kuehne+Nagel (K+N) provides API connectivity for freight forwarding operations through two channels: a developer API portal for system-to-system integration, and the myKN platform for quoting, booking, and tracking. Their API suite covers inbound booking (air, road), shipment visibility (status, container, order), document management, and emissions reporting. K+N is the strongest second-carrier candidate for MML's freight orchestration module after DSV.

**Developer Portal:** [https://portal.api.kuehne-nagel.com](https://portal.api.kuehne-nagel.com)  
**Air Freight API Store:** [https://api-airfreight.kuehne-nagel.com](https://api-airfreight.kuehne-nagel.com)  
**myKN Platform:** [https://mykn.kuehne-nagel.com](https://mykn.kuehne-nagel.com)  
**Connectivity Info:** [https://mykn.kuehne-nagel.com/help-center/connectivity](https://mykn.kuehne-nagel.com/help-center/connectivity)

---

## API Product Catalogue

K+N organises their APIs by direction (Inbound to K+N = you send data, Outbound from K+N = you receive data) and transport mode.

### Inbound APIs (Customer → K+N)

| API | Purpose | Transport Modes |
|-----|---------|-----------------|
| **Book Air** | Electronically place airfreight bookings | Air |
| **Book Road** | Electronically place roadfreight bookings | Road |

### Outbound APIs (K+N → Customer)

| API | Purpose | Transport Modes |
|-----|---------|-----------------|
| **Shipment Overview** | High-level overview of all shipments | All |
| **Shipment Status** | Shipment, transport, and status information | All |
| **Container Status** | Container and status information | Sea |
| **Order Status** | Order and order status information | All |
| **Document Search** | Download shipment/booking documents (v1) + upload documents (v2) | All |
| **Emissions** | CO₂ emissions data on delivered shipments | All |
| **Shipment Status (Push)** | Standard interface sending shipment/transport/container/status events including historical events | All |

### myKN Platform (Web + Potential API)

| Feature | Description |
|---------|-------------|
| **Quote** | Enter route and shipment details, receive competitive quotes instantly |
| **Book** | Convert quotes into confirmed bookings with minimal data entry |
| **Track** | Monitor shipment status using your own reference numbers |
| **Explore** | Integration with seaexplorer for ocean shipping options, departures, capacities |
| **Documents** | Access transport and commercial documents online |

> **Note:** myKN's Quote and Book features may be accessible via API for customers with sufficient volume. This needs to be confirmed with your K+N account manager during onboarding. The public connectivity page lists booking APIs for air and road only — sea freight booking API availability is unclear.

---

## Authentication

### API Portal Access

K+N's developer portal requires customer authentication. Access is gated behind an existing K+N customer relationship.

**Onboarding Steps:**

1. Contact your K+N account manager and request API access
2. K+N provisions credentials for the developer portal
3. Subscribe to required API products in the portal
4. Receive API keys/credentials per product

### Expected Authentication Pattern

Based on the portal structure and industry patterns, K+N APIs likely use one of:

| Method | Evidence |
|--------|----------|
| API Key in header | Developer portal has profile/subscription pages similar to Azure API Management |
| OAuth 2.0 | Common for large forwarder APIs; K+N's portal is built on Azure APIM which supports OAuth |

> **Action Required:** Confirm exact authentication method during API onboarding. The public documentation does not expose auth details.

### EDI Alternative

K+N also supports traditional EDI integration:

| Protocol | Use Case |
|----------|----------|
| EDIFACT | Standard document exchange (IFTMIN, IFTSTA, INVOIC) |
| EDI via API | Modern EDI-over-HTTP for real-time exchange |

EDI may be the fallback path if API access is restricted for MML's volume tier.

---

## API Details (Based on Public Information)

### Book Air Inbound — Airfreight Booking

**Direction:** Customer → K+N  
**Purpose:** Electronically place airfreight bookings with K+N.

**Expected Payload (estimated from industry standard):**

```json
{
  "bookingType": "Air",
  "customer": {
    "accountNumber": "MML_KN_ACCOUNT",
    "reference": "PO-2026-00145"
  },
  "shipper": {
    "name": "Supplier Name",
    "address": {
      "street": "123 Factory Road",
      "city": "Shanghai",
      "country": "CN",
      "postalCode": "200000"
    },
    "contact": {
      "name": "Export Dept",
      "phone": "+86 21 5555 1234",
      "email": "export@supplier.cn"
    }
  },
  "consignee": {
    "name": "MML Consumer Products Ltd",
    "address": {
      "street": "c/o Mainfreight Auckland",
      "city": "Auckland",
      "country": "NZ",
      "postalCode": "1061"
    }
  },
  "cargo": {
    "description": "Consumer Electronics - Coffee Machines",
    "pieces": 100,
    "grossWeight": 1500,
    "weightUnit": "KG",
    "volume": 12.5,
    "volumeUnit": "CBM",
    "dangerousGoods": false
  },
  "routing": {
    "origin": "PVG",
    "destination": "AKL",
    "incoterms": "FOB"
  },
  "dates": {
    "cargoReadyDate": "2026-03-15",
    "requestedDeliveryDate": "2026-03-22"
  }
}
```

> **Disclaimer:** This payload is estimated based on industry standards and K+N's public feature descriptions. Actual schema will be provided during API onboarding.

### Book Road Inbound — Road Freight Booking

**Direction:** Customer → K+N  
**Purpose:** Electronically place road freight bookings. Likely relevant for trans-Tasman or domestic NZ/AU legs.

### Shipment Overview — Portfolio View

**Direction:** K+N → Customer  
**Purpose:** High-level overview of all your shipments with K+N. Provides list view with filtering.

**Expected Response Fields:**

| Field | Description |
|-------|-------------|
| Shipment ID | K+N shipment reference |
| Customer Reference | Your PO/SO reference |
| Transport Mode | Air, Sea, Road, Rail |
| Origin / Destination | Port pairs or city pairs |
| Status | Current milestone status |
| ETD / ETA | Estimated departure / arrival |
| Carrier | Underlying carrier (airline, shipping line) |

### Shipment Status — Detailed Tracking

**Direction:** K+N → Customer  
**Purpose:** Detailed shipment, transport, and status event information.

**Expected Response Structure:**

```json
{
  "shipmentId": "KN-2026-SEA-12345",
  "customerReference": "PO-2026-00145",
  "transportMode": "Sea",
  "status": {
    "current": "In Transit",
    "code": "DEP",
    "description": "Departed origin port",
    "timestamp": "2026-03-18T14:00:00Z",
    "location": "Shanghai, CN"
  },
  "routing": {
    "origin": { "port": "CNSHA", "name": "Shanghai" },
    "destination": { "port": "NZAKL", "name": "Auckland" }
  },
  "transport": {
    "vesselName": "COSCO SHIPPING VENUS",
    "voyageNumber": "065E",
    "etd": "2026-03-18T10:00:00Z",
    "eta": "2026-04-01T06:00:00Z"
  },
  "events": [
    {
      "code": "BKD",
      "description": "Booking confirmed",
      "timestamp": "2026-03-10T09:00:00Z",
      "location": "Shanghai, CN"
    },
    {
      "code": "RCS",
      "description": "Received from shipper",
      "timestamp": "2026-03-16T11:00:00Z",
      "location": "Shanghai, CN"
    },
    {
      "code": "DEP",
      "description": "Departed",
      "timestamp": "2026-03-18T14:00:00Z",
      "location": "Shanghai, CN"
    }
  ]
}
```

> **Disclaimer:** Response structure estimated from K+N's public feature descriptions and IATA/DCSA standards. Actual schema via API onboarding.

### Container Status — Container-Level Tracking

**Direction:** K+N → Customer  
**Purpose:** Container-specific status for sea freight. Includes container number, seal number, container type, weight, and per-container event history.

### Order Status — Order-Level Tracking

**Direction:** K+N → Customer  
**Purpose:** Order and order status information. Useful when a single PO generates multiple shipments — provides the PO-level rollup view.

### Document Search — Document Management

**Direction:** Bidirectional  
**Purpose:** Download shipment and booking related documents (v1) and upload documents (v2).

**Expected Document Types:**

| Document | Direction | Use Case |
|----------|-----------|----------|
| House Bill of Lading | Download | Sea freight — proof of shipment |
| Air Waybill | Download | Air freight — proof of shipment |
| Commercial Invoice | Upload/Download | Trade document |
| Packing List | Upload/Download | Cargo details |
| Certificate of Origin | Upload/Download | Trade compliance |
| Customs Declaration | Download | Import clearance |
| Proof of Delivery | Download | Delivery confirmation |
| Arrival Notice | Download | Notification of cargo arrival at destination |

### Emissions — Carbon Reporting

**Direction:** K+N → Customer  
**Purpose:** CO₂ emissions data for delivered shipments. Useful for ESG reporting but not critical for the freight orchestration module.

### Shipment Status Push — Webhook-Style Events

**Direction:** K+N → Customer (push)  
**Purpose:** Standard interface that sends shipment, transport, container, and status events (including historical) for all shipment types. This is the push-based alternative to polling the Shipment Status API.

**Expected Event Types:**

| Event Category | Examples |
|---------------|----------|
| Booking | Booking confirmed, booking amended |
| Pickup | Cargo received from shipper, picked up |
| Transit | Departed origin, arrived transshipment, departed transshipment |
| Arrival | Arrived destination port/airport |
| Customs | Customs clearance initiated, cleared |
| Delivery | Out for delivery, delivered, POD available |
| Exception | Delay, hold, damage reported |

---

## Mapping to MML Freight Orchestration Architecture

### As a Freight Carrier Adapter (`mml_freight_kn`)

K+N fits into Layer 1 of the orchestration architecture as a freight forwarder — competing with DSV in the tender process.

| Architecture Component | K+N API | Notes |
|----------------------|---------|-------|
| `freight.carrier` config | API credentials, account number | Stored on carrier registry |
| `freight.tender` → request quote | **Manual or myKN** | No confirmed public quote API — may need to use myKN web or email. Confirm with K+N rep. |
| `freight.tender.quote` ← quote response | Manual entry or myKN scrape | Until quote API confirmed |
| `freight.booking` → create booking | Book Air / Book Road API | Air and road bookings confirmed via API. Sea booking API status TBC. |
| `freight.booking` ← booking confirmation | Booking response | Returns K+N shipment reference |
| `freight.tracking.event` ← tracking | Shipment Status API or Push | Full event history with milestones |
| `freight.document` ← documents | Document Search API | HBL, AWB, POD, customs docs |
| `freight.document` → upload | Document Search v2 | Commercial invoices, packing lists |
| Invoice reconciliation | **Not confirmed** | K+N may provide invoice data via EDI or portal. No public invoice API documented. |

### Adapter Method Mapping

```python
class KNAdapter(FreightAdapterBase):
    """Kuehne+Nagel freight adapter for mml_freight module."""

    def request_quote(self, tender):
        # STATUS: Manual / TBC
        # K+N quote API not publicly confirmed.
        # Options:
        #   1. myKN web quote (manual entry into freight.tender.quote)
        #   2. Email quote request (manual entry)
        #   3. API quote if available for MML's account tier
        raise NotImplementedError("K+N quote API pending onboarding confirmation")

    def create_booking(self, tender, selected_quote):
        # STATUS: Available for Air and Road
        # Use Book Air Inbound or Book Road Inbound API
        # Sea freight booking API status TBC
        payload = self._build_booking_payload(tender, selected_quote)
        response = self._api_call('POST', '/booking/air', payload)
        return {
            'carrier_booking_id': response['bookingId'],
            'carrier_shipment_id': response.get('shipmentId'),
            'booked_rate': selected_quote.total_rate,
            'state': 'confirmed',
        }

    def get_tracking(self, booking):
        # STATUS: Available — Shipment Status API
        response = self._api_call('GET', f'/shipment/status/{booking.carrier_shipment_id}')
        return self._normalize_events(response['events'])

    def get_documents(self, booking):
        # STATUS: Available — Document Search API v1
        response = self._api_call('GET', f'/documents/search/{booking.carrier_shipment_id}')
        return [self._normalize_document(doc) for doc in response['documents']]

    def upload_document(self, booking, doc_type, file_data):
        # STATUS: Available — Document Search API v2
        payload = {'documentType': doc_type, 'file': file_data}
        return self._api_call('POST', f'/documents/upload/{booking.carrier_shipment_id}', payload)

    def process_webhook(self, payload):
        # STATUS: Available — Shipment Status Push
        return self._normalize_webhook_event(payload)
```

---

## Onboarding Checklist

Before building the `mml_freight_kn` adapter, these items need to be resolved with your K+N account manager:

| # | Item | Status | Priority |
|---|------|--------|----------|
| 1 | Request API portal access | ❌ Not started | **P1** |
| 2 | Confirm authentication method (API key vs OAuth) | ❌ Unknown | **P1** |
| 3 | Confirm sea freight booking API availability | ❌ Unknown | **P1** |
| 4 | Confirm quote API availability (or myKN-only) | ❌ Unknown | **P2** |
| 5 | Confirm invoice API or EDI availability | ❌ Unknown | **P2** |
| 6 | Get API documentation / OpenAPI specs per product | ❌ Pending access | **P1** |
| 7 | Get sandbox / test environment credentials | ❌ Pending access | **P1** |
| 8 | Confirm webhook/push setup process | ❌ Unknown | **P2** |
| 9 | Get K+N account number for MML | ❌ Check with ops | **P1** |
| 10 | Confirm supported trade lanes (NZ ← Asia, NZ ← EU, NZ ← AU) | ❌ Check with ops | **P2** |

---

## Implementation Priority

| Phase | Scope | Dependency |
|-------|-------|------------|
| **Phase 0** | K+N API onboarding — resolve checklist above | K+N account manager |
| **Phase 1** | Tracking integration — Shipment Status API/Push into `freight.tracking.event` | API access + shipment references |
| **Phase 2** | Document integration — Document Search download/upload | API access |
| **Phase 3** | Booking integration — Book Air and Book Road into `freight.booking` | API access + confirmed schemas |
| **Phase 4** | Quote integration — if quote API available, wire into `freight.tender.quote` auto-population | API access + confirmed quote API |

> **Rationale:** Start with tracking because it requires no booking flow changes — you can immediately get visibility on K+N shipments that are booked manually. Booking automation comes later once schemas are confirmed.

---

## Comparison: K+N vs DSV API Maturity

| Capability | DSV | K+N | Notes |
|-----------|-----|-----|-------|
| Public developer portal | ✅ Full self-service | ⚠️ Gated behind customer access | DSV is more accessible |
| Authentication docs | ✅ OAuth 2.0 fully documented | ❌ Not publicly documented | Need K+N onboarding |
| Booking API (Air) | ✅ Generic API | ✅ Book Air Inbound | Both available |
| Booking API (Sea) | ✅ Generic API | ❓ Not confirmed | DSV wins on sea |
| Booking API (Road) | ✅ Generic + XPress | ✅ Book Road Inbound | Both available |
| Quote API | ✅ Generic Quote API | ❓ Not confirmed (myKN only?) | DSV wins on quoting |
| Tracking API | ✅ Multi-method lookup | ✅ Shipment/Container/Order Status | Both strong |
| Tracking webhooks | ✅ Webhook API | ✅ Shipment Status Push | Both available |
| Document management | ✅ Download + Upload | ✅ Document Search v1/v2 | Both available |
| Invoice API | ✅ Full invoice lifecycle | ❓ Not confirmed | DSV wins on invoicing |
| Emissions | ❌ Not listed | ✅ Emissions API | K+N wins on ESG |
| Sandbox environment | ✅ Demo environment | ❓ TBC | Confirm with K+N |

---

## Notes for Implementation

1. **K+N is the strongest second-carrier candidate** — they have a comprehensive API suite covering booking, tracking, documents, and push events. The main gap is public documentation access.

2. **Start the onboarding conversation now** — API access requires account-level provisioning. Lead time is typically 2-4 weeks for enterprise forwarder API onboarding. Don't wait until Phase 3 of the module build.

3. **The adapter pattern handles the uncertainty well** — even if K+N's quote API turns out to be unavailable, the `mml_freight_kn` adapter can implement `request_quote()` as a no-op (returning `state: 'pending'`) while quotes are entered manually. The tender comparison view doesn't care how the quote arrived.

4. **K+N's push interface is valuable** — the Shipment Status Push API sends historical events, meaning you get a full backfill on subscription rather than needing to poll for history. This simplifies the initial sync.

5. **Sea freight booking is the critical unknown** — MML's primary import mode is sea (LCL/FCL from Asia/EU). If K+N's sea booking API isn't available, the adapter is limited to air and road, with sea booked manually. This significantly reduces the auto-tender value for K+N. Prioritise confirming this during onboarding.

6. **myKN as interim solution** — even without full API access, myKN provides web-based quoting and booking. Consider whether a lightweight myKN scraper or manual workflow is acceptable for Phase 1 while API access is being provisioned.

---

## References

- [K+N Developer Portal](https://portal.api.kuehne-nagel.com/devportal/)
- [K+N Air Freight API Store](https://api-airfreight.kuehne-nagel.com/)
- [myKN Connectivity Help Center](https://mykn.kuehne-nagel.com/help-center/connectivity)
- [myKN Platform](https://mykn.kuehne-nagel.com)
- [K+N Digital Solutions](https://www.kuehne-nagel.com/digital-services/mykn)
- [K+N GitHub](https://github.com/kuehne-nagel) (internal tooling only, no public API SDKs)
