"""Tests for consolidated purchase order support on freight.tender and freight.booking.

These define the target behaviour after migrating from single purchase_order_id
to many2many po_ids on both models.  All tests in this file are written BEFORE
the implementation — run them to confirm RED before touching model code.
"""
from unittest.mock import patch, MagicMock

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestConsolidatedPOs(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_a = cls.env['res.partner'].create({'name': 'Supplier A'})
        cls.partner_b = cls.env['res.partner'].create({'name': 'Supplier B'})
        cls.po_a = cls.env['purchase.order'].create({'partner_id': cls.partner_a.id})
        cls.po_b = cls.env['purchase.order'].create({'partner_id': cls.partner_b.id})
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Consol Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })
        cls.currency = (
            cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1)
            or cls.env.company.currency_id
        )

    # ------------------------------------------------------------------
    # freight.tender — po_ids (Many2many)
    # ------------------------------------------------------------------

    def test_tender_accepts_multiple_purchase_orders(self):
        """freight.tender can be created with multiple POs via po_ids."""
        tender = self.env['freight.tender'].create({
            'po_ids': [(4, self.po_a.id), (4, self.po_b.id)],
            'company_id': self.env.company.id,
            'currency_id': self.currency.id,
        })
        self.assertEqual(len(tender.po_ids), 2)
        self.assertIn(self.po_a, tender.po_ids)
        self.assertIn(self.po_b, tender.po_ids)

    def test_tender_can_be_created_without_any_po(self):
        """Manual tenders can be created without any linked PO (ROQ adds them later)."""
        tender = self.env['freight.tender'].create({
            'company_id': self.env.company.id,
            'currency_id': self.currency.id,
        })
        self.assertFalse(tender.po_ids)

    def test_supplier_count_computed_from_po_ids(self):
        """supplier_count equals the number of linked POs."""
        tender = self.env['freight.tender'].create({
            'po_ids': [(4, self.po_a.id), (4, self.po_b.id)],
            'company_id': self.env.company.id,
            'currency_id': self.currency.id,
        })
        self.assertEqual(tender.supplier_count, 2)

    def test_supplier_count_zero_with_no_pos(self):
        """supplier_count is 0 when no POs are linked."""
        tender = self.env['freight.tender'].create({
            'company_id': self.env.company.id,
            'currency_id': self.currency.id,
        })
        self.assertEqual(tender.supplier_count, 0)

    def test_is_consolidated_true_with_multiple_pos(self):
        """is_consolidated is True when more than one PO is linked."""
        tender = self.env['freight.tender'].create({
            'po_ids': [(4, self.po_a.id), (4, self.po_b.id)],
            'company_id': self.env.company.id,
            'currency_id': self.currency.id,
        })
        self.assertTrue(tender.is_consolidated)

    def test_is_consolidated_false_with_single_po(self):
        """is_consolidated is False when only one PO is linked."""
        tender = self.env['freight.tender'].create({
            'po_ids': [(4, self.po_a.id)],
            'company_id': self.env.company.id,
            'currency_id': self.currency.id,
        })
        self.assertFalse(tender.is_consolidated)

    def test_is_consolidated_false_with_no_pos(self):
        """is_consolidated is False when no POs are linked."""
        tender = self.env['freight.tender'].create({
            'company_id': self.env.company.id,
            'currency_id': self.currency.id,
        })
        self.assertFalse(tender.is_consolidated)

    def test_shipment_group_ref_stored(self):
        """shipment_group_ref is a char field that stores the ROQ shipment group reference."""
        tender = self.env['freight.tender'].create({
            'po_ids': [(4, self.po_a.id)],
            'shipment_group_ref': 'SG-2026-0042',
            'company_id': self.env.company.id,
            'currency_id': self.currency.id,
        })
        self.assertEqual(tender.shipment_group_ref, 'SG-2026-0042')

    # ------------------------------------------------------------------
    # freight.booking — po_ids (Many2many)
    # ------------------------------------------------------------------

    def test_booking_accepts_multiple_purchase_orders(self):
        """freight.booking can be created with multiple POs via po_ids."""
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'po_ids': [(4, self.po_a.id), (4, self.po_b.id)],
            'currency_id': self.currency.id,
        })
        self.assertEqual(len(booking.po_ids), 2)
        self.assertIn(self.po_a, booking.po_ids)
        self.assertIn(self.po_b, booking.po_ids)

    def test_booking_can_be_created_without_any_po(self):
        """freight.booking can be created without any linked PO."""
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': self.currency.id,
        })
        self.assertFalse(booking.po_ids)

    def test_action_book_propagates_all_po_ids_to_booking(self):
        """action_book on a tender with 2 POs creates a booking with both POs in po_ids."""
        tender = self.env['freight.tender'].create({
            'po_ids': [(4, self.po_a.id), (4, self.po_b.id)],
            'company_id': self.env.company.id,
            'currency_id': self.currency.id,
        })
        quote = self.env['freight.tender.quote'].create({
            'tender_id': tender.id,
            'carrier_id': self.carrier.id,
            'state': 'received',
            'currency_id': self.currency.id,
            'transport_mode': 'sea_fcl',
        })
        tender.write({'state': 'selected', 'selected_quote_id': quote.id})

        mock_adapter = MagicMock()
        mock_adapter.create_booking.return_value = {
            'carrier_booking_id': 'BK-CONSOL-001',
            'carrier_shipment_id': 'SH-CONSOL-001',
            'carrier_tracking_url': '',
            'requires_manual_confirmation': True,
        }
        with patch.object(
            type(self.env['freight.adapter.registry']), 'get_adapter',
            return_value=mock_adapter,
        ):
            tender.action_book()

        self.assertTrue(tender.booking_id)
        self.assertEqual(len(tender.booking_id.po_ids), 2)
        self.assertIn(self.po_a, tender.booking_id.po_ids)
        self.assertIn(self.po_b, tender.booking_id.po_ids)

    # ------------------------------------------------------------------
    # 3PL handoff — one inward_order message per PO
    # ------------------------------------------------------------------

    def _make_connector(self, warehouse, priority=10):
        """Helper: create a minimal active 3pl.connector for the given warehouse."""
        return self.env['3pl.connector'].create({
            'name': f'Consol Connector p={priority}',
            'warehouse_id': warehouse.id,
            'warehouse_partner': 'mainfreight',
            'transport': 'rest_api',
            'environment': 'test',
            'priority': priority,
        })

    def _isolate_warehouse(self, warehouse):
        self.env['3pl.connector'].search(
            [('warehouse_id', '=', warehouse.id), ('active', '=', True)]
        ).write({'active': False})

    def test_3pl_queues_one_message_per_po(self):
        """Confirming a booking with 2 POs creates one 3pl inward_order message per PO."""
        if '3pl.message' not in self.env:
            self.skipTest('stock_3pl_core not installed')

        warehouse = self.env['stock.warehouse'].search([], limit=1)
        picking_type = self.env['stock.picking.type'].search(
            [('warehouse_id', '=', warehouse.id), ('code', '=', 'incoming')], limit=1
        )
        self._isolate_warehouse(warehouse)
        self._make_connector(warehouse)

        po_a = self.env['purchase.order'].create({
            'partner_id': self.partner_a.id,
            'picking_type_id': picking_type.id,
        })
        po_b = self.env['purchase.order'].create({
            'partner_id': self.partner_b.id,
            'picking_type_id': picking_type.id,
        })
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'po_ids': [(4, po_a.id), (4, po_b.id)],
            'currency_id': self.currency.id,
        })
        booking.action_confirm()

        messages = self.env['3pl.message'].search([
            ('ref_model', '=', 'purchase.order'),
            ('ref_id', 'in', [po_a.id, po_b.id]),
            ('document_type', '=', 'inward_order'),
            ('action', '=', 'create'),
        ])
        self.assertEqual(len(messages), 2, 'Expected one inward_order message per PO')

    def test_3pl_handoff_idempotent_per_po(self):
        """Calling action_confirm twice does not create duplicate messages for the same PO."""
        if '3pl.message' not in self.env:
            self.skipTest('stock_3pl_core not installed')

        warehouse = self.env['stock.warehouse'].search([], limit=1)
        picking_type = self.env['stock.picking.type'].search(
            [('warehouse_id', '=', warehouse.id), ('code', '=', 'incoming')], limit=1
        )
        self._isolate_warehouse(warehouse)
        self._make_connector(warehouse)

        po = self.env['purchase.order'].create({
            'partner_id': self.partner_a.id,
            'picking_type_id': picking_type.id,
        })
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'po_ids': [(4, po.id)],
            'currency_id': self.currency.id,
            'state': 'draft',
        })
        booking.action_confirm()
        booking.write({'state': 'draft'})  # reset to re-trigger confirm
        booking.action_confirm()

        messages = self.env['3pl.message'].search([
            ('ref_model', '=', 'purchase.order'),
            ('ref_id', '=', po.id),
            ('document_type', '=', 'inward_order'),
            ('action', '=', 'create'),
        ])
        self.assertEqual(len(messages), 1, 'Must not create duplicate message for the same PO')

    def test_3pl_no_error_without_connector_multi_po(self):
        """Confirming a booking with multiple POs is a no-op when no 3pl.connector exists."""
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'po_ids': [(4, self.po_a.id), (4, self.po_b.id)],
            'currency_id': self.currency.id,
        })
        # Must not raise even with no connector or stock_3pl_core installed
        booking.action_confirm()
        self.assertEqual(booking.state, 'confirmed')

    # ------------------------------------------------------------------
    # Landed cost — receipts aggregated from all linked POs
    # ------------------------------------------------------------------

    def _make_done_receipt(self, po, picking_type):
        picking = self.env['stock.picking'].create({
            'partner_id': po.partner_id.id,
            'picking_type_id': picking_type.id,
            'location_id': self.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': picking_type.default_location_dest_id.id,
            'purchase_id': po.id,
        })
        picking.write({'state': 'done'})
        po.write({'picking_ids': [(4, picking.id)]})
        return picking

    def test_landed_cost_aggregates_receipts_from_all_pos(self):
        """action_create_landed_cost collects done receipts from every linked PO."""
        if 'stock.landed.cost' not in self.env:
            self.skipTest('stock.landed.cost not available')

        freight_product = self.env['product.product'].create({
            'name': 'Freight Cost Multi',
            'type': 'service',
        })
        self.env['ir.config_parameter'].sudo().set_param(
            'mml_freight.freight_cost_product_id', str(freight_product.id)
        )
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        picking_type = self.env['stock.picking.type'].search([
            ('warehouse_id', '=', warehouse.id),
            ('code', '=', 'incoming'),
        ], limit=1)

        po_a = self.env['purchase.order'].create({'partner_id': self.partner_a.id})
        po_b = self.env['purchase.order'].create({'partner_id': self.partner_b.id})
        pick_a = self._make_done_receipt(po_a, picking_type)
        pick_b = self._make_done_receipt(po_b, picking_type)

        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'po_ids': [(4, po_a.id), (4, po_b.id)],
            'currency_id': self.currency.id,
            'actual_rate': 3000.00,
        })
        booking.action_create_landed_cost()

        lc = booking.landed_cost_id
        self.assertTrue(lc, 'landed_cost_id must be set after creation')
        lc_picking_ids = lc.picking_ids.ids
        self.assertIn(pick_a.id, lc_picking_ids, 'Receipt from PO A must be in the landed cost')
        self.assertIn(pick_b.id, lc_picking_ids, 'Receipt from PO B must be in the landed cost')

    def test_landed_cost_raises_when_no_pos_linked(self):
        """action_create_landed_cost raises UserError when booking has no linked POs."""
        if 'stock.landed.cost' not in self.env:
            self.skipTest('stock.landed.cost not available')
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': self.currency.id,
            'actual_rate': 500.0,
        })
        with self.assertRaises(UserError):
            booking.action_create_landed_cost()
