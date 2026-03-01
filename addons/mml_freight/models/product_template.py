from odoo import models, fields


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    x_freight_length = fields.Float('Length (cm)', default=0.0)
    x_freight_width = fields.Float('Width (cm)', default=0.0)
    x_freight_height = fields.Float('Height (cm)', default=0.0)
    x_dangerous_goods = fields.Boolean('Dangerous Goods', default=False)
    x_freight_weight = fields.Float(
        'Freight Gross Weight (kg)', default=0.0,
        help='Gross shipping weight including packaging (kg). '
             'Used for freight quoting. '
             'Distinct from the native Odoo weight field (net unit weight).',
    )
