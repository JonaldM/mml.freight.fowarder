from odoo import models, fields

# Services that each have their own APIM subscription (primary + secondary keys).
_SUBKEY_SERVICES = ('quote', 'booking', 'doc_upload', 'doc_download', 'visibility', 'invoicing', 'webhook')


class FreightCarrierDsv(models.Model):
    _inherit = 'delivery.carrier'

    x_dsv_product_name = fields.Selection(
        [('road', 'Road'), ('air', 'Air'), ('sea', 'Sea'), ('rail', 'Rail')],
        string='DSV Product',
    )
    x_dsv_client_id = fields.Char('OAuth Client ID', groups='stock.group_stock_manager')
    x_dsv_client_secret = fields.Char('OAuth Client Secret', groups='stock.group_stock_manager', password=True)
    x_dsv_mdm = fields.Char('DSV MDM Account', groups='stock.group_stock_manager')
    x_dsv_environment = fields.Selection(
        [('demo', 'Demo (Mock)'), ('production', 'Production')],
        default='demo',
        groups='stock.group_stock_manager',
    )

    # XPress API credentials
    x_dsv_service_auth = fields.Char('XPress DSV-Service-Auth', groups='stock.group_stock_manager', password=True)
    x_dsv_pat = fields.Char('XPress PAT', groups='stock.group_stock_manager', password=True)

    # Cached OAuth token (Generic API)
    x_dsv_access_token = fields.Char('DSV Access Token (cached)', password=True, groups='stock.group_stock_manager', copy=False)
    x_dsv_token_expiry = fields.Datetime('DSV Token Expiry', copy=False)

    # Per-service APIM subscription keys — primary + secondary each
    x_dsv_subkey_quote_primary      = fields.Char('Quote — Primary',            groups='stock.group_stock_manager', password=True)
    x_dsv_subkey_quote_secondary    = fields.Char('Quote — Secondary',           groups='stock.group_stock_manager', password=True)
    x_dsv_subkey_booking_primary    = fields.Char('Booking — Primary',           groups='stock.group_stock_manager', password=True)
    x_dsv_subkey_booking_secondary  = fields.Char('Booking — Secondary',         groups='stock.group_stock_manager', password=True)
    x_dsv_subkey_doc_upload_primary     = fields.Char('Doc Upload — Primary',    groups='stock.group_stock_manager', password=True)
    x_dsv_subkey_doc_upload_secondary   = fields.Char('Doc Upload — Secondary',  groups='stock.group_stock_manager', password=True)
    x_dsv_subkey_doc_download_primary   = fields.Char('Doc Download — Primary',  groups='stock.group_stock_manager', password=True)
    x_dsv_subkey_doc_download_secondary = fields.Char('Doc Download — Secondary', groups='stock.group_stock_manager', password=True)
    x_dsv_subkey_visibility_primary     = fields.Char('Visibility — Primary',    groups='stock.group_stock_manager', password=True)
    x_dsv_subkey_visibility_secondary   = fields.Char('Visibility — Secondary',  groups='stock.group_stock_manager', password=True)
    x_dsv_subkey_invoicing_primary      = fields.Char('Invoicing — Primary',     groups='stock.group_stock_manager', password=True)
    x_dsv_subkey_invoicing_secondary    = fields.Char('Invoicing — Secondary',   groups='stock.group_stock_manager', password=True)
    x_dsv_subkey_webhook_primary        = fields.Char('Webhook — Primary',       groups='stock.group_stock_manager', password=True)
    x_dsv_subkey_webhook_secondary      = fields.Char('Webhook — Secondary',     groups='stock.group_stock_manager', password=True)

    # LCL/FCL mode thresholds
    x_dsv_lcl_fcl_threshold = fields.Float(
        'LCL→FCL Threshold (CBM)', default=15.0,
        help='Total CBM below which LCL is requested. Grey zone begins here.',
    )
    x_dsv_fcl20_fcl40_threshold = fields.Float(
        'FCL20→FCL40 Threshold (CBM)', default=25.0,
        help='Total CBM above which FCL 40ft is also quoted.',
    )
    x_dsv_fcl40_upper = fields.Float(
        'FCL40 Upper Threshold (CBM)', default=40.0,
        help='Total CBM above which only FCL 40ft is requested.',
    )

    def dsv_subkey(self, service):
        """Return the active APIM subscription key for a given DSV service.

        Falls back primary → secondary → ''. Use this in all API call headers.
        Valid service names: quote, booking, doc_upload, doc_download,
                             visibility, invoicing, webhook.
        """
        primary = getattr(self, f'x_dsv_subkey_{service}_primary', '') or ''
        if primary:
            return primary
        return getattr(self, f'x_dsv_subkey_{service}_secondary', '') or ''

    def dsv_any_subkey(self):
        """Return any configured subscription key (used for the shared OAuth token endpoint)."""
        for service in _SUBKEY_SERVICES:
            key = self.dsv_subkey(service)
            if key:
                return key
        return ''

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
