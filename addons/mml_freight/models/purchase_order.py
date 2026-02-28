from odoo import models, fields, api

INCOTERMS_BUYER = {'EXW', 'FCA', 'FOB', 'FAS'}
INCOTERMS_SELLER = {'CFR', 'CIF', 'CPT', 'CIP', 'DAP', 'DPU', 'DDP'}

FREIGHT_RESPONSIBILITY = [
    ('buyer', 'Buyer (MML arranges)'),
    ('seller', 'Seller (Supplier arranges)'),
    ('na', 'Not Applicable'),
]

MODE_PREFERENCES = [
    ('any', 'Any'),
    ('sea', 'Sea'),
    ('air', 'Air'),
    ('road', 'Road'),
]


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    freight_responsibility = fields.Selection(
        FREIGHT_RESPONSIBILITY,
        string='Freight Responsibility',
        compute='_compute_freight_responsibility',
        store=True,
        readonly=False,
    )
    freight_tender_id = fields.Many2one(
        'freight.tender', string='Freight Tender', ondelete='set null',
    )
    freight_booking_id = fields.Many2one(
        'freight.booking',
        related='freight_tender_id.booking_id',
        string='Freight Booking',
        readonly=True,
    )
    freight_status = fields.Selection(
        related='freight_tender_id.booking_id.state',
        string='Freight Status',
        readonly=True,
    )
    freight_cost = fields.Monetary(
        related='freight_tender_id.booking_id.booked_rate',
        string='Freight Cost',
        readonly=True,
        currency_field='currency_id',
    )
    freight_carrier_name = fields.Char(
        related='freight_tender_id.booking_id.carrier_id.name',
        string='Freight Carrier',
        readonly=True,
    )
    freight_tracking_url = fields.Char(
        related='freight_tender_id.booking_id.carrier_tracking_url',
        string='Tracking URL',
        readonly=True,
    )
    freight_eta = fields.Datetime(
        related='freight_tender_id.booking_id.eta',
        string='ETA',
        readonly=True,
    )
    cargo_ready_date = fields.Date('Cargo Ready Date')
    required_delivery_date = fields.Date('Required at Warehouse')
    freight_mode_preference = fields.Selection(MODE_PREFERENCES, default='any')
    tender_count = fields.Integer(compute='_compute_tender_count')

    @api.depends('incoterm_id', 'incoterm_id.code')
    def _compute_freight_responsibility(self):
        for po in self:
            code = po.incoterm_id.code if po.incoterm_id else False
            if not code:
                po.freight_responsibility = 'na'
            elif code in INCOTERMS_BUYER:
                po.freight_responsibility = 'buyer'
            elif code in INCOTERMS_SELLER:
                po.freight_responsibility = 'seller'
            else:
                po.freight_responsibility = 'na'

    def _compute_tender_count(self):
        for po in self:
            po.tender_count = self.env['freight.tender'].search_count([
                ('purchase_order_id', '=', po.id),
            ])

    def action_view_freight_tenders(self):
        self.ensure_one()
        return {
            'name': 'Freight Tenders',
            'type': 'ir.actions.act_window',
            'res_model': 'freight.tender',
            'view_mode': 'list,form',
            'domain': [('purchase_order_id', '=', self.id)],
            'context': {'default_purchase_order_id': self.id},
        }

    def action_request_freight_tender(self):
        """Open a new freight tender linked to this PO."""
        self.ensure_one()
        tender = self.env['freight.tender'].create({
            'purchase_order_id': self.id,
            'company_id': self.company_id.id,
            'origin_partner_id': self.partner_id.id,
            'origin_country_id': self.partner_id.country_id.id if self.partner_id.country_id else False,
            'incoterm_id': self.incoterm_id.id if self.incoterm_id else False,
            'requested_pickup_date': self.cargo_ready_date,
            'requested_delivery_date': self.required_delivery_date,
            'goods_value': self.amount_untaxed,
            'currency_id': self.currency_id.id,
            'freight_mode_preference': self.freight_mode_preference or 'any',
        })
        self.freight_tender_id = tender
        return {
            'name': 'Freight Tender',
            'type': 'ir.actions.act_window',
            'res_model': 'freight.tender',
            'res_id': tender.id,
            'view_mode': 'form',
        }
