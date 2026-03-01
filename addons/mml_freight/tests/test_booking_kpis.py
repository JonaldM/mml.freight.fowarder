import datetime
from odoo.tests.common import TransactionCase
from odoo import fields


class TestBookingKPIs(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supplier = cls.env['res.partner'].create({'name': 'KPI Supplier'})
        cls.po = cls.env['purchase.order'].create({'partner_id': cls.supplier.id})
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'KPI Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
        })
        cls.nzd = (
            cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1)
            or cls.env.company.currency_id
        )
        cls.base_dt = datetime.datetime(2026, 6, 1, 0, 0, 0)

    def _make_booking(self, pickup=None, delivery=None, eta=None):
        vals = {
            'carrier_id':  self.carrier.id,
            'currency_id': self.nzd.id,
            'po_ids':      [(4, self.po.id)],
        }
        if pickup:
            vals['actual_pickup_date'] = pickup
        if delivery:
            vals['actual_delivery_date'] = delivery
        if eta:
            vals['eta'] = eta
        return self.env['freight.booking'].create(vals)

    def test_transit_days_computed_from_dates(self):
        pickup   = self.base_dt
        delivery = pickup + datetime.timedelta(days=14)
        booking  = self._make_booking(pickup=pickup, delivery=delivery)
        self.assertAlmostEqual(booking.transit_days_actual, 14.0, places=1)

    def test_transit_days_zero_when_no_pickup(self):
        booking = self._make_booking(delivery=self.base_dt)
        self.assertEqual(booking.transit_days_actual, 0.0)

    def test_transit_days_zero_when_no_delivery(self):
        booking = self._make_booking(pickup=self.base_dt)
        self.assertEqual(booking.transit_days_actual, 0.0)

    def test_on_time_true_when_delivery_before_eta(self):
        eta      = self.base_dt + datetime.timedelta(days=15)
        delivery = self.base_dt + datetime.timedelta(days=14)
        booking  = self._make_booking(delivery=delivery, eta=eta)
        self.assertTrue(booking.on_time)

    def test_on_time_false_when_delivery_after_eta(self):
        eta      = self.base_dt + datetime.timedelta(days=10)
        delivery = self.base_dt + datetime.timedelta(days=12)
        booking  = self._make_booking(delivery=delivery, eta=eta)
        self.assertFalse(booking.on_time)

    def test_on_time_false_when_no_delivery(self):
        booking = self._make_booking(eta=self.base_dt + datetime.timedelta(days=10))
        self.assertFalse(booking.on_time)

    def test_on_time_uses_requested_delivery_when_no_eta(self):
        """Falls back to tender.requested_delivery_date when booking.eta is unset."""
        requested = (self.base_dt + datetime.timedelta(days=20)).date()
        tender = self.env['freight.tender'].create({
            'po_ids':                  [(4, self.po.id)],
            'company_id':              self.env.company.id,
            'currency_id':             self.nzd.id,
            'requested_delivery_date': requested,
        })
        delivery = self.base_dt + datetime.timedelta(days=18)
        booking  = self.env['freight.booking'].create({
            'carrier_id':           self.carrier.id,
            'currency_id':          self.nzd.id,
            'po_ids':               [(4, self.po.id)],
            'tender_id':            tender.id,
            'actual_delivery_date': delivery,
        })
        self.assertTrue(booking.on_time)
