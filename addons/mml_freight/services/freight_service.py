import logging

_logger = logging.getLogger(__name__)


class FreightService:
    """
    Public API for mml_freight. Retrieved via:
        svc = self.env['mml.registry'].service('freight')
    Returns NullService if mml_freight is not installed — all calls return None safely.
    """

    def __init__(self, env):
        self.env = env

    def create_tender(self, vals: dict) -> int | None:
        """
        Create a freight.tender record. Returns the new tender's ID, or None on failure.
        Caller should pass at minimum: {} — company_id defaults to env.company, state
        defaults to 'draft', and tender_expiry is auto-set to now()+3 days by the model.
        Typical caller keys: po_ids, origin_country_id, dest_country_id, shipment_group_ref.
        """
        try:
            tender = self.env['freight.tender'].create(vals)
            return tender.id
        except Exception:
            _logger.exception('FreightService.create_tender failed with vals=%s', vals)
            return None

    def get_booking_lead_time(self, booking_id: int) -> int | None:
        """
        Return the actual transit days for a confirmed freight.booking, or None.
        Uses freight.booking.transit_days_actual (computed Float: days between actual
        pickup and actual delivery). Returns None if booking does not exist or the
        field value is falsy (0.0 when pickup/delivery dates are not yet set).
        Used by mml_roq_forecast to feed back real lead times.
        """
        booking = self.env['freight.booking'].browse(booking_id)
        if not booking.exists():
            return None
        return booking.transit_days_actual or None
