from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestFetchLabel(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supplier = cls.env['res.partner'].create({'name': 'Label Test Supplier'})
        cls.po = cls.env['purchase.order'].create({'partner_id': cls.supplier.id})
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Label Test Carrier',
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
            'carrier_booking_id': 'BK-TEST-001',
        })

    def _patch_adapter(self, return_value):
        """Return a context manager that patches get_adapter to return an adapter
        whose get_label returns return_value."""
        mock_adapter = MagicMock(get_label=MagicMock(return_value=return_value))
        return patch(
            'odoo.addons.mml_freight.models.freight_booking.FreightAdapterRegistry.get_adapter',
            return_value=mock_adapter,
        )

    def test_action_fetch_label_creates_attachment(self):
        """Adapter returns bytes → label_attachment_id is set on booking."""
        fake_bytes = b'%PDF-1.4-test-label'
        with self._patch_adapter(fake_bytes):
            self.booking.action_fetch_label()
        self.assertTrue(
            self.booking.label_attachment_id,
            'label_attachment_id should be set after fetch',
        )
        self.assertEqual(
            self.booking.label_attachment_id.name,
            f'label_{self.booking.name}.pdf',
        )

    def test_action_fetch_label_creates_freight_document(self):
        """After fetch: a freight.document with doc_type='label' exists and
        its attachment_id matches label_attachment_id."""
        fake_bytes = b'%PDF-1.4-test-label'
        with self._patch_adapter(fake_bytes):
            self.booking.action_fetch_label()
        label_docs = self.booking.document_ids.filtered(
            lambda d: d.doc_type == 'label'
        )
        self.assertEqual(len(label_docs), 1, 'Exactly one label freight.document expected')
        self.assertEqual(
            label_docs.attachment_id.id,
            self.booking.label_attachment_id.id,
            'freight.document.attachment_id must match booking.label_attachment_id',
        )

    def test_action_fetch_label_raises_when_no_bytes(self):
        """Adapter returns None → UserError is raised."""
        with self._patch_adapter(None):
            with self.assertRaises(UserError):
                self.booking.action_fetch_label()

    def test_action_fetch_label_idempotent(self):
        """Two calls with same adapter → only 1 freight.document with doc_type='label'."""
        fake_bytes = b'%PDF-1.4-test-label'
        with self._patch_adapter(fake_bytes):
            self.booking.action_fetch_label()
        with self._patch_adapter(fake_bytes):
            self.booking.action_fetch_label()
        label_docs = self.booking.document_ids.filtered(
            lambda d: d.doc_type == 'label'
        )
        self.assertEqual(
            len(label_docs), 1,
            'Second fetch must update existing freight.document, not create a duplicate',
        )
