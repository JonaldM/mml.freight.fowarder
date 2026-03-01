from odoo.tests.common import TransactionCase


class TestDedupConstraints(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Constraint Test Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })
        supplier = cls.env['res.partner'].create({'name': 'Constraint Supplier'})
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
        })

    # ── freight.tracking.event ──────────────────────────────────────────────

    def test_duplicate_tracking_event_blocked_at_db(self):
        """UNIQUE(booking_id, event_date, status) prevents duplicate tracking events."""
        self.env['freight.tracking.event'].create({
            'booking_id': self.booking.id,
            'event_date': '2026-03-01 10:00:00',
            'status': 'in_transit',
            'location': 'Shanghai CN',
            'description': 'Departed',
        })
        with self.assertRaises(Exception, msg='Duplicate tracking event must be blocked'):
            with self.env.cr.savepoint():
                self.env['freight.tracking.event'].create({
                    'booking_id': self.booking.id,
                    'event_date': '2026-03-01 10:00:00',
                    'status': 'in_transit',
                    'location': 'Different location',
                    'description': 'Duplicate',
                })

    def test_same_status_different_date_allowed(self):
        """Same status on a different date is NOT a duplicate."""
        self.env['freight.tracking.event'].create({
            'booking_id': self.booking.id,
            'event_date': '2026-03-01 10:00:00',
            'status': 'delivered',
            'location': 'Auckland NZ',
            'description': 'First event',
        })
        self.env['freight.tracking.event'].create({
            'booking_id': self.booking.id,
            'event_date': '2026-03-02 10:00:00',
            'status': 'delivered',
            'location': 'Auckland NZ',
            'description': 'Second event',
        })

    # ── freight.document ────────────────────────────────────────────────────

    def test_duplicate_document_blocked_at_db(self):
        """UNIQUE(booking_id, doc_type, carrier_doc_ref) prevents duplicate documents."""
        attachment = self.env['ir.attachment'].create({
            'name': 'test.pdf',
            'type': 'binary',
            'datas': 'dGVzdA==',
            'res_model': 'freight.booking',
            'res_id': self.booking.id,
        })
        self.env['freight.document'].create({
            'booking_id': self.booking.id,
            'doc_type': 'pod',
            'attachment_id': attachment.id,
            'carrier_doc_ref': 'DSV-DOC-001',
        })
        with self.assertRaises(Exception, msg='Duplicate document must be blocked'):
            with self.env.cr.savepoint():
                self.env['freight.document'].create({
                    'booking_id': self.booking.id,
                    'doc_type': 'pod',
                    'attachment_id': attachment.id,
                    'carrier_doc_ref': 'DSV-DOC-001',
                })

    def test_same_doc_type_different_ref_allowed(self):
        """Same doc_type with a different carrier_doc_ref is NOT a duplicate."""
        attachment = self.env['ir.attachment'].create({
            'name': 'a.pdf', 'type': 'binary', 'datas': 'dGVzdA==',
            'res_model': 'freight.booking', 'res_id': self.booking.id,
        })
        self.env['freight.document'].create({
            'booking_id': self.booking.id,
            'doc_type': 'pod',
            'attachment_id': attachment.id,
            'carrier_doc_ref': 'REF-A',
        })
        self.env['freight.document'].create({
            'booking_id': self.booking.id,
            'doc_type': 'pod',
            'attachment_id': attachment.id,
            'carrier_doc_ref': 'REF-B',
        })
