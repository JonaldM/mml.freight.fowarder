import datetime
import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)

COMMITMENT_UNITS = [
    ('teu', 'TEU (containers)'),
    ('weight_kg', 'Weight (kg)'),
    ('shipment_count', 'Shipments'),
]


class FreightCarrierContract(models.Model):
    _name = 'freight.carrier.contract'
    _description = 'Freight Carrier Contract'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_start desc'
    _check_date_end_after_start = models.Constraint(
        'CHECK(date_end >= date_start)',
        'Contract end date must be on or after the start date.',
    )
    _check_committed_quantity_positive = models.Constraint(
        'CHECK(committed_quantity > 0)',
        'Committed quantity must be greater than zero.',
    )

    name = fields.Char('Contract Name', required=True)
    carrier_id = fields.Many2one(
        'delivery.carrier', string='Carrier', required=True, ondelete='restrict', index=True,
    )
    date_start = fields.Date('Start Date', required=True)
    date_end = fields.Date('End Date', required=True)
    commitment_unit = fields.Selection(
        COMMITMENT_UNITS, string='Commitment Unit', required=True, default='teu',
    )
    committed_quantity = fields.Float('Committed Quantity', required=True, digits=(10, 2))
    contracted_rate = fields.Monetary(
        'Contracted Rate (per unit)', currency_field='contracted_rate_currency_id',
    )
    contracted_rate_currency_id = fields.Many2one(
        'res.currency', string='Rate Currency', required=True,
    )
    notes = fields.Text('Notes')

    is_active = fields.Boolean(
        'Active',
        compute='_compute_is_active',
        store=True,
        help='True when today falls within the contract period.',
    )

    @api.depends('date_start', 'date_end')
    def _compute_is_active(self):
        today = fields.Date.today()
        for c in self:
            c.is_active = bool(
                c.date_start and c.date_end and c.date_start <= today <= c.date_end
            )

    ACTIVE_BOOKING_STATES = ['confirmed', 'cargo_ready', 'picked_up', 'in_transit',
                              'arrived_port', 'customs', 'delivered', 'received']

    utilized_quantity = fields.Float(
        'Utilized', compute='_compute_utilization', store=False, digits=(10, 2),
        help='Sum of unit_quantity across confirmed/in-transit/delivered bookings in this contract period.',
    )
    remaining_quantity = fields.Float(
        'Remaining', compute='_compute_utilization', store=False, digits=(10, 2),
    )
    utilization_pct = fields.Float(
        'Utilization %', compute='_compute_utilization', store=False, digits=(5, 1),
    )

    def _compute_utilization(self):
        for contract in self:
            if not contract.id:
                contract.utilized_quantity = 0.0
                contract.remaining_quantity = contract.committed_quantity
                contract.utilization_pct = 0.0
                continue
            bookings = self.env['freight.booking'].search([
                ('contract_id', '=', contract.id),
                ('state', 'in', self.ACTIVE_BOOKING_STATES),
            ])
            utilized = sum(bookings.mapped('unit_quantity'))
            committed = contract.committed_quantity
            contract.utilized_quantity = utilized
            contract.remaining_quantity = committed - utilized
            contract.utilization_pct = (utilized / committed * 100) if committed else 0.0

    @api.model
    def cron_contract_pace_alert(self):
        """Weekly cron: warn on active contracts with low utilization and <90 days remaining.

        Threshold: utilization_pct < 50 AND days_remaining < 90.
        Posts a chatter note on the contract record.
        """
        today = fields.Date.today()
        threshold_date = today + datetime.timedelta(days=90)

        at_risk = self.search([
            ('date_start', '<=', today),
            ('date_end', '>=', today),
            ('date_end', '<=', threshold_date),
        ])

        for contract in at_risk:
            if contract.utilization_pct >= 50.0:
                continue
            days_remaining = (contract.date_end - today).days
            msg = (
                f'Commitment pace alert: {contract.utilization_pct:.1f}% utilised '
                f'({contract.utilized_quantity:.1f} of {contract.committed_quantity:.1f} '
                f'{contract.commitment_unit}), {days_remaining} days remaining. '
                f'At current pace you may fall short of committed volume.'
            )
            contract.message_post(body=msg)
            _logger.info(
                'Contract pace alert posted for contract %s (%s)', contract.name, contract.id
            )
