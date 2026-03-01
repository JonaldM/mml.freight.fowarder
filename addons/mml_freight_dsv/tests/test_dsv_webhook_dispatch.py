from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase


class TestDsvWebhookDispatch(TransactionCase):
    """Tests that DsvMockAdapter.handle_webhook() delegates to freight.booking handler."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Dispatch Test',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'dsv_generic',
            'x_dsv_environment': 'demo',
        })

    def _adapter(self):
        from odoo.addons.mml_freight_dsv.adapters.dsv_mock_adapter import DsvMockAdapter
        return DsvMockAdapter(self.carrier, self.env)

    def test_handle_webhook_calls_booking_handler(self):
        """DsvMockAdapter.handle_webhook() must call freight.booking._handle_dsv_tracking_webhook."""
        body = {'shipmentId': 'TEST-SH-001', 'events': []}
        with patch.object(
            type(self.env['freight.booking']),
            '_handle_dsv_tracking_webhook',
        ) as mock_handler:
            self._adapter().handle_webhook(body)
        mock_handler.assert_called_once()
        call_args = mock_handler.call_args
        self.assertEqual(call_args.args[0].id, self.carrier.id)
        self.assertEqual(call_args.args[1], body)

    def test_handle_webhook_base_noop(self):
        """FreightAdapterBase.handle_webhook() is a no-op — must not raise."""
        from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase
        # Use a concrete subclass that doesn't override handle_webhook
        class MinimalAdapter(FreightAdapterBase):
            def request_quote(self, t): return []
            def create_booking(self, t, q): return {}
            def get_tracking(self, b): return []

        adapter = MinimalAdapter(self.carrier, self.env)
        adapter.handle_webhook({'anything': True})  # must not raise
