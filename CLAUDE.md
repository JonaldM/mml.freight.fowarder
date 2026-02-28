# MML Freight Integration — Project Scope

## What We're Building

A two-layer freight orchestration system for **MML Consumer Products** on **Odoo 19**. The system separates inbound freight forwarding (supplier → MML warehouse) from outbound 3PL fulfilment (MML warehouse → customer).

## Business Context

MML imports ~400 SKUs across 5 brands from multiple international and domestic suppliers. For each Purchase Order where MML controls the freight leg (determined by Incoterm — EXW/FCA/FOB/FAS), the system automatically tenders to multiple freight forwarders, selects the best rate, books the shipment, and tracks it through to warehouse receipt.

Outbound fulfilment is handled by Mainfreight as 3PL. DSV is the primary freight forwarder for both inbound and outbound legs.

---

## Architecture: Two Layers

```
INBOUND (Layer 1 priority)
purchase.order → freight.tender → freight.booking → tpl.inbound.notice → stock.picking

OUTBOUND (Layer 2 — Phase 2)
sale.order → stock.picking → tpl.dispatch.order → Mainfreight → customer
```

---

## Module Structure

| Module | Purpose |
|--------|---------|
| `mml_freight` | Layer 1: Freight Forwarder Orchestrator — tender, quote, booking, tracking |
| `mml_freight_dsv` | DSV carrier adapter (Generic API: Road/Air/Sea/Rail + XPress API) |
| `mml_freight_<carrier>` | Future adapters: K+N, Flexport, etc. |
| `mml_3pl` | Layer 2: 3PL Orchestrator — inbound ASN, outbound dispatch |
| `mml_3pl_mainfreight` | Mainfreight 3PL adapter |

---

## Key Models

### Layer 1 — Freight Orchestration (`mml_freight`)
- `purchase.order` ← extended with `freight_responsibility`, `freight_tender_id`, `cargo_ready_date`, etc.
- `freight.carrier` — carrier registry + configuration
- `freight.tender` — tender request (PO-centric, fans out to carriers)
- `freight.tender.package` — cargo line items for the tender
- `freight.tender.quote` — quote responses from carriers
- `freight.booking` — confirmed booking lifecycle + tracking
- `freight.tracking.event` — normalised tracking events
- `freight.document` — document registry (labels, PODs, customs)

### Layer 2 — 3PL (`mml_3pl`)
- `tpl.provider` — 3PL provider registry
- `tpl.inbound.notice` — Advance Shipment Notice sent to Mainfreight
- `tpl.inbound.notice.line` — line items
- `tpl.dispatch.order` — outbound dispatch (Phase 2)

---

## DSV API Integration

Two separate DSV APIs, both configured per `freight.carrier` record:

| API | Use Case | Auth |
|-----|----------|------|
| **DSV Generic** | Road EU, Air, Sea, Rail — rate quoting + booking | OAuth2 (myDSV credentials) + Subscription Key |
| **DSV XPress** | Courier/Express — booking + tracking | Service Auth + PAT |

DSV webhooks received at `/dsv/webhook/<carrier_id>` for tracking events, POD availability, invoice notifications.

---

## Incoterm → Freight Responsibility

| Incoterm | Who Arranges Freight | MML Tenders? |
|----------|---------------------|--------------|
| EXW, FCA, FOB, FAS | Buyer (MML) | **Yes** — triggers freight tender |
| CFR, CIF, CPT, CIP, DAP, DPU, DDP | Seller | No — supplier handles |

---

## Key Odoo Models Referenced (with docs in `/docs`)

Core to DSV integration: `sale.order`, `sale.order.line`, `stock.picking`, `delivery.carrier`, `stock.move`, `account.move`

Supporting: `res.partner`, `product.product`, `product.template`, `product.packaging`, `stock.package.type`, `stock.location`, `stock.warehouse`, `res.company`, `account.incoterms`, `res.currency`

Optional: `stock.landed.cost`, `purchase.order`, `ir.attachment`, `stock.picking.type`, `ir.cron`, `payment.transaction`

Model field-level docs: `/docs/*.pdf`

---

## Custom Fields Required (across models)

See `DSV/DSV-Odoo-Model-Integration-Map.md` for full list. Key ones:
- `sale.order`: `x_dsv_booking_id`, `x_dsv_shipment_id`, `x_dsv_tracking_status`
- `stock.picking`: `x_dsv_booking_id`, `x_dsv_label_url`, `x_dsv_pod_available`
- `account.move`: `x_dsv_invoice_id`, `x_dsv_shipment_id`
- `delivery.carrier`: DSV credentials, environment, product config
- `product.template`: `x_length`, `x_width`, `x_height`, `x_dangerous_goods`

---

## Implementation Phases

| Phase | Scope |
|-------|-------|
| **Phase 1** | `purchase.order` extensions, `freight.tender/quote/package` models, adapter interface, PO form UI, manual quote entry |
| **Phase 2** | `freight.booking` + tracking events, DSV adapter (booking + tracking), `tpl.inbound.notice` |
| **Phase 3** | DSV quote API integration, auto-tender from PO, selection algorithms, cron jobs |
| **Phase 4** | DSV labels/documents/webhooks, Mainfreight adapter, landed cost integration |
| **Phase 5** | Second carrier adapter (K+N/Flexport), analytics dashboard, reliability scoring |

**Phase 1 is the immediate priority** — validates data model and UI without needing any API integration.

---

## Key Files

| File | Purpose |
|------|---------|
| `MML-Freight-Orchestration-Architecture-v2.md` | Full architecture spec with model definitions and code |
| `DSV/DSV-Odoo-Model-Integration-Map.md` | Odoo ↔ DSV field mapping for all 21 models |
| `DSV/DSV-API-Integration-Guide.md` | DSV API endpoints, auth, payload structures |
| `docs/*.pdf` | Odoo 19 model field-level documentation (field names, types, attributes, app module names) |
| `docs/Mainfreight Warehousing Integration Specification.pdf` | Mainfreight 3PL API spec |

## Related Projects

| Project | Path | Purpose |
|---------|------|---------|
| Mainfreight 3PL Integration | `E:\ClaudeCode\projects\mainfreight.3pl.intergration` | Already-built 3PL platform — `stock_3pl_core` + `stock_3pl_mainfreight` modules live here |

**Critical**: The freight modules (`mml_freight` etc.) do NOT include a 3PL layer. Instead they depend on `stock_3pl_core` from the mainfreight project. When a `freight.booking` is confirmed for an inbound shipment, it creates a `3pl.message` (document_type=`inward_order`) via `stock_3pl_core`'s message queue — `stock_3pl_mainfreight` then handles the actual SFTP push to Mainfreight MIMS.

The `tpl.inbound.notice` and `mml_3pl*` models described in `MML-Freight-Orchestration-Architecture-v2.md` are superseded — use `stock_3pl_core` instead.

---

## Developer Notes

- Build as standard Odoo module extending `delivery.carrier` with custom `delivery_type` options
- Token management: store on `delivery.carrier`, lazy refresh on 401, cron refresh every 8 min
- Webhook endpoint: `/dsv/webhook/<carrier_id>` with auth validation
- Package aggregation (SO line → DSV `packages[]`) requires business rule decision from MML
- Pickup address = Mainfreight warehouse, NOT MML office — ensure `stock.warehouse.partner_id` points to correct 3PL address
- Environment switching via `x_dsv_environment` on `delivery.carrier` (demo/production)
