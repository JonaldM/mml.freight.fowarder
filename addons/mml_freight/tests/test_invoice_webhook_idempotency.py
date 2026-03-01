from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase


class TestInvoiceWebhookIdempotency(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Invoice Webhook Idem Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })
        supplier = cls.env['res.partner'].create({'name': 'Idem Supplier'})
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
            'carrier_shipment_id': 'SH-INV-IDEM',
            'actual_rate': 2050.00,
        })

    def _call_webhook(self, amount):
        invoice_data = {
            'dsv_invoice_id': 'DSV-INV-IDEM',
            'amount': amount,
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
                {'shipmentId': 'SH-INV-IDEM', 'eventType': 'Invoice'},
            )

    def test_second_identical_webhook_posts_no_chatter(self):
        """When actual_rate already matches the invoice amount, no new chatter note is posted."""
        msg_count_before = len(self.booking.message_ids)
        # Rate already matches — second delivery of same webhook
        self._call_webhook(amount=2050.00)
        msg_count_after = len(self.booking.message_ids)
        self.assertEqual(
            msg_count_before, msg_count_after,
            'No chatter note must be posted when actual_rate already matches',
        )

    def test_first_webhook_when_rate_zero_does_update(self):
        """When actual_rate is 0, invoice webhook must update it normally."""
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'tender_id': self.booking.tender_id.id,
            'currency_id': self.env.company.currency_id.id,
            'carrier_shipment_id': 'SH-INV-ZERO',
            'actual_rate': 0.0,
        })
        invoice_data = {
            'dsv_invoice_id': 'DSV-INV-NEW',
            'amount': 1750.00,
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
                {'shipmentId': 'SH-INV-ZERO', 'eventType': 'Invoice'},
            )
        self.assertAlmostEqual(booking.actual_rate, 1750.00, places=2)

    def test_rate_change_does_update(self):
        """When invoice amount differs from actual_rate (rate correction), the update proceeds."""
        self._call_webhook(amount=2100.00)  # Different from 2050
        self.assertAlmostEqual(self.booking.actual_rate, 2100.00, places=2)
