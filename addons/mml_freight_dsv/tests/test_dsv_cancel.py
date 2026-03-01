from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter


def _resp(status=200):
    m = MagicMock()
    m.status_code = status
    m.ok = status < 400
    return m


class TestDsvCancel(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.service_product = cls.env['product.product'].create({
            'name': 'DSV Cancel Service',
            'type': 'service',
        })
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Cancel',
            'product_id': cls.service_product.id,
            'delivery_type': 'dsv_generic',
            'x_dsv_environment': 'production',
            'x_dsv_client_id': 'id', 'x_dsv_client_secret': 'sec',
            'x_dsv_subscription_key': 'SUB001',
        })

    def _booking(self, bk_id, state='draft'):
        return self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': self.env.company.currency_id.id,
            'carrier_booking_id': bk_id,
            'state': state,
        })

    def _adapter(self):
        return DsvGenericAdapter(self.carrier, self.env)

    def test_cancel_draft_calls_delete_with_booking_id_in_url(self):
        b = self._booking('DSVBK_CANCEL')
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='T'):
            with patch('requests.delete', return_value=_resp(204)) as mock_del:
                self._adapter().cancel_booking(b)
        mock_del.assert_called_once()
        self.assertIn('DSVBK_CANCEL', mock_del.call_args[0][0])

    def test_cancel_404_does_not_raise(self):
        b = self._booking('DSVBK_GONE')
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='T'):
            with patch('requests.delete', return_value=_resp(404)):
                self._adapter().cancel_booking(b)  # must not raise

    def test_cancel_no_booking_id_is_noop(self):
        b = self._booking('')
        with patch('requests.delete') as mock_del:
            self._adapter().cancel_booking(b)
        mock_del.assert_not_called()

    def test_cancel_confirmed_booking_skips_api_and_posts_chatter(self):
        b = self._booking('DSVBK_CONF', state='confirmed')
        with patch('requests.delete') as mock_del:
            self._adapter().cancel_booking(b)
        mock_del.assert_not_called()
        msgs = b.message_ids.filtered(lambda m: 'Contact DSV directly' in (m.body or ''))
        self.assertTrue(msgs, 'Expected chatter warning for confirmed booking cancel')
