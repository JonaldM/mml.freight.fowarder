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

import hashlib
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

# TODO: populate after K+N onboarding confirms webhook auth header name
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

        body = request.get_json_data()
        if not isinstance(body, dict):
            _logger.warning('K+N webhook: unexpected payload type %s', type(body).__name__)
            return {'status': 'ok'}

        # Extract event type for logging (K+N push schema TBC)
        # Likely fields: eventType, messageType, shipmentId, etc.
        event_type = (
            body.get('eventType') or body.get('messageType') or
            body.get('type') or 'unknown'
        )
        _logger.info('K+N webhook: carrier=%s event_type=%s', carrier.id, event_type)

        # Deduplication: reject retried payloads using SHA-256 of raw body
        source_hash = hashlib.sha256(body_bytes).hexdigest()
        existing = request.env['freight.webhook.event'].sudo().search([
            ('carrier_id', '=', carrier.id),
            ('source_hash', '=', source_hash),
        ], limit=1)
        if existing:
            _logger.info(
                'K+N webhook: duplicate payload ignored (carrier=%s hash=%s)',
                carrier.id, source_hash[:16],
            )
            return {'status': 'ok'}

        try:
            with request.env.cr.savepoint():
                request.env['freight.webhook.event'].sudo().create({
                    'carrier_id': carrier.id,
                    'source_hash': source_hash,
                    'event_type': event_type,
                })
        except Exception as exc:
            if 'unique' in str(exc).lower():
                _logger.info(
                    'K+N webhook: concurrent duplicate ignored (carrier=%s hash=%s)',
                    carrier.id, source_hash[:16],
                )
                return {'status': 'ok'}
            raise

        # Route to adapter's handle_webhook for processing
        adapter = request.env['freight.adapter.registry'].get_adapter(carrier)
        if adapter:
            try:
                adapter.handle_webhook(body)
            except Exception as exc:
                _logger.error(
                    'K+N webhook: adapter.handle_webhook failed for carrier=%s: %s',
                    carrier.id, exc,
                )

        return {'status': 'ok'}
