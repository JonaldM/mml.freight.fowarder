from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter


def _resp(status=200, json_data=None, content=b'', ok=None):
    m = MagicMock()
    m.status_code = status
    m.ok = (status < 400) if ok is None else ok
    m.content = content
    if json_data is not None:
        m.json.return_value = json_data
    return m


class TestDsvDocuments(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.service_product = cls.env['product.product'].create({
            'name': 'Docs Service Product',
            'type': 'service',
        })
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Docs Carrier',
            'product_id': cls.service_product.id,
            'delivery_type': 'dsv_generic',
            'x_dsv_environment': 'production',
            'x_dsv_subkey_doc_download_primary': 'SUB-DL-001',
            'x_dsv_subkey_booking_primary': 'SUB-BK-001',
            'x_dsv_subkey_quote_primary': 'SUB-QT-001',
            'x_dsv_subkey_visibility_primary': 'SUB-VIS-001',
            'x_dsv_subkey_invoicing_primary': 'SUB-INV-001',
        })
        supplier = cls.env['res.partner'].create({'name': 'DSV Docs Supplier'})
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
            'carrier_booking_id': 'BK-DSV-DOCS-001',
        })

    def _adapter(self):
        return DsvGenericAdapter(self.carrier, self.env)

    def test_get_documents_returns_downloaded_docs(self):
        """List response + download response → correct dict with doc_type='pod', bytes, carrier_doc_ref."""
        list_response = _resp(
            status=200,
            json_data=[
                {
                    'documentType': 'POD',
                    'documentId': 'DSV-DOC-42',
                    'fileName': 'pod_42.pdf',
                    'downloadUrl': 'https://api.dsv.com/download/v1/documents/DSV-DOC-42',
                }
            ],
        )
        download_response = _resp(status=200, content=b'%PDF-1.4-pod-bytes')

        def _mock_get(url, **kwargs):
            if 'documents/DSV-DOC-42' in url:
                return download_response
            return list_response

        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token',
                   return_value='tok'), \
             patch('requests.get', side_effect=_mock_get):
            result = self._adapter().get_documents(self.booking)

        self.assertEqual(len(result), 1)
        doc = result[0]
        self.assertEqual(doc['doc_type'], 'pod')
        self.assertEqual(doc['bytes'], b'%PDF-1.4-pod-bytes')
        self.assertEqual(doc['carrier_doc_ref'], 'DSV-DOC-42')
        self.assertEqual(doc['filename'], 'pod_42.pdf')

    def test_get_documents_returns_empty_on_api_error(self):
        """List response returns 503 → get_documents returns []."""
        error_response = _resp(status=503)
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token',
                   return_value='tok'), \
             patch('requests.get', return_value=error_response):
            result = self._adapter().get_documents(self.booking)
        self.assertEqual(result, [])
