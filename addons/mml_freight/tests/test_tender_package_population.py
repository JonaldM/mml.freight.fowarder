from odoo.tests.common import TransactionCase


class TestTenderPackagePopulation(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supplier = cls.env['res.partner'].create({'name': 'Pop Supplier'})
        cls.product = cls.env['product.product'].create({
            'name': 'Widget',
            'type': 'product',
            'x_freight_length': 30.0,
            'x_freight_width': 20.0,
            'x_freight_height': 10.0,
            'x_freight_weight': 2.5,
            'x_dangerous_goods': False,
        })

    def _make_po(self, qty=5):
        po = self.env['purchase.order'].create({'partner_id': self.supplier.id})
        self.env['purchase.order.line'].create({
            'order_id': po.id,
            'product_id': self.product.id,
            'product_qty': qty,
            'price_unit': 10.0,
        })
        return po

    def test_package_lines_created_from_po_lines(self):
        po = self._make_po(qty=5)
        po.action_request_freight_tender()
        tender = po.freight_tender_id
        self.assertEqual(len(tender.package_line_ids), 1)
        line = tender.package_line_ids[0]
        self.assertEqual(line.description, self.product.name)
        self.assertEqual(line.quantity, 5)
        self.assertAlmostEqual(line.weight_kg, 2.5)
        self.assertAlmostEqual(line.length_cm, 30.0)
        self.assertAlmostEqual(line.width_cm, 20.0)
        self.assertAlmostEqual(line.height_cm, 10.0)
        self.assertFalse(line.is_dangerous)

    def test_missing_dims_posts_chatter_warning(self):
        product_no_dims = self.env['product.product'].create({'name': 'NoDims'})
        po = self.env['purchase.order'].create({'partner_id': self.supplier.id})
        self.env['purchase.order.line'].create({
            'order_id': po.id,
            'product_id': product_no_dims.id,
            'product_qty': 3,
            'price_unit': 5.0,
        })
        po.action_request_freight_tender()
        tender = po.freight_tender_id
        msgs = tender.message_ids.filtered(
            lambda m: 'missing freight dimensions' in (m.body or '')
        )
        self.assertTrue(msgs, 'Expected missing-dims chatter warning')

    def test_volume_computed_from_dims(self):
        po = self._make_po(qty=2)
        po.action_request_freight_tender()
        line = po.freight_tender_id.package_line_ids[0]
        expected = 30.0 * 20.0 * 10.0 / 1_000_000.0 * 2
        self.assertAlmostEqual(line.volume_m3, expected, places=6)

    def test_dangerous_goods_flag_copied(self):
        dg_product = self.env['product.product'].create({
            'name': 'DGProd', 'x_dangerous_goods': True,
            'x_freight_weight': 1.0, 'x_freight_length': 10.0,
            'x_freight_width': 10.0, 'x_freight_height': 10.0,
        })
        po = self.env['purchase.order'].create({'partner_id': self.supplier.id})
        self.env['purchase.order.line'].create({
            'order_id': po.id, 'product_id': dg_product.id,
            'product_qty': 1, 'price_unit': 5.0,
        })
        po.action_request_freight_tender()
        line = po.freight_tender_id.package_line_ids[0]
        self.assertTrue(line.is_dangerous)

    def test_fractional_qty_is_rounded_not_truncated(self):
        """product_qty=2.7 should produce quantity=3 (rounded), not 2 (truncated)."""
        po = self.env['purchase.order'].create({'partner_id': self.supplier.id})
        self.env['purchase.order.line'].create({
            'order_id': po.id,
            'product_id': self.product.id,
            'product_qty': 2.7,
            'price_unit': 10.0,
        })
        po.action_request_freight_tender()
        line = po.freight_tender_id.package_line_ids[0]
        self.assertEqual(line.quantity, 3)
