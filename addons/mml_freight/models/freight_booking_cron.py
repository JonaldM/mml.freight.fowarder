from odoo import models, api
from odoo.addons.mml_freight.models.freight_booking import BOOKING_STATES, CarrierRateLimited
import dateutil.parser
import logging

_logger = logging.getLogger(__name__)

# Postgres advisory-lock key for cron_sync_tracking. Any stable 64-bit int works;
# this guards two overlapping cron workers from double-hitting carriers.
_TRACKING_SYNC_LOCK_KEY = 920130411

# Cap how many bookings one cron run will sync, so a rate-limited or slow carrier
# can't make a single run hang on an unbounded batch.
_TRACKING_SYNC_BATCH_LIMIT = 200


class FreightBookingCron(models.Model):
    _inherit = 'freight.booking'

    @api.model
    def cron_fetch_missing_documents(self):
        """Cron: safety net — fetch documents and invoices out-of-band.

        This cron is the ONLY place carrier document/invoice fetches run: write()
        no longer does synchronous carrier HTTP (it would hold row locks on slow
        network). write() instead flags ``docs_fetch_pending`` on the
        arrived_port/delivered transition, which this cron picks up.

        Targets bookings where ALL of:
        - State in ['in_transit', 'arrived_port', 'customs', 'delivered']
        - Carrier has Mainfreight API key or DSV client ID configured
        - At least one of:
            - docs_fetch_pending flag is set (a recent state transition)
            - No freight.document records at all
            - State is 'delivered' and no POD document exists
            - State is 'delivered' and actual_rate == 0 (no invoice fetched)

        Runs _auto_fetch_documents() and/or _auto_fetch_invoice() as needed and
        clears the pending flag afterwards. Silent no-op per booking if the API
        returns nothing new.
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

            pending = booking.docs_fetch_pending
            needs_docs = not booking.document_ids
            needs_pod = (
                booking.state == 'delivered' and
                not booking.document_ids.filtered(lambda d: d.doc_type == 'pod')
            )
            needs_invoice = booking.state == 'delivered' and booking.actual_rate == 0

            if not (pending or needs_docs or needs_pod or needs_invoice):
                continue

            # On the arrived_port transition only the customs/packing_list/label
            # subset is expected; delivered (and the catch-up cases) fetch all types.
            if pending and booking.state == 'arrived_port':
                doc_types = ['customs', 'packing_list', 'label']
            else:
                doc_types = None

            try:
                if pending or needs_docs or needs_pod:
                    booking.with_context(_auto_fetch_in_progress=True)._auto_fetch_documents(
                        doc_types=doc_types,
                    )
                if needs_invoice or (pending and booking.state == 'delivered'):
                    booking.with_context(_auto_fetch_in_progress=True)._auto_fetch_invoice()
            except Exception as exc:
                _logger.error(
                    'cron_fetch_missing_documents: error on booking %s: %s',
                    booking.name, exc,
                )
            finally:
                # Clear the flag whether or not the fetch found anything — the
                # needs_* fallbacks still re-target this booking on a later run if
                # documents/invoice remain genuinely missing.
                if booking.docs_fetch_pending:
                    booking.with_context(_auto_fetch_in_progress=True).write(
                        {'docs_fetch_pending': False}
                    )

    @api.model
    def cron_sync_tracking(self):
        """Cron: sync tracking for active bookings.

        Hardening:
        * Job mutex — a Postgres session advisory lock (pg_try_advisory_lock)
          ensures two overlapping cron workers never double-hit carriers. If the
          lock is already held, this run exits immediately.
        * Batch cap — at most ``_TRACKING_SYNC_BATCH_LIMIT`` bookings per run so a
          slow/rate-limited carrier can't make one run hang on an unbounded batch.
        * 429 handling — when a carrier returns HTTP 429 (CarrierRateLimited) we
          stop syncing that carrier for the rest of the run (honoring Retry-After
          if present) rather than silently dropping every booking under it.

        Per-booking, invalidate_recordset() + state re-check still guards against a
        state change between the search() and the sync.
        """
        # Job mutex: skip the run entirely if another worker holds the lock.
        self.env.cr.execute('SELECT pg_try_advisory_lock(%s)', [_TRACKING_SYNC_LOCK_KEY])
        if not self.env.cr.fetchone()[0]:
            _logger.info('cron_sync_tracking: another run holds the advisory lock — skipping.')
            return
        try:
            active_states = ['confirmed', 'cargo_ready', 'picked_up', 'in_transit', 'arrived_port', 'customs']
            bookings = self.search(
                [('state', 'in', active_states)], limit=_TRACKING_SYNC_BATCH_LIMIT,
            )
            # Carrier ids that returned 429 this run — skip their remaining bookings.
            rate_limited_carriers = set()
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
                if booking.carrier_id.id in rate_limited_carriers:
                    _logger.info(
                        'cron_sync_tracking: skipping booking %s — carrier %s rate-limited this run',
                        booking.name, booking.carrier_id.id,
                    )
                    continue
                try:
                    booking._sync_tracking()
                except CarrierRateLimited as rl:
                    rate_limited_carriers.add(booking.carrier_id.id)
                    _logger.warning(
                        'cron_sync_tracking: carrier %s returned 429 (Retry-After=%s) — '
                        'stopping that carrier for this run.',
                        booking.carrier_id.id, rl.retry_after,
                    )
                except Exception as e:
                    _logger.error('Tracking sync failed for booking %s: %s', booking.name, e)
        finally:
            # Release the advisory lock even if the batch raised.
            self.env.cr.execute('SELECT pg_advisory_unlock(%s)', [_TRACKING_SYNC_LOCK_KEY])

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
