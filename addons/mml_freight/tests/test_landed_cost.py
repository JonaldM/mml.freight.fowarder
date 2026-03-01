from unittest.mock import patch
from odoo.tests.common import TransactionCase


class TestLandedCost(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if 'stock.landed.cost' not in cls.env:
            return  # skip if stock_account not installed

        # Freight cost product
        cls.freight_product = cls.env['product.product'].create({
            'name': 'Freight Cost',
            'type': 'service',
        })
        cls.env['ir.config_parameter'].sudo().set_param(
            'mml_freight.freight_cost_product_id', str(cls.freight_product.id)
        )

        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'LC Test Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })
        cls.supplier = cls.env['res.partner'].create({'name': 'LC Supplier'})
        cls.po = cls.env['purchase.order'].create({'partner_id': cls.supplier.id})
        cls.tender = cls.env['freight.tender'].create({
            'po_ids': [(4, cls.po.id)],
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': cls.tender.id,
            'po_ids': [(4, cls.po.id)],
            'currency_id': cls.env.company.currency_id.id,
            'actual_rate': 1950.00,
        })

    def _skip_if_no_landed_cost(self):
        if 'stock.landed.cost' not in self.env:
            self.skipTest('stock.landed.cost not available (stock_account not installed)')

    def _make_done_receipt(self):
        """Create a minimal done incoming picking for the PO."""
        warehouse = self.env['stock.warehouse'].search([], limit=1)
        picking_type = self.env['stock.picking.type'].search([
            ('warehouse_id', '=', warehouse.id),
            ('code', '=', 'incoming'),
        ], limit=1)
        picking = self.env['stock.picking'].create({
            'partner_id': self.supplier.id,
            'picking_type_id': picking_type.id,
            'location_id': self.env.ref('stock.stock_location_suppliers').id,
            'location_dest_id': picking_type.default_location_dest_id.id,
            'purchase_id': self.po.id,
        })
        # Force state to done for test purposes
        picking.write({'state': 'done'})
        return picking

    def test_action_create_landed_cost_creates_record(self):
        """action_create_landed_cost creates a stock.landed.cost linked to the receipt picking."""
        self._skip_if_no_landed_cost()
        picking = self._make_done_receipt()
        self.po.write({'picking_ids': [(4, picking.id)]})
        self.booking.action_create_landed_cost()
        self.assertTrue(self.booking.landed_cost_id, 'landed_cost_id must be set after creation')
        lc = self.booking.landed_cost_id
        self.assertAlmostEqual(
            lc.cost_lines[0].price_unit if lc.cost_lines else 0,
            1950.00,
            places=2,
        )

    def test_action_create_landed_cost_raises_without_actual_rate(self):
        """Raises UserError when actual_rate is 0 / not set."""
        self._skip_if_no_landed_cost()
        from odoo.exceptions import UserError
        booking_no_rate = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'tender_id': self.tender.id,
            'po_ids': [(4, self.po.id)],
            'currency_id': self.env.company.currency_id.id,
            'actual_rate': 0.0,
        })
        with self.assertRaises(UserError):
            booking_no_rate.action_create_landed_cost()

    def test_action_create_landed_cost_raises_without_receipt(self):
        """Raises UserError when no done receipt picking exists for the PO."""
        self._skip_if_no_landed_cost()
        from odoo.exceptions import UserError
        # PO with no done pickings
        po_new = self.env['purchase.order'].create({'partner_id': self.supplier.id})
        booking_new = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'tender_id': self.tender.id,
            'po_ids': [(4, po_new.id)],
            'currency_id': self.env.company.currency_id.id,
            'actual_rate': 500.0,
        })
        with self.assertRaises(UserError):
            booking_new.action_create_landed_cost()

    def test_action_create_landed_cost_raises_if_already_exists(self):
        """Raises UserError if landed_cost_id is already set (prevents duplicates)."""
        self._skip_if_no_landed_cost()
        from odoo.exceptions import UserError
        picking = self._make_done_receipt()
        self.po.write({'picking_ids': [(4, picking.id)]})
        self.booking.action_create_landed_cost()
        with self.assertRaises(UserError):
            self.booking.action_create_landed_cost()
