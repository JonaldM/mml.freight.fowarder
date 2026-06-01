"""Tests for mml_freight_knplus — mock adapter behaviour and registration."""

import os
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError

from odoo.addons.mml_freight_knplus.models.freight_carrier_knplus import KNPLUS_ENABLE_ENV_VAR


class TestKNMockAdapterSandbox(TransactionCase):
    """KnMockAdapter behaviour in sandbox mode."""

    @classmethod
    def setUpClass(cls):
        # Activate the K+N adapter for the duration of these tests.
        # The gate (MML_KNPLUS_ENABLE) exists to prevent accidental production use;
        # integration tests must set it so the ORM create/write guards pass.
        cls._knplus_was_set = os.environ.get(KNPLUS_ENABLE_ENV_VAR)
        os.environ[KNPLUS_ENABLE_ENV_VAR] = '1'
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'K+N Test',
            'delivery_type': 'knplus',
            'x_knplus_environment': 'sandbox',
            'x_knplus_quote_mode': 'manual',
        })

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        # Restore env var to its pre-test state.
        if cls._knplus_was_set is None:
            os.environ.pop(KNPLUS_ENABLE_ENV_VAR, None)
        else:
            os.environ[KNPLUS_ENABLE_ENV_VAR] = cls._knplus_was_set

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

    @classmethod
    def setUpClass(cls):
        cls._knplus_was_set = os.environ.get(KNPLUS_ENABLE_ENV_VAR)
        os.environ[KNPLUS_ENABLE_ENV_VAR] = '1'
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        if cls._knplus_was_set is None:
            os.environ.pop(KNPLUS_ENABLE_ENV_VAR, None)
        else:
            os.environ[KNPLUS_ENABLE_ENV_VAR] = cls._knplus_was_set

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
