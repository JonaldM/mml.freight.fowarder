from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError

class TestAutoSelect(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        p = cls.env['res.partner'].create({'name': 'AS Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': p.id})
        nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or cls.env.company.currency_id
        cls.tender = cls.env['freight.tender'].create({'po_ids': [(4, po.id)], 'company_id': cls.env.company.id, 'currency_id': nzd.id, 'state': 'quoted'})
        prod = cls.env['product.product'].search([], limit=1)
        c1 = cls.env['delivery.carrier'].create({'name': 'Slow', 'product_id': prod.id, 'delivery_type': 'fixed', 'reliability_score': 80.0})
        c2 = cls.env['delivery.carrier'].create({'name': 'Fast', 'product_id': prod.id, 'delivery_type': 'fixed', 'reliability_score': 90.0})
        cls.q_cheap = cls.env['freight.tender.quote'].create({'tender_id': cls.tender.id, 'carrier_id': c1.id, 'state': 'received', 'currency_id': nzd.id, 'base_rate': 1000.0, 'estimated_transit_days': 14})
        cls.q_fast = cls.env['freight.tender.quote'].create({'tender_id': cls.tender.id, 'carrier_id': c2.id, 'state': 'received', 'currency_id': nzd.id, 'base_rate': 3000.0, 'estimated_transit_days': 3})

    def test_cheapest(self):
        self.tender.selection_mode = 'cheapest'; self.tender.action_auto_select()
        self.assertEqual(self.tender.selected_quote_id, self.q_cheap); self.assertEqual(self.tender.state, 'selected')

    def test_fastest(self):
        self.tender.write({'state': 'quoted', 'selected_quote_id': False, 'selection_mode': 'fastest'})
        self.tender.action_auto_select()
        self.assertEqual(self.tender.selected_quote_id, self.q_fast)

    def test_manual_raises(self):
        self.tender.write({'state': 'quoted', 'selected_quote_id': False, 'selection_mode': 'manual'})
        with self.assertRaises(UserError): self.tender.action_auto_select()

    def test_reason_set(self):
        self.tender.write({'state': 'quoted', 'selected_quote_id': False, 'selection_mode': 'cheapest'})
        self.tender.action_auto_select()
        self.assertIn('cheapest', self.tender.selection_reason.lower())
