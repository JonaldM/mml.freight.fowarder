from odoo.tests.common import TransactionCase

class TestPackageAggregation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Agg Supplier'})
        inc = cls.env['account.incoterms'].search([('code', '=', 'FOB')], limit=1)
        if not inc:
            inc = cls.env['account.incoterms'].create({'name': 'FOB', 'code': 'FOB'})
        cls.po = cls.env['purchase.order'].create({'partner_id': cls.partner.id, 'incoterm_id': inc.id})

    def _tender(self):
        return self.env['freight.tender'].create({
            'purchase_order_id': self.po.id, 'company_id': self.env.company.id,
            'currency_id': self.env.company.currency_id.id,
        })

    def _pkg(self, t, qty, w, l, wi, h, dg=False):
        return self.env['freight.tender.package'].create({
            'tender_id': t.id, 'quantity': qty, 'weight_kg': w,
            'length_cm': l, 'width_cm': wi, 'height_cm': h, 'is_dangerous': dg,
        })

    def test_weight_sum(self):
        t = self._tender()
        self._pkg(t, 1, 20.0, 40, 30, 25)
        self._pkg(t, 2, 10.0, 30, 20, 20)
        self.assertAlmostEqual(t.total_weight_kg, 30.0)

    def test_volume_per_line(self):
        t = self._tender()
        pkg = self._pkg(t, 2, 5.0, 100, 50, 50)
        self.assertAlmostEqual(pkg.volume_m3, 0.5, places=4)

    def test_chargeable_uses_volumetric(self):
        t = self._tender()
        self._pkg(t, 1, 10.0, 100, 100, 100)
        self.assertAlmostEqual(t.chargeable_weight_kg, 333.0, places=1)

    def test_dg_flag_propagates(self):
        t = self._tender()
        self._pkg(t, 1, 2.0, 10, 10, 10, dg=True)
        self.assertTrue(t.contains_dg)

    def test_weight_field_on_product(self):
        product = self.env['product.template'].create({
            'name': 'WeightTest',
            'x_freight_weight': 5.5,
        })
        product.invalidate_recordset()
        self.assertAlmostEqual(product.x_freight_weight, 5.5)

    def test_onchange_sets_weight_from_product(self):
        product = self.env['product.product'].create({
            'name': 'OnchangeWeight',
            'x_freight_weight': 2.0,
        })
        line = self.env['freight.tender.package'].new({'product_id': product.id})
        line._onchange_product_id()
        self.assertAlmostEqual(line.weight_kg, 2.0)
