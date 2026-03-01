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

    def _make_po_with_line(self, incoterm=None):
        """Create a PO with one order line so auto-tender is not skipped."""
        product = self.env['product.product'].search(
            [('purchase_ok', '=', True)], limit=1,
        ) or self.env['product.product'].search([], limit=1)
        po = self._make_po(incoterm)
        self.env['purchase.order.line'].create({
            'order_id': po.id,
            'product_id': product.id,
            'product_qty': 1,
            'price_unit': 10.0,
            'name': product.name,
        })
        return po

    def test_confirm_buyer_incoterm_creates_tender(self):
        """PO confirmation with buyer incoterm auto-creates a freight tender and requests quotes."""
        # I1: patch action_request_quotes directly on the tender model so the assertion is
        # honest — the carrier delivery_type='fixed' would not reach the adapter otherwise.
        po = self._make_po_with_line(self.incoterm_exw)
        mock_request_quotes = MagicMock()
        with patch.object(
            type(self.env['freight.tender']), 'action_request_quotes', mock_request_quotes,
        ):
            po.button_confirm()
        self.assertTrue(po.freight_tender_id, 'Freight tender should be auto-created on PO confirm')
        mock_request_quotes.assert_called_once()

    def test_confirm_seller_incoterm_no_tender(self):
        """PO with seller incoterm (CIF) should NOT create a freight tender on confirm."""
        po = self._make_po_with_line(self.incoterm_cif)
        po.button_confirm()
        self.assertFalse(po.freight_tender_id, 'Seller incoterm should not trigger auto-tender')

    def test_confirm_existing_tender_not_duplicated(self):
        """If a tender already exists, button_confirm must not create a second one."""
        po = self._make_po_with_line(self.incoterm_exw)
        existing = self.env['freight.tender'].create({
            'purchase_order_id': po.id,
            'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
        })
        po.freight_tender_id = existing
        # I1: patch action_request_quotes so the duplicate-guard is the only thing under test
        mock_request_quotes = MagicMock()
        with patch.object(
            type(self.env['freight.tender']), 'action_request_quotes', mock_request_quotes,
        ):
            po.button_confirm()
        tenders = self.env['freight.tender'].search([('purchase_order_id', '=', po.id)])
        self.assertEqual(len(tenders), 1, 'Must not create duplicate tender')
        mock_request_quotes.assert_not_called()

    def test_confirm_still_succeeds_when_quote_request_errors(self):
        """PO confirm must succeed even if action_request_quotes raises; chatter note posted."""
        # I2: patch action_request_quotes to raise directly — patching the low-level adapter
        # never reached the outer handler because action_request_quotes has its own per-carrier
        # try/except that swallows the exception first.
        po = self._make_po_with_line(self.incoterm_exw)
        with patch.object(
            type(self.env['freight.tender']),
            'action_request_quotes',
            side_effect=Exception('Quote fanout failed'),
        ):
            po.button_confirm()   # must not raise
        self.assertEqual(po.state, 'purchase', 'PO must be confirmed despite quote failure')
        chatter_bodies = po.message_ids.mapped('body')
        self.assertTrue(
            any('tender' in (b or '').lower() for b in chatter_bodies),
            'A chatter note must be posted when quote fanout fails',
        )

    def test_confirm_no_incoterm_no_tender(self):
        """PO with no incoterm → freight_responsibility=na → no tender."""
        po = self.env['purchase.order'].create({'partner_id': self.supplier.id})
        po.button_confirm()
        self.assertFalse(po.freight_tender_id)

    def test_confirm_no_order_lines_no_tender(self):
        """PO with buyer incoterm but no order lines → auto-tender skipped."""
        # I3: _auto_create_freight_tender must return early when order_line is empty
        po = self._make_po(self.incoterm_exw)  # no lines added
        po.button_confirm()
        self.assertFalse(
            po.freight_tender_id,
            'Auto-tender must be skipped when PO has no order lines',
        )
