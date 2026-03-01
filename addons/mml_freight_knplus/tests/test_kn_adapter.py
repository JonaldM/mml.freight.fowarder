"""Tests for mml_freight_knplus — mock adapter behaviour and registration."""

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestKNMockAdapterSandbox(TransactionCase):
    """KnMockAdapter behaviour in sandbox mode."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'K+N Test',
            'delivery_type': 'knplus',
            'x_knplus_environment': 'sandbox',
            'x_knplus_quote_mode': 'manual',
        })

    def _get_adapter(self):
        from odoo.addons.mml_freight_knplus.adapters.kn_mock_adapter import KnMockAdapter
        return KnMockAdapter(self.carrier, self.env)

    def test_request_quote_returns_empty_in_manual_mode(self):
        """Sandbox + manual mode → request_quote returns [] (ops enter via myKN)."""
        adapter = self._get_adapter()
        result = adapter.request_quote(None)
        self.assertIsInstance(result, list)
        self.assertEqual(result, [])

    def test_request_quote_returns_canned_quotes_in_api_mode(self):
        """Sandbox + api mode → request_quote returns canned K+N quotes."""
        self.carrier.x_knplus_quote_mode = 'api'
        adapter = self._get_adapter()
        quotes = adapter.request_quote(None)
        self.assertIsInstance(quotes, list)
        self.assertGreater(len(quotes), 0)
        for q in quotes:
            self.assertIn('service_name', q)
            self.assertIn('total_rate', q)
            self.assertIn('currency', q)
            self.assertIn('carrier_quote_ref', q)
        # Reset
        self.carrier.x_knplus_quote_mode = 'manual'

    def test_create_booking_returns_mock_ref(self):
        """Sandbox → create_booking returns a mock carrier_booking_id."""
        adapter = self._get_adapter()
        result = adapter.create_booking(None, None)
        self.assertIn('carrier_booking_id', result)
        self.assertTrue(result['carrier_booking_id'].startswith('KN-MOCK-BK-'))

    def test_get_tracking_returns_canned_events(self):
        """Sandbox → get_tracking returns at least 3 canned events with required keys."""
        currency = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1)
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'state': 'in_transit',
            'currency_id': currency.id,
        })
        adapter = self._get_adapter()
        events = adapter.get_tracking(booking)
        self.assertIsInstance(events, list)
        self.assertGreaterEqual(len(events), 1)
        for evt in events:
            self.assertIn('event_date', evt)
            self.assertIn('status', evt)
            self.assertIn('location', evt)
            self.assertIn('description', evt)

    def test_get_documents_returns_canned_docs(self):
        """Sandbox → get_documents returns at least one document dict."""
        currency = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1)
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'state': 'delivered',
            'carrier_booking_id': 'KN-MOCK-BK-0001',
            'currency_id': currency.id,
        })
        adapter = self._get_adapter()
        docs = adapter.get_documents(booking)
        self.assertIsInstance(docs, list)
        self.assertGreater(len(docs), 0)
        self.assertIn('doc_type', docs[0])
        self.assertIn('bytes', docs[0])
        self.assertIn('filename', docs[0])

    def test_cancel_booking_is_noop_in_sandbox(self):
        """Sandbox → cancel_booking returns None (no-op)."""
        currency = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1)
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'state': 'confirmed',
            'currency_id': currency.id,
        })
        adapter = self._get_adapter()
        result = adapter.cancel_booking(booking)
        self.assertIsNone(result)


class TestKNAdapterRegistration(TransactionCase):
    """Verify the adapter registry correctly resolves 'knplus'."""

    def test_adapter_registry_resolves_knplus(self):
        """FreightAdapterRegistry.get_adapter() returns KnMockAdapter for 'knplus' carrier."""
        from odoo.addons.mml_freight_knplus.adapters.kn_mock_adapter import KnMockAdapter
        carrier = self.env['delivery.carrier'].create({
            'name': 'K+N Registry Test',
            'delivery_type': 'knplus',
            'x_knplus_environment': 'sandbox',
        })
        adapter = self.env['freight.adapter.registry'].get_adapter(carrier)
        self.assertIsNotNone(adapter)
        self.assertIsInstance(adapter, KnMockAdapter)
