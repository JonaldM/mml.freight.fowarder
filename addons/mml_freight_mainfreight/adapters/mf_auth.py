"""Mainfreight API authentication helper.

Mainfreight uses a simple API key — no OAuth flow.
Header: Authorization: Secret {api_key}
"""

MF_PROD_URL = 'https://api.mainfreight.com'
MF_UAT_URL = 'https://apitest.mainfreight.com'

TRACKING_PATH = '/tracking/2.0/references/events'
TRACKING_CURRENT_PATH = '/tracking/2.0/references'


def get_base_url(carrier):
    """Return base URL for the given carrier's environment."""
    return MF_PROD_URL if carrier.x_mf_environment == 'production' else MF_UAT_URL


def get_headers(carrier):
    """Return request headers for Mainfreight API calls."""
    return {
        'Authorization': f'Secret {carrier.x_mf_api_key or ""}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
