"""Mainfreight-specific extension methods on freight.booking."""

import logging
import re

import dateutil.parser

from odoo import models
from odoo.addons.mml_freight.models.freight_booking import BOOKING_STATES

_logger = logging.getLogger(__name__)


class FreightBookingMFExt(models.Model):
    _inherit = 'freight.booking'

    def _handle_mf_tracking_webhook(self, carrier, body):
        """Handle Mainfreight Subscription API TrackingUpdate webhook.

        SECURITY: Caller (mf_webhook.py controller) must validate the request
        before calling this method. body is the parsed 'content' dict from the
        webhook envelope.

        Mainfreight webhook envelope (confirmed from Subscription API docs):
            {
              "id": "uuid",
              "metadata": {
                "eventTypeCode": "StatusUpdateViaApi",
                "serviceTypeCode": "AirAndOcean",
                "referenceTypeCode": "JobNumber",
                "eventCode": "CargoDelivered"
              },
              "content": {
                "reference": {
                  "ourReference": "<housebill/booking ref>",
                  "yourReference": "<our ref>",
                  "serviceType": "AirAndOcean",
                  "trackingUrl": "...",
                  "events": [{"eventDateTime": "...", "code": "..."}]
                }
              }
            }

        The controller passes body = payload['content'] here.
        We extract the tracking reference and events from content.reference.
        """

        def _sanitise(value, max_len=255):
            if not value:
                return ''
            return re.sub(r'[\x00-\x1f\x7f]', '', str(value))[:max_len]

        if not isinstance(body, dict):
            return

        # Webhook content structure (confirmed from Mainfreight Subscription API docs):
        # content = {
        #   "reference": {
        #     "ourReference": "<housebill or booking ref>",
        #     "yourReference": "<our PO or booking name>",
        #     "serviceType": "AirAndOcean",
        #     "trackingUrl": "...",
        #     "events": [{"eventDateTime": "...", "code": "..."}]
        #   }
        # }
        reference_block = body.get('reference') or body.get('Reference') or body

        tracking_ref = (
            reference_block.get('ourReference') or reference_block.get('OurReference') or
            reference_block.get('housebillNumber') or reference_block.get('HousebillNumber') or
            reference_block.get('referenceValue') or reference_block.get('ReferenceValue') or
            reference_block.get('consignmentNumber') or reference_block.get('ConsignmentNumber') or
            reference_block.get('containerNumber') or reference_block.get('ContainerNumber') or ''
        )

        booking = None
        if tracking_ref:
            booking = self.search([
                ('carrier_booking_id', '=', tracking_ref),
                ('carrier_id', '=', carrier.id),
                ('state', 'not in', ['cancelled', 'received']),
            ], limit=1)
            if not booking:
                # Try container number
                booking = self.search([
                    ('container_number', '=', tracking_ref),
                    ('carrier_id', '=', carrier.id),
                    ('state', 'not in', ['cancelled', 'received']),
                ], limit=1)

        if not booking:
            _logger.info(
                'MF webhook: no active booking found for reference %r (carrier %s)',
                tracking_ref, carrier.id,
            )
            return

        # Get the MFAdapter to use its normalisation logic
        registry = self.env['freight.adapter.registry']
        adapter = registry.get_adapter(carrier)
        if not adapter:
            _logger.warning('MF webhook: no adapter for carrier %s', carrier.id)
            return

        # Extract events from reference.events (confirmed webhook structure).
        # Fall back to body-level events in case of alternate formats.
        raw_events = (
            reference_block.get('events') or reference_block.get('Events') or
            body.get('events') or body.get('Events') or
            # Single event at content root (older/alternate format)
            ([body] if 'eventCode' in body or 'code' in body else [])
        )

        if not raw_events:
            _logger.info(
                'MF webhook: no events in content for booking %s', booking.name
            )
            return

        # Use the live MF adapter for normalisation
        from odoo.addons.mml_freight_mainfreight.adapters.mf_adapter import MFAdapter
        live_adapter = MFAdapter(carrier, self.env)
        normalised = live_adapter._normalise_events({'events': raw_events})

        state_order = [s[0] for s in BOOKING_STATES]

        for evt in normalised:
            raw_date_str = evt.get('event_date', '')
            try:
                event_dt = dateutil.parser.parse(raw_date_str).replace(tzinfo=None)
            except Exception:
                _logger.warning(
                    'MF webhook: unparseable event_date %r for booking %s — skipped',
                    raw_date_str, booking.name,
                )
                continue

            status = _sanitise(evt.get('status', ''), 64)
            location = _sanitise(evt.get('location', ''))
            description = _sanitise(evt.get('description', ''))

            exists = booking.tracking_event_ids.filtered(
                lambda e, s=status, dt=event_dt: e.status == s and e.event_date == dt
            )
            if not exists:
                self.env['freight.tracking.event'].create({
                    'booking_id': booking.id,
                    'event_date': event_dt,
                    'status': status,
                    'location': location,
                    'description': description,
                    'raw_payload': 'redacted — PII',
                })

            # Auto-advance state (never go backwards)
            if status in state_order:
                cur_idx = state_order.index(booking.state) if booking.state in state_order else -1
                new_idx = state_order.index(status)
                if new_idx > cur_idx:
                    booking.state = status

        _logger.info(
            'MF webhook: processed %d events for booking %s (state now: %s)',
            len(normalised), booking.name, booking.state,
        )
