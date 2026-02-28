import itertools
import datetime
from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase
from odoo.addons.mml_freight.models.freight_adapter_registry import register_adapter

_counter = itertools.count(1)


@register_adapter('dsv_generic')
@register_adapter('dsv_xpress')
class DsvMockAdapter(FreightAdapterBase):
    """Active when x_dsv_environment == 'demo'. No HTTP calls."""

    def _demo(self):
        return getattr(self.carrier, 'x_dsv_environment', 'demo') == 'demo'

    def request_quote(self, tender):
        if not self._demo():
            raise NotImplementedError('Set x_dsv_environment=demo for mock quotes.')
        return [
            {'service_name': 'DSV Road Standard', 'transport_mode': 'road', 'base_rate': 1800.00,
             'fuel_surcharge': 0, 'origin_charges': 0, 'destination_charges': 0,
             'customs_charges': 0, 'other_surcharges': 0, 'total_rate': 1800.00,
             'currency': 'NZD', 'transit_days': 5, 'carrier_quote_ref': 'MOCK-ROAD-001',
             'rate_valid_until': None, 'estimated_pickup_date': None, 'estimated_delivery_date': None},
            {'service_name': 'DSV Air Express', 'transport_mode': 'air', 'base_rate': 6200.00,
             'fuel_surcharge': 0, 'origin_charges': 0, 'destination_charges': 0,
             'customs_charges': 0, 'other_surcharges': 0, 'total_rate': 6200.00,
             'currency': 'NZD', 'transit_days': 2, 'carrier_quote_ref': 'MOCK-AIR-001',
             'rate_valid_until': None, 'estimated_pickup_date': None, 'estimated_delivery_date': None},
        ]

    def create_booking(self, tender, selected_quote):
        if not self._demo():
            raise NotImplementedError('Set x_dsv_environment=demo for mock booking.')
        return {'carrier_booking_id': f'DSV-MOCK-BK-{next(_counter):04d}', 'carrier_shipment_id': None, 'carrier_tracking_url': None}

    def get_tracking(self, booking):
        if not self._demo():
            raise NotImplementedError('Set x_dsv_environment=demo for mock tracking.')
        now = datetime.datetime.utcnow()
        fmt = lambda d: d.isoformat()
        return [
            {'event_date': fmt(now - datetime.timedelta(days=3)), 'status': 'Picked Up', 'location': 'Shanghai CN', 'description': 'Picked up.', 'raw_payload': '{}'},
            {'event_date': fmt(now - datetime.timedelta(days=2)), 'status': 'In Transit', 'location': 'DSV Hub', 'description': 'In transit to Auckland.', 'raw_payload': '{}'},
            {'event_date': fmt(now - datetime.timedelta(hours=12)), 'status': 'Arrived at Port', 'location': 'Auckland NZ', 'description': 'Arrived at port.', 'raw_payload': '{}'},
        ]
