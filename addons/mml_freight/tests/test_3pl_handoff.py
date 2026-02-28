from odoo.tests.common import TransactionCase

class Test3plHandoff(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': '3PL Supplier'})
        cls.po = cls.env['purchase.order'].create({'partner_id': cls.partner.id})
        cls.carrier = cls.env['delivery.carrier'].create({'name': '3PL C', 'product_id': cls.env['product.product'].search([], limit=1).id, 'delivery_type': 'fixed'})

    def test_no_error_without_connector(self):
        b = self.env['freight.booking'].create({'carrier_id': self.carrier.id, 'purchase_order_id': self.po.id, 'currency_id': self.env.company.currency_id.id})
        b.action_confirm()
        self.assertEqual(b.state, 'confirmed')

    def test_3pl_message_created_when_connector_present(self):
        if '3pl.message' not in self.env:
            self.skipTest('stock_3pl_core not installed')
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        connector = self.env['3pl.connector'].search([('warehouse_id', '=', warehouse.id), ('active', '=', True)], limit=1)
        if not connector:
            self.skipTest('No active 3pl.connector')
        picking_type = self.env['stock.picking.type'].search([('warehouse_id', '=', warehouse.id)], limit=1)
        self.po.write({'picking_type_id': picking_type.id})
        b = self.env['freight.booking'].create({'carrier_id': self.carrier.id, 'purchase_order_id': self.po.id, 'currency_id': self.env.company.currency_id.id})
        b.action_confirm()
        self.assertTrue(b.tpl_message_id)
        self.assertEqual(b.tpl_message_id.document_type, 'inward_order')
        self.assertEqual(b.tpl_message_id.ref_id, self.po.id)
