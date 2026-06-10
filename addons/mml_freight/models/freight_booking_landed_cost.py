from odoo import models
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

# Incoterms whose price/landed figure bundles INSURANCE into the cost of the
# goods (the "I" in CIF/CIP = cost, Insurance & freight). Under IAS 2, insurance
# (and import duty) form part of inventory cost and should be apportioned across
# the goods BY VALUE — a higher-value item bears proportionally more insurance —
# whereas pure ocean/air freight correlates with weight and is apportioned
# BY WEIGHT. For these incoterms the capitalised amount is therefore split by
# value rather than weight. All other (freight-only) incoterms stay by weight.
_INSURANCE_BEARING_INCOTERMS = {'CIF', 'CIP'}


class FreightBookingLandedCost(models.Model):
    _inherit = 'freight.booking'

    def _landed_cost_split_method(self):
        """Choose the IAS 2-aligned cost apportionment for this booking.

        Returns ``'by_value'`` when the incoterm bundles insurance/duty into the
        landed figure (CIF/CIP) so the insurance component is apportioned by the
        value of the goods, and ``'by_weight'`` for freight-only incoterms.

        LIMITATION (documented): ``actual_rate`` is a single blended freight
        figure — the data model carries NO separate insurance amount, import-duty
        amount, or duty/insurance breakdown (only ``freight.tender.goods_value``
        for the cargo value and ``freight.tender.incoterm_id`` for the incoterm).
        Because the insurance portion cannot be separated from the freight portion
        of a single number, an insurance-bearing booking is capitalised entirely
        by_value rather than producing two genuinely distinct freight/insurance
        cost lines. To split into a true freight (by_weight) + insurance/duty
        (by_value) pair, add explicit insurance_amount / duty_amount fields to the
        booking (e.g. parsed from the carrier invoice) and branch on those here.
        """
        self.ensure_one()
        incoterm = self.tender_id.incoterm_id if self.tender_id else False
        code = (incoterm.code or '').upper() if incoterm else ''
        if code in _INSURANCE_BEARING_INCOTERMS:
            return 'by_value'
        return 'by_weight'

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
        # IAS 2: insurance/duty-bearing incoterms (CIF/CIP) apportion by value;
        # freight-only incoterms apportion by weight. See _landed_cost_split_method.
        split_method = self._landed_cost_split_method()
        landed_cost = self.env['stock.landed.cost'].create({
            'picking_ids':    [(4, r.id) for r in receipts],
            'vendor_bill_id': self.invoice_id.id if self.invoice_id else False,
            'cost_lines': [(0, 0, {
                'product_id':   freight_product.id,
                'name':         f'Freight — {self.name}',
                'price_unit':   self.actual_rate,
                'split_method': split_method,
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
