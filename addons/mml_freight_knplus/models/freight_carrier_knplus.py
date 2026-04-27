import os

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Env var that signals K+N onboarding is complete and the adapter is safe to use.
# When unset (or != '1'), the model rejects activation of any K+N carrier so
# operators cannot accidentally tender real freight against the scaffold adapter,
# whose methods raise UserError / NotImplementedError on real calls.
KNPLUS_ENABLE_ENV_VAR = 'MML_KNPLUS_ENABLE'

# Stable error message — also asserted in tests, so prefer string equality
# over substring matches when adjusting copy.
KNPLUS_DISABLED_MESSAGE = (
    'K+N integration is not yet active. The Kuehne+Nagel carrier adapter '
    'is a scaffold pending K+N API onboarding — its booking, tracking, and '
    'document endpoints raise UserError on real calls. Activate it only '
    'after onboarding is complete by setting MML_KNPLUS_ENABLE=1 in the '
    'Odoo environment and restarting the service. See '
    'addons/mml_freight_knplus/README.md.'
)


def _knplus_enabled():
    """Return True only when MML_KNPLUS_ENABLE=1 is present in the env."""
    return os.environ.get(KNPLUS_ENABLE_ENV_VAR, '').strip() == '1'


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
        help='sandbox -> mock responses, no HTTP calls. production -> live K+N API.',
    )

    # --- Token cache (OAuth 2.0 flow — only used when auth method is OAuth) ---
    x_knplus_access_token = fields.Char(
        'K+N Access Token (cached)',
        groups='stock.group_stock_manager',
        password=True,
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

    # ------------------------------------------------------------------
    # Activation gate — defence in depth for the K+N scaffold adapter.
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        # `vals` carries everything we need for create-time gating (no prior
        # record state to merge), so call the assert helper directly.
        for vals in vals_list:
            self._knplus_assert_can_activate(vals)
        return super().create(vals_list)

    def write(self, vals):
        # For each affected record, merge `vals` over current state and only
        # then decide whether the gate should fire. This keeps mixed-carrier
        # writes (e.g. a domain-wide active toggle hitting both DSV and K+N
        # rows) from raising on the DSV rows.
        for rec in self:
            merged = {
                'delivery_type': vals.get(
                    'delivery_type',
                    getattr(rec, 'delivery_type', None),
                ),
                'active': vals.get(
                    'active',
                    getattr(rec, 'active', True),
                ),
            }
            self._knplus_assert_can_activate(merged)
        return super().write(vals)

    @api.model
    def _knplus_assert_can_activate(self, vals):
        """Raise UserError when *vals* describes an active K+N carrier and the
        MML_KNPLUS_ENABLE env override is not set.
        """
        delivery_type = vals.get('delivery_type')
        # `active` defaults to True on delivery.carrier, so absence == active.
        active = vals.get('active', True)
        if delivery_type != 'knplus':
            return
        if not active:
            return
        if _knplus_enabled():
            return
        raise UserError(_(KNPLUS_DISABLED_MESSAGE))

    def _knplus_write_would_activate(self, vals):
        """Return True if writing *vals* on this recordset would leave any K+N
        row active. Helper retained for explicit callers (tests / external
        callers); `write()` itself uses the per-record loop above.
        """
        for rec in self:
            dtype = vals.get(
                'delivery_type',
                getattr(rec, 'delivery_type', None),
            )
            is_active = vals.get(
                'active',
                getattr(rec, 'active', True),
            )
            if dtype == 'knplus' and is_active:
                return True
        return False
