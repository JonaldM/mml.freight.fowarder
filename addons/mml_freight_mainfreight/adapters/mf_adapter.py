import json
import logging

import requests

from odoo.exceptions import UserError
from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase
from odoo.addons.mml_freight_mainfreight.adapters.mf_auth import (
    get_base_url, get_headers,
    TRACKING_PATH, TRACKING_CURRENT_PATH,
    DOCUMENTS_PATH, INVOICE_PATH,  # noqa: F401 — reserved for Documents/Invoice API stubs
)

_logger = logging.getLogger(__name__)

# Mainfreight Tracking API event code → freight.booking state
#
# Keys are UPPERCASE with underscores stripped so lookup is case/style-agnostic:
# PascalCase "CargoDelivered" → upper → "CARGODELIVERED" ✓
# UPPER_SNAKE "CARGO_DELIVERED" → upper+strip → "CARGODELIVERED" ✓
#
# Codes sourced from:
#   - Mainfreight developer portal code-list page (public, unauthenticated)
#   - Subscription API webhook documentation example payloads
#   - Transport/Warehousing API grouping level descriptions
# Expand with any additional codes observed in live UAT responses.
_MF_EVENT_STATE_MAP = {
    # ─── Booking / pre-departure ──────────────────────────────────────────────
    'BOOKINGCONFIRMED':             'confirmed',
    'BOOKED':                       'confirmed',
    'CREATED':                      'confirmed',   # A&O consolidation/shipment created
    'ORDERRECEIVED':                'confirmed',   # Warehousing: order acknowledged
    'ORDERRECEIVEDWITHERROR':       'confirmed',   # Warehousing EU: acknowledged w/ error

    # ─── Cargo pickup / ready ─────────────────────────────────────────────────
    'PICKUPREQUESTED':              'cargo_ready',
    'CARGORECEIVED':                'cargo_ready',
    'PICKEDUP':                     'cargo_ready',
    'PICKUPCOMPLETE':               'cargo_ready',  # Transport NZ/AU
    'PICKUPJOBCREATED':             'cargo_ready',
    'LOCALDIRECTPICKUPJOBCREATED':  'cargo_ready',
    'CALLCONFIRMEDPRIORTOPICKUP':   'cargo_ready',
    'TRUCKARRIVED':                 'cargo_ready',  # Warehousing EU

    # ─── Departure / in transit ───────────────────────────────────────────────
    'DEPARTURE':                    'in_transit',
    'DEPARTED':                     'in_transit',
    'DEPARTEDORIGIN':               'in_transit',  # A&O consolidation departed origin
    'LINEHAULTDEPARTED':            'in_transit',
    'LINEHAULTDEPART':              'in_transit',
    'GATEWAYSCAN':                  'in_transit',
    'INTRANSIT':                    'in_transit',
    'OUTFORDELIVERY':               'in_transit',  # Final-mile dispatch (not delivered yet)
    'ONDELIVERYVEHICLE':            'in_transit',
    'ORDERDEPARTED':                'in_transit',  # Warehousing EU: goods left warehouse
    'LOADINGFINALIZED':             'in_transit',  # Warehousing EU

    # ─── Port / terminal arrival ──────────────────────────────────────────────
    'PORTARRIVAL':                  'arrived_port',
    'ARRIVED':                      'arrived_port',
    'ARRIVEDDESTINATION':           'arrived_port',
    'ARRIVEDATTERMINAL':            'arrived_port',  # A&O consolidation at terminal
    'ATDELIVERYDEPOT':              'arrived_port',
    'ATTEMPTEDDELIVERY':            'arrived_port',  # Tried but not delivered
    'PORTARRIVEDANDPROCESSED':      'arrived_port',
    'INWARDSORDERRECEIVED':         'arrived_port',  # Warehousing EU: inward received

    # ─── Customs ──────────────────────────────────────────────────────────────
    'CUSTOMSLODGED':                'customs',
    'CUSTOMSCLEARED':               'customs',
    'DECLARATIONLODGED':            'customs',
    'DECLARATIONCLEARED':           'customs',
    'CUSTOMSASSESSED':              'customs',
    'CUSTOMSHELD':                  'customs',
    'CUSTOMSRELEASED':              'customs',
    'BORDERCLEARANCE':              'customs',
    'BORDERCLEARANCECLEARED':       'customs',

    # ─── Delivery ─────────────────────────────────────────────────────────────
    'DELIVERED':                    'delivered',
    'DELIVERYCONFIRMED':            'delivered',
    'CARGODELIVERED':               'delivered',         # A&O webhook example
    'FULLCONTAINERDELIVERED':       'delivered',         # A&O webhook example
    'GOODSDELIVERED':               'delivered',         # A&O webhook example
    'POD':                          'delivered',         # Proof of delivery scan
    'FINALIZED':                    'delivered',         # Warehousing: inward complete
    'INWARDSORDERFINALIZED':        'delivered',         # Warehousing EU
    'COMPLETE':                     'delivered',         # Warehousing: outbound complete
    'ORDERCONFIRMATIONSENT':        'delivered',         # Warehousing EU: order dispatched
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

        Handles both the polling response from /tracking/2.0/references/events
        and the webhook content.reference.events structure (confirmed from
        Mainfreight Subscription API docs).

        Polling response:
            {"events": [{"eventDateTime": "...", "code": "...", ...}]}

        Webhook content.reference (passed as dict or already as events list):
            {"events": [{"eventDateTime": "...", "code": "...", ...}]}

        Event code lookup is case/style-agnostic: codes are uppercased and
        underscores stripped before lookup so PascalCase "CargoDelivered" and
        UPPER_SNAKE "CARGO_DELIVERED" both resolve correctly.
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

            # Event code — confirmed field name is 'code' (lowercase) in webhook events;
            # also try eventCode/EventCode for polling responses.
            code_raw = (
                evt.get('eventCode') or evt.get('EventCode') or
                evt.get('code') or evt.get('Code') or ''
            ).strip()
            # Normalize: uppercase + strip underscores/spaces → matches all code styles
            code_norm = code_raw.upper().replace('_', '').replace(' ', '')

            # Event datetime — confirmed field name is 'eventDateTime' in webhook
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
                evt.get('eventDescription') or evt.get('EventDescription') or code_raw
            )

            # Map to booking state; fall back to human-readable form of raw code
            status = _MF_EVENT_STATE_MAP.get(
                code_norm,
                code_raw.replace('_', ' ').replace('-', ' ').lower() if code_raw else '',
            )

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

    def _extract_pod_urls(self, tracking_data):
        """Extract POD download URLs from Mainfreight tracking API response.

        The tracking response may contain a list of document URLs under various
        keys (e.g. 'podUrls', 'documents'). Returns a flat list of URL strings.
        This method is intentionally defensive — MF API shape is not yet confirmed.
        """
        urls = []
        if not isinstance(tracking_data, dict):
            return urls
        for key in ('podUrls', 'podUrl', 'documents', 'attachments'):
            value = tracking_data.get(key)
            if isinstance(value, list):
                urls.extend(v for v in value if isinstance(v, str) and v.startswith('http'))
            elif isinstance(value, str) and value.startswith('http'):
                urls.append(value)
        return urls

    def _fetch_carrier_documents(self, booking):
        """Fetch BOL, customs, packing list from Mainfreight Documents API.

        Not yet implemented — endpoint path unconfirmed pending MF developer account.
        Callers must catch NotImplementedError and degrade gracefully.
        """
        # implement once MF developer account is active — endpoint path unconfirmed
        raise NotImplementedError(
            'Mainfreight Documents API endpoint not yet confirmed. '
            'Returning empty list for graceful degradation.'
        )

    def get_documents(self, booking):
        """Fetch available documents for this booking.

        1. Calls the tracking current endpoint to extract POD URLs from the response.
        2. Downloads each POD URL's bytes using the same API key auth.
        3. Calls _fetch_carrier_documents() for BOL/customs/packing list — returns []
           until MF developer account is active and endpoint is confirmed.

        Returns list of dicts: {doc_type, bytes, filename, carrier_doc_ref}
        """
        docs = []

        ref = self._resolve_reference(booking)
        if ref:
            ref_type, ref_value = ref
            url = f'{get_base_url(self.carrier)}{TRACKING_CURRENT_PATH}'
            params = {'referenceType': ref_type, 'referenceValue': ref_value}
            try:
                resp = requests.get(
                    url, headers=get_headers(self.carrier), params=params, timeout=30,
                )
                if resp.ok:
                    try:
                        data = resp.json()
                    except ValueError:
                        data = {}
                    pod_urls = self._extract_pod_urls(data)
                    for i, pod_url in enumerate(pod_urls, start=1):
                        try:
                            pod_resp = requests.get(
                                pod_url, headers=get_headers(self.carrier), timeout=60,
                            )
                            if pod_resp.ok:
                                docs.append({
                                    'doc_type': 'pod',
                                    'bytes': pod_resp.content,
                                    'filename': f'POD-{booking.name}-{i}.pdf',
                                    'carrier_doc_ref': pod_url,
                                })
                        except requests.RequestException as exc:
                            _logger.warning(
                                'MF: failed to download POD URL for booking %s: %s',
                                booking.name, exc,
                            )
            except requests.RequestException as exc:
                _logger.warning(
                    'MF get_documents: tracking request failed for booking %s: %s',
                    booking.name, exc,
                )

        try:
            carrier_docs = self._fetch_carrier_documents(booking)
            docs.extend(carrier_docs)
        except NotImplementedError:
            pass

        return docs

    def get_invoice(self, booking):
        """Fetch freight invoice from Mainfreight.

        Returns dict {carrier_invoice_ref, amount, currency, invoice_date} or None.
        Stub — returns None until MF invoice API endpoint is confirmed.
        """
        # implement once MF developer account is active — endpoint path unconfirmed
        _logger.info(
            'MF get_invoice: invoice API not yet implemented for booking %s', booking.name,
        )
        return None

    def handle_webhook(self, body):
        """Process Mainfreight Subscription API TrackingUpdate webhook.

        Routes to freight.booking._handle_mf_tracking_webhook() for ORM operations.
        """
        self.env['freight.booking'].sudo()._handle_mf_tracking_webhook(self.carrier, body)
