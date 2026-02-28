# MML Freight Orchestration

Odoo 19 freight forwarding integration for **MML Consumer Products**. Automates inbound freight tendering, carrier selection, booking, and 3PL handoff for Purchase Orders where MML controls the freight leg.

---

## What it does

When a Purchase Order arrives with an EXW, FCA, FOB, or FAS incoterm, MML is responsible for arranging freight from the supplier to the NZ warehouse. This system:

1. Detects freight responsibility from the PO incoterm automatically
2. Creates a **Freight Tender** and fans it out to configured carriers
3. Carriers return quotes (real API or mock); the system ranks by cost, speed, or best-value
4. A quote is selected (manually or auto) and a **Freight Booking** is confirmed
5. On confirmation, an inward order notice is queued to **Mainfreight** via `stock_3pl_core`
6. Tracking events sync from the carrier on a 30-minute cron

---

## Module overview

| Module | Purpose | Install? |
|--------|---------|---------|
| `mml_freight` | Core orchestrator — tender, quote, booking, tracking models + adapter interface | Required |
| `mml_freight_dsv` | DSV Generic (Road/Air/Sea/Rail) and DSV XPress adapters | Required for DSV |
| `mml_freight_knplus` | K+N (Kuehne+Nagel) adapter stub — interface wired, API not implemented | Optional |
| `mml_freight_demo` | Demo carriers, supplier, products, and a ready-to-tender PO for Harold | Dev/staging only |

**External dependency:** `stock_3pl_core` from `E:\ClaudeCode\projects\mainfreight.3pl.intergration\addons\`. Must be installed before `mml_freight`.

---

## Incoterm → freight responsibility

| Incoterm | Who arranges freight | System behaviour |
|----------|---------------------|-----------------|
| EXW, FCA, FOB, FAS | **MML (buyer)** | Freight tab shown on PO; "Request Freight Tender" button active |
| CFR, CIF, CPT, CIP, DAP, DPU, DDP | Seller (supplier) | Freight tab hidden; no tender created |
| None set | N/A | Freight tab hidden |

---

## Installation

### Prerequisites

```
Odoo 19
Python 3.12+
stock_3pl_core installed and active
```

### Install order

```
1. stock_3pl_core          (from mainfreight.3pl.intergration project)
2. mml_freight
3. mml_freight_dsv
4. mml_freight_knplus      (optional)
5. mml_freight_demo        (dev/staging only — do not install in production)
```

Via Odoo CLI:

```bash
odoo-bin -d your_db -i mml_freight,mml_freight_dsv --stop-after-init
```

---

## Carrier configuration

### DSV Generic (Road / Air / Sea / Rail)

Go to **Inventory → Freight → Freight Carriers** and create or edit a DSV carrier:

| Field | Value |
|-------|-------|
| Delivery Type | `DSV Generic` |
| Environment | `Demo (Mock)` for testing, `Production` for live |
| OAuth Client ID | From myDSV portal |
| OAuth Client Secret | From myDSV portal |
| DSV Subscription Key | From Azure API Management |
| DSV MDM Account | Your DSV account number |
| DSV Product | Road / Air / Sea / Rail |
| Webhook Signing Secret | See below |

### DSV XPress (courier)

Same as above with `Delivery Type = DSV XPress`, plus:

| Field | Value |
|-------|-------|
| XPress DSV-Service-Auth | From DSV XPress portal |
| XPress PAT | Personal Access Token |

### Webhook signing secret

Each carrier needs a shared HMAC secret so DSV can sign webhook payloads. Generate one and paste it into **Webhook Signing Secret** on the carrier form:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Register the same secret with DSV when configuring the webhook URL. The webhook endpoint is:

```
POST https://your-odoo.example.com/dsv/webhook/<carrier_id>
Header: X-DSV-Signature: sha256=<hmac_hex>
```

### Eligible lanes

Configure **Eligible Origin Countries** and **Eligible Destination Countries** on each carrier to control which tenders it receives. Leave empty to match all.

---

## Running tests

Requires a full Odoo 19 test database with all dependencies installed:

```bash
odoo-bin --test-enable --stop-after-init \
  -d freight_test \
  -i mml_freight,mml_freight_dsv,mml_freight_demo \
  --test-tags=mml_freight,mml_freight_dsv,mml_freight_demo
```

### Test coverage

| Test file | What it covers |
|-----------|---------------|
| `test_freight_responsibility.py` | All 11 Incoterms 2020 → buyer/seller/na mapping |
| `test_package_aggregation.py` | Weight sum, volume, chargeable weight (CBM×333), DG flag |
| `test_carrier_eligibility.py` | DG exclusion, overweight, country lanes, mode filtering |
| `test_quote_ranking.py` | is_cheapest, is_fastest, rank_by_cost, cost_vs_cheapest_pct |
| `test_auto_select.py` | Cheapest/fastest/manual modes, selection reason |
| `test_tender_lifecycle.py` | State machine, sequence prefix, cancel, error guards |
| `test_3pl_handoff.py` | Graceful no-op without connector; 3pl.message creation when present |
| `test_po_form_fields.py` | Responsibility recomputes, tender count, action_request_freight_tender |
| `test_dsv_auth.py` | Demo short-circuit, token cache, near-expiry refresh, 401/403 handling |
| `test_dsv_mock_adapter.py` | Mock quote values, booking ref prefix, tracking events, live guard |
| `test_cron_jobs.py` | Tracking cron, token refresh cron, cron XML records present |
| `test_demo_install.py` | Demo carriers, partner, product dimensions, demo PO |

---

## Demo mode (Harold's starting point)

Install `mml_freight_demo` to get:

- **DSV Road NZ** carrier — `dsv_generic`, `auto_tender=True`, environment=demo
- **K+N Sea LCL Global** carrier — `knplus` stub, `auto_tender=False`
- **Enduro Pet Pty Ltd** — Australian supplier
- 3 products with freight dimensions (Dog Food 20kg, Cat Food 5kg, Bird Seed 10kg)
- **PO/DEMO/001** — FOB incoterm, cargo ready in 15 days, sea mode preference

In demo mode DSV returns hardcoded mock quotes — no API keys required:

| Service | Mode | Rate (NZD) | Transit |
|---------|------|-----------|---------|
| DSV Road Standard | Road | $1,800 | 5 days |
| DSV Air Express | Air | $6,200 | 2 days |

To go live: set the carrier's **Environment** to `Production` and fill in the API credentials.

---

## Architecture

```
purchase.order
    ↓ action_request_freight_tender()
freight.tender  ──── action_request_quotes() ──── FreightAdapterBase.request_quote()
    │                                                       ↑
    │                                              register_adapter('dsv_generic')
    │                                              DsvMockAdapter / DsvGenericAdapter
    ↓ action_book()
freight.booking ──── action_confirm() ──── 3pl.message (inward_order)
    │                                            ↓
    │                                     stock_3pl_core → Mainfreight SFTP
    ↓ cron / webhook
freight.tracking.event
```

### Key business rules

- **Chargeable weight** = max(actual_kg, CBM × 333)
- **Auto-select modes**: cheapest (lowest NZD rate), fastest (lowest transit days), best_value (0.6×cost_rank + 0.4×reliability)
- **3PL handoff**: graceful no-op if `stock_3pl_core` not installed or no active connector for the PO's warehouse
- **OAuth tokens**: cached on carrier record, refreshed 120s before expiry, cron runs every 8 minutes

### Adding a new carrier adapter

1. Create a new module: `mml_freight_<carrier>/`
2. Inherit `FreightAdapterBase` and implement `request_quote`, `create_booking`, `get_tracking`
3. Decorate with `@register_adapter('your_delivery_type')`
4. Add `'your_delivery_type'` to the `delivery_type` selection via view inheritance
5. Depend on `mml_freight`

```python
from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase
from odoo.addons.mml_freight.models.freight_adapter_registry import register_adapter

@register_adapter('flexport')
class FlexportAdapter(FreightAdapterBase):
    def request_quote(self, tender): ...
    def create_booking(self, tender, quote): ...
    def get_tracking(self, booking): ...
```

---

## Security notes

- All credential fields (`x_dsv_client_secret`, `x_dsv_subscription_key`, etc.) are restricted to `stock.group_stock_manager` and rendered as password fields
- `x_dsv_environment` is manager-only — stock users cannot flip demo → production
- Webhook endpoints validate HMAC-SHA256 signatures before any ORM access; returning identical responses to valid and invalid carrier IDs to prevent enumeration
- Webhook body is not logged; only event type and carrier ID are recorded
- Before implementing `_handle_dsv_tracking_webhook`, verify `booking.carrier_id == carrier` and sanitise all payload string fields before writing

---

## Related projects

| Project | Path | Purpose |
|---------|------|---------|
| Mainfreight 3PL Integration | `E:\ClaudeCode\projects\mainfreight.3pl.intergration` | `stock_3pl_core` + `stock_3pl_mainfreight` — handles SFTP push to Mainfreight MIMS |

---

## Implementation phases

| Phase | Status | Scope |
|-------|--------|-------|
| Phase 1 | **Complete** | Models, UI, mock adapter, 3PL handoff stub, full test suite |
| Phase 2 | Planned | Live DSV Generic API (quote + booking), tracking sync |
| Phase 3 | Planned | Auto-tender from PO on confirm, selection algorithms, DSV webhooks |
| Phase 4 | Planned | DSV labels/PODs, Mainfreight adapter, landed cost integration |
| Phase 5 | Planned | K+N adapter, analytics dashboard, reliability scoring |
