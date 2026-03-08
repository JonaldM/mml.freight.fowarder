"""K+N Shipment Status Push webhook controller.

K+N sends tracking events via a push interface (Shipment Status Push API).
This controller receives and processes those events.

Auth status: UNKNOWN — confirm webhook auth method with K+N during onboarding.
Options:
  - HMAC-SHA256 signature (like DSV)
  - API key / shared secret in header
  - mTLS (unlikely)
  - No auth (IP whitelist only)

Until auth is confirmed, we accept all requests from any source in sandbox mode
and log a warning. Production mode should have auth validation in place.

Endpoint: POST /knplus/webhook/<carrier_id>
"""

import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

# Placeholder — confirm auth header name with K+N during onboarding.
_KN_AUTH_HEADER = 'X-KN-Signature'


class KnWebhookController(http.Controller):

    @http.route('/knplus/webhook/<int:carrier_id>', type='json', auth='none', csrf=False, methods=['POST'])
    def kn_webhook(self, carrier_id, **kwargs):
        """K+N Shipment Status Push webhook receiver.

        Security notes:
        - Auth validation is stubbed until K+N onboarding confirms the method.
        - In sandbox mode, all requests are accepted with a warning log.
        - In production mode, auth MUST be validated before any ORM access.
        - Identical response for missing/invalid carrier (prevents enumeration).
        - Webhook body is NOT logged to avoid PII leakage.
        """
        body_bytes = request.httprequest.get_data()

        carrier = request.env['delivery.carrier'].browse(carrier_id)
        try:
            exists = carrier.exists()
        except Exception:
            exists = False

        environment = getattr(carrier, 'x_knplus_environment', 'sandbox') if exists else 'production'

        if not exists or environment == 'production':
            if not exists:
                _logger.warning(
                    'K+N webhook: carrier_id=%s not found — returning 403 (enumeration prevention)',
                    carrier_id,
                )
            else:
                _logger.error(
                    'K+N webhook: production mode auth not yet implemented for carrier=%s '
                    '— rejecting request. Configure auth before enabling production mode.',
                    carrier.id,
                )
            return request.make_json_response(
                {'error': 'Forbidden'},
                status=403,
            )
        else:
            _logger.warning(
                'K+N webhook: auth not yet implemented for carrier %s; '
                'returning 501 until K+N provides HMAC or API key spec.',
                carrier_id,
            )
            return request.make_json_response(
                {'status': 'not_implemented', 'message': 'Webhook auth pending K+N onboarding'},
                status=501,
            )
