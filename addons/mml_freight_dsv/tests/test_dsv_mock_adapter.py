from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_mock_adapter import DsvMockAdapter


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
        return self.env['freight.tender'].create({'purchase_order_id': po.id, 'company_id': self.env.company.id, 'currency_id': self.env.company.currency_id.id})

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
    def test_live_raises(self):
        self.carrier.x_dsv_environment = 'production'
        with self.assertRaises(NotImplementedError): self.adapter.request_quote(self._tender())
