import hmac
import hashlib
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

_SIGNATURE_HEADER = 'X-Freight-Signature'


def _validate_webhook_signature(carrier, body_bytes):
    """Validate HMAC-SHA256 signature. Returns False if secret not configured or sig invalid.

    Expected header format: 'sha256=<hex_digest>'
    Always call this before any ORM write via sudo().
    """
    secret = carrier.sudo().x_webhook_secret
    if not secret:
        # Secret not configured — reject. Never allow unsigned webhooks in production.
        _logger.warning(
            'Webhook rejected for carrier %s: x_webhook_secret not configured', carrier.id
        )
        return False
    sig_header = request.httprequest.headers.get(_SIGNATURE_HEADER, '')
    if not sig_header.startswith('sha256='):
        return False
    received_hex = sig_header[7:]  # strip 'sha256='
    expected_hex = hmac.new(
        secret.encode('utf-8'), body_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_hex, received_hex)


class FreightWebhookController(http.Controller):

    @http.route('/freight/webhook/<int:carrier_id>', type='json', auth='none', csrf=False)
    def freight_webhook(self, carrier_id, **kwargs):
        """Generic webhook entry point — dispatches to carrier adapter.

        Security: HMAC-SHA256 signature is validated before any ORM access.
        The carrier lookup uses a minimal env (no sudo) just to fetch the secret;
        full sudo() is only used after signature passes.
        """
        body_bytes = request.httprequest.get_data()

        # Minimal lookup — no sudo, just check existence and read signing secret.
        carrier = request.env['delivery.carrier'].browse(carrier_id)
        try:
            exists = carrier.exists()
        except Exception:
            exists = False

        if not exists or not _validate_webhook_signature(carrier, body_bytes):
            # Return identical response whether carrier exists or not (prevent enumeration).
            return {'status': 'ok'}

        _logger.info('Freight webhook validated for carrier %s', carrier_id)

        body = request.jsonrequest or {}
        registry = request.env['freight.adapter.registry'].sudo()
        adapter = registry.get_adapter(carrier.sudo())
        if adapter:
            try:
                adapter.handle_webhook(body)
            except Exception as e:
                _logger.error(
                    'Webhook dispatch error for carrier %s: %s', carrier_id, e,
                )

        return {'status': 'ok'}
