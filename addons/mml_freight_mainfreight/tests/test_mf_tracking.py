"""Tests for mml_freight_mainfreight — tracking adapter and mock behaviour."""

from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestMFMockAdapterUAT(TransactionCase):
    """MFMockAdapter behaviour in UAT mode (no HTTP)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Mainfreight Test',
            'delivery_type': 'mainfreight',
            'x_mf_environment': 'uat',
            'x_mf_customer_code': 'MMLCONS',
            'x_mf_warehouse_code': 'AKL',
        })

    def _get_adapter(self):
        from odoo.addons.mml_freight_mainfreight.adapters.mf_mock_adapter import MFMockAdapter
        return MFMockAdapter(self.carrier, self.env)

    def _make_booking(self, **kwargs):
        currency = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1)
        vals = {'carrier_id': self.carrier.id, 'currency_id': currency.id, 'state': 'confirmed'}
        vals.update(kwargs)
        return self.env['freight.booking'].create(vals)

    # --- request_quote ---

    def test_request_quote_always_returns_empty(self):
        """Mainfreight A&O has no quote API — always returns []."""
        adapter = self._get_adapter()
        result = adapter.request_quote(None)
        self.assertEqual(result, [])

    def test_request_quote_returns_empty_in_production_mode_too(self):
        """Even in production mode, request_quote returns [] (no API)."""
        self.carrier.x_mf_environment = 'production'
        try:
            adapter = self._get_adapter()
            result = adapter.request_quote(None)
            self.assertEqual(result, [])
        finally:
            self.carrier.x_mf_environment = 'uat'

    # --- create_booking ---

    def test_create_booking_raises_user_error(self):
        """Mainfreight A&O booking is manual — adapter always raises UserError."""
        adapter = self._get_adapter()
        with self.assertRaises(UserError) as ctx:
            adapter.create_booking(None, None)
        self.assertIn('Mainchain', str(ctx.exception))

    def test_create_booking_raises_in_production_mode_too(self):
        """Even in production mode, create_booking raises UserError (no API)."""
        self.carrier.x_mf_environment = 'production'
        try:
            adapter = self._get_adapter()
            with self.assertRaises(UserError):
                adapter.create_booking(None, None)
        finally:
            self.carrier.x_mf_environment = 'uat'

    # --- get_tracking ---

    def test_get_tracking_returns_canned_events(self):
        """UAT → get_tracking returns at least 4 canned events."""
        booking = self._make_booking()
        adapter = self._get_adapter()
        events = adapter.get_tracking(booking)
        self.assertIsInstance(events, list)
        self.assertGreaterEqual(len(events), 4)

    def test_get_tracking_events_have_required_keys(self):
        """All canned tracking events have the required dict keys."""
        booking = self._make_booking()
        adapter = self._get_adapter()
        events = adapter.get_tracking(booking)
        required = {'event_date', 'status', 'location', 'description', 'raw_payload'}
        for evt in events:
            self.assertEqual(required, required & set(evt.keys()))

    def test_get_tracking_events_have_valid_statuses(self):
        """Canned tracking events map to known booking states or status strings."""
        booking = self._make_booking()
        adapter = self._get_adapter()
        events = adapter.get_tracking(booking)
        # All statuses must be non-empty strings
        for evt in events:
            self.assertIsInstance(evt['status'], str)
            self.assertTrue(evt['status'])

    # --- cancel_booking ---

    def test_cancel_booking_is_noop(self):
        """cancel_booking is a no-op (Mainfreight cancellation is manual)."""
        booking = self._make_booking()
        adapter = self._get_adapter()
        result = adapter.cancel_booking(booking)
        self.assertIsNone(result)

    # --- adapter registration ---

    def test_adapter_registry_resolves_mainfreight(self):
        """FreightAdapterRegistry.get_adapter() returns MFMockAdapter for 'mainfreight' carrier."""
        from odoo.addons.mml_freight_mainfreight.adapters.mf_mock_adapter import MFMockAdapter
        adapter = self.env['freight.adapter.registry'].get_adapter(self.carrier)
        self.assertIsNotNone(adapter)
        self.assertIsInstance(adapter, MFMockAdapter)


class TestMFLiveAdapterNormalisation(TransactionCase):
    """MFAdapter._normalise_events: verify event normalisation from raw API payloads."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Mainfreight Live Test',
            'delivery_type': 'mainfreight',
            'x_mf_environment': 'uat',
        })

    def _get_live_adapter(self):
        from odoo.addons.mml_freight_mainfreight.adapters.mf_adapter import MFAdapter
        return MFAdapter(self.carrier, self.env)

    def test_normalise_departure_event(self):
        """DEPARTURE code maps to 'in_transit' status."""
        adapter = self._get_live_adapter()
        events = adapter._normalise_events({
            'events': [
                {
                    'eventCode': 'DEPARTURE',
                    'eventDateTime': '2026-03-18T14:00:00Z',
                    'location': 'Shanghai, CN',
                    'eventDescription': 'Vessel departed Shanghai',
                }
            ]
        })
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['status'], 'in_transit')
        self.assertEqual(events[0]['location'], 'Shanghai, CN')

    def test_normalise_port_arrival_event(self):
        """PORT_ARRIVAL code maps to 'arrived_port' status."""
        adapter = self._get_live_adapter()
        events = adapter._normalise_events({
            'events': [
                {
                    'eventCode': 'PORT_ARRIVAL',
                    'eventDateTime': '2026-04-05T08:00:00Z',
                    'location': 'Auckland, NZ',
                    'eventDescription': 'Vessel arrived Auckland',
                }
            ]
        })
        self.assertEqual(events[0]['status'], 'arrived_port')

    def test_normalise_delivered_event(self):
        """DELIVERED code maps to 'delivered' status."""
        adapter = self._get_live_adapter()
        events = adapter._normalise_events([
            {
                'eventCode': 'DELIVERED',
                'eventDateTime': '2026-04-08T11:30:00Z',
                'location': 'Mainfreight Auckland',
                'eventDescription': 'Delivered to warehouse',
            }
        ])
        self.assertEqual(events[0]['status'], 'delivered')

    def test_normalise_unknown_code_uses_code_as_status(self):
        """Unknown event codes become lowercased status strings."""
        adapter = self._get_live_adapter()
        events = adapter._normalise_events({
            'events': [
                {
                    'eventCode': 'SOME_NEW_CODE',
                    'eventDateTime': '2026-03-20T10:00:00Z',
                    'location': 'Hong Kong',
                    'eventDescription': 'Custom event',
                }
            ]
        })
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['status'], 'some new code')

    def test_normalise_event_without_date_is_skipped(self):
        """Events without a datetime are silently skipped."""
        adapter = self._get_live_adapter()
        events = adapter._normalise_events({
            'events': [
                {'eventCode': 'DEPARTURE', 'location': 'Shanghai'},  # no datetime
            ]
        })
        self.assertEqual(events, [])

    def test_normalise_accepts_flat_list(self):
        """Normaliser accepts a flat list (not wrapped in a dict)."""
        adapter = self._get_live_adapter()
        events = adapter._normalise_events([
            {'eventCode': 'CARGO_RECEIVED', 'eventDateTime': '2026-03-15T09:00:00Z',
             'location': 'Shanghai', 'eventDescription': 'Cargo received'},
        ])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['status'], 'cargo_ready')

    def test_resolve_reference_prefers_housebill(self):
        """_resolve_reference prefers carrier_booking_id over container_number."""
        currency = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1)
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': currency.id,
            'state': 'in_transit',
            'carrier_booking_id': 'MFAO-HBL-001',
            'container_number': 'CSNU1234567',
        })
        adapter = self._get_live_adapter()
        ref = adapter._resolve_reference(booking)
        self.assertEqual(ref, ('InternationalHousebill', 'MFAO-HBL-001'))

    def test_resolve_reference_falls_back_to_container(self):
        """_resolve_reference falls back to container_number when no housebill."""
        currency = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1)
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': currency.id,
            'state': 'in_transit',
            'container_number': 'CSNU9876543',
        })
        adapter = self._get_live_adapter()
        ref = adapter._resolve_reference(booking)
        self.assertEqual(ref, ('ContainerNumber', 'CSNU9876543'))

    def test_resolve_reference_returns_none_when_no_refs(self):
        """_resolve_reference returns None when no tracking reference is available."""
        currency = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1)
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': currency.id,
            'state': 'confirmed',
        })
        adapter = self._get_live_adapter()
        ref = adapter._resolve_reference(booking)
        self.assertIsNone(ref)

    def test_get_tracking_returns_empty_when_no_reference(self):
        """get_tracking returns [] gracefully when no tracking reference exists."""
        currency = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1)
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': currency.id,
            'state': 'confirmed',
        })
        adapter = self._get_live_adapter()
        result = adapter.get_tracking(booking)
        self.assertEqual(result, [])

    def test_get_tracking_returns_empty_on_api_error(self):
        """get_tracking returns [] gracefully when API request fails."""
        currency = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1)
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': currency.id,
            'state': 'in_transit',
            'carrier_booking_id': 'MFAO-HBL-TEST',
        })
        adapter = self._get_live_adapter()
        import requests as req_module
        with patch.object(req_module, 'get', side_effect=req_module.RequestException('timeout')):
            result = adapter.get_tracking(booking)
        self.assertEqual(result, [])

    def test_get_tracking_returns_empty_on_404(self):
        """get_tracking returns [] when API returns 404 (shipment not found yet)."""
        currency = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1)
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': currency.id,
            'state': 'confirmed',
            'carrier_booking_id': 'MFAO-HBL-NOTFOUND',
        })
        adapter = self._get_live_adapter()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.ok = False
        import requests as req_module
        with patch.object(req_module, 'get', return_value=mock_resp):
            result = adapter.get_tracking(booking)
        self.assertEqual(result, [])
