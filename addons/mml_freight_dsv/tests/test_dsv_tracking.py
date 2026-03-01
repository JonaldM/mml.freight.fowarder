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

    def test_unknown_event_type_falls_back_to_lower(self):
        """Unknown DSV event types fall back to lowercased string."""
        data = {'events': [{'eventType': 'EXCEPTION_HOLD', 'eventDate': '2026-05-01T00:00:00Z'}]}
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='T'):
            with patch('requests.get', return_value=_resp(200, data)):
                events = self._adapter().get_tracking(self.booking)
        self.assertEqual(events[0]['status'], 'exception_hold')


class TestEtaDriftDetection(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.service_product = cls.env['product.product'].create({
            'name': 'ETA Service Product',
            'type': 'service',
        })
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'ETA Drift',
            'product_id': cls.service_product.id,
            'delivery_type': 'dsv_generic',
        })
        from datetime import datetime
        cls.orig_eta = datetime(2026, 6, 15)
        partner = cls.env['res.partner'].create({'name': 'DS'})
        cls.po  = cls.env['purchase.order'].create({'partner_id': partner.id})
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id':          cls.carrier.id,
            'currency_id':         cls.env.company.currency_id.id,
            'carrier_shipment_id': 'SH_ETA',
            'po_ids':              [(4, cls.po.id)],
            'state':               'in_transit',
            'eta':                 cls.orig_eta,
            'vessel_name':         '',
        })

    def test_no_update_when_eta_change_under_24h(self):
        from datetime import timedelta
        from unittest.mock import patch
        self.booking.eta = self.orig_eta + timedelta(hours=2)
        with patch.object(self.booking, '_queue_inward_order_update') as m:
            self.booking._check_inward_order_updates(self.orig_eta, '')
        m.assert_not_called()

    def test_update_queued_when_eta_drifts_over_24h(self):
        from datetime import timedelta
        from unittest.mock import patch
        self.booking.eta = self.orig_eta + timedelta(hours=25)
        with patch.object(self.booking, '_queue_inward_order_update') as m:
            self.booking._check_inward_order_updates(self.orig_eta, '')
        m.assert_called_once()

    def test_update_queued_when_vessel_becomes_known(self):
        from unittest.mock import patch
        self.booking.vessel_name = 'MSC Oscar'
        with patch.object(self.booking, '_queue_inward_order_update') as m:
            self.booking._check_inward_order_updates(self.orig_eta, '')  # prev_vessel = ''
        m.assert_called_once()

    def test_no_update_when_vessel_was_already_known(self):
        from unittest.mock import patch
        self.booking.vessel_name = 'MSC Oscar'
        with patch.object(self.booking, '_queue_inward_order_update') as m:
            self.booking._check_inward_order_updates(self.orig_eta, 'MSC Oscar')
        m.assert_not_called()
