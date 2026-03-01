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

    def get_documents(self, booking):
        """Return list of document dicts: {doc_type, bytes, filename, carrier_doc_ref}.
        Optional — returns empty list by default. Override in adapters that support document download.
        """
        return []

    def get_invoice(self, booking):
        """Fetch invoice data from carrier. Returns dict or None (not yet invoiced / not supported).
        Dict keys: dsv_invoice_id (str), amount (float), currency (str ISO-4217), invoice_date (str).
        """
        return None

    def cancel_booking(self, booking):
        """Cancel a booking with the carrier. Default is a no-op. Override where supported."""
        pass

    def handle_webhook(self, body):
        """Process an inbound webhook payload from the carrier.

        Default is a no-op. Override in carrier-specific adapters that support webhooks.

        Args:
            body: parsed JSON payload (dict)
        """
        pass

    def confirm_booking(self, booking):
        """Confirm a booking with the carrier after carrier-side review.

        Optional — only carriers that support a two-step draft/confirm flow need to
        implement this. Default raises NotImplementedError so callers can detect
        capability via hasattr or try/except.
        """
        raise NotImplementedError(
            f'{type(self).__name__} does not support confirm_booking()'
        )
