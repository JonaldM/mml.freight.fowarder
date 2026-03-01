import json
import logging

import requests

from odoo.exceptions import UserError
from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase
from odoo.addons.mml_freight_mainfreight.adapters.mf_auth import (
    get_base_url,
    get_headers,
    TRACKING_PATH,
)

_logger = logging.getLogger(__name__)

# Mainfreight Tracking API event code → freight.booking state
# Full code list: https://developer.mainfreight.com/tracking-api/code-list
# TODO: expand this map with full event code list from Mainfreight developer portal
_MF_EVENT_STATE_MAP = {
    # Booking / pre-departure
    'BOOKING_CONFIRMED':    'confirmed',
    'CARGO_RECEIVED':       'cargo_ready',
    'PICKED_UP':            'cargo_ready',
    # In transit
    'DEPARTURE':            'in_transit',
    'DEPARTED':             'in_transit',
    'GATEWAY_SCAN':         'in_transit',
    'IN_TRANSIT':           'in_transit',
    # Arrival
    'PORT_ARRIVAL':         'arrived_port',
    'ARRIVED':              'arrived_port',
    'ARRIVED_DESTINATION':  'arrived_port',
    # Customs
    'CUSTOMS_LODGED':       'customs',
    'CUSTOMS_CLEARED':      'customs',
    # Delivery
    'OUT_FOR_DELIVERY':     'delivered',
    'DELIVERED':            'delivered',
    'POD':                  'delivered',
}

# Mainfreight reference type for tracking A&O shipments (in priority order)
_MF_REFERENCE_TYPES = [
    'InternationalHousebill',
    'ContainerNumber',
    'MasterBillNumber',
    'OrderReference',
]


class MFAdapter(FreightAdapterBase):
    """Live Mainfreight Air & Ocean carrier adapter.

    Not directly registered — used by MFMockAdapter in production mode.

    Capabilities:
      - Tracking via Mainfreight Tracking API (Air & Ocean housebills/containers)

    Not supported (no API available):
      - Quote request (manual via Mainchain portal)
      - Booking creation (manual via Mainchain portal / email)
    """

    def request_quote(self, tender):
        """Mainfreight A&O has no quote API.

        Returns [] so the tender framework treats this as a manual-only carrier.
        Ops enter quotes manually on freight.tender.quote after obtaining them
        via Mainchain portal or by contacting their Mainfreight account manager.
        """
        return []

    def create_booking(self, tender, selected_quote):
        """Mainfreight A&O has no booking API.

        Raise UserError guiding ops to the manual booking process.
        After booking via Mainchain, ops enter the housebill / booking reference
        on the freight.booking record — tracking then proceeds automatically.
        """
        raise UserError(
            'Mainfreight Air & Ocean bookings cannot be created via API.\n\n'
            'Please book via Mainchain portal (mainchain.mainfreight.com) or '
            'contact your Mainfreight account manager.\n\n'
            'Once you have the housebill or booking reference, enter it in the '
            '"Carrier Booking Ref" field on this booking record to enable '
            'automated tracking.'
        )

    def get_tracking(self, booking):
        """Fetch tracking events from Mainfreight Tracking API.

        Tries available reference types in order:
          1. carrier_booking_id (housebill or booking ref manually entered)
          2. container_number
          3. bill_of_lading (master bill)

        Returns list of normalised event dicts (same interface as all adapters).
        """
        ref = self._resolve_reference(booking)
        if not ref:
            _logger.info(
                'MF tracking: no reference available for booking %s — '
                'enter carrier_booking_id (housebill) manually.',
                booking.name,
            )
            return []

        ref_type, ref_value = ref
        url = f'{get_base_url(self.carrier)}{TRACKING_PATH}'
        params = {'referenceType': ref_type, 'referenceValue': ref_value}

        try:
            resp = requests.get(url, headers=get_headers(self.carrier), params=params, timeout=30)
        except requests.RequestException as exc:
            _logger.error('MF tracking API request failed for booking %s: %s', booking.name, exc)
            return []

        if resp.status_code == 401:
            _logger.error(
                'MF tracking API 401 for booking %s — check API key on carrier record.',
                booking.name,
            )
            return []

        if resp.status_code == 404:
            _logger.info(
                'MF tracking API 404 for booking %s (ref %s=%s) — shipment not found yet.',
                booking.name, ref_type, ref_value,
            )
            return []

        if not resp.ok:
            _logger.error(
                'MF tracking API error %s for booking %s: %s',
                resp.status_code, booking.name, resp.text[:200],
            )
            return []

        try:
            data = resp.json()
        except ValueError:
            _logger.error('MF tracking API returned non-JSON for booking %s', booking.name)
            return []

        return self._normalise_events(data)

    def _resolve_reference(self, booking):
        """Return (referenceType, referenceValue) tuple for tracking, or None."""
        if booking.carrier_booking_id:
            return ('InternationalHousebill', booking.carrier_booking_id)
        if booking.container_number:
            return ('ContainerNumber', booking.container_number)
        if booking.bill_of_lading:
            return ('MasterBillNumber', booking.bill_of_lading)
        return None

    def _normalise_events(self, data):
        """Normalise Mainfreight API tracking response to freight.tracking.event dicts.

        Mainfreight /tracking/2.0/references/events returns an array of events
        under a 'events' key (exact schema TBC — confirm with Mainfreight portal docs).
        """
        events_raw = []
        if isinstance(data, list):
            events_raw = data
        elif isinstance(data, dict):
            events_raw = data.get('events') or data.get('Events') or []

        normalised = []
        for evt in events_raw:
            if not isinstance(evt, dict):
                continue

            # Event code — try common field names from Mainfreight docs
            code = (
                evt.get('eventCode') or evt.get('EventCode') or
                evt.get('code') or evt.get('Code') or ''
            ).upper()

            # Event datetime
            event_date_str = (
                evt.get('eventDateTime') or evt.get('EventDateTime') or
                evt.get('timestamp') or evt.get('Timestamp') or
                evt.get('eventDate') or ''
            )

            # Location
            location = (
                evt.get('location') or evt.get('Location') or
                evt.get('locationName') or evt.get('LocationName') or ''
            )

            # Description
            description = (
                evt.get('description') or evt.get('Description') or
                evt.get('eventDescription') or evt.get('EventDescription') or code
            )

            # Map code to booking state (or use code as-is if unmapped)
            status = _MF_EVENT_STATE_MAP.get(code, code.lower().replace('_', ' ') if code else '')

            if not event_date_str:
                continue

            normalised.append({
                'event_date': event_date_str,
                'status': status,
                'location': location,
                'description': description,
                'raw_payload': json.dumps(evt),
            })

        return normalised

    def handle_webhook(self, body):
        """Process Mainfreight Subscription API TrackingUpdate webhook.

        Routes to freight.booking._handle_mf_tracking_webhook() for ORM operations.
        """
        self.env['freight.booking'].sudo()._handle_mf_tracking_webhook(self.carrier, body)
