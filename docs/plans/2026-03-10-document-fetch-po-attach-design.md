# Document Fetch & PO Auto-Attach Design

**Goal:** Automatically fetch freight documents (BOL, POD, customs, packing list) and invoices from Mainfreight and DSV, attach them to the linked purchase orders in Odoo, and keep them in sync via state-driven triggers and a daily cron safety net.

**Architecture:** Three layers — (1) Mainfreight document/invoice adapter methods, (2) core auto-attach-to-PO pipeline in `mml_freight`, (3) dual trigger strategy (state-driven + daily cron).

**Tech Stack:** Python, Odoo 19, Mainfreight Tracking/Documents API, DSV Download API (already implemented), `ir.attachment`, `purchase.order` chatter.

---

## Layer 1: Mainfreight Document Methods

### `get_documents(booking)` — `mf_adapter.py`

1. Call `get_tracking()` (already implemented) and extract POD URLs from the response payload — these are returned directly by the Mainfreight Tracking API.
2. Download each POD URL's bytes using the same API key auth header.
3. For BOL, customs, packing list — call `_fetch_carrier_documents(booking)` helper:
   - Hits a dedicated documents endpoint (path constant `DOCUMENTS_PATH` in `mf_auth.py`)
   - Raises `NotImplementedError` with comment: *"implement once MF developer account is active — endpoint path unconfirmed"*
   - Returns `[]` in the stub (graceful degradation)
4. Returns list of dicts: `{doc_type, bytes, filename, carrier_doc_ref}`

### `get_invoice(booking)` — `mf_adapter.py`

- Hits `INVOICE_PATH` (constant in `mf_auth.py`, stubbed)
- Returns `None` until endpoint is confirmed
- When implemented: returns `{carrier_invoice_ref, amount, currency, invoice_date}`

### `mf_auth.py` additions

```python
DOCUMENTS_PATH = '/documents/2.0/references'   # unconfirmed — stub
INVOICE_PATH   = '/invoices/2.0/references'    # unconfirmed — stub
```

### Mock adapter (`mf_mock_adapter.py`)

- `get_documents()` in UAT mode returns two fake docs:
  - A minimal valid 5-byte PDF (`%PDF-`) as `pod` type, filename `POD-MOCK.pdf`
  - A 5-byte stub as `customs` type, filename `CUSTOMS-MOCK.pdf`
- `get_invoice()` in UAT mode returns:
  ```python
  {'carrier_invoice_ref': 'MF-INV-MOCK-001', 'amount': 2840.0, 'currency': 'NZD', 'invoice_date': '2026-03-10'}
  ```
- Production mode delegates to live `MFAdapter` (same as tracking)

---

## Layer 2: Auto-Attach to PO (`mml_freight` core)

### `freight.booking.action_fetch_documents()`

Extended to call `_attach_documents_to_pos(documents)` after creating `freight.document` records.

### `freight.booking._attach_documents_to_pos(documents)`

For each `freight.document` just created, for each PO in `booking.po_ids`:
- Create `ir.attachment` (res_model=`purchase.order`, res_id=po.id) with the same binary
- Skip if an attachment with the same filename already exists on that PO (idempotent check)

After all docs attached, post one chatter message per PO:
> *"N document(s) attached from freight booking [ref]: POD, Invoice, Customs"*

One message per fetch run regardless of doc count — no chatter spam.

### `freight.booking.action_fetch_invoice()`

New method — calls `adapter.get_invoice(booking)`:
- On success: writes `booking.actual_rate` and posts chatter on each linked PO:
  > *"Freight cost confirmed: NZD 2,840 (Mainfreight invoice MF-INV-12345)"*
- On `None` return: silent no-op
- On exception: logs warning, posts chatter note on booking: *"Invoice fetch failed, will retry via cron"*

Both methods live in `mml_freight` — DSV gets PO attachment for free with no changes to `dsv_generic_adapter.py`.

---

## Layer 3: Triggers

### State-driven (`freight.booking.write()` override)

On state transition:

| New State | Action |
|-----------|--------|
| `arrived_port` | `action_fetch_documents()` for `['customs', 'packing_list', 'label']` |
| `delivered` | `action_fetch_documents()` for all types + `action_fetch_invoice()` |

Both calls wrapped in `try/except`:
- API failure → `_logger.warning(...)` + chatter note: *"Auto-fetch failed, will retry via cron"*
- State advance always succeeds — fetch failure never blocks booking state machine

### Cron safety net (`cron_fetch_missing_documents()`)

Daily cron, `active=True`, installed in `mml_freight/data/ir_cron.xml`.

Targets bookings where ALL of:
- State in `['in_transit', 'arrived_port', 'customs', 'delivered']`
- Carrier has credentials configured (`x_mf_api_key` or DSV equivalent is set)
- At least one of:
  - No `freight.document` records at all
  - State is `delivered` and no POD document exists
  - State is `delivered` and `actual_rate` is 0 (no invoice fetched)

Reruns `action_fetch_documents()` and/or `action_fetch_invoice()` as needed.
Silent no-op per booking if API returns nothing new.

---

## Testing

### Pure-Python tests (no Odoo required)

| File | Tests |
|------|-------|
| `mml_freight_mainfreight/tests/test_mf_documents.py` | Mock adapter document/invoice response shapes; UAT mode returns expected fake docs; production mode delegates to MFAdapter; `_fetch_carrier_documents` stub returns `[]` gracefully |
| `mml_freight/tests/test_po_attachment.py` | `_attach_documents_to_pos()` creates correct attachment count; idempotency (second call creates no duplicates); chatter message content and count; invoice fetch writes `actual_rate` and posts PO note |
| `mml_freight/tests/test_document_triggers.py` | State transition to `arrived_port` triggers fetch for correct doc types only; state transition to `delivered` triggers full fetch + invoice; API failure does not block state advance; cron targets correct bookings; cron skips bookings with complete docs |

All tests use existing mock adapter infrastructure. No live Odoo or API credentials required.

---

## File Changelist

| File | Change |
|------|--------|
| `mml_freight_mainfreight/adapters/mf_auth.py` | Add `DOCUMENTS_PATH`, `INVOICE_PATH` constants |
| `mml_freight_mainfreight/adapters/mf_adapter.py` | Implement `get_documents()`, `_fetch_carrier_documents()` stub, `get_invoice()` stub |
| `mml_freight_mainfreight/adapters/mf_mock_adapter.py` | Add mock `get_documents()` + `get_invoice()` UAT responses |
| `mml_freight_mainfreight/tests/test_mf_documents.py` | New test file |
| `mml_freight/models/freight_booking.py` | Add `_attach_documents_to_pos()`, `action_fetch_invoice()`, extend `action_fetch_documents()`, override `write()` for state triggers |
| `mml_freight/data/ir_cron.xml` | Add `cron_fetch_missing_documents` (active=True, daily) |
| `mml_freight/tests/test_po_attachment.py` | New test file |
| `mml_freight/tests/test_document_triggers.py` | New test file |
