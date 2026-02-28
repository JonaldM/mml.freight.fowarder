from odoo import models, fields


class FreightCarrierKnplus(models.Model):
    _inherit = 'delivery.carrier'

    x_knplus_client_id = fields.Char('K+N Client ID', groups='stock.group_stock_manager')
    x_knplus_environment = fields.Selection([('demo', 'Demo'), ('production', 'Production')], default='demo')
