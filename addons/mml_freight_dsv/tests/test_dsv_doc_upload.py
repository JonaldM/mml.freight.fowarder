import base64
import unittest
from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase
from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter
from odoo.addons.mml_freight_dsv.wizards.dsv_doc_upload_wizard import detect_dsv_type


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

    def test_upload_url_has_type_in_path_before_booking_id(self):
        """Upload URL must be .../bookingId/{doc_type}/{booking_id}."""
        mock_resp = _resp(status=200, json_data={'documentId': 'REF-URL'})
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token',
                   return_value='tok'), \
             patch('requests.post', return_value=mock_resp) as mock_post:
            self._adapter().upload_document(
                self.booking, 'pi.pdf', b'bytes', 'INV'
            )
        called_url = mock_post.call_args[0][0]
        self.assertIn('bookingId/INV/BK-UPLOAD-001', called_url)

    def test_upload_body_has_no_document_type_field(self):
        """Upload POST body must NOT include document_type — type belongs in the URL path."""
        mock_resp = _resp(status=200, json_data={'documentId': 'REF-BODY'})
        with patch('odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter.get_token',
                   return_value='tok'), \
             patch('requests.post', return_value=mock_resp) as mock_post:
            self._adapter().upload_document(
                self.booking, 'pi.pdf', b'bytes', 'PKL'
            )
        call_kwargs = mock_post.call_args[1]
        self.assertNotIn('document_type', call_kwargs.get('data', {}))


class TestDetectDsvType(unittest.TestCase):

    def test_pi_filename_detects_inv(self):
        self.assertEqual(detect_dsv_type('MML-PI-PO001.pdf'), 'INV')

    def test_invoice_filename_detects_inv(self):
        self.assertEqual(detect_dsv_type('Commercial_Invoice_March.pdf'), 'INV')

    def test_packing_list_detects_pkl(self):
        self.assertEqual(detect_dsv_type('Packing_List_v2.xlsx'), 'PKL')

    def test_pkl_abbreviation_detects_pkl(self):
        self.assertEqual(detect_dsv_type('PKL-PO123.pdf'), 'PKL')

    def test_quarantine_detects_cus(self):
        self.assertEqual(detect_dsv_type('Quarantine_Certificate.pdf'), 'CUS')

    def test_phyto_detects_cus(self):
        self.assertEqual(detect_dsv_type('Phytosanitary_Cert.pdf'), 'CUS')

    def test_unknown_filename_detects_gds(self):
        self.assertEqual(detect_dsv_type('random_document.pdf'), 'GDS')

    def test_case_insensitive(self):
        self.assertEqual(detect_dsv_type('PACKING_LIST.PDF'), 'PKL')


class TestDsvDocUploadWizard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.product'].create(
            {'name': 'Wizard Test Product', 'type': 'service'}
        )
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Wizard Carrier',
            'product_id': cls.product.id,
            'delivery_type': 'dsv_generic',
            'x_dsv_environment': 'demo',
        })
        cls.supplier = cls.env['res.partner'].create({'name': 'Wizard Supplier'})
        cls.po = cls.env['purchase.order'].create({'partner_id': cls.supplier.id})
        tender = cls.env['freight.tender'].create({
            'po_ids': [(4, cls.po.id)],
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': tender.id,
            'po_ids': [(4, cls.po.id)],
            'currency_id': cls.env.company.currency_id.id,
            'carrier_booking_id': 'WIZ-BK-001',
            'state': 'confirmed',
        })

    def _make_attachment(self, name, content=b'%PDF-test', po=None):
        po = po or self.po
        return self.env['ir.attachment'].create({
            'name': name,
            'res_model': 'purchase.order',
            'res_id': po.id,
            'datas': base64.b64encode(content).decode(),
            'mimetype': 'application/pdf',
        })

    def test_default_get_populates_lines_from_po_attachments(self):
        """Wizard lines are auto-populated from PO attachments."""
        att = self._make_attachment('MML-PI-001.pdf')
        wizard = self.env['freight.dsv.doc.upload.wizard'].with_context(
            default_po_id=self.po.id
        ).create({'po_id': self.po.id})
        att_ids = wizard.line_ids.mapped('attachment_id').ids
        self.assertIn(att.id, att_ids)

    def test_keyword_detection_applied_to_lines(self):
        """PI filename → line.dsv_type == INV."""
        self._make_attachment('Proforma_Invoice.pdf')
        wizard = self.env['freight.dsv.doc.upload.wizard'].with_context(
            default_po_id=self.po.id
        ).create({'po_id': self.po.id})
        pi_lines = wizard.line_ids.filtered(
            lambda l: 'invoice' in (l.attachment_id.name or '').lower()
        )
        self.assertTrue(pi_lines)
        self.assertEqual(pi_lines[0].dsv_type, 'INV')

    def test_oversized_file_pre_unchecked(self):
        """Attachment > 3MB is pre-unchecked in the wizard."""
        big_content = b'X' * (3 * 1024 * 1024 + 1)
        att = self._make_attachment('BigFile.pdf', content=big_content)
        wizard = self.env['freight.dsv.doc.upload.wizard'].with_context(
            default_po_id=self.po.id
        ).create({'po_id': self.po.id})
        big_line = wizard.line_ids.filtered(lambda l: l.attachment_id.id == att.id)
        if big_line:
            self.assertFalse(big_line[0].include)

    def test_action_upload_creates_freight_document_on_success(self):
        """Successful upload creates freight.document with uploaded_to_carrier=True."""
        att = self._make_attachment('MML-PKL-001.pdf')
        wizard = self.env['freight.dsv.doc.upload.wizard'].create({
            'po_id': self.po.id,
            'line_ids': [(0, 0, {
                'attachment_id': att.id,
                'dsv_type': 'PKL',
                'include': True,
            })],
        })
        wizard.action_upload()
        doc = self.env['freight.document'].search([
            ('booking_id', '=', self.booking.id),
            ('attachment_id', '=', att.id),
        ])
        self.assertTrue(doc)
        self.assertTrue(doc.uploaded_to_carrier)

    def test_action_upload_logs_on_po_chatter(self):
        """Successful upload posts a message on the PO."""
        att = self._make_attachment('MML-QD-001.pdf')
        initial_msg_count = len(self.po.message_ids)
        wizard = self.env['freight.dsv.doc.upload.wizard'].create({
            'po_id': self.po.id,
            'line_ids': [(0, 0, {
                'attachment_id': att.id,
                'dsv_type': 'CUS',
                'include': True,
            })],
        })
        wizard.action_upload()
        self.assertGreater(len(self.po.message_ids), initial_msg_count)

    def test_action_upload_skips_unchecked_lines(self):
        """Lines with include=False are not uploaded."""
        att = self._make_attachment('Skip_Me.pdf')
        wizard = self.env['freight.dsv.doc.upload.wizard'].create({
            'po_id': self.po.id,
            'line_ids': [(0, 0, {
                'attachment_id': att.id,
                'dsv_type': 'GDS',
                'include': False,
            })],
        })
        wizard.action_upload()
        doc = self.env['freight.document'].search([
            ('booking_id', '=', self.booking.id),
            ('attachment_id', '=', att.id),
        ])
        self.assertFalse(doc)
