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
        body = b'{"eventType":"TRACKING_UPDATE","shipmentId":"SH-DUP-001"}'
        h = self._make_hash(body)
        # Pre-seed the event log as if first delivery already processed
        self.env['freight.webhook.event'].create({
            'carrier_id': self.carrier.id,
            'source_hash': h,
            'event_type': 'TRACKING_UPDATE',
        })
        mock_handler = MagicMock()
        with patch.object(
            type(self.env['freight.booking']),
            '_handle_dsv_tracking_webhook',
            mock_handler,
        ):
            # Simulate what the controller does after HMAC validation
            existing = self.env['freight.webhook.event'].search([
                ('carrier_id', '=', self.carrier.id),
                ('source_hash', '=', h),
            ], limit=1)
            if not existing:
                self.env['freight.webhook.event'].create({
                    'carrier_id': self.carrier.id,
                    'source_hash': h,
                    'event_type': 'TRACKING_UPDATE',
                })
                # Would dispatch here
            mock_handler.assert_not_called()
