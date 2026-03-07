# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> Read the parent `mml.odoo.apps/CLAUDE.md` first — it defines the overall repo structure, company context, and Odoo conventions. This file covers the freight integration workspace specifically.

---

## Running Tests

All tests in this workspace are pure-Python (no live Odoo instance needed). Run from the workspace root:

```bash
# All tests in this workspace
pytest addons/ -q

# Single module
pytest addons/mml_freight/ -q
pytest addons/mml_freight_dsv/ -q

# Single test file
pytest addons/mml_freight/tests/test_tender_lifecycle.py -q

# Single test by name
pytest addons/mml_freight/tests/test_tender_lifecycle.py::TestTenderLifecycle::test_name -q
```

The `conftest.py` at the workspace root wires up Odoo stubs, registers `mml_freight` and `mml_freight_dsv` into `odoo.addons.*`, and provides a `psycopg2` stub. Tests in `mml_freight_knplus` and `mml_freight_mainfreight` may need their own conftest wiring if added.

---

## Module Map

| Module | `delivery_type` key | Status |
|--------|--------------------|----|
| `mml_freight` | — (core orchestrator) | Active |
| `mml_freight_dsv` | `dsv_generic`, `dsv_xpress` | Active |
| `mml_freight_knplus` | `kn_generic` | Scaffold |
| `mml_freight_mainfreight` | `mainfreight` | Scaffold |
| `mml_freight_demo` | — | Disabled (`installable=False`) |

---

## Architecture

### Freight Lifecycle

```
purchase.order (incoterm EXW/FCA/FOB/FAS)
  → freight.tender  (fans out to carriers via FreightAdapterRegistry)
  → freight.tender.quote  (one per carrier per service)
  → freight.booking  (confirmed booking, tracks state through delivery)
  → 3pl.message  (queued to stock_3pl_core when booking confirmed)
  → stock.landed.cost  (created from actual_rate after delivery)
```

### Adapter Pattern

Each carrier module registers adapters using the `@register_adapter('delivery_type')` decorator from `mml_freight/models/freight_adapter_registry.py`. `FreightAdapterRegistry` (abstract model) resolves adapters at runtime via `carrier.delivery_type`.

`FreightAdapterBase` (`mml_freight/adapters/base_adapter.py`) defines the interface:
- `request_quote(tender)` → list of quote dicts
- `create_booking(tender, quote)` → booking ref dict
- `get_tracking(booking)` → list of event dicts
- Optional: `get_label`, `get_documents`, `get_invoice`, `upload_document`, `cancel_booking`, `confirm_booking`, `handle_webhook`

Quote dicts must include specific keys — see `base_adapter.py` docstrings for the full contract.

### DSV Adapters (`mml_freight_dsv`)

Two adapters:
- `DsvGenericAdapter` (`dsv_generic`) — OAuth2 + Subscription Key; handles Road/Air/Sea/Rail; reads `x_dsv_environment` on `delivery.carrier` to switch demo/production endpoints
- `DsvXpressAdapter` (`dsv_xpress`) — stub only; raises `NotImplementedError`

DSV URL validation: all URLs from DSV API responses are checked against `_ALLOWED_DSV_DOMAINS` allowlist before any HTTP request (`_validate_dsv_url` in `dsv_generic_adapter.py`).

DSV auth token management is in `dsv_auth.py` — lazy refresh on 401, tokens stored on `delivery.carrier`.

### Webhook Security

`/dsv/webhook/<carrier_id>` validates HMAC-SHA256 before any ORM access. Returns identical `{"status": "ok"}` regardless of carrier existence (prevents enumeration). Raw webhook body is never stored — only sanitised fields are written to `freight.tracking.event`.

### 3PL Handoff

When `freight.booking.action_confirm()` runs:
1. `_queue_3pl_inward_order()` — creates one `3pl.message` (document_type=`inward_order`) per linked PO. Uses a two-step connector resolution: specific product-category match first, then catch-all.
2. `_build_inward_order_payload()` — builds XML payload via `InwardOrderDocument` from `stock_3pl_mainfreight` (imported at runtime; gracefully skips if module not installed).

Idempotency guard: skips POs that already have a `create`-type `inward_order` message. UPDATE messages (ETA drift > 24h or vessel TBA→known) are queued without dedup — multiple updates per PO are valid.

### Contract-Aware Selection

`freight.carrier.contract` tracks volume commitments (TEU/weight/shipments). When `selection_mode = 'contract_aware'`, `action_auto_select()` on `freight.tender` picks the cheapest contracted carrier, computes opportunity cost vs market, and sets `has_opportunity_cost_alert` if the contracted rate exceeds the cheapest quote.

---

## Key Gotchas

- **Pessimistic locking**: `action_request_quotes()`, `action_book()`, and `action_confirm_with_dsv()` all use `SELECT FOR UPDATE NOWAIT` with a post-lock state re-check to guard against double-click races.
- **`delivery_type`**: carrier adapters extend `delivery.carrier` (Odoo's built-in model). Each carrier module adds its own `delivery_type` selection value via model inheritance in its `models/` directory.
- **Mock adapters**: each carrier module has a `*_mock_adapter.py` — used in tests. They are not registered by default; tests register them manually or via `@register_adapter` in the test setup.
- **`mml_freight_demo` is disabled**: `installable = False`. Do not re-enable without reviewing demo data for production safety.

---

## Adding a New Carrier Adapter

1. Create `addons/mml_freight_<carrier>/` with `__manifest__.py` depending on `mml_freight`.
2. Add a `delivery_type` selection value on `delivery.carrier` in `models/`.
3. Implement `FreightAdapterBase` subclass in `adapters/`, decorate with `@register_adapter('your_type')`.
4. Wire the adapter in `__init__.py` so the decorator runs on module load.
5. Add a mock adapter for tests and register it in `conftest.py`-level fixtures.

---

## Related Projects

| Project | Path |
|---------|------|
| Mainfreight 3PL (`stock_3pl_core`, `stock_3pl_mainfreight`) | `E:\ClaudeCode\projects\mainfreight.3pl.intergration` |
| Root mono-repo | `E:\ClaudeCode\projects\mml.odoo.apps` |

The `3pl.message` and `3pl.connector` models referenced in `freight_booking.py` live in `stock_3pl_core`. `freight.booking` checks `'3pl.connector' in self.env` before attempting any 3PL operations.
