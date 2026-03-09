import datetime
import itertools
import logging

from odoo.addons.mml_freight.adapters.base_adapter import FreightAdapterBase
from odoo.addons.mml_freight.models.freight_adapter_registry import register_adapter

_logger = logging.getLogger(__name__)
_counter = itertools.count(1)


@register_adapter('mainfreight')
class MFMockAdapter(FreightAdapterBase):
    """Registered adapter for 'mainfreight' delivery type.

    uat mode        → returns hardcoded mock responses (no HTTP)
    production mode → delegates to MFAdapter (live Mainfreight API)

    Note: request_quote() always returns [] regardless of mode —
    Mainfreight A&O has no quote API. create_booking() always raises
    UserError regardless of mode — same reason.
    """

    def _uat(self):
        return getattr(self.carrier, 'x_mf_environment', 'uat') == 'uat'

    def _live(self):
        """Return an MFAdapter instance for delegation in production mode."""
        from odoo.addons.mml_freight_mainfreight.adapters.mf_adapter import MFAdapter
        return MFAdapter(self.carrier, self.env)

    def request_quote(self, tender):
        """Always returns [] — Mainfreight A&O has no quote API."""
        return []

    def create_booking(self, tender, selected_quote):
        """Always raises UserError — Mainfreight A&O has no booking API."""
        from odoo.exceptions import UserError
        raise UserError(
            'Mainfreight Air & Ocean bookings cannot be created via API.\n\n'
            'Please book via Mainchain portal (mainchain.mainfreight.com) or '
            'contact your Mainfreight account manager.\n\n'
            'Once you have the housebill or booking reference, enter it in the '
            '"Carrier Booking Ref" field on this booking record to enable '
            'automated tracking.'
        )

    def get_tracking(self, booking):
        if not self._uat():
            return self._live().get_tracking(booking)
        now = datetime.datetime.utcnow()
        fmt = lambda d: d.isoformat()
        return [
            {
                'event_date': fmt(now - datetime.timedelta(days=25)),
                'status': 'confirmed',
                'location': 'Shanghai, CN',
                'description': 'Booking confirmed with Mainfreight A&O.',
                'raw_payload': '{"code":"BOOKING_CONFIRMED"}',
            },
            {
                'event_date': fmt(now - datetime.timedelta(days=22)),
                'status': 'cargo_ready',
                'location': 'Shanghai, CN',
                'description': 'Cargo received at origin CFS.',
                'raw_payload': '{"code":"CARGO_RECEIVED"}',
            },
            {
                'event_date': fmt(now - datetime.timedelta(days=20)),
                'status': 'in_transit',
                'location': 'Shanghai, CN',
                'description': 'Vessel departed Shanghai.',
                'raw_payload': '{"code":"DEPARTURE"}',
            },
            {
                'event_date': fmt(now - datetime.timedelta(days=3)),
                'status': 'arrived_port',
                'location': 'Auckland, NZ',
                'description': 'Vessel arrived Auckland.',
                'raw_payload': '{"code":"PORT_ARRIVAL"}',
            },
        ]

    def cancel_booking(self, booking):
        # No-op — Mainfreight A&O cancellations handled via Mainchain portal.
        pass

    def handle_webhook(self, body):
        """Route webhook to booking handler in both UAT and production."""
        if not self._uat():
            return self._live().handle_webhook(body)
        # UAT: log and ignore
        _logger.info(
            'MF sandbox webhook received — no action. messageType=%s',
            body.get('messageType', 'unknown') if isinstance(body, dict) else '?',
        )

    def get_documents(self, booking):
        if not self._uat():
            return self._live().get_documents(booking)
        # Minimal valid PDF header — enough for Odoo to store as attachment
        _PDF_STUB = b'%PDF-1.0\n1 0 obj<</Type /Catalog>>endobj\nxref\n0 0\ntrailer<</Root 1 0 R>>\nstartxref\n9\n%%EOF'
        return [
            {
                'doc_type': 'pod',
                'bytes': _PDF_STUB,
                'filename': f'POD-MOCK-{booking.name}.pdf',
                'carrier_doc_ref': 'MF-POD-MOCK-001',
            },
            {
                'doc_type': 'customs',
                'bytes': _PDF_STUB,
                'filename': f'CUSTOMS-MOCK-{booking.name}.pdf',
                'carrier_doc_ref': 'MF-CUSTOMS-MOCK-001',
            },
        ]

    def get_invoice(self, booking):
        if not self._uat():
            return self._live().get_invoice(booking)
        return {
            'carrier_invoice_ref': 'MF-INV-MOCK-001',
            'amount': 2840.0,
            'currency': 'NZD',
            'invoice_date': '2026-03-10',
        }
