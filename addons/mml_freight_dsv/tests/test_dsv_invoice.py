from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase


class TestDsvInvoice(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Invoice Test',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
            'x_dsv_environment': 'production',
        })
        supplier = cls.env['res.partner'].create({'name': 'DSV Inv Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        tender = cls.env['freight.tender'].create({
            'purchase_order_id': po.id,
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': tender.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_shipment_id': 'SCPH-TEST',
        })

    def test_get_invoice_returns_dict_on_200(self):
        """DsvGenericAdapter.get_invoice parses amount and invoice ID from 200 response."""
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter
        adapter = DsvGenericAdapter(self.carrier, self.env)
        mock_resp = MagicMock(ok=True, status_code=200)
        mock_resp.json.return_value = {
            'invoiceId': 'DSV-INV-999',
            'totalAmount': 2345.67,
            'currency': 'USD',
            'invoiceDate': '2026-02-28',
        }
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='tok'), \
             patch('requests.get', return_value=mock_resp):
            result = adapter.get_invoice(self.booking)
        self.assertIsNotNone(result)
        self.assertEqual(result['dsv_invoice_id'], 'DSV-INV-999')
        self.assertAlmostEqual(result['amount'], 2345.67, places=2)
        self.assertEqual(result['currency'], 'USD')

    def test_get_invoice_returns_none_on_404(self):
        """DsvGenericAdapter.get_invoice returns None for 404 (not yet invoiced)."""
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter
        adapter = DsvGenericAdapter(self.carrier, self.env)
        mock_resp = MagicMock(ok=False, status_code=404)
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token', return_value='tok'), \
             patch('requests.get', return_value=mock_resp):
            result = adapter.get_invoice(self.booking)
        self.assertIsNone(result)

    def test_get_invoice_returns_none_when_no_shipment_id(self):
        """DsvGenericAdapter.get_invoice returns None immediately when carrier_shipment_id unset."""
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter
        adapter = DsvGenericAdapter(self.carrier, self.env)
        self.booking.carrier_shipment_id = ''
        result = adapter.get_invoice(self.booking)
        self.assertIsNone(result)
