from odoo.tests.common import TransactionCase
from odoo import fields


class TestCarrierContract(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        prod = cls.env['product.product'].create({
            'name': 'Test Freight Service',
            'type': 'service',
        })
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Test',
            'product_id': prod.id,
            'delivery_type': 'fixed',
        })
        cls.nzd = cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1) or cls.env.company.currency_id

    def _make_contract(self, **kwargs):
        today = fields.Date.today()
        defaults = {
            'name': 'DSV 2026 FCL',
            'carrier_id': self.carrier.id,
            'date_start': today,
            'date_end': today.replace(year=today.year + 1),
            'commitment_unit': 'teu',
            'committed_quantity': 20.0,
            'contracted_rate': 2500.0,
            'contracted_rate_currency_id': self.nzd.id,
        }
        defaults.update(kwargs)
        return self.env['freight.carrier.contract'].create(defaults)

    def test_create_contract(self):
        c = self._make_contract()
        self.assertEqual(c.name, 'DSV 2026 FCL')
        self.assertEqual(c.committed_quantity, 20.0)
        self.assertEqual(c.commitment_unit, 'teu')

    def test_is_active_true(self):
        c = self._make_contract()
        self.assertTrue(c.is_active)

    def test_is_active_false_future(self):
        today = fields.Date.today()
        c = self._make_contract(
            date_start=today.replace(year=today.year + 1),
            date_end=today.replace(year=today.year + 2),
        )
        self.assertFalse(c.is_active)

    def test_is_active_false_expired(self):
        today = fields.Date.today()
        c = self._make_contract(
            date_start=today.replace(year=today.year - 2),
            date_end=today.replace(year=today.year - 1),
        )
        self.assertFalse(c.is_active)
