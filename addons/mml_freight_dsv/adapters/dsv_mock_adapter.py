import itertools
import datetime
from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase
from odoo.addons.mml_freight.models.freight_adapter_registry import register_adapter

_counter = itertools.count(1)


@register_adapter('dsv_generic')
@register_adapter('dsv_xpress')
class DsvMockAdapter(FreightAdapterBase):
    """Registered adapter for dsv_generic and dsv_xpress.

    demo mode  → returns hardcoded mock responses (no HTTP)
    production → delegates to DsvGenericAdapter (live HTTP)
    """

    def _demo(self):
        return getattr(self.carrier, 'x_dsv_environment', 'demo') == 'demo'

    def _live(self):
        """Return a DsvGenericAdapter instance for delegation in production mode."""
        from odoo.addons.mml_freight_dsv.adapters.dsv_generic_adapter import DsvGenericAdapter
        return DsvGenericAdapter(self.carrier, self.env)

    def request_quote(self, tender):
        if not self._demo():
            return self._live().request_quote(tender)
        return [
            {'service_name': 'DSV Road Standard', 'transport_mode': 'road',
             'base_rate': 1800.00, 'fuel_surcharge': 0, 'origin_charges': 0,
             'destination_charges': 0, 'customs_charges': 0, 'other_surcharges': 0,
             'total_rate': 1800.00, 'currency': 'NZD', 'transit_days': 5,
             'carrier_quote_ref': 'MOCK-ROAD-001', 'rate_valid_until': None,
             'estimated_pickup_date': None, 'estimated_delivery_date': None},
            {'service_name': 'DSV Air Express', 'transport_mode': 'air',
             'base_rate': 6200.00, 'fuel_surcharge': 0, 'origin_charges': 0,
             'destination_charges': 0, 'customs_charges': 0, 'other_surcharges': 0,
             'total_rate': 6200.00, 'currency': 'NZD', 'transit_days': 2,
             'carrier_quote_ref': 'MOCK-AIR-001', 'rate_valid_until': None,
             'estimated_pickup_date': None, 'estimated_delivery_date': None},
        ]

    def create_booking(self, tender, selected_quote):
        if not self._demo():
            return self._live().create_booking(tender, selected_quote)
        return {
            'carrier_booking_id': f'DSV-MOCK-BK-{next(_counter):04d}',
            'carrier_shipment_id': None,
            'carrier_tracking_url': None,
        }

    def get_tracking(self, booking):
        if not self._demo():
            return self._live().get_tracking(booking)
        now = datetime.datetime.utcnow()
        fmt = lambda d: d.isoformat()
        return [
            {'event_date': fmt(now - datetime.timedelta(days=3)), 'status': 'Picked Up',
             'location': 'Shanghai CN', 'description': 'Picked up.', 'raw_payload': '{}'},
            {'event_date': fmt(now - datetime.timedelta(days=2)), 'status': 'In Transit',
             'location': 'DSV Hub', 'description': 'In transit.', 'raw_payload': '{}'},
            {'event_date': fmt(now - datetime.timedelta(hours=12)), 'status': 'Arrived at Port',
             'location': 'Auckland NZ', 'description': 'Arrived.', 'raw_payload': '{}'},
        ]

    def cancel_booking(self, booking):
        if not self._demo():
            return self._live().cancel_booking(booking)
        # No-op in demo

    def confirm_booking(self, booking):
        if not self._demo():
            return self._live().confirm_booking(booking)
        # Demo confirm: return synthetic result
        return {
            'carrier_shipment_id': f'DSV-MOCK-SH-{next(_counter):04d}',
            'vessel_name': 'MOCK VESSEL',
            'voyage_number': 'MOCK-V001',
            'container_number': 'MOCK-CONT',
            'bill_of_lading': '',
            'feeder_vessel_name': '',
            'feeder_voyage_number': '',
            'eta': '',
        }
