import hmac
import hashlib
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

_DSV_SIGNATURE_HEADER = 'X-DSV-Signature'


def _validate_dsv_signature(carrier, body_bytes):
    """Validate HMAC-SHA256 signature from DSV webhook.

    DSV sends: X-DSV-Signature: sha256=<hex_digest>
    Secret is stored in carrier.x_webhook_secret (manager-only field).
    Always call before any sudo() ORM access.
    """
    secret = carrier.sudo().x_webhook_secret
    if not secret:
        _logger.warning(
            'DSV webhook rejected for carrier %s: x_webhook_secret not configured', carrier.id
        )
        return False
    sig_header = request.httprequest.headers.get(_DSV_SIGNATURE_HEADER, '')
    if not sig_header.startswith('sha256='):
        return False
    received_hex = sig_header[7:]
    expected_hex = hmac.new(
        secret.encode('utf-8'), body_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_hex, received_hex)


class DsvWebhookController(http.Controller):

    @http.route('/dsv/webhook/<int:carrier_id>', type='json', auth='none', csrf=False, methods=['POST'])
    def dsv_webhook(self, carrier_id, **kwargs):
        """DSV webhook receiver. Validates HMAC signature before processing.

        Security notes:
        - Signature validated before any ORM write or sudo() access.
        - Returns identical response for missing/invalid carrier (prevents carrier ID enumeration).
        - Webhook body is NOT logged to avoid PII leakage (consignee names/addresses).
        """
        body_bytes = request.httprequest.get_data()

        carrier = request.env['delivery.carrier'].browse(carrier_id)
        try:
            exists = carrier.exists()
        except Exception:
            exists = False

        if not exists or not _validate_dsv_signature(carrier, body_bytes):
            # Same response whether carrier exists or not — prevents enumeration.
            return {'status': 'ok'}

        body = request.get_json_data()
        event_type = body.get('eventType', '') if isinstance(body, dict) else ''

        # Log only the event type — not the body — to avoid PII in server logs.
        _logger.info('DSV webhook: carrier=%s event_type=%s', carrier.id, event_type)

        # Deduplication: reject retried webhook payloads using SHA-256 of raw body.
        # Identical payloads from DSV retries are silently ignored (same 200 response).
        source_hash = hashlib.sha256(body_bytes).hexdigest()
        existing = request.env['freight.webhook.event'].sudo().search([
            ('carrier_id', '=', carrier.id),
            ('source_hash', '=', source_hash),
        ], limit=1)
        if existing:
            _logger.info(
                'DSV webhook: duplicate payload ignored (carrier=%s hash=%s)',
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
                    'DSV webhook: concurrent duplicate ignored (carrier=%s hash=%s)',
                    carrier.id, source_hash[:16],
                )
                return {'status': 'ok'}
            raise

        if event_type == 'TRACKING_UPDATE':
            request.env['freight.booking'].sudo()._handle_dsv_tracking_webhook(carrier, body)
        elif event_type == 'Invoice':
            request.env['freight.booking'].sudo()._handle_dsv_invoice_webhook(carrier, body)
        else:
            _logger.warning('DSV unhandled event type: %s', event_type)

        return {'status': 'ok'}
