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
        if resp.status_code in (401, 403):
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
        token      = get_token(self.carrier)
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
                    'raw_response':            str(resp.json()),
                })
        return results

    # ------------------------------------------------------------------
    # create_booking / cancel_booking — implemented in Task 7
    # ------------------------------------------------------------------

    def create_booking(self, tender, selected_quote):
        raise NotImplementedError('Implemented in Task 7')

    def cancel_booking(self, booking):
        raise NotImplementedError('Implemented in Task 7')

    # ------------------------------------------------------------------
    # confirm_booking — implemented in Task 9
    # ------------------------------------------------------------------

    def confirm_booking(self, booking):
        raise NotImplementedError('Implemented in Task 9')

    # ------------------------------------------------------------------
    # get_tracking — implemented in Task 11
    # ------------------------------------------------------------------

    def get_tracking(self, booking):
        raise NotImplementedError('Implemented in Task 11')
