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
            'x_dsv_mdm': 'MDM001', 'x_dsv_subscription_key': 'SUB001',
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
            'purchase_order_id': po.id,
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
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
            with patch('requests.post', return_value=_resp(200, dsv_data)):
                results = self._adapter().request_quote(self.tender)
        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['service_name'], 'DSV Sea LCL')
        self.assertAlmostEqual(results[0]['total_rate'], 2500.0)
        self.assertEqual(results[0]['transport_mode'], 'sea_lcl')

    def test_request_quote_multiple_quotes_in_response(self):
        dsv_data = {'quotes': [
            {'serviceCode': 'A', 'serviceName': 'LCL', 'productType': 'SEA_LCL',
             'totalCharge': {'amount': 1000.0, 'currency': 'NZD'}, 'transitDays': 30},
            {'serviceCode': 'B', 'serviceName': 'Air', 'productType': 'AIR_EXPRESS',
             'totalCharge': {'amount': 5000.0, 'currency': 'NZD'}, 'transitDays': 3},
        ]}
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
            with patch('requests.post', return_value=_resp(200, dsv_data)):
                results = self._adapter().request_quote(self.tender)
        self.assertEqual(len(results), 2)

    def test_request_quote_401_retries_once(self):
        """401 triggers token refresh and one retry; both fail → error dict returned."""
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
            with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.refresh_token', return_value='T2'):
                with patch('requests.post', return_value=_resp(401)):
                    results = self._adapter().request_quote(self.tender)
        self.assertTrue(all(r.get('_error') for r in results))

    def test_request_quote_500_returns_error_dict(self):
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
            with patch('requests.post', return_value=_resp(500)):
                results = self._adapter().request_quote(self.tender)
        self.assertTrue(all(r.get('_error') for r in results))

    def test_request_quote_network_error_returns_error_dict(self):
        import requests as _req
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.get_token', return_value='T'):
            with patch('requests.post', side_effect=_req.ConnectionError('timeout')):
                results = self._adapter().request_quote(self.tender)
        self.assertTrue(all(r.get('_error') for r in results))
