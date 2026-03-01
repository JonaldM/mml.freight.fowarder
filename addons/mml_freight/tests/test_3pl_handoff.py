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
            'po_ids': [(4, self.po.id)],
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
            'po_ids': [(4, self.po.id)],
            'currency_id': self.env.company.currency_id.id,
        })
        b.action_confirm()
        msg = self.env['3pl.message'].search([
            ('ref_model', '=', 'purchase.order'),
            ('ref_id', '=', self.po.id),
            ('document_type', '=', 'inward_order'),
            ('action', '=', 'create'),
        ], limit=1)
        self.assertTrue(msg, 'Expected a 3pl.message inward_order for the PO')
        self.assertEqual(msg.ref_id, self.po.id)

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
            'po_ids': [(4, po.id)],
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
            'po_ids': [(4, po.id)],
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
            'po_ids': [(4, po.id)],
            'currency_id': self.env.company.currency_id.id,
        })
        connector = b._resolve_3pl_connector(warehouse, po)
        self.assertEqual(connector.id, catchall.id, 'Should fall back to catch-all when no category match')

    def test_action_confirm_builds_inward_order_payload(self):
        """action_confirm() must call _build_inward_order_payload() after queueing 3PL messages.

        Bug: action_confirm() calls _queue_3pl_inward_order() (creates draft 3pl.messages)
        but never calls _build_inward_order_payload() (advances them to queued with XML).
        Messages stay stuck in draft — Mainfreight never receives the inward order.
        """
        from unittest.mock import patch
        b = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'po_ids': [(4, self.po.id)],
            'currency_id': self.env.company.currency_id.id,
        })
        with patch.object(type(b), '_build_inward_order_payload') as mock_build:
            b.action_confirm()
        # assert_called_once() (not _with) — class-level patch receives `self` as first arg
        mock_build.assert_called_once()

    def test_build_inward_order_payload_populates_message(self):
        """_build_inward_order_payload() writes XML to tpl_message_id and advances state to queued."""
        if '3pl.message' not in self.env:
            self.skipTest('stock_3pl_core not installed')
        from unittest.mock import patch, MagicMock
        import sys
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        self._isolate_warehouse(warehouse)
        picking_type = self.env['stock.picking.type'].search(
            [('warehouse_id', '=', warehouse.id)], limit=1
        )
        connector = self._make_connector(warehouse)
        partner = self.env['res.partner'].create({'name': 'PL Sup'})
        po = self.env['purchase.order'].create({
            'partner_id': partner.id, 'picking_type_id': picking_type.id,
        })
        booking = self.env['freight.booking'].create({
            'carrier_id':          self.carrier.id,
            'currency_id':         self.env.company.currency_id.id,
            'carrier_booking_id':  'BK_PAYLOAD_001',
            'po_ids':              [(4, po.id)],
            'vessel_name':         'MSC Oscar',
            'voyage_number':       'VOY1',
            'container_number':    'CONT1',
            'state':               'draft',
        })
        mock_adapter = MagicMock()
        mock_adapter.confirm_booking.return_value = {
            'carrier_shipment_id': 'SH_PL_001', 'vessel_name': 'MSC Oscar',
            'voyage_number': 'VOY1', 'container_number': 'CONT1',
            'bill_of_lading': '', 'feeder_vessel_name': '', 'feeder_voyage_number': '', 'eta': '',
        }

        # Stub the InwardOrderDocument so this test does not require stock_3pl_mainfreight installed
        mock_doc_cls = MagicMock()
        mock_doc_cls.return_value.build_outbound.return_value = (
            '<?xml version=\'1.0\' encoding=\'UTF-8\'?>\n<InwardOrder action="CREATE"/>'
        )
        mock_module = MagicMock()
        mock_module.InwardOrderDocument = mock_doc_cls

        with patch.dict(sys.modules, {
                'odoo.addons.stock_3pl_mainfreight': MagicMock(),
                'odoo.addons.stock_3pl_mainfreight.document': MagicMock(),
                'odoo.addons.stock_3pl_mainfreight.document.inward_order': mock_module,
        }):
            with patch(
                'odoo.addons.mml_freight.models.freight_booking.FreightAdapterRegistry.get_adapter',
                return_value=mock_adapter,
            ):
                booking.action_confirm_with_dsv()

        msg = self.env['3pl.message'].search([
            ('ref_model', '=', 'purchase.order'),
            ('ref_id', '=', po.id),
            ('document_type', '=', 'inward_order'),
            ('action', '=', 'create'),
        ], limit=1)
        self.assertTrue(msg, '3pl.message should be created for the PO')
        self.assertEqual(msg.state, 'queued', 'Message should be queued after payload built')
        self.assertTrue(msg.payload_xml, 'payload_xml should be populated')
        self.assertIn('<InwardOrder', msg.payload_xml)
