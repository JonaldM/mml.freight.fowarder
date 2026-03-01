from odoo import models, fields, api

QUOTE_STATES = [
    ('pending', 'Pending'),
    ('received', 'Received'),
    ('expired', 'Expired'),
    ('error', 'Error'),
    ('declined', 'Declined'),
]

TRANSPORT_MODES = [
    ('road', 'Road'),
    ('air', 'Air'),
    ('sea_lcl', 'Sea LCL'),
    ('sea_fcl', 'Sea FCL'),
    ('rail', 'Rail'),
    ('express', 'Express'),
]


class FreightTenderQuote(models.Model):
    _name = 'freight.tender.quote'
    _description = 'Freight Tender — Carrier Quote'
    _order = 'total_rate_nzd asc, estimated_transit_days asc'

    tender_id = fields.Many2one(
        'freight.tender', required=True, ondelete='cascade', index=True,
    )
    carrier_id = fields.Many2one('delivery.carrier', string='Carrier', required=True)
    state = fields.Selection(QUOTE_STATES, default='pending', required=True)
    service_name = fields.Char('Service')
    transport_mode = fields.Selection(TRANSPORT_MODES)

    currency_id = fields.Many2one('res.currency', required=True)
    base_rate = fields.Monetary('Base Rate', currency_field='currency_id')
    fuel_surcharge = fields.Monetary('Fuel Surcharge', currency_field='currency_id')
    origin_charges = fields.Monetary('Origin Charges', currency_field='currency_id')
    destination_charges = fields.Monetary('Destination Charges', currency_field='currency_id')
    customs_charges = fields.Monetary('Customs Charges', currency_field='currency_id')
    other_surcharges = fields.Monetary('Other Surcharges', currency_field='currency_id')
    total_rate = fields.Monetary(
        'Total Rate', compute='_compute_total_rate', store=True, currency_field='currency_id',
    )
    total_rate_nzd = fields.Float(
        'Total Rate (NZD)', compute='_compute_total_rate_nzd', store=True, digits=(10, 2),
    )

    rate_valid_until = fields.Datetime('Rate Valid Until')
    estimated_transit_days = fields.Float('Transit Days')
    estimated_pickup_date = fields.Date('Est. Pickup')
    estimated_delivery_date = fields.Date('Est. Delivery')
    carrier_quote_ref = fields.Char('Carrier Quote Ref')
    error_message = fields.Text('Error')
    raw_response = fields.Text('Raw Response')

    is_cheapest = fields.Boolean(compute='_compute_rankings', store=True)
    is_fastest = fields.Boolean(compute='_compute_rankings', store=True)
    rank_by_cost = fields.Integer(compute='_compute_rankings', store=True)
    rank_by_speed = fields.Integer(compute='_compute_rankings', store=True)
    cost_vs_cheapest_pct = fields.Float(
        '% vs Cheapest', compute='_compute_rankings', store=True, digits=(5, 1),
    )

    @api.depends('base_rate', 'fuel_surcharge', 'origin_charges',
                 'destination_charges', 'customs_charges', 'other_surcharges')
    def _compute_total_rate(self):
        for q in self:
            q.total_rate = (
                q.base_rate + q.fuel_surcharge + q.origin_charges
                + q.destination_charges + q.customs_charges + q.other_surcharges
            )

    @api.depends('total_rate', 'currency_id')
    def _compute_total_rate_nzd(self):
        nzd = self.env.ref('base.NZD', raise_if_not_found=False)
        for q in self:
            if not q.currency_id or not q.total_rate:
                q.total_rate_nzd = 0.0
                continue
            if nzd and q.currency_id != nzd:
                q.total_rate_nzd = q.currency_id._convert(
                    q.total_rate, nzd, q.tender_id.company_id, fields.Date.today(),
                )
            else:
                q.total_rate_nzd = q.total_rate

    @api.depends('tender_id.quote_line_ids.total_rate_nzd',
                 'tender_id.quote_line_ids.estimated_transit_days',
                 'tender_id.quote_line_ids.state')
    def _compute_rankings(self):
        for q in self:
            received = q.tender_id.quote_line_ids.filtered(
                lambda x: x.state == 'received'
            )
            if not received:
                q.is_cheapest = False
                q.is_fastest = False
                q.rank_by_cost = 0
                q.rank_by_speed = 0
                q.cost_vs_cheapest_pct = 0.0
                continue

            sorted_cost = received.sorted('total_rate_nzd')
            sorted_speed = received.filtered(
                lambda x: x.estimated_transit_days > 0
            ).sorted('estimated_transit_days')

            cheapest_rate = sorted_cost[0].total_rate_nzd if sorted_cost else 0

            q.rank_by_cost = list(sorted_cost.ids).index(q.id) + 1 if q.id in sorted_cost.ids else 0
            q.is_cheapest = bool(sorted_cost and sorted_cost[0].id == q.id)
            q.is_fastest = bool(sorted_speed and sorted_speed[0].id == q.id)
            if sorted_speed:
                speed_ids = list(sorted_speed.ids)
                q.rank_by_speed = speed_ids.index(q.id) + 1 if q.id in speed_ids else 0
            else:
                q.rank_by_speed = 0

            if cheapest_rate and q.total_rate_nzd:
                q.cost_vs_cheapest_pct = (
                    (q.total_rate_nzd - cheapest_rate) / cheapest_rate * 100
                )
            else:
                q.cost_vs_cheapest_pct = 0.0

    is_selected = fields.Boolean(
        'Selected', compute='_compute_is_selected',
        help='True if this quote is currently selected on the tender.',
    )

    @api.depends('tender_id.selected_quote_id')
    def _compute_is_selected(self):
        for q in self:
            q.is_selected = q.tender_id.selected_quote_id.id == q.id

    # --- Contract awareness ---
    contract_id = fields.Many2one(
        'freight.carrier.contract',
        string='Active Contract',
        compute='_compute_contract_fields',
        store=False,
        help='Active contract for this carrier (if any).',
    )
    is_contract_carrier = fields.Boolean(
        'Contract Carrier',
        compute='_compute_contract_fields',
        store=False,
        help='True when this carrier has an active contract with remaining commitment.',
    )
    contract_remaining_qty = fields.Float(
        'Contract Remaining',
        compute='_compute_contract_fields',
        store=False,
        digits=(10, 2),
    )
    contracted_rate_total_nzd = fields.Float(
        'Contracted Rate Total (NZD)',
        compute='_compute_contract_fields',
        store=False,
        digits=(10, 2),
        help='What this shipment would cost at the contracted rate, converted to NZD.',
    )
    opportunity_cost_nzd = fields.Float(
        'Opportunity Cost (NZD)',
        compute='_compute_contract_fields',
        store=False,
        digits=(10, 2),
        help='Contracted rate total minus market quote total (NZD). '
             'Positive = contract costs more than market. Negative = contract saves money.',
    )

    def _get_estimated_unit_quantity(self):
        """Estimate unit quantity for this quote based on transport mode and tender cargo."""
        self.ensure_one()
        mode = self.transport_mode or ''
        if mode == 'sea_fcl':
            return max(1.0, float(self.tender_id.total_packages or 1))
        elif mode in ('air', 'sea_lcl'):
            return max(1.0, self.tender_id.chargeable_weight_kg or 1.0)
        else:
            return 1.0

    @api.depends('carrier_id', 'transport_mode', 'total_rate_nzd',
                 'tender_id.total_packages', 'tender_id.chargeable_weight_kg')
    def _compute_contract_fields(self):
        today = fields.Date.today()
        for q in self:
            if not q.carrier_id:
                q.contract_id = False
                q.is_contract_carrier = False
                q.contract_remaining_qty = 0.0
                q.contracted_rate_total_nzd = 0.0
                q.opportunity_cost_nzd = 0.0
                continue

            contract = self.env['freight.carrier.contract'].search([
                ('carrier_id', '=', q.carrier_id.id),
                ('date_start', '<=', today),
                ('date_end', '>=', today),
            ], limit=1)

            if not contract:
                q.contract_id = False
                q.is_contract_carrier = False
                q.contract_remaining_qty = 0.0
                q.contracted_rate_total_nzd = 0.0
                q.opportunity_cost_nzd = 0.0
                continue

            remaining = contract.remaining_quantity
            q.contract_id = contract
            q.is_contract_carrier = remaining > 0
            q.contract_remaining_qty = remaining

            unit_qty = q._get_estimated_unit_quantity()
            nzd = self.env.ref('base.NZD', raise_if_not_found=False)
            rate_nzd = contract.contracted_rate
            if nzd and contract.contracted_rate_currency_id != nzd:
                rate_nzd = contract.contracted_rate_currency_id._convert(
                    contract.contracted_rate, nzd,
                    q.tender_id.company_id or self.env.company,
                    today,
                )
            contracted_total = rate_nzd * unit_qty
            q.contracted_rate_total_nzd = contracted_total
            q.opportunity_cost_nzd = contracted_total - (q.total_rate_nzd or 0.0)

    def action_select(self):
        """Select this quote: write selected_quote_id on tender and advance to 'selected' state."""
        self.ensure_one()
        self.tender_id.write({
            'selected_quote_id': self.id,
            'state': 'selected',
        })
        return True

    def action_decline(self):
        """Mark this quote as declined — removes it from consideration."""
        self.write({'state': 'declined'})
        return True
