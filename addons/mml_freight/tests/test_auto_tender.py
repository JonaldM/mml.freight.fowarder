from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase


class TestAutoTender(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supplier = cls.env['res.partner'].create({'name': 'Auto Tender Supplier'})
        incoterm_exw = cls.env['account.incoterms'].search([('code', '=', 'EXW')], limit=1)
        if not incoterm_exw:
            incoterm_exw = cls.env['account.incoterms'].create({'code': 'EXW', 'name': 'EXW'})
        cls.incoterm_exw = incoterm_exw

        incoterm_cif = cls.env['account.incoterms'].search([('code', '=', 'CIF')], limit=1)
        if not incoterm_cif:
            incoterm_cif = cls.env['account.incoterms'].create({'code': 'CIF', 'name': 'CIF'})
        cls.incoterm_cif = incoterm_cif

        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Auto Tender Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'auto_tender': True,
            'delivery_type': 'fixed',
        })

    def _make_po(self, incoterm=None):
        return self.env['purchase.order'].create({
            'partner_id': self.supplier.id,
            'incoterm_id': (incoterm or self.incoterm_exw).id,
        })

    def test_confirm_buyer_incoterm_creates_tender(self):
        """PO confirmation with buyer incoterm auto-creates a freight tender."""
        po = self._make_po(self.incoterm_exw)
        mock_adapter = MagicMock()
        mock_adapter.request_quote.return_value = []
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=mock_adapter,
        ):
            po.button_confirm()
        self.assertTrue(po.freight_tender_id, 'Freight tender should be auto-created on PO confirm')

    def test_confirm_seller_incoterm_no_tender(self):
        """PO with seller incoterm (CIF) should NOT create a freight tender on confirm."""
        po = self._make_po(self.incoterm_cif)
        po.button_confirm()
        self.assertFalse(po.freight_tender_id, 'Seller incoterm should not trigger auto-tender')

    def test_confirm_existing_tender_not_duplicated(self):
        """If a tender already exists, button_confirm must not create a second one."""
        po = self._make_po(self.incoterm_exw)
        existing = self.env['freight.tender'].create({
            'purchase_order_id': po.id,
            'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
        })
        po.freight_tender_id = existing
        mock_adapter = MagicMock()
        mock_adapter.request_quote.return_value = []
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=mock_adapter,
        ):
            po.button_confirm()
        tenders = self.env['freight.tender'].search([('purchase_order_id', '=', po.id)])
        self.assertEqual(len(tenders), 1, 'Must not create duplicate tender')

    def test_confirm_still_succeeds_when_quote_request_errors(self):
        """PO confirm must succeed even if action_request_quotes raises."""
        po = self._make_po(self.incoterm_exw)
        mock_adapter = MagicMock()
        mock_adapter.request_quote.side_effect = Exception('DSV down')
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=mock_adapter,
        ):
            po.button_confirm()   # must not raise
        self.assertEqual(po.state, 'purchase', 'PO must be confirmed despite quote failure')

    def test_confirm_no_incoterm_no_tender(self):
        """PO with no incoterm → freight_responsibility=na → no tender."""
        po = self.env['purchase.order'].create({'partner_id': self.supplier.id})
        po.button_confirm()
        self.assertFalse(po.freight_tender_id)
