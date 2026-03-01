from odoo.tests.common import TransactionCase
from odoo import fields
import datetime


class TestTenderExpiry(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        supplier = cls.env['res.partner'].create({'name': 'Expiry Test Supplier'})
        po = cls.env['purchase.order'].create({'partner_id': supplier.id})
        cls.po = po
        cls.carrier = cls.env['delivery.carrier'].create({
            'name': 'Expiry Carrier',
            'product_id': cls.env['product.product'].search([], limit=1).id,
        })
        cls.nzd = (
            cls.env['res.currency'].search([('name', '=', 'NZD')], limit=1)
            or cls.env.company.currency_id
        )

    def _make_tender(self, state='quoted', expiry_offset_hours=-1):
        expiry = fields.Datetime.now() + datetime.timedelta(hours=expiry_offset_hours)
        tender = self.env['freight.tender'].create({
            'po_ids': [(4, self.po.id)],
            'company_id':        self.env.company.id,
            'currency_id':       self.nzd.id,
            'state':             state,
            'tender_expiry':     expiry,
        })
        return tender

    def _add_quote(self, tender, state='pending', rate_valid_until=None):
        return self.env['freight.tender.quote'].create({
            'tender_id':        tender.id,
            'carrier_id':       self.carrier.id,
            'state':            state,
            'currency_id':      self.nzd.id,
            'rate_valid_until': rate_valid_until,
        })

    def test_expired_tender_marked_expired(self):
        """cron_expire_tenders() moves past-expiry open tenders to 'expired' state."""
        tender = self._make_tender(state='quoted', expiry_offset_hours=-2)
        self.env['freight.tender'].cron_expire_tenders()
        self.assertEqual(tender.state, 'expired')

    def test_pending_quotes_on_expired_tender_become_expired(self):
        """Pending quotes on an expired tender get state='expired'."""
        tender = self._make_tender(state='requesting', expiry_offset_hours=-1)
        q = self._add_quote(tender, state='pending')
        self.env['freight.tender'].cron_expire_tenders()
        self.assertEqual(q.state, 'expired')

    def test_received_quotes_not_touched_on_tender_expiry(self):
        """Already-received quotes on an expired tender keep state='received'."""
        tender = self._make_tender(state='quoted', expiry_offset_hours=-1)
        q = self._add_quote(tender, state='received')
        self.env['freight.tender'].cron_expire_tenders()
        self.assertEqual(q.state, 'received')

    def test_future_tender_not_expired(self):
        """Tender with future expiry stays in its current state."""
        tender = self._make_tender(state='quoted', expiry_offset_hours=+24)
        self.env['freight.tender'].cron_expire_tenders()
        self.assertEqual(tender.state, 'quoted')

    def test_booked_tender_not_expired(self):
        """Booked tenders must not be expired even if expiry has passed."""
        tender = self._make_tender(state='booked', expiry_offset_hours=-1)
        self.env['freight.tender'].cron_expire_tenders()
        self.assertEqual(tender.state, 'booked')

    def test_individual_quote_expiry(self):
        """Quotes past their rate_valid_until are expired even if tender is still open."""
        tender = self._make_tender(state='quoted', expiry_offset_hours=+24)  # tender still valid
        past = fields.Datetime.now() - datetime.timedelta(hours=2)
        q = self._add_quote(tender, state='received', rate_valid_until=past)
        self.env['freight.tender'].cron_expire_tenders()
        self.assertEqual(q.state, 'expired')

    def test_future_quote_not_expired(self):
        """Quote with future rate_valid_until stays received."""
        tender = self._make_tender(state='quoted', expiry_offset_hours=+24)
        future = fields.Datetime.now() + datetime.timedelta(hours=48)
        q = self._add_quote(tender, state='received', rate_valid_until=future)
        self.env['freight.tender'].cron_expire_tenders()
        self.assertEqual(q.state, 'received')
