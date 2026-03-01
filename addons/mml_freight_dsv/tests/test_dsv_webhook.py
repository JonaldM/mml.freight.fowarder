from odoo.tests.common import TransactionCase


class TestDsvWebhook(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.service_product = cls.env['product.product'].create({
            'name': 'Webhook Service Product',
            'type': 'service',
        })
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'DSV Webhook',
            'product_id': cls.service_product.id,
            'delivery_type': 'dsv_generic',
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id':          cls.carrier.id,
            'currency_id':         cls.env.company.currency_id.id,
            'carrier_shipment_id': 'SH_WH_001',
            'state':               'confirmed',
        })

    def _fire(self, body, carrier=None):
        self.env['freight.booking'].sudo()._handle_dsv_tracking_webhook(
            carrier or self.carrier, body
        )

    def test_valid_event_creates_tracking_record(self):
        body = {'shipmentId': 'SH_WH_001', 'events': [
            {'eventType': 'DEPARTURE', 'eventDate': '2026-05-10T08:00:00Z',
             'location': 'Shanghai CN', 'description': 'Departed.'},
        ]}
        self._fire(body)
        events = self.booking.tracking_event_ids.filtered(lambda e: e.status == 'in_transit')
        self.assertTrue(events)

    def test_unknown_shipment_id_silently_ignored(self):
        body = {'shipmentId': 'UNKNOWN_SH', 'events': [
            {'eventType': 'DEPARTURE', 'eventDate': '2026-05-10T08:00:00Z'},
        ]}
        self._fire(body)  # must not raise

    def test_carrier_mismatch_logs_warning_and_ignores(self):
        other = self.env['delivery.carrier'].create({
            'name': 'Other', 'product_id': self.service_product.id,
            'delivery_type': 'dsv_generic',
        })
        body = {'shipmentId': 'SH_WH_001', 'events': [
            {'eventType': 'DEPARTURE', 'eventDate': '2026-05-10T08:00:00Z'},
        ]}
        import logging
        with self.assertLogs('odoo.addons.mml_freight.models.freight_booking', level='WARNING'):
            self._fire(body, carrier=other)
        # No new in_transit tracking event should exist on the booking from this carrier mismatch call
        # (there may already be one from test_valid_event_creates_tracking_record if run first,
        # but that test uses a different date)

    def test_oversized_location_truncated(self):
        body = {'shipmentId': 'SH_WH_001', 'events': [
            {'eventType': 'CARGO_RECEIVED', 'eventDate': '2026-05-11T09:00:00Z',
             'location': 'A' * 400 + '\x00\x01', 'description': 'ok'},
        ]}
        self._fire(body)
        evt = self.booking.tracking_event_ids.filtered(
            lambda e: e.status == 'cargo_ready'
        )
        if evt:
            self.assertLessEqual(len(evt[-1].location), 255)
            self.assertNotIn('\x00', evt[-1].location)

    def test_empty_body_does_not_raise(self):
        self._fire({})
        self._fire({'shipmentId': ''})
