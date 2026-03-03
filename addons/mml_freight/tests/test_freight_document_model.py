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
