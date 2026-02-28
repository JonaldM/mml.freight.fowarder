from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError

class TestTenderLifecycle(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        p = cls.env['res.partner'].create({'name': 'LC Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': p.id})
        nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or cls.env.company.currency_id
        cls.tender = cls.env['freight.tender'].create({'purchase_order_id': po.id, 'company_id': cls.env.company.id, 'currency_id': nzd.id})

    def test_initial_state(self): self.assertEqual(self.tender.state, 'draft')
    def test_sequence_assigned(self): self.assertTrue(self.tender.name.startswith('FT/'))
    def test_cancel(self):
        t = self.env['freight.tender'].create({'purchase_order_id': self.tender.purchase_order_id.id, 'company_id': self.env.company.id, 'currency_id': self.env.company.currency_id.id})
        t.action_cancel()
        self.assertEqual(t.state, 'cancelled')
    def test_book_without_quote_raises(self):
        with self.assertRaises(UserError): self.tender.action_book()
    def test_auto_select_no_quotes_raises(self):
        t2 = self.env['freight.tender'].create({'purchase_order_id': self.tender.purchase_order_id.id, 'company_id': self.env.company.id, 'currency_id': self.env.company.currency_id.id, 'state': 'quoted', 'selection_mode': 'cheapest'})
        with self.assertRaises(UserError): t2.action_auto_select()
