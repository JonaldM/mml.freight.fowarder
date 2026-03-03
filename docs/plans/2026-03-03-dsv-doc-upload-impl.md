# DSV Document Upload — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Replace the manual "email DSV rep" step with a one-click wizard on the PO form that uploads PI, packing list, and quarantine documents directly to DSV via the Upload API.

**Architecture:** A transient wizard (`freight.dsv.doc.upload.wizard`) reads PO attachments, auto-classifies by filename keyword, lets ops confirm, then POSTs each file as multipart/form-data to `POST /my/upload/v1/shipments/{booking_id}/documents`. Results are logged on the PO chatter and stored as `freight.document` records.

**Tech Stack:** Odoo 19, Python `requests` multipart upload, DSV Generic Upload API v1, `ir.attachment.datas` (base64), `mml_freight` adapter pattern.

**Design doc:** `docs/plans/2026-03-03-dsv-doc-upload-design.md`

---

## Before You Start

Read these files to understand the existing patterns:
- `addons/mml_freight/adapters/base_adapter.py` — adapter contract
- `addons/mml_freight_dsv/adapters/dsv_generic_adapter.py` — existing HTTP methods (`_headers`, `_post_with_retry`)
- `addons/mml_freight_dsv/adapters/dsv_mock_adapter.py` — demo/production delegation pattern
- `addons/mml_freight_dsv/models/freight_carrier_dsv.py` — `dsv_subkey(service)` helper
- `addons/mml_freight/models/freight_document.py` — `freight.document` model
- `addons/mml_freight/models/freight_adapter_registry.py` — `FreightAdapterRegistry.get_adapter(carrier)`

---

## Task 1: Fix broken tests from subscription key refactor

Six test files reference the now-deleted `x_dsv_subscription_key` field. They must be updated before any new work.

**Files to modify:**
- `addons/mml_freight_dsv/tests/test_dsv_documents.py`
- `addons/mml_freight_dsv/tests/test_dsv_tracking.py`
- `addons/mml_freight_dsv/tests/test_dsv_confirm_booking.py`
- `addons/mml_freight_dsv/tests/test_dsv_generic_adapter.py`
- `addons/mml_freight_dsv/tests/test_dsv_label.py`
- `addons/mml_freight_dsv/tests/test_dsv_cancel.py`

**Step 1: Find all occurrences**

```bash
grep -rn "x_dsv_subscription_key" addons/mml_freight_dsv/tests/
```

**Step 2: Replace in each file**

In every carrier `create({...})` dict, replace:
```python
'x_dsv_subscription_key': 'SUB-XXX-001',
```
with:
```python
'x_dsv_subkey_doc_download_primary': 'SUB-DL-001',
'x_dsv_subkey_booking_primary': 'SUB-BK-001',
'x_dsv_subkey_quote_primary': 'SUB-QT-001',
'x_dsv_subkey_visibility_primary': 'SUB-VIS-001',
'x_dsv_subkey_invoicing_primary': 'SUB-INV-001',
```
(Each test only needs the key(s) relevant to what it tests — use all of the above for safety.)

**Step 3: Run the six test files**

```bash
python -m pytest addons/mml_freight_dsv/tests/test_dsv_documents.py \
  addons/mml_freight_dsv/tests/test_dsv_tracking.py \
  addons/mml_freight_dsv/tests/test_dsv_confirm_booking.py \
  addons/mml_freight_dsv/tests/test_dsv_generic_adapter.py \
  addons/mml_freight_dsv/tests/test_dsv_label.py \
  addons/mml_freight_dsv/tests/test_dsv_cancel.py -v
```
Expected: all PASS.

**Step 4: Commit**

```bash
git add addons/mml_freight_dsv/tests/
git commit -m "fix(tests): update carrier fixtures to use per-service subscription key fields"
```

---

## Task 2: Extend freight.document model

**File:** `addons/mml_freight/models/freight_document.py`

**Step 1: Write the failing test**

Create `addons/mml_freight/tests/test_freight_document_model.py`:

```python
from odoo.tests.common import TransactionCase


class TestFreightDocumentModel(TransactionCase):

    def test_packing_list_doc_type_exists(self):
        """packing_list is a valid doc_type selection value."""
        field = self.env['freight.document']._fields['doc_type']
        keys = [k for k, _ in field.selection]
        self.assertIn('packing_list', keys)

    def test_quarantine_doc_type_exists(self):
        """quarantine is a valid doc_type selection value."""
        field = self.env['freight.document']._fields['doc_type']
        keys = [k for k, _ in field.selection]
        self.assertIn('quarantine', keys)

    def test_uploaded_to_carrier_default_false(self):
        """uploaded_to_carrier defaults to False."""
        supplier = self.env['res.partner'].create({'name': 'Test Supplier'})
        po = self.env['purchase.order'].create({'partner_id': supplier.id})
        tender = self.env['freight.tender'].create({
            'po_ids': [(4, po.id)],
            'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
        })
        carrier = self.env['delivery.carrier'].create({
            'name': 'Test Carrier',
            'product_id': self.env['product.product'].create(
                {'name': 'Test', 'type': 'service'}
            ).id,
            'delivery_type': 'dsv_generic',
        })
        booking = self.env['freight.booking'].create({
            'carrier_id': carrier.id,
            'tender_id': tender.id,
            'currency_id': self.env.company.currency_id.id,
        })
        doc = self.env['freight.document'].create({
            'booking_id': booking.id,
            'doc_type': 'packing_list',
        })
        self.assertFalse(doc.uploaded_to_carrier)
        self.assertFalse(doc.carrier_upload_ref)
```

**Step 2: Run to verify it fails**

```bash
python -m pytest addons/mml_freight/tests/test_freight_document_model.py -v
```
Expected: FAIL — `packing_list` not in selection.

**Step 3: Implement changes**

In `addons/mml_freight/models/freight_document.py`, update `DOC_TYPES` and add fields:

```python
from odoo import models, fields

DOC_TYPES = [
    ('label', 'Shipping Label'),
    ('pod', 'Proof of Delivery'),
    ('invoice', 'Freight Invoice'),
    ('customs', 'Customs Document'),
    ('packing_list', 'Packing List'),
    ('quarantine', 'Quarantine / Phytosanitary'),
    ('other', 'Other'),
]


class FreightDocument(models.Model):
    _name = 'freight.document'
    _description = 'Freight Booking — Document'
    _order = 'id'

    _sql_constraints = [
        (
            'unique_booking_doc',
            'UNIQUE(booking_id, doc_type, carrier_doc_ref)',
            'A document with this type and carrier reference already exists for this booking.',
        ),
    ]

    booking_id = fields.Many2one(
        'freight.booking', required=True, ondelete='cascade', index=True,
    )
    doc_type = fields.Selection(DOC_TYPES, string='Type', required=True, default='other')
    attachment_id = fields.Many2one('ir.attachment', string='Attachment', ondelete='set null')
    carrier_doc_ref = fields.Char('Carrier Doc Ref')
    uploaded_to_carrier = fields.Boolean('Uploaded to Carrier', default=False)
    carrier_upload_ref = fields.Char('Carrier Upload Ref', readonly=True)
```

**Step 4: Run tests**

```bash
python -m pytest addons/mml_freight/tests/test_freight_document_model.py -v
```
Expected: PASS.

**Step 5: Commit**

```bash
git add addons/mml_freight/models/freight_document.py \
        addons/mml_freight/tests/test_freight_document_model.py
git commit -m "feat(mml_freight): add packing_list/quarantine doc types and upload tracking fields"
```

---

## Task 3: Add upload_document to base adapter contract

**File:** `addons/mml_freight/adapters/base_adapter.py`

**Step 1: Write the failing test**

Add to `addons/mml_freight/tests/test_carrier_contract.py` (file already exists — append):

```python
def test_upload_document_default_returns_none(self):
    """Base adapter upload_document returns None (not supported by default)."""
    from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase

    class _StubAdapter(FreightAdapterBase):
        def request_quote(self, tender): return []
        def create_booking(self, tender, quote): return {}
        def get_tracking(self, booking): return []

    adapter = _StubAdapter(None, None)
    result = adapter.upload_document(None, 'test.pdf', b'bytes', 'INV')
    self.assertIsNone(result)
```

**Step 2: Run to verify it fails**

```bash
python -m pytest addons/mml_freight/tests/test_carrier_contract.py::TestCarrierContract::test_upload_document_default_returns_none -v
```
Expected: FAIL — `upload_document` not found on base class.

**Step 3: Add to base adapter**

In `addons/mml_freight/adapters/base_adapter.py`, add after `get_invoice`:

```python
def upload_document(self, booking, filename, file_bytes, dsv_type):
    """Upload a document to the carrier against a booking.

    Args:
        booking: freight.booking record
        filename: original filename (str)
        file_bytes: raw file content (bytes)
        dsv_type: carrier document type code (e.g. INV, PKL, CUS, HAZ, GDS)

    Returns:
        carrier_upload_ref (str) on success, None if not supported or failed.
    """
    return None
```

**Step 4: Run tests**

```bash
python -m pytest addons/mml_freight/tests/test_carrier_contract.py -v
```
Expected: PASS.

**Step 5: Commit**

```bash
git add addons/mml_freight/adapters/base_adapter.py \
        addons/mml_freight/tests/test_carrier_contract.py
git commit -m "feat(mml_freight): add upload_document to adapter contract (default no-op)"
```

---

## Task 4: Implement upload_document in DsvGenericAdapter

**File:** `addons/mml_freight_dsv/adapters/dsv_generic_adapter.py`

**Step 1: Write the failing test**

Create `addons/mml_freight_dsv/tests/test_dsv_doc_upload.py`:

```python
import base64
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter


def _resp(status=200, json_data=None, ok=None):
    m = MagicMock()
    m.status_code = status
    m.ok = (status < 400) if ok is None else ok
    if json_data is not None:
        m.json.return_value = json_data
    return m


class TestDsvDocUpload(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.product'].create(
            {'name': 'Upload Test Product', 'type': 'service'}
        )
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Upload Carrier',
            'product_id': cls.product.id,
            'delivery_type': 'dsv_generic',
            'x_dsv_environment': 'production',
            'x_dsv_subkey_doc_upload_primary': 'SUB-UPLOAD-001',
        })
        supplier = cls.env['res.partner'].create({'name': 'Upload Supplier'})
        cls.po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        tender = cls.env['freight.tender'].create({
            'po_ids': [(4, cls.po.id)],
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': tender.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_booking_id': 'BK-UPLOAD-001',
        })

    def _adapter(self):
        return DsvGenericAdapter(self.carrier, self.env)

    def test_upload_success_returns_document_id(self):
        """200 response with documentId → returns that ref."""
        mock_resp = _resp(status=200, json_data={'documentId': 'DSV-DOC-UPLOAD-99'})
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token',
                   return_value='tok'), \
             patch('requests.post', return_value=mock_resp) as mock_post:
            result = self._adapter().upload_document(
                self.booking, 'MML-PI-001.pdf', b'%PDF-content', 'INV'
            )
        self.assertEqual(result, 'DSV-DOC-UPLOAD-99')

    def test_upload_uses_doc_upload_subscription_key(self):
        """DSV-Subscription-Key header uses the doc_upload key, not another."""
        mock_resp = _resp(status=200, json_data={'documentId': 'REF-1'})
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token',
                   return_value='tok'), \
             patch('requests.post', return_value=mock_resp) as mock_post:
            self._adapter().upload_document(
                self.booking, 'packing.pdf', b'bytes', 'PKL'
            )
        call_headers = mock_post.call_args[1]['headers']
        self.assertEqual(call_headers['DSV-Subscription-Key'], 'SUB-UPLOAD-001')

    def test_upload_failure_returns_none(self):
        """Non-2xx response → returns None (non-raising)."""
        mock_resp = _resp(status=413)
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token',
                   return_value='tok'), \
             patch('requests.post', return_value=mock_resp):
            result = self._adapter().upload_document(
                self.booking, 'huge.pdf', b'big', 'PKL'
            )
        self.assertIsNone(result)

    def test_upload_no_booking_id_returns_none(self):
        """Booking with no carrier_booking_id → returns None without HTTP call."""
        booking_no_ref = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': self.env.company.currency_id.id,
        })
        with patch('requests.post') as mock_post:
            result = self._adapter().upload_document(
                booking_no_ref, 'test.pdf', b'bytes', 'INV'
            )
        self.assertIsNone(result)
        mock_post.assert_not_called()

    def test_upload_retries_on_401(self):
        """401 response → refresh token → retry POST."""
        resp_401 = _resp(status=401)
        resp_200 = _resp(status=200, json_data={'documentId': 'RETRY-REF'})
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token',
                   return_value='old-tok'), \
             patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.refresh_token',
                   return_value='new-tok'), \
             patch('requests.post', side_effect=[resp_401, resp_200]):
            result = self._adapter().upload_document(
                self.booking, 'pi.pdf', b'bytes', 'INV'
            )
        self.assertEqual(result, 'RETRY-REF')
```

**Step 2: Run to verify it fails**

```bash
python -m pytest addons/mml_freight_dsv/tests/test_dsv_doc_upload.py -v
```
Expected: FAIL — `upload_document` not defined on `DsvGenericAdapter`.

**Step 3: Implement**

Add to `addons/mml_freight_dsv/adapters/dsv_generic_adapter.py`, after `get_invoice`:

```python
    # ------------------------------------------------------------------
    # upload_document
    # ------------------------------------------------------------------

    def upload_document(self, booking, filename, file_bytes, dsv_type):
        """Upload a document to DSV against a booking reference.

        DSV Upload API: POST /my/upload/v1/shipments/{booking_id}/documents
        Body: multipart/form-data — file + document_type
        Supported dsv_type codes: CUS, GDS, HAZ, INV, PKL

        Returns carrier_upload_ref (str) on success, None on any failure.
        Note: uploads are permanent — DSV provides no delete endpoint.
        Note: exact endpoint path to confirm against demo sandbox.
        """
        bk_id = booking.carrier_booking_id
        if not bk_id:
            _logger.warning('DSV upload_document: no carrier_booking_id on booking %s', booking.name)
            return None
        try:
            token = get_token(self.carrier)
        except DsvAuthError as e:
            _logger.warning('DSV upload_document auth failed for %s: %s', booking.name, e)
            return None
        url = f'{_DSV_GENERIC_BASE}/upload/v1/shipments/{bk_id}/documents'
        headers = {
            'Authorization': f'Bearer {token}',
            'DSV-Subscription-Key': self.carrier.dsv_subkey('doc_upload'),
        }
        try:
            resp = requests.post(
                url,
                headers=headers,
                files={'file': (filename, file_bytes, 'application/octet-stream')},
                data={'document_type': dsv_type},
                timeout=60,
            )
        except Exception as e:
            _logger.warning('DSV upload_document request failed for %s: %s', booking.name, e)
            return None
        if resp.status_code == 401:
            try:
                token = refresh_token(self.carrier)
            except DsvAuthError:
                return None
            headers['Authorization'] = f'Bearer {token}'
            try:
                resp = requests.post(
                    url,
                    headers=headers,
                    files={'file': (filename, file_bytes, 'application/octet-stream')},
                    data={'document_type': dsv_type},
                    timeout=60,
                )
            except Exception as e:
                _logger.warning('DSV upload_document retry failed for %s: %s', booking.name, e)
                return None
        if not resp.ok:
            _logger.warning('DSV upload_document HTTP %s for %s', resp.status_code, booking.name)
            return None
        try:
            data = resp.json()
            return data.get('documentId') or data.get('uploadId') or f'UPLOADED-{dsv_type}-{bk_id}'
        except Exception:
            return f'UPLOADED-{dsv_type}-{bk_id}'
```

**Step 4: Run tests**

```bash
python -m pytest addons/mml_freight_dsv/tests/test_dsv_doc_upload.py -v
```
Expected: all PASS.

**Step 5: Commit**

```bash
git add addons/mml_freight_dsv/adapters/dsv_generic_adapter.py \
        addons/mml_freight_dsv/tests/test_dsv_doc_upload.py
git commit -m "feat(mml_freight_dsv): implement upload_document on DsvGenericAdapter"
```

---

## Task 5: Add upload_document to DsvMockAdapter

**File:** `addons/mml_freight_dsv/adapters/dsv_mock_adapter.py`

**Step 1: Write the failing test**

Add to `addons/mml_freight_dsv/tests/test_dsv_mock_adapter.py` (file already exists — append):

```python
def test_upload_document_demo_returns_mock_ref(self):
    """Demo mode returns a mock upload ref without HTTP."""
    carrier = self.env['delivery.carrier'].create({
        'name': 'DSV Mock Upload',
        'product_id': self.env['product.product'].create(
            {'name': 'Upload Mock Product', 'type': 'service'}
        ).id,
        'delivery_type': 'dsv_generic',
        'x_dsv_environment': 'demo',
    })
    supplier = self.env['res.partner'].create({'name': 'Mock Upload Supplier'})
    po = self.env['purchase.order'].create({'partner_id': supplier.id})
    tender = self.env['freight.tender'].create({
        'po_ids': [(4, po.id)],
        'company_id': self.env.company.id,
        'currency_id': self.env.company.currency_id.id,
    })
    booking = self.env['freight.booking'].create({
        'carrier_id': carrier.id,
        'tender_id': tender.id,
        'currency_id': self.env.company.currency_id.id,
        'carrier_booking_id': 'MOCK-BK-001',
    })
    from odoo.addons.mml_freight_dsv.adapters.dsv_mock_adapter import DsvMockAdapter
    adapter = DsvMockAdapter(carrier, self.env)
    with patch('requests.post') as mock_post:
        result = adapter.upload_document(booking, 'pi.pdf', b'bytes', 'INV')
    self.assertIsNotNone(result)
    self.assertIn('MOCK', result)
    mock_post.assert_not_called()
```

**Step 2: Run to verify it fails**

```bash
python -m pytest addons/mml_freight_dsv/tests/test_dsv_mock_adapter.py::TestDsvMockAdapter::test_upload_document_demo_returns_mock_ref -v
```
Expected: FAIL.

**Step 3: Implement**

Add to `addons/mml_freight_dsv/adapters/dsv_mock_adapter.py`, after `get_invoice`:

```python
    def upload_document(self, booking, filename, file_bytes, dsv_type):
        if not self._demo():
            return self._live().upload_document(booking, filename, file_bytes, dsv_type)
        return f'MOCK-UPLOAD-{dsv_type}-{next(_counter):04d}'
```

**Step 4: Run tests**

```bash
python -m pytest addons/mml_freight_dsv/tests/test_dsv_mock_adapter.py -v
```
Expected: all PASS.

**Step 5: Commit**

```bash
git add addons/mml_freight_dsv/adapters/dsv_mock_adapter.py \
        addons/mml_freight_dsv/tests/test_dsv_mock_adapter.py
git commit -m "feat(mml_freight_dsv): add upload_document to DsvMockAdapter"
```

---

## Task 6: Create the wizard model

**Files to create:**
- `addons/mml_freight_dsv/wizards/__init__.py`
- `addons/mml_freight_dsv/wizards/dsv_doc_upload_wizard.py`

**Step 1: Write the failing tests**

Add to `addons/mml_freight_dsv/tests/test_dsv_doc_upload.py` (append after existing class):

```python
import base64
from odoo.addons.mml_freight_dsv.wizards.dsv_doc_upload_wizard import detect_dsv_type


class TestDetectDsvType(TransactionCase):

    def test_pi_filename_detects_inv(self):
        self.assertEqual(detect_dsv_type('MML-PI-PO001.pdf'), 'INV')

    def test_invoice_filename_detects_inv(self):
        self.assertEqual(detect_dsv_type('Commercial_Invoice_March.pdf'), 'INV')

    def test_packing_list_detects_pkl(self):
        self.assertEqual(detect_dsv_type('Packing_List_v2.xlsx'), 'PKL')

    def test_pkl_abbreviation_detects_pkl(self):
        self.assertEqual(detect_dsv_type('PKL-PO123.pdf'), 'PKL')

    def test_quarantine_detects_cus(self):
        self.assertEqual(detect_dsv_type('Quarantine_Certificate.pdf'), 'CUS')

    def test_phyto_detects_cus(self):
        self.assertEqual(detect_dsv_type('Phytosanitary_Cert.pdf'), 'CUS')

    def test_unknown_filename_detects_gds(self):
        self.assertEqual(detect_dsv_type('random_document.pdf'), 'GDS')

    def test_case_insensitive(self):
        self.assertEqual(detect_dsv_type('PACKING_LIST.PDF'), 'PKL')


class TestDsvDocUploadWizard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.product'].create(
            {'name': 'Wizard Test Product', 'type': 'service'}
        )
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Wizard Carrier',
            'product_id': cls.product.id,
            'delivery_type': 'dsv_generic',
            'x_dsv_environment': 'demo',
        })
        cls.supplier = cls.env['res.partner'].create({'name': 'Wizard Supplier'})
        cls.po = cls.env['purchase.order'].create({'partner_id': cls.supplier.id})
        tender = cls.env['freight.tender'].create({
            'po_ids': [(4, cls.po.id)],
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': tender.id,
            'po_ids': [(4, cls.po.id)],
            'currency_id': cls.env.company.currency_id.id,
            'carrier_booking_id': 'WIZ-BK-001',
            'state': 'confirmed',
        })

    def _make_attachment(self, name, content=b'%PDF-test', po=None):
        po = po or self.po
        return self.env['ir.attachment'].create({
            'name': name,
            'res_model': 'purchase.order',
            'res_id': po.id,
            'datas': base64.b64encode(content).decode(),
            'mimetype': 'application/pdf',
        })

    def test_default_get_populates_lines_from_po_attachments(self):
        """Wizard lines are auto-populated from PO attachments."""
        att = self._make_attachment('MML-PI-001.pdf')
        wizard = self.env['freight.dsv.doc.upload.wizard'].with_context(
            default_po_id=self.po.id
        ).create({'po_id': self.po.id})
        att_ids = wizard.line_ids.mapped('attachment_id').ids
        self.assertIn(att.id, att_ids)

    def test_keyword_detection_applied_to_lines(self):
        """PI filename → line.dsv_type == INV."""
        self._make_attachment('Proforma_Invoice.pdf')
        wizard = self.env['freight.dsv.doc.upload.wizard'].with_context(
            default_po_id=self.po.id
        ).create({'po_id': self.po.id})
        pi_lines = wizard.line_ids.filtered(
            lambda l: 'invoice' in (l.attachment_id.name or '').lower()
        )
        self.assertTrue(pi_lines)
        self.assertEqual(pi_lines[0].dsv_type, 'INV')

    def test_oversized_file_pre_unchecked(self):
        """Attachment > 3MB is pre-unchecked in the wizard."""
        big_content = b'X' * (3 * 1024 * 1024 + 1)
        att = self._make_attachment('BigFile.pdf', content=big_content)
        wizard = self.env['freight.dsv.doc.upload.wizard'].with_context(
            default_po_id=self.po.id
        ).create({'po_id': self.po.id})
        big_line = wizard.line_ids.filtered(lambda l: l.attachment_id.id == att.id)
        if big_line:
            self.assertFalse(big_line[0].include)

    def test_action_upload_creates_freight_document_on_success(self):
        """Successful upload creates freight.document with uploaded_to_carrier=True."""
        att = self._make_attachment('MML-PKL-001.pdf')
        wizard = self.env['freight.dsv.doc.upload.wizard'].create({
            'po_id': self.po.id,
            'line_ids': [(0, 0, {
                'attachment_id': att.id,
                'dsv_type': 'PKL',
                'include': True,
            })],
        })
        wizard.action_upload()
        doc = self.env['freight.document'].search([
            ('booking_id', '=', self.booking.id),
            ('attachment_id', '=', att.id),
        ])
        self.assertTrue(doc)
        self.assertTrue(doc.uploaded_to_carrier)

    def test_action_upload_logs_on_po_chatter(self):
        """Successful upload posts a message on the PO."""
        att = self._make_attachment('MML-QD-001.pdf')
        initial_msg_count = len(self.po.message_ids)
        wizard = self.env['freight.dsv.doc.upload.wizard'].create({
            'po_id': self.po.id,
            'line_ids': [(0, 0, {
                'attachment_id': att.id,
                'dsv_type': 'CUS',
                'include': True,
            })],
        })
        wizard.action_upload()
        self.assertGreater(len(self.po.message_ids), initial_msg_count)

    def test_action_upload_skips_unchecked_lines(self):
        """Lines with include=False are not uploaded."""
        att = self._make_attachment('Skip_Me.pdf')
        wizard = self.env['freight.dsv.doc.upload.wizard'].create({
            'po_id': self.po.id,
            'line_ids': [(0, 0, {
                'attachment_id': att.id,
                'dsv_type': 'GDS',
                'include': False,
            })],
        })
        wizard.action_upload()
        doc = self.env['freight.document'].search([
            ('booking_id', '=', self.booking.id),
            ('attachment_id', '=', att.id),
        ])
        self.assertFalse(doc)
```

**Step 2: Run to verify it fails**

```bash
python -m pytest addons/mml_freight_dsv/tests/test_dsv_doc_upload.py::TestDetectDsvType \
  addons/mml_freight_dsv/tests/test_dsv_doc_upload.py::TestDsvDocUploadWizard -v
```
Expected: FAIL — module not found.

**Step 3: Create `wizards/__init__.py`**

```python
from . import dsv_doc_upload_wizard
```

**Step 4: Create `wizards/dsv_doc_upload_wizard.py`**

```python
import base64
import logging
from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# DSV document type codes accepted by the Upload API
DSV_DOC_TYPES = [
    ('INV', 'Commercial Invoice'),
    ('PKL', 'Packing List'),
    ('CUS', 'Customs / Quarantine'),
    ('HAZ', 'Dangerous Goods'),
    ('GDS', 'Other Goods Doc'),
]

# Maps DSV upload type code back to freight.document.doc_type
_DSV_TYPE_TO_DOC_TYPE = {
    'INV': 'invoice',
    'PKL': 'packing_list',
    'CUS': 'customs',
    'HAZ': 'other',
    'GDS': 'other',
}

# Max file size DSV accepts (bytes)
_MAX_FILE_SIZE = 3 * 1024 * 1024  # 3 MB

# Keyword → DSV type detection rules (checked in order, first match wins)
_KEYWORD_TYPE_MAP = [
    (['pi', 'proforma', 'invoice', 'commercial'], 'INV'),
    (['packing', 'pkl'], 'PKL'),
    (['quarantine', 'quar', 'phyto', 'biosecurity'], 'CUS'),
    (['dangerous', 'dg', 'haz', 'msds'], 'HAZ'),
]


def detect_dsv_type(filename):
    """Detect DSV document type from filename. Returns 'GDS' as fallback.

    Case-insensitive. First matching keyword group wins.
    """
    name = (filename or '').lower()
    for keywords, dsv_type in _KEYWORD_TYPE_MAP:
        if any(kw in name for kw in keywords):
            return dsv_type
    return 'GDS'


class FreightDsvDocUploadWizardLine(models.TransientModel):
    _name = 'freight.dsv.doc.upload.wizard.line'
    _description = 'DSV Document Upload — Line'

    wizard_id = fields.Many2one(
        'freight.dsv.doc.upload.wizard', required=True, ondelete='cascade',
    )
    attachment_id = fields.Many2one('ir.attachment', required=True, ondelete='cascade')
    filename = fields.Char(related='attachment_id.name', readonly=True)
    file_size = fields.Integer(related='attachment_id.file_size', readonly=True)
    dsv_type = fields.Selection(DSV_DOC_TYPES, string='Document Type', required=True)
    include = fields.Boolean('Upload', default=True)
    size_warning = fields.Boolean(compute='_compute_size_warning', store=False)

    @api.depends('file_size')
    def _compute_size_warning(self):
        for line in self:
            line.size_warning = (line.file_size or 0) > _MAX_FILE_SIZE


class FreightDsvDocUploadWizard(models.TransientModel):
    _name = 'freight.dsv.doc.upload.wizard'
    _description = 'Send Documents to DSV'

    po_id = fields.Many2one('purchase.order', required=True, readonly=True)
    booking_id = fields.Many2one('freight.booking', compute='_compute_booking', store=False)
    line_ids = fields.One2many(
        'freight.dsv.doc.upload.wizard.line', 'wizard_id', string='Documents',
    )

    @api.depends('po_id')
    def _compute_booking(self):
        for w in self:
            w.booking_id = self.env['freight.booking'].search([
                ('po_ids', 'in', w.po_id.id),
                ('carrier_id.delivery_type', 'in', ('dsv_generic', 'dsv_xpress')),
                ('state', 'not in', ('delivered', 'cancelled', 'received')),
            ], limit=1)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        po_id = self.env.context.get('default_po_id') or self.env.context.get('active_id')
        if not po_id:
            return res
        attachments = self.env['ir.attachment'].search([
            ('res_model', '=', 'purchase.order'),
            ('res_id', '=', po_id),
        ])
        lines = []
        for att in attachments:
            oversized = (att.file_size or 0) > _MAX_FILE_SIZE
            lines.append({
                'attachment_id': att.id,
                'dsv_type': detect_dsv_type(att.name or ''),
                'include': not oversized,
            })
        res['line_ids'] = [(0, 0, line) for line in lines]
        res['po_id'] = po_id
        return res

    def action_upload(self):
        """Upload selected documents to DSV and log results on PO chatter."""
        booking = self.booking_id
        if not booking:
            raise UserError(
                'No active DSV booking found for this purchase order. '
                'Create and confirm a freight booking first.'
            )
        registry = self.env['freight.adapter.registry']
        adapter = registry.get_adapter(booking.carrier_id)
        if not adapter:
            raise UserError(f'No adapter registered for carrier {booking.carrier_id.name}.')

        results = []
        for line in self.line_ids.filtered('include'):
            att = line.attachment_id
            if (att.file_size or 0) > _MAX_FILE_SIZE:
                size_mb = (att.file_size or 0) / (1024 * 1024)
                results.append(
                    f'✗ {att.name} — Skipped (file is {size_mb:.1f} MB, limit is 3 MB)'
                )
                continue
            try:
                file_bytes = base64.b64decode(att.datas or b'')
            except Exception as e:
                results.append(f'✗ {att.name} — Could not read file: {e}')
                continue
            try:
                ref = adapter.upload_document(booking, att.name, file_bytes, line.dsv_type)
            except Exception as e:
                _logger.error('DSV doc upload exception for %s: %s', att.name, e, exc_info=True)
                ref = None

            label = dict(DSV_DOC_TYPES).get(line.dsv_type, line.dsv_type)
            if ref is not None:
                self.env['freight.document'].create({
                    'booking_id': booking.id,
                    'doc_type': _DSV_TYPE_TO_DOC_TYPE.get(line.dsv_type, 'other'),
                    'attachment_id': att.id,
                    'carrier_doc_ref': ref,
                    'uploaded_to_carrier': True,
                })
                results.append(f'✓ {att.name} → {label}')
            else:
                results.append(f'✗ {att.name} → {label} (upload failed)')

        body = (
            f'<b>Documents sent to DSV</b> (booking {booking.name}):<br/>'
            + '<br/>'.join(f'&nbsp;&nbsp;{r}' for r in results)
        )
        self.po_id.message_post(body=body)
        return {'type': 'ir.actions.act_window_close'}
```

**Step 5: Run tests**

```bash
python -m pytest addons/mml_freight_dsv/tests/test_dsv_doc_upload.py::TestDetectDsvType \
  addons/mml_freight_dsv/tests/test_dsv_doc_upload.py::TestDsvDocUploadWizard -v
```
Expected: all PASS.

**Step 6: Commit**

```bash
git add addons/mml_freight_dsv/wizards/__init__.py \
        addons/mml_freight_dsv/wizards/dsv_doc_upload_wizard.py \
        addons/mml_freight_dsv/tests/test_dsv_doc_upload.py
git commit -m "feat(mml_freight_dsv): add DSV doc upload wizard model with keyword detection"
```

---

## Task 7: Wizard view, PO button, and PO extension model

**Files to create:**
- `addons/mml_freight_dsv/views/dsv_doc_upload_wizard_views.xml`
- `addons/mml_freight_dsv/views/purchase_order_dsv_views.xml`
- `addons/mml_freight_dsv/models/purchase_order_dsv.py` (PO extension for computed field + action)

**Step 1: Create the PO extension model**

Create `addons/mml_freight_dsv/models/purchase_order_dsv.py`:

```python
from odoo import models, fields, api


class PurchaseOrderDsv(models.Model):
    _inherit = 'purchase.order'

    x_dsv_booking_id = fields.Many2one(
        'freight.booking',
        string='Active DSV Booking',
        compute='_compute_dsv_booking',
        store=False,
    )

    @api.depends('name')  # recomputes on save; good enough for a wizard trigger
    def _compute_dsv_booking(self):
        for po in self:
            po.x_dsv_booking_id = self.env['freight.booking'].search([
                ('po_ids', 'in', po.id),
                ('carrier_id.delivery_type', 'in', ('dsv_generic', 'dsv_xpress')),
                ('state', 'not in', ('delivered', 'cancelled', 'received')),
            ], limit=1)

    def action_open_dsv_doc_upload(self):
        """Open the Send Documents to DSV wizard."""
        return {
            'type': 'ir.actions.act_window',
            'name': 'Send Documents to DSV',
            'res_model': 'freight.dsv.doc.upload.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_po_id': self.id,
                'active_id': self.id,
            },
        }
```

**Step 2: Add to `addons/mml_freight_dsv/models/__init__.py`**

Add:
```python
from . import purchase_order_dsv
```

**Step 3: Create wizard view**

Create `addons/mml_freight_dsv/views/dsv_doc_upload_wizard_views.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_dsv_doc_upload_wizard_form" model="ir.ui.view">
        <field name="name">freight.dsv.doc.upload.wizard.form</field>
        <field name="model">freight.dsv.doc.upload.wizard</field>
        <field name="arch" type="xml">
            <form string="Send Documents to DSV">
                <sheet>
                    <group>
                        <field name="po_id" readonly="1"/>
                        <field name="booking_id" readonly="1"/>
                    </group>
                    <field name="line_ids">
                        <list editable="bottom">
                            <field name="include" string="Upload"/>
                            <field name="filename" readonly="1"/>
                            <field name="dsv_type"/>
                            <field name="file_size" string="Size (bytes)" readonly="1"/>
                            <field name="size_warning" column_invisible="1"/>
                        </list>
                    </field>
                    <group invisible="not line_ids">
                        <div class="text-muted">
                            Files over 3 MB are pre-unchecked. DSV uploads are permanent and cannot be deleted.
                        </div>
                    </group>
                </sheet>
                <footer>
                    <button name="action_upload" type="object" string="Send to DSV"
                            class="btn-primary"/>
                    <button string="Cancel" class="btn-secondary" special="cancel"/>
                </footer>
            </form>
        </field>
    </record>
</odoo>
```

**Step 4: Create PO button view**

Create `addons/mml_freight_dsv/views/purchase_order_dsv_views.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="view_purchase_order_dsv_button" model="ir.ui.view">
        <field name="name">purchase.order.form.dsv.button</field>
        <field name="model">purchase.order</field>
        <field name="inherit_id" ref="purchase.purchase_order_form"/>
        <field name="arch" type="xml">
            <xpath expr="//div[@name='button_box']" position="inside">
                <button name="action_open_dsv_doc_upload"
                        type="object"
                        class="oe_stat_button"
                        icon="fa-upload"
                        string="Send to DSV"
                        invisible="not x_dsv_booking_id"/>
            </xpath>
        </field>
    </record>
</odoo>
```

**Step 5: Commit**

```bash
git add addons/mml_freight_dsv/models/purchase_order_dsv.py \
        addons/mml_freight_dsv/models/__init__.py \
        addons/mml_freight_dsv/views/dsv_doc_upload_wizard_views.xml \
        addons/mml_freight_dsv/views/purchase_order_dsv_views.xml
git commit -m "feat(mml_freight_dsv): add Send to DSV wizard view and PO smart button"
```

---

## Task 8: Update manifest and security

**Files to modify:**
- `addons/mml_freight_dsv/__manifest__.py`
- `addons/mml_freight_dsv/security/ir.model.access.csv`

**Step 1: Update `__manifest__.py`**

```python
{
    'name': 'MML Freight — DSV Adapter',
    'version': '19.0.1.1.0',
    'category': 'Inventory/Inventory',
    'summary': 'DSV Generic and XPress carrier adapters for MML freight orchestration',
    'author': 'MML',
    'license': 'OPL-1',
    'depends': ['mml_freight', 'purchase'],
    'data': [
        'security/ir.model.access.csv',
        'views/freight_carrier_dsv_views.xml',
        'views/dsv_doc_upload_wizard_views.xml',
        'views/purchase_order_dsv_views.xml',
    ],
    'installable': True,
    'auto_install': False,
}
```

**Step 2: Update `security/ir.model.access.csv`**

```csv
id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink
access_dsv_doc_upload_wizard,freight.dsv.doc.upload.wizard user,model_freight_dsv_doc_upload_wizard,base.group_user,1,1,1,1
access_dsv_doc_upload_wizard_line,freight.dsv.doc.upload.wizard.line user,model_freight_dsv_doc_upload_wizard_line,base.group_user,1,1,1,1
```

**Step 3: Restart Odoo and upgrade module**

```bash
# In your Odoo dev environment:
./odoo-bin -u mml_freight_dsv -d your_db --stop-after-init
```

**Step 4: Commit**

```bash
git add addons/mml_freight_dsv/__manifest__.py \
        addons/mml_freight_dsv/security/ir.model.access.csv
git commit -m "chore(mml_freight_dsv): add wizard models to manifest and security"
```

---

## Task 9: Run full test suite and verify

**Step 1: Run all DSV tests**

```bash
python -m pytest addons/mml_freight_dsv/tests/ -v
```
Expected: all PASS. If any test fails with `x_dsv_subscription_key`, it was missed in Task 1 — apply the same fix.

**Step 2: Run mml_freight tests**

```bash
python -m pytest addons/mml_freight/tests/ -v
```
Expected: all PASS.

**Step 3: Final commit if anything was fixed**

```bash
git add -A
git commit -m "fix(tests): ensure all tests pass after doc upload sprint"
```

---

## Verification Checklist

Before calling this sprint done:

- [ ] All 6 subscription key test files updated (Task 1)
- [ ] `freight.document` has `packing_list`, `quarantine`, `uploaded_to_carrier`, `carrier_upload_ref`
- [ ] `base_adapter.upload_document()` returns `None` by default
- [ ] `DsvGenericAdapter.upload_document()` POSTs multipart to `/my/upload/v1/...`
- [ ] Upload uses `doc_upload` subscription key (not booking or other)
- [ ] 401 triggers token refresh + retry
- [ ] `DsvMockAdapter.upload_document()` returns mock ref in demo, delegates in production
- [ ] Keyword detection: PI→INV, packing→PKL, quarantine→CUS, unknown→GDS
- [ ] Oversized files (>3MB) pre-unchecked in wizard, skipped in `action_upload`
- [ ] Successful upload creates `freight.document` with `uploaded_to_carrier=True`
- [ ] Failed upload logged on PO chatter, other docs still processed (non-blocking)
- [ ] "Send to DSV" button only visible on PO when active DSV booking exists
- [ ] Manifest includes `purchase` dependency + both new views
- [ ] Security CSV covers both transient models

---

## Open Question (confirm against demo sandbox)

The exact DSV Upload API endpoint path is documented as **auth-walled** in our API guide. The implementation uses:
```
POST /my/upload/v1/shipments/{carrier_booking_id}/documents
```
Confirm this path and the response schema (`documentId` field name) against the DSV demo sandbox once the `doc_upload` subscription keys are active. Update `dsv_generic_adapter.py` if the path differs.
