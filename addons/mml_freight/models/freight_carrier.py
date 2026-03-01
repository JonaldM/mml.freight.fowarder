from odoo import models, fields

TRANSPORT_MODES = [
    ('any', 'Any'),
    ('road', 'Road'),
    ('air', 'Air'),
    ('sea_lcl', 'Sea LCL'),
    ('sea_fcl', 'Sea FCL'),
    ('rail', 'Rail'),
    ('express', 'Express'),
]


class FreightCarrier(models.Model):
    _inherit = 'delivery.carrier'

    auto_tender = fields.Boolean(
        'Include in Auto-Tender',
        default=False,
        help='Include this carrier in automatic tender fan-out from POs.',
    )
    origin_country_ids = fields.Many2many(
        'res.country',
        'freight_carrier_origin_country_rel',
        'carrier_id', 'country_id',
        string='Eligible Origin Countries',
        help='Leave empty to allow all origins.',
    )
    dest_country_ids = fields.Many2many(
        'res.country',
        'freight_carrier_dest_country_rel',
        'carrier_id', 'country_id',
        string='Eligible Destination Countries',
        help='Leave empty to allow all destinations.',
    )
    max_weight_kg = fields.Float(
        'Max Weight (kg)',
        default=0.0,
        help='0 = no limit.',
    )
    supports_dg = fields.Boolean('Dangerous Goods Capable', default=False)
    transport_modes = fields.Selection(
        TRANSPORT_MODES,
        string='Transport Mode',
        default='any',
    )
    reliability_score = fields.Float(
        'Reliability Score',
        default=50.0,
        help='0–100. Used in best_value auto-selection scoring.',
    )
    x_webhook_secret = fields.Char(
        'Webhook Signing Secret',
        groups='stock.group_stock_manager',
        copy=False,
        password=True,
        help='HMAC-SHA256 secret shared with the carrier for webhook signature validation. '
             'Generate with: python -c "import secrets; print(secrets.token_hex(32))"',
    )
    freight_contract_ids = fields.One2many(
        'freight.carrier.contract', 'carrier_id', string='Contracts',
    )

    def is_eligible(self, origin_country, dest_country, weight_kg, has_dg, mode_preference):
        """Return True if this carrier is eligible for the given shipment parameters.

        Args:
            origin_country: res.country record or None
            dest_country: res.country record or None
            weight_kg: float total chargeable weight
            has_dg: bool — shipment contains dangerous goods
            mode_preference: str selection value ('any', 'road', 'air', etc.)
        """
        self.ensure_one()
        if has_dg and not self.supports_dg:
            return False
        if self.max_weight_kg > 0 and weight_kg > self.max_weight_kg:
            return False
        if origin_country and self.origin_country_ids and origin_country not in self.origin_country_ids:
            return False
        if dest_country and self.dest_country_ids and dest_country not in self.dest_country_ids:
            return False
        if mode_preference != 'any' and self.transport_modes != 'any' and self.transport_modes != mode_preference:
            return False
        return True
