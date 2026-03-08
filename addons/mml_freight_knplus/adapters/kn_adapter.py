import logging

from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase

_logger = logging.getLogger(__name__)


class KnAdapter(FreightAdapterBase):
    """Live K+N adapter — not directly registered.

    KnMockAdapter (registered as 'knplus') delegates here in production mode.

    Implementation status:
        request_quote   — NotImplementedError (K+N quote API unconfirmed; manual via myKN)
        create_booking  — NotImplementedError (pending K+N onboarding + confirmed schema)
        get_tracking    — NotImplementedError (pending K+N API access)
        get_documents   — NotImplementedError (pending K+N API access)
        handle_webhook  — NotImplementedError (pending K+N webhook onboarding)

    All methods will be implemented after K+N API onboarding is complete.
    See: fowarder.docs/KN-API-Integration-Guide.md — Onboarding Checklist.
    """

    def request_quote(self, tender):
        """K+N quote API not confirmed available.

        Options once onboarded:
          - API quote (if available for MML's volume tier)
          - Manual entry via myKN portal (mykn.kuehne-nagel.com)

        Returns [] in all cases until API is confirmed — ops enter quotes manually.
        See x_knplus_quote_mode on the carrier record.
        """
        if getattr(self.carrier, 'x_knplus_quote_mode', 'manual') == 'api':
            raise NotImplementedError(
                'K+N quote API not yet implemented. '
                'Set quote_mode=manual and enter quotes via myKN portal.'
            )
        return []

    def create_booking(self, tender, selected_quote):
        """Book air or road freight via K+N API.

        Pending K+N onboarding — expected endpoints:
          - Book Air Inbound: POST /booking/air
          - Book Road Inbound: POST /booking/road
          - Sea booking: TBC (confirm with K+N rep)
        """
        raise NotImplementedError(
            'K+N booking API not yet implemented. '
            'Pending K+N API onboarding — see fowarder.docs/KN-API-Integration-Guide.md.'
        )

    def get_tracking(self, booking):
        """Fetch tracking events via Shipment Status API.

        Pending K+N onboarding — expected endpoint:
          GET /shipment/status/{shipment_id}
          Normalise K+N event codes → freight.tracking.event dicts.
        """
        raise NotImplementedError(
            'K+N tracking API not yet implemented. '
            'Pending K+N API onboarding — see fowarder.docs/KN-API-Integration-Guide.md.'
        )

    def get_documents(self, booking):
        """Download shipment documents via Document Search API v1.

        Pending K+N onboarding — expected endpoint:
          GET /documents/search/{shipment_id}
          Download HBL, AWB, POD, customs declaration, packing list.
        """
        return []

    def handle_webhook(self, body):
        """Process K+N Shipment Status Push webhook payload.

        K+N push sends historical events on subscription — full backfill on first push.
        Deduplicate on (booking_id, carrier_event_code, event_date).

        Pending K+N webhook onboarding:
          Confirm push endpoint URL, auth method, and payload schema with K+N rep.
        """
        _logger.warning(
            'K+N webhook received but handler not implemented. '
            'body keys: %s', list(body.keys()) if isinstance(body, dict) else type(body).__name__,
        )
