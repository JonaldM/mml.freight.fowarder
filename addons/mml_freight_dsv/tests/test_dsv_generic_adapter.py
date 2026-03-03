import unittest
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter


def _resp(status=200, data=None):
    m = MagicMock()
    m.status_code = status
    m.ok = status < 400
    m.text = str(data or '')
    m.json.return_value = data or {}
    return m


class TestDsvGenericAdapter(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.service_product = cls.env['product.product'].create({
            'name': 'DSV Test Service',
            'type': 'service',
        })
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Live',
            'product_id': cls.service_product.id,
            'delivery_type': 'dsv_generic',
            'x_dsv_environment': 'production',
            'x_dsv_client_id': 'cid', 'x_dsv_client_secret': 'csec',
            'x_dsv_mdm': 'MDM001',
            'x_dsv_subkey_doc_download_primary': 'SUB-DL-001',
            'x_dsv_subkey_doc_upload_primary':   'SUB-UL-001',
            'x_dsv_subkey_booking_primary': 'SUB-BK-001',
            'x_dsv_subkey_quote_primary': 'SUB-QT-001',
            'x_dsv_subkey_visibility_primary': 'SUB-VIS-001',
            'x_dsv_subkey_invoicing_primary': 'SUB-INV-001',
            'x_dsv_lcl_fcl_threshold': 15.0,
            'x_dsv_fcl20_fcl40_threshold': 25.0,
            'x_dsv_fcl40_upper': 40.0,
        })
        origin = cls.env['res.partner'].create({
            'name': 'Sup', 'country_id': cls.env.ref('base.cn').id,
            'city': 'SH', 'zip': '200001', 'street': '1 Main',
        })
        dest = cls.env['res.partner'].create({
            'name': 'WH', 'country_id': cls.env.ref('base.nz').id,
            'city': 'AKL', 'zip': '0600', 'street': '2 Freight',
        })
        po = cls.env['purchase.order'].create({'partner_id': origin.id})
        cls.tender = cls.env['freight.tender'].create({
            'po_ids': [(4, po.id)],
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
            'origin_partner_id': origin.id,
            'origin_country_id': cls.env.ref('base.cn').id,
            'dest_partner_id': dest.id,
            'dest_country_id': cls.env.ref('base.nz').id,
            'requested_pickup_date': '2026-05-01',
        })
        cls.env['freight.tender.package'].create({
            'tender_id': cls.tender.id,
            'description': 'Widget', 'quantity': 5,
            'weight_kg': 10.0, 'length_cm': 40.0,
            'width_cm': 30.0, 'height_cm': 20.0,
        })

    def _adapter(self):
        return DsvGenericAdapter(self.carrier, self.env)

    # --- request_quote ---

    def test_request_quote_returns_list(self):
        dsv_data = {'quotes': [{
            'serviceCode': 'SVC001', 'serviceName': 'DSV Sea LCL',
            'productType': 'SEA_LCL',
            'totalCharge': {'amount': 2500.0, 'currency': 'NZD'},
            'transitDays': 25,
        }]}
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='T'):
            with patch('requests.post', return_value=_resp(200, dsv_data)):
                results = self._adapter().request_quote(self.tender)
        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 1)
        q = results[0]
        self.assertEqual(q['service_name'], 'DSV Sea LCL')
        self.assertAlmostEqual(q['total_rate'], 2500.0)
        self.assertEqual(q['transport_mode'], 'sea_lcl')
        self.assertEqual(q['carrier_quote_ref'], 'SVC001')
        self.assertEqual(q['currency'], 'NZD')
        self.assertAlmostEqual(q['transit_days'], 25.0)
        self.assertIn('SVC001', q['raw_response'])  # raw response contains the service code

    def test_request_quote_multiple_quotes_in_response(self):
        dsv_data = {'quotes': [
            {'serviceCode': 'A', 'serviceName': 'LCL', 'productType': 'SEA_LCL',
             'totalCharge': {'amount': 1000.0, 'currency': 'NZD'}, 'transitDays': 30},
            {'serviceCode': 'B', 'serviceName': 'Air', 'productType': 'AIR_EXPRESS',
             'totalCharge': {'amount': 5000.0, 'currency': 'NZD'}, 'transitDays': 3},
        ]}
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='T'):
            with patch('requests.post', return_value=_resp(200, dsv_data)):
                results = self._adapter().request_quote(self.tender)
        self.assertEqual(len(results), 2)

    def test_request_quote_401_retries_once(self):
        """401 triggers token refresh and one retry; both fail → error dict returned."""
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='T'):
            with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.refresh_token', return_value='T2'):
                with patch('requests.post', return_value=_resp(401)) as mock_post:
                    results = self._adapter().request_quote(self.tender)
        self.assertTrue(all(r.get('_error') for r in results))
        # Should have been called twice: initial attempt + one retry after token refresh
        self.assertEqual(mock_post.call_count, 2)

    def test_request_quote_500_returns_error_dict(self):
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='T'):
            with patch('requests.post', return_value=_resp(500)):
                results = self._adapter().request_quote(self.tender)
        self.assertTrue(all(r.get('_error') for r in results))

    def test_request_quote_network_error_returns_error_dict(self):
        import requests as _req
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='T'):
            with patch('requests.post', side_effect=_req.ConnectionError('timeout')):
                results = self._adapter().request_quote(self.tender)
        self.assertTrue(all(r.get('_error') for r in results))

    def test_request_quote_auth_failure_returns_error_dict(self):
        """If get_token() raises DsvAuthError, request_quote returns error dicts."""
        from odoo.addons.mml_freight_dsv.adapters.dsv_auth import DsvAuthError
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token',
                   side_effect=DsvAuthError('bad credentials')):
            results = self._adapter().request_quote(self.tender)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].get('_error'))

    # --- create_booking ---

    def _quote(self):
        nzd = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1) \
              or self.env.company.currency_id
        return self.env['freight.tender.quote'].create({
            'tender_id': self.tender.id, 'carrier_id': self.carrier.id,
            'state': 'received', 'currency_id': nzd.id,
            'carrier_quote_ref': 'QREF99', 'transport_mode': 'sea_lcl',
        })

    def test_create_booking_returns_refs(self):
        dsv_data = {'bookingId': 'DSVBK001', 'shipmentId': 'DSVSH001'}
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='T'):
            with patch('requests.post', return_value=_resp(200, dsv_data)):
                result = self._adapter().create_booking(self.tender, self._quote())
        self.assertEqual(result['carrier_booking_id'], 'DSVBK001')
        self.assertEqual(result['carrier_shipment_id'], 'DSVSH001')
        self.assertTrue(result['requires_manual_confirmation'])
        self.assertIn('carrier_tracking_url', result)

    def test_create_booking_422_raises_user_error(self):
        from odoo.exceptions import UserError
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='T'):
            with patch('requests.post', return_value=_resp(422, {'error': 'invalid payload'})):
                with self.assertRaises(UserError):
                    self._adapter().create_booking(self.tender, self._quote())



class TestDsvBaseUrls(unittest.TestCase):

    def _carrier(self, env):
        m = MagicMock()
        m.x_dsv_environment = env
        return m

    def test_generic_base_demo(self):
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import _generic_base
        self.assertEqual(_generic_base(self._carrier('demo')), 'https://api.dsv.com/my-demo')

    def test_generic_base_production(self):
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import _generic_base
        self.assertEqual(_generic_base(self._carrier('production')), 'https://api.dsv.com/my')

    def test_quote_base_demo(self):
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import _quote_base
        self.assertEqual(_quote_base(self._carrier('demo')), 'https://api.dsv.com/qs-demo')

    def test_quote_base_production(self):
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import _quote_base
        self.assertEqual(_quote_base(self._carrier('production')), 'https://api.dsv.com/qs')

    def test_generic_base_unknown_env_falls_back_to_production(self):
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import _generic_base
        self.assertEqual(_generic_base(self._carrier('staging')), 'https://api.dsv.com/my')

    def test_quote_base_unknown_env_falls_back_to_production(self):
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import _quote_base
        self.assertEqual(_quote_base(self._carrier('staging')), 'https://api.dsv.com/qs')
