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
            'po_ids': [(4, po.id)],
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
