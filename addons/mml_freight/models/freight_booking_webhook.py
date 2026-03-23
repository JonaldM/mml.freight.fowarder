import re

from odoo import models
from odoo.addons.mml_freight.models.freight_booking import _DSV_BOOKING_STATE_MAP, BOOKING_STATES
import dateutil.parser
import logging

_logger = logging.getLogger(__name__)


class FreightBookingWebhook(models.Model):
    _inherit = 'freight.booking'

    def _handle_dsv_invoice_webhook(self, carrier, body):
        """Handle DSV Invoice webhook notification. Fetches invoice via API and updates actual_rate.

        Called by dsv_webhook.py when eventType == 'Invoice'. Carrier ID validation is done
        by the controller before this is called.
        """
        if not isinstance(body, dict):
            return
        shipment_id = body.get('shipmentId', '')
        if not shipment_id:
            return
        booking = self.search([
            ('carrier_shipment_id', '=', shipment_id),
            ('carrier_id', '=', carrier.id),
            ('state', 'not in', ['cancelled', 'received']),
        ], limit=1)
        if not booking:
            _logger.info('DSV invoice webhook: no active booking for shipmentId %s', shipment_id)
            return
        registry = self.env['freight.adapter.registry']
        adapter = registry.get_adapter(carrier)
        if not adapter:
            _logger.warning('DSV invoice webhook: no adapter for carrier %s', carrier.id)
            return
        invoice_data = adapter.get_invoice(booking)
        if not invoice_data:
            _logger.info('DSV invoice webhook: get_invoice returned None for booking %s', booking.name)
            return
        # Idempotency guard: skip write and chatter if actual_rate already matches.
        # Prevents duplicate chatter notes on DSV webhook retries.
        if booking.actual_rate and abs(booking.actual_rate - invoice_data['amount']) < 0.01:
            _logger.info(
                'DSV invoice webhook: actual_rate already matches (%.2f) for booking %s — skipping',
                booking.actual_rate, booking.name,
            )
            return
        curr = self.env['res.currency'].search(
            [('name', '=', invoice_data.get('currency', 'NZD'))], limit=1,
        ) or booking.currency_id
        booking.write({
            'actual_rate': invoice_data['amount'],
            'currency_id': curr.id if curr else booking.currency_id.id,
        })
        booking.message_post(
            body=(
                f"DSV invoice webhook: actual rate updated to "
                f"{invoice_data['amount']:.2f} {invoice_data.get('currency', '')} "
                f"(DSV Invoice #{invoice_data.get('dsv_invoice_id', 'N/A')})"
            )
        )

    def _handle_dsv_tracking_webhook(self, carrier, body):
        """Handle DSV TRACKING_UPDATE webhook. Caller must have validated HMAC before calling.

        SECURITY: HMAC-SHA256 validation is performed by dsv_webhook.py before this method
        is called. All string values from body are sanitised before storage.
        """
        def _sanitise(value, max_len=255):
            if not value:
                return ''
            return re.sub(r'[\x00-\x1f\x7f]', '', str(value))[:max_len]

        if not isinstance(body, dict):
            return
        shipment_id = body.get('shipmentId', '')
        if not shipment_id:
            return

        booking = self.search([
            ('carrier_shipment_id', '=', shipment_id),
            ('state', 'not in', ['cancelled', 'received']),
        ], limit=1)
        if not booking:
            _logger.info('DSV webhook: no active booking for shipmentId %s', shipment_id)
            return

        if booking.carrier_id.id != carrier.id:
            _logger.warning(
                'DSV webhook carrier mismatch: booking %s carrier=%s, webhook carrier=%s',
                booking.name, booking.carrier_id.id, carrier.id,
            )
            return

        prev_eta    = booking.eta
        prev_vessel = booking.vessel_name or ''
        state_order = [s[0] for s in BOOKING_STATES]

        for raw in (body.get('events') or []):
            event_type  = raw.get('eventType', '')
            status      = _DSV_BOOKING_STATE_MAP.get(event_type, _sanitise(event_type.lower(), 64))
            raw_date_str = raw.get('eventDate', '')
            try:
                event_dt = dateutil.parser.parse(raw_date_str).replace(tzinfo=None)
            except Exception:
                event_dt = None
            location    = _sanitise(raw.get('location', ''))
            description = _sanitise(raw.get('description', ''))

            if event_dt is None:
                _logger.warning(
                    'DSV webhook: unparseable eventDate %r for shipment %s event %r — skipped',
                    raw_date_str, shipment_id, event_type,
                )
                continue

            exists = booking.tracking_event_ids.filtered(
                lambda e, s=status, dt=event_dt: e.status == s and e.event_date == dt
            )
            if not exists:
                self.env['freight.tracking.event'].create({
                    'booking_id':  booking.id,
                    'event_date':  event_dt,
                    'status':      status,
                    'location':    location,
                    'description': description,
                    'raw_payload': 'redacted — PII',   # body never stored
                })

            # Auto-advance state (never go backwards)
            if status in state_order:
                cur_idx = state_order.index(booking.state) if booking.state in state_order else -1
                new_idx = state_order.index(status)
                if new_idx > cur_idx:
                    booking.state = status

        booking._check_inward_order_updates(prev_eta, prev_vessel)
