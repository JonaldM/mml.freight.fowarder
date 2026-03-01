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

    def test_create_booking_returns_requires_manual_confirmation(self):
        """Demo create_booking must return requires_manual_confirmation=True to match production.

        Bug: the mock adapter omits this key, so action_book() calls action_confirm()
        instead of waiting for the manual action_confirm_with_dsv() step — bypassing
        the two-step DSV flow and leaving 3PL messages stuck in draft (Bug 1 compounded).
        """
        t = self._tender()
        nzd = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or self.env.company.currency_id
        q = self.env['freight.tender.quote'].create({
            'tender_id': t.id, 'carrier_id': self.carrier.id,
            'state': 'received', 'currency_id': nzd.id, 'base_rate': 1800.0,
        })
        result = self.adapter.create_booking(t, q)
        self.assertTrue(
            result.get('requires_manual_confirmation'),
            'Demo create_booking must return requires_manual_confirmation=True to match live DSV behaviour',
        )

    def test_handle_webhook_accesses_booking_model_via_sudo(self):
        """handle_webhook must call .sudo() before _handle_dsv_tracking_webhook.

        Bug: DsvMockAdapter.handle_webhook calls self.env['freight.booking']._handle_dsv_...
        directly without .sudo(), unlike mf_adapter.handle_webhook and the DSV webhook
        controller which both use .sudo(). This causes AccessError in low-privilege contexts.
        """
        from unittest.mock import patch

        sudo_calls = []
        booking_model = self.env['freight.booking']
        original_sudo = type(booking_model).sudo

        def recording_sudo(self_model, *args, **kwargs):
            sudo_calls.append(True)
            return original_sudo(self_model, *args, **kwargs)

        with patch.object(type(booking_model), 'sudo', recording_sudo):
            # Minimal body — no matching booking so handler exits early, but sudo must still be called
            self.adapter.handle_webhook({'shipmentId': 'NONEXISTENT', 'events': []})

        self.assertTrue(
            sudo_calls,
            'handle_webhook must call .sudo() on freight.booking before _handle_dsv_tracking_webhook',
        )

    def test_feeder_vessel_fields_exist(self):
        b = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': self.env.company.currency_id.id,
        })
        b.feeder_vessel_name = 'MSC Flaminia'
        b.feeder_voyage_number = 'FV001'
        self.assertEqual(b.feeder_vessel_name, 'MSC Flaminia')
