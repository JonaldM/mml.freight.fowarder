from odoo.tests.common import TransactionCase

class TestQuoteRanking(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        p = cls.env['res.partner'].create({'name': 'Rank S'})
        po = cls.env['purchase.order'].create({'partner_id': p.id})
        nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or cls.env.company.currency_id
        cls.tender = cls.env['freight.tender'].create({'po_ids': [(4, po.id)], 'company_id': cls.env.company.id, 'currency_id': nzd.id})
        prod = cls.env['product.product'].search([], limit=1)
        c1 = cls.env['delivery.carrier'].create({'name': 'C1', 'product_id': prod.id, 'delivery_type': 'fixed'})
        c2 = cls.env['delivery.carrier'].create({'name': 'C2', 'product_id': prod.id, 'delivery_type': 'fixed'})
        cls.q_cheap = cls.env['freight.tender.quote'].create({'tender_id': cls.tender.id, 'carrier_id': c1.id, 'state': 'received', 'currency_id': nzd.id, 'base_rate': 1000.0, 'estimated_transit_days': 7})
        cls.q_fast = cls.env['freight.tender.quote'].create({'tender_id': cls.tender.id, 'carrier_id': c2.id, 'state': 'received', 'currency_id': nzd.id, 'base_rate': 2000.0, 'estimated_transit_days': 3})

    def test_cheapest_flag(self): self.assertTrue(self.q_cheap.is_cheapest); self.assertFalse(self.q_fast.is_cheapest)
    def test_fastest_flag(self): self.assertTrue(self.q_fast.is_fastest); self.assertFalse(self.q_cheap.is_fastest)
    def test_rank_by_cost(self): self.assertEqual(self.q_cheap.rank_by_cost, 1); self.assertEqual(self.q_fast.rank_by_cost, 2)
    def test_cost_vs_cheapest(self): self.assertAlmostEqual(self.q_fast.cost_vs_cheapest_pct, 100.0, places=1)
    def test_total_rate_sum(self):
        nzd = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or self.env.company.currency_id
        prod = self.env['product.product'].search([], limit=1)
        c = self.env['delivery.carrier'].create({'name': 'CR', 'product_id': prod.id, 'delivery_type': 'fixed'})
        q = self.env['freight.tender.quote'].create({'tender_id': self.tender.id, 'carrier_id': c.id, 'state': 'received', 'currency_id': nzd.id, 'base_rate': 500.0, 'fuel_surcharge': 50.0, 'origin_charges': 100.0, 'destination_charges': 75.0, 'customs_charges': 25.0, 'other_surcharges': 10.0})
        self.assertAlmostEqual(q.total_rate, 760.0)
