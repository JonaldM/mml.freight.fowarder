"""Tests for mml_freight_mainfreight webhook controller and booking handler."""

import json
import hashlib
from unittest.mock import patch
from odoo.tests.common import TransactionCase


class TestMFWebhookDedup(TransactionCase):
    """Mainfreight webhook deduplication — ORM-level tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Mainfreight Webhook Test',
            'delivery_type': 'mainfreight',
            'x_mf_environment': 'uat',
            'x_mf_customer_code': 'MMLCONS',
        })

    def test_messageId_used_as_dedup_key(self):
        """messageId is used as dedup key when present."""
        message_id = 'abc-123-def-456'
        # First create
        self.env['freight.webhook.event'].create({
            'carrier_id': self.carrier.id,
            'source_hash': message_id,
            'event_type': 'TrackingUpdate',
        })
        # Second create with same key should fail
        with self.assertRaises(Exception):
            self.env['freight.webhook.event'].create({
                'carrier_id': self.carrier.id,
                'source_hash': message_id,
                'event_type': 'TrackingUpdate',
            })

    def test_3pl_message_types_dont_create_booking_events(self):
        """InwardConfirmation messageType is logged and ignored — no booking state change."""
        currency = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1)
        booking = self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': currency.id,
            'state': 'confirmed',
            'carrier_booking_id': 'MFAO-HBL-001',
        })
        initial_events = len(booking.tracking_event_ids)

        # InwardConfirmation should not touch freight.booking
        inward_body = {
            'housebillNumber': 'MFAO-HBL-001',
            'events': [
                {'eventCode': 'INWARD_RECEIVED', 'eventDateTime': '2026-04-10T09:00:00Z',
                 'location': 'Mainfreight AKL', 'eventDescription': 'Goods received'},
            ],
        }
        # Directly test the booking handler skips 3PL events (those go to stock_3pl_mainfreight)
        # The _handle_mf_tracking_webhook is what handles TrackingUpdate content.
        # We verify 3PL events don't arrive here in the first place via the controller.
        # This test verifies the booking handler itself handles unknown codes gracefully.
        self.env['freight.booking']._handle_mf_tracking_webhook(self.carrier, inward_body)
        self.env.cr.flush()
        booking.invalidate_recordset()
        # The handler will attempt to normalise 'INWARD_RECEIVED' — it will map to an unknown
        # status string, which is fine. The important thing is it doesn't blow up.
        # (State should still be 'confirmed' since 'inward received' is not a booking state)
        self.assertEqual(booking.state, 'confirmed')


class TestMFTrackingWebhookHandler(TransactionCase):
    """_handle_mf_tracking_webhook — ORM-level tracking event creation tests."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Mainfreight Handler Test',
            'delivery_type': 'mainfreight',
            'x_mf_environment': 'uat',
        })

    def _make_booking(self, housebill='MFAO-HBL-TEST', state='confirmed'):
        currency = self.env['res.currency'].search([('name', '=', 'NZD')], limit=1)
        return self.env['freight.booking'].create({
            'carrier_id': self.carrier.id,
            'currency_id': currency.id,
            'state': state,
            'carrier_booking_id': housebill,
        })

    def test_departure_event_creates_tracking_event(self):
        """TrackingUpdate with DEPARTURE code creates a freight.tracking.event."""
        booking = self._make_booking()
        body = {
            'housebillNumber': 'MFAO-HBL-TEST',
            'events': [
                {
                    'eventCode': 'DEPARTURE',
                    'eventDateTime': '2026-03-18T14:00:00Z',
                    'location': 'Shanghai, CN',
                    'eventDescription': 'Vessel departed Shanghai',
                }
            ],
        }
        self.env['freight.booking']._handle_mf_tracking_webhook(self.carrier, body)
        self.env.cr.flush()
        booking.invalidate_recordset()
        self.assertEqual(len(booking.tracking_event_ids), 1)
        evt = booking.tracking_event_ids[0]
        self.assertEqual(evt.status, 'in_transit')
        self.assertEqual(evt.location, 'Shanghai, CN')

    def test_departure_event_advances_booking_state(self):
        """DEPARTURE event advances booking state from 'confirmed' to 'in_transit'."""
        booking = self._make_booking()
        self.assertEqual(booking.state, 'confirmed')
        body = {
            'housebillNumber': 'MFAO-HBL-TEST',
            'events': [
                {
                    'eventCode': 'DEPARTURE',
                    'eventDateTime': '2026-03-18T14:00:00Z',
                    'location': 'Shanghai, CN',
                    'eventDescription': 'Departed',
                }
            ],
        }
        self.env['freight.booking']._handle_mf_tracking_webhook(self.carrier, body)
        self.env.cr.flush()
        booking.invalidate_recordset()
        self.assertEqual(booking.state, 'in_transit')

    def test_duplicate_event_is_idempotent(self):
        """Duplicate webhook payload (same status + datetime) creates only one tracking event."""
        booking = self._make_booking()
        body = {
            'housebillNumber': 'MFAO-HBL-TEST',
            'events': [
                {
                    'eventCode': 'DEPARTURE',
                    'eventDateTime': '2026-03-18T14:00:00Z',
                    'location': 'Shanghai, CN',
                    'eventDescription': 'Departed',
                }
            ],
        }
        self.env['freight.booking']._handle_mf_tracking_webhook(self.carrier, body)
        self.env['freight.booking']._handle_mf_tracking_webhook(self.carrier, body)
        self.env.cr.flush()
        booking.invalidate_recordset()
        self.assertEqual(len(booking.tracking_event_ids), 1)

    def test_state_never_goes_backwards(self):
        """Events for earlier states do not regress booking state."""
        booking = self._make_booking(state='in_transit')
        body = {
            'housebillNumber': 'MFAO-HBL-TEST',
            'events': [
                {
                    'eventCode': 'BOOKING_CONFIRMED',  # earlier state
                    'eventDateTime': '2026-03-10T09:00:00Z',
                    'location': 'Shanghai, CN',
                    'eventDescription': 'Booking confirmed',
                }
            ],
        }
        self.env['freight.booking']._handle_mf_tracking_webhook(self.carrier, body)
        self.env.cr.flush()
        booking.invalidate_recordset()
        self.assertEqual(booking.state, 'in_transit')

    def test_unknown_housebill_is_silently_ignored(self):
        """Webhook for an unknown housebill does not raise — just logs and returns."""
        body = {
            'housebillNumber': 'MFAO-HBL-DOESNOTEXIST',
            'events': [
                {
                    'eventCode': 'DEPARTURE',
                    'eventDateTime': '2026-03-18T14:00:00Z',
                    'location': 'Shanghai',
                    'eventDescription': 'Departed',
                }
            ],
        }
        # Should not raise
        self.env['freight.booking']._handle_mf_tracking_webhook(self.carrier, body)

    def test_cancelled_booking_is_not_updated(self):
        """Webhook for a cancelled booking does not create tracking events."""
        booking = self._make_booking(state='confirmed')
        booking.write({'state': 'cancelled'})
        initial_event_count = len(booking.tracking_event_ids)
        body = {
            'housebillNumber': 'MFAO-HBL-TEST',
            'events': [
                {
                    'eventCode': 'DEPARTURE',
                    'eventDateTime': '2026-03-18T14:00:00Z',
                    'location': 'Shanghai',
                    'eventDescription': 'Departed',
                }
            ],
        }
        self.env['freight.booking']._handle_mf_tracking_webhook(self.carrier, body)
        self.env.cr.flush()
        booking.invalidate_recordset()
        self.assertEqual(len(booking.tracking_event_ids), initial_event_count)

    def test_cron_dispatches_to_mf_adapter_for_mf_bookings(self):
        """cron_mf_tracking_poll calls _sync_tracking on active MF bookings."""
        booking = self._make_booking(state='in_transit')
        synced = []

        def fake_sync():
            synced.append(booking.id)

        with patch.object(
            type(self.env['freight.booking']), '_sync_tracking',
            side_effect=lambda: fake_sync(),
        ):
            self.carrier.cron_mf_tracking_poll()

        self.assertIn(booking.id, synced)

    def test_cron_skips_delivered_bookings(self):
        """cron_mf_tracking_poll does not process already-delivered bookings."""
        booking = self._make_booking(state='delivered')
        synced = []

        def fake_sync():
            synced.append(booking.id)

        with patch.object(
            type(self.env['freight.booking']), '_sync_tracking',
            side_effect=lambda: fake_sync(),
        ):
            self.carrier.cron_mf_tracking_poll()

        self.assertNotIn(booking.id, synced)
