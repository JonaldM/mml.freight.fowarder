import unittest
from unittest.mock import patch, MagicMock, call
from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_auth import get_token, DsvAuthError, _oauth_url
from odoo import fields
from datetime import timedelta


class TestDsvAuth(TransactionCase):
    def setUp(self):
        super().setUp()
        self.carrier = self.env['delivery.carrier'].create({
            'name': 'DSV Auth Test', 'product_id': self.env['product.product'].search([], limit=1).id,
            'delivery_type': 'dsv_generic', 'x_dsv_environment': 'demo',
        })

    def test_cached_token_not_expired(self):
        self.carrier.write({'x_dsv_environment': 'production', 'x_dsv_client_id': 'id', 'x_dsv_client_secret': 'sec',
            'x_dsv_access_token': 'CACHED', 'x_dsv_token_expiry': fields.Datetime.now() + timedelta(hours=1)})
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.requests.post') as m:
            token = get_token(self.carrier)
        self.assertEqual(token, 'CACHED')
        m.assert_not_called()

    def test_near_expiry_refreshes(self):
        self.carrier.write({'x_dsv_environment': 'production', 'x_dsv_client_id': 'id', 'x_dsv_client_secret': 'sec',
            'x_dsv_access_token': 'OLD', 'x_dsv_token_expiry': fields.Datetime.now() + timedelta(seconds=60)})
        mock_resp = MagicMock(ok=True, status_code=200)
        mock_resp.json.return_value = {'access_token': 'NEW', 'expires_in': 3600}
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.requests.post', return_value=mock_resp):
            token = get_token(self.carrier)
        self.assertEqual(token, 'NEW')

    def test_401_raises(self):
        self.carrier.write({'x_dsv_environment': 'production', 'x_dsv_client_id': 'bad', 'x_dsv_client_secret': 'bad',
            'x_dsv_access_token': False, 'x_dsv_token_expiry': False})
        mock_resp = MagicMock(ok=False, status_code=401)
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.requests.post', return_value=mock_resp):
            with self.assertRaises(DsvAuthError): get_token(self.carrier)

    def test_missing_creds_raises(self):
        self.carrier.write({'x_dsv_environment': 'production', 'x_dsv_client_id': False, 'x_dsv_client_secret': False})
        with self.assertRaises(DsvAuthError): get_token(self.carrier)

    def test_demo_uses_demo_oauth_url(self):
        """Demo environment authenticates against /my-demo/ OAuth endpoint."""
        self.carrier.write({
            'x_dsv_environment': 'demo',
            'x_dsv_client_id': 'demo-id',
            'x_dsv_client_secret': 'demo-secret',
            'x_dsv_access_token': False,
            'x_dsv_token_expiry': False,
        })
        mock_resp = MagicMock(ok=True, status_code=200)
        mock_resp.json.return_value = {'access_token': 'DEMO_REAL_TOKEN', 'expires_in': 600}
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.requests.post',
                   return_value=mock_resp) as mock_post:
            token = get_token(self.carrier)
        called_url = mock_post.call_args[0][0]
        self.assertIn('my-demo', called_url)
        self.assertNotIn('/my/', called_url.replace('my-demo', ''))
        self.assertEqual(token, 'DEMO_REAL_TOKEN')

    def test_production_uses_my_oauth_url(self):
        """Production environment authenticates against /my/ OAuth endpoint."""
        self.carrier.write({
            'x_dsv_environment': 'production',
            'x_dsv_client_id': 'prod-id',
            'x_dsv_client_secret': 'prod-secret',
            'x_dsv_access_token': False,
            'x_dsv_token_expiry': False,
        })
        mock_resp = MagicMock(ok=True, status_code=200)
        mock_resp.json.return_value = {'access_token': 'PROD_TOKEN', 'expires_in': 600}
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_auth.requests.post',
                   return_value=mock_resp) as mock_post:
            token = get_token(self.carrier)
        called_url = mock_post.call_args[0][0]
        self.assertIn('/my/', called_url)
        self.assertNotIn('my-demo', called_url)
        self.assertEqual(token, 'PROD_TOKEN')


class TestDsvOauthUrls(unittest.TestCase):
    """Pure-Python tests — no Odoo DB needed."""

    def _carrier(self, env):
        m = MagicMock()
        m.x_dsv_environment = env
        return m

    def test_demo_uses_demo_oauth_url(self):
        url = _oauth_url(self._carrier('demo'))
        self.assertIn('/my-demo/', url)
        self.assertNotIn('/my/', url.replace('/my-demo/', ''))

    def test_production_uses_my_oauth_url(self):
        url = _oauth_url(self._carrier('production'))
        self.assertIn('/my/', url)
        self.assertNotIn('demo', url)
