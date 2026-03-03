# Design: DSV Document Upload from Purchase Order

**Date:** 2026-03-03
**Status:** Approved
**Scope:** `mml_freight_dsv` + `mml_freight`

---

## Problem

MML ops currently email the DSV rep manually to send freight documents (PI, packing list, quarantine declaration) for ocean freight bookings. These documents are already attached to the Odoo Purchase Order. The goal is to eliminate that email step entirely.

---

## Solution

A "Send Documents to DSV" wizard on the PO form. The system reads all PO attachments, auto-classifies each by filename keywords, and presents ops with a one-click confirmation table. On confirm, documents are uploaded directly to DSV via the Upload API and the result is logged on the PO chatter.

---

## Architecture & Data Flow

```
PO form
  └── [Send Documents to DSV] button
        │
        ▼
  freight.dsv.doc.upload.wizard  (transient)
  ├── reads all ir.attachment on this PO
  ├── auto-classifies each by filename keywords
  ├── shows ops a table: ✓ | filename | type (editable dropdown) | size
  └── on Confirm:
        │
        ├── resolves freight.booking linked via tender_id → po_ids
        │
        ├── for each selected attachment:
        │     adapter.upload_document(booking, filename, file_bytes, dsv_type)
        │     → POST /my/upload/v1/shipments/{booking_id}/documents
        │     → multipart/form-data: file + document_type
        │
        ├── on success → create freight.document record (uploaded_to_carrier=True)
        └── logs summary on PO chatter
```

---

## Keyword → DSV Type Detection

| Filename contains (case-insensitive) | DSV type | Wizard label |
|---|---|---|
| `pi`, `proforma`, `invoice`, `commercial` | `INV` | Commercial Invoice |
| `packing`, `pkl` | `PKL` | Packing List |
| `quarantine`, `quar`, `phyto`, `biosecurity` | `CUS` | Customs / Quarantine |
| `dangerous`, `dg`, `haz`, `msds` | `HAZ` | Dangerous Goods |
| anything else | `GDS` | Other Goods Doc |

Auto-detection is a suggestion only — ops can override any type in the wizard before sending.

---

## DSV Upload API

- **Endpoint:** `POST /my/upload/v1/shipments/{booking_id}/documents`
  *(exact path to confirm against demo sandbox — documented as auth-walled in DSV portal)*
- **Auth:** OAuth2 Bearer token + `doc_upload` APIM subscription key
- **Body:** `multipart/form-data` — `file` (bytes) + `document_type` (CUS|GDS|HAZ|INV|PKL)
- **Constraints:** max 3MB per file, permanent (no delete), antimalware scanned server-side

---

## Components

### Modified: `mml_freight/adapters/base_adapter.py`
Add `upload_document()` to the adapter contract as an optional no-op:
```python
def upload_document(self, booking, filename, file_bytes, dsv_type):
    """Upload a document to the carrier. Returns carrier_upload_ref (str) or None."""
    return None
```

### Modified: `mml_freight/models/freight_document.py`
- Add `packing_list` and `quarantine` to `DOC_TYPES`
- Add `uploaded_to_carrier = fields.Boolean()`
- Add `carrier_upload_ref = fields.Char()`

### Modified: `mml_freight_dsv/adapters/dsv_generic_adapter.py`
Add `upload_document(booking, filename, file_bytes, dsv_type)`:
- Uses `doc_upload` subscription key via `self._headers(token, 'doc_upload')`
- POSTs multipart/form-data to DSV
- Returns `carrier_upload_ref` on success, `None` on failure (non-raising)

### Modified: `mml_freight_dsv/adapters/dsv_mock_adapter.py`
Add `upload_document()`:
- Demo mode: returns `'MOCK-UPLOAD-{n}'` without HTTP
- Production mode: delegates to `DsvGenericAdapter`

### New: `mml_freight_dsv/wizards/dsv_doc_upload_wizard.py`
Transient model `freight.dsv.doc.upload.wizard`:
- `po_id` — Many2one purchase.order
- `line_ids` — One2many to wizard line (attachment + detected type + include flag)
- `_default_lines()` — reads PO attachments, runs keyword detection
- `action_upload()` — resolves booking, calls adapter per line, creates freight.document records, logs chatter

### New: `mml_freight_dsv/views/dsv_doc_upload_wizard_views.xml`
Wizard form: table of lines (checkbox | filename | type dropdown | size), confirm button.

### Modified: PO form view
"Send Documents to DSV" button — visible only when PO has a linked `freight.booking` with `carrier_id.delivery_type` in `('dsv_generic', 'dsv_xpress')` and `state` not in `('delivered', 'cancelled')`.

---

## Button Visibility Rule

```python
# Show button when:
# - PO has a freight.tender with at least one freight.booking
# - That booking's carrier is DSV (dsv_generic or dsv_xpress)
# - Booking state is not delivered/cancelled/received
```

---

## Chatter Log Format

```
Documents sent to DSV (booking DSV-BK-0042):
  ✓ MML-PI-PO123.pdf → Commercial Invoice
  ✓ MML-PKL-PO123.pdf → Packing List
  ✗ MML-QD-PO123.pdf → Upload failed (file exceeds 3MB limit)
```

---

## Tests

| Test | Covers |
|---|---|
| `test_keyword_detection` | Each keyword pattern → correct DSV type; unknown → `GDS` |
| `test_upload_success` | 200 response → `freight.document` created, `uploaded_to_carrier=True`, ref stored |
| `test_upload_failure_non_blocking` | 413 → logged on chatter, no exception, other docs still processed |
| `test_upload_uses_doc_upload_subkey` | `DSV-Subscription-Key` header uses `doc_upload` key |
| `test_no_booking_no_button` | Button not rendered when PO has no linked DSV booking |
| `test_mock_adapter_returns_success` | Demo mode returns mock ref, no HTTP call |
| `test_file_size_guard` | Files >3MB skipped before API call, warning shown in wizard |

---

## Out of Scope

- Labels (not needed for ocean freight)
- Automatic upload without ops confirmation (deliberate — compliance docs need human sign-off)
- Document deletion (DSV API does not support it)
- XPress upload (different endpoint — scaffold only, implement when needed)
