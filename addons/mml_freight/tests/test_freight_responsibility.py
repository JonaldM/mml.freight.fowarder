from odoo.tests.common import TransactionCase

class TestFreightResponsibility(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Resp Supplier', 'supplier_rank': 1})
        cls.product = cls.env['product.product'].create({'name': 'Test Prod', 'type': 'product'})

    def _make_po(self, code):
        inc = self.env['account.incoterms'].search([('code', '=', code)], limit=1)
        if not inc:
            inc = self.env['account.incoterms'].create({'name': code, 'code': code})
        return self.env['purchase.order'].create({
            'partner_id': self.partner.id, 'incoterm_id': inc.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_qty': 1, 'price_unit': 10, 'name': 'x'})],
        })

    def test_exw_buyer(self): self.assertEqual(self._make_po('EXW').freight_responsibility, 'buyer')
    def test_fca_buyer(self): self.assertEqual(self._make_po('FCA').freight_responsibility, 'buyer')
    def test_fob_buyer(self): self.assertEqual(self._make_po('FOB').freight_responsibility, 'buyer')
    def test_fas_buyer(self): self.assertEqual(self._make_po('FAS').freight_responsibility, 'buyer')
    def test_cfr_seller(self): self.assertEqual(self._make_po('CFR').freight_responsibility, 'seller')
    def test_cif_seller(self): self.assertEqual(self._make_po('CIF').freight_responsibility, 'seller')
    def test_cpt_seller(self): self.assertEqual(self._make_po('CPT').freight_responsibility, 'seller')
    def test_cip_seller(self): self.assertEqual(self._make_po('CIP').freight_responsibility, 'seller')
    def test_dap_seller(self): self.assertEqual(self._make_po('DAP').freight_responsibility, 'seller')
    def test_dpu_seller(self): self.assertEqual(self._make_po('DPU').freight_responsibility, 'seller')
    def test_ddp_seller(self): self.assertEqual(self._make_po('DDP').freight_responsibility, 'seller')
    def test_no_incoterm_na(self):
        po = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_qty': 1, 'price_unit': 10, 'name': 'x'})],
        })
        self.assertEqual(po.freight_responsibility, 'na')
