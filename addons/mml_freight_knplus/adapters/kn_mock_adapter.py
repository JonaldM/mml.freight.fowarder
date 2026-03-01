import datetime
import itertools
import logging

from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase
from odoo.addons.mml_freight.models.freight_adapter_registry import register_adapter

_logger = logging.getLogger(__name__)
_counter = itertools.count(1)


@register_adapter('knplus')
class KnMockAdapter(FreightAdapterBase):
    """Registered adapter for 'knplus' delivery type.

    sandbox mode  → returns hardcoded mock responses (no HTTP)
    production    → delegates to KnAdapter (live HTTP)

    This follows the DsvMockAdapter pattern: the registered adapter is always
    the mock, which delegates to the live adapter in production. This ensures
    demo environments never blow up with NotImplementedError.
    """

    def _sandbox(self):
        return getattr(self.carrier, 'x_knplus_environment', 'sandbox') == 'sandbox'

    def _live(self):
        """Return a KnAdapter instance for delegation in production mode."""
        from odoo.addons.mml_freight_knplus.adapters.kn_adapter import KnAdapter
        return KnAdapter(self.carrier, self.env)

    def request_quote(self, tender):
        if not self._sandbox():
            return self._live().request_quote(tender)
        if getattr(self.carrier, 'x_knplus_quote_mode', 'manual') != 'manual':
            # api mode in sandbox: return canned K+N quotes
            return [
                {
                    'service_name': 'K+N Sea LCL Standard',
                    'transport_mode': 'sea_lcl',
                    'base_rate': 2100.00,
                    'fuel_surcharge': 210.00,
                    'origin_charges': 180.00,
                    'destination_charges': 150.00,
                    'customs_charges': 0,
                    'other_surcharges': 0,
                    'total_rate': 2640.00,
                    'currency': 'NZD',
                    'transit_days': 28,
                    'carrier_quote_ref': 'KN-MOCK-SEA-001',
                    'rate_valid_until': None,
                    'estimated_pickup_date': None,
                    'estimated_delivery_date': None,
                },
                {
                    'service_name': 'K+N Air Express',
                    'transport_mode': 'air',
                    'base_rate': 7500.00,
                    'fuel_surcharge': 750.00,
                    'origin_charges': 200.00,
                    'destination_charges': 150.00,
                    'customs_charges': 0,
                    'other_surcharges': 0,
                    'total_rate': 8600.00,
                    'currency': 'NZD',
                    'transit_days': 3,
                    'carrier_quote_ref': 'KN-MOCK-AIR-001',
                    'rate_valid_until': None,
                    'estimated_pickup_date': None,
                    'estimated_delivery_date': None,
                },
            ]
        # manual mode: return [] — ops enter quotes via myKN portal
        return []

    def create_booking(self, tender, selected_quote):
        if not self._sandbox():
            return self._live().create_booking(tender, selected_quote)
        return {
            'carrier_booking_id': f'KN-MOCK-BK-{next(_counter):04d}',
            'carrier_shipment_id': f'KN-MOCK-SH-{next(_counter):04d}',
            'carrier_tracking_url': None,
        }

    def get_tracking(self, booking):
        if not self._sandbox():
            return self._live().get_tracking(booking)
        now = datetime.datetime.utcnow()
        fmt = lambda d: d.isoformat()
        return [
            {
                'event_date': fmt(now - datetime.timedelta(days=20)),
                'status': 'confirmed',
                'location': 'Shanghai, CN',
                'description': 'Booking confirmed with K+N.',
                'raw_payload': '{"code":"BKD"}',
            },
            {
                'event_date': fmt(now - datetime.timedelta(days=17)),
                'status': 'cargo_ready',
                'location': 'Shanghai, CN',
                'description': 'Cargo received from shipper.',
                'raw_payload': '{"code":"RCS"}',
            },
            {
                'event_date': fmt(now - datetime.timedelta(days=15)),
                'status': 'in_transit',
                'location': 'Shanghai, CN',
                'description': 'Departed origin port.',
                'raw_payload': '{"code":"DEP"}',
            },
        ]

    def cancel_booking(self, booking):
        if not self._sandbox():
            return self._live().cancel_booking(booking)
        # No-op in sandbox

    def get_documents(self, booking):
        if not self._sandbox():
            return self._live().get_documents(booking)
        return [
            {
                'doc_type': 'other',
                'bytes': b'%PDF-1.4-mock-hbl',
                'filename': f'HBL-{booking.carrier_booking_id or "mock"}.pdf',
                'carrier_doc_ref': 'KN-MOCK-HBL-001',
            },
        ]

    def handle_webhook(self, body):
        """Process K+N Shipment Status Push webhook.

        In both sandbox and production modes, route to the booking handler.
        KnAdapter.handle_webhook() is a no-op until implemented post-onboarding.
        """
        if not self._sandbox():
            return self._live().handle_webhook(body)
        # Sandbox: log and ignore
        _logger.info(
            'K+N sandbox webhook received — no action. keys: %s',
            list(body.keys()) if isinstance(body, dict) else type(body).__name__,
        )
