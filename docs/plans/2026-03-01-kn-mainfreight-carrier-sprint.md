# Sprint Plan — K+N & Mainfreight Carrier Adapters

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Build out the K+N (`mml_freight_knplus`) and Mainfreight A&O (`mml_freight_mainfreight`) carrier adapters in the existing `mml_freight` orchestration framework. Both carriers are live operational needs. DSV is already the reference implementation — all new adapters follow the same pattern.

**Current state:**
- `mml_freight_knplus` — stub only. `KnplusAdapter` raises `NotImplementedError` on all 3 methods. Carrier model has 2 fields.
- No Mainfreight carrier adapter exists. The 3PL layer (`stock_3pl_mainfreight`) is a separate project.

**Repo root:** `E:\ClaudeCode\projects\mml.odoo.apps\fowarder.intergration` (referred to as `./`)
**Reference implementation:** `addons/mml_freight_dsv/` — study this before writing any adapter code.

---

## Scope Boundary: This Repo vs 3PL Repo

This sprint is **carrier adapters only** — fitting Mainfreight A&O and K+N into the `freight.tender` / `freight.booking` pipeline. The 3PL layer (Mainfreight Warehousing API for inward receipts and outbound dispatch) is **out of scope** — it lives in `E:\ClaudeCode\projects\mml.odoo.apps\mainfreight.3pl.intergration` and is already handled by `stock_3pl_core` + `stock_3pl_mainfreight`.

| Integration | Lives In | This Sprint? |
|---|---|---|
| K+N quoting + booking + tracking | `mml_freight_knplus` | **Yes** |
| Mainfreight A&O tracking | `mml_freight_mainfreight` | **Yes** |
| Mainfreight Warehousing (Inward/Order/Stock) | `mainfreight.3pl.intergration` | No |
| Mainfreight Transport (domestic road) | TBD (low priority) | No |
| Mainfreight Subscription webhooks (inward/dispatch confirm) | `mainfreight.3pl.intergration` | No |

---

## Architecture: Where These Carriers Fit

Both carriers slot into the existing orchestration flow without any model changes:

```
purchase.order (Incoterm = EXW/FOB/FCA)
    │
    ▼
freight.tender  ──► fan-out to enabled carriers
    │
    ├── DsvMockAdapter      (dsv_generic) — quote API available
    ├── KnplusAdapter       (knplus)      — quote API TBC, manual entry fallback
    └── MainfreightAdapter  (mainfreight) — NO quote/booking API, manual only + tracking
    │
    ▼
freight.tender.quote  ──► ops selects cheapest
    │
    ▼
freight.booking  ──► tracking events via carrier tracking API
```

**Key constraint for Mainfreight A&O:** There is no API for quoting or booking international air/ocean freight with Mainfreight. Quotes and bookings happen via Mainchain portal or email. The adapter's value is entirely in **tracking** — once a booking reference (housebill, container number) is obtained manually, the Tracking API gives full event visibility.

**Key constraint for K+N:** Quote API is unconfirmed (may not exist). Air and Road booking APIs are confirmed. Sea booking API is unconfirmed. Tracking (Shipment Status API + Push) is confirmed. Start with tracking, layer in booking, add quoting if/when the API is confirmed.

---

## Onboarding Prerequisites (Actions Required Before API Work)

### K+N Onboarding Checklist

These items must be resolved with the K+N account manager **before** any live API work begins:

| # | Item | Owner | Priority |
|---|------|-------|----------|
| 1 | Request API portal access at portal.api.kuehne-nagel.com | MML ops | **P0** |
| 2 | Confirm auth method: API key vs OAuth 2.0 | K+N rep | **P0** |
| 3 | Confirm sea freight booking API availability | K+N rep | **P0** |
| 4 | Confirm quote API availability (or myKN web-only) | K+N rep | **P1** |
| 5 | Get sandbox / test environment credentials | K+N rep | **P0** |
| 6 | Get OpenAPI spec per product (booking, tracking, documents) | K+N rep | **P0** |
| 7 | Confirm webhook / push setup process | K+N rep | **P1** |
| 8 | Get MML's K+N account number | MML ops | **P0** |
| 9 | Confirm trade lanes supported (NZ ← Asia, NZ ← EU) | MML ops | **P1** |

> **Lead time:** Enterprise forwarder API onboarding typically takes 2–4 weeks. Start this conversation immediately — don't wait until the code is ready.

### Mainfreight Onboarding Checklist

| # | Item | Owner | Priority |
|---|------|-------|----------|
| 1 | Register at developer.mainfreight.com | MML ops | **P0** |
| 2 | Request API keys for: Tracking, Warehousing, Subscription | MML ops | **P0** |
| 3 | Confirm MML customer code (e.g., `MMLCONS`) | MML ops / MF rep | **P0** |
| 4 | Confirm warehouse codes for AKL, CHC | MF rep | **P0** |
| 5 | Register UAT API keys (apitest.mainfreight.com) | MML ops | **P0** |
| 6 | Clarify webhook auth: HMAC signature or IP whitelist? | MF rep | **P1** |
| 7 | Confirm product master sync: all ~400 SKUs registered in Mainchain? | MML ops / MF rep | **P0** |
| 8 | Configure webhook endpoint URL with MF rep | MML ops | **P1** |

> **Note:** SKU registration in Mainchain is a **prerequisite for the 3PL layer**, not for tracking. Tracking by housebill/container works immediately after API key issuance.

---

## Module: `mml_freight_mainfreight`

### What to Build

New module alongside `mml_freight_dsv` and `mml_freight_knplus`.

```
addons/mml_freight_mainfreight/
├── __manifest__.py
├── __init__.py
├── models/
│   ├── __init__.py
│   └── freight_carrier_mainfreight.py   # delivery.carrier extension
├── adapters/
│   ├── __init__.py
│   ├── mf_auth.py                       # API key helper
│   ├── mf_adapter.py                    # Live adapter (tracking only)
│   └── mf_mock_adapter.py               # Mock/demo adapter (registered)
├── controllers/
│   ├── __init__.py
│   └── mf_webhook.py                    # Subscription API webhook endpoint
├── security/
│   └── ir.model.access.csv
├── views/
│   └── freight_carrier_mainfreight_views.xml
└── tests/
    ├── __init__.py
    ├── test_mf_tracking.py
    └── test_mf_webhook.py
```

### API Authentication

Mainfreight uses a simple API key — no OAuth flow.

```http
Authorization: Secret {api_key}
Content-Type: application/json
```

| Environment | Base URL |
|---|---|
| Production | `https://api.mainfreight.com` |
| UAT | `https://apitest.mainfreight.com` |

### Carrier Model Extension (`freight_carrier_mainfreight.py`)

```python
class FreightCarrierMainfreight(models.Model):
    _inherit = 'delivery.carrier'

    x_mf_api_key         = fields.Char('Mainfreight API Key', groups='stock.group_stock_manager')
    x_mf_customer_code   = fields.Char('Customer Code', help='Mainfreight account code, e.g. MMLCONS')
    x_mf_warehouse_code  = fields.Char('Default Warehouse', default='AKL', help='e.g. AKL, CHC')
    x_mf_environment     = fields.Selection([('uat', 'UAT'), ('production', 'Production')], default='uat')
```

### Adapter: Tracking Methods

The Tracking API covers all Mainfreight divisions including A&O. Reference types relevant to MML inbound:

| Reference Type | Use Case |
|---|---|
| `InternationalHousebill` | Track by housebill number (primary) |
| `ContainerNumber` | Track sea freight container |
| `MasterBillNumber` | Track by master bill |
| `OrderReference` | Track by customer PO reference |

**Endpoints:**
```
GET /tracking/2.0/references?referenceType={type}&referenceValue={value}
GET /tracking/2.0/references/events?referenceType={type}&referenceValue={value}
```

**Event normalization:** Map Mainfreight event codes → `freight.tracking.event.status`:
```python
_MF_EVENT_STATE_MAP = {
    # Codes from: developer.mainfreight.com/tracking-api/code-list
    'BOOKING_CONFIRMED':  'confirmed',
    'CARGO_RECEIVED':     'cargo_ready',
    'DEPARTURE':          'in_transit',
    'PORT_ARRIVAL':       'arrived_port',
    'CUSTOMS_CLEARED':    'customs',
    'DELIVERY':           'delivered',
}
```
> Actual codes come from the Mainfreight developer portal code list. Map these during implementation once portal access is available.

### Adapter: No-op Methods

```python
def request_quote(self, tender):
    # Mainfreight A&O has no quote API.
    # Return empty list — ops enter quotes manually via freight.tender.quote.
    return []

def create_booking(self, tender, quote):
    # No booking API for A&O.
    # Booking is done via Mainchain portal / email.
    # Ops manually enter carrier_booking_id on the booking record.
    raise UserError(
        "Mainfreight A&O bookings must be created manually via Mainchain. "
        "Enter the housebill/booking reference on this record once confirmed."
    )
```

### Webhook Controller

Mainfreight Subscription API sends tracking updates to a configured endpoint.

```
POST /mainfreight/webhook
```

Webhook payload envelope:
```json
{
  "messageType": "TrackingUpdate",
  "messageId": "abc-123",
  "timestamp": "2026-03-30T14:22:00Z",
  "content": { ... }
}
```

Handler routes by `messageType`:
- `TrackingUpdate` → find `freight.booking` by carrier reference → create `freight.tracking.event`
- `InwardConfirmation` → this belongs to the 3PL layer; log and ignore (or forward via `stock_3pl_core`)
- `OrderConfirmation` → same — 3PL layer

### Cron Jobs

```python
# ir.cron: Mainfreight — Poll Tracking (A&O)
# Interval: every 30 minutes
# Active bookings in states: confirmed, cargo_ready, in_transit, arrived_port, customs
```

---

## Sprint Tasks: `mml_freight_mainfreight`

### Task MF-1: Module scaffold + carrier model

**Files:**
- Create: `addons/mml_freight_mainfreight/__manifest__.py`
- Create: `addons/mml_freight_mainfreight/__init__.py`
- Create: `addons/mml_freight_mainfreight/models/__init__.py`
- Create: `addons/mml_freight_mainfreight/models/freight_carrier_mainfreight.py`
- Create: `addons/mml_freight_mainfreight/adapters/__init__.py`
- Create: `addons/mml_freight_mainfreight/security/ir.model.access.csv`
- Create: `addons/mml_freight_mainfreight/views/freight_carrier_mainfreight_views.xml`

**Acceptance criteria:**
- Module installs cleanly alongside `mml_freight`
- `delivery.carrier` records can have `delivery_type = 'mainfreight'`
- All 4 credential fields present in carrier form view
- Environment toggle (UAT / Production) works

---

### Task MF-2: Auth helper + base adapter structure

**Files:**
- Create: `addons/mml_freight_mainfreight/adapters/mf_auth.py`
- Create: `addons/mml_freight_mainfreight/adapters/mf_adapter.py`
- Create: `addons/mml_freight_mainfreight/adapters/mf_mock_adapter.py`
- Create: `addons/mml_freight_mainfreight/tests/__init__.py`
- Create: `addons/mml_freight_mainfreight/tests/test_mf_tracking.py`

**Step 1: Write failing test**

```python
# test_mf_tracking.py
class TestMFTrackingAdapter(TransactionCase):

    def test_request_quote_returns_empty(self):
        """Mainfreight has no quote API — adapter returns []."""
        carrier = self.env['delivery.carrier'].create({...})
        adapter = MainfreightAdapter(carrier, self.env)
        result = adapter.request_quote(None)
        self.assertEqual(result, [])

    def test_create_booking_raises_user_error(self):
        """Mainfreight A&O booking is manual — adapter raises UserError."""
        carrier = self.env['delivery.carrier'].create({...})
        adapter = MainfreightAdapter(carrier, self.env)
        with self.assertRaises(UserError):
            adapter.create_booking(None, None)

    def test_get_tracking_normalises_events(self):
        """Tracking events are normalised to standard freight.tracking.event format."""
        carrier = self.env['delivery.carrier'].create({...})
        adapter = MainfreightAdapter(carrier, self.env)
        mock_response = {
            'events': [
                {'code': 'DEPARTURE', 'description': 'Departed Shanghai',
                 'timestamp': '2026-03-18T14:00:00Z', 'location': 'Shanghai, CN'}
            ]
        }
        with patch('requests.get') as mock_get:
            mock_get.return_value.json.return_value = mock_response
            mock_get.return_value.status_code = 200
            booking = self.env['freight.booking'].create({...})
            events = adapter.get_tracking(booking)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['status'], 'in_transit')
        self.assertEqual(events[0]['location'], 'Shanghai, CN')
```

**Step 2: Implement** `mf_auth.py` (simple header builder, no OAuth):

```python
MF_PROD_URL = 'https://api.mainfreight.com'
MF_UAT_URL  = 'https://apitest.mainfreight.com'

def get_base_url(carrier):
    return MF_PROD_URL if carrier.x_mf_environment == 'production' else MF_UAT_URL

def get_headers(carrier):
    return {
        'Authorization': f'Secret {carrier.x_mf_api_key or ""}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
```

**Step 3: Implement** `mf_adapter.py` — live adapter (not registered directly):
- `request_quote()` → return `[]`
- `create_booking()` → raise `UserError` with Mainchain guidance
- `get_tracking(booking)` → `GET /tracking/2.0/references/events` with `referenceType=InternationalHousebill`, normalise to event dicts

**Step 4: Implement** `mf_mock_adapter.py` — registered as `'mainfreight'`:
- In demo mode: return canned tracking events
- In production mode: delegate to `MFAdapter`

---

### Task MF-3: Webhook controller

**Files:**
- Create: `addons/mml_freight_mainfreight/controllers/__init__.py`
- Create: `addons/mml_freight_mainfreight/controllers/mf_webhook.py`
- Create: `addons/mml_freight_mainfreight/tests/test_mf_webhook.py`

**Webhook endpoint:** `POST /mainfreight/webhook`

**Auth:** No HMAC documented publicly. Validate `Authorization: Secret {api_key}` header against stored key (or implement IP whitelist until clarified with Mainfreight rep).

**Route logic:**
```
messageType == 'TrackingUpdate'   → update freight.booking tracking events
messageType == 'InwardConfirmation' → log + ignore (3PL layer handles)
messageType == 'OrderConfirmation'  → log + ignore (3PL layer handles)
```

**Acceptance criteria:**
- HTTP 200 returned on all known messageTypes
- TrackingUpdate creates `freight.tracking.event` on correct booking
- Unknown messageTypes return 200 (don't break retry logic) but log warning
- Duplicate events (same messageId) are idempotent

---

### Task MF-4: Cron job — Tracking poll

**Files:**
- Modify: `addons/mml_freight_mainfreight/__manifest__.py` (add data/cron.xml)
- Create: `addons/mml_freight_mainfreight/data/cron.xml`
- Modify: `addons/mml_freight_mainfreight/models/freight_carrier_mainfreight.py`

**Cron:** Every 30 minutes, for all `freight.booking` records where `carrier_id.delivery_type == 'mainfreight'` and `state in ('confirmed', 'cargo_ready', 'in_transit', 'arrived_port', 'customs')`:
- Call `get_tracking(booking)`
- Upsert `freight.tracking.event` records (no duplicates)
- Advance `booking.state` based on latest event

**Acceptance criteria:**
- Cron fires without error against mock adapter
- Delivered state set correctly on final event
- Does not re-process already-delivered bookings

---

## Module: `mml_freight_knplus` (Expand from Stub)

### Current State

`mml_freight_knplus` has:
- `KnplusAdapter` — raises `NotImplementedError` on all 3 methods (correct)
- `FreightCarrierKnplus` — only `x_knplus_client_id` and `x_knplus_environment`
- No webhook controller, no mock adapter, no cron

### What to Build (Phases)

| Phase | Scope | API Access Required? |
|---|---|---|
| **KN-A** | Credential model expansion + mock adapter + carrier view | No |
| **KN-B** | Tracking adapter (Shipment Status API + polling cron) | Yes |
| **KN-C** | Document adapter (Document Search v1 download + v2 upload) | Yes |
| **KN-D** | Booking adapter (Book Air + Book Road) | Yes (sea TBC) |
| **KN-E** | Quote adapter (if API confirmed available) | Yes + K+N confirmation |

> **Phase KN-A is buildable immediately.** Phases KN-B through KN-E are blocked on K+N API onboarding (see checklist above).

### Expected Final Structure

```
addons/mml_freight_knplus/
├── __manifest__.py              (update)
├── __init__.py
├── models/
│   ├── __init__.py
│   └── freight_carrier_knplus.py   (expand)
├── adapters/
│   ├── __init__.py
│   ├── kn_auth.py               (create — auth helper, TBC method)
│   ├── kn_adapter.py            (create — live adapter, not registered)
│   └── kn_mock_adapter.py       (create — registered as 'knplus', delegates)
├── controllers/
│   ├── __init__.py
│   └── kn_webhook.py            (create — Shipment Status Push endpoint)
├── security/
│   └── ir.model.access.csv
├── views/
│   └── freight_carrier_knplus_views.xml  (expand)
└── tests/
    ├── __init__.py
    ├── test_kn_adapter.py
    └── test_kn_webhook.py
```

### API Authentication

**Status: Unknown until K+N onboarding.** Two likely patterns based on portal architecture (Azure API Management):

**Pattern A: API Key**
```http
Ocp-Apim-Subscription-Key: {subscription_key}
Content-Type: application/json
```

**Pattern B: OAuth 2.0** (similar to DSV Generic)
```http
Authorization: Bearer {access_token}
Ocp-Apim-Subscription-Key: {subscription_key}
Content-Type: application/json
```

The `kn_auth.py` module must handle both. Implement OAuth2 (like `dsv_auth.py`) as the likely path; add API key fallback based on confirmed auth method.

### Carrier Model Expansion (`freight_carrier_knplus.py`)

```python
class FreightCarrierKnplus(models.Model):
    _inherit = 'delivery.carrier'

    # Auth — exact fields TBC based on confirmed auth method
    x_knplus_client_id      = fields.Char('K+N Client ID', groups='stock.group_stock_manager')
    x_knplus_client_secret  = fields.Char('K+N Client Secret', groups='stock.group_stock_manager')
    x_knplus_api_key        = fields.Char('K+N API Key / Subscription Key', groups='stock.group_stock_manager')
    x_knplus_account_number = fields.Char('K+N Account Number', help='Account number issued by K+N')
    x_knplus_environment    = fields.Selection([('sandbox', 'Sandbox'), ('production', 'Production')], default='sandbox')
    x_knplus_token          = fields.Char('Access Token (cached)', groups='stock.group_stock_manager')
    x_knplus_token_expiry   = fields.Datetime('Token Expiry')
    x_knplus_quote_mode     = fields.Selection(
        [('api', 'API (if available)'), ('manual', 'Manual entry / myKN')],
        default='manual',
        help='K+N quote API availability not confirmed. Default to manual until onboarding confirms.'
    )
```

---

## Sprint Tasks: `mml_freight_knplus`

### Task KN-A1: Credential model expansion + carrier view

**Dependency:** None (buildable now)

**Files:**
- Modify: `addons/mml_freight_knplus/models/freight_carrier_knplus.py`
- Modify: `addons/mml_freight_knplus/views/freight_carrier_knplus_views.xml`
- Modify: `addons/mml_freight_knplus/__manifest__.py` (add security)
- Create: `addons/mml_freight_knplus/security/ir.model.access.csv`

**Acceptance criteria:**
- All credential fields defined and saved
- Carrier form shows K+N tab with all fields, secrets masked
- `x_knplus_quote_mode` defaults to `manual`
- Existing `KnplusAdapter` continues to raise NotImplementedError (no regression)

---

### Task KN-A2: Mock adapter + adapter registration fix

**Dependency:** None (buildable now)

**Context:** Currently `KnplusAdapter` is registered as `'knplus'` and raises NotImplementedError. This pattern is wrong — demo environments shouldn't blow up. Follow the DSV pattern: a `KnMockAdapter` registers as `'knplus'` and returns canned data in demo mode.

**Files:**
- Create: `addons/mml_freight_knplus/adapters/kn_mock_adapter.py`
- Modify: `addons/mml_freight_knplus/adapters/knplus_adapter.py` (rename to `kn_adapter.py` — unregistered live adapter)
- Create: `addons/mml_freight_knplus/tests/test_kn_adapter.py`

**Step 1: Write failing tests**

```python
class TestKNMockAdapter(TransactionCase):

    def test_request_quote_returns_empty_in_manual_mode(self):
        """When quote_mode=manual, adapter returns [] — ops enter quotes manually."""
        carrier = self.env['delivery.carrier'].create({
            'name': 'K+N Test', 'delivery_type': 'knplus',
            'x_knplus_quote_mode': 'manual', 'x_knplus_environment': 'sandbox',
        })
        adapter = KnMockAdapter(carrier, self.env)
        result = adapter.request_quote(None)
        self.assertEqual(result, [])

    def test_get_tracking_returns_canned_events_in_demo(self):
        """Mock adapter returns at least one canned tracking event in demo mode."""
        carrier = self.env['delivery.carrier'].create({
            'name': 'K+N Test', 'delivery_type': 'knplus',
            'x_knplus_environment': 'sandbox',
        })
        adapter = KnMockAdapter(carrier, self.env)
        booking = self.env['freight.booking'].create({...})
        events = adapter.get_tracking(booking)
        self.assertIsInstance(events, list)
        self.assertGreater(len(events), 0)
        self.assertIn('status', events[0])
```

**Step 2: Implement** `kn_mock_adapter.py`:
- Registered as `'knplus'`
- In sandbox mode: return canned quote (if `quote_mode == 'api'`) or `[]` (if `manual`)
- In sandbox mode: return canned tracking events
- In production mode: delegate to `KnAdapter` (live, not registered)
- `create_booking()` in sandbox: return a fake `carrier_booking_id`

**Step 3: Move** live logic from `knplus_adapter.py` → `kn_adapter.py`:
- `KnAdapter` — not registered, used by `KnMockAdapter` in production mode
- All methods raise `NotImplementedError` with `# TODO: implement after K+N onboarding` comments

---

### Task KN-A3: Webhook controller stub

**Dependency:** None (buildable now — no API access required for controller structure)

**Files:**
- Create: `addons/mml_freight_knplus/controllers/__init__.py`
- Create: `addons/mml_freight_knplus/controllers/kn_webhook.py`
- Create: `addons/mml_freight_knplus/tests/test_kn_webhook.py`

**Endpoint:** `POST /knplus/webhook/<int:carrier_id>`

Following same pattern as `addons/mml_freight_dsv/controllers/dsv_webhook.py`.

**Note on auth:** K+N's Shipment Status Push auth method is unconfirmed. Stub the auth validation with a `# TODO: implement webhook auth after K+N onboarding` comment. Accept all requests in UAT mode.

**Acceptance criteria:**
- Controller registers without error
- Returns 200 for any JSON payload (stub)
- Logs payload for debugging
- Test exercises the route with a mock K+N tracking push payload

---

### Task KN-B: Tracking adapter — Shipment Status API + Push

**Dependency:** K+N API access (onboarding required)

**Files:**
- Create: `addons/mml_freight_knplus/adapters/kn_auth.py`
- Modify: `addons/mml_freight_knplus/adapters/kn_adapter.py`
- Modify: `addons/mml_freight_knplus/controllers/kn_webhook.py`
- Create: `addons/mml_freight_knplus/data/cron.xml`

**API endpoints:**
```
GET /shipment/status/{shipment_id}         # Polling — Shipment Status API
POST <configured_endpoint>                 # Push — Shipment Status Push (K+N → MML)
```

**Event normalization:** Map K+N event codes to `freight.tracking.event.status`:
```python
_KN_EVENT_STATE_MAP = {
    'BKD': 'confirmed',      # Booking confirmed
    'RCS': 'cargo_ready',    # Received from shipper
    'DEP': 'in_transit',     # Departed
    'ARR': 'arrived_port',   # Arrived destination
    'CCL': 'customs',        # Customs cleared
    'DLV': 'delivered',      # Delivered
}
```
> Exact codes come from K+N API documentation during onboarding.

**Notes:**
- K+N's push sends **historical events** on subscription — full backfill on first push. Store all events, deduplicate on `(booking_id, carrier_event_code, event_date)`.
- Polling via Shipment Status API is the fallback when push is not configured.

---

### Task KN-C: Document adapter

**Dependency:** K+N API access

**API endpoints:**
```
GET /documents/search/{shipment_id}         # List documents (v1)
GET {downloadUrl}                           # Download individual document (v1)
POST /documents/upload/{shipment_id}        # Upload document (v2)
```

**Document types to handle:**

| K+N Doc Type | `freight.document.doc_type` |
|---|---|
| House Bill of Lading | `other` (HBL) |
| Air Waybill | `other` (AWB) |
| Commercial Invoice | `invoice` |
| Packing List | `other` |
| Customs Declaration | `customs` |
| Proof of Delivery | `pod` |

**Implement:**
- `get_documents(booking)` → list + download docs → return `[{doc_type, bytes, filename, carrier_doc_ref}]`
- `upload_document(booking, doc_type, file_data)` → upload to K+N (commercial invoice, packing list for customs)

---

### Task KN-D: Booking adapter

**Dependency:** K+N API access + confirmed schemas

**API endpoints:**
```
POST /booking/air     # Book Air Inbound
POST /booking/road    # Book Road Inbound
# Sea booking: TBC — confirm with K+N rep during onboarding
```

**Payload building** (see K+N API guide Section "Book Air Inbound" for structure):
- `customer.accountNumber` → `carrier.x_knplus_account_number`
- `customer.reference` → `tender.po_ids[0].name` (primary PO reference)
- `shipper` → from `tender.partner_id` (supplier)
- `consignee` → Mainfreight AKL warehouse address
- `cargo` → from `freight.tender.package` lines (CBM, weight, pieces, description)
- `routing.incoterms` → `tender.po_ids[0].incoterm_id.code`
- `dates.cargoReadyDate` → `tender.cargo_ready_date`

**Sea freight fallback:** If sea booking API is unavailable, `create_booking()` for `transport_mode in ('sea_lcl', 'sea_fcl')` raises `UserError` directing ops to book via myKN portal.

---

### Task KN-E: Quote adapter (conditional)

**Dependency:** K+N API access + K+N confirmation that quote API exists

> This task is **conditional** — only implement if K+N confirms a quote API is available for MML's volume tier. If not available, `request_quote()` returns `[]` permanently and quotes are entered manually.

**If quote API is available:**
- Set `x_knplus_quote_mode = 'api'`
- Implement `request_quote(tender)` using confirmed API endpoint
- Map response to standard quote dict format (same as DSV)

**If myKN web-only:**
- `request_quote()` returns `[]` (no change needed)
- Document in carrier form: "K+N quotes must be entered manually via myKN: mykn.kuehne-nagel.com"

---

## Testing Strategy

Follow the DSV testing pattern exactly:

| Test file | Scope |
|---|---|
| `test_mf_tracking.py` | Tracking normalisation, no-op quote/booking |
| `test_mf_webhook.py` | Webhook payload parsing, idempotency, 200 returns |
| `test_kn_adapter.py` | Mock adapter quote/booking/tracking |
| `test_kn_webhook.py` | Webhook controller routing |

All tests must run with `--test-enable` using the mock adapter only. No live API calls in CI. Live integration testing uses the UAT environments.

---

## Delivery Order

```
Week 1 (no API access required):
  [KN-A1]  K+N credential model + view
  [KN-A2]  K+N mock adapter + adapter registration fix
  [KN-A3]  K+N webhook controller stub
  [MF-1]   Mainfreight module scaffold + carrier model
  [MF-2]   Mainfreight auth helper + tracking adapter
  [MF-3]   Mainfreight webhook controller

Week 2 (no API access required):
  [MF-4]   Mainfreight tracking cron
         → Both carriers usable in manual/mock mode. Ops can enter K+N
           quotes manually. Mainfreight A&O tracking works once housebill
           is manually entered on booking.

Week 3–4 (requires K+N sandbox credentials):
  [KN-B]   K+N tracking adapter (live)
  [KN-C]   K+N document adapter

Week 5–6 (requires K+N confirmed booking schemas):
  [KN-D]   K+N booking adapter
  [KN-E]   K+N quote adapter (if available)
```

---

## Open Questions

| # | Question | Blocks |
|---|----------|--------|
| 1 | K+N auth method (API key or OAuth)? | KN-B auth implementation |
| 2 | K+N sea booking API available? | KN-D sea mode |
| 3 | K+N quote API available for MML tier? | KN-E |
| 4 | K+N webhook auth method? | KN-A3 auth implementation |
| 5 | Mainfreight webhook auth (HMAC or IP whitelist)? | MF-3 auth |
| 6 | Mainfreight exact event codes? | MF-2 normalisation map |
| 7 | All 400 MML SKUs registered in Mainchain? | 3PL layer (not this sprint) |
| 8 | MML customer code / warehouse codes confirmed? | MF-1 carrier model defaults |

---

## References

- DSV reference implementation: `addons/mml_freight_dsv/`
- Base adapter contract: `addons/mml_freight/adapters/base_adapter.py`
- Adapter registry: `addons/mml_freight/models/freight_adapter_registry.py`
- K+N API guide: `fowarder.docs/KN-API-Integration-Guide.md`
- Mainfreight API guide: `fowarder.docs/Mainfreight-API-Integration-Guide.md`
- K+N Developer Portal: https://portal.api.kuehne-nagel.com
- Mainfreight Developer Portal: https://developer.mainfreight.com
