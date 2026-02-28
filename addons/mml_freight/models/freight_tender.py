from odoo import models, fields, api
from odoo.exceptions import UserError
import logging
from datetime import timedelta

_logger = logging.getLogger(__name__)

TENDER_STATES = [
    ('draft', 'Draft'),
    ('requesting', 'Requesting Quotes'),
    ('quoted', 'Quoted'),
    ('partial', 'Partial Quotes'),
    ('selected', 'Quote Selected'),
    ('booked', 'Booked'),
    ('expired', 'Expired'),
    ('cancelled', 'Cancelled'),
]

SELECTION_MODES = [
    ('cheapest', 'Cheapest'),
    ('fastest', 'Fastest'),
    ('best_value', 'Best Value'),
    ('manual', 'Manual'),
]

MODE_PREFERENCES = [
    ('any', 'Any'),
    ('sea', 'Sea'),
    ('air', 'Air'),
    ('road', 'Road'),
]


class FreightTender(models.Model):
    _name = 'freight.tender'
    _description = 'Freight Tender'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name desc'

    name = fields.Char('Reference', readonly=True, default='New', copy=False)
    state = fields.Selection(TENDER_STATES, default='draft', required=True, tracking=True)
    purchase_order_id = fields.Many2one(
        'purchase.order', required=True, ondelete='restrict', index=True,
    )
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company,
    )

    origin_partner_id = fields.Many2one('res.partner', string='Ship From (Supplier)')
    origin_country_id = fields.Many2one('res.country', string='Origin Country')
    origin_port = fields.Char('Origin Port')

    dest_partner_id = fields.Many2one('res.partner', string='Ship To (Warehouse)')
    dest_country_id = fields.Many2one('res.country', string='Destination Country')
    dest_port = fields.Char('Destination Port')

    incoterm_id = fields.Many2one('account.incoterms', string='Incoterm')
    requested_pickup_date = fields.Date('Cargo Ready Date')
    requested_delivery_date = fields.Date('Required at Warehouse')
    tender_expiry = fields.Datetime('Tender Expiry')
    freight_mode_preference = fields.Selection(MODE_PREFERENCES, default='any')

    total_weight_kg = fields.Float(
        'Total Weight (kg)', compute='_compute_totals', store=True,
    )
    total_volume_m3 = fields.Float(
        'Total Volume (m³)', compute='_compute_totals', store=True, digits=(10, 4),
    )
    total_cbm = fields.Float(
        'Total CBM', compute='_compute_totals', store=True, digits=(10, 4),
    )
    total_packages = fields.Integer(
        'Total Packages', compute='_compute_totals', store=True,
    )
    chargeable_weight_kg = fields.Float(
        'Chargeable Weight (kg)', compute='_compute_totals', store=True,
    )
    contains_dg = fields.Boolean(
        'Contains DG', compute='_compute_totals', store=True,
    )

    goods_value = fields.Monetary('Goods Value', currency_field='currency_id')
    currency_id = fields.Many2one('res.currency')

    package_line_ids = fields.One2many(
        'freight.tender.package', 'tender_id', string='Package Lines',
    )
    quote_line_ids = fields.One2many(
        'freight.tender.quote', 'tender_id', string='Quotes',
    )

    cheapest_quote_id = fields.Many2one(
        'freight.tender.quote', compute='_compute_cheapest_quote', store=True,
    )
    selected_quote_id = fields.Many2one(
        'freight.tender.quote', string='Selected Quote', ondelete='set null',
    )
    selection_mode = fields.Selection(SELECTION_MODES, default='manual')
    selection_reason = fields.Text('Selection Reason')

    booking_id = fields.Many2one('freight.booking', string='Booking', ondelete='set null')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('freight.tender') or 'New'
            if not vals.get('tender_expiry'):
                vals['tender_expiry'] = fields.Datetime.now() + timedelta(days=3)
        return super().create(vals_list)

    @api.depends('package_line_ids.weight_kg', 'package_line_ids.volume_m3',
                 'package_line_ids.quantity', 'package_line_ids.is_dangerous')
    def _compute_totals(self):
        for t in self:
            lines = t.package_line_ids
            total_weight = sum(lines.mapped('weight_kg'))
            total_vol = sum(lines.mapped('volume_m3'))
            total_qty = sum(lines.mapped('quantity'))
            volumetric_weight = total_vol * 333
            t.total_weight_kg = total_weight
            t.total_volume_m3 = total_vol
            t.total_cbm = total_vol
            t.total_packages = total_qty
            t.chargeable_weight_kg = max(total_weight, volumetric_weight)
            t.contains_dg = any(lines.mapped('is_dangerous'))

    @api.depends('quote_line_ids.total_rate_nzd', 'quote_line_ids.state')
    def _compute_cheapest_quote(self):
        for t in self:
            received = t.quote_line_ids.filtered(lambda q: q.state == 'received')
            if received:
                t.cheapest_quote_id = received.sorted('total_rate_nzd')[0]
            else:
                t.cheapest_quote_id = False

    def action_request_quotes(self):
        """Fan out quote requests to all eligible carriers."""
        self.ensure_one()
        if self.state not in ('draft', 'partial'):
            raise UserError('Can only request quotes from Draft or Partial Quotes state.')
        self.write({'state': 'requesting'})
        registry = self.env['freight.adapter.registry']
        carriers = registry.get_eligible_carriers(self)
        if not carriers:
            raise UserError('No eligible carriers found for this tender. Check carrier configuration.')
        for carrier in carriers:
            quote = self.env['freight.tender.quote'].create({
                'tender_id': self.id,
                'carrier_id': carrier.id,
                'state': 'pending',
                'currency_id': self.currency_id.id or self.env.company.currency_id.id,
            })
            adapter = registry.get_adapter(carrier)
            if not adapter:
                quote.write({'state': 'error', 'error_message': 'No adapter registered for this carrier.'})
                continue
            try:
                results = adapter.request_quote(self)
                if results:
                    for i, result in enumerate(results):
                        target_quote = quote if i == 0 else self.env['freight.tender.quote'].create({
                            'tender_id': self.id,
                            'carrier_id': carrier.id,
                            'state': 'pending',
                            'currency_id': self.currency_id.id or self.env.company.currency_id.id,
                        })
                        curr = self.env['res.currency'].search(
                            [('name', '=', result.get('currency', 'NZD'))], limit=1,
                        )
                        target_quote.write({
                            'state': 'received',
                            'service_name': result.get('service_name', ''),
                            'transport_mode': result.get('transport_mode', 'road'),
                            'currency_id': curr.id if curr else target_quote.currency_id.id,
                            'base_rate': result.get('base_rate', result.get('total_rate', 0)),
                            'fuel_surcharge': result.get('fuel_surcharge', 0),
                            'origin_charges': result.get('origin_charges', 0),
                            'destination_charges': result.get('destination_charges', 0),
                            'customs_charges': result.get('customs_charges', 0),
                            'other_surcharges': result.get('other_surcharges', 0),
                            'estimated_transit_days': result.get('transit_days', 0),
                            'carrier_quote_ref': result.get('carrier_quote_ref', ''),
                            'raw_response': str(result),
                        })
            except Exception as e:
                _logger.error('Quote request failed for carrier %s: %s', carrier.name, e)
                quote.write({'state': 'error', 'error_message': str(e)[:500]})

        received = self.quote_line_ids.filtered(lambda q: q.state == 'received')
        pending_or_error = self.quote_line_ids.filtered(lambda q: q.state in ('pending', 'error'))
        if received and not pending_or_error:
            self.state = 'quoted'
        else:
            self.state = 'partial'
        return True

    def action_auto_select(self):
        """Auto-select best quote based on selection_mode."""
        self.ensure_one()
        received = self.quote_line_ids.filtered(lambda q: q.state == 'received')
        if not received:
            raise UserError('No received quotes to select from.')

        mode = self.selection_mode or 'cheapest'
        if mode == 'cheapest':
            winner = received.sorted('total_rate_nzd')[0]
            reason = f'Auto-selected: cheapest rate ({winner.total_rate_nzd:.2f} NZD)'
        elif mode == 'fastest':
            with_days = received.filtered(lambda q: q.estimated_transit_days > 0)
            if not with_days:
                raise UserError('No quotes with transit days for fastest selection.')
            winner = with_days.sorted('estimated_transit_days')[0]
            reason = f'Auto-selected: fastest transit ({winner.estimated_transit_days:.1f} days)'
        elif mode == 'best_value':
            def best_value_score(q):
                cost_score = q.rank_by_cost or 99
                reliability = q.carrier_id.reliability_score or 50
                return cost_score * 0.6 + (100 - reliability) * 0.4 / 10
            winner = received.sorted(best_value_score)[0]
            reason = f'Auto-selected: best value (cost rank {winner.rank_by_cost}, reliability {winner.carrier_id.reliability_score:.0f})'
        else:
            raise UserError('Manual selection mode: select a quote manually.')

        self.write({
            'selected_quote_id': winner.id,
            'state': 'selected',
            'selection_reason': reason,
        })
        self.message_post(body=reason)
        return True

    def action_book(self):
        """Confirm booking with selected carrier."""
        self.ensure_one()
        if not self.selected_quote_id:
            raise UserError('Select a quote before booking.')
        if self.state != 'selected':
            raise UserError('Tender must be in Selected state to book.')

        registry = self.env['freight.adapter.registry']
        adapter = registry.get_adapter(self.selected_quote_id.carrier_id)
        if not adapter:
            raise UserError('No adapter available for selected carrier.')

        result = adapter.create_booking(self, self.selected_quote_id)

        booking = self.env['freight.booking'].create({
            'tender_id': self.id,
            'carrier_id': self.selected_quote_id.carrier_id.id,
            'purchase_order_id': self.purchase_order_id.id,
            'currency_id': self.selected_quote_id.currency_id.id,
            'booked_rate': self.selected_quote_id.total_rate,
            'transport_mode': self.selected_quote_id.transport_mode,
            'carrier_booking_id': result.get('carrier_booking_id', ''),
            'carrier_shipment_id': result.get('carrier_shipment_id', ''),
            'carrier_tracking_url': result.get('carrier_tracking_url', ''),
            'state': 'draft',
        })
        booking.action_confirm()

        self.write({
            'booking_id': booking.id,
            'state': 'booked',
        })
        if self.purchase_order_id:
            self.purchase_order_id.freight_tender_id = self
        return True

    def action_cancel(self):
        self.write({'state': 'cancelled'})
        return True
