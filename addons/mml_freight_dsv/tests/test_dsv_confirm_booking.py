from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter


def _resp(status=200, data=None):
    m = MagicMock()
    m.status_code = status
    m.ok = status < 400
    m.text = ''
    m.json.return_value = data or {}
    return m


class TestDsvConfirmBookingAdapter(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.service_product = cls.env['product.product'].create({
            'name': 'DSV Confirm Service',
            'type': 'service',
        })
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Confirm',
            'product_id': cls.service_product.id,
            'delivery_type': 'dsv_generic',
            'x_dsv_environment': 'production',
            'x_dsv_subscription_key': 'SUB001',
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_booking_id': 'DSVBK001',
            'state': 'draft',
        })

    def _adapter(self):
        return DsvGenericAdapter(self.carrier, self.env)

    def test_confirm_returns_vessel_and_eta(self):
        dsv_data = {
            'shipmentId': 'SH001',
            'vesselName': 'MSC Oscar',
            'voyageNumber': 'VOY42',
            'containerNumber': 'CONT001',
            'estimatedDelivery': '2026-06-15T00:00:00Z',
        }
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='T'):
            with patch('requests.post', return_value=_resp(200, dsv_data)):
                result = self._adapter().confirm_booking(self.booking)
        self.assertEqual(result['vessel_name'], 'MSC Oscar')
        self.assertEqual(result['voyage_number'], 'VOY42')
        self.assertEqual(result['container_number'], 'CONT001')
        self.assertIn('2026-06-15', result['eta'])

    def test_confirm_no_booking_id_raises(self):
        from odoo.exceptions import UserError
        empty = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': self.env.company.currency_id.id,
        })
        with self.assertRaises(UserError):
            self._adapter().confirm_booking(empty)

    def test_confirm_400_raises_user_error(self):
        from odoo.exceptions import UserError
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='T'):
            with patch('requests.post', return_value=_resp(400)):
                with self.assertRaises(UserError):
                    self._adapter().confirm_booking(self.booking)

    def test_confirm_feeder_vessel_mapped(self):
        dsv_data = {
            'shipmentId': 'SH002',
            'feederVesselName': 'Feeder A',
            'feederVoyageNumber': 'FV01',
            'estimatedDelivery': '',
        }
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='T'):
            with patch('requests.post', return_value=_resp(200, dsv_data)):
                result = self._adapter().confirm_booking(self.booking)
        self.assertEqual(result['feeder_vessel_name'], 'Feeder A')
        self.assertEqual(result['feeder_voyage_number'], 'FV01')
