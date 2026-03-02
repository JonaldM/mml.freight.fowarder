import psycopg2

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
    ('contract_aware', 'Contract Aware'),
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
    po_ids = fields.Many2many(
        'purchase.order',
        'freight_tender_purchase_order_rel',
        'tender_id', 'purchase_order_id',
        string='Purchase Orders',
    )
    shipment_group_ref = fields.Char(
        'Shipment Group Ref',
        help='ROQ shipment group reference. The ROQ module adds a proper Many2one '
             'via model inheritance; this char field provides the lightweight link '
             'when ROQ is not installed.',
    )
    supplier_count = fields.Integer(
        'Supplier Count', compute='_compute_consolidation', store=True,
    )
    is_consolidated = fields.Boolean(
        'Consolidated Shipment', compute='_compute_consolidation', store=True,
        help='True when more than one purchase order is linked to this tender.',
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
    has_opportunity_cost_alert = fields.Boolean(
        'Opportunity Cost Alert',
        default=False,
        help='Set when contract_aware selected a carrier whose contracted rate exceeds the cheapest market quote.',
    )
    opportunity_cost_nzd = fields.Float(
        'Opportunity Cost (NZD)',
        digits=(10, 2),
        help='Contracted rate total minus cheapest market rate (NZD) for the selected tender.',
    )

    booking_id = fields.Many2one('freight.booking', string='Booking', ondelete='set null')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('freight.tender') or 'New'
            if not vals.get('tender_expiry'):
                vals['tender_expiry'] = fields.Datetime.now() + timedelta(days=3)
        records = super().create(vals_list)
        for tender in records:
            self.env['mml.event'].emit(
                'freight.tender.created',
                quantity=1,
                billable_unit='freight_tender',
                res_model=tender._name,
                res_id=tender.id,
                source_module='mml_freight',
                payload={'tender_ref': tender.name},
            )
        return records

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

    @api.depends('po_ids')
    def _compute_consolidation(self):
        for t in self:
            t.supplier_count = len(t.po_ids)
            t.is_consolidated = len(t.po_ids) > 1

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
        # Pessimistic lock — prevents concurrent calls creating duplicate quote records per carrier.
        try:
            self.env.cr.execute(
                'SELECT id FROM freight_tender WHERE id = %s FOR UPDATE NOWAIT', [self.id]
            )
        except psycopg2.errors.LockNotAvailable:
            raise UserError(
                'Another operation is in progress for this tender. Please try again.'
            )
        # Re-check state after lock (another request may have changed it while we waited)
        self.invalidate_recordset()
        if self.state not in ('draft', 'partial'):
            raise UserError('Tender state changed — please refresh and try again.')
        registry = self.env['freight.adapter.registry']
        carriers = registry.get_eligible_carriers(self)
        if not carriers:
            raise UserError('No eligible carriers found for this tender. Check carrier configuration.')
        self.write({'state': 'requesting'})
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
            self.write({'state': 'quoted'})
        else:
            self.write({'state': 'partial'})
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
        elif mode == 'contract_aware':
            # Find contract candidates: received quotes where carrier has active contract + remaining qty > 0
            contract_candidates = received.filtered(lambda q: q.is_contract_carrier)

            if not contract_candidates:
                # No active contract with remaining commitment — fall back to cheapest
                winner = received.sorted('total_rate_nzd')[0]
                reason = (
                    f'Contract-aware: no contract commitment remaining — '
                    f'selected cheapest market rate ({winner.total_rate_nzd:.2f} NZD, {winner.carrier_id.name})'
                )
                self.write({
                    'selected_quote_id': winner.id,
                    'state': 'selected',
                    'selection_reason': reason,
                    'has_opportunity_cost_alert': False,
                    'opportunity_cost_nzd': 0.0,
                })
                self.message_post(body=reason)
                return True

            # Multiple contract candidates: pick lowest contracted_rate_total_nzd
            winner = contract_candidates.sorted('contracted_rate_total_nzd')[0]
            contract = winner.contract_id

            # Compute opportunity cost vs cheapest market quote
            # OC = contracted rate vs cheapest available market rate (not winner's own market rate)
            cheapest_market = received.sorted('total_rate_nzd')[0]
            oc = winner.contracted_rate_total_nzd - cheapest_market.total_rate_nzd

            has_alert = oc > 0
            if has_alert:
                reason = (
                    f'Contract-aware: {winner.carrier_id.name} selected (contract commitment). '
                    f'Opportunity cost vs cheapest market: +{oc:.2f} NZD. '
                    f'Contract utilisation: {contract.utilized_quantity:.1f} of '
                    f'{contract.committed_quantity:.1f} {contract.commitment_unit}. '
                    f'Review if deviation from contract is warranted.'
                )
            else:
                reason = (
                    f'Contract-aware: {winner.carrier_id.name} selected. '
                    f'Contract rate beats market by {abs(oc):.2f} NZD. '
                    f'Contract utilisation: {contract.utilized_quantity:.1f} of '
                    f'{contract.committed_quantity:.1f} {contract.commitment_unit}.'
                )

            self.write({
                'selected_quote_id': winner.id,
                'state': 'selected',
                'selection_reason': reason,
                'has_opportunity_cost_alert': has_alert,
                'opportunity_cost_nzd': oc,
            })
            self.message_post(body=reason)
            return True

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
        # Fast-fail: cheap hint only — ORM cache may be stale. The authoritative check is post-lock below.
        if self.state != 'selected':
            raise UserError('Tender must be in Selected state to book.')
        # Pessimistic lock — prevents double-click race that would call the DSV API twice
        # and create two freight.booking records for the same tender.
        try:
            self.env.cr.execute(
                'SELECT id FROM freight_tender WHERE id = %s FOR UPDATE NOWAIT', [self.id]
            )
        except psycopg2.errors.LockNotAvailable:
            raise UserError(
                'Another operation is in progress for this tender. Please try again.'
            )
        # Re-check state after acquiring lock (another process may have changed it)
        self.invalidate_recordset()
        if self.state != 'selected':
            raise UserError('Tender state changed — please refresh and try again.')

        registry = self.env['freight.adapter.registry']
        adapter  = registry.get_adapter(self.selected_quote_id.carrier_id)
        if not adapter:
            raise UserError('No adapter available for selected carrier.')

        # Cancel any existing draft booking before creating a new one
        if self.booking_id and self.booking_id.state == 'draft' and self.booking_id.carrier_booking_id:
            prior_adapter = registry.get_adapter(self.booking_id.carrier_id)
            if prior_adapter:
                prior_adapter.cancel_booking(self.booking_id)

        result = adapter.create_booking(self, self.selected_quote_id)

        booking = self.env['freight.booking'].create({
            'tender_id':            self.id,
            'carrier_id':           self.selected_quote_id.carrier_id.id,
            'po_ids':               [(4, po.id) for po in self.po_ids],
            'currency_id':          self.selected_quote_id.currency_id.id,
            'booked_rate':          self.selected_quote_id.total_rate,
            'transport_mode':       self.selected_quote_id.transport_mode,
            'carrier_booking_id':   result.get('carrier_booking_id', ''),
            'carrier_shipment_id':  result.get('carrier_shipment_id', ''),
            'carrier_tracking_url': result.get('carrier_tracking_url', ''),
            'state':                'draft',
        })

        # Only auto-confirm if the adapter does not require manual confirmation
        if not result.get('requires_manual_confirmation'):
            booking.action_confirm()

        self.write({'booking_id': booking.id, 'state': 'booked'})
        return True

    def action_cancel(self):
        self.write({'state': 'cancelled'})
        return True

    @api.model
    def cron_expire_tenders(self):
        """Hourly cron: expire overdue quotes and tenders.

        Two passes:
        1. Expire individual quotes where rate_valid_until < now().
        2. Expire full tenders where tender_expiry < now() and still open.
        """
        now = fields.Datetime.now()

        # Pass 1: individual quote expiry regardless of tender state
        stale_quotes = self.env['freight.tender.quote'].search([
            ('state', 'in', ('pending', 'received')),
            ('rate_valid_until', '<', now),
            ('rate_valid_until', '!=', False),
        ])
        if stale_quotes:
            stale_quotes.write({'state': 'expired'})
            _logger.info('Freight cron: expired %d stale quotes', len(stale_quotes))

        # Pass 2: tender-level expiry
        open_states = ['requesting', 'quoted', 'partial']
        overdue = self.search([
            ('state', 'in', open_states),
            ('tender_expiry', '<', now),
            ('tender_expiry', '!=', False),
        ])
        if overdue:
            # Single query for all pending quotes on overdue tenders (avoids N+1)
            pending_quotes = self.env['freight.tender.quote'].search([
                ('tender_id', 'in', overdue.ids),
                ('state', '=', 'pending'),
            ])
            if pending_quotes:
                pending_quotes.write({'state': 'expired'})
            _logger.info('Freight cron: expiring %d tenders: %s', len(overdue), overdue.mapped('name'))
            overdue.write({'state': 'expired'})
