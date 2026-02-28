import requests
import logging
from datetime import timedelta
from odoo import fields

_logger = logging.getLogger(__name__)
DSV_OAUTH_URL = 'https://api.dsv.com/oauth2/token'
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
    """POST to DSV OAuth and store token + expiry on carrier record."""
    if not carrier.x_dsv_client_id or not carrier.x_dsv_client_secret:
        raise DsvAuthError(f'DSV carrier "{carrier.name}" missing OAuth credentials.')
    try:
        resp = requests.post(DSV_OAUTH_URL, data={
            'grant_type': 'client_credentials',
            'client_id': carrier.x_dsv_client_id,
            'client_secret': carrier.x_dsv_client_secret,
            'scope': 'freight',
        }, timeout=10)
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
    expiry = fields.Datetime.now() + timedelta(seconds=data.get('expires_in', 3600))
    carrier.sudo().write({'x_dsv_access_token': token, 'x_dsv_token_expiry': expiry})
    _logger.info('DSV token refreshed for %s', carrier.name)
    return token
