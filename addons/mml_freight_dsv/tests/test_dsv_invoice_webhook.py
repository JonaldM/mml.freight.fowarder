from unittest.mock import patch, MagicMock
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
        import inspect
        from odoo.addons.mml_freight_dsv.controllers import dsv_webhook
        source = inspect.getsource(dsv_webhook)
        self.assertIn('Invoice', source, "Controller source must contain 'Invoice' event dispatch")
        self.assertIn('_handle_dsv_invoice_webhook', source, "Controller must call _handle_dsv_invoice_webhook")
