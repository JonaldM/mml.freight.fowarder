from odoo.tests.common import TransactionCase


class Test3plHandoff(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': '3PL Supplier'})
        cls.po = cls.env['purchase.order'].create({'partner_id': cls.partner.id})
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': '3PL C',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })

    # ------------------------------------------------------------------ #
    # Existing tests                                                       #
    # ------------------------------------------------------------------ #

    def test_no_error_without_connector(self):
        b = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'purchase_order_id': self.po.id,
            'currency_id': self.env.company.currency_id.id,
        })
        b.action_confirm()
        self.assertEqual(b.state, 'confirmed')

    def test_3pl_message_created_when_connector_present(self):
        if '3pl.message' not in self.env:
            self.skipTest('stock_3pl_core not installed')
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        connector = self.env['3pl.connector'].search(
            [('warehouse_id', '=', warehouse.id), ('active', '=', True)], limit=1,
        )
        if not connector:
            self.skipTest('No active 3pl.connector')
        picking_type = self.env['stock.picking.type'].search(
            [('warehouse_id', '=', warehouse.id)], limit=1,
        )
        self.po.write({'picking_type_id': picking_type.id})
        b = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'purchase_order_id': self.po.id,
            'currency_id': self.env.company.currency_id.id,
        })
        b.action_confirm()
        self.assertTrue(b.tpl_message_id)
        self.assertEqual(b.tpl_message_id.document_type, 'inward_order')
        self.assertEqual(b.tpl_message_id.ref_id, self.po.id)

    # ------------------------------------------------------------------ #
    # Routing tests (require stock_3pl_core)                              #
    # ------------------------------------------------------------------ #

    def _make_connector(self, warehouse, priority=10, categories=None, partner='mainfreight'):
        """Helper: create a minimal 3pl.connector."""
        vals = {
            'name': f'Test Connector p={priority}',
            'warehouse_id': warehouse.id,
            'warehouse_partner': partner,
            'transport': 'rest_api',
            'environment': 'test',
            'priority': priority,
        }
        connector = self.env['3pl.connector'].create(vals)
        if categories:
            connector.product_category_ids = [(6, 0, [c.id for c in categories])]
        return connector

    def _isolate_warehouse(self, warehouse):
        """Deactivate all pre-existing connectors on the warehouse so routing tests
        are not affected by connectors created outside this test's transaction."""
        self.env['3pl.connector'].search(
            [('warehouse_id', '=', warehouse.id), ('active', '=', True)],
        ).write({'active': False})

    def test_priority_selects_lower_number(self):
        """When two catch-all connectors exist, the lower priority integer wins."""
        if '3pl.connector' not in self.env:
            self.skipTest('stock_3pl_core not installed')
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        self._isolate_warehouse(warehouse)
        picking_type = self.env['stock.picking.type'].search(
            [('warehouse_id', '=', warehouse.id)], limit=1,
        )
        preferred = self._make_connector(warehouse, priority=5)
        _fallback = self._make_connector(warehouse, priority=20)
        po = self.env['purchase.order'].create({'partner_id': self.partner.id, 'picking_type_id': picking_type.id})
        b = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'purchase_order_id': po.id,
            'currency_id': self.env.company.currency_id.id,
        })
        connector = b._resolve_3pl_connector(warehouse, po)
        self.assertEqual(connector.id, preferred.id, 'Lower priority integer should be selected')

    def test_category_specific_routing(self):
        """Connector with matching category is preferred over catch-all."""
        if '3pl.connector' not in self.env:
            self.skipTest('stock_3pl_core not installed')
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        self._isolate_warehouse(warehouse)
        picking_type = self.env['stock.picking.type'].search(
            [('warehouse_id', '=', warehouse.id)], limit=1,
        )
        chilled_cat = self.env['product.category'].create({'name': 'Chilled'})
        specific = self._make_connector(warehouse, priority=10, categories=[chilled_cat])
        _catchall = self._make_connector(warehouse, priority=5)  # lower priority but no category match

        product = self.env['product.product'].create({
            'name': 'Chilled Product',
            'categ_id': chilled_cat.id,
            'type': 'product',
        })
        po = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'picking_type_id': picking_type.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'product_qty': 10,
                'price_unit': 5.0,
            })],
        })
        b = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'purchase_order_id': po.id,
            'currency_id': self.env.company.currency_id.id,
        })
        connector = b._resolve_3pl_connector(warehouse, po)
        self.assertEqual(connector.id, specific.id, 'Category-specific connector should win over catch-all')

    def test_category_fallback_to_catchall(self):
        """When no specific connector matches the product category, fall back to catch-all."""
        if '3pl.connector' not in self.env:
            self.skipTest('stock_3pl_core not installed')
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        self._isolate_warehouse(warehouse)
        picking_type = self.env['stock.picking.type'].search(
            [('warehouse_id', '=', warehouse.id)], limit=1,
        )
        frozen_cat = self.env['product.category'].create({'name': 'Frozen'})
        other_cat = self.env['product.category'].create({'name': 'OtherCat'})
        _specific_other = self._make_connector(warehouse, priority=10, categories=[other_cat])
        catchall = self._make_connector(warehouse, priority=10)  # no categories = catch-all

        product = self.env['product.product'].create({
            'name': 'Frozen Product',
            'categ_id': frozen_cat.id,
            'type': 'product',
        })
        po = self.env['purchase.order'].create({
            'partner_id': self.partner.id,
            'picking_type_id': picking_type.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'product_qty': 5,
                'price_unit': 8.0,
            })],
        })
        b = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'purchase_order_id': po.id,
            'currency_id': self.env.company.currency_id.id,
        })
        connector = b._resolve_3pl_connector(warehouse, po)
        self.assertEqual(connector.id, catchall.id, 'Should fall back to catch-all when no category match')
