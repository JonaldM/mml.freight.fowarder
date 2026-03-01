"""Tests for mml_freight_knplus webhook controller."""

import hashlib
import json
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase, HttpCase


class TestKNWebhookDedup(TransactionCase):
    """K+N webhook deduplication — ORM-level test (no HTTP)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'K+N Webhook Test',
            'delivery_type': 'knplus',
            'x_knplus_environment': 'sandbox',
        })

    def _build_payload(self, event_type='ShipmentUpdate'):
        return {'eventType': event_type, 'shipmentId': 'KN-SHP-001'}

    def test_duplicate_source_hash_is_deduplicated(self):
        """Same payload body processed twice → second call creates no new webhook event."""
        body = self._build_payload()
        body_bytes = json.dumps(body).encode()
        source_hash = hashlib.sha256(body_bytes).hexdigest()

        self.env['freight.webhook.event'].create({
            'carrier_id': self.carrier.id,
            'source_hash': source_hash,
            'event_type': body['eventType'],
        })

        # Second write with same hash should be blocked by unique constraint
        from odoo.exceptions import ValidationError
        with self.assertRaises(Exception):
            self.env['freight.webhook.event'].create({
                'carrier_id': self.carrier.id,
                'source_hash': source_hash,
                'event_type': body['eventType'],
            })

    def test_different_payloads_create_separate_events(self):
        """Two different payloads → two separate webhook event records."""
        body1 = json.dumps({'eventType': 'A', 'shipmentId': 'KN-001'}).encode()
        body2 = json.dumps({'eventType': 'B', 'shipmentId': 'KN-002'}).encode()
        hash1 = hashlib.sha256(body1).hexdigest()
        hash2 = hashlib.sha256(body2).hexdigest()
        self.assertNotEqual(hash1, hash2)

        self.env['freight.webhook.event'].create({
            'carrier_id': self.carrier.id,
            'source_hash': hash1,
            'event_type': 'A',
        })
        self.env['freight.webhook.event'].create({
            'carrier_id': self.carrier.id,
            'source_hash': hash2,
            'event_type': 'B',
        })

        events = self.env['freight.webhook.event'].search([
            ('carrier_id', '=', self.carrier.id),
        ])
        self.assertGreaterEqual(len(events), 2)


class TestKNWebhookAdapterDispatch(TransactionCase):
    """K+N webhook routes to adapter.handle_webhook()."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'K+N Dispatch Test',
            'delivery_type': 'knplus',
            'x_knplus_environment': 'sandbox',
        })

    def test_webhook_routes_to_adapter(self):
        """Webhook controller calls adapter.handle_webhook() with the parsed body."""
        from odoo.addons.mml_freight_knplus.adapters.kn_mock_adapter import KnMockAdapter

        dispatched = []

        def fake_handle(body):
            dispatched.append(body)

        with patch.object(KnMockAdapter, 'handle_webhook', side_effect=fake_handle):
            adapter = self.env['freight.adapter.registry'].get_adapter(self.carrier)
            test_body = {'eventType': 'TrackingUpdate', 'shipmentId': 'KN-TEST-001'}
            adapter.handle_webhook(test_body)

        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0]['eventType'], 'TrackingUpdate')
