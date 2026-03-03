# Phase 4 Implementation Plan — DSV Documents, Invoices & Landed Costs

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add DSV label/document fetching, freight invoice retrieval, DSV invoice webhook handling, and landed cost creation from confirmed bookings.

**Architecture:** Five independent tasks that extend `freight.booking` with new action methods and extend the DSV adapters with new API calls. No new Odoo models are introduced. The `stock.landed.cost` model from `stock_account` is used for Task 4. Mainfreight 3PL payload building is already implemented via `_build_inward_order_payload()` and is out of scope here (lives in the separate mainfreight.3pl.intergration project).

**Tech Stack:** Odoo 19, Python, `requests`, `base64`, DSV Generic API (label print, document download, invoice API), `stock.landed.cost`.

**Key files to understand before starting:**
- `addons/mml_freight/models/freight_booking.py` — booking model + existing DSV webhook handler
- `addons/mml_freight/models/freight_document.py` — thin `freight.document` model
- `addons/mml_freight/adapters/base_adapter.py` — adapter interface; `get_label()` is already a no-op
- `addons/mml_freight_dsv/adapters/dsv_generic_adapter.py` — live DSV adapter (HTTP calls)
- `addons/mml_freight_dsv/adapters/dsv_mock_adapter.py` — registered adapter; routes to live in prod, mock in demo
- `addons/mml_freight_dsv/controllers/dsv_webhook.py` — webhook controller; currently handles `TRACKING_UPDATE` only
- `addons/mml_freight/views/freight_booking_views.xml` — booking form + Documents notebook page

**DSV API endpoints used in this phase:**
```
GET  /printing/v1/labels/{bookingId}?printFormat=Portrait1Label     # Label PDF
GET  /download/v1/shipments/bookingId/{bookingId}/documents         # List docs
GET  {downloadUrl}                                                  # Download individual doc
GET  /invoice/v1/invoices/shipments/{shipmentId}                    # Invoice by shipment
```
Base: `https://api.dsv.com` (env switching not yet implemented; follows existing adapter pattern)

---

## Task 1: DSV Label Fetch

**Files:**
- Modify: `addons/mml_freight/adapters/base_adapter.py`
- Modify: `addons/mml_freight_dsv/adapters/dsv_generic_adapter.py`
- Modify: `addons/mml_freight_dsv/adapters/dsv_mock_adapter.py`
- Modify: `addons/mml_freight/models/freight_booking.py`
- Modify: `addons/mml_freight/views/freight_booking_views.xml`
- Create: `addons/mml_freight/tests/test_fetch_label.py`
- Create: `addons/mml_freight_dsv/tests/test_dsv_label.py`

---

**Step 1: Write failing tests**

`addons/mml_freight/tests/test_fetch_label.py`:
```python
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase


class TestFetchLabel(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Test Label Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })
        cls.tender = cls.env['freight.tender'].create({
            'purchase_order_id': cls.env['purchase.order'].search([], limit=1).id
                                  or cls._make_po(),
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': cls.tender.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_booking_id': 'BK-TEST-001',
        })

    @classmethod
    def _make_po(cls):
        supplier = cls.env['res.partner'].create({'name': 'Label Test Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        return po.id

    def test_action_fetch_label_creates_attachment(self):
        """action_fetch_label stores bytes as ir.attachment and sets label_attachment_id."""
        fake_bytes = b'%PDF-1.4 fake label content'
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=MagicMock(get_label=MagicMock(return_value=fake_bytes)),
        ):
            self.booking.action_fetch_label()
        self.assertTrue(self.booking.label_attachment_id, 'label_attachment_id must be set')
        self.assertIn('label', self.booking.label_attachment_id.name.lower())

    def test_action_fetch_label_creates_freight_document(self):
        """action_fetch_label creates a freight.document record with doc_type='label'."""
        fake_bytes = b'%PDF-1.4 fake label'
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=MagicMock(get_label=MagicMock(return_value=fake_bytes)),
        ):
            self.booking.action_fetch_label()
        label_doc = self.booking.document_ids.filtered(lambda d: d.doc_type == 'label')
        self.assertTrue(label_doc, 'freight.document with doc_type=label must exist')
        self.assertEqual(label_doc[0].attachment_id, self.booking.label_attachment_id)

    def test_action_fetch_label_raises_when_no_bytes(self):
        """action_fetch_label raises UserError when adapter returns None."""
        from odoo.exceptions import UserError
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=MagicMock(get_label=MagicMock(return_value=None)),
        ):
            with self.assertRaises(UserError):
                self.booking.action_fetch_label()

    def test_action_fetch_label_idempotent(self):
        """Second call updates existing freight.document rather than creating a duplicate."""
        fake_bytes = b'%PDF-1.4 v2'
        mock_adapter = MagicMock(get_label=MagicMock(return_value=fake_bytes))
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=mock_adapter,
        ):
            self.booking.action_fetch_label()
            self.booking.action_fetch_label()
        label_docs = self.booking.document_ids.filtered(lambda d: d.doc_type == 'label')
        self.assertEqual(len(label_docs), 1, 'Must not create duplicate label document')
```

`addons/mml_freight_dsv/tests/test_dsv_label.py`:
```python
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase


class TestDsvLabel(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Label Test',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
            'x_dsv_environment': 'production',
        })
        supplier = cls.env['res.partner'].create({'name': 'DSV Label Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_booking_id': 'BK-LIVE-001',
            'tender_id': cls.env['freight.tender'].create({
                'purchase_order_id': po.id,
                'company_id': cls.env.company.id,
                'currency_id': cls.env.company.currency_id.id,
            }).id,
        })

    def test_get_label_returns_bytes_on_200(self):
        """DsvGenericAdapter.get_label returns bytes when API returns 200 PDF."""
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter
        adapter = DsvGenericAdapter(self.carrier, self.env)
        fake_pdf = b'%PDF-1.4 label bytes'
        mock_resp = MagicMock(ok=True, content=fake_pdf, status_code=200)
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='tok'), \
             patch('requests.get', return_value=mock_resp):
            result = adapter.get_label(self.booking)
        self.assertEqual(result, fake_pdf)

    def test_get_label_returns_none_on_404(self):
        """DsvGenericAdapter.get_label returns None when API returns 404."""
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter
        adapter = DsvGenericAdapter(self.carrier, self.env)
        mock_resp = MagicMock(ok=False, status_code=404)
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='tok'), \
             patch('requests.get', return_value=mock_resp):
            result = adapter.get_label(self.booking)
        self.assertIsNone(result)

    def test_get_label_returns_none_when_no_booking_id(self):
        """DsvGenericAdapter.get_label returns None immediately when carrier_booking_id is unset."""
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter
        adapter = DsvGenericAdapter(self.carrier, self.env)
        self.booking.carrier_booking_id = ''
        result = adapter.get_label(self.booking)
        self.assertIsNone(result)
```

**Step 2: Run tests to confirm they fail**

```bash
cd /e/ClaudeCode/projects/mml.odoo.apps/fowarder.intergration
python -m pytest addons/mml_freight/tests/test_fetch_label.py addons/mml_freight_dsv/tests/test_dsv_label.py -v 2>&1 | head -40
```
Expected: errors (methods don't exist yet)

**Step 3: Implement `get_label()` in DSV adapters**

In `addons/mml_freight_dsv/adapters/dsv_generic_adapter.py`, add after the existing URL constants:
```python
DSV_LABEL_URL = f'{DSV_BASE}/printing/v1/labels/{{booking_id}}'
```

Add method to `DsvGenericAdapter`:
```python
def get_label(self, booking):
    """Download PDF shipping label. Returns bytes or None (no label available / error)."""
    bk_id = booking.carrier_booking_id
    if not bk_id:
        return None
    try:
        token = get_token(self.carrier)
    except DsvAuthError as e:
        _logger.warning('DSV label auth failed for %s: %s', booking.name, e)
        return None
    url = DSV_LABEL_URL.format(booking_id=bk_id)
    headers = {**self._headers(token), 'Accept': 'application/pdf'}
    try:
        resp = requests.get(url, params={'printFormat': 'Portrait1Label'}, headers=headers, timeout=30)
    except Exception as e:
        _logger.warning('DSV label GET failed for %s: %s', booking.name, e)
        return None
    if not resp.ok:
        _logger.warning('DSV label HTTP %s for %s', resp.status_code, booking.name)
        return None
    return resp.content
```

In `addons/mml_freight_dsv/adapters/dsv_mock_adapter.py`, add inside `DsvMockAdapter`:
```python
def get_label(self, booking):
    if not self._demo():
        return self._live().get_label(booking)
    return b'%PDF-1.4-mock-label'
```

In `addons/mml_freight/models/freight_booking.py`, add `import base64` to the top imports block.

Add method to `FreightBooking`:
```python
def action_fetch_label(self):
    """Fetch shipping label from carrier and store as attachment."""
    self.ensure_one()
    adapter = self.env['freight.adapter.registry'].get_adapter(self.carrier_id)
    if not adapter:
        raise UserError('No adapter available for this carrier.')
    label_bytes = adapter.get_label(self)
    if not label_bytes:
        raise UserError('No label available from carrier yet. Try again later.')
    attachment = self.env['ir.attachment'].create({
        'name': f'{self.name}-label.pdf',
        'type': 'binary',
        'datas': base64.b64encode(label_bytes).decode(),
        'res_model': 'freight.booking',
        'res_id': self.id,
        'mimetype': 'application/pdf',
    })
    self.label_attachment_id = attachment
    existing = self.document_ids.filtered(lambda d: d.doc_type == 'label')
    if existing:
        existing[0].write({'attachment_id': attachment.id})
    else:
        self.env['freight.document'].create({
            'booking_id': self.id,
            'doc_type': 'label',
            'attachment_id': attachment.id,
        })
    self.message_post(body='Shipping label fetched and attached.')
    return True
```

**Step 4: Add "Fetch Label" button to booking form view**

In `addons/mml_freight/views/freight_booking_views.xml`, in the `<header>` block of `view_freight_booking_form`, add after the existing buttons:
```xml
<button name="action_fetch_label"
        string="Fetch Label"
        type="object"
        class="btn-secondary"
        invisible="not carrier_booking_id or state in ('cancelled',)"/>
```

**Step 5: Run tests — expect PASS**

```bash
python -m pytest addons/mml_freight/tests/test_fetch_label.py addons/mml_freight_dsv/tests/test_dsv_label.py -v
```
Expected: all green

**Step 6: Commit**

```bash
git add addons/mml_freight/adapters/base_adapter.py \
        addons/mml_freight_dsv/adapters/dsv_generic_adapter.py \
        addons/mml_freight_dsv/adapters/dsv_mock_adapter.py \
        addons/mml_freight/models/freight_booking.py \
        addons/mml_freight/views/freight_booking_views.xml \
        addons/mml_freight/tests/test_fetch_label.py \
        addons/mml_freight_dsv/tests/test_dsv_label.py
git commit -m "feat: DSV label fetch — action_fetch_label + get_label adapter method"
```

---

## Task 2: DSV Document Download

**Files:**
- Modify: `addons/mml_freight/adapters/base_adapter.py` — add `get_documents()` no-op
- Modify: `addons/mml_freight_dsv/adapters/dsv_generic_adapter.py` — implement `get_documents()`
- Modify: `addons/mml_freight_dsv/adapters/dsv_mock_adapter.py` — mock `get_documents()`
- Modify: `addons/mml_freight/models/freight_booking.py` — add `action_fetch_documents()`
- Modify: `addons/mml_freight/views/freight_booking_views.xml` — add button
- Create: `addons/mml_freight/tests/test_fetch_documents.py`
- Create: `addons/mml_freight_dsv/tests/test_dsv_documents.py`

---

**Step 1: Write failing tests**

`addons/mml_freight/tests/test_fetch_documents.py`:
```python
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase


class TestFetchDocuments(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Doc Test Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })
        supplier = cls.env['res.partner'].create({'name': 'Doc Test Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        tender = cls.env['freight.tender'].create({
            'purchase_order_id': po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': tender.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_booking_id': 'BK-DOC-001',
        })

    def _mock_docs(self):
        return [
            {'doc_type': 'pod', 'bytes': b'%PDF-pod', 'filename': 'POD.pdf', 'carrier_doc_ref': 'DOC-POD-001'},
            {'doc_type': 'invoice', 'bytes': b'%PDF-inv', 'filename': 'Invoice.pdf', 'carrier_doc_ref': 'DOC-INV-001'},
        ]

    def test_action_fetch_documents_creates_freight_documents(self):
        """action_fetch_documents creates freight.document records for each doc."""
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=MagicMock(get_documents=MagicMock(return_value=self._mock_docs())),
        ):
            self.booking.action_fetch_documents()
        self.assertEqual(len(self.booking.document_ids), 2)
        types = self.booking.document_ids.mapped('doc_type')
        self.assertIn('pod', types)
        self.assertIn('invoice', types)

    def test_action_fetch_documents_sets_pod_attachment(self):
        """POD document sets pod_attachment_id on the booking."""
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=MagicMock(get_documents=MagicMock(return_value=self._mock_docs())),
        ):
            self.booking.action_fetch_documents()
        self.assertTrue(self.booking.pod_attachment_id, 'pod_attachment_id must be set')

    def test_action_fetch_documents_raises_when_empty(self):
        """Raises UserError when adapter returns no documents."""
        from odoo.exceptions import UserError
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=MagicMock(get_documents=MagicMock(return_value=[])),
        ):
            with self.assertRaises(UserError):
                self.booking.action_fetch_documents()

    def test_action_fetch_documents_idempotent(self):
        """Second call updates existing freight.document (same carrier_doc_ref), not duplicates."""
        mock_adapter = MagicMock(get_documents=MagicMock(return_value=[
            {'doc_type': 'pod', 'bytes': b'%PDF-v1', 'filename': 'POD.pdf', 'carrier_doc_ref': 'DOC-POD-001'},
        ]))
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter', return_value=mock_adapter,
        ):
            self.booking.action_fetch_documents()
            self.booking.action_fetch_documents()
        pod_docs = self.booking.document_ids.filtered(lambda d: d.doc_type == 'pod' and d.carrier_doc_ref == 'DOC-POD-001')
        self.assertEqual(len(pod_docs), 1, 'Must not duplicate freight.document with same ref')
```

`addons/mml_freight_dsv/tests/test_dsv_documents.py`:
```python
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase


class TestDsvDocuments(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Doc Test',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
            'x_dsv_environment': 'production',
        })
        supplier = cls.env['res.partner'].create({'name': 'DSV Doc Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        tender = cls.env['freight.tender'].create({
            'purchase_order_id': po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': tender.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_booking_id': 'BK-LIVE-DOC',
        })

    def test_get_documents_returns_downloaded_docs(self):
        """DsvGenericAdapter.get_documents lists then downloads each doc."""
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter
        adapter = DsvGenericAdapter(self.carrier, self.env)
        list_response = MagicMock(ok=True, status_code=200)
        list_response.json.return_value = [
            {'documentType': 'POD', 'downloadUrl': 'https://dsv.com/dl/pod1', 'documentId': 'DOC1', 'fileName': 'pod.pdf'},
        ]
        dl_response = MagicMock(ok=True, content=b'%PDF-pod-bytes')
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='tok'), \
             patch('requests.get', side_effect=[list_response, dl_response]):
            result = adapter.get_documents(self.booking)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['doc_type'], 'pod')
        self.assertEqual(result[0]['bytes'], b'%PDF-pod-bytes')
        self.assertEqual(result[0]['carrier_doc_ref'], 'DOC1')

    def test_get_documents_returns_empty_on_api_error(self):
        """DsvGenericAdapter.get_documents returns [] on non-200 list response."""
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter
        adapter = DsvGenericAdapter(self.carrier, self.env)
        mock_resp = MagicMock(ok=False, status_code=503)
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='tok'), \
             patch('requests.get', return_value=mock_resp):
            result = adapter.get_documents(self.booking)
        self.assertEqual(result, [])
```

**Step 2: Run tests — expect failures (methods don't exist)**

```bash
python -m pytest addons/mml_freight/tests/test_fetch_documents.py addons/mml_freight_dsv/tests/test_dsv_documents.py -v 2>&1 | head -30
```

**Step 3: Implement `get_documents()` in `base_adapter.py`**

Add after `get_label()`:
```python
def get_documents(self, booking):
    """Return list of document dicts: {doc_type, bytes, filename, carrier_doc_ref}.
    Optional — returns empty list by default. Override in adapters that support document download.
    """
    return []
```

**Step 4: Implement `get_documents()` in `dsv_generic_adapter.py`**

Add URL constant after `DSV_LABEL_URL`:
```python
DSV_DOC_LIST_URL = f'{DSV_BASE}/download/v1/shipments/bookingId/{{booking_id}}/documents'
```

Add mapping dict after `_DSV_PRODUCT_TYPE_TO_MODE`:
```python
_DSV_DOC_TYPE_MAP = {
    'POD':                  'pod',
    'COMMERCIAL_INVOICE':   'invoice',
    'CUSTOMS_DECLARATION':  'customs',
    'PACKING_LIST':         'other',
    'HOUSE_BILL_OF_LADING': 'other',
    'DANGEROUS_GOODS':      'other',
    'GOODS_DOCUMENTS':      'other',
}
```

Add method to `DsvGenericAdapter`:
```python
def get_documents(self, booking):
    """List and download all available documents for this booking. Returns [] on any error."""
    bk_id = booking.carrier_booking_id
    if not bk_id:
        return []
    try:
        token = get_token(self.carrier)
    except DsvAuthError as e:
        _logger.warning('DSV doc list auth failed for %s: %s', booking.name, e)
        return []
    url = DSV_DOC_LIST_URL.format(booking_id=bk_id)
    try:
        resp = requests.get(url, headers=self._headers(token), timeout=30)
    except Exception as e:
        _logger.warning('DSV doc list GET failed for %s: %s', booking.name, e)
        return []
    if not resp.ok:
        _logger.warning('DSV doc list HTTP %s for %s', resp.status_code, booking.name)
        return []
    docs = []
    for raw in (resp.json() or []):
        download_url = raw.get('downloadUrl', '')
        if not download_url:
            continue
        doc_type = _DSV_DOC_TYPE_MAP.get(raw.get('documentType', ''), 'other')
        try:
            dl = requests.get(download_url, headers=self._headers(token), timeout=30)
        except Exception as e:
            _logger.warning('DSV doc download failed (%s) for %s: %s', raw.get('documentId'), booking.name, e)
            continue
        if not dl.ok:
            _logger.warning('DSV doc download HTTP %s for doc %s', dl.status_code, raw.get('documentId'))
            continue
        docs.append({
            'doc_type':        doc_type,
            'bytes':           dl.content,
            'filename':        raw.get('fileName', f'doc-{doc_type}.pdf'),
            'carrier_doc_ref': raw.get('documentId', ''),
        })
    return docs
```

**Step 5: Add mock `get_documents()` to `dsv_mock_adapter.py`**

```python
def get_documents(self, booking):
    if not self._demo():
        return self._live().get_documents(booking)
    return [
        {'doc_type': 'pod', 'bytes': b'%PDF-1.4-mock-pod', 'filename': 'POD-mock.pdf',
         'carrier_doc_ref': 'MOCK-POD-001'},
    ]
```

**Step 6: Implement `action_fetch_documents()` in `freight_booking.py`**

```python
def action_fetch_documents(self):
    """Fetch all available documents from carrier and store as attachments."""
    self.ensure_one()
    adapter = self.env['freight.adapter.registry'].get_adapter(self.carrier_id)
    if not adapter:
        raise UserError('No adapter available for this carrier.')
    documents = adapter.get_documents(self)
    if not documents:
        raise UserError('No documents available from carrier yet. Try again later.')
    count = 0
    for doc in documents:
        attachment = self.env['ir.attachment'].create({
            'name':      doc['filename'],
            'type':      'binary',
            'datas':     base64.b64encode(doc['bytes']).decode(),
            'res_model': 'freight.booking',
            'res_id':    self.id,
        })
        existing = self.document_ids.filtered(
            lambda d: d.doc_type == doc['doc_type'] and d.carrier_doc_ref == doc['carrier_doc_ref']
        )
        if existing:
            existing[0].write({'attachment_id': attachment.id})
        else:
            self.env['freight.document'].create({
                'booking_id':      self.id,
                'doc_type':        doc['doc_type'],
                'attachment_id':   attachment.id,
                'carrier_doc_ref': doc['carrier_doc_ref'],
            })
        if doc['doc_type'] == 'pod':
            self.pod_attachment_id = attachment
        count += 1
    self.message_post(body=f'{count} document(s) fetched from carrier and attached.')
    return True
```

**Step 7: Add "Fetch Documents" button to booking form view**

In `freight_booking_views.xml`, in the `<header>` block, add after the "Fetch Label" button:
```xml
<button name="action_fetch_documents"
        string="Fetch Documents"
        type="object"
        class="btn-secondary"
        invisible="not carrier_booking_id or state in ('cancelled',)"/>
```

**Step 8: Run tests — expect PASS**

```bash
python -m pytest addons/mml_freight/tests/test_fetch_documents.py addons/mml_freight_dsv/tests/test_dsv_documents.py -v
```

**Step 9: Commit**

```bash
git add addons/mml_freight/adapters/base_adapter.py \
        addons/mml_freight_dsv/adapters/dsv_generic_adapter.py \
        addons/mml_freight_dsv/adapters/dsv_mock_adapter.py \
        addons/mml_freight/models/freight_booking.py \
        addons/mml_freight/views/freight_booking_views.xml \
        addons/mml_freight/tests/test_fetch_documents.py \
        addons/mml_freight_dsv/tests/test_dsv_documents.py
git commit -m "feat: DSV document download — get_documents adapter + action_fetch_documents"
```

---

## Task 3: DSV Invoice Fetch + `actual_rate`

**Files:**
- Modify: `addons/mml_freight/adapters/base_adapter.py` — add `get_invoice()` no-op
- Modify: `addons/mml_freight_dsv/adapters/dsv_generic_adapter.py` — implement `get_invoice()`
- Modify: `addons/mml_freight_dsv/adapters/dsv_mock_adapter.py` — mock `get_invoice()`
- Modify: `addons/mml_freight/models/freight_booking.py` — add `action_fetch_invoice()`
- Modify: `addons/mml_freight/views/freight_booking_views.xml` — add button
- Create: `addons/mml_freight/tests/test_fetch_invoice.py`
- Create: `addons/mml_freight_dsv/tests/test_dsv_invoice.py`

---

**Step 1: Write failing tests**

`addons/mml_freight/tests/test_fetch_invoice.py`:
```python
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase


class TestFetchInvoice(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Invoice Test Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })
        supplier = cls.env['res.partner'].create({'name': 'Invoice Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        tender = cls.env['freight.tender'].create({
            'purchase_order_id': po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': tender.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_shipment_id': 'SH-TEST-001',
        })

    def test_action_fetch_invoice_sets_actual_rate(self):
        """action_fetch_invoice sets actual_rate from adapter response."""
        invoice_data = {
            'dsv_invoice_id': 'INV-001',
            'amount': 1950.00,
            'currency': 'NZD',
            'invoice_date': '2026-03-01',
        }
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=MagicMock(get_invoice=MagicMock(return_value=invoice_data)),
        ):
            self.booking.action_fetch_invoice()
        self.assertAlmostEqual(self.booking.actual_rate, 1950.00, places=2)

    def test_action_fetch_invoice_raises_when_no_data(self):
        """action_fetch_invoice raises UserError when adapter returns None (not yet invoiced)."""
        from odoo.exceptions import UserError
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=MagicMock(get_invoice=MagicMock(return_value=None)),
        ):
            with self.assertRaises(UserError):
                self.booking.action_fetch_invoice()

    def test_action_fetch_invoice_posts_chatter_note(self):
        """action_fetch_invoice posts a chatter note with invoice details."""
        invoice_data = {
            'dsv_invoice_id': 'INV-002',
            'amount': 2100.00,
            'currency': 'NZD',
            'invoice_date': '2026-03-01',
        }
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=MagicMock(get_invoice=MagicMock(return_value=invoice_data)),
        ):
            self.booking.action_fetch_invoice()
        bodies = self.booking.message_ids.mapped('body')
        self.assertTrue(
            any('INV-002' in (b or '') for b in bodies),
            'Chatter note must contain the DSV invoice ID',
        )
```

`addons/mml_freight_dsv/tests/test_dsv_invoice.py`:
```python
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase


class TestDsvInvoice(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Invoice Test',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
            'x_dsv_environment': 'production',
        })
        supplier = cls.env['res.partner'].create({'name': 'DSV Inv Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        tender = cls.env['freight.tender'].create({
            'purchase_order_id': po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': tender.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_shipment_id': 'SCPH-TEST',
        })

    def test_get_invoice_returns_dict_on_200(self):
        """DsvGenericAdapter.get_invoice parses amount and invoice ID from 200 response."""
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter
        adapter = DsvGenericAdapter(self.carrier, self.env)
        mock_resp = MagicMock(ok=True, status_code=200)
        mock_resp.json.return_value = {
            'invoiceId': 'DSV-INV-999',
            'totalAmount': 2345.67,
            'currency': 'USD',
            'invoiceDate': '2026-02-28',
        }
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='tok'), \
             patch('requests.get', return_value=mock_resp):
            result = adapter.get_invoice(self.booking)
        self.assertIsNotNone(result)
        self.assertEqual(result['dsv_invoice_id'], 'DSV-INV-999')
        self.assertAlmostEqual(result['amount'], 2345.67, places=2)
        self.assertEqual(result['currency'], 'USD')

    def test_get_invoice_returns_none_on_404(self):
        """DsvGenericAdapter.get_invoice returns None for 404 (not yet invoiced)."""
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter
        adapter = DsvGenericAdapter(self.carrier, self.env)
        mock_resp = MagicMock(ok=False, status_code=404)
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='tok'), \
             patch('requests.get', return_value=mock_resp):
            result = adapter.get_invoice(self.booking)
        self.assertIsNone(result)

    def test_get_invoice_returns_none_when_no_shipment_id(self):
        """DsvGenericAdapter.get_invoice returns None immediately when carrier_shipment_id unset."""
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter
        adapter = DsvGenericAdapter(self.carrier, self.env)
        self.booking.carrier_shipment_id = ''
        result = adapter.get_invoice(self.booking)
        self.assertIsNone(result)
```

**Step 2: Run tests — expect failures**

```bash
python -m pytest addons/mml_freight/tests/test_fetch_invoice.py addons/mml_freight_dsv/tests/test_dsv_invoice.py -v 2>&1 | head -30
```

**Step 3: Implement in `base_adapter.py`**

Add after `get_documents()`:
```python
def get_invoice(self, booking):
    """Fetch invoice data from carrier. Returns dict or None (not yet invoiced / not supported).
    Dict keys: dsv_invoice_id (str), amount (float), currency (str ISO-4217), invoice_date (str).
    """
    return None
```

**Step 4: Implement `get_invoice()` in `dsv_generic_adapter.py`**

Add URL constant:
```python
DSV_INVOICE_BY_SHIPMENT_URL = f'{DSV_BASE}/invoice/v1/invoices/shipments/{{shipment_id}}'
```

Add method:
```python
def get_invoice(self, booking):
    """Fetch DSV freight invoice for this shipment. Returns dict or None (404 = not invoiced yet)."""
    shipment_id = booking.carrier_shipment_id
    if not shipment_id:
        return None
    try:
        token = get_token(self.carrier)
    except DsvAuthError as e:
        _logger.warning('DSV invoice auth failed for %s: %s', booking.name, e)
        return None
    url = DSV_INVOICE_BY_SHIPMENT_URL.format(shipment_id=shipment_id)
    try:
        resp = requests.get(url, headers=self._headers(token), timeout=30)
    except Exception as e:
        _logger.warning('DSV invoice GET failed for %s: %s', booking.name, e)
        return None
    if resp.status_code == 404:
        return None  # Not yet invoiced — caller treats this as "try again later"
    if not resp.ok:
        _logger.warning('DSV invoice HTTP %s for %s', resp.status_code, booking.name)
        return None
    data = resp.json()
    return {
        'dsv_invoice_id': data.get('invoiceId', ''),
        'amount':         float(data.get('totalAmount', 0)),
        'currency':       data.get('currency', 'NZD'),
        'invoice_date':   data.get('invoiceDate', ''),
    }
```

**Step 5: Add mock `get_invoice()` to `dsv_mock_adapter.py`**

```python
def get_invoice(self, booking):
    if not self._demo():
        return self._live().get_invoice(booking)
    from odoo import fields
    return {
        'dsv_invoice_id': 'MOCK-INV-001',
        'amount':         booking.booked_rate or 1800.00,
        'currency':       booking.currency_id.name if booking.currency_id else 'NZD',
        'invoice_date':   str(fields.Date.today()),
    }
```

**Step 6: Implement `action_fetch_invoice()` in `freight_booking.py`**

```python
def action_fetch_invoice(self):
    """Fetch freight invoice from carrier and update actual_rate."""
    self.ensure_one()
    adapter = self.env['freight.adapter.registry'].get_adapter(self.carrier_id)
    if not adapter:
        raise UserError('No adapter available for this carrier.')
    invoice_data = adapter.get_invoice(self)
    if not invoice_data:
        raise UserError('No invoice available for this shipment yet. Try again later.')
    curr = self.env['res.currency'].search(
        [('name', '=', invoice_data.get('currency', 'NZD'))], limit=1,
    ) or self.currency_id
    self.write({
        'actual_rate': invoice_data['amount'],
        'currency_id': curr.id if curr else self.currency_id.id,
    })
    self.message_post(
        body=(
            f"Freight invoice fetched: {invoice_data['amount']:.2f} "
            f"{invoice_data.get('currency', '')} "
            f"(DSV Invoice #{invoice_data.get('dsv_invoice_id', 'N/A')})"
        )
    )
    return True
```

**Step 7: Add "Fetch Invoice" button to booking form view**

In `freight_booking_views.xml`, `<header>` block, after "Fetch Documents":
```xml
<button name="action_fetch_invoice"
        string="Fetch Invoice"
        type="object"
        class="btn-secondary"
        invisible="not carrier_shipment_id or state in ('cancelled',)"/>
```

**Step 8: Run tests — expect PASS**

```bash
python -m pytest addons/mml_freight/tests/test_fetch_invoice.py addons/mml_freight_dsv/tests/test_dsv_invoice.py -v
```

**Step 9: Commit**

```bash
git add addons/mml_freight/adapters/base_adapter.py \
        addons/mml_freight_dsv/adapters/dsv_generic_adapter.py \
        addons/mml_freight_dsv/adapters/dsv_mock_adapter.py \
        addons/mml_freight/models/freight_booking.py \
        addons/mml_freight/views/freight_booking_views.xml \
        addons/mml_freight/tests/test_fetch_invoice.py \
        addons/mml_freight_dsv/tests/test_dsv_invoice.py
git commit -m "feat: DSV invoice fetch — get_invoice adapter + action_fetch_invoice"
```

---

## Task 4: Landed Cost Integration

**Files:**
- Modify: `addons/mml_freight/models/freight_booking.py` — add `landed_cost_id`, `_create_landed_cost()`, `action_create_landed_cost()`
- Modify: `addons/mml_freight/views/freight_booking_views.xml` — add field + button
- Modify: `addons/mml_freight/__manifest__.py` — ensure `stock_account` in depends (for `stock.landed.cost`)
- Create: `addons/mml_freight/tests/test_landed_cost.py`

**Critical context:** `stock.landed.cost` lives in the `stock_landed_costs` module (OCA) or is part of `stock_account` in Odoo 19. The booking must find a `done` receipt picking for the PO. The freight cost product is looked up from `ir.config_parameter` key `mml_freight.freight_cost_product_id` (stores `product.product` ID as string). Fallback: search for a service product named `'Freight Cost'`.

---

**Step 1: Write failing tests**

`addons/mml_freight/tests/test_landed_cost.py`:
```python
from unittest.mock import patch
from odoo.tests.common import TransactionCase


class TestLandedCost(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if 'stock.landed.cost' not in cls.env:
            return  # skip if stock_account not installed

        # Freight cost product
        cls.freight_product = cls.env['product.product'].create({
            'name': 'Freight Cost',
            'type': 'service',
        })
        cls.env['ir.config_parameter'].sudo().set_param(
            'mml_freight.freight_cost_product_id', str(cls.freight_product.id)
        )

        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'LC Test Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })
        cls.supplier = cls.env['res.partner'].create({'name': 'LC Supplier'})
        cls.po = cls.env['purchase.order'].create({'partner_id': cls.supplier.id})
        cls.tender = cls.env['freight.tender'].create({
            'purchase_order_id': cls.po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': cls.tender.id,
            'purchase_order_id': cls.po.id,
            'currency_id': cls.env.company.currency_id.id,
            'actual_rate': 1950.00,
        })

    def _skip_if_no_landed_cost(self):
        if 'stock.landed.cost' not in self.env:
            self.skipTest('stock.landed.cost not available (stock_account not installed)')

    def _make_done_receipt(self):
        """Create a minimal done incoming picking for the PO."""
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        picking_type = self.env['stock.picking.type'].search([
            ('warehouse_id', '=', warehouse.id),
            ('code', '=', 'incoming'),
        ], limit=1)
        picking = self.env['stock.picking'].create({
            'partner_id': self.supplier.id,
            'picking_type_id': picking_type.id,
            'location_id': self.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': picking_type.default_location_dest_id.id,
            'purchase_id': self.po.id,
        })
        # Force state to done for test purposes
        picking.write({'state': 'done'})
        return picking

    def test_action_create_landed_cost_creates_record(self):
        """action_create_landed_cost creates a stock.landed.cost linked to the receipt picking."""
        self._skip_if_no_landed_cost()
        picking = self._make_done_receipt()
        self.po.write({'picking_ids': [(4, picking.id)]})
        self.booking.action_create_landed_cost()
        self.assertTrue(self.booking.landed_cost_id, 'landed_cost_id must be set after creation')
        lc = self.booking.landed_cost_id
        self.assertAlmostEqual(
            lc.cost_lines[0].price_unit if lc.cost_lines else 0,
            1950.00,
            places=2,
        )

    def test_action_create_landed_cost_raises_without_actual_rate(self):
        """Raises UserError when actual_rate is 0 / not set."""
        self._skip_if_no_landed_cost()
        from odoo.exceptions import UserError
        booking_no_rate = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'tender_id': self.tender.id,
            'purchase_order_id': self.po.id,
            'currency_id': self.env.company.currency_id.id,
            'actual_rate': 0.0,
        })
        with self.assertRaises(UserError):
            booking_no_rate.action_create_landed_cost()

    def test_action_create_landed_cost_raises_without_receipt(self):
        """Raises UserError when no done receipt picking exists for the PO."""
        self._skip_if_no_landed_cost()
        from odoo.exceptions import UserError
        # PO with no done pickings
        po_new = self.env['purchase.order'].create({'partner_id': self.supplier.id})
        booking_new = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'tender_id': self.tender.id,
            'purchase_order_id': po_new.id,
            'currency_id': self.env.company.currency_id.id,
            'actual_rate': 500.0,
        })
        with self.assertRaises(UserError):
            booking_new.action_create_landed_cost()

    def test_action_create_landed_cost_raises_if_already_exists(self):
        """Raises UserError if landed_cost_id is already set (prevents duplicates)."""
        self._skip_if_no_landed_cost()
        from odoo.exceptions import UserError
        picking = self._make_done_receipt()
        self.po.write({'picking_ids': [(4, picking.id)]})
        self.booking.action_create_landed_cost()
        with self.assertRaises(UserError):
            self.booking.action_create_landed_cost()
```

**Step 2: Run tests — expect failures**

```bash
python -m pytest addons/mml_freight/tests/test_landed_cost.py -v 2>&1 | head -30
```

**Step 3: Add `landed_cost_id` field and methods to `freight_booking.py`**

In `FreightBooking`, add field after `invoice_id`:
```python
landed_cost_id = fields.Many2one(
    'stock.landed.cost', string='Landed Cost', ondelete='set null', readonly=True,
)
```

Add helper + action methods:
```python
def _get_freight_cost_product(self):
    """Return the configured freight cost product for landed cost creation."""
    param = self.env['ir.config_parameter'].sudo().get_param(
        'mml_freight.freight_cost_product_id'
    )
    if param:
        try:
            product = self.env['product.product'].browse(int(param))
            if product.exists():
                return product
        except (ValueError, TypeError):
            pass
    return self.env['product.product'].search(
        [('name', '=', 'Freight Cost'), ('type', '=', 'service')], limit=1,
    )

def action_create_landed_cost(self):
    """Create a stock.landed.cost from this booking's actual_rate and open it."""
    self.ensure_one()
    if not self.actual_rate:
        raise UserError(
            'Set the actual freight rate (Fetch Invoice or enter manually) '
            'before creating a landed cost.'
        )
    if self.landed_cost_id:
        raise UserError(
            'A landed cost already exists for this booking (%s). '
            'Open it from the Financials group.' % self.landed_cost_id.name
        )
    if 'stock.landed.cost' not in self.env:
        raise UserError(
            'stock.landed.cost model not available. '
            'Ensure the stock_account (or stock_landed_costs) module is installed.'
        )
    po = self.purchase_order_id
    if not po:
        raise UserError('No purchase order linked to this booking.')
    receipt = po.picking_ids.filtered(
        lambda p: p.state == 'done' and p.picking_type_code == 'incoming'
    )
    if not receipt:
        raise UserError(
            'No validated receipt found for %s. '
            'Receive the goods before creating a landed cost.' % po.name
        )
    freight_product = self._get_freight_cost_product()
    if not freight_product:
        raise UserError(
            'No freight cost product configured. '
            'Set system parameter mml_freight.freight_cost_product_id, '
            'or create a service product named "Freight Cost".'
        )
    account = (
        freight_product.categ_id.property_account_expense_categ_id
        if freight_product.categ_id else False
    )
    landed_cost = self.env['stock.landed.cost'].create({
        'picking_ids':  [(4, receipt[0].id)],
        'vendor_bill_id': self.invoice_id.id if self.invoice_id else False,
        'cost_lines': [(0, 0, {
            'product_id':   freight_product.id,
            'name':         f'Freight — {self.name}',
            'price_unit':   self.actual_rate,
            'split_method': 'by_weight',
            'account_id':   account.id if account else False,
        })],
    })
    self.landed_cost_id = landed_cost
    self.message_post(
        body=(
            f'Landed cost created: {landed_cost.name} '
            f'({self.actual_rate:.2f} {self.currency_id.name}). '
            f'Validate the landed cost to apply freight to product valuations.'
        )
    )
    return {
        'type': 'ir.actions.act_window',
        'res_model': 'stock.landed.cost',
        'res_id': landed_cost.id,
        'view_mode': 'form',
    }
```

**Step 4: Update `freight_booking_views.xml`**

In the `<header>` block, add after "Fetch Invoice":
```xml
<button name="action_create_landed_cost"
        string="Create Landed Cost"
        type="object"
        class="btn-secondary"
        invisible="not actual_rate or landed_cost_id or state in ('cancelled',)"/>
```

In the Financials `<group>` block, add after `invoice_id`:
```xml
<field name="landed_cost_id" readonly="1"/>
```

**Step 5: Add `stock_account` to `mml_freight/__manifest__.py` depends**

Add `'stock_account'` to the `depends` list (after `'delivery'`):
```python
'depends': [
    'mail',
    'stock',
    'account',
    'purchase',
    'delivery',
    'stock_account',
    'stock_3pl_core',
],
```

**Step 6: Run tests — expect PASS**

```bash
python -m pytest addons/mml_freight/tests/test_landed_cost.py -v
```

**Step 7: Commit**

```bash
git add addons/mml_freight/__manifest__.py \
        addons/mml_freight/models/freight_booking.py \
        addons/mml_freight/views/freight_booking_views.xml \
        addons/mml_freight/tests/test_landed_cost.py
git commit -m "feat: landed cost creation from freight booking actual_rate"
```

---

## Task 5: DSV Invoice Webhook

**Files:**
- Modify: `addons/mml_freight_dsv/controllers/dsv_webhook.py` — handle `Invoice` event type
- Modify: `addons/mml_freight/models/freight_booking.py` — add `_handle_dsv_invoice_webhook()`
- Create: `addons/mml_freight_dsv/tests/test_dsv_invoice_webhook.py`

---

**Step 1: Write failing tests**

`addons/mml_freight_dsv/tests/test_dsv_invoice_webhook.py`:
```python
from unittest.mock import patch, MagicMock, call
from odoo.tests.common import TransactionCase


class TestDsvInvoiceWebhook(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Invoice Webhook Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })
        supplier = cls.env['res.partner'].create({'name': 'Webhook Inv Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        tender = cls.env['freight.tender'].create({
            'purchase_order_id': po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': tender.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_shipment_id': 'SCPH-INV-HOOK',
            'state': 'in_transit',
        })

    def test_invoice_webhook_updates_actual_rate(self):
        """_handle_dsv_invoice_webhook updates actual_rate via get_invoice adapter call."""
        invoice_data = {
            'dsv_invoice_id': 'DSV-HOOK-INV',
            'amount': 2050.00,
            'currency': 'NZD',
            'invoice_date': '2026-03-01',
        }
        mock_adapter = MagicMock(get_invoice=MagicMock(return_value=invoice_data))
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=mock_adapter,
        ):
            self.env['freight.booking']._handle_dsv_invoice_webhook(
                self.carrier,
                {'shipmentId': 'SCPH-INV-HOOK', 'eventType': 'Invoice'},
            )
        self.assertAlmostEqual(self.booking.actual_rate, 2050.00, places=2)

    def test_invoice_webhook_no_op_for_unknown_shipment(self):
        """_handle_dsv_invoice_webhook is a no-op for an unrecognised shipmentId."""
        mock_adapter = MagicMock(get_invoice=MagicMock(return_value=None))
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=mock_adapter,
        ):
            # Must not raise
            self.env['freight.booking']._handle_dsv_invoice_webhook(
                self.carrier,
                {'shipmentId': 'SCPH-UNKNOWN-99', 'eventType': 'Invoice'},
            )
        # get_invoice must never have been called (no matching booking → early return)
        mock_adapter.get_invoice.assert_not_called()

    def test_invoice_webhook_no_op_when_adapter_returns_none(self):
        """_handle_dsv_invoice_webhook is a no-op when get_invoice returns None."""
        mock_adapter = MagicMock(get_invoice=MagicMock(return_value=None))
        original_rate = self.booking.actual_rate
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=mock_adapter,
        ):
            self.env['freight.booking']._handle_dsv_invoice_webhook(
                self.carrier,
                {'shipmentId': 'SCPH-INV-HOOK', 'eventType': 'Invoice'},
            )
        self.assertEqual(self.booking.actual_rate, original_rate, 'actual_rate must not change')

    def test_invoice_webhook_event_type_dispatched_in_controller(self):
        """dsv_webhook controller dispatches Invoice eventType to the invoice handler, not tracking."""
        # Verify dsv_webhook.py routes 'Invoice' to _handle_dsv_invoice_webhook
        # We check the source to ensure the dispatch branch exists.
        import inspect
        from odoo.addons.mml_freight_dsv.controllers import dsv_webhook
        source = inspect.getsource(dsv_webhook)
        self.assertIn('Invoice', source, "Controller source must contain 'Invoice' event dispatch")
        self.assertIn('_handle_dsv_invoice_webhook', source, "Controller must call _handle_dsv_invoice_webhook")
```

**Step 2: Run tests — expect failures**

```bash
python -m pytest addons/mml_freight_dsv/tests/test_dsv_invoice_webhook.py -v 2>&1 | head -30
```

**Step 3: Add `_handle_dsv_invoice_webhook()` to `freight_booking.py`**

```python
def _handle_dsv_invoice_webhook(self, carrier, body):
    """Handle DSV Invoice webhook notification. Fetches invoice via API and updates actual_rate.

    Called by dsv_webhook.py when eventType == 'Invoice'. Carrier ID validation is done
    by the controller before this is called.
    """
    if not isinstance(body, dict):
        return
    shipment_id = body.get('shipmentId', '')
    if not shipment_id:
        return
    booking = self.search([
        ('carrier_shipment_id', '=', shipment_id),
        ('carrier_id', '=', carrier.id),
        ('state', 'not in', ['cancelled', 'received']),
    ], limit=1)
    if not booking:
        _logger.info('DSV invoice webhook: no active booking for shipmentId %s', shipment_id)
        return
    registry = self.env['freight.adapter.registry']
    adapter = registry.get_adapter(carrier)
    if not adapter:
        _logger.warning('DSV invoice webhook: no adapter for carrier %s', carrier.id)
        return
    invoice_data = adapter.get_invoice(booking)
    if not invoice_data:
        _logger.info('DSV invoice webhook: get_invoice returned None for booking %s', booking.name)
        return
    curr = self.env['res.currency'].search(
        [('name', '=', invoice_data.get('currency', 'NZD'))], limit=1,
    ) or booking.currency_id
    booking.write({
        'actual_rate': invoice_data['amount'],
        'currency_id': curr.id if curr else booking.currency_id.id,
    })
    booking.message_post(
        body=(
            f"DSV invoice webhook: actual rate updated to "
            f"{invoice_data['amount']:.2f} {invoice_data.get('currency', '')} "
            f"(DSV Invoice #{invoice_data.get('dsv_invoice_id', 'N/A')})"
        )
    )
```

**Step 4: Extend `dsv_webhook.py` to dispatch `Invoice` event**

In the `dsv_webhook` method, replace:
```python
        if event_type == 'TRACKING_UPDATE':
            request.env['freight.booking'].sudo()._handle_dsv_tracking_webhook(carrier, body)
        else:
            _logger.warning('DSV unhandled event type: %s', event_type)
```

With:
```python
        if event_type == 'TRACKING_UPDATE':
            request.env['freight.booking'].sudo()._handle_dsv_tracking_webhook(carrier, body)
        elif event_type == 'Invoice':
            request.env['freight.booking'].sudo()._handle_dsv_invoice_webhook(carrier, body)
        else:
            _logger.warning('DSV unhandled event type: %s', event_type)
```

**Step 5: Run tests — expect PASS**

```bash
python -m pytest addons/mml_freight_dsv/tests/test_dsv_invoice_webhook.py -v
```

**Step 6: Run full test suite to confirm no regressions**

```bash
python -m pytest addons/mml_freight/tests/ addons/mml_freight_dsv/tests/ -v 2>&1 | tail -20
```
Expected: all green

**Step 7: Commit**

```bash
git add addons/mml_freight/models/freight_booking.py \
        addons/mml_freight_dsv/controllers/dsv_webhook.py \
        addons/mml_freight_dsv/tests/test_dsv_invoice_webhook.py
git commit -m "feat: DSV invoice webhook — dispatch Invoice event, update actual_rate"
```

---

## Out of scope: Mainfreight adapter

The `_queue_3pl_inward_order()` + `_build_inward_order_payload()` scaffold in `freight_booking.py` is already complete. The `InwardOrderDocument` XML builder lives in `stock_3pl_mainfreight` (the separate `mainfreight.3pl.intergration` project). Phase 4 Mainfreight work belongs in that project, not here.

---

## Execution Handoff

Plan complete and saved to `docs/plans/2026-03-01-phase4-implementation.md`.

Two execution options:

**1. Subagent-Driven (this session)** — I dispatch a fresh subagent per task, spec+quality review between tasks, fast iteration.

**2. Parallel Session (separate)** — Open a new session with executing-plans skill, batch execution with checkpoints.

Which approach?
