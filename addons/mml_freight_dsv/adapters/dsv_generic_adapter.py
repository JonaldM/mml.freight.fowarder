import json
import logging
import requests
from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase
from odoo.addons.mml_freight_dsv.adapters.dsv_auth import get_token, refresh_token, DsvAuthError
from odoo.addons.mml_freight_dsv.adapters.dsv_quote_builder import get_product_types, build_quote_payload

_logger = logging.getLogger(__name__)

DSV_BASE         = 'https://api.dsv.com'
DSV_QUOTE_URL    = f'{DSV_BASE}/qs/quote/v1/quotes'
DSV_BOOKING_URL  = f'{DSV_BASE}/booking/v2/bookings'
DSV_TRACKING_URL = f'{DSV_BASE}/tracking/v1/shipments/{{shipment_id}}/events'

_DSV_EVENT_STATE_MAP = {
    'BOOKING_CONFIRMED': 'confirmed',
    'CARGO_RECEIVED':    'cargo_ready',
    'DEPARTURE':         'in_transit',
    'ARRIVED_POD':       'arrived_port',
    'CUSTOMS_CLEARED':   'customs',
    'DELIVERED':         'delivered',
}

_DSV_PRODUCT_TYPE_TO_MODE = {
    'SEA_LCL':    'sea_lcl',
    'SEA_FCL_20': 'sea_fcl',
    'SEA_FCL_40': 'sea_fcl',
    'AIR_EXPRESS': 'air',
    'ROAD':        'road',
}


class DsvGenericAdapter(FreightAdapterBase):
    """Live DSV Generic adapter. Not directly registered — used via DsvMockAdapter delegation."""

    def _headers(self, token):
        return {
            'Authorization':             f'Bearer {token}',
            'Ocp-Apim-Subscription-Key': self.carrier.x_dsv_subscription_key or '',
            'Content-Type':              'application/json',
        }

    def _post_with_retry(self, url, payload, token):
        """POST to DSV. Retries once on 401 after token refresh."""
        resp = requests.post(url, json=payload, headers=self._headers(token), timeout=30)
        if resp.status_code == 401:
            try:
                token = refresh_token(self.carrier)
            except DsvAuthError:
                return resp
            resp = requests.post(url, json=payload, headers=self._headers(token), timeout=30)
        return resp

    # ------------------------------------------------------------------
    # request_quote
    # ------------------------------------------------------------------

    def request_quote(self, tender):
        """Return list of quote dicts. Error conditions return dicts with _error=True."""
        try:
            token = get_token(self.carrier)
        except DsvAuthError as e:
            _logger.error('DSV token acquisition failed: %s', e)
            return [{'_error': True, 'error_message': f'Auth failed: {e}'}]

        mdm        = self.carrier.x_dsv_mdm or ''
        total_cbm  = tender.total_cbm or 0.0
        mode_pref  = tender.freight_mode_preference or 'any'
        prod_types = get_product_types(self.carrier, total_cbm, mode_pref)

        results = []
        for product_type in prod_types:
            payload = build_quote_payload(tender, product_type, mdm)
            try:
                resp = self._post_with_retry(DSV_QUOTE_URL, payload, token)
            except Exception as e:
                _logger.error('DSV quote request failed (%s): %s', product_type, e)
                results.append({'_error': True, 'error_message': str(e)[:500]})
                continue

            if not resp.ok:
                _logger.warning('DSV quote HTTP %s for %s', resp.status_code, product_type)
                results.append({'_error': True, 'error_message': f'DSV HTTP {resp.status_code}'})
                continue

            raw = resp.text
            for quote in (resp.json().get('quotes') or []):
                charge = quote.get('totalCharge') or {}
                mode   = _DSV_PRODUCT_TYPE_TO_MODE.get(
                    quote.get('productType', product_type), 'sea_lcl'
                )
                results.append({
                    'service_name':            quote.get('serviceName', ''),
                    'transport_mode':          mode,
                    'carrier_quote_ref':       quote.get('serviceCode', ''),
                    'total_rate':              float(charge.get('amount', 0)),
                    'base_rate':               float(charge.get('amount', 0)),
                    'fuel_surcharge':          0.0,
                    'origin_charges':          0.0,
                    'destination_charges':     0.0,
                    'customs_charges':         0.0,
                    'other_surcharges':        0.0,
                    'currency':                charge.get('currency', 'NZD'),
                    'transit_days':            float(quote.get('transitDays', 0)),
                    'rate_valid_until':        None,
                    'estimated_pickup_date':   None,
                    'estimated_delivery_date': None,
                    'raw_response':            raw,
                })
        return results

    # ------------------------------------------------------------------
    # create_booking / cancel_booking — implemented in Task 7
    # ------------------------------------------------------------------

    def create_booking(self, tender, selected_quote):
        """Create DSV draft booking (autobook=False). Raises UserError on any API failure."""
        from odoo.exceptions import UserError
        from odoo.addons.mml_freight_dsv.adapters.dsv_booking_builder import build_booking_payload
        try:
            token = get_token(self.carrier)
        except DsvAuthError as e:
            raise UserError(f'DSV auth failed: {e}') from e
        payload = build_booking_payload(tender, selected_quote, self.carrier)
        try:
            resp = self._post_with_retry(DSV_BOOKING_URL, payload, token)
        except Exception as e:
            raise UserError(f'DSV booking API error: {e}') from e
        if not resp.ok:
            raise UserError(f'DSV booking failed (HTTP {resp.status_code}): {resp.text[:200]}')
        data = resp.json()
        return {
            'carrier_booking_id':           data.get('bookingId', ''),
            'carrier_shipment_id':          data.get('shipmentId', ''),
            'carrier_tracking_url':         data.get('trackingUrl', ''),
            'requires_manual_confirmation': True,
        }

    def cancel_booking(self, booking):
        """Cancel DSV draft booking via DELETE. Confirmed → warn only. 404 → treat as success."""
        if booking.state == 'confirmed':
            booking.message_post(
                body='This booking is already confirmed with DSV. '
                     'Contact DSV directly to cancel — cancellation fees may apply.'
            )
            return
        bk_id = booking.carrier_booking_id
        if not bk_id:
            return
        try:
            token = get_token(self.carrier)
        except DsvAuthError as e:
            _logger.warning('DSV cancel booking %s: auth error, skipping cancel: %s', bk_id, e)
            return
        url = f'{DSV_BOOKING_URL}/{bk_id}'
        try:
            resp = requests.delete(url, headers=self._headers(token), timeout=30)
        except Exception as e:
            _logger.warning('DSV cancel booking %s: request error %s', bk_id, e)
            return
        if resp.status_code == 404:
            _logger.info('DSV cancel %s: 404 (already gone) — treating as success', bk_id)
            return
        if not resp.ok:
            _logger.warning('DSV cancel booking %s: HTTP %s', bk_id, resp.status_code)

    # ------------------------------------------------------------------
    # confirm_booking — implemented in Task 9
    # ------------------------------------------------------------------

    def confirm_booking(self, booking):
        """Confirm DSV draft booking. Returns vessel/ETA dict. Raises UserError on failure."""
        from odoo.exceptions import UserError
        bk_id = booking.carrier_booking_id
        if not bk_id:
            raise UserError('Cannot confirm booking: no carrier_booking_id set.')
        try:
            token = get_token(self.carrier)
        except DsvAuthError as e:
            raise UserError(f'DSV auth failed: {e}') from e
        url = f'{DSV_BOOKING_URL}/{bk_id}/confirm'
        try:
            resp = self._post_with_retry(url, {}, token)
        except Exception as e:
            raise UserError(f'DSV confirm booking error: {e}') from e
        if not resp.ok:
            raise UserError(
                f'DSV confirm booking failed (HTTP {resp.status_code}): {resp.text[:200]}'
            )
        data = resp.json()
        return {
            'carrier_shipment_id':  data.get('shipmentId', booking.carrier_shipment_id or ''),
            'vessel_name':          data.get('vesselName', ''),
            'voyage_number':        data.get('voyageNumber', ''),
            'container_number':     data.get('containerNumber', ''),
            'bill_of_lading':       data.get('billOfLading', ''),
            'feeder_vessel_name':   data.get('feederVesselName', ''),
            'feeder_voyage_number': data.get('feederVoyageNumber', ''),
            'eta':                  data.get('estimatedDelivery', ''),
        }

    # ------------------------------------------------------------------
    # get_tracking — implemented in Task 11
    # ------------------------------------------------------------------

    def get_tracking(self, booking):
        """Fetch tracking events from DSV. Returns empty list on any error (non-fatal)."""
        shipment_id = booking.carrier_shipment_id
        if not shipment_id:
            return []
        try:
            token = get_token(self.carrier)
        except DsvAuthError as e:
            _logger.warning('DSV tracking auth failed for %s: %s', booking.name, e)
            return []
        url = DSV_TRACKING_URL.format(shipment_id=shipment_id)
        try:
            resp = requests.get(url, headers=self._headers(token), timeout=30)
        except Exception as e:
            _logger.warning('DSV tracking GET failed for %s: %s', booking.name, e, exc_info=True)
            return []
        if not resp.ok:
            _logger.warning('DSV tracking HTTP %s for %s', resp.status_code, booking.name)
            return []
        events = []
        for raw in (resp.json().get('events') or []):
            event_type = raw.get('eventType', '')
            status     = _DSV_EVENT_STATE_MAP.get(event_type, event_type.lower())
            events.append({
                'event_date':  raw.get('eventDate', ''),
                'status':      status,
                'location':    raw.get('location', ''),
                'description': raw.get('description', ''),
                'raw_payload': json.dumps(raw),
                '_new_eta':    raw.get('estimatedDelivery', ''),
            })
        return events
