import hashlib
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase


class TestDsvWebhookDedup(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Dedup Webhook Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })

    def _make_hash(self, body: bytes) -> str:
        return hashlib.sha256(body).hexdigest()

    def test_webhook_event_model_exists(self):
        """freight.webhook.event model must be registered."""
        self.assertIn('freight.webhook.event', self.env)

    def test_duplicate_source_hash_blocked_at_db(self):
        """UNIQUE(carrier_id, source_hash) prevents duplicate webhook event log entries."""
        h = self._make_hash(b'{"eventType":"TRACKING_UPDATE","shipmentId":"SH-001"}')
        self.env['freight.webhook.event'].create({
            'carrier_id': self.carrier.id,
            'source_hash': h,
            'event_type': 'TRACKING_UPDATE',
        })
        with self.assertRaises(Exception, msg='Duplicate webhook event must be blocked at DB level'):
            with self.env.cr.savepoint():
                self.env['freight.webhook.event'].create({
                    'carrier_id': self.carrier.id,
                    'source_hash': h,
                    'event_type': 'TRACKING_UPDATE',
                })

    def test_different_payload_same_carrier_allowed(self):
        """Different payloads (different hash) on same carrier must both be accepted."""
        h1 = self._make_hash(b'{"shipmentId":"SH-A"}')
        h2 = self._make_hash(b'{"shipmentId":"SH-B"}')
        self.env['freight.webhook.event'].create({
            'carrier_id': self.carrier.id,
            'source_hash': h1,
            'event_type': 'TRACKING_UPDATE',
        })
        self.env['freight.webhook.event'].create({
            'carrier_id': self.carrier.id,
            'source_hash': h2,
            'event_type': 'TRACKING_UPDATE',
        })

    def test_duplicate_webhook_dispatch_skipped(self):
        """Second delivery of identical webhook body must be silently ignored — no handler called."""
        import json
        from odoo.addons.mml_freight_dsv.controllers.dsv_webhook import DsvWebhookController

        body = b'{"eventType":"TRACKING_UPDATE","shipmentId":"SH-DUP-001"}'
        h = self._make_hash(body)

        # Pre-seed the event log as if first delivery already processed
        self.env['freight.webhook.event'].sudo().create({
            'carrier_id': self.carrier.id,
            'source_hash': h,
            'event_type': 'TRACKING_UPDATE',
        })

        controller = DsvWebhookController()

        mock_request = MagicMock()
        mock_request.httprequest.get_data.return_value = body
        mock_request.httprequest.headers.get.return_value = 'sha256=dummy'
        mock_request.get_json_data.return_value = json.loads(body)
        mock_request.env = self.env

        with patch('odoo.addons.mml_freight_dsv.controllers.dsv_webhook.request', mock_request), \
             patch('odoo.addons.mml_freight_dsv.controllers.dsv_webhook._validate_dsv_signature', return_value=True), \
             patch.object(type(self.env['freight.booking']), '_handle_dsv_tracking_webhook') as mock_handler:
            controller.dsv_webhook(self.carrier.id)
            mock_handler.assert_not_called()
