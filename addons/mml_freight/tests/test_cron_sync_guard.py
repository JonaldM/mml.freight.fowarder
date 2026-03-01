from unittest.mock import patch, MagicMock
from odoo.tests.common import TransactionCase


class TestCronSyncGuard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Cron Guard Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
            'delivery_type': 'fixed',
        })
        supplier = cls.env['res.partner'].create({'name': 'Cron Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        tender = cls.env['freight.tender'].create({
            'po_ids': [(4, po.id)],
            'company_id': cls.env.company.id,
            'currency_id': cls.env.company.currency_id.id,
        })
        cls.booking = cls.env['freight.booking'].create({
            'carrier_id': cls.carrier.id,
            'tender_id': tender.id,
            'currency_id': cls.env.company.currency_id.id,
            'carrier_shipment_id': 'SH-CRON-001',
            'state': 'in_transit',
        })

    def test_cron_skips_booking_if_state_changes_before_sync(self):
        """Booking cancelled between cron fetch and processing must be skipped."""
        sync_calls = []

        def fake_sync(booking_self):
            # Simulate: between the search() and _sync_tracking(), another process
            # cancels the booking. invalidate_recordset() ensures we re-read state.
            booking_self.write({'state': 'cancelled'})
            sync_calls.append(booking_self.id)

        # Set to cancelled BEFORE cron runs — simulates a state change
        self.booking.write({'state': 'cancelled'})

        with patch.object(
            type(self.env['freight.booking']), '_sync_tracking', side_effect=fake_sync,
        ):
            self.env['freight.booking'].cron_sync_tracking()

        # _sync_tracking must NOT be called for cancelled booking
        self.assertNotIn(
            self.booking.id, sync_calls,
            '_sync_tracking must not be called for a booking that is now cancelled',
        )

    def test_cron_invalidates_recordset_before_processing(self):
        """cron_sync_tracking must call invalidate_recordset() before processing each booking."""
        invalidate_calls = []
        original_invalidate = self.booking.invalidate_recordset

        def track_invalidate(*args, **kwargs):
            invalidate_calls.append(True)
            return original_invalidate(*args, **kwargs)

        self.booking.write({'state': 'in_transit'})

        with patch.object(
            type(self.env['freight.booking']), '_sync_tracking', return_value=None,
        ), patch.object(
            type(self.booking), 'invalidate_recordset', side_effect=track_invalidate,
        ):
            self.env['freight.booking'].cron_sync_tracking()

        self.assertTrue(invalidate_calls, 'invalidate_recordset() must be called before _sync_tracking')
