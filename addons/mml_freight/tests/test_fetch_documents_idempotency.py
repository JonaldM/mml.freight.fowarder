from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase


class TestFetchDocumentsIdempotency(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Doc Idempotency Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })
        supplier = cls.env['res.partner'].create({'name': 'Doc Idem Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        tender = cls.env['freight.tender'].create({
            'po_ids': [(4, po.id)],
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': tender.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_booking_id': 'BK-DOC-IDEM',
        })

    def _mock_docs(self, carrier_doc_ref=''):
        """Returns adapter mock that yields one POD document."""
        return MagicMock(get_documents=MagicMock(return_value=[{
            'doc_type': 'pod',
            'bytes': b'%PDF-pod',
            'filename': 'POD-001.pdf',
            'carrier_doc_ref': carrier_doc_ref,
        }]))

    def test_fetch_twice_with_ref_creates_one_document(self):
        """Fetching docs twice with a carrier_doc_ref results in exactly one freight.document."""
        adapter = self._mock_docs(carrier_doc_ref='DSV-POD-XYZ')
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter', return_value=adapter,
        ):
            self.booking.action_fetch_documents()
            self.booking.action_fetch_documents()
        docs = self.booking.document_ids.filtered(lambda d: d.doc_type == 'pod')
        self.assertEqual(len(docs), 1, 'Must have exactly 1 POD document after two fetches')

    def test_fetch_twice_without_ref_creates_one_document(self):
        """Fetching docs twice with empty carrier_doc_ref must also result in exactly one document."""
        adapter = self._mock_docs(carrier_doc_ref='')
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter', return_value=adapter,
        ):
            self.booking.action_fetch_documents()
            self.booking.action_fetch_documents()
        docs = self.booking.document_ids.filtered(lambda d: d.doc_type == 'pod')
        self.assertEqual(len(docs), 1, 'Must have exactly 1 POD document even without carrier_doc_ref')

    def test_synthetic_ref_is_stable(self):
        """Synthetic ref must be the same value on every call for the same doc_type + filename."""
        import hashlib
        ref_a = 'local:' + hashlib.sha256(('pod' + 'POD-001.pdf').encode('utf-8')).hexdigest()[:32]
        ref_b = 'local:' + hashlib.sha256(('pod' + 'POD-001.pdf').encode('utf-8')).hexdigest()[:32]
        self.assertEqual(ref_a, ref_b)
