from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter


def _resp(status=200, data=None):
    m = MagicMock()
    m.status_code = status
    m.ok = status < 400
    m.json.return_value = data or {}
    return m


class TestDsvTracking(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.service_product = cls.env['product.product'].create({
            'name': 'Service Product',
            'type': 'service',
        })
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Track',
            'product_id': cls.service_product.id,
            'delivery_type': 'dsv_generic',
            'x_dsv_environment': 'production',
            'x_dsv_subscription_key': 'SUB001',
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id':          cls.carrier.id,
            'currency_id':         cls.env.company.currency_id.id,
            'carrier_shipment_id': 'SH_TRACK_001',
            'state':               'confirmed',
        })

    def _adapter(self):
        return DsvGenericAdapter(self.carrier, self.env)

    def test_returns_events_list(self):
        dsv_data = {'events': [{'eventType': 'DEPARTURE', 'eventDate': '2026-05-10T08:00:00Z',
                                 'location': 'Shanghai CN', 'description': 'Departed.',
                                 'estimatedDelivery': '2026-06-15T00:00:00Z'}]}
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='T'):
            with patch('requests.get', return_value=_resp(200, dsv_data)):
                events = self._adapter().get_tracking(self.booking)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['status'], 'in_transit')
        self.assertEqual(events[0]['location'], 'Shanghai CN')
        self.assertEqual(events[0]['_new_eta'], '2026-06-15T00:00:00Z')

    def test_all_event_types_mapped(self):
        mapping = [
            ('BOOKING_CONFIRMED', 'confirmed'),
            ('CARGO_RECEIVED',    'cargo_ready'),
            ('DEPARTURE',         'in_transit'),
            ('ARRIVED_POD',       'arrived_port'),
            ('CUSTOMS_CLEARED',   'customs'),
            ('DELIVERED',         'delivered'),
        ]
        for dsv_type, expected in mapping:
            data = {'events': [{'eventType': dsv_type, 'eventDate': '2026-05-01T00:00:00Z'}]}
            with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='T'):
                with patch('requests.get', return_value=_resp(200, data)):
                    events = self._adapter().get_tracking(self.booking)
            self.assertEqual(events[0]['status'], expected, f'Failed for {dsv_type}')

    def test_error_returns_empty_list(self):
        """Tracking errors are non-fatal."""
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='T'):
            with patch('requests.get', return_value=_resp(500)):
                events = self._adapter().get_tracking(self.booking)
        self.assertEqual(events, [])

    def test_no_shipment_id_returns_empty(self):
        b = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': self.env.company.currency_id.id,
        })
        events = self._adapter().get_tracking(b)
        self.assertEqual(events, [])

    def test_auth_failure_returns_empty_list(self):
        """DsvAuthError from get_token must not propagate — return []."""
        from odoo.addons.mml_freight_dsv.adapters.dsv_auth import DsvAuthError
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token',
                   side_effect=DsvAuthError('token fail')):
            events = self._adapter().get_tracking(self.booking)
        self.assertEqual(events, [])
