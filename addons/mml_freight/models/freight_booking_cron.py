from odoo import models, api
from odoo.addons.mml_freight.models.freight_booking import BOOKING_STATES
import dateutil.parser
import logging

_logger = logging.getLogger(__name__)


class FreightBookingCron(models.Model):
    _inherit = 'freight.booking'

    @api.model
    def cron_fetch_missing_documents(self):
        """Cron: daily safety net — fetch missing documents and invoices.

        Targets bookings where ALL of:
        - State in ['in_transit', 'arrived_port', 'customs', 'delivered']
        - Carrier has Mainfreight API key or DSV client ID configured
        - At least one of:
            - No freight.document records at all
            - State is 'delivered' and no POD document exists
            - State is 'delivered' and actual_rate == 0 (no invoice fetched)

        Runs _auto_fetch_documents() and/or _auto_fetch_invoice() as needed.
        Silent no-op per booking if API returns nothing new.
        """
        doc_states = ['in_transit', 'arrived_port', 'customs', 'delivered']
        bookings = self.search([('state', 'in', doc_states)])

        for booking in bookings:
            booking.invalidate_recordset()
            if booking.state not in doc_states:
                continue

            carrier = booking.carrier_id
            has_credentials = bool(
                getattr(carrier, 'x_mf_api_key', None) or
                getattr(carrier, 'x_dsv_client_id', None)
            )
            if not has_credentials:
                continue

            needs_docs = not booking.document_ids
            needs_pod = (
                booking.state == 'delivered' and
                not booking.document_ids.filtered(lambda d: d.doc_type == 'pod')
            )
            needs_invoice = booking.state == 'delivered' and booking.actual_rate == 0

            if not (needs_docs or needs_pod or needs_invoice):
                continue

            try:
                if needs_docs or needs_pod:
                    booking.with_context(_auto_fetch_in_progress=True)._auto_fetch_documents(
                        doc_types=None,
                    )
                if needs_invoice:
                    booking.with_context(_auto_fetch_in_progress=True)._auto_fetch_invoice()
            except Exception as exc:
                _logger.error(
                    'cron_fetch_missing_documents: error on booking %s: %s',
                    booking.name, exc,
                )

    @api.model
    def cron_sync_tracking(self):
        """Cron: sync tracking for all active bookings.

        Guard against concurrent cron runs: invalidate_recordset() + state re-check
        before processing each booking. Pattern copied from stock_3pl_core
        _process_outbound_queue. Prevents redundant DSV API calls when two cron
        instances overlap.
        """
        active_states = ['confirmed', 'cargo_ready', 'picked_up', 'in_transit', 'arrived_port', 'customs']
        bookings = self.search([('state', 'in', active_states)])
        for booking in bookings:
            # Re-read from DB — another cron instance or user action may have
            # changed state since the initial search().
            booking.invalidate_recordset()
            if booking.state not in active_states:
                _logger.info(
                    'cron_sync_tracking: skipping booking %s (state=%s, changed since fetch)',
                    booking.name, booking.state,
                )
                continue
            try:
                booking._sync_tracking()
            except Exception as e:
                _logger.error('Tracking sync failed for booking %s: %s', booking.name, e)

    def _sync_tracking(self):
        """Sync tracking events from carrier adapter; auto-advance state; detect ETA drift."""
        adapter = self.env['freight.adapter.registry'].get_adapter(self.carrier_id)
        if not adapter:
            return

        prev_eta    = self.eta
        prev_vessel = self.vessel_name or ''
        events      = adapter.get_tracking(self)

        latest_state = None
        latest_eta   = None
        state_order  = [s[0] for s in BOOKING_STATES]

        for evt in events:
            raw_date_str = evt.get('event_date', '')
            try:
                event_dt = dateutil.parser.parse(raw_date_str).replace(tzinfo=None)
            except Exception:
                event_dt = None

            if event_dt is None:
                continue

            evt_status = evt.get('status', '')
            exists = self.tracking_event_ids.filtered(
                lambda e, s=evt_status, dt=event_dt: e.status == s and e.event_date == dt
            )
            if not exists:
                self.env['freight.tracking.event'].create({
                    'booking_id':  self.id,
                    'event_date':  event_dt,
                    'status':      evt_status,
                    'location':    evt.get('location', ''),
                    'description': evt.get('description', ''),
                    'raw_payload': 'redacted — PII',
                })
            if evt_status in state_order:
                if latest_state is None or state_order.index(evt_status) > state_order.index(latest_state):
                    latest_state = evt_status
            if evt.get('_new_eta'):
                latest_eta = evt['_new_eta']  # last ETA in batch wins; fine in practice

        # Update ETA
        if latest_eta:
            try:
                self.eta = dateutil.parser.parse(latest_eta).replace(tzinfo=None)
            except Exception:
                pass

        # Auto-advance state (never go backwards)
        if latest_state and latest_state in state_order:
            cur_idx = state_order.index(self.state) if self.state in state_order else -1
            new_idx = state_order.index(latest_state)
            if new_idx > cur_idx:
                self.state = latest_state

        self._check_inward_order_updates(prev_eta, prev_vessel)
