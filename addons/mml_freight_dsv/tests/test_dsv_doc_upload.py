import base64
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter


def _resp(status=200, json_data=None, ok=None):
    m = MagicMock()
    m.status_code = status
    m.ok = (status < 400) if ok is None else ok
    if json_data is not None:
        m.json.return_value = json_data
    return m


class TestDsvDocUpload(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.product'].create(
            {'name': 'Upload Test Product', 'type': 'service'}
        )
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Upload Carrier',
            'product_id': cls.product.id,
            'delivery_type': 'dsv_generic',
            'x_dsv_environment': 'production',
            'x_dsv_subkey_doc_upload_primary': 'SUB-UPLOAD-001',
        })
        supplier = cls.env['res.partner'].create({'name': 'Upload Supplier'})
        cls.po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        tender = cls.env['freight.tender'].create({
            'po_ids': [(4, cls.po.id)],
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': tender.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_booking_id': 'BK-UPLOAD-001',
        })

    def _adapter(self):
        return DsvGenericAdapter(self.carrier, self.env)

    def test_upload_success_returns_document_id(self):
        """200 response with documentId → returns that ref."""
        mock_resp = _resp(status=200, json_data={'documentId': 'DSV-DOC-UPLOAD-99'})
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token',
                   return_value='tok'), \
             patch('requests.post', return_value=mock_resp):
            result = self._adapter().upload_document(
                self.booking, 'MML-PI-001.pdf', b'%PDF-content', 'INV'
            )
        self.assertEqual(result, 'DSV-DOC-UPLOAD-99')

    def test_upload_uses_doc_upload_subscription_key(self):
        """DSV-Subscription-Key header uses the doc_upload key, not another."""
        mock_resp = _resp(status=200, json_data={'documentId': 'REF-1'})
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token',
                   return_value='tok'), \
             patch('requests.post', return_value=mock_resp) as mock_post:
            self._adapter().upload_document(
                self.booking, 'packing.pdf', b'bytes', 'PKL'
            )
        call_headers = mock_post.call_args[1]['headers']
        self.assertEqual(call_headers['DSV-Subscription-Key'], 'SUB-UPLOAD-001')

    def test_upload_failure_returns_none(self):
        """Non-2xx response → returns None (non-raising)."""
        mock_resp = _resp(status=413)
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token',
                   return_value='tok'), \
             patch('requests.post', return_value=mock_resp):
            result = self._adapter().upload_document(
                self.booking, 'huge.pdf', b'big', 'PKL'
            )
        self.assertIsNone(result)

    def test_upload_no_booking_id_returns_none(self):
        """Booking with no carrier_booking_id → returns None without HTTP call."""
        booking_no_ref = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': self.env.company.currency_id.id,
        })
        with patch('requests.post') as mock_post:
            result = self._adapter().upload_document(
                booking_no_ref, 'test.pdf', b'bytes', 'INV'
            )
        self.assertIsNone(result)
        mock_post.assert_not_called()

    def test_upload_retries_on_401(self):
        """401 response → refresh token → retry POST."""
        resp_401 = _resp(status=401)
        resp_200 = _resp(status=200, json_data={'documentId': 'RETRY-REF'})
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token',
                   return_value='old-tok'), \
             patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.refresh_token',
                   return_value='new-tok'), \
             patch('requests.post', side_effect=[resp_401, resp_200]):
            result = self._adapter().upload_document(
                self.booking, 'pi.pdf', b'bytes', 'INV'
            )
        self.assertEqual(result, 'RETRY-REF')
