from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError

class TestTenderLifecycle(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        p = cls.env['res.partner'].create({'name': 'LC Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': p.id})
        nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or cls.env.company.currency_id
        cls.tender = cls.env['freight.tender'].create({'purchase_order_id': po.id, 'company_id': cls.env.company.id, 'currency_id': nzd.id})

    def test_initial_state(self): self.assertEqual(self.tender.state, 'draft')
    def test_sequence_assigned(self): self.assertTrue(self.tender.name.startswith('FT/'))
    def test_cancel(self):
        t = self.env['freight.tender'].create({'purchase_order_id': self.tender.purchase_order_id.id, 'company_id': self.env.company.id, 'currency_id': self.env.company.currency_id.id})
        t.action_cancel()
        self.assertEqual(t.state, 'cancelled')
    def test_book_without_quote_raises(self):
        with self.assertRaises(UserError): self.tender.action_book()
    def test_auto_select_no_quotes_raises(self):
        t2 = self.env['freight.tender'].create({'purchase_order_id': self.tender.purchase_order_id.id, 'company_id': self.env.company.id, 'currency_id': self.env.company.currency_id.id, 'state': 'quoted', 'selection_mode': 'cheapest'})
        with self.assertRaises(UserError): t2.action_auto_select()

    def test_booking_stays_draft_when_requires_manual_confirmation(self):
        """When adapter returns requires_manual_confirmation=True, booking must not auto-confirm."""
        from unittest.mock import patch, MagicMock
        # Create a delivery.carrier to use as the carrier for the quote
        carrier = self.env['delivery.carrier'].create({
            'name': 'Test Carrier',
            'product_id': self.env['product.product'].search([], limit=1).id,
        })
        nzd = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or self.env.company.currency_id
        # Create a tender in 'selected' state with a selected quote
        tender = self.env['freight.tender'].create({
            'purchase_order_id': self.tender.purchase_order_id.id,
            'company_id': self.env.company.id,
            'currency_id': nzd.id,
        })
        quote = self.env['freight.tender.quote'].create({
            'tender_id': tender.id,
            'carrier_id': carrier.id,
            'state': 'received',
            'currency_id': nzd.id,
            'transport_mode': 'road',
        })
        tender.write({'state': 'selected', 'selected_quote_id': quote.id})
        mock_adapter = MagicMock()
        mock_adapter.create_booking.return_value = {
            'carrier_booking_id':           'BK-DRAFT-TEST',
            'carrier_shipment_id':          'SH-001',
            'carrier_tracking_url':         '',
            'requires_manual_confirmation': True,
        }
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=mock_adapter,
        ):
            tender.action_book()
        self.assertEqual(
            tender.booking_id.state, 'draft',
            'Booking must stay in draft when requires_manual_confirmation=True',
        )
