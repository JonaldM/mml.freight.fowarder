from odoo import models, fields, api


class FreightTenderPackage(models.Model):
    _name = 'freight.tender.package'
    _description = 'Freight Tender — Package Line'
    _order = 'id'

    tender_id = fields.Many2one(
        'freight.tender', required=True, ondelete='cascade', index=True,
    )
    product_id = fields.Many2one('product.product', string='Product')
    description = fields.Char('Description')
    quantity = fields.Integer('Qty', default=1)
    weight_kg = fields.Float('Gross Weight (kg)')
    net_weight_kg = fields.Float('Net Weight (kg)')
    length_cm = fields.Float('Length (cm)')
    width_cm = fields.Float('Width (cm)')
    height_cm = fields.Float('Height (cm)')
    volume_m3 = fields.Float(
        'Volume (m³)', compute='_compute_volume', store=True, digits=(10, 6),
    )
    hs_code = fields.Char('HS Code')
    is_dangerous = fields.Boolean('Dangerous Goods', default=False)

    @api.depends('length_cm', 'width_cm', 'height_cm', 'quantity')
    def _compute_volume(self):
        for line in self:
            if line.length_cm and line.width_cm and line.height_cm:
                line.volume_m3 = (
                    line.length_cm * line.width_cm * line.height_cm
                    / 1_000_000.0
                    * line.quantity
                )
            else:
                line.volume_m3 = 0.0

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            tmpl = self.product_id.product_tmpl_id
            self.description = self.product_id.name
            self.length_cm = tmpl.x_freight_length
            self.width_cm = tmpl.x_freight_width
            self.height_cm = tmpl.x_freight_height
            self.is_dangerous = tmpl.x_dangerous_goods
            self.weight_kg = tmpl.x_freight_weight
            self.hs_code = getattr(self.product_id, 'hs_code', False) or ''
