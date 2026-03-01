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
    _sql_constraints = [
        (
            'date_end_after_start',
            'CHECK(date_end >= date_start)',
            'Contract end date must be on or after the start date.',
        ),
    ]

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
