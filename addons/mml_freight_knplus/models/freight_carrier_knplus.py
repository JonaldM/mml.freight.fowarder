from odoo import models, fields


class FreightCarrierKnplus(models.Model):
    _inherit = 'delivery.carrier'

    # --- Auth credentials (TBC: API key vs OAuth 2.0 — confirm with K+N rep) ---
    x_knplus_client_id = fields.Char(
        'K+N OAuth Client ID',
        groups='stock.group_stock_manager',
        help='OAuth 2.0 client ID issued by K+N developer portal. '
             'Leave blank if K+N uses API key auth instead.',
    )
    x_knplus_client_secret = fields.Char(
        'K+N OAuth Client Secret',
        groups='stock.group_stock_manager',
        password=True,
        help='OAuth 2.0 client secret. '
             'Leave blank if K+N uses API key auth instead.',
    )
    x_knplus_api_key = fields.Char(
        'K+N API Key / Subscription Key',
        groups='stock.group_stock_manager',
        password=True,
        help='API key or Ocp-Apim-Subscription-Key header value. '
             'Used if K+N portal uses key-based auth (Azure APIM pattern).',
    )
    x_knplus_account_number = fields.Char(
        'K+N Account Number',
        groups='stock.group_stock_manager',
        help='Customer account number issued by K+N. Required on all API calls.',
    )
    x_knplus_environment = fields.Selection(
        [('sandbox', 'Sandbox'), ('production', 'Production')],
        default='sandbox',
        groups='stock.group_stock_manager',
        help='sandbox → mock responses, no HTTP calls. production → live K+N API.',
    )

    # --- Token cache (OAuth 2.0 flow — only used when auth method is OAuth) ---
    x_knplus_access_token = fields.Char(
        'K+N Access Token (cached)',
        groups='stock.group_stock_manager',
        copy=False,
        help='Cached OAuth access token. Refreshed automatically on expiry.',
    )
    x_knplus_token_expiry = fields.Datetime(
        'K+N Token Expiry',
        copy=False,
        help='Datetime when the cached access token expires.',
    )

    # --- Quoting configuration ---
    x_knplus_quote_mode = fields.Selection(
        [
            ('manual', 'Manual entry / myKN portal'),
            ('api', 'API (if available for account tier)'),
        ],
        default='manual',
        help='K+N public quote API is unconfirmed. Default to manual — ops enter '
             'quotes via myKN (mykn.kuehne-nagel.com) and record them on the tender.',
    )
