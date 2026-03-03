from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter


def _resp(status=200, content=b'', ok=None):
    m = MagicMock()
    m.status_code = status
    m.ok = (status < 400) if ok is None else ok
    m.content = content
    return m


class TestDsvLabel(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.service_product = cls.env['product.product'].create({
            'name': 'Label Service Product',
            'type': 'service',
        })
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Label Carrier',
            'product_id': cls.service_product.id,
            'delivery_type': 'dsv_generic',
            'x_dsv_environment': 'production',
            'x_dsv_subkey_doc_download_primary': 'SUB-DL-001',
            'x_dsv_subkey_booking_primary': 'SUB-BK-001',
            'x_dsv_subkey_quote_primary': 'SUB-QT-001',
            'x_dsv_subkey_visibility_primary': 'SUB-VIS-001',
            'x_dsv_subkey_invoicing_primary': 'SUB-INV-001',
        })
        supplier = cls.env['res.partner'].create({'name': 'DSV Label Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        tender = cls.env['freight.tender'].create({
            'po_ids': [(4, po.id)],
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': tender.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_booking_id': 'BK-DSV-LABEL-001',
        })

    def _adapter(self):
        return DsvGenericAdapter(self.carrier, self.env)

    def test_get_label_returns_bytes_on_200(self):
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter
        adapter = DsvGenericAdapter(self.carrier, self.env)
        fake_pdf = b'%PDF-1.4 label bytes'
        mock_resp = MagicMock(ok=True, content=fake_pdf, status_code=200)
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='tok'), \
             patch('requests.get', return_value=mock_resp) as mock_get:
            result = adapter.get_label(self.booking)
        self.assertEqual(result, fake_pdf)
        call_kwargs = mock_get.call_args
        self.assertIn('printFormat=Portrait1Label', str(call_kwargs),
                      'get_label must pass printFormat=Portrait1Label as a query param')

    def test_get_label_returns_none_on_404(self):
        """HTTP 404 → get_label returns None (not an error)."""
        mock_resp = _resp(status=404)
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token',
                   return_value='tok'), \
             patch('requests.get', return_value=mock_resp):
            result = self._adapter().get_label(self.booking)
        self.assertIsNone(result)

    def test_get_label_returns_none_when_no_booking_id(self):
        """carrier_booking_id='' → returns None immediately (no HTTP call made)."""
        empty_booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': self.env.company.currency_id.id,
            'carrier_booking_id': '',
        })
        with patch('requests.get') as mock_get:
            result = self._adapter().get_label(empty_booking)
        self.assertIsNone(result)
        mock_get.assert_not_called()
