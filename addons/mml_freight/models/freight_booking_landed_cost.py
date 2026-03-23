from odoo import models
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


class FreightBookingLandedCost(models.Model):
    _inherit = 'freight.booking'

    def _get_freight_cost_product(self):
        """Return the configured freight cost product for landed cost creation."""
        param = self.env['ir.config_parameter'].sudo().get_param(
            'mml_freight.freight_cost_product_id'
        )
        if param:
            try:
                product = self.env['product.product'].browse(int(param))
                if product.exists():
                    return product
            except (ValueError, TypeError):
                pass
        return self.env['product.product'].search(
            [
                ('name', '=', 'Freight Cost'),
                ('type', '=', 'service'),
                ('company_id', 'in', [self.company_id.id, False]),
            ],
            limit=1,
        )

    def action_create_landed_cost(self):
        """Create a stock.landed.cost from this booking's actual_rate and open it.

        Collects done incoming receipts from all linked purchase orders so that a
        consolidated multi-PO booking is covered by a single landed cost entry.
        Odoo's split_method (by_weight / by_value) then apportions the freight
        cost across the individual product moves.
        """
        self.ensure_one()
        if not self.actual_rate:
            raise UserError(
                'Set the actual freight rate (Fetch Invoice or enter manually) '
                'before creating a landed cost.'
            )
        if self.landed_cost_id:
            raise UserError(
                'A landed cost already exists for this booking (%s). '
                'Open it from the Financials group.' % self.landed_cost_id.name
            )
        if 'stock.landed.cost' not in self.env:
            raise UserError(
                'stock.landed.cost model not available. '
                'Ensure the stock_account (or stock_landed_costs) module is installed.'
            )
        if not self.po_ids:
            raise UserError('No purchase orders linked to this booking.')
        receipts = self.po_ids.mapped('picking_ids').filtered(
            lambda p: p.state == 'done' and p.picking_type_code == 'incoming'
        )
        if not receipts:
            po_names = ', '.join(self.po_ids.mapped('name'))
            raise UserError(
                'No validated receipts found for %s. '
                'Receive the goods before creating a landed cost.' % po_names
            )
        freight_product = self._get_freight_cost_product()
        if not freight_product:
            raise UserError(
                'No freight cost product configured. '
                'Set system parameter mml_freight.freight_cost_product_id, '
                'or create a service product named "Freight Cost".'
            )
        account = (
            freight_product.categ_id.property_account_expense_categ_id
            if freight_product.categ_id else False
        )
        landed_cost = self.env['stock.landed.cost'].create({
            'picking_ids':    [(4, r.id) for r in receipts],
            'vendor_bill_id': self.invoice_id.id if self.invoice_id else False,
            'cost_lines': [(0, 0, {
                'product_id':   freight_product.id,
                'name':         f'Freight — {self.name}',
                'price_unit':   self.actual_rate,
                'split_method': 'by_weight',
                'account_id':   account.id if account else False,
            })],
        })
        self.landed_cost_id = landed_cost
        self.message_post(
            body=(
                f'Landed cost created: {landed_cost.name} '
                f'({self.actual_rate:.2f} {self.currency_id.name}) '
                f'covering {len(receipts)} receipt(s) across {len(self.po_ids)} PO(s). '
                f'Validate the landed cost to apply freight to product valuations.'
            )
        )
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.landed.cost',
            'res_id': landed_cost.id,
            'view_mode': 'form',
        }
