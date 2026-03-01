from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestFetchDocuments(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supplier = cls.env['res.partner'].create({'name': 'Docs Test Supplier'})
        cls.po = cls.env['purchase.order'].create({'partner_id': cls.supplier.id})
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Docs Test Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'dsv_generic',
            'x_dsv_environment': 'demo',
        })
        tender = cls.env['freight.tender'].create({
            'purchase_order_id': cls.po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': tender.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_booking_id': 'BK-DOCS-001',
        })

    def _patch_adapter(self, return_value):
        """Return a context manager that patches get_adapter to return an adapter
        whose get_documents returns return_value."""
        mock_adapter = MagicMock(get_documents=MagicMock(return_value=return_value))
        return patch(
            'odoo.addons.mml_freight.models.freight_booking.FreightAdapterRegistry.get_adapter',
            return_value=mock_adapter,
        )

    def test_action_fetch_documents_creates_freight_documents(self):
        """Adapter returns 2 docs (pod + invoice) → 2 freight.document records created."""
        fake_docs = [
            {
                'doc_type': 'pod',
                'bytes': b'%PDF-1.4-pod',
                'filename': 'POD-001.pdf',
                'carrier_doc_ref': 'DSV-DOC-POD-001',
            },
            {
                'doc_type': 'invoice',
                'bytes': b'%PDF-1.4-invoice',
                'filename': 'INV-001.pdf',
                'carrier_doc_ref': 'DSV-DOC-INV-001',
            },
        ]
        with self._patch_adapter(fake_docs):
            self.booking.action_fetch_documents()

        doc_refs = self.booking.document_ids.mapped('carrier_doc_ref')
        self.assertIn('DSV-DOC-POD-001', doc_refs)
        self.assertIn('DSV-DOC-INV-001', doc_refs)
        self.assertEqual(
            len(self.booking.document_ids.filtered(
                lambda d: d.carrier_doc_ref in ('DSV-DOC-POD-001', 'DSV-DOC-INV-001')
            )),
            2,
            'Exactly 2 freight.document records should be created',
        )

    def test_action_fetch_documents_sets_pod_attachment(self):
        """POD doc in returned list sets pod_attachment_id on the booking."""
        fake_docs = [
            {
                'doc_type': 'pod',
                'bytes': b'%PDF-1.4-pod',
                'filename': 'POD-002.pdf',
                'carrier_doc_ref': 'DSV-DOC-POD-002',
            },
        ]
        with self._patch_adapter(fake_docs):
            self.booking.action_fetch_documents()

        self.assertTrue(
            self.booking.pod_attachment_id,
            'pod_attachment_id should be set when a pod doc is returned',
        )
        self.assertEqual(self.booking.pod_attachment_id.name, 'POD-002.pdf')

    def test_action_fetch_documents_raises_when_empty(self):
        """Adapter returns [] → UserError is raised."""
        with self._patch_adapter([]):
            with self.assertRaises(UserError):
                self.booking.action_fetch_documents()

    def test_action_fetch_documents_idempotent(self):
        """Calling twice with same carrier_doc_ref → still 1 freight.document per ref, no duplicates."""
        fake_docs = [
            {
                'doc_type': 'customs',
                'bytes': b'%PDF-1.4-customs',
                'filename': 'CUST-001.pdf',
                'carrier_doc_ref': 'DSV-DOC-CUST-001',
            },
        ]
        with self._patch_adapter(fake_docs):
            self.booking.action_fetch_documents()
        with self._patch_adapter(fake_docs):
            self.booking.action_fetch_documents()

        matching_docs = self.booking.document_ids.filtered(
            lambda d: d.carrier_doc_ref == 'DSV-DOC-CUST-001'
        )
        self.assertEqual(
            len(matching_docs),
            1,
            'Second fetch must update existing freight.document, not create a duplicate',
        )
