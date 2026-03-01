import requests
import logging
from datetime import timedelta
from odoo import fields

_logger = logging.getLogger(__name__)

# DSV OAuth token endpoint (production).
# Demo environment short-circuits before this is called (returns DEMO_TOKEN).
# Ref: https://developer.dsv.com/oauth-guide
_OAUTH_URL = 'https://api.dsv.com/my/oauth/v1/token'

# Refresh token when less than this many seconds remain before expiry.
# DSV access tokens expire in 10 minutes (600s); refresh 120s before.
REFRESH_WINDOW_SECONDS = 120


class DsvAuthError(Exception):
    pass


def get_token(carrier):
    """Return valid DSV access token. Demo mode returns DEMO_TOKEN without HTTP."""
    if carrier.x_dsv_environment == 'demo':
        return 'DEMO_TOKEN'
    now = fields.Datetime.now()
    if (carrier.x_dsv_access_token and carrier.x_dsv_token_expiry
            and carrier.x_dsv_token_expiry > now + timedelta(seconds=REFRESH_WINDOW_SECONDS)):
        return carrier.x_dsv_access_token
    return refresh_token(carrier)


def refresh_token(carrier):
    """POST to DSV OAuth endpoint and store token + expiry on carrier record.

    DSV requires:
      - DSV-Subscription-Key header (from Developer Portal profile page)
      - client_credentials grant with myDSV username/password as client_id/client_secret
      - Access token valid for 10 minutes; no refresh_token for client_credentials grant
    """
    if not carrier.x_dsv_client_id or not carrier.x_dsv_client_secret:
        raise DsvAuthError(f'DSV carrier "{carrier.name}" missing OAuth credentials.')
    try:
        resp = requests.post(
            _OAUTH_URL,
            headers={
                'DSV-Subscription-Key': carrier.x_dsv_subscription_key or '',
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            data={
                'grant_type':    'client_credentials',
                'client_id':     carrier.x_dsv_client_id,
                'client_secret': carrier.x_dsv_client_secret,
            },
            timeout=10,
        )
    except requests.RequestException as e:
        raise DsvAuthError(f'DSV OAuth request failed: {e}') from e
    if resp.status_code in (401, 403):
        raise DsvAuthError(f'DSV OAuth rejected credentials (HTTP {resp.status_code}).')
    if not resp.ok:
        raise DsvAuthError(f'DSV OAuth HTTP {resp.status_code}.')
    data = resp.json()
    token = data.get('access_token')
    if not token:
        raise DsvAuthError('DSV OAuth response missing access_token.')
    # DSV tokens expire in 10 minutes; default to 600s if expires_in absent
    expiry = fields.Datetime.now() + timedelta(seconds=data.get('expires_in', 600))
    carrier.sudo().write({'x_dsv_access_token': token, 'x_dsv_token_expiry': expiry})
    _logger.info('DSV token refreshed for %s', carrier.name)
    return token
