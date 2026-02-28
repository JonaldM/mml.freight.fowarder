from odoo import models, fields


class FreightCarrierDsv(models.Model):
    _inherit = 'delivery.carrier'

    x_dsv_product_name = fields.Selection([('road','Road'),('air','Air'),('sea','Sea'),('rail','Rail')], string='DSV Product')
    x_dsv_subscription_key = fields.Char('DSV Subscription Key', groups='stock.group_stock_manager', password=True)
    x_dsv_client_id = fields.Char('OAuth Client ID', groups='stock.group_stock_manager')
    x_dsv_client_secret = fields.Char('OAuth Client Secret', groups='stock.group_stock_manager', password=True)
    x_dsv_mdm = fields.Char('DSV MDM Account', groups='stock.group_stock_manager')
    x_dsv_environment = fields.Selection(
        [('demo', 'Demo (Mock)'), ('production', 'Production')],
        default='demo',
        groups='stock.group_stock_manager',
    )
    x_dsv_service_auth = fields.Char('XPress DSV-Service-Auth', groups='stock.group_stock_manager', password=True)
    x_dsv_pat = fields.Char('XPress PAT', groups='stock.group_stock_manager', password=True)
    x_dsv_access_token = fields.Char('DSV Access Token (cached)', groups='stock.group_stock_manager', copy=False)
    x_dsv_token_expiry = fields.Datetime('DSV Token Expiry', copy=False)

    def cron_refresh_dsv_tokens(self):
        """Cron: proactively refresh DSV OAuth tokens expiring within 10 minutes."""
        from datetime import timedelta
        import logging
        soon = fields.Datetime.now() + timedelta(minutes=10)
        carriers = self.search([
            ('x_dsv_environment', '=', 'production'),
            ('x_dsv_client_id', '!=', False),
            ('x_dsv_token_expiry', '<', soon),
        ])
        from odoo.addons.mml_freight_dsv.adapters.dsv_auth import refresh_token
        for carrier in carriers:
            try:
                refresh_token(carrier)
            except Exception as e:
                logging.getLogger(__name__).error('DSV token refresh failed for %s: %s', carrier.name, e)
