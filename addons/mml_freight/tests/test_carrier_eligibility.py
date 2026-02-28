from odoo.tests.common import TransactionCase

class TestCarrierEligibility(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.nz = cls.env['res.country'].search([('code', '=', 'NZ')], limit=1)
        cls.au = cls.env['res.country'].search([('code', '=', 'AU')], limit=1)
        cls.prod = cls.env['product.product'].search([], limit=1)
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Elig Test', 'product_id': cls.prod.id, 'delivery_type': 'fixed',
            'auto_tender': True, 'transport_modes': 'road', 'max_weight_kg': 500.0,
            'supports_dg': False,
            'origin_country_ids': [(6, 0, [cls.au.id])],
            'dest_country_ids': [(6, 0, [cls.nz.id])],
        })

    def test_all_match(self): self.assertTrue(self.carrier.is_eligible(self.au, self.nz, 100, False, 'road'))
    def test_dg_excluded(self): self.assertFalse(self.carrier.is_eligible(self.au, self.nz, 100, True, 'road'))
    def test_overweight(self): self.assertFalse(self.carrier.is_eligible(self.au, self.nz, 600, False, 'road'))
    def test_wrong_origin(self):
        cn = self.env['res.country'].search([('code', '=', 'CN')], limit=1)
        self.assertFalse(self.carrier.is_eligible(cn, self.nz, 100, False, 'road'))
    def test_wrong_mode(self): self.assertFalse(self.carrier.is_eligible(self.au, self.nz, 100, False, 'air'))
    def test_any_mode_carrier(self):
        c = self.env['delivery.carrier'].create({'name': 'Any', 'product_id': self.prod.id, 'delivery_type': 'fixed', 'transport_modes': 'any'})
        self.assertTrue(c.is_eligible(self.au, self.nz, 100, False, 'air'))
