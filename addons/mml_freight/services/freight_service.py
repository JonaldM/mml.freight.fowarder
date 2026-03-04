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

    def get_booking_lead_time(self, booking_id: int) -> float | None:
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

    def get_booking_supplier_partner_id(self, booking_id: int) -> int | None:
        """
        Return the res.partner ID of the supplier linked to the first purchase order
        on a freight.booking, or None if not found.
        Used by mml_roq_forecast to update lead-time statistics without directly
        browsing freight.booking (to preserve NullService isolation).
        """
        booking = self.env['freight.booking'].browse(booking_id)
        if not booking.exists():
            return None
        po = booking.po_ids[:1]
        if not po:
            return None
        return po.partner_id.id or None

    def get_delivered_booking_lead_times(self, po_ids: list[int]) -> list[float]:
        """
        Return a flat list of transit_days_actual values for all delivered freight.booking
        records linked to the given purchase order IDs. Returns an empty list if no
        delivered bookings exist for those POs.
        Used by mml_roq_forecast to update per-supplier lead-time statistics.
        """
        bookings = self.env['freight.booking'].search([
            ('po_ids', 'in', po_ids),
            ('state', '=', 'delivered'),
            ('transit_days_actual', '>', 0),
        ])
        return [b.transit_days_actual for b in bookings]
