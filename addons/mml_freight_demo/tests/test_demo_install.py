from odoo.tests.common import TransactionCase


class TestDemoInstall(TransactionCase):
    def test_dsv_carrier(self):
        c = self.env.ref('mml_freight_demo.carrier_dsv_road_nz', raise_if_not_found=False)
        self.assertIsNotNone(c)
        self.assertEqual(c.delivery_type, 'dsv_generic')
        self.assertTrue(c.auto_tender)

    def test_knplus_carrier(self):
        c = self.env.ref('mml_freight_demo.carrier_knplus_sea_lcl', raise_if_not_found=False)
        self.assertIsNotNone(c)
        self.assertEqual(c.delivery_type, 'knplus')

    def test_enduro_partner(self):
        p = self.env.ref('mml_freight_demo.partner_enduro_pet_au', raise_if_not_found=False)
        self.assertIsNotNone(p)
        self.assertGreater(p.supplier_rank, 0)

    def test_products_have_dims(self):
        for xmlid in ('product_dog_food_20kg', 'product_cat_food_5kg', 'product_bird_seed_10kg'):
            d = self.env.ref(f'mml_freight_demo.{xmlid}', raise_if_not_found=False)
            self.assertIsNotNone(d, f'{xmlid} not found')
            self.assertGreater(d.x_freight_length, 0, f'{xmlid} missing x_freight_length')

    def test_demo_po_buyer_responsibility(self):
        po = self.env.ref('mml_freight_demo.po_enduro_ready_to_tender', raise_if_not_found=False)
        self.assertIsNotNone(po)
        self.assertEqual(po.freight_responsibility, 'buyer')

    def test_demo_po_cargo_date(self):
        po = self.env.ref('mml_freight_demo.po_enduro_ready_to_tender', raise_if_not_found=False)
        self.assertTrue(po.cargo_ready_date)
