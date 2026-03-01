from odoo.tests.common import TransactionCase

class TestPoFormFields(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'PO Fields S'})

    def _inc(self, code):
        i = self.env['account.incoterms'].search([('code', '=', code)], limit=1)
        if not i:
            i = self.env['account.incoterms'].create({'name': code, 'code': code})
        return i

    def test_responsibility_recomputes(self):
        po = self.env['purchase.order'].create({'partner_id': self.partner.id, 'incoterm_id': self._inc('FOB').id})
        self.assertEqual(po.freight_responsibility, 'buyer')
        po.incoterm_id = self._inc('DDP')
        self.assertEqual(po.freight_responsibility, 'seller')

    def test_cargo_date_writable(self):
        po = self.env['purchase.order'].create({'partner_id': self.partner.id})
        po.cargo_ready_date = '2026-04-01'
        self.assertEqual(str(po.cargo_ready_date), '2026-04-01')

    def test_tender_count_zero(self):
        po = self.env['purchase.order'].create({'partner_id': self.partner.id})
        self.assertEqual(po.tender_count, 0)

    def test_tender_count_increments(self):
        po = self.env['purchase.order'].create({'partner_id': self.partner.id, 'incoterm_id': self._inc('FOB').id})
        self.env['freight.tender'].create({'po_ids': [(4, po.id)], 'company_id': self.env.company.id, 'currency_id': self.env.company.currency_id.id})
        self.assertEqual(po.tender_count, 1)

    def test_action_creates_tender(self):
        po = self.env['purchase.order'].create({'partner_id': self.partner.id, 'incoterm_id': self._inc('EXW').id})
        po.action_request_freight_tender()
        self.assertTrue(po.freight_tender_id)
        self.assertIn(po, po.freight_tender_id.po_ids)
