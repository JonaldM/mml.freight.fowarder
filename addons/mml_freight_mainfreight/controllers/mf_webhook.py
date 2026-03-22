"""Mainfreight Subscription API webhook controller.

Mainfreight sends push notifications for:
  - TrackingUpdate: shipment status change events
  - InwardConfirmation: warehouse has received inbound goods (3PL layer)
  - OrderConfirmation: warehouse has dispatched outbound order (3PL layer)

This controller handles TrackingUpdate events only. InwardConfirmation and
OrderConfirmation belong to the 3PL layer (stock_3pl_mainfreight in the
mainfreight.3pl.intergration project) and are logged + ignored here.

Auth: validates X-MF-Secret header against x_mf_webhook_secret on the carrier record.
When x_mf_webhook_secret is not configured the request is accepted with a warning
(permissive during onboarding). Set the secret before go-live.

Endpoint: POST /mainfreight/webhook
(Configured in Mainfreight developer portal registration — not per-carrier)
"""

import hashlib
import hmac
import logging
import secrets

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

# Mainfreight webhook message types handled here
_MF_HANDLED_MESSAGE_TYPES = {'TrackingUpdate', 'Tracking Update'}

# Message types that belong to the 3PL layer — log and ignore
_MF_3PL_MESSAGE_TYPES = {'InwardConfirmation', 'Inward Confirmation', 'OrderConfirmation', 'Order Confirmation'}


def _find_carrier(env):
    """Find the active Mainfreight carrier record.

    Mainfreight webhooks are not per-carrier-id (unlike DSV's /<carrier_id>
    endpoint). Find the first active 'mainfreight' delivery carrier.
    If multiple exist, prefer the production one.
    """
    carriers = env['delivery.carrier'].sudo().search([
        ('delivery_type', '=', 'mainfreight'),
        ('active', '=', True),
    ])
    if not carriers:
        return None
    prod = carriers.filtered(lambda c: c.x_mf_environment == 'production')
    return prod[:1] if prod else carriers[:1]


class MFWebhookController(http.Controller):

    @http.route('/mainfreight/webhook', type='jsonrpc', auth='none', csrf=False, methods=['POST'])
    def mf_webhook(self, **kwargs):
        """Mainfreight Subscription API webhook receiver.

        Security notes:
        - Auth validation TBC — implement after Mainfreight rep confirms method.
        - IP whitelist is the fallback until confirmed.
        - Deduplication on messageId (if present) or SHA-256 of body.
        - Returns HTTP 200 for all known message types to prevent retries.
        - Body NOT logged to avoid PII leakage.
        """
        body_bytes = request.httprequest.get_data()

        body = request.get_json_data()
        if not isinstance(body, dict):
            _logger.warning('MF webhook: unexpected payload type %s', type(body).__name__)
            return {'status': 'ok'}

        carrier = _find_carrier(request.env)
        if not carrier:
            _logger.warning('MF webhook: no active mainfreight carrier configured.')
            return {'status': 'ok'}

        # Auth: validate X-MF-Secret header when x_mf_webhook_secret is configured.
        # If secret is unset, reject with 403 (must configure before go-live).
        configured_secret = carrier.sudo().x_mf_webhook_secret
        if configured_secret:
            received_secret = request.httprequest.headers.get('X-MF-Secret', '')
            if not received_secret or not secrets.compare_digest(
                received_secret.encode('utf-8'),
                configured_secret.encode('utf-8'),
            ):
                _logger.warning(
                    'MF webhook: rejected request with invalid or missing X-MF-Secret '
                    '(carrier=%s)', carrier.id,
                )
                return request.make_json_response({'error': 'Unauthorized'}, status=403)
        else:
            _logger.error(
                'MF webhook: x_mf_webhook_secret not configured on carrier %s — '
                'rejecting request. Set secret before go-live.',
                carrier.id,
            )
            return request.make_json_response(
                {'error': 'Webhook authentication not configured'},
                status=403,
            )

        # Extract message metadata only after authentication succeeds.
        message_type = body.get('messageType') or body.get('MessageType') or 'unknown'
        message_id = body.get('messageId') or body.get('MessageId') or ''

        # Log AFTER authentication succeeds to avoid leaking message metadata pre-auth.
        _logger.info(
            'MF webhook received: messageType=%s messageId=%s',
            message_type, message_id,
        )

        # Deduplication: use messageId if available, else SHA-256 of body
        dedup_key = message_id if message_id else hashlib.sha256(body_bytes).hexdigest()
        existing = request.env['freight.webhook.event'].sudo().search([
            ('carrier_id', '=', carrier.id),
            ('source_hash', '=', dedup_key),
        ], limit=1)
        if existing:
            _logger.info(
                'MF webhook: duplicate message ignored (carrier=%s messageId/hash=%s)',
                carrier.id, dedup_key[:24],
            )
            return {'status': 'ok'}

        try:
            with request.env.cr.savepoint():
                request.env['freight.webhook.event'].sudo().create({
                    'carrier_id': carrier.id,
                    'source_hash': dedup_key,
                    'event_type': message_type,
                })
        except Exception as exc:
            if 'unique' in str(exc).lower():
                _logger.info(
                    'MF webhook: concurrent duplicate ignored (carrier=%s key=%s)',
                    carrier.id, dedup_key[:24],
                )
                return {'status': 'ok'}
            raise

        content = body.get('content') or body.get('Content') or body

        if message_type in _MF_HANDLED_MESSAGE_TYPES:
            try:
                request.env['freight.booking'].sudo()._handle_mf_tracking_webhook(carrier, content)
            except Exception as exc:
                _logger.error(
                    'MF webhook: _handle_mf_tracking_webhook failed for messageId=%s: %s',
                    message_id, exc,
                )

        elif message_type in _MF_3PL_MESSAGE_TYPES:
            # These belong to the 3PL layer (stock_3pl_mainfreight).
            # Log and ignore — do not attempt to process here.
            _logger.info(
                'MF webhook: %s event received — belongs to 3PL layer. '
                'Ensure stock_3pl_mainfreight is handling these via its own endpoint.',
                message_type,
            )

        else:
            _logger.warning('MF webhook: unhandled messageType=%s', message_type)

        return {'status': 'ok'}
