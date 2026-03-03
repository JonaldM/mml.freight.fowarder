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
            'x_dsv_subkey_doc_download_primary': 'SUB-DL-001',
            'x_dsv_subkey_booking_primary': 'SUB-BK-001',
            'x_dsv_subkey_quote_primary': 'SUB-QT-001',
            'x_dsv_subkey_visibility_primary': 'SUB-VIS-001',
            'x_dsv_subkey_invoicing_primary': 'SUB-INV-001',
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


class TestBookingConfirmWithDsv(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.service_product = cls.env['product.product'].create({
            'name': 'DSV Confirm Odoo Service',
            'type': 'service',
        })
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Confirm Odoo',
            'product_id': cls.service_product.id,
            'delivery_type': 'dsv_generic',
            'x_dsv_environment': 'production',
        })
        partner = cls.env['res.partner'].create({'name': 'BK Supplier'})
        cls.po = cls.env['purchase.order'].create({'partner_id': partner.id})

    def _fresh_booking(self):
        return self.env['freight.booking'].create({
            'carrier_id':         self.carrier.id,
            'currency_id':        self.env.company.currency_id.id,
            'carrier_booking_id': 'DSVBK_CONF',
            'po_ids':             [(4, self.po.id)],
            'state':              'draft',
        })

    def _mock_confirm(self, result=None):
        from unittest.mock import MagicMock
        m = MagicMock()
        m.confirm_booking.return_value = result or {
            'carrier_shipment_id':  'SH99',
            'vessel_name':          'Ever Given',
            'voyage_number':        'VOY99',
            'container_number':     'CONT99',
            'bill_of_lading':       '',
            'feeder_vessel_name':   '',
            'feeder_voyage_number': '',
            'eta':                  '2026-07-01T00:00:00Z',
        }
        return m

    def test_action_confirm_with_dsv_state_becomes_confirmed(self):
        from unittest.mock import patch
        b = self._fresh_booking()
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=self._mock_confirm(),
        ):
            b.action_confirm_with_dsv()
        self.assertEqual(b.state, 'confirmed')

    def test_action_confirm_with_dsv_stores_vessel(self):
        from unittest.mock import patch
        b = self._fresh_booking()
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=self._mock_confirm(),
        ):
            b.action_confirm_with_dsv()
        self.assertEqual(b.vessel_name, 'Ever Given')
        self.assertEqual(b.voyage_number, 'VOY99')

    def test_action_confirm_with_dsv_posts_chatter(self):
        from unittest.mock import patch
        b = self._fresh_booking()
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=self._mock_confirm(),
        ):
            b.action_confirm_with_dsv()
        msgs = b.message_ids.filtered(
            lambda m: 'confirmed with DSV' in (m.body or '')
        )
        self.assertEqual(len(msgs), 1, 'Expected exactly one confirm chatter message')

    def test_action_cancel_calls_adapter_cancel(self):
        from unittest.mock import patch, MagicMock
        b = self._fresh_booking()
        mock_adapter = MagicMock()
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=mock_adapter,
        ):
            b.action_cancel()
        mock_adapter.cancel_booking.assert_called_once_with(b)
        self.assertEqual(b.state, 'cancelled')
