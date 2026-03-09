"""Mainfreight API authentication helper.

Mainfreight uses a simple API key — no OAuth flow.
Header: Authorization: Secret {api_key}
"""

MF_PROD_URL = 'https://api.mainfreight.com'
MF_UAT_URL = 'https://apitest.mainfreight.com'

TRACKING_PATH = '/tracking/2.0/references/events'
TRACKING_CURRENT_PATH = '/tracking/2.0/references'
DOCUMENTS_PATH = '/documents/2.0/references'   # unconfirmed — stub until MF developer account active
INVOICE_PATH   = '/invoices/2.0/references'    # unconfirmed — stub until MF developer account active


def get_base_url(carrier):
    """Return base URL for the given carrier's environment."""
    return MF_PROD_URL if carrier.x_mf_environment == 'production' else MF_UAT_URL


def get_headers(carrier):
    """Return request headers for Mainfreight API calls.

    Raises ValueError if the API key is not configured — prevents sending
    a bare 'Authorization: Secret ' header that would be silently rejected
    by the Mainfreight API without a useful error.
    """
    if not carrier.x_mf_api_key:
        raise ValueError(
            f'Mainfreight API key not configured on carrier "{carrier.name}". '
            'Set it under Inventory → Freight → Freight Carriers → Mainfreight API Key.'
        )
    return {
        'Authorization': f'Secret {carrier.x_mf_api_key}',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }
