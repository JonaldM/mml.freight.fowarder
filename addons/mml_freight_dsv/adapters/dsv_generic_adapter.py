import json
import logging
import requests
from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase
from odoo.addons.mml_freight_dsv.adapters.dsv_auth import get_token, refresh_token, DsvAuthError
from odoo.addons.mml_freight_dsv.adapters.dsv_quote_builder import get_product_types, build_quote_payload

_logger = logging.getLogger(__name__)

# DSV API base URLs (production).
# Generic APIs (booking, tracking, labels, documents, invoice) use /my/ prefix.
# Quote API uses its own /qs/ prefix.
# Ref: https://developer.dsv.com/guide-mydsv (Endpoint Reference section)
_DSV_GENERIC_BASE = 'https://api.dsv.com/my'
_DSV_QUOTE_BASE   = 'https://api.dsv.com/qs'

_DSV_EVENT_STATE_MAP = {
    'BOOKING_CONFIRMED': 'confirmed',
    'CARGO_RECEIVED':    'cargo_ready',
    'DEPARTURE':         'in_transit',
    'ARRIVED_POD':       'arrived_port',
    'CUSTOMS_CLEARED':   'customs',
    'DELIVERED':         'delivered',
}

_DSV_DOC_TYPE_MAP = {
    'POD':                  'pod',
    'COMMERCIAL_INVOICE':   'invoice',
    'CUSTOMS_DECLARATION':  'customs',
    'PACKING_LIST':         'other',
    'HOUSE_BILL_OF_LADING': 'other',
    'DANGEROUS_GOODS':      'other',
    'GOODS_DOCUMENTS':      'other',
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
            'Authorization':      f'Bearer {token}',
            'DSV-Subscription-Key': self.carrier.x_dsv_subscription_key or '',
            'Content-Type':       'application/json',
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
            quote_url = f'{_DSV_QUOTE_BASE}/quote/v1/quotes'
            try:
                resp = self._post_with_retry(quote_url, payload, token)
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
        booking_url = f'{_DSV_GENERIC_BASE}/booking/v2/bookings'
        try:
            resp = self._post_with_retry(booking_url, payload, token)
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
        url = f'{_DSV_GENERIC_BASE}/booking/v2/bookings/{bk_id}'
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
        url = f'{_DSV_GENERIC_BASE}/booking/v2/bookings/{bk_id}/confirm'
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
        # Tracking API v2: GET /my/tracking/v2/shipments/tmsId/{shipment_id}
        # v2 replaces the deprecated v1 endpoint; returns full shipment detail with events array.
        url = f'{_DSV_GENERIC_BASE}/tracking/v2/shipments/tmsId/{shipment_id}'
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

    # ------------------------------------------------------------------
    # get_label — implemented in Task 1
    # ------------------------------------------------------------------

    def get_label(self, booking):
        """Fetch shipping label PDF bytes from DSV Label Print API.

        Returns resp.content (bytes) on HTTP 200, None on any error or 404.
        """
        bk_id = booking.carrier_booking_id
        if not bk_id:
            return None
        try:
            token = get_token(self.carrier)
        except DsvAuthError as e:
            _logger.warning('DSV label fetch auth failed for %s: %s', booking.name, e)
            return None
        url = f'{_DSV_GENERIC_BASE}/printing/v1/labels/{bk_id}'
        headers = self._headers(token)
        headers['Accept'] = 'application/pdf'
        try:
            resp = requests.get(url, params={'printFormat': 'Portrait1Label'}, headers=headers, timeout=30)
        except Exception as e:
            _logger.warning('DSV label GET failed for %s: %s', booking.name, e, exc_info=True)
            return None
        if not resp.ok:
            _logger.warning('DSV label HTTP %s for booking %s', resp.status_code, bk_id)
            return None
        return resp.content

    # ------------------------------------------------------------------
    # get_documents — implemented in Task 2
    # ------------------------------------------------------------------

    def get_documents(self, booking):
        """Fetch all available documents for a booking from the DSV Document Download API.

        Returns a list of dicts: {doc_type, bytes, filename, carrier_doc_ref}.
        Returns [] on any error (auth failure, network error, non-2xx list response).
        Individual document download failures are logged as warnings and skipped.
        """
        bk_id = booking.carrier_booking_id
        if not bk_id:
            return []
        try:
            token = get_token(self.carrier)
        except DsvAuthError as e:
            _logger.warning('DSV documents auth failed for %s: %s', booking.name, e)
            return []
        url = f'{_DSV_GENERIC_BASE}/download/v1/shipments/bookingId/{bk_id}/documents'
        try:
            resp = requests.get(url, headers=self._headers(token), timeout=30)
        except Exception as e:
            _logger.warning('DSV document list GET failed for %s: %s', booking.name, e, exc_info=True)
            return []
        if not resp.ok:
            _logger.warning('DSV document list HTTP %s for booking %s', resp.status_code, bk_id)
            return []
        documents = []
        for raw in (resp.json() or []):
            download_url = raw.get('downloadUrl', '')
            if not download_url:
                continue
            doc_type = _DSV_DOC_TYPE_MAP.get(raw.get('documentType', ''), 'other')
            try:
                dl = requests.get(download_url, headers=self._headers(token), timeout=30)
            except Exception as e:
                _logger.warning(
                    'DSV document download failed for %s (type=%s): %s',
                    bk_id, raw.get('documentType', ''), e, exc_info=True,
                )
                continue
            if not dl.ok:
                _logger.warning(
                    'DSV document download HTTP %s for %s (type=%s)',
                    dl.status_code, bk_id, raw.get('documentType', ''),
                )
                continue
            documents.append({
                'doc_type':        doc_type,
                'bytes':           dl.content,
                'filename':        raw.get('fileName', f'doc-{doc_type}.pdf'),
                'carrier_doc_ref': raw.get('documentId', ''),
            })
        return documents

    # ------------------------------------------------------------------
    # get_invoice — implemented in Task 3
    # ------------------------------------------------------------------

    def get_invoice(self, booking):
        """Fetch DSV freight invoice for this shipment. Returns dict or None (404 = not invoiced yet)."""
        shipment_id = booking.carrier_shipment_id
        if not shipment_id:
            return None
        try:
            token = get_token(self.carrier)
        except DsvAuthError as e:
            _logger.warning('DSV invoice auth failed for %s: %s', booking.name, e)
            return None
        url = f'{_DSV_GENERIC_BASE}/invoice/v1/invoices/shipments/{shipment_id}'
        try:
            resp = requests.get(url, headers=self._headers(token), timeout=30)
        except Exception as e:
            _logger.warning('DSV invoice GET failed for %s: %s', booking.name, e)
            return None
        if resp.status_code == 404:
            return None  # Not yet invoiced — caller treats this as "try again later"
        if not resp.ok:
            _logger.warning('DSV invoice HTTP %s for %s', resp.status_code, booking.name)
            return None
        data = resp.json()
        return {
            'dsv_invoice_id': data.get('invoiceId', ''),
            'amount':         float(data.get('totalAmount', 0)),
            'currency':       data.get('currency', 'NZD'),
            'invoice_date':   data.get('invoiceDate', ''),
        }
