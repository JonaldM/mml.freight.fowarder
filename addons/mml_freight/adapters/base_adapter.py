from abc import ABC, abstractmethod


class FreightAdapterBase(ABC):
    """Abstract base for all freight carrier adapters.

    Each adapter is instantiated with the delivery.carrier record and
    the freight.tender record. Subclasses MUST implement all abstract
    methods: request_quote, create_booking, and get_tracking.
    get_label is optional and returns None by default.
    """

    def __init__(self, carrier, env):
        self.carrier = carrier
        self.env = env

    @abstractmethod
    def request_quote(self, tender):
        """Return list of quote dicts for the given tender.

        Each dict must contain:
            service_name (str), transport_mode (str), total_rate (float),
            currency (str ISO-4217), transit_days (float),
            carrier_quote_ref (str), rate_valid_until (str ISO-8601 or None),
            base_rate (float), fuel_surcharge (float),
            origin_charges (float), destination_charges (float),
            customs_charges (float), other_surcharges (float),
            estimated_pickup_date (str ISO-8601 or None),
            estimated_delivery_date (str ISO-8601 or None)
        """

    @abstractmethod
    def create_booking(self, tender, selected_quote):
        """Confirm a booking. Return booking reference dict:
            carrier_booking_id (str), carrier_shipment_id (str or None),
            carrier_tracking_url (str or None)
        """

    @abstractmethod
    def get_tracking(self, booking):
        """Return list of tracking event dicts:
            event_date (str ISO-8601), status (str), location (str),
            description (str), raw_payload (str)
        """

    def get_label(self, booking):
        """Return label bytes or None. Optional."""
        return None

    def cancel_booking(self, booking):
        """Cancel a booking with the carrier. Default is a no-op. Override where supported."""
        pass
