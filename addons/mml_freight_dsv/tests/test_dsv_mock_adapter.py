import json
from unittest.mock import MagicMock
from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_mock_adapter import DsvMockAdapter


def _build_mock_resp(status, data):
    m = MagicMock()
    m.status_code = status
    m.ok = status < 400
    m.text = json.dumps(data)
    m.json.return_value = data
    return m


class TestDsvMockAdapter(TransactionCase):
    def setUp(self):
        super().setUp()
        self.carrier = self.env['delivery.carrier'].create({
            'name': 'Mock Test', 'product_id': self.env['product.product'].search([], limit=1).id,
            'delivery_type': 'dsv_generic', 'x_dsv_environment': 'demo',
        })
        self.adapter = DsvMockAdapter(self.carrier, self.env)

    def _tender(self):
        p = self.env['res.partner'].create({'name': 'Mock S'})
        po = self.env['purchase.order'].create({'partner_id': p.id})
        return self.env['freight.tender'].create({'po_ids': [(4, po.id)], 'company_id': self.env.company.id, 'currency_id': self.env.company.currency_id.id})

    def test_two_quotes(self): self.assertEqual(len(self.adapter.request_quote(self._tender())), 2)
    def test_road_quote(self):
        q = next(x for x in self.adapter.request_quote(self._tender()) if x['transport_mode'] == 'road')
        self.assertEqual(q['service_name'], 'DSV Road Standard')
        self.assertAlmostEqual(q['total_rate'], 1800.0)
        self.assertEqual(q['transit_days'], 5)
    def test_air_quote(self):
        q = next(x for x in self.adapter.request_quote(self._tender()) if x['transport_mode'] == 'air')
        self.assertAlmostEqual(q['total_rate'], 6200.0)
    def test_mock_booking_ref(self):
        t = self._tender()
        nzd = self.env['res.currency'].search([('name','=','NZD')], limit=1) or self.env.company.currency_id
        q = self.env['freight.tender.quote'].create({'tender_id': t.id, 'carrier_id': self.carrier.id, 'state': 'received', 'currency_id': nzd.id, 'base_rate': 1800.0})
        r = self.adapter.create_booking(t, q)
        self.assertTrue(r['carrier_booking_id'].startswith('DSV-MOCK-BK-'))
    def test_tracking_events(self):
        b = self.env['freight.booking'].create({'carrier_id': self.carrier.id, 'currency_id': self.env.company.currency_id.id})
        events = self.adapter.get_tracking(b)
        self.assertEqual(len(events), 3)
        self.assertIn('Picked Up', [e['status'] for e in events])
    def test_production_delegates_request_quote_to_generic(self):
        """In production mode, DsvMockAdapter forwards to DsvGenericAdapter (mocked HTTP)."""
        from unittest.mock import patch
        self.carrier.x_dsv_environment = 'production'
        dsv_data = {'quotes': [{'serviceCode': 'X', 'serviceName': 'Sea', 'productType': 'SEA_LCL',
                                 'totalCharge': {'amount': 1000.0, 'currency': 'NZD'}, 'transitDays': 20}]}
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
            with patch('requests.post', return_value=_build_mock_resp(200, dsv_data)):
                results = self.adapter.request_quote(self._tender())
        self.assertIsInstance(results, list)
        self.assertTrue(any(not r.get('_error') for r in results))
        self.carrier.x_dsv_environment = 'demo'  # restore

    def test_demo_still_returns_mock_quotes(self):
        self.carrier.x_dsv_environment = 'demo'
        results = self.adapter.request_quote(self._tender())
        self.assertEqual(len(results), 2)

    def test_cbm_threshold_fields_exist(self):
        self.carrier.x_dsv_lcl_fcl_threshold = 15.0
        self.carrier.x_dsv_fcl20_fcl40_threshold = 25.0
        self.carrier.x_dsv_fcl40_upper = 40.0
        self.assertAlmostEqual(self.carrier.x_dsv_lcl_fcl_threshold, 15.0)

    def test_feeder_vessel_fields_exist(self):
        b = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': self.env.company.currency_id.id,
        })
        b.feeder_vessel_name = 'MSC Flaminia'
        b.feeder_voyage_number = 'FV001'
        self.assertEqual(b.feeder_vessel_name, 'MSC Flaminia')
