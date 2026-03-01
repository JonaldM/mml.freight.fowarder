# addons/mml_freight/tests/test_contract_opportunity_cost.py
from odoo.tests.common import TransactionCase
from odoo import fields


class TestContractOpportunityCost(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        prod = cls.env['product.product'].create({
            'name': 'Test Freight OC',
            'type': 'service',
        })
        cls.nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or cls.env.company.currency_id
        cls.carrier_dsv = cls.env['delivery.carrier'].create({
            'name': 'DSV OC',
            'product_id': prod.id,
            'delivery_type': 'fixed',
        })
        cls.carrier_kn = cls.env['delivery.carrier'].create({
            'name': 'KN OC',
            'product_id': prod.id,
            'delivery_type': 'fixed',
        })
        today = fields.Date.today()
        cls.contract = cls.env['freight.carrier.contract'].create({
            'name': 'DSV OC Contract',
            'carrier_id': cls.carrier_dsv.id,
            'date_start': today,
            'date_end': today.replace(year=today.year + 1),
            'commitment_unit': 'teu',
            'committed_quantity': 20.0,
            'contracted_rate': 3000.0,
            'contracted_rate_currency_id': cls.nzd.id,
        })
        partner = cls.env['res.partner'].create({'name': 'OC Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': partner.id})
        cls.tender = cls.env['freight.tender'].create({
            'po_ids': [(4, po.id)],
            'company_id': cls.env.company.id,
            'currency_id': cls.nzd.id,
            'state': 'quoted',
            'freight_mode_preference': 'sea',
        })

    def _make_quote(self, carrier, base_rate, transit_days=14, mode='sea_fcl'):
        return self.env['freight.tender.quote'].create({
            'tender_id': self.tender.id,
            'carrier_id': carrier.id,
            'state': 'received',
            'currency_id': self.nzd.id,
            'transport_mode': mode,
            'base_rate': base_rate,
            'estimated_transit_days': transit_days,
        })

    def test_is_contract_carrier_true_for_dsv(self):
        q = self._make_quote(self.carrier_dsv, base_rate=2800.0)
        self.assertTrue(q.is_contract_carrier)

    def test_is_contract_carrier_false_for_kn(self):
        q = self._make_quote(self.carrier_kn, base_rate=2600.0)
        self.assertFalse(q.is_contract_carrier)

    def test_contract_id_resolved(self):
        q = self._make_quote(self.carrier_dsv, base_rate=2800.0)
        self.assertEqual(q.contract_id, self.contract)

    def test_contracted_rate_total_nzd(self):
        # Contracted rate $3000/TEU, tender has 0 packages -> clamped to 1 TEU -> $3000 total
        q = self._make_quote(self.carrier_dsv, base_rate=2800.0)
        self.assertAlmostEqual(q.contracted_rate_total_nzd, 3000.0, places=0)

    def test_opportunity_cost_positive_when_contract_above_market(self):
        # Contract $3000, market quote $2800 -> OC = +$200
        q = self._make_quote(self.carrier_dsv, base_rate=2800.0)
        self.assertGreater(q.opportunity_cost_nzd, 0)

    def test_opportunity_cost_negative_when_contract_below_market(self):
        # Contract $3000, market quote $3500 -> OC = -$500
        q = self._make_quote(self.carrier_dsv, base_rate=3500.0)
        self.assertLess(q.opportunity_cost_nzd, 0)

    def test_opportunity_cost_zero_for_non_contract_carrier(self):
        q = self._make_quote(self.carrier_kn, base_rate=2600.0)
        self.assertAlmostEqual(q.opportunity_cost_nzd, 0.0)
