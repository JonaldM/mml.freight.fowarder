from odoo import models, fields


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    x_freight_length = fields.Float('Length (cm)', default=0.0)
    x_freight_width = fields.Float('Width (cm)', default=0.0)
    x_freight_height = fields.Float('Height (cm)', default=0.0)
    x_dangerous_goods = fields.Boolean('Dangerous Goods', default=False)
